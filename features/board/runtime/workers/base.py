"""Worker contract: ``TurnContext`` / ``TurnResult`` and the ``AgentWorker`` protocol.

A worker is handed everything it needs to drive one turn (``TurnContext``), a way
to stream frames as they happen (``EmitFn``), and a cancel signal
(``asyncio.Event``). It returns a ``TurnResult`` summarising the turn.

Token usage is accumulated **in place** on ``TurnContext.usage`` rather than only
returned, so a turn cancelled mid-stream still leaves the partial totals visible
to the caller (which finalises the run from them).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class WorkerRole(StrEnum):
    """Why a worker is being run, for the (future) autonomous loop layer.

    Today every run is :attr:`CHAT` (one interactive turn). The loop layer adds
    :attr:`GENERATOR` (does the task) and :attr:`EVALUATOR` (independently grades
    it); the role lets a worker tailor its prompt/permissions per stage.
    """

    CHAT = "chat"
    GENERATOR = "generator"
    EVALUATOR = "evaluator"
    SUMMARIZER = "summarizer"


class PermissionMode(StrEnum):
    """How a CLI worker answers the agent's permission requests.

    :attr:`AUTO` approves every request (the default — unattended runs need to
    proceed without a human). :attr:`READ_ONLY` denies requests so the agent can
    inspect but not mutate. Interactive per-request confirmation is intentionally
    not modelled here yet (it needs the loop layer's human-in-the-loop plumbing).
    """

    AUTO = "auto"
    READ_ONLY = "read_only"


def _zero_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
    }


@dataclass
class TurnContext:
    """Everything a worker needs to drive one turn against a task.

    ``usage`` is a mutable accumulator the worker updates as tokens are counted,
    so the caller can read partial totals even if the turn is cancelled.
    """

    run_id: str
    agent_alias: str
    prompt: str
    workspace_path: str
    thread_id: str
    role: WorkerRole = WorkerRole.CHAT
    #: The task this turn runs against — used to key a per-task isolated sandbox
    #: (empty for callers that predate the isolated runtime).
    task_id: str = ""
    #: The board owning the task — used to resolve a board-level runtime profile.
    board_id: str = ""
    permission_mode: PermissionMode = PermissionMode.AUTO
    usage: dict[str, int] = field(default_factory=_zero_usage)
    #: Per-agent MCP config (``{"mcpServers": {...}}``) for a direct-CLI worker,
    #: or ``None`` for none. Only the owned ACP engine forwards this to the CLI.
    mcp_config: dict | None = None
    #: Secret values to mask in streamed tool-call frames (e.g. MCP auth tokens).
    secrets: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    """Outcome of one turn."""

    final_text: str
    cancelled: bool
    usage: dict[str, int]
    #: Direct-CLI context-window gauge text (e.g. "45,000/200,000 tokens"), or
    #: ``None`` for engines/paths that do not report one.
    cli_usage_text: str | None = None
    #: Terminal failure text when the worker completed cleanly at the transport
    #: layer but the underlying agent turn did not. ``None`` preserves the
    #: historical success/cancel contract for workers that do not set it.
    error: str | None = None


#: Persists one streamed frame. The backend's implementation offloads large tool
#: output out-of-band and appends the frame to the event store (the source of
#: truth for replay and SSE).
EmitFn = Callable[[str, dict], Awaitable[None]]


class AgentWorker(Protocol):
    """Drives exactly one turn of an agent and streams its frames via ``emit``."""

    async def run_turn(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult: ...
