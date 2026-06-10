"""Board membership queries and serialization."""

from __future__ import annotations

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamBoard, AgentTeamBoardMember
from agent_team.features.board.schemas import BoardMemberDTO
from core.database.models import User

VALID_ROLES = ("owner", "editor", "viewer")


def get_role(db: Session, board_id: str, user_id: str) -> str | None:
    row = (
        db.query(AgentTeamBoardMember.role)
        .filter(
            AgentTeamBoardMember.board_id == board_id,
            AgentTeamBoardMember.user_id == user_id,
        )
        .first()
    )
    return row[0] if row else None


def effective_role(
    db: Session, board: AgentTeamBoard, *, user_id: str, is_admin: bool
) -> str:
    """Resolve a user's role: explicit owner/membership first, admin as owner."""
    if board.owner_id and board.owner_id == user_id:
        return "owner"
    role = get_role(db, board.id, user_id)
    if role:
        return role
    return "owner" if is_admin else "viewer"


def list_members(db: Session, board_id: str) -> list[tuple[AgentTeamBoardMember, User]]:
    return (
        db.query(AgentTeamBoardMember, User)
        .join(User, User.id == AgentTeamBoardMember.user_id)
        .filter(AgentTeamBoardMember.board_id == board_id)
        .order_by(AgentTeamBoardMember.created_at.asc())
        .all()
    )


def add_member(
    db: Session, *, board_id: str, user_id: str, role: str
) -> AgentTeamBoardMember:
    if role not in VALID_ROLES:
        role = "editor"
    existing = (
        db.query(AgentTeamBoardMember)
        .filter(
            AgentTeamBoardMember.board_id == board_id,
            AgentTeamBoardMember.user_id == user_id,
        )
        .first()
    )
    if existing is not None:
        existing.role = role
        db.flush()
        return existing
    member = AgentTeamBoardMember(board_id=board_id, user_id=user_id, role=role)
    db.add(member)
    db.flush()
    return member


def remove_member(db: Session, *, board_id: str, user_id: str) -> bool:
    deleted = (
        db.query(AgentTeamBoardMember)
        .filter(
            AgentTeamBoardMember.board_id == board_id,
            AgentTeamBoardMember.user_id == user_id,
        )
        .delete()
    )
    return bool(deleted)


def serialize_member(member: AgentTeamBoardMember, user: User) -> BoardMemberDTO:
    return BoardMemberDTO(
        board_id=member.board_id,
        user_id=member.user_id,
        role=member.role,
        email=user.email,
        display_name=user.full_name or user.username,
        avatar_url=None,
    )
