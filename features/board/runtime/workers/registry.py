"""Resolve an agent alias to the worker that should drive it.

A ``cli:<engine>`` alias is driven by one of three workers, selected by the
runtime profile:

* :class:`AcpCliWorker` — straight over ACP **on the host** (no isolation).
* :class:`SandboxedCliWorker` — one-shot **inside an isolated sandbox** (Phase 1).
* :class:`SidecarAcpWorker` — full ACP **inside an isolated sandbox** via the
  in-sandbox bridge server (Phase 2, ``runtime_strategy=acp_sidecar``).

Any other alias is a regular agent driven through its graph
(:class:`LlmGraphWorker`). The sandboxed paths only engage for ``cli:*`` agents —
LLM graph agents run in-process and are harder to isolate.
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
    agent_alias: str,
    role: WorkerRole = WorkerRole.CHAT,
    *,
    board_id: str = "",
) -> AgentWorker:
    """Return the worker for ``agent_alias`` (``role`` reserved for the loop layer).

    A ``cli:*`` alias runs isolated when the runtime profile — resolved from the
    board override then env defaults — selects the ``opensandbox`` provider:
    ``acp_sidecar`` strategy → :class:`SidecarAcpWorker`, else
    :class:`SandboxedCliWorker`. Otherwise it runs on the host over ACP.
    """
    if is_direct_cli_alias(agent_alias):
        engine = engine_for_alias(agent_alias)
        profile = _resolve_profile(board_id)
        if profile.is_acp_sidecar:
            from agent_team.features.board.runtime.workers.sidecar_acp import (
                SidecarAcpWorker,
            )

            return SidecarAcpWorker(engine=engine, profile=profile)
        if profile.is_sandboxed:
            from agent_team.features.board.runtime.workers.sandboxed_cli import (
                SandboxedCliWorker,
            )

            return SandboxedCliWorker(engine=engine, profile=profile)
        return AcpCliWorker(engine=engine)
    return LlmGraphWorker()


def _resolve_profile(board_id: str = ""):
    """Resolve the effective runtime profile (board override → env default)."""
    from agent_team.features.board.runtime.sandbox.service import resolve_profile

    return resolve_profile(board_id=board_id)
