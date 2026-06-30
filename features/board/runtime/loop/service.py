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

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    RUN_ROLE_EVALUATOR,
    RUN_ROLE_GENERATOR,
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
from agent_team.features.board.runtime.loop import planning_prompts
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
from agent_team.features.board.runtime.loop.status import LoopStatus
from agent_team.features.board.runtime.loop.verdict import (
    LoopVerdict,
    has_verification_evidence,
    parse_verdict,
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
    *, task_id: str, agent_alias: str, prompt: str, role: str, attempt_id: str | None
) -> str:
    """Create a queued run tagged with its loop role + attempt (own session).

    The conversation is scoped per role so the planner, generator and evaluator
    each run in their own agent session — never one shared process. The
    evaluator gets a brand-new thread every time (``fresh``) so each grading is
    independent of the generation it judges and of any prior verdict.
    """
    db = SessionLocal()
    try:
        conv = conversations_repo.get_or_create_loop_conversation(
            db,
            task_id=task_id,
            agent_alias=agent_alias,
            role=role,
            fresh=role == RUN_ROLE_EVALUATOR,
        )
        run = runs_repo.create_run(
            db,
            task_id=task_id,
            conversation=conv,
            agent_alias=agent_alias,
            trigger="loop",
            actor_id=None,
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


def _downgrade_unverified_pass(verdict: Verdict) -> Verdict:
    """Turn an evidence-less ``pass`` into a ``fail`` so the loop keeps going.

    Preserves the score/evidence and the accounting fields; prepends a clear
    note to ``missing`` so the next attempt knows real verification is required.
    The attempt budget remains the backstop if the evaluator keeps refusing to
    produce evidence.
    """
    missing = (verdict.missing or "").strip()
    combined = f"{_UNVERIFIED_PASS_NOTE}\n\n{missing}" if missing else _UNVERIFIED_PASS_NOTE
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
    ) -> None:
        self._task_id = task_id
        self._evaluator_alias = evaluator_alias
        self._strict = strict
        #: When set (task-graph execution), grade only this single plan task
        #: rather than the whole SPEC.
        self._graph_task = graph_task

    async def evaluate(
        self,
        *,
        objective: str,
        generator_summary: str,
        workspace_path: str,
        attempt_id: str | None = None,
    ) -> Verdict | None:
        if self._strict and self._graph_task is not None:
            prompt = planning_prompts.build_task_evaluator_prompt(
                task=self._graph_task,
                generator_summary=generator_summary,
                verdict_path=artifacts.EVIDENCE_PATH,
            )
        elif self._strict:
            prompt = planning_prompts.build_strict_evaluator_prompt(
                objective=objective,
                generator_summary=generator_summary,
                verdict_path=artifacts.EVIDENCE_PATH,
            )
        else:
            # Unique per evaluation so a leftover file from a prior attempt can
            # never be read back as this attempt's verdict.
            rel_path = f"{_VERDICT_DIR}/verdict-{uuid.uuid4().hex}.json"
            prompt = build_evaluator_prompt(
                objective, generator_summary, verdict_path=rel_path
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
        if verdict.verdict == LoopVerdict.PASS and not has_verification_evidence(verdict):
            return _downgrade_unverified_pass(verdict)
        return verdict


def _task_workspace_and_board(task_id: str) -> tuple[str, str | None]:
    db = SessionLocal()
    try:
        task = get_task(db, task_id)
        if task is None:
            return "", None
        return task.workspace_path, task.board_id
    finally:
        db.close()


def _persist_loop_state(task_id: str, state: str) -> None:
    db = SessionLocal()
    try:
        db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).update(
            {"loop_state": state}
        )
        db.commit()
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
    workspace_path, board_id = await asyncio.to_thread(
        _task_workspace_and_board, task_id
    )
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

        def make_evaluator(graph_task: dict | None) -> WorkerEvaluator:
            return WorkerEvaluator(
                task_id=task_id,
                evaluator_alias=evaluator_alias,
                strict=True,
                graph_task=graph_task,
            )

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
            extra_preamble=resume_note,
            max_zero_streak=max_zero_streak,
        )

    # On resume after a question pause, fold the human's answers into the
    # generator preamble so the continuing thread proceeds with them.
    generator_preamble = planning_prompts.GENERATOR_STRICT_PREAMBLE if strict else None
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
            task_id=task_id, evaluator_alias=evaluator_alias, strict=strict
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
        except Exception:  # noqa: BLE001 — never let the loop crash the event loop
            logger.exception("autonomous loop failed for task %s", task_id)
        finally:
            current = _RUNNING_LOOPS.get(task_id)
            if current is not None and current.cancel is cancel:
                _RUNNING_LOOPS.pop(task_id, None)

    task = asyncio.create_task(_go())
    _RUNNING_LOOPS[task_id] = _RunningLoop(task=task, cancel=cancel)
    return task
