"""Wire the loop layer to real runs: generator + evaluator backed by the backend.

This is the integration glue between the pure loop engine
(:mod:`...loop.driver`) and the existing run machinery: each generator/evaluator
turn is a real ``AgentTeamRun`` driven by the run backend, so it streams into the
event store and shows in the cockpit exactly like a chat run — only tagged with
its loop ``role`` and ``attempt_id``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from typing import NamedTuple

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    RUN_ROLE_EVALUATOR,
    RUN_ROLE_GENERATOR,
    RUN_ROLE_REVIEWER,
    AgentTeamTask,
)
from agent_team.features.board.repositories import conversations as conversations_repo
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.repositories.tasks import get_task
from agent_team.features.board.runtime import registry, task_journal
from agent_team.features.board.runtime.backend import get_run_backend
from agent_team.features.board.runtime.events import (
    RUN_CANCELLED,
    RUN_DONE,
    RUN_ERROR,
)
from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
from agent_team.features.board.runtime.loop import planning_prompts, verification_runner
from agent_team.features.board.runtime.loop.budget import LoopBudget
from agent_team.features.board.runtime.loop.controller import DEFAULT_MAX_ZERO_STREAK
from agent_team.features.board.runtime.loop.driver import (
    GeneratorTurn,
    LoopOutcome,
    run_loop,
)
from agent_team.features.board.runtime.loop.evaluator import (
    Verdict,
    build_evaluator_prompt,
)
from agent_team.features.board.runtime.loop.status import LoopState, LoopStatus
from agent_team.features.board.runtime.loop.verdict import (
    LoopVerdict,
    has_verification_evidence,
    parse_verdict,
    validate_command_evidence,
    validate_verification_evidence,
)
from agent_team.features.board.runtime.sandbox.service import (
    repair_workspace_ownership,
    resolve_profile,
)
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class _RunningLoop:
    """An in-process loop: its driving task and a cancel switch."""

    task: asyncio.Task
    cancel: asyncio.Event


#: task_id → the loop currently driving it (so a second start is a no-op and the
#: UI can cancel a live loop). Cleared when the loop's background task settles.
_RUNNING_LOOPS: dict[str, _RunningLoop] = {}


def is_loop_running(task_id: str) -> bool:
    """Whether an autonomous loop is actively driving ``task_id`` right now."""
    loop = _RUNNING_LOOPS.get(task_id)
    return loop is not None and not loop.task.done()


def cancel_loop(task_id: str) -> bool:
    """Request a running loop to stop after its current attempt; ``False`` if idle.

    The loop checks its cancel event between attempts and finishes as
    ``cancelled`` (an in-flight generator run is left to complete so the
    workspace is never abandoned mid-write).
    """
    loop = _RUNNING_LOOPS.get(task_id)
    if loop is None or loop.task.done():
        return False
    loop.cancel.set()
    return True


def _create_loop_run(
    *,
    task_id: str,
    agent_alias: str,
    prompt: str,
    role: str,
    attempt_id: str | None,
    actor_id: str | None = None,
) -> str:
    """Create a queued run tagged with its loop role + attempt (own session).

    The conversation is scoped per role so planner, reviewer, generator and
    evaluator each run in their own agent session — never one shared process.
    Reviewers/evaluators get a brand-new thread every time (``fresh``) so each
    judgement is independent of the work it grades and of any prior verdict.
    """
    db = SessionLocal()
    try:
        conv = conversations_repo.get_or_create_loop_conversation(
            db,
            task_id=task_id,
            agent_alias=agent_alias,
            role=role,
            fresh=role in (RUN_ROLE_REVIEWER, RUN_ROLE_EVALUATOR),
        )
        run = runs_repo.create_run(
            db,
            task_id=task_id,
            conversation=conv,
            agent_alias=agent_alias,
            trigger="loop",
            actor_id=actor_id,
            prompt=prompt,
            role=role,
            attempt_id=attempt_id,
        )
        run_id = run.id
        db.commit()
        return run_id
    finally:
        db.close()


@dataclass
class _RunResult:
    status: str
    final_answer: str
    tokens: int
    cost_usd: float


def _read_run_result(run_id: str) -> _RunResult:
    """Return the terminal status, answer and resource use of a finished run."""
    db = SessionLocal()
    try:
        run = runs_repo.get_run(db, run_id)
        if run is None:
            return _RunResult(RUN_CANCELLED, "", 0, 0.0)
        return _RunResult(
            status=run.status,
            final_answer=run.final_answer or "",
            tokens=int(run.total_tokens or 0),
            cost_usd=float(run.cost_usd or 0.0),
        )
    finally:
        db.close()


async def _drive_to_completion(run_id: str) -> _RunResult:
    """Start a run on the backend and wait for it to reach a terminal state."""
    await get_run_backend().start(run_id)
    handle = registry.get(run_id)
    if handle is not None and handle.task is not None:
        try:
            await handle.task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — the backend already persisted the error
            logger.warning("loop run %s raised while driving", run_id, exc_info=True)
    return await asyncio.to_thread(_read_run_result, run_id)


class BackendGenerator:
    """Runs one generator turn as a real run on the task's agent thread."""

    def __init__(self, *, task_id: str, agent_alias: str, workspace_path: str = "") -> None:
        self._task_id = task_id
        self._agent_alias = agent_alias
        self._workspace_path = workspace_path

    async def __call__(self, attempt_id: str, prompt: str) -> GeneratorTurn:
        # Mirror the full durable journal to a workspace file before the turn so
        # the agent can read its own prior decisions on demand (a light pointer
        # in the prompt preamble tells it where), even after its context was
        # compacted. We do not inline the whole journal to keep the prompt small.
        await asyncio.to_thread(
            task_journal.write_journal_file, self._task_id, self._workspace_path
        )
        run_id = await asyncio.to_thread(
            _create_loop_run,
            task_id=self._task_id,
            agent_alias=self._agent_alias,
            prompt=prompt,
            role=RUN_ROLE_GENERATOR,
            attempt_id=attempt_id,
        )
        result = await _drive_to_completion(run_id)
        # Fold any journal notes the generator left in its inbox into the durable
        # journal (best-effort) before the loop inspects markers / grades.
        await asyncio.to_thread(
            task_journal.ingest_agent_notes,
            task_id=self._task_id,
            workspace_path=self._workspace_path,
            actor_id=self._agent_alias,
            phase="execution",
        )
        return GeneratorTurn(
            run_id=run_id,
            final_text=result.final_answer,
            cancelled=result.status == RUN_CANCELLED,
            errored=result.status == RUN_ERROR,
            tokens=result.tokens,
            cost_usd=result.cost_usd,
        )


