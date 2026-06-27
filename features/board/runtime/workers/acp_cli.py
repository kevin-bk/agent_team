"""Worker that drives a direct coding CLI over ACP for one turn.

Wraps :class:`~agent_team.features.board.runtime.direct_acp.DirectCliRun`, which
talks to Claude / Cursor / Codex straight over ACP and streams the same
``AgentEvent`` frames the graph path emits. Token totals come from ACP's
``PromptResponse.usage`` when the engine reports it, else a gauge-based fallback.

Beyond the base streaming, this worker applies two run-control policies:

* a **permission mode** that decides how the agent's permission requests are
  answered (auto-approve vs read-only), and
* an **idle timeout** that stops a turn which produces no output for too long
  (a genuinely working agent streams progress, so silence means it is wedged).
"""

from __future__ import annotations

import asyncio
import os
import time

from agent_team.features.board.runtime import event_store
from agent_team.features.board.runtime.workers.base import (
    EmitFn,
    PermissionMode,
    TurnContext,
    TurnResult,
)


def _load_direct_cli():
    """Pick the ACP engine: the agent-team-owned one or the legacy ai_code base.

    Selected by ``AGENT_TEAM_ACP_ENGINE`` (``owned`` | ``legacy``, default
    ``owned``). Both expose the identical ``DirectCliRun`` seam and
    ``engine_for_alias`` helper, so flipping the flag is the only switch. Bound at
    import as module globals so the choice is made once and stays patchable.

    The owned engine is the default because it carries the hardened features the
    UI relies on — per-agent MCP pass-through and the structured live plan
    checklist — which the legacy base cannot emit. Set ``legacy`` to opt out.
    """
    choice = (os.getenv("AGENT_TEAM_ACP_ENGINE") or "owned").strip().lower()
    if choice == "legacy":
        from agent_team.features.board.runtime.direct_acp import (
            DirectCliRun,
            engine_for_alias,
        )
    else:
        from agent_team.features.board.runtime.acp import (
            DirectCliRun,
            engine_for_alias,
        )
    return DirectCliRun, engine_for_alias


DirectCliRun, engine_for_alias = _load_direct_cli()

#: How often to poll the DB for a cross-process cancel while streaming.
_CANCEL_POLL_SECONDS = 2.0

#: Stop a turn that streams no output at all for this long (a working agent emits
#: progress continuously, so prolonged silence means the run is stuck). The 3h
#: hard ceiling in ``direct_acp`` stays as an absolute backstop. ``0`` disables.
_DEFAULT_IDLE_TIMEOUT_SECONDS = 1800


def _read_idle_timeout() -> int:
    raw = (os.getenv("AGENT_TEAM_DIRECT_ACP_IDLE_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_IDLE_TIMEOUT_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_IDLE_TIMEOUT_SECONDS


class AcpCliWorker:
    """Runs a ``cli:<engine>`` alias straight over ACP for one turn."""

    def __init__(
        self,
        *,
        engine: str,
        idle_timeout_seconds: int | None = None,
    ) -> None:
        self.engine = engine
        self.idle_timeout_seconds = (
            _read_idle_timeout()
            if idle_timeout_seconds is None
            else max(0, idle_timeout_seconds)
        )

    async def run_turn(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult:
        run = DirectCliRun(
            engine=self.engine or engine_for_alias(ctx.agent_alias),
            prompt=ctx.prompt,
            cwd=ctx.workspace_path,
            thread_id=ctx.thread_id,
            auto_approve=ctx.permission_mode != PermissionMode.READ_ONLY,
            idle_timeout_seconds=self.idle_timeout_seconds,
            mcp_config=ctx.mcp_config,
            secrets=ctx.secrets,
        )
        last_cancel_poll = 0.0
        async for event_type, data in run.stream_frames(cancel):
            now = time.monotonic()
            if now - last_cancel_poll >= _CANCEL_POLL_SECONDS:
                last_cancel_poll = now
                if await asyncio.to_thread(event_store.is_cancel_requested, ctx.run_id):
                    cancel.set()
            await emit(event_type, data)
        cancelled = run.cancelled or cancel.is_set()
        ctx.usage.update(run.usage)
        return TurnResult(
            final_text=run.final_text,
            cancelled=cancelled,
            usage=ctx.usage,
            cli_usage_text=run.cli_usage_text,
        )
