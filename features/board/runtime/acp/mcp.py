"""Translate an MCP config into ACP MCP server objects.

When a board/task configures MCP servers, the direct CLI agent should connect to
them itself (the ACP subprocess owns the MCP connection and exposes the tools
through its own turn). This converts the standard
``{"mcpServers": {name: {...}}}`` shape into the ACP server objects passed to
``new_session()`` / ``load_session()``.

Remote transports (http/sse) are only forwarded when the live session advertised
support for them; a server whose transport the engine cannot honour is dropped
with a warning rather than failing the whole turn.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def remote_mcp_headers(spec: dict[str, Any], name: str) -> list[Any]:
    """Build the ACP ``HttpHeader`` list for a remote MCP server.

    Explicit ``headers`` pass through; a string ``auth`` value is forwarded as a
    bearer ``Authorization`` header when none is already present.
    """
    from acp.schema import HttpHeader

    raw_headers = spec.get("headers") or {}
    headers = [HttpHeader(name=str(k), value=str(v)) for k, v in raw_headers.items()]

    auth = spec.get("auth")
    has_authorization = any(h.name.lower() == "authorization" for h in headers)
    if auth and not has_authorization:
        if isinstance(auth, str) and auth != "oauth":
            headers.append(HttpHeader(name="Authorization", value=f"Bearer {auth}"))
        else:
            logger.warning(
                "MCP server %r uses unsupported remote auth type %r; only explicit "
                "headers or a string bearer token can be forwarded",
                name,
                type(auth).__name__,
            )
    return headers


def mcp_config_to_acp_servers(
    mcp_config: dict[str, Any] | None,
    mcp_capabilities: Any = None,
) -> list[Any]:
    """Convert ``mcp_config`` into a list of ACP MCP server objects.

    ``mcp_capabilities`` is the session's advertised MCP support (``.http`` /
    ``.sse`` booleans); when ``None`` remote transports are dropped defensively.
    """
    if not isinstance(mcp_config, dict):
        return []
    servers = mcp_config.get("mcpServers")
    if not isinstance(servers, dict):
        return []

    from acp.schema import (
        EnvVariable,
        HttpMcpServer,
        McpServerStdio,
        SseMcpServer,
    )

    http_ok = bool(getattr(mcp_capabilities, "http", False))
    sse_ok = bool(getattr(mcp_capabilities, "sse", False))
    result: list[Any] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            logger.warning("Skipping malformed MCP server %r", name)
            continue
        command = spec.get("command")
        url = spec.get("url")
        if command:
            env = [
                EnvVariable(name=str(k), value=str(v))
                for k, v in (spec.get("env") or {}).items()
            ]
            result.append(
                McpServerStdio(
                    name=str(name),
                    command=str(command),
                    args=[str(a) for a in (spec.get("args") or [])],
                    env=env,
                )
            )
        elif url:
            headers = remote_mcp_headers(spec, str(name))
            is_sse = str(spec.get("transport") or "http").lower() == "sse"
            if not (sse_ok if is_sse else http_ok):
                logger.warning(
                    "Engine does not advertise %s MCP support; dropping server %r (%s)",
                    "SSE" if is_sse else "HTTP",
                    name,
                    url,
                )
                continue
            if is_sse:
                result.append(
                    SseMcpServer(type="sse", name=str(name), url=str(url), headers=headers)
                )
            else:
                result.append(
                    HttpMcpServer(type="http", name=str(name), url=str(url), headers=headers)
                )
        else:
            logger.warning(
                "Skipping MCP server %r: needs a 'command' (stdio) or 'url' (http/sse)",
                name,
            )
    return result
