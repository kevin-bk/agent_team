"""Task queries and serialization."""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.board.keys import TASK_KEY_PREFIX, next_human_key
from agent_team.features.board.models import AgentTeamBoard, AgentTeamTask
from agent_team.features.board.schemas import TaskDTO
from agent_team.features.board.workspace import workspace_path_for


def list_tasks(
    db: Session, *, board_id: str, include_archived: bool = False
) -> list[AgentTeamTask]:
    query = db.query(AgentTeamTask).filter(AgentTeamTask.board_id == board_id)
    if not include_archived:
        query = query.filter(AgentTeamTask.archived.is_(False))
    return query.order_by(AgentTeamTask.status, AgentTeamTask.position).all()


def get_task(db: Session, task_id: str) -> AgentTeamTask | None:
    return db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).first()


def get_task_by_jira_key(
    db: Session, *, board_id: str, jira_key: str
) -> AgentTeamTask | None:
    """Find a (non-archived) task on this board already linked to ``jira_key``."""
    return (
        db.query(AgentTeamTask)
        .filter(
            AgentTeamTask.board_id == board_id,
            AgentTeamTask.jira_key == jira_key,
            AgentTeamTask.archived.is_(False),
        )
        .first()
    )


def _next_position(db: Session, *, board_id: str, status: str) -> float:
    """Append a new card to the end of its column."""
    current_max = (
        db.query(func.max(AgentTeamTask.position))
        .filter(AgentTeamTask.board_id == board_id, AgentTeamTask.status == status)
        .scalar()
    )
    return float(current_max or 0.0) + 1.0


def create_task(
    db: Session,
    *,
    board_id: str,
    title: str,
    description: str | None,
    status: str,
    assignee_id: str | None,
    labels: list[str] | None,
    priority: str | None,
    created_by: str | None,
    task_type: str = "task",
) -> AgentTeamTask:
    human_key = next_human_key(db, TASK_KEY_PREFIX)
    board_slug = (
        db.query(AgentTeamBoard.slug).filter(AgentTeamBoard.id == board_id).scalar()
        or board_id
    )
    task = AgentTeamTask(
        human_key=human_key,
        board_id=board_id,
        title=title.strip(),
        description=(description or None),
        task_type=task_type or "task",
        status=status,
        position=_next_position(db, board_id=board_id, status=status),
        assignee_id=assignee_id,
        labels_json=json.dumps(list(labels or [])),
        priority=priority,
        workspace_path=workspace_path_for(board_slug, human_key),
        created_by=created_by,
    )
    db.add(task)
    db.flush()
    return task


def serialize_task(task: AgentTeamTask) -> TaskDTO:
    return TaskDTO(
        id=task.id,
        human_key=task.human_key,
        board_id=task.board_id,
        title=task.title,
        description=task.description,
        status=task.status,
        position=task.position,
        assignee_id=task.assignee_id,
        labels=task.labels(),
        priority=task.priority,
        task_type=task.task_type,
        jira_key=task.jira_key,
        jira_url=task.jira_url,
        workspace_path=task.workspace_path,
        created_by=task.created_by,
        archived=task.archived,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )
