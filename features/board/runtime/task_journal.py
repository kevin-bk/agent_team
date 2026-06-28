"""Best-effort writer for the task journal.

Lifecycle code (the router and the loop runtime) records meaningful moments by
calling :func:`record` / :func:`record_with` here. Journaling must never break
the workflow it observes, so every write is best-effort: failures are logged and
swallowed, and a missing/rolled-back entry is acceptable.

Two entry points exist because callers live in two worlds:

* :func:`record_with` — when the caller already holds a ``Session`` (e.g. inside
  a request handler that will commit anyway). The entry is appended to that
  session; the caller's existing commit persists it.
* :func:`record` — when the caller has no session (e.g. background loop driver).
  A short-lived session is opened, the entry committed, and the session closed.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from agent_team.features.board.models import (
    JOURNAL_ACTOR_AGENT,
    JOURNAL_ACTOR_SYSTEM,
    JOURNAL_SEVERITY_INFO,
)
from agent_team.features.board.repositories import journal as journal_repo
from agent_team.features.board.runtime.acp.masking import SecretMasker

logger = logging.getLogger(__name__)


def record_with(
    db: Session,
    *,
    task_id: str,
    title: str,
    type: str = "note",
    phase: str = "system",
    actor_type: str = JOURNAL_ACTOR_SYSTEM,
    actor_id: str | None = None,
    body: str = "",
    severity: str = JOURNAL_SEVERITY_INFO,
    refs: dict | None = None,
    metadata: dict | None = None,
    supersedes_id: str | None = None,
) -> None:
    """Append an entry on an existing session (best-effort, never raises).

    The entry is flushed into ``db``; persistence relies on the caller's own
    commit. Use this inside request handlers that already commit their work so
    the journal entry shares the request's transaction.
    """
    try:
        journal_repo.append_entry(
            db,
            task_id=task_id,
            actor_type=actor_type,
            actor_id=actor_id,
            phase=phase,
            type=type,
            title=title,
            body=body,
            severity=severity,
            refs=refs,
            metadata=metadata,
            supersedes_id=supersedes_id,
        )
    except Exception:  # pragma: no cover - journaling is best-effort
        logger.warning("journal: failed to append entry for task %s", task_id, exc_info=True)


def record(
    *,
    task_id: str,
    title: str,
    type: str = "note",
    phase: str = "system",
    actor_type: str = JOURNAL_ACTOR_SYSTEM,
    actor_id: str | None = None,
    body: str = "",
    severity: str = JOURNAL_SEVERITY_INFO,
    refs: dict | None = None,
    metadata: dict | None = None,
    supersedes_id: str | None = None,
) -> None:
    """Append an entry on a fresh short-lived session (best-effort, never raises).

    Opens its own ``Session``, commits the entry, and closes it. Use this from
    background code (the loop driver/task-graph) that has no request session.
    """
    db = None
    try:
        from core.database.base import SessionLocal

        db = SessionLocal()
        journal_repo.append_entry(
            db,
            task_id=task_id,
            actor_type=actor_type,
            actor_id=actor_id,
            phase=phase,
            type=type,
            title=title,
            body=body,
            severity=severity,
            refs=refs,
            metadata=metadata,
            supersedes_id=supersedes_id,
        )
        db.commit()
    except Exception:  # pragma: no cover - journaling is best-effort
        logger.warning("journal: failed to record entry for task %s", task_id, exc_info=True)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        if db is not None:
            db.close()


def _collect_agent_secrets(task_id: str, agent_alias: str | None) -> list[str]:
    """Best-effort: the agent's MCP secret values, to mask in ingested notes.

    A CLI agent on the board may have MCP servers configured with auth/header/env
    values; those are the secrets at risk of being pasted into a note. Returns an
    empty list (masker inactive) for LLM agents or when anything cannot be
    resolved.
    """
    if not agent_alias:
        return []
    db = None
    try:
        from agent_team.features.board.repositories.boards import get_board
        from agent_team.features.board.repositories.tasks import get_task
        from core.database.base import SessionLocal

        db = SessionLocal()
        task = get_task(db, task_id)
        if task is None:
            return []
        board = get_board(db, task.board_id)
        if board is None:
            return []
        cfg = board.agent_mcp_for(agent_alias)
        if not isinstance(cfg, dict) or not cfg.get("mcpServers"):
            return []
        from agent_team.features.board.runtime.local_backend import _collect_mcp_secrets

        return _collect_mcp_secrets(cfg)
    except Exception:  # pragma: no cover - secret resolution is best-effort
        return []
    finally:
        if db is not None:
            db.close()


def ingest_agent_notes(
    *,
    task_id: str,
    workspace_path: str,
    actor_id: str | None = None,
    phase: str | None = None,
) -> int:
    """Ingest an agent's ``JOURNAL_NOTES.jsonl`` inbox into the durable journal.

    Reads the inbox, archives it immediately (so the same suggestions are never
    ingested twice even if the DB write below fails), masks any of the agent's
    MCP secrets out of each note, drops in-batch duplicates, and appends each as
    an ``agent``-authored entry. Best-effort: any failure is swallowed and
    returns ``0``.
    """
    if not workspace_path:
        return 0
    from agent_team.features.board.runtime.loop import planning_artifacts as _artifacts

    try:
        notes = _artifacts.read_journal_notes(workspace_path)
    except Exception:  # pragma: no cover - reading the inbox is best-effort
        return 0
    if not notes:
        return 0
    # Clear the inbox first so a later turn cannot re-ingest the same lines.
    try:
        _artifacts.archive_journal_notes(workspace_path)
    except Exception:  # pragma: no cover
        logger.warning("journal: failed to archive notes for task %s", task_id, exc_info=True)

    mask = SecretMasker(_collect_agent_secrets(task_id, actor_id))
    seen: set[tuple[str, str, str]] = set()
    ingested = 0
    db = None
    try:
        from core.database.base import SessionLocal

        db = SessionLocal()
        for note in notes:
            title = mask(note["title"])
            body = mask(note["body"])
            key = (note["type"], title, body)
            if key in seen:
                continue
            seen.add(key)
            journal_repo.append_entry(
                db,
                task_id=task_id,
                actor_type=JOURNAL_ACTOR_AGENT,
                actor_id=actor_id,
                phase=note["phase"] or phase or "execution",
                type=note["type"],
                title=title,
                body=body,
                severity=note["severity"],
                metadata={"source": "agent_inbox"},
            )
            ingested += 1
        db.commit()
    except Exception:  # pragma: no cover - ingestion is best-effort
        logger.warning("journal: failed to ingest notes for task %s", task_id, exc_info=True)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        return 0
    finally:
        if db is not None:
            db.close()
    return ingested


def refs(
    *,
    run_id: str | None = None,
    attempt_id: str | None = None,
    conversation_id: str | None = None,
    artifacts: list[str] | None = None,
    **extra: object,
) -> dict:
    """Build a references payload, dropping empty values.

    Keeps the common loop references (run/attempt/conversation/artifacts) in a
    stable shape so the cockpit can render links consistently.
    """
    out: dict[str, object] = {}
    if run_id:
        out["run_id"] = run_id
    if attempt_id:
        out["attempt_id"] = attempt_id
    if conversation_id:
        out["conversation_id"] = conversation_id
    if artifacts:
        out["artifacts"] = list(artifacts)
    for key, value in extra.items():
        if value is not None:
            out[key] = value
    return out
