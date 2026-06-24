"""Per-task cron schedules: schedule math and the periodic fire tick.

This mirrors ``runtime.autopilot`` but at the *task* level. :func:`run_tick` is
what the background ticker calls each interval; for each enabled schedule whose
``next_run_at`` is due it advances the cursor first (at-most-once), then starts
one agent run with the configured opening prompt.

Differences from the board autopilot:

* A scheduled run **never moves the task between columns** (it uses
  ``trigger="schedule"``; ``autopilot.on_run_finished`` only acts on
  ``trigger="autopilot"``).
* If the previous scheduled run is still in flight, the due tick is **skipped**
  rather than queued or run in parallel.
* The agent and opening prompt come from the schedule itself, not the board.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    TASK_SCHEDULE_MODE_NEW,
    AgentTeamRun,
    AgentTeamTaskSchedule,
)
from agent_team.features.board.repositories import activity as activity_repo
from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import conversations as conversations_repo
from agent_team.features.board.repositories import task_schedule as schedule_repo
from agent_team.features.board.repositories import tasks as tasks_repo
from agent_team.features.board.runtime import dispatch, run_service
from agent_team.features.board.runtime.autopilot import _tzinfo, is_valid_cron
from agent_team.features.board.runtime.events import TERMINAL_RUN_STATUSES
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

#: One-shot guard so the "main loop not captured" warning is logged at most once.
_WARNED_NO_LOOP = False

#: Seed prompt used when a schedule has no custom opening message configured.
_DEFAULT_PROMPT = (
    "Please work on this task and complete it. If you need the full task brief, "
    "read `.agent-team/TASK.md`."
)


# ── schedule math ──────────────────────────────────────────────────────────


def compute_next_run_at(
    row: AgentTeamTaskSchedule, base: datetime | None = None
) -> datetime | None:
    """Return the next due time (UTC) for the schedule, or ``None`` if invalid."""
    expr = (row.cron or "").strip()
    if not is_valid_cron(expr):
        return None
    from croniter import croniter

    now = base or datetime.now(UTC)
    local_now = now.astimezone(_tzinfo(row.timezone))
    nxt = croniter(expr, local_now).get_next(datetime)
    return nxt.astimezone(UTC)


# ── tick ─────────────────────────────────────────────────────────────────────


def run_tick() -> int:
    """Fire all due task schedules once; return the number of runs started."""
    if not dispatch.main_loop_ready():
        global _WARNED_NO_LOOP
        if not _WARNED_NO_LOOP:
            logger.warning(
                "task schedule: tick deferred — app main loop not captured yet"
            )
            _WARNED_NO_LOOP = True
        return 0

    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        due = schedule_repo.list_due(db, now)
        if due:
            logger.debug("task schedule: tick — %d schedule(s) due", len(due))
        started = 0
        for row in due:
            # Advance the cursor first so a slow/failing fire can't be picked
            # twice for the same slot.
            row.next_run_at = compute_next_run_at(row, base=now)
            row.last_run_at = now
            db.commit()
            try:
                if _fire(db, row, now):
                    started += 1
            except Exception:  # noqa: BLE001 — one schedule must not break the rest
                logger.exception(
                    "task schedule: fire failed for task %s", row.task_id
                )
                db.rollback()
        return started
    finally:
        db.close()


def _has_active_run(db: Session, task_id: str, agent: str) -> bool:
    """True if this agent already has an in-flight run on the task.

    Checks *any* trigger (schedule, autopilot, or manual mention), not just
    scheduled runs: a scheduled fire shares the agent's conversation thread, so
    starting one while the same agent is mid-run (e.g. claimed by autopilot, or
    chatting with a human) would collide on that thread. Different agents run on
    separate threads, so they don't block each other.
    """
    return (
        db.query(AgentTeamRun.id)
        .filter(
            AgentTeamRun.task_id == task_id,
            AgentTeamRun.agent_alias == agent,
            AgentTeamRun.status.notin_(tuple(TERMINAL_RUN_STATUSES)),
        )
        .first()
        is not None
    )


def _skip(db: Session, task_id: str, reason: str) -> bool:
    """Record a skipped fire and commit. Returns False (no run started)."""
    activity_repo.record(
        db,
        task_id=task_id,
        actor_id=None,
        kind=activity_repo.SCHEDULE_SKIPPED,
        data={"reason": reason},
    )
    db.commit()
    logger.debug("task schedule: skip task %s — %s", task_id, reason)
    return False


def _fire(db: Session, row: AgentTeamTaskSchedule, now: datetime) -> bool:
    """Start one scheduled run for the task; True on success."""
    task = tasks_repo.get_task(db, row.task_id)
    if task is None or task.archived:
        return _skip(db, row.task_id, "task missing or archived")

    agent = (row.agent_alias or "").strip()
    if not agent:
        return _skip(db, row.task_id, "no agent configured")

    board = boards_repo.get_board(db, task.board_id)
    if board is None:
        return _skip(db, row.task_id, "board missing")
    staffed = set(board.agent_ids()) | set(board.cli_target_ids())
    if agent not in staffed:
        return _skip(db, row.task_id, f"agent {agent} not staffed on board")

    # Skip (don't queue) when this agent already has a run in flight on the task
    # — whether from a prior fire, autopilot, or a human chat — so we never start
    # two concurrent runs on the same conversation thread.
    if _has_active_run(db, row.task_id, agent):
        return _skip(db, row.task_id, "agent already has a run in flight")

    # A fresh conversation each time, or append to the agent's existing thread.
    if row.conversation_mode == TASK_SCHEDULE_MODE_NEW:
        conversations_repo.reset_conversation(
            db, task_id=row.task_id, agent_alias=agent
        )

    prompt = (row.prompt or "").strip() or _DEFAULT_PROMPT
    run, _conv = run_service.create_run_for_task(
        db,
        task_id=row.task_id,
        agent_alias=agent,
        prompt=prompt,
        trigger="schedule",
        actor_id=None,
    )
    row.last_run_id = run.id
    activity_repo.record(
        db,
        task_id=row.task_id,
        actor_id=None,
        kind=activity_repo.SCHEDULE_FIRED,
        data={"agent_id": agent, "run_id": run.id, "mode": row.conversation_mode},
    )
    db.commit()
    db.refresh(run)

    if not dispatch.dispatch_start(run.id):
        logger.warning(
            "task schedule: could not dispatch run %s (loop not ready)", run.id
        )
        return False

    bus = get_board_bus()
    bus.publish(
        board.id,
        {
            "type": "run.started",
            "board_id": board.id,
            "task_id": row.task_id,
            "agent_id": agent,
            "run_id": run.id,
            "actor_id": None,
        },
    )
    logger.info(
        "task schedule: fired task %s for %s (run %s)", row.task_id, agent, run.id
    )
    return True