#: Workspace-relative directory the evaluator drops its verdict file into.
_VERDICT_DIR = ".agent-team/loop"


def _read_verdict_file(workspace_path: str, rel_path: str) -> Verdict | None:
    """Read and parse the evaluator's verdict file, deleting it afterwards.

    The file is the authoritative channel; a per-evaluation token in its name
    means a stale file from an earlier attempt is never mistaken for this one.
    """
    if not workspace_path:
        return None
    abs_path = os.path.join(workspace_path, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    finally:
        try:
            os.remove(abs_path)
        except OSError:
            pass
    return parse_verdict(text)


def _verdict_from_evidence(workspace_path: str) -> Verdict | None:
    """Map a strict evaluator's ``EVIDENCE.json`` artifact onto a verdict.

    The durable evidence file (kept on disk, overwritten each evaluation) is the
    authoritative output in strict mode. ``missing``/``risks`` lists are joined
    into the controller's free-text ``missing`` field, and the whole document is
    carried in ``evidence`` so the cockpit can render it.
    """
    data = artifacts.read_json(workspace_path, artifacts.EVIDENCE_PATH)
    if not isinstance(data, dict):
        return None
    verdict = _coerce_loop_verdict(data.get("verdict"))
    if verdict is None:
        return None
    try:
        score = min(1.0, max(0.0, float(data.get("score", 0.0) or 0.0)))
    except (TypeError, ValueError):
        score = 0.0
    missing_items = data.get("missing")
    missing = (
        "; ".join(str(m) for m in missing_items)
        if isinstance(missing_items, list)
        else str(missing_items or "")
    ).strip()
    return Verdict(verdict=verdict, score=score, missing=missing, evidence=data)


def _coerce_loop_verdict(value: object) -> LoopVerdict | None:
    text = str(value or "").strip().lower()
    for member in LoopVerdict:
        if text == member.value:
            return member
    return None


#: Fed back to the generator when an evaluator returns ``pass`` without any
#: proof it verified anything — the whole point of an independent evaluator is
#: that completion is earned with evidence, not asserted.
_UNVERIFIED_PASS_NOTE = (
    "The evaluator reported pass but recorded NO verification evidence (no "
    "commands run, no checks observed). A completion claim without evidence does "
    "not count. Re-run the project's tests/build/lint, observe the actual "
    "results, and record them as evidence before the task can be marked complete."
)


@dataclass(frozen=True)
class _VerificationContract:
    """Aggregated machine-enforced verification requirements for a turn."""

    active: bool
    profiles: list[str]
    expected_criteria: list[str]
    planned_commands: list[object]
    required_evidence: list[str]


def _verification_contract(tasks: list[dict]) -> _VerificationContract:
    """Aggregate optional per-task verification blocks without project coupling."""
    profiles: list[str] = []
    expected_criteria: list[str] = []
    planned_commands: list[object] = []
    planned_command_keys: set[tuple[str, str]] = set()
    required_evidence: list[str] = []
    active = False

    for task in tasks:
        verification = artifacts.normalize_verification(task.get("verification"))
        task_active = bool(
            verification.get("profiles")
            or verification.get("feature_commands")
            or verification.get("regression_commands")
            or verification.get("required_evidence")
            or verification.get("test_change") in {"add", "update"}
        )
        if not task_active:
            continue
        active = True
        for profile in verification.get("profiles") or []:
            if profile not in profiles:
                profiles.append(profile)
        for command in (
            (verification.get("feature_commands") or [])
            + (verification.get("regression_commands") or [])
        ):
            key = artifacts.verification_command_key(command)
            if key != ("", "") and key not in planned_command_keys:
                planned_command_keys.add(key)
                planned_commands.append(command)
        for section in verification.get("required_evidence") or []:
            if section not in required_evidence:
                required_evidence.append(section)

        task_id = str(task.get("id") or "task")
        acceptance = task.get("acceptance") or []
        expected_criteria.extend(
            f"{task_id}:AC-{index}" for index in range(1, len(acceptance) + 1)
        )

    return _VerificationContract(
        active=active,
        profiles=profiles,
        expected_criteria=expected_criteria,
        planned_commands=planned_commands,
        required_evidence=required_evidence,
    )


def _strict_verification_issues(
    verdict: Verdict,
    *,
    workspace_path: str,
    graph_task: dict | None,
    trusted_receipts: list[dict] | None = None,
    current_source_sha256: str = "",
) -> list[str]:
    """Return backend-observed reasons a strict ``pass`` is not trustworthy."""
    tasks = [graph_task] if graph_task is not None else artifacts.task_list(workspace_path)
    contract = _verification_contract(tasks)
    if not contract.active:
        # Legacy TASKS.json files remain valid, but a strict pass must still
        # contain concrete successful commands with explicit exit codes.
        return validate_command_evidence(verdict.evidence)
    return validate_verification_evidence(
        verdict.evidence,
        profiles=contract.profiles,
        expected_criteria=contract.expected_criteria,
        planned_commands=contract.planned_commands,
        required_evidence=contract.required_evidence,
        workspace_path=workspace_path,
        trusted_receipts=trusted_receipts,
        current_source_sha256=current_source_sha256,
    )


def _downgrade_unverified_pass(
    verdict: Verdict, issues: list[str] | None = None
) -> Verdict:
    """Turn an evidence-less ``pass`` into a ``fail`` so the loop keeps going.

    Preserves the score/evidence and the accounting fields; prepends a clear
    note to ``missing`` so the next attempt knows real verification is required.
    The attempt budget remains the backstop if the evaluator keeps refusing to
    produce evidence.
    """
    note = _UNVERIFIED_PASS_NOTE
    if issues:
        note = "The evaluator's pass failed the verification contract:\n" + "\n".join(
            f"- {issue}" for issue in issues
        )
    missing = (verdict.missing or "").strip()
    combined = f"{note}\n\n{missing}" if missing else note
    return Verdict(
        verdict=LoopVerdict.FAIL,
        score=verdict.score,
        missing=combined,
        evidence=verdict.evidence,
        eval_tokens=verdict.eval_tokens,
        eval_cost_usd=verdict.eval_cost_usd,
    )


class WorkerEvaluator:
    """Grades an attempt by running a separate evaluator agent over the task.

    In ``strict`` mode the evaluator works from the approved planning artifacts
    and writes a durable ``EVIDENCE.json``; otherwise it uses the lightweight
    verdict-file contract.
    """

    def __init__(
        self,
        *,
        task_id: str,
        evaluator_alias: str,
        strict: bool = False,
        graph_task: dict | None = None,
        conventions: str = "",
    ) -> None:
        self._task_id = task_id
        self._evaluator_alias = evaluator_alias
        self._strict = strict
        #: When set (task-graph execution), grade only this single plan task
        #: rather than the whole SPEC.
        self._graph_task = graph_task
        #: The board's planning conventions; folded into strict evaluator
        #: prompts so a human-stated house rule is graded as scope, not noise.
        self._conventions = conventions

    async def evaluate(
        self,
        *,
        objective: str,
        generator_summary: str,
        workspace_path: str,
        attempt_id: str | None = None,
    ) -> Verdict | None:
        trusted_batch: verification_runner.ReceiptBatch | None = None
        runner_required = False
        if self._strict:
            tasks = (
                [self._graph_task]
                if self._graph_task is not None
                else artifacts.task_list(workspace_path)
            )
            contract = _verification_contract(tasks)
            runner_required = bool(contract.active and contract.planned_commands)
            if runner_required:
                _stored_workspace, board_id, approved_contract_etag = await asyncio.to_thread(
                    _task_workspace_and_board, self._task_id
                )
                try:
                    trusted_batch = await verification_runner.run_approved_commands(
                        task_id=self._task_id,
                        board_id=board_id or "",
                        attempt_id=attempt_id,
                        workspace_path=workspace_path,
                        tasks=tasks,
                        approved_tasks_contract_etag=approved_contract_etag,
                    )
                except Exception:  # noqa: BLE001 — evaluator must report the block
                    logger.exception(
                        "trusted verification runner failed for task=%s",
                        self._task_id,
                    )
        if self._strict and self._graph_task is not None:
            prompt = planning_prompts.build_task_evaluator_prompt(
                task=self._graph_task,
                generator_summary=generator_summary,
                verdict_path=artifacts.EVIDENCE_PATH,
                conventions=self._conventions,
            )
        elif self._strict:
            prompt = planning_prompts.build_strict_evaluator_prompt(
                objective=objective,
                generator_summary=generator_summary,
                verdict_path=artifacts.EVIDENCE_PATH,
                conventions=self._conventions,
                profiles=contract.profiles,
            )
        else:
            # Unique per evaluation so a leftover file from a prior attempt can
            # never be read back as this attempt's verdict.
            rel_path = f"{_VERDICT_DIR}/verdict-{uuid.uuid4().hex}.json"
            prompt = build_evaluator_prompt(
                objective, generator_summary, verdict_path=rel_path
            )
        if runner_required:
            if trusted_batch is None:
                prompt += (
                    "\n\n## Trusted verification runner\n"
                    "The backend could not produce trusted command receipts. "
                    "Do not claim that planned commands passed; return fail and "
                    "explain the runner/runtime problem.\n"
                )
            else:
                prompt += (
                    "\n\n## Trusted verification runner\n"
                    f"Backend batch: `{trusted_batch.batch_id}`. Read "
                    f"`{verification_runner.RECEIPT_MANIFEST_PATH}`. Cite every "
                    "backend receipt id in top-level `receipt_ids` and map useful "
                    "receipt ids to acceptance criteria. The database copy is "
                    "authoritative; do not replace receipts with commands you ran "
                    "yourself. You may still use browser/MCP checks for scenarios "
                    "and artifacts.\n"
                )
        run_id = await asyncio.to_thread(
            _create_loop_run,
            task_id=self._task_id,
            agent_alias=self._evaluator_alias,
            prompt=prompt,
            role=RUN_ROLE_EVALUATOR,
            # Tag the critic run with the attempt it judges so the cockpit can
            # surface the critic's verification transcript next to the verdict.
            attempt_id=attempt_id,
        )
        result = await _drive_to_completion(run_id)
        # The evaluator can also leave journal notes (risks it spotted, etc.).
        await asyncio.to_thread(
            task_journal.ingest_agent_notes,
            task_id=self._task_id,
            workspace_path=workspace_path,
            actor_id=self._evaluator_alias,
            phase="verification",
        )
        if result.status != RUN_DONE:
            return None  # could not evaluate → fail-open
        if self._strict:
            verdict = await asyncio.to_thread(_verdict_from_evidence, workspace_path)
            if verdict is None:
                verdict = parse_verdict(result.final_answer)
        else:
            # Prefer the file the evaluator wrote (robust for noisy CLI stdout),
            # then fall back to parsing the JSON it echoed in its reply.
            verdict = await asyncio.to_thread(
                _read_verdict_file, workspace_path, rel_path
            )
            if verdict is None:
                verdict = parse_verdict(result.final_answer)
        if verdict is None:
            return None
        # Fold the evaluator turn's spend into the budget (it ran a real agent
        # turn with tests/build); the driver reads these off the verdict.
        verdict.eval_tokens = result.tokens
        verdict.eval_cost_usd = result.cost_usd
        # A pass with no proof of verification is not a verified completion —
        # downgrade it so the backend never marks the task complete on an
        # evidence-less claim (the core invariant of an independent evaluator).
        if verdict.verdict == LoopVerdict.PASS:
            if self._strict:
                trusted_receipts: list[dict] | None = None
                current_source_sha256 = ""
                if runner_required:
                    trusted_receipts = (
                        await asyncio.to_thread(
                            verification_runner.load_receipt_batch,
                            self._task_id,
                            trusted_batch.batch_id,
                        )
                        if trusted_batch is not None
                        else []
                    )
                    _source, current_source_sha256 = await asyncio.to_thread(
                        verification_runner.capture_source_state, workspace_path
                    )
                issues = _strict_verification_issues(
                    verdict,
                    workspace_path=workspace_path,
                    graph_task=self._graph_task,
                    trusted_receipts=trusted_receipts,
                    current_source_sha256=current_source_sha256,
                )
                if issues:
                    return _downgrade_unverified_pass(verdict, issues)
            elif not has_verification_evidence(verdict):
                return _downgrade_unverified_pass(verdict)
        return verdict


def _task_workspace_and_board(task_id: str) -> tuple[str, str | None, str]:
    db = SessionLocal()
    try:
        task = get_task(db, task_id)
        if task is None:
            return "", None, ""
        meta = task.planning_meta()
        approved_contract_etag = str(meta.get("tasks_contract_etag") or "")
        return task.workspace_path, task.board_id, approved_contract_etag
    finally:
        db.close()


class BoardPlanningSettings(NamedTuple):
    """Per-board planning knobs the loop/planning phases read (all optional)."""

    #: Free-text house rules injected into every strict-phase prompt.
    conventions: str
    #: Skill pack owning SPEC/PLAN structure guidance ("" = bundled default).
    planning_skill: str
    #: Auto-approve quick-lane plans on their first draft (default off).
    auto_approve_quick: bool
    #: Maximum reviewer-driven planner re-drafts (zero disables the loop).
    review_max_redrafts: int


_DEFAULT_PLANNING_SETTINGS = BoardPlanningSettings("", "", False, 0)


def _board_planning_settings(board_id: str | None) -> BoardPlanningSettings:
    """The board's planning settings — all-defaults when unset.

    Best-effort: a missing board or a DB hiccup degrades to the defaults (no
    conventions, the bundled harness skill, no auto-approval), never a failed
    planning/loop run.
    """
    if not board_id:
        return _DEFAULT_PLANNING_SETTINGS
    try:
        from agent_team.features.board.repositories import boards as boards_repo

        with SessionLocal() as db:
            board = boards_repo.get_board(db, board_id)
            if board is None:
                return _DEFAULT_PLANNING_SETTINGS
            return BoardPlanningSettings(
                conventions=(getattr(board, "planning_conventions", "") or "").strip(),
                planning_skill=(getattr(board, "planning_skill", "") or "").strip(),
                auto_approve_quick=bool(
                    getattr(board, "planning_auto_approve_quick", False)
                ),
                review_max_redrafts=max(
                    0,
                    min(
                        10,
                        int(getattr(board, "planning_review_max_redrafts", 0) or 0),
                    ),
                ),
            )
    except Exception:  # noqa: BLE001 — these are knobs, never fatal
        logger.debug("loop: board planning settings load failed", exc_info=True)
        return _DEFAULT_PLANNING_SETTINGS


def _persist_loop_state(task_id: str, state: str) -> None:
    db = SessionLocal()
    try:
        db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).update(
            {"loop_state": state}
        )
        db.commit()
    finally:
        db.close()


