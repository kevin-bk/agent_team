"""Worker that runs a direct coding CLI **inside an isolated sandbox** (Strategy A).

Instead of spawning the CLI on the host (:class:`AcpCliWorker`), this worker
acquires a task-scoped OpenSandbox, mounts the task workspace at ``/workspace``,
and runs the CLI in non-interactive print mode via the sandbox command API,
translating its stream into the same ``AgentEvent`` frames — so the UI/SSE is
unchanged. After the turn it **pauses** the sandbox so an idle task costs no
resources.

Trade-off vs the ACP path: no interactive permission prompts / live plan
checklist / MCP passthrough (those need the Phase-2 ACP sidecar). Acceptable
because agent_team CLI runs are unattended (``auto_approve``).

Strict isolation never silently falls back to the host: if the sandbox cannot be
prepared and ``strict_isolation`` is set, the turn errors; only an explicit
``allow_fallback`` profile runs the host CLI (and records that it did).
"""

from __future__ import annotations

import asyncio
import logging
import time

from agent_team.features.board.runtime import event_store
from agent_team.features.board.runtime import events as ev
from agent_team.features.board.runtime.acp.masking import SecretMasker, mask_json_value
from agent_team.features.board.runtime.sandbox import cli_exec
from agent_team.features.board.runtime.sandbox.base import SandboxError
from agent_team.features.board.runtime.sandbox.config import RuntimeProfile
from agent_team.features.board.runtime.sandbox.service import (
    pause_task_sandbox,
    prepare_task_sandbox,
    resolve_profile,
)
from agent_team.features.board.runtime.workers.base import (
    EmitFn,
    TurnContext,
    TurnResult,
)

logger = logging.getLogger(__name__)

#: How often to poll the DB for a cross-process cancel while a turn runs.
_CANCEL_POLL_SECONDS = 2.0

#: Absolute per-turn ceiling for the in-sandbox command (matches the ACP path's
#: 3h backstop); the sandbox's own idle-close is the resource backstop.
_TURN_TIMEOUT_SECONDS = 3 * 60 * 60


class SandboxedCliWorker:
    """Runs a ``cli:<engine>`` alias one-shot inside a task-scoped sandbox."""

    def __init__(self, *, engine: str, profile: RuntimeProfile | None = None) -> None:
        self.engine = engine
        self.profile = profile

    async def run_turn(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult:
        profile = self.profile or resolve_profile(ctx.task_id, ctx.board_id)

        try:
            spec = cli_exec.get_exec_spec(self.engine)
        except NotImplementedError as exc:
            # No print-mode wiring for this engine yet — fail loud, never host-run.
            return await self._fail(ctx, emit, "EngineUnsupported", str(exc))

        try:
            sandbox = await prepare_task_sandbox(
                task_id=ctx.task_id or ctx.run_id,
                host_workspace_path=ctx.workspace_path,
                profile=profile,
            )
        except SandboxError as exc:
            if profile.strict_isolation or not profile.allow_fallback:
                return await self._fail(
                    ctx,
                    emit,
                    "SandboxUnavailable",
                    f"Isolated runtime could not be prepared: {exc}",
                )
            logger.warning(
                "agent_team runtime: sandbox prepare failed for task=%s; "
                "falling back to host CLI (allow_fallback)",
                ctx.task_id,
            )
            return await self._fallback_host(ctx, emit, cancel)

        workdir = profile.workspace_mount_path
        argv = spec.build_argv(prompt=ctx.prompt, workdir=workdir)
        command = cli_exec.command_string(argv)
        translator = spec.new_translator()
        masker = SecretMasker(ctx.secrets or [])

        async def on_line(line: str) -> None:
            for event_type, data in translator.on_line(line):
                if masker.active:
                    data = mask_json_value(data, masker)
                await emit(event_type, data)

        exec_task = asyncio.create_task(
            sandbox.exec_shell(
                command,
                cwd=workdir,
                timeout_seconds=_TURN_TIMEOUT_SECONDS,
                on_stdout=on_line,
            )
        )

        cancelled = await self._await_with_cancel(exec_task, ctx.run_id, cancel)

        # Flush any tool cards left open + record parse result.
        for event_type, data in translator.finalize():
            await emit(event_type, data)
        parsed = translator.result
        ctx.usage.update(parsed.usage)

        if not cancelled and not exec_task.cancelled():
            exec_res = exec_task.result()
            if not parsed.ok or not exec_res.success:
                message = (
                    parsed.error_kind
                    or (exec_res.stderr or exec_res.stdout or "")[-500:]
                    or f"exit_code={exec_res.exit_code}"
                )
                await emit(*ev.error(error_class="CliError", message=str(message)))

        # Pause the sandbox so an idle task frees its resources (best-effort).
        await pause_task_sandbox(ctx.task_id or ctx.run_id)

        return TurnResult(
            final_text=parsed.final_text,
            cancelled=cancelled,
            usage=ctx.usage,
            cli_usage_text=None,
        )

    async def _await_with_cancel(
        self, exec_task: asyncio.Task, run_id: str, cancel: asyncio.Event
    ) -> bool:
        """Wait for the exec to finish, honouring same- and cross-process cancel.

        Returns ``True`` if the turn was cancelled. On cancel we stop waiting and
        let :func:`pause_task_sandbox` suspend the still-running in-sandbox
        command (a hard interrupt of the command is a Phase-2 item — it needs the
        background-command + interrupt API).
        """
        last_poll = 0.0
        while not exec_task.done():
            done, _ = await asyncio.wait({exec_task}, timeout=_CANCEL_POLL_SECONDS)
            if done:
                break
            now = time.monotonic()
            if cancel.is_set():
                return True
            if now - last_poll >= _CANCEL_POLL_SECONDS:
                last_poll = now
                if await asyncio.to_thread(event_store.is_cancel_requested, run_id):
                    cancel.set()
                    return True
        return cancel.is_set()

    async def _fail(
        self, ctx: TurnContext, emit: EmitFn, error_class: str, message: str
    ) -> TurnResult:
        await emit(*ev.error(error_class=error_class, message=message))
        return TurnResult(final_text=message, cancelled=False, usage=ctx.usage)

    async def _fallback_host(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult:
        """Run the host ACP CLI and record that we fell back (non-strict only)."""
        from agent_team.features.board.runtime.workers.acp_cli import AcpCliWorker

        await emit(
            *ev.error(
                error_class="RuntimeFallback",
                message="Isolated runtime unavailable; fell back to host runtime.",
                recoverable=True,
            )
        )
        return await AcpCliWorker(engine=self.engine).run_turn(ctx, emit, cancel)
