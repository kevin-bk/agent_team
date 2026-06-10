"""Seam between this plugin and the core agent runtime.

Building the graph through the core runtime helper means a run inherits the
agent's full capability set — every enabled tool (AI Code CLI/ACP, sandbox,
MCP, file/shell), middleware, and sub-agents — exactly as configured per agent.
Wrapping it here keeps the dependency on a core internal in one place.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _workspace_root_override(workspace_path: str):
    """Root the standard file/shell tools at the task workspace while building.

    The core graph builds file/shell tools sandboxed to ``workspaces/<alias>``.
    A task run must instead read and write the *shared* task folder, so we set a
    context-local override that those tools honor at construction time. Using a
    ``ContextVar`` (not a global patch) keeps the redirect isolated to this async
    task: concurrent builds for other agents/flows are unaffected. The override
    is always reset afterwards.
    """
    try:
        from plugins.standard_tools.tools.workspace_override import (
            reset_workspace_override,
            set_workspace_override,
        )
    except ImportError:
        # Standard tools not installed: nothing to redirect.
        yield
        return
    token = set_workspace_override(workspace_path)
    try:
        yield
    finally:
        reset_workspace_override(token)


async def build_graph(
    agent_alias: str,
    checkpointer: Any,
    session: Any | None = None,
    *,
    workspace_path: str | None = None,
):
    """Return a compiled agent graph for ``agent_alias`` (regular or deep).

    When ``workspace_path`` is given, the agent's standard file/shell tools are
    rooted at that task folder so collaborators share one workspace.
    """
    from core.agents.agent_api import _create_runtime_graph

    if not workspace_path:
        return await _create_runtime_graph(agent_alias, checkpointer, session)
    with _workspace_root_override(workspace_path):
        return await _create_runtime_graph(agent_alias, checkpointer, session)


def make_checkpointer(agent_alias: str):
    """Create a checkpointer for the agent, reusing core's resolution.

    Returns ``(checkpointer, context_manager)``; the caller must exit the
    context manager when the run finishes. Mirrors how core long-horizon jobs
    obtain a durable Postgres saver from the agent's ``POSTGRES_STORE_URL``.
    """
    from core.agents.agent_factory import get_effective_agent_settings
    from core.agents.agent_settings import s
    from core.agents.custom_checkpointer.custom_postgres_cp import CustomPostgresSaver

    settings = get_effective_agent_settings(agent_alias)
    if not settings:
        raise RuntimeError(f"Agent '{agent_alias}' not found")
    postgres_url = s(settings, "POSTGRES_STORE_URL")
    if not postgres_url:
        raise RuntimeError("POSTGRES_STORE_URL is not configured for this agent")

    ctx = CustomPostgresSaver.from_conn_string(postgres_url)
    checkpointer = ctx.__enter__()
    checkpointer.setup()
    return checkpointer, ctx
