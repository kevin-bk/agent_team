"""Durable event store for runs — the source of truth for replay and SSE.

These are synchronous helpers that each open their own short-lived
``SessionLocal`` so the (async) backend can call them via ``asyncio.to_thread``
without ever holding a DB session across an ``await``. The frontend always
reads run state and frames from here, so SSE/REST do not depend on which run
backend produced them.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from agent_team.features.board.models import AgentTeamRun, AgentTeamRunEvent
from agent_team.features.board.runtime.events import (
    RUN_QUEUED,
    RUN_RUNNING,
    TERMINAL_RUN_STATUSES,
)
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


def append_event(run_id: str, event_type: str, data: dict) -> int | None:
    """Append one frame and return its run-monotonic ``seq``.

    The ``seq`` is allocated by bumping the run's ``last_seq`` in the same
    transaction, so concurrent appends cannot collide (the row update is
    serialized by the database).
    """
    db = SessionLocal()
    try:
        run = (
            db.query(AgentTeamRun)
            .filter(AgentTeamRun.id == run_id)
            .with_for_update()
            .first()
        )
        if run is None:
            return None
        run.last_seq += 1
        seq = run.last_seq
        db.add(
            AgentTeamRunEvent(
                run_id=run_id,
                seq=seq,
                type=event_type,
                data=json.dumps(data, ensure_ascii=False),
            )
        )
        db.commit()
        return seq
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("append_event failed run_id=%s: %s", run_id, exc)
        db.rollback()
        return None
    finally:
        db.close()


def list_events(run_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict]:
    """Return frames with ``seq > after_seq`` in order, for replay."""
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentTeamRunEvent)
            .filter(
                AgentTeamRunEvent.run_id == run_id,
                AgentTeamRunEvent.seq > after_seq,
            )
            .order_by(AgentTeamRunEvent.seq.asc())
            .limit(limit)
            .all()
        )
        return [
            {
                "seq": row.seq,
                "type": row.type,
                "data": json.loads(row.data or "{}"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    finally:
        db.close()


def get_run_status(run_id: str) -> str | None:
    """Return the run status, or ``None`` if the run does not exist."""
    db = SessionLocal()
    try:
        status = (
            db.query(AgentTeamRun.status).filter(AgentTeamRun.id == run_id).scalar()
        )
        return status
    finally:
        db.close()


def mark_running(run_id: str) -> None:
    """Move a run to ``running`` and stamp ``started_at`` once."""
    db = SessionLocal()
    try:
        run = db.query(AgentTeamRun).filter(AgentTeamRun.id == run_id).first()
        if run is None:
            return
        run.status = RUN_RUNNING
        if run.started_at is None:
            run.started_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def finalize_run(
    run_id: str,
    *,
    status: str,
    final_answer: str | None = None,
    error: str | None = None,
    usage: dict | None = None,
) -> None:
    """Write a run's terminal status, answer/error and token totals at once."""
    usage = usage or {}
    db = SessionLocal()
    try:
        values: dict = {
            "status": status,
            "ended_at": datetime.now(UTC),
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "cache_read_tokens": int(usage.get("cache_read_tokens", 0) or 0),
        }
        if final_answer is not None:
            values["final_answer"] = final_answer[:50000]
        if error is not None:
            values["error"] = error[:5000]
        db.query(AgentTeamRun).filter(AgentTeamRun.id == run_id).update(values)
        db.commit()
    finally:
        db.close()


def request_cancel(run_id: str) -> str:
    """Durably request cancellation.

    A still-queued run is cancelled outright; a running run gets its
    ``cancel_requested`` flag set so its owning worker stops. Returns
    ``"cancelled"``, ``"requested"`` or ``"noop"``.
    """
    db = SessionLocal()
    try:
        run = db.query(AgentTeamRun).filter(AgentTeamRun.id == run_id).first()
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return "noop"
        if run.status == RUN_QUEUED:
            from agent_team.features.board.runtime.events import RUN_CANCELLED

            run.status = RUN_CANCELLED
            run.cancel_requested = True
            run.ended_at = datetime.now(UTC)
            db.commit()
            return "cancelled"
        run.cancel_requested = True
        db.commit()
        return "requested"
    finally:
        db.close()


def is_cancel_requested(run_id: str) -> bool:
    """Return whether a cross-process cancel was requested for this run."""
    db = SessionLocal()
    try:
        flag = (
            db.query(AgentTeamRun.cancel_requested)
            .filter(AgentTeamRun.id == run_id)
            .scalar()
        )
        return bool(flag)
    finally:
        db.close()
