"""Durable ``key -> session_id`` persistence for ACP sessions.

Live sessions are kept in memory only, so a server restart would lose every
``session_id`` and start fresh conversations. These helpers back that cache with
the existing ``plugin_ai_acp_sessions`` table, keyed by ``alias::thread_id`` —
the *same* key scheme the legacy engine uses, so switching engines keeps a
conversation's agent-side session continuous.

All functions are synchronous and open their own short-lived session; callers on
the ACP background loop invoke them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging

from core.database.base import SessionLocal
from plugins.ai_code.models import AIAcpSession

logger = logging.getLogger(__name__)


def load_session_id(key: str) -> tuple[str, str] | None:
    """Return ``(session_id, cwd)`` stored for ``key``, or ``None``. Never raises."""
    if not key:
        return None
    try:
        with SessionLocal() as db:
            row = db.get(AIAcpSession, key)
            if row is None or not row.session_id:
                return None
            return str(row.session_id), str(row.cwd or "")
    except Exception:
        logger.warning("acp session store read failed", exc_info=True)
        return None


def save_session_id(key: str, session_id: str, cwd: str | None) -> None:
    """Upsert the ``key -> session_id`` mapping. Best-effort; never raises."""
    if not key or not session_id:
        return
    try:
        with SessionLocal() as db:
            row = db.get(AIAcpSession, key)
            if row is None:
                row = AIAcpSession(key=key, session_id=session_id, cwd=cwd or "")
                db.add(row)
            else:
                row.session_id = session_id
                row.cwd = cwd or ""
            db.commit()
    except Exception:
        logger.warning("acp session store write failed", exc_info=True)


def delete_session_id(key: str) -> None:
    """Drop the stored mapping for ``key``. Best-effort; never raises."""
    if not key:
        return
    try:
        with SessionLocal() as db:
            row = db.get(AIAcpSession, key)
            if row is not None:
                db.delete(row)
                db.commit()
    except Exception:
        logger.warning("acp session store delete failed", exc_info=True)
