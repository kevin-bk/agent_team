"""Direct CLI run path: chat straight with an ACP coding agent.

A *direct CLI* conversation talks to Claude / Cursor / Codex over ACP without the
LLM orchestrator in between. Only the agent_team plugin uses this. A direct run
drives one ACP prompt turn on the shared background loop that owns every ACP
connection and translates the agent's live progress — assistant text, thinking,
tool calls — into the same ``AgentEvent`` frames the LLM path emits, so the
cockpit renders a direct conversation identically to a regular agent one.

The CLI keeps its own persistent session keyed by the conversation thread, so a
follow-up turn continues the same conversation: that session *is* the
conversational memory (there is no LLM checkpointer here).

A direct agent is addressed by a synthetic alias ``cli:<engine>`` (e.g.
``cli:claude``). The alias flows through the normal conversation/run/thread
machinery untouched; only the run driver branches here instead of building a
graph.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import shutil
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from agent_team.features.board.runtime import events as ev

logger = logging.getLogger(__name__)

#: Synthetic-alias namespace marking a direct CLI conversation.
CLI_ALIAS_PREFIX = "cli:"

#: Polling slice used while waiting on the ACP run's progress queue.
_DRAIN_TIMEOUT_SECONDS = 0.2

#: ACP custom-progress keys (see ai_code ``_acp_base._route_update``).
_KEY_PROGRESS = "claude_acp_progress"
_KEY_THOUGHT = "claude_acp_thought"
_KEY_PLAN = "claude_acp_plan"
_KEY_TOOL_START = "claude_acp_tool_start"
_KEY_TOOL_PROGRESS = "claude_acp_tool_progress"
_KEY_USAGE = "claude_acp_usage"
_KEY_USAGE_FINAL = "claude_acp_usage_final"

#: ACP tool-call statuses that close a tool card.
_TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed"})

#: Chars of a tool's output shown inline on its card. A longer result is
#: persisted out-of-stream and revealed via the card's "show more" affordance
#: (mirrors the LLM path's ``StreamTranslator`` preview budget).
_OUTPUT_PREVIEW_LIMIT = 2000


@dataclass(frozen=True)
class _EngineSpec:
    """Static defaults + env-key prefix for one ACP engine."""

    engine: str
    label: str
    command: str
    args: str


#: Direct CLI engines, mirroring the per-agent ACP tools' defaults. Config is
#: read from the process environment with the same ``AI_CODE_<ENGINE>_ACP_*``
#: keys, so a direct agent needs no Agent row — it is deliberately *not* one.
_ENGINES: dict[str, _EngineSpec] = {
    "claude": _EngineSpec(
        "claude", "Claude", "npx", "-y @agentclientprotocol/claude-agent-acp"
    ),
    "cursor": _EngineSpec("cursor", "Cursor", "cursor-agent", "acp"),
    "codex": _EngineSpec(
        "codex",
        "Codex",
        "npx",
        "-y @agentclientprotocol/codex-acp "
        "-c 'sandbox_mode=\"danger-full-access\"' "
        "-c 'approval_policy=\"never\"'",
    ),
}


def _parse_usage_totals(value: str) -> dict[str, int] | None:
    """Decode ``input\\x00output\\x00total\\x00cache_read`` into a usage dict."""
    parts = value.split("\x00")
    try:
        nums = [int(p) for p in parts[:4]]
    except (TypeError, ValueError):
        return None
    nums += [0] * (4 - len(nums))
    inp, out, total, cache = nums[0], nums[1], nums[2], nums[3]
    if total <= 0:
        total = inp + out
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": total,
        "cache_read_tokens": cache,
    }


def _parse_gauge_tokens(text: str | None) -> tuple[int, int]:
    """Best-effort ``(used, size)`` from a gauge like ``"45,000/200,000 tokens"``.

    Used as a fallback when the engine does not report ``PromptResponse.usage``:
    ``used`` is the conversation's current context occupancy, the closest proxy
    we have for "tokens used so far". Returns ``(0, 0)`` when unparseable.
    """
    if not text:
        return 0, 0
    head = text.split("tokens", 1)[0]
    head = head.replace(",", "").strip()
    if "/" not in head:
        return 0, 0
    used_str, _, size_str = head.partition("/")
    try:
        return int(used_str.strip() or 0), int(size_str.strip() or 0)
    except ValueError:
        return 0, 0


def is_direct_cli_alias(alias: str | None) -> bool:
    """True when ``alias`` addresses a direct CLI engine (``cli:<engine>``)."""
    return bool(alias) and alias.startswith(CLI_ALIAS_PREFIX)


def engine_for_alias(alias: str | None) -> str:
    """Return the engine name encoded in a ``cli:<engine>`` alias, or ""."""
    if not is_direct_cli_alias(alias):
        return ""
    return (alias or "")[len(CLI_ALIAS_PREFIX):].strip().lower()


def alias_for_engine(engine: str) -> str:
    """Return the synthetic alias for an engine (inverse of :func:`engine_for_alias`)."""
    return f"{CLI_ALIAS_PREFIX}{engine}"


def known_cli_aliases() -> set[str]:
    """All valid direct-CLI aliases (``cli:<engine>``), regardless of install state.

    Used to validate a board's enabled-CLI list: an engine may be enabled even
    when its launch command is not installed on this host (it can run elsewhere).
    """
    return {alias_for_engine(engine) for engine in _ENGINES}


def display_name_for_alias(alias: str | None) -> str:
    """Human label for a direct CLI alias (e.g. ``cli:claude`` → ``Claude (direct)``)."""
    spec = _ENGINES.get(engine_for_alias(alias))
    return f"{spec.label} (direct)" if spec else (alias or "")


@dataclass(frozen=True)
class _EngineRuntime:
    label: str
    command: str
    args: list[str]
    timeout_seconds: int
    create_timeout_seconds: int


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_args(key: str, default: str) -> str:
    """Read adapter args while preserving an explicit empty override."""
    raw = os.environ.get(key)
    return default if raw is None else raw.strip()


# Hard-coded turn timeout for agent-team direct-CLI runs. Long-form jobs (e.g. a
# 90-minute revenge-youtube script) routinely run well past the ai_code default
# (and past ``_safe_timeout``'s 900s cap), so we pin a generous 3-hour ceiling
# here and bypass that cap. Session-create stays short (spawning npx is fast).
_DIRECT_ACP_TURN_TIMEOUT_SECONDS = 3 * 60 * 60  # 3 hours


def _engine_runtime(engine: str) -> _EngineRuntime:
    """Resolve an engine's command/args from env; turn timeout is hard-pinned to 3h."""
    from plugins.ai_code.tools._acp_base import _DEFAULT_CREATE_TIMEOUT_SECONDS
    from plugins.ai_code.tools.cli_tools import _split_args

    spec = _ENGINES[engine]
    up = engine.upper()
    return _EngineRuntime(
        label=f"{spec.label} ACP",
        command=_env(f"AI_CODE_{up}_ACP_COMMAND", spec.command),
        args=_split_args(_env_args(f"AI_CODE_{up}_ACP_ARGS", spec.args)),
        timeout_seconds=_DIRECT_ACP_TURN_TIMEOUT_SECONDS,
        create_timeout_seconds=_DEFAULT_CREATE_TIMEOUT_SECONDS,
    )


