"""Activity changelog: record and read task events.

``record`` is used inside a request transaction (caller commits). ``record_standalone``
opens its own session for callers without one (e.g. the run backend) and is
best-effort: a logging failure never breaks the work it describes.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamActivity
from agent_team.features.board.schemas import ActivityDTO
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

# Activity kinds.
TASK_CREATED = "task_created"
TASK_UPDATED = "task_updated"
TASK_MOVED = "task_moved"
COMMENT_ADDED = "comment_added"
MENTION_CREATED = "mention_created"
RUN_FINISHED = "run_finished"
JIRA_SYNCED = "jira_synced"


def record(
    db: Session, *, task_id: str, actor_id: str | None, kind: str, data: dict | None = None
) -> AgentTeamActivity:
    activity = AgentTeamActivity(
        task_id=task_id,
        actor_id=actor_id,
        kind=kind,
        data_json=json.dumps(data or {}, ensure_ascii=False),
    )
    db.add(activity)
    db.flush()
    return activity


def record_standalone(
    *, task_id: str, actor_id: str | None, kind: str, data: dict | None = None
) -> None:
    """Record an activity in its own transaction; never raises."""
    db = SessionLocal()
    try:
        db.add(
            AgentTeamActivity(
                task_id=task_id,
                actor_id=actor_id,
                kind=kind,
                data_json=json.dumps(data or {}, ensure_ascii=False),
            )
        )
        db.commit()
    except Exception:  # pragma: no cover - best-effort logging
        logger.warning("record_standalone activity failed task_id=%s kind=%s", task_id, kind)
        db.rollback()
    finally:
        db.close()


def list_activity(db: Session, task_id: str, limit: int = 200) -> list[AgentTeamActivity]:
    return (
        db.query(AgentTeamActivity)
        .filter(AgentTeamActivity.task_id == task_id)
        .order_by(AgentTeamActivity.created_at.desc(), AgentTeamActivity.id.desc())
        .limit(limit)
        .all()
    )


def serialize_activity(activity: AgentTeamActivity) -> ActivityDTO:
    return ActivityDTO(
        id=activity.id,
        task_id=activity.task_id,
        actor_id=activity.actor_id,
        kind=activity.kind,
        data=activity.data(),
        created_at=activity.created_at.isoformat() if activity.created_at else None,
    )
