"""Platform-level REST endpoints for Agent Team (not tied to one feature)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from agent_team.web import API_PREFIX, auth_or_401
from core.database.base import get_db

router = APIRouter(prefix=API_PREFIX, tags=["agent-team"])


def _is_admin(user) -> bool:
    role = getattr(user.role, "value", user.role)
    return str(role).lower() == "admin"


@router.get("/me")
async def get_me(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    return {
        "user_id": user.id,
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.full_name or user.username,
        "is_admin": _is_admin(user),
    }


@router.get("/profiles")
async def list_profiles(request: Request, db: Session = Depends(get_db)):
    """Profiles are a deep-agent concept the FE probes on boot.

    Agent Team has no profiles, so return an empty list to keep the shared web
    UI happy without coupling it to a backend it does not use.
    """
    _, err = auth_or_401(db, request)
    if err:
        return err
    return []


@router.get("/users")
async def list_users(request: Request, q: str | None = None, db: Session = Depends(get_db)):
    """Directory of users for assignee / author pickers."""
    user, err = auth_or_401(db, request)
    if err:
        return err

    from core.database.models import User

    query = db.query(User)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.username.ilike(like),
                User.email.ilike(like),
                User.full_name.ilike(like),
            )
        )
    rows = query.order_by(User.username.asc()).limit(50).all()
    return [
        {
            "id": row.id,
            "email": row.email,
            "display_name": row.full_name or row.username,
            "avatar_url": None,
        }
        for row in rows
    ]
