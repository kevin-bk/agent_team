"""Agent-team-owned ACP engine for direct CLI conversations.

Self-contained ACP session management (independent of the shared ai_code base):
spawns and resumes ACP subprocesses, streams their progress as ``AgentEvent``
frames, masks injected secrets, attaches configured MCP servers, and supports
fork sub-queries. The public surface mirrors the legacy direct-CLI seam so the
worker/runtime above it is unchanged.
"""

from __future__ import annotations

from agent_team.features.board.runtime.acp.engines import (
    CLI_ALIAS_PREFIX,
    ENGINES,
    alias_for_engine,
    available_targets,
    display_name_for_alias,
    engine_for_alias,
    is_direct_cli_alias,
    known_cli_aliases,
)
from agent_team.features.board.runtime.acp.manager import ask_agent, cancel_acp_sessions
from agent_team.features.board.runtime.acp.run import DirectCliRun

__all__ = [
    "CLI_ALIAS_PREFIX",
    "ENGINES",
    "DirectCliRun",
    "alias_for_engine",
    "ask_agent",
    "available_targets",
    "cancel_acp_sessions",
    "display_name_for_alias",
    "engine_for_alias",
    "is_direct_cli_alias",
    "known_cli_aliases",
]
