"""Run queries and serialization."""

from __future__ import annotations

from sqlalchemy.orm import Session

from agent_team.features.board.keys import RUN_KEY_PREFIX, next_human_key
from agent_team.features.board.models import (
    AgentTeamConversation,
    AgentTeamRun,
)
from agent_team.features.board.runtime.events import RUN_QUEUED
from agent_team.features.board.schemas import RunDTO


def create_run(
    db: Session,
    *,
    task_id: str,
    conversation: AgentTeamConversation,
    agent_alias: str,
    trigger: str,
    actor_id: str | None,
    prompt: str,
) -> AgentTeamRun:
    run = AgentTeamRun(
        human_key=next_human_key(db, RUN_KEY_PREFIX),
        task_id=task_id,
        conversation_id=conversation.id,
        agent_alias=agent_alias,
        thread_id=conversation.thread_id,
        trigger=trigger,
        actor_id=actor_id,
        status=RUN_QUEUED,
        prompt=prompt,
    )
    db.add(run)
    db.flush()
    return run


def get_run(db: Session, run_id: str) -> AgentTeamRun | None:
    return db.query(AgentTeamRun).filter(AgentTeamRun.id == run_id).first()


def list_runs_for_task(
    db: Session, task_id: str, *, agent_alias: str | None = None
) -> list[AgentTeamRun]:
    query = db.query(AgentTeamRun).filter(AgentTeamRun.task_id == task_id)
    if agent_alias:
        query = query.filter(AgentTeamRun.agent_alias == agent_alias)
    return query.order_by(AgentTeamRun.created_at.desc()).all()


def list_runs_for_conversation(db: Session, conversation_id: str) -> list[AgentTeamRun]:
    """Return a conversation's runs oldest-first (transcript order)."""
    return (
        db.query(AgentTeamRun)
        .filter(AgentTeamRun.conversation_id == conversation_id)
        .order_by(AgentTeamRun.created_at.asc(), AgentTeamRun.human_key.asc())
        .all()
    )


def serialize_run(run: AgentTeamRun) -> RunDTO:
    return RunDTO(
        id=run.id,
        human_key=run.human_key,
        task_id=run.task_id,
        conversation_id=run.conversation_id,
        agent_id=run.agent_alias,
        trigger=run.trigger,
        actor_id=run.actor_id,
        status=run.status,
        prompt=run.prompt,
        final_answer=run.final_answer,
        error=run.error,
        tokens=run.total_tokens,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        total_tokens=run.total_tokens,
        cost_usd=run.cost_usd,
        last_seq=run.last_seq,
        created_at=run.created_at.isoformat() if run.created_at else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
    )
