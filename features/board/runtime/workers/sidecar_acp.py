"""Worker that drives a direct CLI over ACP **inside an isolated sandbox** (Phase 2).

Where :class:`SandboxedCliWorker` runs the CLI one-shot (Strategy A), this worker
keeps full ACP fidelity: an ``agent-team-runtime-server`` running *inside* the
sandbox owns the ACP subprocess and streams the exact same ``AgentEvent`` frames
back over a WebSocket (Strategy B). The host relays those frames to ``emit``
unchanged, so live plan checklists, tool cards, thinking, and usage all survive
— they are produced by the same :class:`DirectCliRun` the host path uses, only
executed next to the workspace and the CLI binary.

After the turn the sandbox is paused so an idle task costs no resources. Strict
isolation never silently falls back to the host.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from agent_team.features.board.runtime import event_store
from agent_team.features.board.runtime import events as ev
from agent_team.features.board.runtime.acp.masking import SecretMasker, mask_json_value
from agent_team.features.board.runtime.sandbox import sidecar_protocol as proto
from agent_team.features.board.runtime.sandbox.base import SandboxError
from agent_team.features.board.runtime.sandbox.config import RuntimeProfile
from agent_team.features.board.runtime.sandbox.service import (
    open_sidecar_channel,
    pause_task_sandbox,
    prepare_task_sandbox,
    resolve_profile,
)
from agent_team.features.board.runtime.workers.base import (
    EmitFn,
    PermissionMode,
    TurnContext,
    TurnResult,
)

logger = logging.getLogger(__name__)

_CANCEL_POLL_SECONDS = 2.0
_DEFAULT_IDLE_TIMEOUT_SECONDS = 1800
#: WebSocket receive slice — bounds how long we block before re-checking cancel.
_RECV_SLICE_SECONDS = 1.0
#: Hard ceiling for one turn (matches the host ACP path's 3h backstop).
_TURN_TIMEOUT_SECONDS = 3 * 60 * 60


class SidecarAcpWorker:
    """Runs a ``cli:<engine>`` alias over the in-sandbox ACP sidecar for one turn."""

    def __init__(
        self,
        *,
        engine: str,
        profile: RuntimeProfile | None = None,
        idle_timeout_seconds: int = _DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self.engine = engine
        self.profile = profile
        self.idle_timeout_seconds = max(0, idle_timeout_seconds)

    async def run_turn(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult:
        profile = self.profile or resolve_profile(ctx.task_id, ctx.board_id)
        task_key = ctx.task_id or ctx.run_id

        try:
            sandbox = await prepare_task_sandbox(
                task_id=task_key,
                host_workspace_path=ctx.workspace_path,
                profile=profile,
                board_id=ctx.board_id,
            )
            ws_url, ws_headers = await open_sidecar_channel(sandbox, profile)
        except SandboxError as exc:
            return await self._fail(
                ctx, emit, "SandboxUnavailable",
                f"Isolated ACP runtime could not be prepared: {exc}",
            )

        try:
            return await self._drive(
                ctx, emit, cancel, profile, ws_url, ws_headers, sandbox
            )
        finally:
            await pause_task_sandbox(
                task_key,
                workspace_mount_path=profile.workspace_mount_path,
            )

    async def _drive(
        self,
        ctx: TurnContext,
        emit: EmitFn,
        cancel: asyncio.Event,
        profile: RuntimeProfile,
        ws_url: str,
        ws_headers: dict[str, str] | None = None,
        sandbox: Any = None,
    ) -> TurnResult:
        import websockets

        masker = SecretMasker(ctx.secrets or [])
        request = proto.turn_request(
            engine=self.engine,
            prompt=ctx.prompt,
            cwd=profile.workspace_mount_path,
            thread_id=ctx.thread_id,
            auto_approve=ctx.permission_mode != PermissionMode.READ_ONLY,
            idle_timeout_seconds=self.idle_timeout_seconds,
            mcp_config=ctx.mcp_config,
            secrets=list(ctx.secrets or []),
        )

        final_text = ""
        cancelled = False
        cli_usage_text: str | None = None
        cancel_sent = False
        last_poll = 0.0
        errored = False
        frames_emitted = 0
        close_info = ""

        try:
            async with websockets.connect(
                ws_url,
                open_timeout=30,
                max_size=None,
                ping_interval=20,
                additional_headers=ws_headers or None,
            ) as ws:
                await ws.send(proto.encode(request))
                deadline = time.monotonic() + _TURN_TIMEOUT_SECONDS
                while True:
                    if time.monotonic() > deadline:
                        cancelled = True
                        await emit(*ev.error(
                            error_class="TurnTimeout",
                            message="Isolated ACP turn exceeded its time budget.",
                        ))
                        break

                    if not cancel_sent:
                        want_cancel = cancel.is_set()
                        now = time.monotonic()
                        if not want_cancel and (now - last_poll) >= _CANCEL_POLL_SECONDS:
                            last_poll = now
                            want_cancel = await asyncio.to_thread(
                                event_store.is_cancel_requested, ctx.run_id
                            )
                        if want_cancel:
                            cancel.set()
                            cancel_sent = True
                            cancelled = True
                            with contextlib.suppress(Exception):
                                await ws.send(proto.encode(proto.cancel_request()))

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_SLICE_SECONDS)
                    except TimeoutError:
                        continue
                    except websockets.ConnectionClosed as closed:
                        code = getattr(closed, "code", None)
                        reason = (getattr(closed, "reason", "") or "").strip()
                        close_info = f"code={code}" + (f" reason={reason}" if reason else "")
                        break

                    msg = proto.decode(raw)
                    mtype = msg.get("type")
                    if mtype == proto.MSG_FRAME:
                        event_type = str(msg.get("event") or "")
                        data = msg.get("data") or {}
                        if masker.active and isinstance(data, dict):
                            data = mask_json_value(data, masker)
                        if event_type:
                            frames_emitted += 1
                            await emit(event_type, data)
                    elif mtype == proto.MSG_RESULT:
                        final_text = str(msg.get("final_text") or "")
                        cancelled = cancelled or bool(msg.get("cancelled"))
                        cli_usage_text = msg.get("cli_usage_text")
                        usage = msg.get("usage") or {}
                        if isinstance(usage, dict):
                            ctx.usage.update(usage)
                        break
                    elif mtype == proto.MSG_ERROR:
                        errored = True
                        err_text = str(msg.get("message") or "sidecar error")
                        logger.error(
                            "SidecarAcpWorker: sidecar error for run=%s task=%s "
                            "engine=%s: %s",
                            ctx.run_id, ctx.task_id, self.engine, err_text,
                        )
                        await emit(*ev.error(
                            error_class="SidecarError", message=err_text,
                        ))
                        break
                    # MSG_HELLO and unknown types are ignored.
        except (TimeoutError, OSError, websockets.WebSocketException) as exc:
            return await self._fail(
                ctx, emit, "SidecarUnreachable",
                f"Could not reach the in-sandbox ACP server: {exc}",
            )

        # The turn produced nothing (no frames, empty final text) and we didn't
        # cancel or surface an error: the in-sandbox CLI likely failed to launch
        # or authenticate — whether the socket dropped or an empty result came
        # back. Pull the sidecar log so the failure is visible, not a blank reply.
        if (
            not cancelled
            and not errored
            and frames_emitted == 0
            and not final_text.strip()
        ):
            log_tail = await self._tail_sidecar_log(sandbox, profile.sidecar_port)
            detail = f" ({close_info})" if close_info else ""
            message = (
                "The in-sandbox ACP turn produced no output" + detail
                + " — the CLI likely failed to start or authenticate inside the "
                "sandbox (e.g. missing model credentials)."
            )
            if log_tail:
                message += f"\n\n--- sidecar log (tail) ---\n{log_tail}"
            # Mirror to the host log too — run events are easy to miss in the UI
            # when a turn dies this early, and operators watch the console.
            logger.error(
                "SidecarAcpWorker: no output for run=%s task=%s engine=%s %s\n%s",
                ctx.run_id,
                ctx.task_id,
                self.engine,
                close_info or "(socket not closed abnormally)",
                log_tail or "(sidecar log empty/unreadable)",
            )
            await emit(*ev.error(error_class="SidecarNoResult", message=message))
            # Surface it as the turn's reply too (like _fail does) — an error
            # event alone renders as a silent, empty turn in the chat UI.
            final_text = message

        if masker.active:
            final_text = masker(final_text)
        return TurnResult(
            final_text=final_text,
            cancelled=cancelled,
            usage=ctx.usage,
            cli_usage_text=cli_usage_text,
        )

    async def _tail_sidecar_log(self, sandbox: Any, port: int) -> str:
        """Best-effort tail of the in-sandbox sidecar log for diagnostics."""
        if sandbox is None:
            return ""
        try:
            res = await sandbox.exec_shell(
                "tail -n 80 /tmp/agent-team-runtime-server.log 2>/dev/null || true",
                timeout_seconds=15,
            )
        except Exception:  # noqa: BLE001 — diagnostics only
            return ""
        text = (getattr(res, "stdout", "") or getattr(res, "stderr", "") or "").strip()
        return text[-4000:]

    async def _fail(
        self, ctx: TurnContext, emit: EmitFn, error_class: str, message: str
    ) -> TurnResult:
        await emit(*ev.error(error_class=error_class, message=message))
        return TurnResult(final_text=message, cancelled=False, usage=ctx.usage)
