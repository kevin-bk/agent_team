"""The strict planning phase: draft durable artifacts, then stop for a human.

Unlike the autonomous loop, planning is a *bounded* job: a planner agent
researches the workspace and writes ``SPEC.md``/``PLAN.md``/``TASKS.json``, an
optional reviewer grades them, and then the job stops and parks the task at
``waiting_plan_approval``. No process, DB connection or agent session is kept
alive while a human reviews — approval and execution are separate commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    PLANNING_MODE_STRICT,
    RUN_ROLE_PLANNER,
    RUN_ROLE_REVIEWER,
    AgentTeamTask,
)
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.runtime import task_journal
from agent_team.features.board.runtime.backend import get_run_backend
from agent_team.features.board.runtime.events import RUN_DONE
from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
from agent_team.features.board.runtime.loop import planning_prompts
from agent_team.features.board.runtime.loop.service import (
    _board_planning_settings,
    _create_loop_run,
    _drive_to_completion,
    _task_workspace_and_board,
)
from agent_team.features.board.runtime.loop.status import LoopState
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class _RunningPlan:
    task: asyncio.Task


@dataclass(frozen=True)
class _PlanReviewResult:
    """Validated result and provenance from one configured reviewer turn."""

    verdict: str
    run_id: str
    data: dict | None
    errors: tuple[str, ...]
    review_etag: str | None
    reviewed_artifact_etags: dict[str, str]


#: task_id → the planning job currently drafting it (so a second start is a
#: no-op). Cleared when the background task settles.
_RUNNING_PLANS: dict[str, _RunningPlan] = {}


def is_planning_running(task_id: str) -> bool:
    """Whether a strict planning job is actively drafting ``task_id`` now."""
    plan = _RUNNING_PLANS.get(task_id)
    return plan is not None and not plan.task.done()


def _planning_meta(task_id: str) -> dict:
    """Read the durable planning metadata for one task."""
    with SessionLocal() as db:
        task = db.get(AgentTeamTask, task_id)
        return task.planning_meta() if task is not None else {}


def _pause_requested(task_id: str) -> bool:
    with SessionLocal() as db:
        task = db.get(AgentTeamTask, task_id)
        return bool(task and task.planning_meta().get("pause_requested"))


def _park_for_guidance(
    task_id: str,
    *,
    planner_alias: str,
    reviewer_alias: str | None,
    run_id: str,
    board_id: str,
) -> LoopState:
    """Preserve partial planning work and wait for human guidance."""
    task_journal.record(
        task_id=task_id,
        phase="planning",
        type="state_change",
        title="Planning paused for human guidance",
        actor_type="system",
        severity="warning",
        refs=task_journal.refs(run_id=run_id),
    )
    _persist_planning(
        task_id,
        state=LoopState.PLANNING_PAUSED,
        meta_updates={
            "approved": False,
            "last_error": None,
            "pause_requested": False,
            "planning_paused": True,
            "planner_id": planner_alias,
            "reviewer_id": reviewer_alias,
        },
        board_id=board_id,
    )
    return LoopState.PLANNING_PAUSED


async def pause_planning_job(task_id: str, *, actor_id: str | None) -> bool:
    """Request cancellation of the active planning role and park its context."""
    db = SessionLocal()
    try:
        task = db.get(AgentTeamTask, task_id)
        if task is None or task.loop_state != LoopState.PLANNING.value:
            return False
        active = runs_repo.get_active_loop_run(db, task_id=task_id)
        if active is None and not is_planning_running(task_id):
            return False
        meta = task.planning_meta()
        meta.update(
            {
                "pause_requested": True,
                "pause_requested_by": actor_id,
            }
        )
        task.planning_meta_json = json.dumps(meta, ensure_ascii=False)
        db.commit()
        run_id = active.id if active is not None else None
    finally:
        db.close()
    if run_id:
        await get_run_backend().cancel(run_id)
    return True


def _agent_visible_workspace(host_workspace_path: str, board_id: str) -> str:
    """Return the workspace path as the agent sees it inside its sandbox.

    For OpenSandbox the host path is bind-mounted at the profile's
    ``workspace_mount_path`` (default ``/workspace``). For the local provider
    the host path is used directly.
    """
    try:
        from agent_team.features.board.runtime.sandbox.service import resolve_profile

        profile = resolve_profile(board_id=board_id)
        if profile.provider != "local":
            return profile.workspace_mount_path
    except Exception:
        logger.debug("_agent_visible_workspace: could not resolve profile", exc_info=True)
    return host_workspace_path


def _board_repo_names(board_id: str) -> list[str]:
    """Return the display names of repos assigned to the board (best-effort)."""
    if not board_id:
        return []
    try:
        from agent_team.features.repos.repositories import repos_for_board

        with SessionLocal() as db:
            return [repo.name for repo, _branch, _push, _wiki in repos_for_board(db, board_id)]
    except Exception:
        logger.debug("_board_repo_names: could not load repos", exc_info=True)
        return []


def _persist_planning(
    task_id: str,
    *,
    state: LoopState,
    meta_updates: dict | None = None,
    board_id: str | None = None,
) -> None:
    """Persist the task's loop state + merge planning metadata, then notify."""
    db = SessionLocal()
    try:
        task = db.get(AgentTeamTask, task_id)
        if task is None:
            return
        task.loop_state = state.value
        task.planning_mode = PLANNING_MODE_STRICT
        if meta_updates:
            meta = task.planning_meta()
            meta.update(meta_updates)
            task.planning_meta_json = json.dumps(meta, ensure_ascii=False)
        db.commit()
    finally:
        db.close()
    if board_id:
        get_board_bus().publish(
            board_id,
            {
                "type": "loop.status",
                "board_id": board_id,
                "task_id": task_id,
                "state": state.value,
            },
        )
        # Outbound-notification chokepoint for planning-phase states
        # (waiting_plan_approval / waiting_answers). Best-effort, off-thread.
        try:
            from agent_team.features.comm.service import notify_loop_state

            notify_loop_state(task_id=task_id, board_id=board_id, state=state.value)
        except Exception:  # pragma: no cover - notifications are best-effort
            pass