def available_targets() -> list[dict]:
    """List the direct CLI engines, flagging which look runnable on this host.

    ``available`` is a best-effort hint (the engine's launch command is on
    ``PATH``); the UI uses it to disable engines that clearly are not installed.
    A run still surfaces a precise error if the command turns out to be missing.
    """
    targets: list[dict] = []
    for engine, spec in _ENGINES.items():
        command = _env(f"AI_CODE_{engine.upper()}_ACP_COMMAND", spec.command)
        targets.append(
            {
                "id": alias_for_engine(engine),
                "engine": engine,
                "label": spec.label,
                "available": shutil.which(command) is not None,
            }
        )
    return targets


class _DirectAcpTranslator:
    """Map ACP custom-progress deltas to ``AgentEvent`` frames.

    Unlike the LLM path — where the whole ACP call is one tool card and its
    progress is folded into that card — a direct conversation surfaces the agent
    naturally: assistant text streams as ``text_delta``, reasoning as
    ``thinking``, and each ACP tool call becomes its own tool card. Tool calls
    are paired by the ACP tool id so a card opens on start and closes on the
    terminal progress update.
    """

    def __init__(self) -> None:
        self._counter = 0
        #: ACP tool-call id → ``(our tool_id, tool_name, command)`` for open
        #: cards. ``command`` is the latest params summary seen (it may arrive
        #: after the call starts, so it is remembered and surfaced when known).
        self._open: dict[str, tuple[str, str, str]] = {}
        #: Last context-window gauge text seen (``"45,000/200,000 tokens"``),
        #: persisted so the cockpit can show it after the run ends.
        self.cli_usage_text: str | None = None
        #: Authoritative cumulative token totals for the turn, from ACP's
        #: ``PromptResponse.usage`` when the engine provides it; else ``None``.
        self.totals: dict[str, int] | None = None

    def on_delta(self, delta: dict[str, object]) -> list[tuple[str, dict]]:
        frames: list[tuple[str, dict]] = []
        for key, value in delta.items():
            if isinstance(value, str) and value:
                frames.extend(self._on_event(key, value))
        return frames

    def finalize(self) -> list[tuple[str, dict]]:
        """Close any tool cards still open when the run ends (best-effort)."""
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
            # The CLI's own context-window gauge (e.g. "45,000/200,000 tokens").
            # Surfaced live *and* remembered so the cockpit can still show it once
            # the run ends (the run record persists this string).
            self.cli_usage_text = value
            return [ev.usage({"text": value})]
        if key == _KEY_USAGE_FINAL:
            # Authoritative cumulative totals from ACP's ``PromptResponse.usage``
            # (``input\x00output\x00total\x00cache_read``). Stored for finalize;
            # no live frame needed (the gauge already shows progress).
            self.totals = _parse_usage_totals(value)
            return []
        # Unknown keys are status pings — ignore.
        return []

    def _tool_start(self, value: str) -> list[tuple[str, dict]]:
        # Encoded ``kind\x00title\x00tool_id\x00command`` (see ai_code _acp_base).
        # ``maxsplit`` keeps any stray delimiter inside the trailing command.
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
        # ``command`` feeds the cockpit's input summary (shown beside the tool
        # name) and the expandable params block.
        tool_input: dict[str, str] = {}
        if kind:
            tool_input["kind"] = kind
        if command:
            tool_input["command"] = command
        return [
            ev.tool_use_start(
                tool_id=tool_id,
                tool_name=tool_name,
                tool_input=tool_input,
            )
        ]

    def _tool_progress(self, value: str) -> list[tuple[str, dict]]:
        # Encoded ``tool_id\x00status\x00title\x00output\x00command``.
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
        # The command can arrive after the call starts (and updates are deltas),
        # so remember the latest non-empty one and surface it when new.
        newly_known = bool(command) and command != known_command
        if newly_known:
            known_command = command
            self._open[acp_id] = (tool_id, tool_name, known_command)
        input_update = {"command": known_command} if known_command else None
        if status in _TERMINAL_TOOL_STATUSES:
            self._open.pop(acp_id, None)
            # The card shows ``output_preview`` inline; the full result is
            # persisted out-of-stream and lazy-loaded when it exceeds the cap.
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
        # Non-terminal: forward progress text and a freshly-revealed command.
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
    """Drives one direct ACP prompt turn and streams its frames.

    ``stream_frames`` yields ``(event_type, data)`` frames as the agent works and,
    when the run completes, records the assistant's full reply in
    :attr:`final_text`. The caller persists the frames (the event store is the
    source of truth) exactly as for an LLM run.
    """

    def __init__(
        self,
        *,
        engine: str,
        prompt: str,
        cwd: str,
        thread_id: str,
        auto_approve: bool = True,
        idle_timeout_seconds: int = 0,
        mcp_config: dict | None = None,
        secrets: list[str] | None = None,
    ) -> None:
        self.engine = engine
        self.prompt = prompt
        self.cwd = cwd
        self.thread_id = thread_id
        # Accepted for a uniform seam with the owned ACP engine but not honoured
        # here: MCP pass-through and secret masking require the owned engine
        # (``AGENT_TEAM_ACP_ENGINE=owned``); this legacy path ignores them.
        self._mcp_config = mcp_config
        self._secrets = secrets or []
        #: Whether to approve the agent's permission requests automatically.
        #: Unattended runs need ``True`` to proceed; ``False`` makes the agent
        #: read-only (the ACP manager answers requests with ``cancelled``).
        self.auto_approve = auto_approve
        #: Stop the turn when no progress frame arrives for this many seconds
        #: (``0`` disables). A working agent streams output continuously, so a
        #: long silence means the subprocess is wedged; this bounds that wait
        #: well below the ACP manager's absolute turn ceiling.
        self.idle_timeout_seconds = max(0, idle_timeout_seconds)
        self.final_text = ""
        self.ok = True
        self.cancelled = False
        #: Set when the turn was stopped by the idle timeout (vs a user cancel).
        self.timed_out = False
        self._translator = _DirectAcpTranslator()
        #: Context-window gauge text seen during the turn (for display after end).
        self.cli_usage_text: str | None = None
        #: Token totals to persist on the run: the engine's authoritative
        #: cumulative ``PromptResponse.usage`` when available, else a gauge-based
        #: fallback (``total_tokens`` = current context occupancy).
        self.usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
        }

    async def stream_frames(
        self, cancel_event: asyncio.Event
    ) -> AsyncIterator[tuple[str, dict]]:
        spec = _ENGINES.get(self.engine)
        if spec is None:
            self.ok = False
            self.final_text = f"Unknown direct CLI engine: {self.engine!r}."
            yield ev.error(error_class="ValueError", message=self.final_text)
            return

        from plugins.ai_code.tools._acp_base import (
            _BackgroundLoop,
            _drain_queue,
            _manager_run_ok,
            cancel_acp_sessions,
        )

        runtime = _engine_runtime(self.engine)
        progress_q: queue.Queue = queue.Queue()
        # Account-scoped session key, persistent so a follow-up turn on the same
        # conversation thread continues the same ACP session.
        key = f"{alias_for_engine(self.engine)}::{self.thread_id}"
        future = _BackgroundLoop.instance().submit(
            _manager_run_ok(
                key=key,
                persist=True,
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
                agent_label=runtime.label,
            )
        )
        loop = asyncio.get_running_loop()
        result_fut = asyncio.wrap_future(future)
        last_activity = time.monotonic()
        try:
            while not result_fut.done():
                if cancel_event.is_set():
                    self.cancelled = True
                    future.cancel()
                    await asyncio.to_thread(cancel_acp_sessions, self.thread_id)
                    self._capture_usage()
                    return
                deltas = await loop.run_in_executor(
                    None, _drain_queue, progress_q, _DRAIN_TIMEOUT_SECONDS
                )
                if deltas:
                    last_activity = time.monotonic()
                elif self.idle_timeout_seconds and (
                    time.monotonic() - last_activity > self.idle_timeout_seconds
                ):
                    # No output for the idle window: treat the run as wedged,
                    # stop the subprocess, and surface a clear reason.
                    self.cancelled = True
                    self.timed_out = True
                    self.ok = False
                    self.final_text = (
                        f"{runtime.label} produced no output for "
                        f"{self.idle_timeout_seconds}s; the run was stopped."
                    )
                    future.cancel()
                    await asyncio.to_thread(cancel_acp_sessions, self.thread_id)
                    yield ev.error(
                        error_class="IdleTimeout", message=self.final_text
                    )
                    self._capture_usage()
                    return
                for delta in deltas:
                    for frame in self._translator.on_delta(delta):
                        yield frame
            # Flush whatever progress arrived just before completion.
            for delta in _drain_queue(progress_q, 0):
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

    def _capture_usage(self) -> None:
        """Snapshot the gauge text + token totals for the run record.

        Prefers the engine's authoritative cumulative ``PromptResponse.usage``;
        when absent, falls back to the context-window gauge's ``used`` count as a
        best-effort running total so the cockpit still shows a number.
        """
        self.cli_usage_text = self._translator.cli_usage_text
        totals = self._translator.totals
        if totals is not None:
            self.usage = {**self.usage, **totals}
            return
        used, _size = _parse_gauge_tokens(self.cli_usage_text)
        if used:
            self.usage = {**self.usage, "total_tokens": used}
