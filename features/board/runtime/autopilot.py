"""Board autopilot: schedule math, the periodic tick, and run-completion moves.

This module holds the *logic* (no threading): :func:`run_tick` is what the
background ticker calls each interval, and :func:`on_run_finished` is the hook
the run backend calls when any run reaches a terminal state. The thread that
drives :func:`run_tick` lives in ``autopilot_scheduler.py``.

Flow per tick:

1. Find enabled autopilots whose ``next_run_at`` is due; advance the cursor
   first (at-most-once), then process the board.
2. Scan the board's ``source_status`` column for tasks that carry an
   ``agent_assignee`` and are not in back-off, respecting two concurrency caps
   (whole-board and per-agent).
3. Claim each by moving it to ``working_status`` and starting an agent run on
   the app's main loop.

On completion the run's task moves to ``done_status`` (success),
``error_status`` (failure, with a cool-down + attempt count), or back to the
source column (cancelled).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    AUTOPILOT_MAX_INTERVAL_SECONDS,
    AUTOPILOT_MIN_INTERVAL_SECONDS,
    AUTOPILOT_SCHEDULE_CRON,
    AUTOPILOT_SCHEDULE_INTERVAL,
    AgentTeamAutopilot,
    AgentTeamRun,
    AgentTeamTask,
)
from agent_team.features.board.repositories import activity as activity_repo
from agent_team.features.board.repositories import autopilot as autopilot_repo
from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.repositories import tasks as tasks_repo
from agent_team.features.board.runtime import dispatch, run_service
from agent_team.features.board.runtime.events import (
    RUN_CANCELLED,
    RUN_DONE,
    RUN_ERROR,
    TERMINAL_RUN_STATUSES,
)
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

#: Seed user message for an auto-run when the board sets no custom template. The
#: per-agent context (LLM system prompt, or the CLI's ``.agent-team/TASK.md``) is
#: built by the normal run pipeline, so this only needs to kick the work off.
_DEFAULT_PROMPT = (
    "Please work on this task and complete it. If you need the full task brief, "
    "read `.agent-team/TASK.md`."
)


# ── schedule math ──────────────────────────────────────────────────────────


def is_valid_cron(expr: str | None) -> bool:
    expr = (expr or "").strip()
    if not expr:
        return False
    try:
        from croniter import croniter
    except ImportError:
        return False
    return bool(croniter.is_valid(expr))


def _tzinfo(name: str | None):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 — unknown tz falls back to UTC
        return UTC


def compute_next_run_at(
    row: AgentTeamAutopilot, base: datetime | None = None
) -> datetime | None:
    """Return the next due time (UTC) for the autopilot, or ``None`` if off."""
    now = base or datetime.now(UTC)
    if row.schedule_mode == AUTOPILOT_SCHEDULE_INTERVAL:
        secs = max(
            AUTOPILOT_MIN_INTERVAL_SECONDS,
            min(AUTOPILOT_MAX_INTERVAL_SECONDS, int(row.interval_seconds or 3600)),
        )
        return now + timedelta(seconds=secs)
    if row.schedule_mode == AUTOPILOT_SCHEDULE_CRON:
        expr = (row.cron or "").strip()
        if not is_valid_cron(expr):
            return None
        from croniter import croniter

        local_now = now.astimezone(_tzinfo(row.timezone))
        nxt = croniter(expr, local_now).get_next(datetime)
        return nxt.astimezone(UTC)
    return None


# ── tick ─────────────────────────────────────────────────────────────────────


def run_tick() -> int:
    """Process all due autopilots once; return the number of runs started."""
    # Runs execute on the app's main loop; if it isn't captured yet we can't
    # start anything, so defer without claiming (tasks stay in their column).
    if not dispatch.main_loop_ready():
        return 0

    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        due = (
            db.query(AgentTeamAutopilot)
            .filter(
                AgentTeamAutopilot.enabled.is_(True),
                AgentTeamAutopilot.next_run_at.is_not(None),
                AgentTeamAutopilot.next_run_at <= now,
            )
            .all()
        )
        started = 0
        for row in due:
            # Advance the schedule cursor first so a slow/failing board can't be
            # picked twice for the same slot.
            row.next_run_at = compute_next_run_at(row, base=now)
            row.last_run_at = now
            db.commit()
            try:
                started += _process_board(db, row, now)
            except Exception:  # noqa: BLE001 — one board must not break the rest
                logger.exception("autopilot: tick failed for board %s", row.board_id)
                db.rollback()
        return started
    finally:
        db.close()


def _inflight_by_agent(db: Session, board_id: str) -> dict[str, int]:
    """Count in-flight *autopilot* runs on a board, grouped by agent alias.

    Only autopilot-triggered runs count toward the autopilot concurrency caps
    (matching the status panel); manual ``@mention`` runs are not throttled by
    the board's autopilot limits.
    """
    rows = (
        db.query(AgentTeamRun.agent_alias, func.count(AgentTeamRun.id))
        .join(AgentTeamTask, AgentTeamRun.task_id == AgentTeamTask.id)
        .filter(
            AgentTeamTask.board_id == board_id,
            AgentTeamRun.trigger == "autopilot",
            AgentTeamRun.status.notin_(tuple(TERMINAL_RUN_STATUSES)),
        )
        .group_by(AgentTeamRun.agent_alias)
        .all()
    )
    return {str(alias): int(n) for alias, n in rows}


def _process_board(db: Session, row: AgentTeamAutopilot, now: datetime) -> int:
    board = boards_repo.get_board(db, row.board_id)
    if board is None:
        return 0
    column_keys = {c["key"] for c in board.columns()}
    # A renamed column keeps its key; only a removed/changed key breaks us. If
    # the source/working columns are gone, skip rather than misfile tasks.
    if row.source_status not in column_keys or row.working_status not in column_keys:
        logger.warning(
            "autopilot: board %s source/working column missing; skipping", row.board_id
        )
        return 0

    staffed = set(board.agent_ids()) | set(board.cli_target_ids())
    inflight = _inflight_by_agent(db, row.board_id)
    board_slots = row.board_concurrency - sum(inflight.values())
    if board_slots <= 0:
        return 0

    candidates = (
        db.query(AgentTeamTask)
        .filter(
            AgentTeamTask.board_id == row.board_id,
            AgentTeamTask.archived.is_(False),
            AgentTeamTask.status == row.source_status,
            AgentTeamTask.agent_assignee.is_not(None),
            AgentTeamTask.autopilot_attempts < row.max_attempts,
            or_(
                AgentTeamTask.autopilot_resume_after.is_(None),
                AgentTeamTask.autopilot_resume_after <= now,
            ),
        )
        .order_by(AgentTeamTask.position.asc())
        .all()
    )

    started = 0
    claimed_per_agent: dict[str, int] = {}
    for task in candidates:
        if started >= board_slots:
            break
        agent = (task.agent_assignee or "").strip()
        # Only run agents actually staffed/enabled on this board.
        if not agent or agent not in staffed:
            continue
        cap = row.concurrency_for(agent)
        used = inflight.get(agent, 0) + claimed_per_agent.get(agent, 0)
        if used >= cap:
            continue
        if _claim_and_start(db, row, board, task, agent):
            started += 1
            claimed_per_agent[agent] = claimed_per_agent.get(agent, 0) + 1
    return started


def _claim_and_start(
    db: Session, row: AgentTeamAutopilot, board, task: AgentTeamTask, agent: str
) -> bool:
    """Move the task to ``working`` and start an auto-run; True on success."""
    task.status = row.working_status
    activity_repo.record(
        db,
        task_id=task.id,
        actor_id=None,
        kind=activity_repo.AUTOPILOT_PICKED,
        data={"agent_id": agent, "from": row.source_status, "to": row.working_status},
    )
    run, _conv = run_service.create_run_for_task(
        db,
        task_id=task.id,
        agent_alias=agent,
        prompt=row.prompt_template or _DEFAULT_PROMPT,
        trigger="autopilot",
        actor_id=None,
    )
    db.commit()
    db.refresh(run)

    if not dispatch.dispatch_start(run.id):
        # The main loop vanished between the guard and here (very unlikely). The
        # queued run will be failed by the orphan reconciler; leave the task in
        # working so the failure path moves it, rather than silently re-claiming.
        logger.warning("autopilot: could not dispatch run %s (loop not ready)", run.id)
        return False

    bus = get_board_bus()
    bus.publish(
        board.id,
        {
            "type": "run.started",
            "board_id": board.id,
            "task_id": task.id,
            "agent_id": agent,
            "run_id": run.id,
            "actor_id": None,
        },
    )
    bus.publish(
        board.id,
        {
            "type": "task.moved",
            "board_id": board.id,
            "task_id": task.id,
            "status": row.working_status,
        },
    )
    return True


# ── routing (manual auto-assign) ─────────────────────────────────────────────


def _rule_matches(rule: dict, task: AgentTeamTask) -> bool:
    """A rule matches when every *specified* condition holds (empty = wildcard)."""
    want_labels = rule.get("labels") or []
    if want_labels:
        try:
            have = set(json.loads(task.labels_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            have = set()
        if not (have & set(want_labels)):
            return False
    want_priorities = rule.get("priorities") or []
    if want_priorities and (task.priority or "") not in want_priorities:
        return False
    return True


def route_now(db: Session, board_id: str) -> int:
    """Apply routing rules once to unassigned source-column tasks.

    First matching rule wins; its ``agents`` (filtered to those staffed on the
    board) are round-robined. Only fills tasks that have no ``agent_assignee``;
    never reassigns. Returns the number of tasks assigned. Caller commits.
    """
    row = autopilot_repo.get(db, board_id)
    if row is None:
        return 0
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return 0

    rules = row.routing_rules()
    if not rules:
        return 0
    staffed = set(board.agent_ids()) | set(board.cli_target_ids())
    rr = row.routing_rr()

    tasks = (
        db.query(AgentTeamTask)
        .filter(
            AgentTeamTask.board_id == board_id,
            AgentTeamTask.archived.is_(False),
            AgentTeamTask.status == row.source_status,
            AgentTeamTask.agent_assignee.is_(None),
        )
        .order_by(AgentTeamTask.position.asc())
        .all()
    )

    assigned = 0
    for task in tasks:
        for idx, rule in enumerate(rules):
            if not _rule_matches(rule, task):
                continue
            group = [a for a in (rule.get("agents") or []) if a in staffed]
            if not group:
                # Conditions matched but no usable agent — try the next rule.
                continue
            key = str(idx)
            offset = rr.get(key, 0) % len(group)
            agent = group[offset]
            rr[key] = offset + 1
            task.agent_assignee = agent
            task.autopilot_attempts = 0
            task.autopilot_resume_after = None
            activity_repo.record(
                db,
                task_id=task.id,
                actor_id=None,
                kind=activity_repo.AUTOPILOT_ASSIGNED,
                data={"agent_id": agent, "rule": idx},
            )
            assigned += 1
            break

    if assigned:
        row.routing_rr_json = json.dumps(rr)
        bus = get_board_bus()
        for task in tasks:
            if task.agent_assignee:
                bus.publish(
                    board_id,
                    {
                        "type": "task.updated",
                        "board_id": board_id,
                        "task_id": task.id,
                    },
                )
    return assigned


# ── run-completion transition ────────────────────────────────────────────────


def on_run_finished(run_id: str, status: str) -> None:
    """Move an autopilot run's task on completion. No-op for non-autopilot runs.

    Opens its own session and never raises (best-effort, mirrors the activity
    logger it is called alongside).
    """
    db = SessionLocal()
    try:
        run = runs_repo.get_run(db, run_id)
        if run is None or run.trigger != "autopilot":
            return
        task = tasks_repo.get_task(db, run.task_id)
        if task is None:
            return
        row = autopilot_repo.get(db, task.board_id)
        if row is None:
            return

        now = datetime.now(UTC)
        moved_to: str | None = None
        # Only move a task still sitting in the working column — a human (or, in
        # phase 2, the agent itself) may have moved it meanwhile; don't clobber.
        in_working = task.status == row.working_status

        if status == RUN_DONE:
            if in_working:
                task.status = row.done_status
                moved_to = row.done_status
            task.autopilot_attempts = 0
            task.autopilot_resume_after = None
        elif status == RUN_ERROR:
            if in_working:
                task.status = row.error_status
                moved_to = row.error_status
            task.autopilot_attempts = (task.autopilot_attempts or 0) + 1
            task.autopilot_resume_after = now + timedelta(
                seconds=max(0, int(row.error_cooldown_seconds))
            )
        elif status == RUN_CANCELLED:
            # Deliberate stop: send it back to the source column, no penalty.
            if in_working:
                task.status = row.source_status
                moved_to = row.source_status

        if moved_to is not None:
            activity_repo.record(
                db,
                task_id=task.id,
                actor_id=None,
                kind=activity_repo.AUTOPILOT_TRANSITIONED,
                data={"to": moved_to, "run_status": status, "run_id": run_id},
            )
        db.commit()

        if moved_to is not None:
            get_board_bus().publish(
                task.board_id,
                {
                    "type": "task.moved",
                    "board_id": task.board_id,
                    "task_id": task.id,
                    "status": moved_to,
                },
            )
    except Exception:  # noqa: BLE001 — completion handling must never crash a run
        logger.exception("autopilot: on_run_finished failed for run %s", run_id)
        db.rollback()
    finally:
        db.close()