async def run_planning_job(
    *,
    task_id: str,
    planner_alias: str,
    objective: str,
    reviewer_alias: str | None = None,
    allow_auto_approve: bool = True,
    _review_addendum: str | None = None,
    guidance: str | None = None,
    actor_id: str | None = None,
) -> LoopState:
    """Run the planner (and optional reviewer), then park for human approval.

    Returns the terminal :class:`LoopState`. The job never waits for the human;
    it persists ``waiting_plan_approval`` (or ``failed`` when the planner could
    not produce the required artifacts) and returns.

    Lane-aware exception: when a configured reviewer passes a planner draft
    whose risk intake lands in the ``quick`` lane, the board opted in
    (``planning_auto_approve_quick``) and ``allow_auto_approve`` is True, the job
    stamps a *system* approval and parks at ``plan_approved`` instead. Choosing
    no reviewer is the cost-free manual-review mode and always parks for human
    approval. Re-drafts after human feedback (request changes / answered
    questions) pass ``allow_auto_approve=False`` so a human who engaged always
    gets the final look.
    """
    workspace_path, board_id, _approved_contract_etag = await asyncio.to_thread(
        _task_workspace_and_board, task_id
    )
    # Per-board guidance knobs: house rules injected into every phase prompt,
    # the board's own harness skill (SPEC/PLAN structure owner) and the
    # quick-lane auto-approval opt-in.
    settings = await asyncio.to_thread(_board_planning_settings, board_id)
    conventions, harness_skill = settings.conventions, settings.planning_skill
    review_max_redrafts = max(
        0, min(10, int(getattr(settings, "review_max_redrafts", 0) or 0))
    )
    # A public planning start creates a fresh review cycle. Recursive automatic
    # redrafts read the counter back from DB so the cap is durable and never
    # depends on the process-local _RUNNING_PLANS map.
    if _review_addendum is None:
        review_attempts = 0
        await asyncio.to_thread(artifacts.archive_plan_review, workspace_path)
        review_cycle_meta = {
            "review_verdict": None,
            "review_run_id": None,
            "review_etag": None,
            "reviewed_artifact_etags": {},
        }
    else:
        durable_meta = await asyncio.to_thread(_planning_meta, task_id)
        review_attempts = max(0, int(durable_meta.get("review_attempts") or 0))
        review_cycle_meta = {}

    # The prompt must show the workspace path as the agent will see it inside
    # the sandbox, not the host-side absolute path.
    agent_workspace_path = await asyncio.to_thread(
        _agent_visible_workspace, workspace_path, board_id
    )

    # Resolve the board's assigned repos for the "Repository" prompt field.
    repo_names = await asyncio.to_thread(_board_repo_names, board_id)

    _persist_planning(
        task_id,
        state=LoopState.PLANNING,
        meta_updates={
            **review_cycle_meta,
            "approved": False,
            "last_error": None,
            "review_attempts": review_attempts,
            "review_max_redrafts": review_max_redrafts,
            "pause_requested": False,
            "planning_paused": False,
        },
        board_id=board_id,
    )

    prompt = (
        planning_prompts.build_planning_guidance_prompt(guidance)
        if guidance and guidance.strip()
        else planning_prompts.build_planning_prompt(
            objective,
            task_id=task_id,
            workspace_path=agent_workspace_path,
            repo=", ".join(repo_names) if repo_names else None,
            conventions=conventions,
            harness_skill=harness_skill,
        )
    )
    if _review_addendum:
        prompt = f"{prompt}\n\n{_review_addendum}"
    run_id = await asyncio.to_thread(
        _create_loop_run,
        task_id=task_id,
        agent_alias=planner_alias,
        prompt=prompt,
        role=RUN_ROLE_PLANNER,
        attempt_id=None,
        actor_id=actor_id,
    )
    result = await _drive_to_completion(run_id)

    # Fold any journal notes the planner left in its inbox into the durable
    # journal (best-effort) before parking for approval/questions.
    await asyncio.to_thread(
        task_journal.ingest_agent_notes,
        task_id=task_id,
        workspace_path=workspace_path,
        actor_id=planner_alias,
        phase="planning",
    )

    if _pause_requested(task_id):
        return _park_for_guidance(
            task_id,
            planner_alias=planner_alias,
            reviewer_alias=reviewer_alias,
            run_id=run_id,
            board_id=board_id,
        )

    # The planner may stop early to ask the human blocking questions instead of
    # guessing. Honour that before treating missing artifacts as a failure: park
    # for answers and remember the agents so the answer endpoint can re-plan.
    if await asyncio.to_thread(artifacts.questions_pending, workspace_path):
        open_qs = await asyncio.to_thread(artifacts.open_questions, workspace_path)
        task_journal.record(
            task_id=task_id,
            phase="planning",
            type="question",
            title=f"Planner raised {len(open_qs)} blocking question(s)",
            body="\n".join(f"- {q.get('question', q.get('id', ''))}" for q in open_qs),
            actor_type="agent",
            actor_id=planner_alias,
            severity="blocking",
            refs=task_journal.refs(run_id=run_id, artifacts=[artifacts.QUESTIONS_PATH]),
        )
        _persist_planning(
            task_id,
            state=LoopState.WAITING_ANSWERS,
            meta_updates={
                "approved": False,
                "last_error": None,
                "planner_id": planner_alias,
                "reviewer_id": reviewer_alias,
            },
            board_id=board_id,
        )
        return LoopState.WAITING_ANSWERS

    missing = await asyncio.to_thread(artifacts.missing_required, workspace_path)
    if result.status != RUN_DONE or missing:
        error = (
            "planner run did not finish"
            if result.status != RUN_DONE
            else f"planner did not produce: {', '.join(missing)}"
        )
        task_journal.record(
            task_id=task_id,
            phase="planning",
            type="state_change",
            title="Planning failed",
            body=error,
            actor_type="agent",
            actor_id=planner_alias,
            severity="blocking",
            refs=task_journal.refs(run_id=run_id),
        )
        _persist_planning(
            task_id,
            state=LoopState.FAILED,
            meta_updates={"approved": False, "last_error": error},
            board_id=board_id,
        )
        return LoopState.FAILED

    # ── Risk intake → lane ────────────────────────────────────────────────
    # The backend recomputes the lane from the planner's INTAKE.json flags
    # (never trusting an agent-written "lane" field). No/invalid intake ⇒
    # lane None ⇒ the workflow behaves exactly as before lanes existed.
    lane_info = await asyncio.to_thread(artifacts.intake_lane, workspace_path)
    lane_meta = {
        "lane": lane_info.lane,
        "lane_flags": list(lane_info.flags),
        "lane_hard_gates": list(lane_info.hard_gates),
        "lane_input_type": lane_info.input_type,
    }
    if lane_info.lane is not None:
        gates = (
            f" — hard gates: {', '.join(lane_info.hard_gates)}"
            if lane_info.hard_gates
            else ""
        )
        task_journal.record(
            task_id=task_id,
            phase="intake",
            type="state_change",
            title=f"Risk intake: {lane_info.lane} lane{gates}",
            body=(
                f"Input type: {lane_info.input_type or 'unspecified'}; "
                f"flags set: {', '.join(lane_info.flags) or '(none)'}"
            ),
            actor_type="agent",
            actor_id=planner_alias,
            severity="warning" if lane_info.lane == artifacts.LANE_RISK else "info",
            refs=task_journal.refs(run_id=run_id, artifacts=[artifacts.INTAKE_PATH]),
            metadata=lane_meta,
        )
    if lane_info.lane == artifacts.LANE_RISK and not reviewer_alias:
        # The rigor the lane asks for is missing a leg — surface it, don't block.
        task_journal.record(
            task_id=task_id,
            phase="review",
            type="risk",
            title="Risk-lane plan has no adversarial reviewer",
            body=(
                "The intake classified this task as risk lane but planning was "
                "started without a reviewer. Consider re-planning with one, or "
                "review the artifacts extra carefully before approving."
            ),
            actor_type="system",
            severity="warning",
        )

    if _pause_requested(task_id):
        return _park_for_guidance(
            task_id,
            planner_alias=planner_alias,
            reviewer_alias=reviewer_alias,
            run_id=run_id,
            board_id=board_id,
        )

    review_verdict: str | None = None
    review_result: _PlanReviewResult | None = None
    review_meta: dict = {}
    if reviewer_alias:
        review_result = await _run_reviewer(
            task_id=task_id,
            reviewer_alias=reviewer_alias,
            workspace_path=workspace_path,
            conventions=conventions,
        )
        review_verdict = review_result.verdict
        review_data = review_result.data or {}
        blocking_issues = list(review_data.get("blocking_issues") or [])
        body_lines = blocking_issues or list(review_result.errors)
        review_metadata = {
            "risk_level": review_data.get("risk_level"),
            "verdict": review_verdict,
            "validation_errors": list(review_result.errors),
            "review_etag": review_result.review_etag,
            "reviewed_artifact_etags": review_result.reviewed_artifact_etags,
        }
        task_journal.record(
            task_id=task_id,
            phase="review",
            type="plan_review",
            title=f"Plan reviewer verdict: {review_verdict}",
            body="\n".join(body_lines),
            actor_type="agent",
            actor_id=reviewer_alias,
            severity=(
                "info"
                if review_verdict == "pass"
                else "blocking"
                if review_verdict in ("error", "needs_human")
                else "warning"
            ),
            refs=task_journal.refs(
                run_id=review_result.run_id,
                artifacts=[artifacts.PLAN_REVIEW_PATH],
            ),
            metadata=review_metadata,
        )

        review_meta = {
            "review_verdict": review_verdict,
            "review_run_id": review_result.run_id,
            "review_etag": review_result.review_etag,
            "reviewed_artifact_etags": review_result.reviewed_artifact_etags,
        }
        if _pause_requested(task_id):
            with SessionLocal() as db:
                latest = runs_repo.get_latest_task_run_by_role(
                    db, task_id=task_id, role=RUN_ROLE_REVIEWER
                )
                paused_run_id = latest.id if latest is not None else run_id
            return _park_for_guidance(
                task_id,
                planner_alias=planner_alias,
                reviewer_alias=reviewer_alias,
                run_id=paused_run_id,
                board_id=board_id,
            )
        if review_verdict == "fail" and review_attempts < review_max_redrafts:
            next_attempt = review_attempts + 1
            task_journal.record(
                task_id=task_id,
                phase="planning",
                type="state_change",
                title=(
                    "Reviewer requested plan re-draft "
                    f"{next_attempt}/{review_max_redrafts}"
                ),
                body="\n".join(blocking_issues),
                actor_type="system",
                severity="warning",
                refs=task_journal.refs(run_id=review_result.run_id),
                metadata={
                    "review_attempts": next_attempt,
                    "review_max_redrafts": review_max_redrafts,
                },
            )
            _persist_planning(
                task_id,
                state=LoopState.PLANNING,
                meta_updates={
                    **lane_meta,
                    **review_meta,
                    "auto_approved": False,
                    "approved": False,
                    "review_attempts": next_attempt,
                    "review_max_redrafts": review_max_redrafts,
                    "last_error": None,
                    "planner_id": planner_alias,
                    "reviewer_id": reviewer_alias,
                },
                board_id=board_id,
            )
            return await run_planning_job(
                task_id=task_id,
                planner_alias=planner_alias,
                objective=objective,
                reviewer_alias=reviewer_alias,
                # Auto-approval remains a first-draft-only feature.
                allow_auto_approve=False,
                _review_addendum=planning_prompts.build_review_addendum(
                    blocking_issues,
                    list(review_data.get("suggested_fixes") or []),
                ),
            )

    review_last_error: str | None = None
    if review_result is not None and review_verdict == "error":
        detail = "; ".join(review_result.errors) or "reviewer run failed"
        review_last_error = f"Plan reviewer error: {detail}"
    elif (
        review_verdict == "fail"
        and review_max_redrafts > 0
        and review_attempts >= review_max_redrafts
    ):
        review_last_error = (
            "Plan reviewer still rejected the plan after "
            f"{review_attempts} automatic re-draft(s); human approval is required."
        )

    # ── Quick-lane auto-approval (board opt-in, first draft only) ─────────
    # No reviewer is an intentional manual-review policy. Surface the reason
    # when it disables a board's auto-approve opt-in so existing boards do not
    # appear to have silently stopped auto-approving after this safety change.
    if (
        allow_auto_approve
        and settings.auto_approve_quick
        and lane_info.lane == artifacts.LANE_QUICK
        and not reviewer_alias
    ):
        task_journal.record(
            task_id=task_id,
            phase="review",
            type="guardrail",
            title="Quick-lane auto-approval skipped: no reviewer",
            body=(
                "This board enables quick-lane auto-approval, but planning was "
                "started with No reviewer. The draft is waiting for manual "
                "human approval. Select a reviewer on the next planning run if "
                "reviewer-gated auto-approval is desired."
            ),
            actor_type="system",
            severity="warning",
            refs=task_journal.refs(
                run_id=run_id,
                artifacts=[artifacts.SPEC_PATH, artifacts.PLAN_PATH, artifacts.TASKS_PATH],
            ),
            metadata={
                "reason": "missing_reviewer",
                "lane": lane_info.lane,
                "planning_auto_approve_quick": True,
            },
        )

    # A failed stamp (e.g. TASKS.json went invalid) falls through to the
    # normal human-approval park — auto-approval must never *lose* a plan.
    if _should_auto_approve(
        allow=allow_auto_approve,
        board_opt_in=settings.auto_approve_quick,
        lane=lane_info.lane,
        review_verdict=review_verdict,
    ):
        auto_error = await asyncio.to_thread(_auto_approve_plan, task_id)
        if auto_error is None:
            _persist_planning(
                task_id,
                state=LoopState.PLAN_APPROVED,
                meta_updates={
                    **lane_meta,
                    **review_meta,
                    "auto_approved": True,
                    "review_verdict": review_verdict,
                    "review_attempts": review_attempts,
                    "review_max_redrafts": review_max_redrafts,
                    "last_error": None,
                    "planner_id": planner_alias,
                    "reviewer_id": reviewer_alias,
                },
                board_id=board_id,
            )
            return LoopState.PLAN_APPROVED
        logger.warning(
            "planning: quick-lane auto-approve failed for task %s: %s",
            task_id,
            auto_error,
        )

    task_journal.record(
        task_id=task_id,
        phase="planning",
        type="state_change",
        title="Plan drafted — awaiting approval",
        actor_type="agent",
        actor_id=planner_alias,
        refs=task_journal.refs(
            run_id=run_id,
            artifacts=[artifacts.SPEC_PATH, artifacts.PLAN_PATH, artifacts.TASKS_PATH],
        ),
        metadata={"review_verdict": review_verdict},
    )
    _persist_planning(
        task_id,
        state=LoopState.WAITING_PLAN_APPROVAL,
        meta_updates={
            **lane_meta,
            **review_meta,
            "auto_approved": False,
            "approved": False,
            "review_verdict": review_verdict,
            "review_attempts": review_attempts,
            "review_max_redrafts": review_max_redrafts,
            "last_error": review_last_error,
            # Remembered so "request changes" can re-draft with the same agents.
            "planner_id": planner_alias,
            "reviewer_id": reviewer_alias,
        },
        board_id=board_id,
    )
    return LoopState.WAITING_PLAN_APPROVAL


