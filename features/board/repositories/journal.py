"""Task Journal queries, append, and serialization.

The journal is an append-only semantic timeline per task. ``append_entry``
assigns a task-local monotonic ``seq`` (max+1) so entries render in a stable
order regardless of clock skew; ``list_entries`` supports cursor pagination and
type/phase/severity filtering for the cockpit.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.board.models import (
    JOURNAL_ACTOR_SYSTEM,
    JOURNAL_ACTORS,
    JOURNAL_PHASES,
    JOURNAL_SEVERITIES,
    JOURNAL_SEVERITY_INFO,
    JOURNAL_TYPES,
    AgentTeamJournalEntry,
    AgentTeamTask,
)
from agent_team.features.board.schemas import JournalEntryDTO

#: The journal ``type`` used for friction signals (see the board Friction page).
JOURNAL_TYPE_FRICTION = "friction"


def _coerce(value: str, allowed: frozenset[str], default: str) -> str:
    return value if value in allowed else default


def append_entry(
    db: Session,
    *,
    task_id: str,
    actor_type: str = JOURNAL_ACTOR_SYSTEM,
    actor_id: str | None = None,
    phase: str = "system",
    type: str = "note",
    title: str,
    body: str = "",
    severity: str = JOURNAL_SEVERITY_INFO,
    refs: dict | None = None,
    metadata: dict | None = None,
    supersedes_id: str | None = None,
) -> AgentTeamJournalEntry:
    """Append one entry, assigning the next task-local ``seq``.

    Unknown ``actor_type``/``phase``/``severity`` values are coerced to safe
    defaults; ``type`` is stored as-is (VARCHAR, no CHECK) but unknown types are
    mapped to ``note`` to keep the timeline filterable. ``title`` is clamped to
    200 chars and ``body`` to 10k chars.
    """
    next_seq = (
        db.query(func.coalesce(func.max(AgentTeamJournalEntry.seq), 0))
        .filter(AgentTeamJournalEntry.task_id == task_id)
        .scalar()
        or 0
    ) + 1
    entry = AgentTeamJournalEntry(
        task_id=task_id,
        seq=int(next_seq),
        actor_type=_coerce(actor_type, JOURNAL_ACTORS, JOURNAL_ACTOR_SYSTEM),
        actor_id=actor_id,
        phase=_coerce(phase, JOURNAL_PHASES, "system"),
        type=type if type in JOURNAL_TYPES else "note",
        title=(title or "").strip()[:200],
        body=(body or "")[:10000],
        severity=_coerce(severity, JOURNAL_SEVERITIES, JOURNAL_SEVERITY_INFO),
        refs_json=json.dumps(refs or {}, ensure_ascii=False),
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        supersedes_id=supersedes_id,
    )
    db.add(entry)
    db.flush()
    return entry


def list_entries(
    db: Session,
    task_id: str,
    *,
    limit: int = 100,
    before_seq: int | None = None,
    after_seq: int | None = None,
    type: str | None = None,
    phase: str | None = None,
    severity: str | None = None,
) -> list[AgentTeamJournalEntry]:
    """Return journal entries for a task in ascending ``seq`` order.

    ``before_seq``/``after_seq`` bound the window for cursor pagination; the
    remaining params filter by entry attributes. ``limit`` is clamped 1..500.
    """
    query = db.query(AgentTeamJournalEntry).filter(
        AgentTeamJournalEntry.task_id == task_id
    )
    if before_seq is not None:
        query = query.filter(AgentTeamJournalEntry.seq < before_seq)
    if after_seq is not None:
        query = query.filter(AgentTeamJournalEntry.seq > after_seq)
    if type:
        query = query.filter(AgentTeamJournalEntry.type == type)
    if phase:
        query = query.filter(AgentTeamJournalEntry.phase == phase)
    if severity:
        query = query.filter(AgentTeamJournalEntry.severity == severity)
    rows = (
        query.order_by(AgentTeamJournalEntry.seq.asc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return rows


def list_board_friction(
    db: Session,
    board_id: str,
    *,
    limit: int = 200,
    since: datetime | None = None,
) -> list[tuple[AgentTeamJournalEntry, AgentTeamTask]]:
    """Return ``friction`` entries across all tasks of a board, newest first.

    The journal is per-task; this joins each entry to its task so the board-level
    Friction page can show *which* task hit each friction. Ordered by
    ``created_at`` descending (then ``seq``) and clamped to ``limit`` (1..500).
    """
    query = (
        db.query(AgentTeamJournalEntry, AgentTeamTask)
        .join(AgentTeamTask, AgentTeamJournalEntry.task_id == AgentTeamTask.id)
        .filter(AgentTeamTask.board_id == board_id)
        .filter(AgentTeamJournalEntry.type == JOURNAL_TYPE_FRICTION)
    )
    if since is not None:
        query = query.filter(AgentTeamJournalEntry.created_at >= since)
    return (
        query.order_by(
            AgentTeamJournalEntry.created_at.desc(),
            AgentTeamJournalEntry.seq.desc(),
        )
        .limit(max(1, min(limit, 500)))
        .all()
    )


def serialize_board_friction(
    entry: AgentTeamJournalEntry, task: AgentTeamTask
) -> dict:
    """Flatten a friction entry + its task into the board Friction wire shape."""
    return {
        "id": entry.id,
        "task_id": task.id,
        "task_key": task.human_key,
        "task_title": task.title,
        "title": entry.title,
        "body": entry.body,
        "severity": entry.severity,
        "phase": entry.phase,
        "actor_type": entry.actor_type,
        "actor_id": entry.actor_id,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def serialize_entry(entry: AgentTeamJournalEntry) -> JournalEntryDTO:
    """Build the wire DTO for one journal entry."""
    return JournalEntryDTO(
        id=entry.id,
        task_id=entry.task_id,
        seq=entry.seq,
        actor_type=entry.actor_type,
        actor_id=entry.actor_id,
        phase=entry.phase,
        type=entry.type,
        title=entry.title,
        body=entry.body,
        severity=entry.severity,
        refs=entry.refs(),
        metadata=entry.meta(),
        supersedes_id=entry.supersedes_id,
        created_at=entry.created_at.isoformat() if entry.created_at else None,
    )
