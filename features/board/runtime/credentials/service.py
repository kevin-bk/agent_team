"""Board-driven credential injection facade for the isolated runtime.

A board already declares which coding agents staff it (``cli_target_ids``, e.g.
``cli:claude`` + ``cli:codex``) and their per-agent MCP config (``agent_mcp``).
This module turns that into one merged :class:`InjectionPlan` for the task's
sandbox:

* one credential per staffed coding agent, sourced from the AI Code Factory
  Environment Pool (:mod:`ai_code_source`) — no second account registry;
* every staffed agent's creds live in the **same** sandbox (a task's sandbox is
  shared across turns/agents), so a multi-agent board just merges N plans;
* remote (http/sse) MCP server hosts are added to the egress allow-list so they
  still work when a network policy is active.

``core.database`` and the ``ai_code`` plugin are imported lazily so this module
stays out of light import closures (e.g. the sidecar image).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from agent_team.features.board.runtime.credentials.ai_code_source import (
    resolve_account_for_provider,
)
from agent_team.features.board.runtime.credentials.injector import build_plan
from agent_team.features.board.runtime.credentials.spec import InjectionPlan

logger = logging.getLogger(__name__)


def _providers_for_board(board) -> list[str]:
    """Distinct coding-agent providers staffed on ``board`` (order-preserving)."""
    from agent_team.features.board.runtime.acp.engines import engine_for_alias

    seen: list[str] = []
    for alias in board.cli_target_ids():
        engine = engine_for_alias(alias)
        if engine and engine not in seen:
            seen.append(engine)
    return seen


def _mcp_allow_rules(board) -> list[dict]:
    """Allow-rules for every remote (http/sse) MCP host configured on the board.

    stdio MCP servers (``command``) are skipped — they run inside the sandbox and
    need no egress. Malformed/urlless entries are ignored.
    """
    rules: list[dict] = []
    seen: set[str] = set()
    for cfg in board.agent_mcp().values():
        servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
        if not isinstance(servers, dict):
            continue
        for spec in servers.values():
            if not isinstance(spec, dict):
                continue
            url = spec.get("url")
            if not url:
                continue
            host = urlparse(str(url)).hostname
            if host and host not in seen:
                seen.add(host)
                rules.append({"action": "allow", "target": host})
    return rules


def build_injection_for_board(board_id: str | None) -> InjectionPlan | None:
    """Build the merged credential :class:`InjectionPlan` for ``board_id``.

    Returns ``None`` when nothing needs injecting (no board, no staffed coding
    agent with a pool account, no remote MCP). Never raises for a missing pool
    account — that provider is simply left to the image's ambient login.
    """
    ref = (board_id or "").strip()
    if not ref:
        return None

    from agent_team.features.board.repositories import boards as boards_repo
    from core.database.base import SessionLocal

    with SessionLocal() as db:
        board = boards_repo.get_board(db, ref)
        if board is None:
            return None
        providers = _providers_for_board(board)
        mcp_rules = _mcp_allow_rules(board)

    plan = InjectionPlan()
    injected = False
    for provider in providers:
        account = resolve_account_for_provider(provider)
        if account is None:
            logger.info(
                "agent_team credentials: no pool account for provider %r "
                "(board=%s); leaving it to the image's ambient login",
                provider,
                ref,
            )
            continue
        plan.merge(build_plan(account))
        injected = True

    if mcp_rules:
        plan.network_rules.extend(mcp_rules)

    if not injected and not mcp_rules:
        return None
    return plan
