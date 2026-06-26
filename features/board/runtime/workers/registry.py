"""Resolve an agent alias to the worker that should drive it.

A ``cli:<engine>`` alias is driven straight over ACP (:class:`AcpCliWorker`);
any other alias is a regular agent driven through its graph
(:class:`LlmGraphWorker`).
"""

from __future__ import annotations

from agent_team.features.board.runtime.direct_acp import (
    engine_for_alias,
    is_direct_cli_alias,
)
from agent_team.features.board.runtime.workers.acp_cli import AcpCliWorker
from agent_team.features.board.runtime.workers.base import AgentWorker, WorkerRole
from agent_team.features.board.runtime.workers.llm_graph import LlmGraphWorker


def resolve_worker(
    agent_alias: str, role: WorkerRole = WorkerRole.CHAT
) -> AgentWorker:
    """Return the worker for ``agent_alias`` (``role`` reserved for the loop layer)."""
    if is_direct_cli_alias(agent_alias):
        return AcpCliWorker(engine=engine_for_alias(agent_alias))
    return LlmGraphWorker()
