"""Shared web helpers for Agent Team feature routers.

Authentication reuses the core session cookie; unauthenticated API requests get
a JSON 401 (the single-page app redirects to ``/login`` on that status).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.auth.service import get_current_user_from_request

#: Common URL prefix for every Agent Team REST router.
API_PREFIX = "/api/agent-team"


def auth_or_401(db: Session, request: Request):
    """Return ``(user, None)`` when authenticated, else ``(None, JSONResponse)``."""
    user = get_current_user_from_request(db, request)
    if user is None:
        return None, JSONResponse(
            status_code=401, content={"detail": "Authentication required"}
        )
    return user, None


def not_found(detail: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": detail})
