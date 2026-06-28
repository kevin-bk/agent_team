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
    AgentTeamTask,
)
from agent_team.features.board.runtime import task_journal
from agent_team.features.board.runtime.events import RUN_DONE
from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
from agent_team.features.board.runtime.loop import planning_prompts
from agent_team.features.board.runtime.loop.service import (
    _create_loop_run,
    _drive_to_completion,
    _task_workspace_and_board,
)
from agent_team.features.board.runtime.loop.status import LoopState
from agent_team.features.board.runtime.loop.verdict import parse_verdict
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class _RunningPlan:
    task: asyncio.Task


#: task_id → the planning job currently drafting it (so a second start is a
#: no-op). Cleared when the background task settles.
_RUNNING_PLANS: dict[str, _RunningPlan] = {}


def is_planning_running(task_id: str) -> bool:
    """Whether a strict planning job is actively drafting ``task_id`` now."""
    plan = _RUNNING_PLANS.get(task_id)
    return plan is not None and not plan.task.done()


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
) -> LoopState:
    """Run the planner (and optional reviewer), then park for human approval.

    Returns the terminal :class:`LoopState`. The job never waits for the human;
    it persists ``waiting_plan_approval`` (or ``failed`` when the planner could
    not produce the required artifacts) and returns.
    """
    workspace_path, board_id = await asyncio.to_thread(
        _task_workspace_and_board, task_id
    )
    _persist_planning(
        task_id,
        state=LoopState.PLANNING,
        meta_updates={"approved": False, "last_error": None},
        board_id=board_id,
    )

    prompt = planning_prompts.build_planning_prompt(
        objective, task_id=task_id, workspace_path=workspace_path
    )
    run_id = await asyncio.to_thread(
        _create_loop_run,
        task_id=task_id,
        agent_alias=planner_alias,
        prompt=prompt,
        role=RUN_ROLE_PLANNER,
        attempt_id=None,
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

    review_verdict: str | None = None
    if reviewer_alias:
        review_verdict = await _run_reviewer(
            task_id=task_id,
            reviewer_alias=reviewer_alias,
            workspace_path=workspace_path,
        )
        task_journal.record(
            task_id=task_id,
            phase="review",
            type="plan_review",
            title=f"Plan reviewer verdict: {review_verdict or 'unknown'}",
            actor_type="agent",
            actor_id=reviewer_alias,
            severity="warning" if review_verdict not in (None, "pass") else "info",
            refs=task_journal.refs(artifacts=[artifacts.PLAN_REVIEW_PATH]),
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
            "approved": False,
            "review_verdict": review_verdict,
            "last_error": None,
            # Remembered so "request changes" can re-draft with the same agents.
            "planner_id": planner_alias,
            "reviewer_id": reviewer_alias,
        },
        board_id=board_id,
    )
    return LoopState.WAITING_PLAN_APPROVAL


async def _run_reviewer(
    *, task_id: str, reviewer_alias: str, workspace_path: str
) -> str | None:
    """Run the optional adversarial plan reviewer; return its verdict string."""
    run_id = await asyncio.to_thread(
        _create_loop_run,
        task_id=task_id,
        agent_alias=reviewer_alias,
        prompt=planning_prompts.build_review_prompt(),
        role=RUN_ROLE_PLANNER,
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
        return None
    data = artifacts.read_json(workspace_path, artifacts.PLAN_REVIEW_PATH)
    if isinstance(data, dict) and data.get("verdict"):
        return str(data["verdict"])
    verdict = parse_verdict(result.final_answer)
    return verdict.verdict.value if verdict is not None else None


def start_planning_job(
    *,
    task_id: str,
    planner_alias: str,
    objective: str,
    reviewer_alias: str | None = None,
) -> asyncio.Task:
    """Launch the planning job as a background task; a double-start is a no-op."""
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
            )
        except Exception:  # noqa: BLE001 — never let planning crash the event loop
            logger.exception("planning job failed for task %s", task_id)
        finally:
            _RUNNING_PLANS.pop(task_id, None)

    task = asyncio.create_task(_go())
    _RUNNING_PLANS[task_id] = _RunningPlan(task=task)
    return task