def _publish_goal_snapshot_ready(task_id: str) -> None:
    """Invalidate cockpit proof/history only after its durable snapshot commits.

    The loop emits its terminal status just before returning to this service.
    Without this second lightweight hint, a fast browser can refetch history in
    that small gap and cache the pre-snapshot row until the next unrelated event.
    """
    db = SessionLocal()
    try:
        task = db.get(AgentTeamTask, task_id)
        if task is None or not task.board_id:
            return
        get_board_bus().publish(
            task.board_id,
            {
                "type": "loop.status",
                "board_id": task.board_id,
                "task_id": task.id,
                "state": task.loop_state,
                "snapshot_ready": True,
            },
        )
    finally:
        db.close()


def _make_status_sink(board_id: str | None):
    """Build an ``on_status`` callback: persist loop state + notify the board."""

    def _on_status(status: LoopStatus) -> None:
        _persist_loop_state(status.task_id, status.state.value)
        if board_id:
            get_board_bus().publish(
                board_id,
                {
                    "type": "loop.status",
                    "board_id": board_id,
                    "task_id": status.task_id,
                    "state": status.state.value,
                    "attempt": status.attempt,
                    "max_attempts": status.max_attempts,
                    "outcome": status.outcome,
                    "total_tokens": status.total_tokens,
                },
            )
        # Single outbound-notification chokepoint: the gateway decides (per board
        # rules) whether this state transition warrants an external message. It
        # is best-effort and runs off-thread, so it never blocks/breaks the loop.
        try:
            from agent_team.features.comm.service import notify_loop_state

            notify_loop_state(
                task_id=status.task_id,
                board_id=board_id,
                state=status.state.value,
                attempt=status.attempt,
            )
        except Exception:  # pragma: no cover - notifications are best-effort
            logger.debug("comm: notify_loop_state failed to schedule", exc_info=True)

    return _on_status


