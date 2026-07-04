"""Durable ``key -> ACP session_id`` persistence with a pluggable backend.

Live sessions are kept in memory only, so a server restart would lose every
``session_id`` and start fresh conversations. These helpers back that cache with
a durable store keyed by ``alias::thread_id`` — the *same* key scheme the legacy
engine uses, so switching engines keeps a conversation's agent-side session
continuous.

Two backends, chosen at import from the environment:

* **standalone SQLite** — set ``AGENT_TEAM_ACP_STORE_DB`` to a file path. Uses
  only the stdlib ``sqlite3``; it imports **nothing** from the app (``core`` /
  ``plugins``). This is what lets the ACP stack run inside an OpenSandbox image
  that ships only the ``agent_team`` runtime subtree — no ``src/``.
* **app database** (default) — when the env var is unset, falls back to the
  shared ``core.database`` + ``plugin_ai_acp_sessions`` table. That import is
  **lazy** (inside the functions) so the standalone path never touches ``src/``.

All functions are synchronous and open their own short-lived connection/session;
callers on the ACP background loop invoke them through ``asyncio.to_thread``. They
never raise — a store failure degrades to a fresh session, never a crash.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)

#: File path for the standalone SQLite backend. Empty ⇒ use the app database.
_SQLITE_PATH = os.getenv("AGENT_TEAM_ACP_STORE_DB", "").strip()

_sqlite_lock = threading.Lock()
_sqlite_ready = False


# ─── standalone SQLite backend (stdlib only; in-sandbox) ────────────────────


def _sqlite_connect() -> sqlite3.Connection:
    con = sqlite3.connect(_SQLITE_PATH, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _sqlite_ensure() -> None:
    global _sqlite_ready
    if _sqlite_ready:
        return
    with _sqlite_lock:
        if _sqlite_ready:
            return
        parent = os.path.dirname(_SQLITE_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _sqlite_connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS acp_sessions ("
                "key TEXT PRIMARY KEY, "
                "session_id TEXT NOT NULL, "
                "cwd TEXT NOT NULL DEFAULT '')"
            )
        _sqlite_ready = True


def _sqlite_load(key: str) -> tuple[str, str] | None:
    _sqlite_ensure()
    with _sqlite_connect() as con:
        row = con.execute(
            "SELECT session_id, cwd FROM acp_sessions WHERE key = ?", (key,)
        ).fetchone()
    if row is None or not row[0]:
        return None
    return str(row[0]), str(row[1] or "")


def _sqlite_save(key: str, session_id: str, cwd: str | None) -> None:
    _sqlite_ensure()
    with _sqlite_connect() as con:
        con.execute(
            "INSERT INTO acp_sessions (key, session_id, cwd) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET session_id = excluded.session_id, "
            "cwd = excluded.cwd",
            (key, session_id, cwd or ""),
        )
        con.commit()


def _sqlite_delete(key: str) -> None:
    _sqlite_ensure()
    with _sqlite_connect() as con:
        con.execute("DELETE FROM acp_sessions WHERE key = ?", (key,))
        con.commit()


# ─── app-database backend (lazy imports; host default) ──────────────────────


def _db_load(key: str) -> tuple[str, str] | None:
    from core.database.base import SessionLocal
    from plugins.ai_code.models import AIAcpSession

    with SessionLocal() as db:
        row = db.get(AIAcpSession, key)
        if row is None or not row.session_id:
            return None
        return str(row.session_id), str(row.cwd or "")


def _db_save(key: str, session_id: str, cwd: str | None) -> None:
    from core.database.base import SessionLocal
    from plugins.ai_code.models import AIAcpSession

    with SessionLocal() as db:
        row = db.get(AIAcpSession, key)
        if row is None:
            row = AIAcpSession(key=key, session_id=session_id, cwd=cwd or "")
            db.add(row)
        else:
            row.session_id = session_id
            row.cwd = cwd or ""
        db.commit()


def _db_delete(key: str) -> None:
    from core.database.base import SessionLocal
    from plugins.ai_code.models import AIAcpSession

    with SessionLocal() as db:
        row = db.get(AIAcpSession, key)
        if row is not None:
            db.delete(row)
            db.commit()


# ─── public API (backend-agnostic; never raises) ────────────────────────────


def load_session_id(key: str) -> tuple[str, str] | None:
    """Return ``(session_id, cwd)`` stored for ``key``, or ``None``. Never raises."""
    if not key:
        return None
    try:
        return _sqlite_load(key) if _SQLITE_PATH else _db_load(key)
    except Exception:
        logger.warning("acp session store read failed", exc_info=True)
        return None


def save_session_id(key: str, session_id: str, cwd: str | None) -> None:
    """Upsert the ``key -> session_id`` mapping. Best-effort; never raises."""
    if not key or not session_id:
        return
    try:
        if _SQLITE_PATH:
            _sqlite_save(key, session_id, cwd)
        else:
            _db_save(key, session_id, cwd)
    except Exception:
        logger.warning("acp session store write failed", exc_info=True)


def delete_session_id(key: str) -> None:
    """Drop the stored mapping for ``key``. Best-effort; never raises."""
    if not key:
        return
    try:
        if _SQLITE_PATH:
            _sqlite_delete(key)
        else:
            _db_delete(key)
    except Exception:
        logger.warning("acp session store delete failed", exc_info=True)
