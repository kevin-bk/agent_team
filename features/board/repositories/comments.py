"""Comment queries and serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamComment
from agent_team.features.board.schemas import CommentDTO
from core.database.models import User


def list_comments(db: Session, task_id: str) -> list[AgentTeamComment]:
    return (
        db.query(AgentTeamComment)
        .filter(
            AgentTeamComment.task_id == task_id,
            AgentTeamComment.deleted_at.is_(None),
        )
        .order_by(AgentTeamComment.created_at.asc())
        .all()
    )


def get_comment(db: Session, comment_id: str) -> AgentTeamComment | None:
    return db.query(AgentTeamComment).filter(AgentTeamComment.id == comment_id).first()


def create_comment(
    db: Session,
    *,
    task_id: str,
    author_id: str | None,
    body: str,
    attachments: list[dict] | None,
    visible_to_agents: bool = True,
    external_author: str | None = None,
    jira_comment_id: str | None = None,
) -> AgentTeamComment:
    comment = AgentTeamComment(
        task_id=task_id,
        author_id=author_id,
        body=body.strip(),
        attachments_json=json.dumps(list(attachments or [])),
        visible_to_agents=visible_to_agents,
        external_author=external_author,
        jira_comment_id=jira_comment_id,
    )
    db.add(comment)
    db.flush()
    return comment


def jira_comments_map(db: Session, task_id: str) -> dict[str, AgentTeamComment]:
    """Imported Jira comments for a task, keyed by their Jira comment id.

    Used on re-sync to skip unchanged comments and update edited ones.
    """
    rows = (
        db.query(AgentTeamComment)
        .filter(
            AgentTeamComment.task_id == task_id,
            AgentTeamComment.jira_comment_id.isnot(None),
            AgentTeamComment.deleted_at.is_(None),
        )
        .all()
    )
    return {c.jira_comment_id: c for c in rows if c.jira_comment_id}


def update_comment(
    db: Session,
    comment: AgentTeamComment,
    *,
    body: str | None = None,
    visible_to_agents: bool | None = None,
) -> AgentTeamComment:
    """Apply the provided changes (``updated_at`` bumps via the model's onupdate)."""
    if body is not None:
        comment.body = body.strip()
    if visible_to_agents is not None:
        comment.visible_to_agents = visible_to_agents
    db.flush()
    return comment


def soft_delete_comment(db: Session, comment: AgentTeamComment) -> None:
    comment.deleted_at = datetime.now(UTC)
    db.flush()


def serialize_comment(
    comment: AgentTeamComment, author: User | None = None
) -> CommentDTO:
    return CommentDTO(
        id=comment.id,
        task_id=comment.task_id,
        author_id=comment.author_id,
        author_name=(
            (author.full_name or author.username)
            if author
            else comment.external_author
        ),
        author_avatar=None,
        body=comment.body,
        attachments=comment.attachments(),
        visible_to_agents=comment.visible_to_agents,
        created_at=comment.created_at.isoformat() if comment.created_at else None,
        updated_at=comment.updated_at.isoformat() if comment.updated_at else None,
    )


def resolve_authors(db: Session, comments: list[AgentTeamComment]) -> dict[str, User]:
    """Bulk-load the ``User`` rows for a batch of comments, keyed by user id.

    Returned as a map so callers can serialize a whole thread with a single
    query instead of one lookup per comment.
    """
    ids = {c.author_id for c in comments if c.author_id}
    if not ids:
        return {}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()}