async def run_autonomous_loop(
    *,
    task_id: str,
    agent_alias: str,
    evaluator_alias: str,
    objective: str,
    max_attempts: int = 10,
    budget: LoopBudget | None = None,
    cancel: asyncio.Event | None = None,
    strict: bool = False,
    task_graph: bool = False,
    resume_note: str | None = None,
    max_zero_streak: int = DEFAULT_MAX_ZERO_STREAK,
) -> LoopOutcome:
    """Drive a task to a verified result using real generator + evaluator runs.

    The plan is drafted and approved before this loop starts: in ``strict`` mode
    the generator is pointed at the approved ``SPEC.md``/``PLAN.md`` and the
    evaluator grades against them. When ``task_graph`` is set (and a non-empty
    ``TASKS.json`` exists), execution is scheduled task-by-task from that file
    instead of running one loop over the whole objective.
    """
    workspace_path, board_id, _approved_contract_etag = await asyncio.to_thread(
        _task_workspace_and_board, task_id
    )
    # The board's planning house rules ride into every strict phase prompt
    # (generator preamble + evaluator) so a team's stated best practices govern
    # execution too, not just the plan draft.
    conventions = (
        await asyncio.to_thread(_board_planning_settings, board_id)
    ).conventions
    generator = BackendGenerator(
        task_id=task_id, agent_alias=agent_alias, workspace_path=workspace_path
    )
    on_status = _make_status_sink(board_id)
    # In strict mode the generator can pause execution by writing the
    # plan-change-request marker, or by raising blocking questions; detect both
    # after each turn.
    replan = (
        (lambda: artifacts.exists(workspace_path, artifacts.PLAN_CHANGE_REQUEST_PATH))
        if strict
        else None
    )
    questions = (
        (lambda: artifacts.questions_pending(workspace_path)) if strict else None
    )

    if strict and task_graph and artifacts.task_list(workspace_path):
        from agent_team.features.board.runtime.loop.task_graph import run_task_graph

        # Self-heal artifacts left root-owned by older sandbox turns. Without
        # this, a resumed graph fails on its first host-side status write before
        # any new agent turn can reach the normal pre-pause ownership handoff.
        tasks_abs_path = os.path.join(workspace_path, artifacts.TASKS_PATH)
        if not os.access(tasks_abs_path, os.W_OK):
            await repair_workspace_ownership(
                task_id=task_id,
                host_workspace_path=workspace_path,
                profile=resolve_profile(task_id, board_id),
                board_id=board_id,
            )

        def make_evaluator(graph_task: dict | None) -> WorkerEvaluator:
            return WorkerEvaluator(
                task_id=task_id,
                evaluator_alias=evaluator_alias,
                strict=True,
                graph_task=graph_task,
                conventions=conventions,
            )

        # Conventions + (on resume) the human's answers ride in the per-task
        # extra preamble, appended after TASK_GRAPH_PREAMBLE by the orchestrator.
        extra_parts = [planning_prompts.conventions_block(conventions)]
        if resume_note and resume_note.strip():
            extra_parts.append(resume_note.strip())
        extra = "\n\n".join(p for p in extra_parts if p) or None

        return await run_task_graph(
            task_id=task_id,
            objective=objective,
            workspace_path=workspace_path,
            run_generator=generator,
            make_evaluator=make_evaluator,
            max_attempts_per_task=max_attempts,
            budget=budget,
            cancel=cancel,
            on_status=on_status,
            final_verify=True,
            replan_requested=replan,
            questions_pending=questions,
            extra_preamble=extra,
            max_zero_streak=max_zero_streak,
        )

    # On resume after a question pause, fold the human's answers into the
    # generator preamble so the continuing thread proceeds with them.
    generator_preamble = (
        planning_prompts.strict_generator_preamble(conventions=conventions)
        if strict
        else None
    )
    if resume_note and resume_note.strip():
        generator_preamble = (
            f"{generator_preamble}\n\n{resume_note.strip()}"
            if generator_preamble
            else resume_note.strip()
        )

    return await run_loop(
        task_id=task_id,
        objective=objective,
        workspace_path=workspace_path,
        run_generator=generator,
        evaluator=WorkerEvaluator(
            task_id=task_id,
            evaluator_alias=evaluator_alias,
            strict=strict,
            conventions=conventions,
        ),
        max_attempts=max_attempts,
        budget=budget,
        cancel=cancel,
        on_status=on_status,
        plan_path=artifacts.PLAN_PATH if strict else None,
        preamble=generator_preamble,
        replan_requested=replan,
        questions_pending=questions,
        max_zero_streak=max_zero_streak,
    )


