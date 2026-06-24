"""Per-task cron schedule queries and serialization.

One :class:`AgentTeamTaskSchedule` row per task holds a recurring-run schedule.
``get_or_create`` lazily materializes a disabled default row so the API always
has a config to read; the ticker only ever sees enabled, due rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamTaskSchedule
from agent_team.features.board.schemas import TaskScheduleDTO


def get(db: Session, task_id: str) -> AgentTeamTaskSchedule | None:
    return (
        db.query(AgentTeamTaskSchedule)
        .filter(AgentTeamTaskSchedule.task_id == task_id)
        .first()
    )


def get_or_create(db: Session, task_id: str) -> AgentTeamTaskSchedule:
    """Return the task's schedule, creating a disabled default if absent."""
    row = get(db, task_id)
    if row is not None:
        return row
    row = AgentTeamTaskSchedule(task_id=task_id)
    db.add(row)
    db.flush()
    return row


def list_due(db: Session, now: datetime) -> list[AgentTeamTaskSchedule]:
    """Return enabled schedules whose ``next_run_at`` is at or before ``now``."""
    return (
        db.query(AgentTeamTaskSchedule)
        .filter(
            AgentTeamTaskSchedule.enabled.is_(True),
            AgentTeamTaskSchedule.next_run_at.is_not(None),
            AgentTeamTaskSchedule.next_run_at <= now,
        )
        .all()
    )


def serialize(row: AgentTeamTaskSchedule) -> TaskScheduleDTO:
    return TaskScheduleDTO(
        task_id=row.task_id,
        enabled=row.enabled,
        cron=row.cron,
        timezone=row.timezone,
        agent_alias=row.agent_alias,
        prompt=row.prompt,
        conversation_mode=row.conversation_mode,
        next_run_at=row.next_run_at.isoformat() if row.next_run_at else None,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        last_run_id=row.last_run_id,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )
