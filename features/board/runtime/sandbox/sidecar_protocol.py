"""Wire protocol for the Phase-2 ACP sidecar bridge (host ⇄ in-sandbox server).

A single WebSocket carries one prompt turn. The host sends a :data:`MSG_TURN`
(optionally :data:`MSG_CANCEL` later); the server streams :data:`MSG_FRAME`
messages — each wrapping one ``(event_type, data)`` :mod:`events` frame exactly
as the host worker would ``emit`` it — then a terminal :data:`MSG_RESULT` (or
:data:`MSG_ERROR`).

Kept dependency-free (plain ``json`` + dicts) so the same module can be vendored
into the sidecar image without pulling the rest of agent_team.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1

# host → server
MSG_TURN = "turn"
MSG_CANCEL = "cancel"

# server → host
MSG_HELLO = "hello"
MSG_FRAME = "frame"
MSG_RESULT = "result"
MSG_ERROR = "error"


def encode(obj: dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def decode(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("sidecar protocol: expected a JSON object")
    return obj


def turn_request(
    *,
    engine: str,
    prompt: str,
    cwd: str,
    thread_id: str,
    auto_approve: bool = True,
    idle_timeout_seconds: int = 0,
    mcp_config: dict | None = None,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": MSG_TURN,
        "v": PROTOCOL_VERSION,
        "engine": engine,
        "prompt": prompt,
        "cwd": cwd,
        "thread_id": thread_id,
        "auto_approve": bool(auto_approve),
        "idle_timeout_seconds": int(idle_timeout_seconds),
        "mcp_config": mcp_config,
        "secrets": list(secrets or []),
    }


def cancel_request() -> dict[str, Any]:
    return {"type": MSG_CANCEL}


def hello(*, engines: list[str]) -> dict[str, Any]:
    return {"type": MSG_HELLO, "v": PROTOCOL_VERSION, "engines": engines}


def frame(event_type: str, data: dict) -> dict[str, Any]:
    return {"type": MSG_FRAME, "event": event_type, "data": data}


def result(
    *,
    final_text: str,
    cancelled: bool,
    ok: bool,
    usage: dict | None = None,
    cli_usage_text: str | None = None,
) -> dict[str, Any]:
    return {
        "type": MSG_RESULT,
        "final_text": final_text,
        "cancelled": bool(cancelled),
        "ok": bool(ok),
        "usage": usage or {},
        "cli_usage_text": cli_usage_text,
    }


def error(message: str) -> dict[str, Any]:
    return {"type": MSG_ERROR, "message": message}