def start_autonomous_loop(
    *,
    task_id: str,
    agent_alias: str,
    evaluator_alias: str,
    objective: str,
    max_attempts: int = 10,
    budget: LoopBudget | None = None,
    strict: bool = False,
    task_graph: bool = False,
    resume_note: str | None = None,
    max_zero_streak: int = DEFAULT_MAX_ZERO_STREAK,
) -> asyncio.Task:
    """Launch the loop as a background task on the running event loop.

    Starting a loop for a task that already has one running is a no-op (the
    existing task is returned), so a double-click never spawns two drivers.
    """
    existing = _RUNNING_LOOPS.get(task_id)
    if existing is not None and not existing.task.done():
        return existing.task

    cancel = asyncio.Event()

    async def _go() -> None:
        try:
            from agent_team.features.board.runtime import goal_runs

            await asyncio.to_thread(goal_runs.mark_current_started_for_task, task_id)
            outcome = await run_autonomous_loop(
                task_id=task_id,
                agent_alias=agent_alias,
                evaluator_alias=evaluator_alias,
                objective=objective,
                max_attempts=max_attempts,
                budget=budget,
                cancel=cancel,
                strict=strict,
                task_graph=task_graph,
                resume_note=resume_note,
                max_zero_streak=max_zero_streak,
            )
            logger.info(
                "autonomous loop finished task=%s outcome=%s attempts=%s",
                task_id, outcome.outcome, outcome.attempts,
            )
            try:
                await asyncio.to_thread(
                    goal_runs.refresh_current_goal_run,
                    task_id,
                    outcome=outcome.outcome,
                )
                await asyncio.to_thread(_publish_goal_snapshot_ready, task_id)
            except Exception:  # noqa: BLE001 — history capture must not mask outcome
                logger.exception("could not snapshot goal run for task %s", task_id)
        except Exception:  # noqa: BLE001 — never let the loop crash the event loop
            logger.exception("autonomous loop failed for task %s", task_id)
            try:
                _persist_loop_state(task_id, LoopState.FAILED.value)
            except Exception:  # noqa: BLE001
                logger.warning("could not persist FAILED state for task %s", task_id)
            try:
                from agent_team.features.board.runtime import goal_runs

                await asyncio.to_thread(
                    goal_runs.refresh_current_goal_run, task_id, outcome="failed"
                )
                await asyncio.to_thread(_publish_goal_snapshot_ready, task_id)
            except Exception:  # noqa: BLE001
                logger.exception("could not snapshot failed goal run for task %s", task_id)
        finally:
            current = _RUNNING_LOOPS.get(task_id)
            if current is not None and current.cancel is cancel:
                _RUNNING_LOOPS.pop(task_id, None)

    task = asyncio.create_task(_go())
    _RUNNING_LOOPS[task_id] = _RunningLoop(task=task, cancel=cancel)
    return task
