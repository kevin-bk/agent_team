"""Shared helper for starting an agent run against a task.

Both the ``@mention`` route (a request) and the autopilot ticker (a background
thread) need to open/continue a ``(task, agent)`` conversation and create a
queued run. This factors out that DB-only step so neither path duplicates it;
each caller then starts the run on its own (``await`` on the loop, or
``dispatch_start`` from a thread) and records its own activity/bus event.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamConversation, AgentTeamRun
from agent_team.features.board.repositories import conversations as conversations_repo
from agent_team.features.board.repositories import runs as runs_repo


def create_run_for_task(
    db: Session,
    *,
    task_id: str,
    agent_alias: str,
    prompt: str,
    trigger: str,
    actor_id: str | None,
) -> tuple[AgentTeamRun, AgentTeamConversation]:
    """Open/continue the ``(task, agent)`` conversation and queue a run.

    Does not commit, record activity, or start the backend — the caller owns
    those so it can attach trigger-specific activity and choose how to dispatch.
    """
    conversation = conversations_repo.get_or_create_active_conversation(
        db, task_id=task_id, agent_alias=agent_alias
    )
    run = runs_repo.create_run(
        db,
        task_id=task_id,
        conversation=conversation,
        agent_alias=agent_alias,
        trigger=trigger,
        actor_id=actor_id,
        prompt=prompt,
    )
    return run, conversation