def _should_auto_approve(
    *,
    allow: bool,
    board_opt_in: bool,
    lane: str | None,
    review_verdict: str | None,
) -> bool:
    """Whether a freshly drafted plan may skip human approval.

    Every guard must hold: the caller allows it (i.e. NOT a re-draft after a
    human requested changes or answered questions), the board opted in, the
    planner's intake genuinely classified the task as ``quick`` (a missing
    intake is never quick), and a configured adversarial reviewer explicitly
    returned ``pass``. No reviewer is the manual human-review policy, so it can
    never auto-approve. Normal/risk lanes always park for a human.
    """
    return (
        allow
        and board_opt_in
        and lane == artifacts.LANE_QUICK
        and review_verdict == "pass"
    )


def _auto_approve_plan(task_id: str) -> str | None:
    """Stamp a *system* approval on the drafted quick-lane plan.

    Reuses the same validation + etag pinning as a human approval
    (:func:`human_actions.approve_plan`), so an auto-approved plan is held to
    the identical artifact contract. Returns an error string when the stamp
    was refused (caller falls back to human approval), ``None`` on success.
    """
    from types import SimpleNamespace

    from agent_team.features.board.runtime.loop import human_actions

    db = SessionLocal()
    try:
        task = db.get(AgentTeamTask, task_id)
        if task is None:
            return "task not found"
        human_actions.approve_plan(
            db,
            task,
            SimpleNamespace(id="system:quick-lane"),
            actor_type="system",
            title="Plan auto-approved (quick lane)",
        )
        return None
    except human_actions.ActionError as e:
        return str(e)
    finally:
        db.close()


