"""Out-of-stream storage for full tool outputs (lazy-loaded on demand).

The streamed ``tool_use_end`` frame carries only a short preview; the complete
output is persisted here and fetched separately when the user expands a tool
card. These helpers each manage their own short-lived ``SessionLocal`` so the
async backend can call them via ``asyncio.to_thread`` without holding a session
across an ``await`` (mirrors ``event_store``).
"""

from __future__ import annotations

import logging

from agent_team.features.board.models import AgentTeamToolOutput
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


def save_tool_output(
    run_id: str, tool_id: str, content: str, *, is_error: bool = False
) -> None:
    """Persist (or overwrite) the full output for one ``(run_id, tool_id)``."""
    if not run_id or not tool_id:
        return
    db = SessionLocal()
    try:
        existing = (
            db.query(AgentTeamToolOutput)
            .filter(
                AgentTeamToolOutput.run_id == run_id,
                AgentTeamToolOutput.tool_id == tool_id,
            )
            .first()
        )
        if existing is None:
            db.add(
                AgentTeamToolOutput(
                    run_id=run_id,
                    tool_id=tool_id,
                    content=content,
                    is_error=is_error,
                )
            )
        else:
            existing.content = content
            existing.is_error = is_error
        db.commit()
    except Exception as exc:  # pragma: no cover - durability is best-effort
        logger.warning(
            "save_tool_output failed run_id=%s tool_id=%s: %s", run_id, tool_id, exc
        )
        db.rollback()
    finally:
        db.close()


def get_tool_output(run_id: str, tool_id: str) -> dict | None:
    """Return ``{content, is_error}`` for one tool, or ``None`` if absent."""
    db = SessionLocal()
    try:
        row = (
            db.query(AgentTeamToolOutput)
            .filter(
                AgentTeamToolOutput.run_id == run_id,
                AgentTeamToolOutput.tool_id == tool_id,
            )
            .first()
        )
        if row is None:
            return None
        return {"content": row.content, "is_error": row.is_error}
    finally:
        db.close()
