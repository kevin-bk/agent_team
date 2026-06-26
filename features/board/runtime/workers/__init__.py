"""Agent workers: one contract for driving a single turn of any agent.

A *worker* drives exactly one turn of an agent against a task and streams the
agent's output as ``AgentEvent`` frames through an ``emit`` callback. The inner
think-act-observe loop is owned by the underlying engine (the LLM graph, or the
coding CLI itself), so a worker only spans one turn.

Two implementations exist:

* :class:`~agent_team.features.board.runtime.workers.llm_graph.LlmGraphWorker`
  drives a regular agent through its LangGraph graph.
* :class:`~agent_team.features.board.runtime.workers.acp_cli.AcpCliWorker`
  drives a direct coding CLI over ACP.

The run backend resolves the right worker for an agent alias via
:func:`~agent_team.features.board.runtime.workers.registry.resolve_worker` and
calls ``run_turn``; everything downstream (event store, SSE) is engine-agnostic.
"""

from agent_team.features.board.runtime.workers.base import (
    AgentWorker,
    EmitFn,
    PermissionMode,
    TurnContext,
    TurnResult,
    WorkerRole,
)
from agent_team.features.board.runtime.workers.registry import resolve_worker

__all__ = [
    "AgentWorker",
    "EmitFn",
    "PermissionMode",
    "TurnContext",
    "TurnResult",
    "WorkerRole",
    "resolve_worker",
]
