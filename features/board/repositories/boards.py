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
    # The digest comes from the lazy ``policy_bundle`` relationship. This
    # serializer is also used with detached rows, where a lazy load raises
    # instead of returning None — expose only the binding id in that case.
    try:
        policy = board.policy_bundle if board.policy_bundle_id else None
    except Exception:  # noqa: BLE001 — detached row: relationship unavailable
        policy = None
    return BoardDTO(
        id=board.id,
        slug=board.slug,
        name=board.name,
        description=board.description,
        owner_id=board.owner_id,
        columns=[BoardColumn(key=c["key"], name=c["name"]) for c in board.columns()],
        agent_ids=board.agent_ids(),
        cli_target_ids=board.cli_target_ids(),
        skill_ids=board.skill_ids(),
        # MCP config may carry auth tokens, so expose it only to owners (who edit
        # it in board settings); everyone else sees an empty map.
        agent_mcp=board.agent_mcp() if my_role == "owner" else {},
        starter_prompt=board.starter_prompt or "",
        planning_conventions=getattr(board, "planning_conventions", "") or "",
        planning_skill=getattr(board, "planning_skill", "") or "",
        planning_auto_approve_quick=bool(
            getattr(board, "planning_auto_approve_quick", False)
        ),
        planning_review_max_redrafts=max(
            0,
            min(
                10,
                int(getattr(board, "planning_review_max_redrafts", 0) or 0),
            ),
        ),
        policy_bundle_id=getattr(board, "policy_bundle_id", None),
        policy_bundle_sha256=(policy.bundle_sha256 if policy is not None else None),
        planning_max_tasks=int(getattr(board, "planning_max_tasks", 25) or 25),
        planning_max_total_attempts=int(
            getattr(board, "planning_max_total_attempts", 30) or 30
        ),
        # Runtime profile may carry infra tuning; expose only to owners who edit
        # it in board settings (mirrors agent_mcp handling).
        runtime_profile=board.runtime_profile() if my_role == "owner" else {},
        archived=board.archived,
        task_count=task_count,
        my_role=my_role,
        jira_enabled=board.jira_enabled,
        jira_base_url=board.jira_base_url,
        jira_email=board.jira_email,
        jira_project_key=board.jira_project_key,
        jira_mappings=board.jira_mappings(),
        jira_sync_filter=board.jira_sync_filter(),
        jira_sync_status=board.jira_sync_status,
        jira_has_token=board.jira_has_token(),
        created_at=board.created_at.isoformat() if board.created_at else None,
        updated_at=board.updated_at.isoformat() if board.updated_at else None,
    )
