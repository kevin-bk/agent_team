"""Conversation queries: one active thread per ``(task, agent)``."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamConversation
from agent_team.features.board.schemas import AttemptDTO


def _thread_id(task_id: str, agent_alias: str, attempt: int) -> str:
    """Build the checkpointer thread id for a conversation attempt."""
    safe_alias = re.sub(r"[^a-zA-Z0-9_-]+", "_", agent_alias).strip("_") or "agent"
    return f"agentteam:{task_id}:{safe_alias}:{attempt}"


def _loop_alias(agent_alias: str, role: str) -> str:
    """Synthetic conversation alias that isolates a loop role's thread/session.

    Loop runs keep their *real* ``agent_alias`` on the run row (so the worker
    still resolves the right engine), but their conversation — and therefore the
    ``thread_id`` that keys the agent session — is scoped per role so the
    planner, generator and evaluator never share one process. The bare alias
    stays reserved for the user's manual direct chat.
    """
    return f"{agent_alias}::loop:{role}"


def get_conversation(db: Session, conv_id: str) -> AgentTeamConversation | None:
    return (
        db.query(AgentTeamConversation)
        .filter(AgentTeamConversation.id == conv_id)
        .first()
    )


def get_active_conversation(
    db: Session, *, task_id: str, agent_alias: str
) -> AgentTeamConversation | None:
    return (
        db.query(AgentTeamConversation)
        .filter(
            AgentTeamConversation.task_id == task_id,
            AgentTeamConversation.agent_alias == agent_alias,
            AgentTeamConversation.is_active.is_(True),
        )
        .order_by(AgentTeamConversation.attempt.desc())
        .first()
    )


def get_or_create_active_conversation(
    db: Session, *, task_id: str, agent_alias: str
) -> AgentTeamConversation:
    """Return the active conversation for ``(task, agent)``, creating it if absent."""
    conv = get_active_conversation(db, task_id=task_id, agent_alias=agent_alias)
    if conv is not None:
        return conv
    conv = AgentTeamConversation(
        task_id=task_id,
        agent_alias=agent_alias,
        attempt=1,
        thread_id=_thread_id(task_id, agent_alias, 1),
        is_active=True,
    )
    db.add(conv)
    db.flush()
    return conv


def get_or_create_loop_conversation(
    db: Session, *, task_id: str, agent_alias: str, role: str, fresh: bool = False
) -> AgentTeamConversation:
    """Return a role-scoped conversation for an autonomous-loop run.

    Each loop role gets its own thread (and therefore its own agent session),
    isolated from the others and from the manual direct chat:

    * ``fresh=False`` reuses a persistent per-role thread — used by the
      generator (one session carried across attempts) and the planner.
    * ``fresh=True`` opens a brand-new isolated thread on every call — used by
      the evaluator so each grading runs with clean eyes, never sharing context
      with the generation it is judging or with a prior verdict.
    """
    scoped = _loop_alias(agent_alias, role)
    if not fresh:
        return get_or_create_active_conversation(
            db, task_id=task_id, agent_alias=scoped
        )
    # A token in the scoped alias makes every fresh call a distinct row (the
    # ``(task, agent_alias, attempt)`` uniqueness holds) and a distinct thread,
    # so each evaluation runs in its own isolated session.
    unique = f"{scoped}:{uuid.uuid4().hex[:8]}"
    conv = AgentTeamConversation(
        task_id=task_id,
        agent_alias=unique,
        attempt=1,
        thread_id=_thread_id(task_id, unique, 1),
        is_active=True,
    )
    db.add(conv)
    db.flush()
    return conv


def list_attempts(
    db: Session, *, task_id: str, agent_alias: str
) -> list[AgentTeamConversation]:
    return (
        db.query(AgentTeamConversation)
        .filter(
            AgentTeamConversation.task_id == task_id,
            AgentTeamConversation.agent_alias == agent_alias,
        )
        .order_by(AgentTeamConversation.attempt.desc())
        .all()
    )


def serialize_attempt(conv: AgentTeamConversation) -> AttemptDTO:
    return AttemptDTO(
        id=conv.id,
        task_id=conv.task_id,
        agent_id=conv.agent_alias,
        conv_id=conv.id,
        attempt=conv.attempt,
        is_active=conv.is_active,
        created_at=conv.created_at.isoformat() if conv.created_at else None,
        title=None,
    )


def reset_conversation(
    db: Session, *, task_id: str, agent_alias: str
) -> AgentTeamConversation:
    """Archive the current active conversation and open a fresh attempt.

    The task's shared workspace is untouched; only the agent's thread restarts.
    """
    current = get_active_conversation(db, task_id=task_id, agent_alias=agent_alias)
    next_attempt = (current.attempt + 1) if current is not None else 1
    if current is not None:
        current.is_active = False
    conv = AgentTeamConversation(
        task_id=task_id,
        agent_alias=agent_alias,
        attempt=next_attempt,
        thread_id=_thread_id(task_id, agent_alias, next_attempt),
        is_active=True,
    )
    db.add(conv)
    db.flush()
    return conv
