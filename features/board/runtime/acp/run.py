"""The direct-CLI run seam: ``DirectCliRun`` + the progress-to-frame translator.

``DirectCliRun`` drives one ACP prompt turn on the background loop and streams
its progress as the same ``AgentEvent`` frames the LLM path emits, so the cockpit
renders a direct conversation identically. This is the stable seam every layer
above (the worker, backend, runs, loop) depends on — its constructor, the
``stream_frames`` async generator, and the result attributes must not drift.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from collections.abc import AsyncIterator

from agent_team.features.board.runtime import events as ev
from agent_team.features.board.runtime.acp import usage as _usage
from agent_team.features.board.runtime.acp.engines import (
    ENGINES,
    alias_for_engine,
    engine_runtime,
)
from agent_team.features.board.runtime.acp.manager import (
    BackgroundLoop,
    cancel_acp_sessions,
    drain_queue,
    manager_run_ok,
)
from agent_team.features.board.runtime.acp.masking import SecretMasker

logger = logging.getLogger(__name__)

#: Polling slice used while waiting on the run's progress queue.
_DRAIN_TIMEOUT_SECONDS = 0.2

#: Grace window after a graceful cancel: let the in-flight prompt return with
#: ``stop_reason="cancelled"`` (so the subprocess is not killed mid-write) before
#: hard-cancelling the run.
_CANCEL_DRAIN_SECONDS = 8.0

_KEY_PROGRESS = "claude_acp_progress"
_KEY_THOUGHT = "claude_acp_thought"
_KEY_PLAN = "claude_acp_plan"
_KEY_TOOL_START = "claude_acp_tool_start"
_KEY_TOOL_PROGRESS = "claude_acp_tool_progress"
_KEY_USAGE = "claude_acp_usage"
_KEY_USAGE_FINAL = "claude_acp_usage_final"

_TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed"})

#: Chars of a tool's output shown inline on its card; a longer result is
#: persisted out-of-stream and revealed via the card's "show more".
_OUTPUT_PREVIEW_LIMIT = 2000


class _DirectAcpTranslator:
    """Map ACP custom-progress deltas to ``AgentEvent`` frames.

    Assistant text streams as ``text_delta``, reasoning as ``thinking``, and each
    ACP tool call becomes its own card paired by tool id — opened on start,
    closed on the terminal progress update (or by :meth:`finalize`).
    """

    def __init__(self) -> None:
        self._counter = 0
        self._open: dict[str, tuple[str, str, str]] = {}
        self.cli_usage_text: str | None = None
        self.totals: dict[str, int] | None = None

    def on_delta(self, delta: dict[str, object]) -> list[tuple[str, dict]]:
        frames: list[tuple[str, dict]] = []
        for key, value in delta.items():
            if isinstance(value, str) and value:
                frames.extend(self._on_event(key, value))
        return frames

    def finalize(self) -> list[tuple[str, dict]]:
        """Close any tool cards still open when the run ends (reconcile)."""
        frames = [
            ev.tool_use_end(
                tool_id=tool_id,
                tool_name=tool_name,
                success=True,
                is_error=False,
                output_preview="",
            )
            for tool_id, tool_name, _command in self._open.values()
        ]
        self._open.clear()
        return frames

    def _on_event(self, key: str, value: str) -> list[tuple[str, dict]]:
        if key == _KEY_PROGRESS:
            return [ev.text_delta(value)]
        if key in (_KEY_THOUGHT, _KEY_PLAN):
            return [ev.thinking(value)]
        if key == _KEY_TOOL_START:
            return self._tool_start(value)
        if key == _KEY_TOOL_PROGRESS:
            return self._tool_progress(value)
        if key == _KEY_USAGE:
            self.cli_usage_text = value
            return [ev.usage({"text": value})]
        if key == _KEY_USAGE_FINAL:
            self.totals = _usage.parse_totals(value)
            return []
        return []

    def _tool_start(self, value: str) -> list[tuple[str, dict]]:
        parts = value.split("\x00", 3)
        kind = parts[0] if parts else ""
        title = parts[1] if len(parts) > 1 else ""
        acp_id = parts[2] if len(parts) > 2 else ""
        command = parts[3] if len(parts) > 3 else ""
        if not (title or kind):
            return []
        self._counter += 1
        tool_id = f"c{self._counter}"
        tool_name = title or kind or "tool"
        if acp_id:
            self._open[acp_id] = (tool_id, tool_name, command)
        tool_input: dict[str, str] = {}
        if kind:
            tool_input["kind"] = kind
        if command:
            tool_input["command"] = command
        return [
            ev.tool_use_start(tool_id=tool_id, tool_name=tool_name, tool_input=tool_input)
        ]

    def _tool_progress(self, value: str) -> list[tuple[str, dict]]:
        parts = value.split("\x00", 4)
        acp_id = parts[0] if parts else ""
        status = parts[1] if len(parts) > 1 else ""
        title = parts[2] if len(parts) > 2 else ""
        output = parts[3] if len(parts) > 3 else ""
        command = parts[4] if len(parts) > 4 else ""
        opened = self._open.get(acp_id)
        if opened is None:
            return []
        tool_id, tool_name, known_command = opened
        newly_known = bool(command) and command != known_command
        if newly_known:
            known_command = command
            self._open[acp_id] = (tool_id, tool_name, known_command)
        input_update = {"command": known_command} if known_command else None
        if status in _TERMINAL_TOOL_STATUSES:
            self._open.pop(acp_id, None)
            preview = output or title
            truncated = len(preview) > _OUTPUT_PREVIEW_LIMIT
            return [
                ev.tool_use_end(
                    tool_id=tool_id,
                    tool_name=title or tool_name,
                    success=status != "failed",
                    is_error=status == "failed",
                    output_preview=preview[:_OUTPUT_PREVIEW_LIMIT],
                    truncated=truncated,
                    output_full=preview if truncated else None,
                    tool_input=input_update,
                )
            ]
        if title or newly_known:
            return [
                ev.tool_use_progress(
                    tool_id=tool_id,
                    chunk=title,
                    tool_input=input_update if newly_known else None,
                )
            ]
        return []


class DirectCliRun:
    """Drives one direct ACP prompt turn and streams its frames."""

    def __init__(
        self,
        *,
        engine: str,
        prompt: str,
        cwd: str,
        thread_id: str,
        auto_approve: bool = True,
        idle_timeout_seconds: int = 0,
        secrets: list[str] | None = None,
        mcp_config: dict | None = None,
    ) -> None:
        self.engine = engine
        self.prompt = prompt
        self.cwd = cwd
        self.thread_id = thread_id
        self.auto_approve = auto_approve
        self.idle_timeout_seconds = max(0, idle_timeout_seconds)
        self._secrets = secrets or []
        self._mcp_config = mcp_config
        self.final_text = ""
        self.ok = True
        self.cancelled = False
        self.timed_out = False
        self._translator = _DirectAcpTranslator()
        self.cli_usage_text: str | None = None
        self.usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
        }

    async def stream_frames(
        self, cancel_event: asyncio.Event
    ) -> AsyncIterator[tuple[str, dict]]:
        if self.engine not in ENGINES:
            self.ok = False
            self.final_text = f"Unknown direct CLI engine: {self.engine!r}."
            yield ev.error(error_class="ValueError", message=self.final_text)
            return

        runtime = engine_runtime(self.engine)
        masker = SecretMasker(self._secrets)
        progress_q: queue.Queue = queue.Queue()
        key = f"{alias_for_engine(self.engine)}::{self.thread_id}"
        future = BackgroundLoop.instance().submit(
            manager_run_ok(
                key=key,
                prompt=self.prompt,
                cwd=self.cwd or None,
                command=runtime.command,
                args=list(runtime.args),
                env_overrides={},
                auto_approve=self.auto_approve,
                timeout_seconds=runtime.timeout_seconds,
                create_timeout=runtime.create_timeout_seconds,
                progress_q=progress_q,
                cancel_id=self.thread_id,
                mask=masker if masker.active else None,
                mcp_config=self._mcp_config,
                agent_label=runtime.label,
            )
        )
        loop = asyncio.get_running_loop()
        result_fut = asyncio.wrap_future(future)
        last_activity = time.monotonic()
        try:
            while not result_fut.done():
                if cancel_event.is_set():
                    async for frame in self._graceful_cancel(future, result_fut, progress_q):
                        yield frame
                    return
                deltas = await loop.run_in_executor(
                    None, drain_queue, progress_q, _DRAIN_TIMEOUT_SECONDS
                )
                if deltas:
                    last_activity = time.monotonic()
                elif self.idle_timeout_seconds and (
                    time.monotonic() - last_activity > self.idle_timeout_seconds
                ):
                    self.cancelled = True
                    self.timed_out = True
                    self.ok = False
                    self.final_text = (
                        f"{runtime.label} produced no output for "
                        f"{self.idle_timeout_seconds}s; the run was stopped."
                    )
                    future.cancel()
                    await asyncio.to_thread(cancel_acp_sessions, self.thread_id)
                    yield ev.error(error_class="IdleTimeout", message=self.final_text)
                    for frame in self._translator.finalize():
                        yield frame
                    self._capture_usage()
                    return
                for delta in deltas:
                    for frame in self._translator.on_delta(delta):
                        yield frame
            for delta in drain_queue(progress_q, 0):
                for frame in self._translator.on_delta(delta):
                    yield frame
        except asyncio.CancelledError:
            self.cancelled = True
            future.cancel()
            await asyncio.to_thread(cancel_acp_sessions, self.thread_id)
            raise

        text, ok = await result_fut
        self.ok = ok
        self.final_text = (text or "").strip()
        for frame in self._translator.finalize():
            yield frame
        self._capture_usage()

    async def _graceful_cancel(
        self,
        future,
        result_fut,
        progress_q: queue.Queue,
    ) -> AsyncIterator[tuple[str, dict]]:
        """Stop the turn gracefully: signal cancel, drain, then reconcile cards."""
        self.cancelled = True
        # Ask the engine to stop and let the prompt return on its own within a
        # grace window so the subprocess is not killed mid-write.
        await asyncio.to_thread(cancel_acp_sessions, self.thread_id)
        try:
            text, _ok = await asyncio.wait_for(
                asyncio.shield(result_fut), timeout=_CANCEL_DRAIN_SECONDS
            )
            self.final_text = (text or "").strip()
        except (TimeoutError, asyncio.CancelledError):
            future.cancel()  # did not quiesce → hard stop (manager evicts it)
        except Exception:
            future.cancel()
        for delta in drain_queue(progress_q, 0):
            for frame in self._translator.on_delta(delta):
                yield frame
        for frame in self._translator.finalize():
            yield frame
        self._capture_usage()

    def _capture_usage(self) -> None:
        self.cli_usage_text = self._translator.cli_usage_text
        totals = self._translator.totals
        if totals:
            self.usage = totals
            return
        used, _size = _usage.parse_gauge_tokens(self._translator.cli_usage_text)
        if used:
            self.usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": used,
                "cache_read_tokens": 0,
            }