async def _run_reviewer(
    *,
    task_id: str,
    reviewer_alias: str,
    workspace_path: str,
    conventions: str = "",
) -> _PlanReviewResult:
    """Run and strictly validate one adversarial plan-review turn."""
    # Clear the active path before the run. Otherwise a crash could accidentally
    # reuse a pass written by the previous plan revision.
    await asyncio.to_thread(artifacts.archive_plan_review, workspace_path)
    reviewed_artifact_etags = await asyncio.to_thread(
        artifacts.approved_etags, workspace_path
    )
    # Snapshot the plan artifacts + source *before* the reviewer runs. The
    # reviewer executes on the real workspace with write access, so it (or a
    # prompt-injection in the content it reads) could edit SPEC/PLAN/TASKS or a
    # source file and then write a `pass`. We compare after the turn and void
    # the verdict on any drift — an independent review cannot also be the author.
    from agent_team.features.board.runtime.loop.verification_runner import (
        capture_source_state,
    )

    _source_before, source_sha_before = await asyncio.to_thread(
        capture_source_state, workspace_path
    )
    run_id = await asyncio.to_thread(
        _create_loop_run,
        task_id=task_id,
        agent_alias=reviewer_alias,
        prompt=planning_prompts.build_review_prompt(conventions=conventions),
        role=RUN_ROLE_REVIEWER,
        attempt_id=None,
    )
    result = await _drive_to_completion(run_id)
    await asyncio.to_thread(
        task_journal.ingest_agent_notes,
        task_id=task_id,
        workspace_path=workspace_path,
        actor_id=reviewer_alias,
        phase="review",
    )
    if result.status != RUN_DONE:
        return _PlanReviewResult(
            verdict="error",
            run_id=run_id,
            data=None,
            errors=(f"reviewer run ended with status {result.status!r}",),
            review_etag=None,
            reviewed_artifact_etags=reviewed_artifact_etags,
        )

    # Fail closed if the reviewer mutated the very contract it was grading.
    current_artifact_etags = await asyncio.to_thread(
        artifacts.approved_etags, workspace_path
    )
    _source_after, source_sha_after = await asyncio.to_thread(
        capture_source_state, workspace_path
    )
    if (
        current_artifact_etags != reviewed_artifact_etags
        or source_sha_after != source_sha_before
    ):
        return _PlanReviewResult(
            verdict="needs_human",
            run_id=run_id,
            data=None,
            errors=(
                "plan reviewer modified the plan artifacts or source during "
                "review; the review is not independent and cannot auto-approve — "
                "re-plan or approve manually after inspecting the changes",
            ),
            review_etag=None,
            reviewed_artifact_etags=reviewed_artifact_etags,
        )

    raw_data = await asyncio.to_thread(
        artifacts.read_json, workspace_path, artifacts.PLAN_REVIEW_PATH
    )
    errors = tuple(artifacts.validate_plan_review(raw_data))
    review_meta = await asyncio.to_thread(
        artifacts.metadata, workspace_path, artifacts.PLAN_REVIEW_PATH
    )
    data = artifacts.normalize_plan_review(raw_data) if not errors else None
    return _PlanReviewResult(
        verdict=data["verdict"] if data is not None else "error",
        run_id=run_id,
        data=data,
        errors=errors,
        review_etag=review_meta.etag if review_meta.exists else None,
        reviewed_artifact_etags=reviewed_artifact_etags,
    )


def start_planning_job(
    *,
    task_id: str,
    planner_alias: str,
    objective: str,
    reviewer_alias: str | None = None,
    allow_auto_approve: bool = True,
    guidance: str | None = None,
    actor_id: str | None = None,
) -> asyncio.Task:
    """Launch the planning job as a background task; a double-start is a no-op.

    Pass ``allow_auto_approve=False`` on re-drafts triggered by human feedback
    (request-changes, answered questions): once a human engaged, they always
    get the final approval even on a quick-lane board.
    """
    existing = _RUNNING_PLANS.get(task_id)
    if existing is not None and not existing.task.done():
        return existing.task

    async def _go() -> None:
        try:
            await run_planning_job(
                task_id=task_id,
                planner_alias=planner_alias,
                objective=objective,
                reviewer_alias=reviewer_alias,
                allow_auto_approve=allow_auto_approve,
                guidance=guidance,
                actor_id=actor_id,
            )
        except Exception:  # noqa: BLE001 — never let planning crash the event loop
            logger.exception("planning job failed for task %s", task_id)
        finally:
            _RUNNING_PLANS.pop(task_id, None)

    task = asyncio.create_task(_go())
    _RUNNING_PLANS[task_id] = _RunningPlan(task=task)
    return task
