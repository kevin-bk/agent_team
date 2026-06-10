"""Board queries and serialization."""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.board.keys import slugify
from agent_team.features.board.models import (
    DEFAULT_BOARD_COLUMNS,
    AgentTeamBoard,
    AgentTeamTask,
)
from agent_team.features.board.schemas import BoardColumn, BoardDTO


def _unique_slug(db: Session, name: str) -> str:
    """Return a slug derived from ``name`` that is not yet used."""
    base = slugify(name)
    candidate = base
    suffix = 2
    while db.query(AgentTeamBoard.id).filter(AgentTeamBoard.slug == candidate).first():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def list_boards(db: Session, *, include_archived: bool = False) -> list[AgentTeamBoard]:
    query = db.query(AgentTeamBoard)
    if not include_archived:
        query = query.filter(AgentTeamBoard.archived.is_(False))
    return query.order_by(AgentTeamBoard.updated_at.desc()).all()


def get_board(db: Session, board_id: str) -> AgentTeamBoard | None:
    return db.query(AgentTeamBoard).filter(AgentTeamBoard.id == board_id).first()


def create_board(
    db: Session,
    *,
    name: str,
    description: str | None,
    columns: list[BoardColumn] | None,
    owner_id: str | None,
) -> AgentTeamBoard:
    column_dicts = (
        [{"key": c.key, "name": c.name} for c in columns]
        if columns
        else list(DEFAULT_BOARD_COLUMNS)
    )
    board = AgentTeamBoard(
        slug=_unique_slug(db, name),
        name=name.strip(),
        description=(description or None),
        owner_id=owner_id,
        columns_json=json.dumps(column_dicts),
    )
    db.add(board)
    db.flush()
    return board


def task_counts_by_board(db: Session, board_ids: list[str]) -> dict[str, int]:
    """Return ``{board_id: live_task_count}`` for the given boards."""
    if not board_ids:
        return {}
    rows = (
        db.query(AgentTeamTask.board_id, func.count(AgentTeamTask.id))
        .filter(
            AgentTeamTask.board_id.in_(board_ids),
            AgentTeamTask.archived.is_(False),
        )
        .group_by(AgentTeamTask.board_id)
        .all()
    )
    return {board_id: int(count) for board_id, count in rows}


def serialize_board(
    board: AgentTeamBoard, *, task_count: int = 0, my_role: str | None = None
) -> BoardDTO:
    return BoardDTO(
        id=board.id,
        slug=board.slug,
        name=board.name,
        description=board.description,
        owner_id=board.owner_id,
        columns=[BoardColumn(key=c["key"], name=c["name"]) for c in board.columns()],
        agent_ids=board.agent_ids(),
        archived=board.archived,
        task_count=task_count,
        my_role=my_role,
        jira_enabled=board.jira_enabled,
        jira_base_url=board.jira_base_url,
        jira_email=board.jira_email,
        jira_project_key=board.jira_project_key,
        jira_mappings=board.jira_mappings(),
        jira_sync_filter=board.jira_sync_filter(),
        jira_has_token=board.jira_has_token(),
        created_at=board.created_at.isoformat() if board.created_at else None,
        updated_at=board.updated_at.isoformat() if board.updated_at else None,
    )
