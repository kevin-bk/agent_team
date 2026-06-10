"""Translate LangGraph stream chunks into ``AgentEvent`` frames.

P1 consumes ``stream_mode="updates"``: assistant message snapshots become
``text_delta`` frames (the last one is the final answer), tool calls/results
become ``tool_use_start``/``tool_use_end`` pairs. Token-level deltas and ACP
``custom`` progress events are a later enhancement; unknown modes are ignored so
the stream degrades gracefully.
"""

from __future__ import annotations

import json
from typing import Any

from agent_team.features.board.runtime import events as ev
from core.agents.stream_updates import (
    extract_stream_node_outputs,
    iter_stream_chunk_events,
    normalize_stream_chunk,
)

_ERROR_HINTS = ("error", "traceback", "exception", "failed")
_PREVIEW_LIMIT = 500

#: Structured content-block types that represent a tool invocation, never
#: visible prose. They are surfaced as ``tool_use_start`` frames, so any copy
#: that leaks into the assistant text must be dropped.
_TOOL_BLOCK_TYPES = frozenset(
    {"tool_use", "tool_call", "input_json_delta", "server_tool_use"}
)


def _looks_like_error(message: str) -> bool:
    head = (message or "").strip().lower()[:64]
    return any(hint in head for hint in _ERROR_HINTS)


def normalize_tool_input(tool_name: str, tool_input: Any) -> dict:
    """Adapt standard-tool arg names to the shared cockpit's display schema.

    The cockpit UI is shared with deep-agent and renders a tool's input from
    specific keys: file writes show ``content`` and the shell summary reads
    ``command``. agent-manager's standard tools name some of these args
    differently — LangChain's ``WriteFileTool`` stores the body under ``text``
    and the shell tool takes ``commands`` — so without this adapter the file
    body the agent writes renders blank. This only shapes the *display* frame;
    the real tool invocation and its arguments are untouched.

    The mapping is additive (originals are kept) and idempotent, so it is safe
    to apply both when emitting live frames and when rebuilding old transcripts.
    """
    if not isinstance(tool_input, dict):
        return {}
    out = dict(tool_input)
    if "content" not in out and isinstance(out.get("text"), str):
        out["content"] = out["text"]
    if "command" not in out and "commands" in out:
        commands = out["commands"]
        if isinstance(commands, list):
            out["command"] = "\n".join(str(item) for item in commands)
        elif isinstance(commands, str):
            out["command"] = commands
    return out


def strip_tool_blocks(text: str) -> str:
    """Remove tool-use JSON blocks that leaked into assistant text.

    Anthropic-style messages carry ``content`` as a list of ``text`` and
    ``tool_use`` blocks. The upstream normalizer serializes each non-text block
    as a standalone JSON line, so a tool call appears both as a proper tool
    frame and as a JSON line in the text. The tool frame is the source of truth;
    here we discard every line that parses to a tool-invocation block, leaving
    only the model's prose. Lines that are not such blocks are kept verbatim.
    """
    if "tool_use" not in text:
        return text
    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                block = json.loads(stripped)
            except ValueError:
                kept.append(line)
                continue
            if isinstance(block, dict) and block.get("type") in _TOOL_BLOCK_TYPES:
                continue
        kept.append(line)
    return "\n".join(kept).strip()


class StreamTranslator:
    """Stateful translator: pairs tool calls with results across chunks."""

    def __init__(self) -> None:
        self._tool_counter = 0
        #: FIFO of ``(tool_id, tool_name)`` awaiting their result frame.
        self._pending_tools: list[tuple[str, str]] = []
        #: Last assistant text seen, surfaced as the final answer at run end.
        self.final_text = ""
        #: Text already streamed for the current (open) assistant bubble, so we
        #: emit only the new suffix and never re-send a duplicated snapshot.
        self._open_text = ""

    #: Emit order within a single update: the model's prose ("I'll use a tool")
    #: comes before the tool call it announces, and tool results come last. The
    #: upstream parser lists tool calls before their message text, so we reorder
    #: here to match how the conversation actually reads in the UI.
    _EVENT_ORDER = {"node_message": 0, "tool_call": 1, "tool_result": 2}

    def translate(self, chunk: Any) -> list[tuple[str, dict]]:
        """Return ``(event_type, data)`` frames produced by one stream chunk."""
        mode, data = normalize_stream_chunk(chunk)
        if mode != "updates":
            return []

        frames: list[tuple[str, dict]] = []
        # ``sorted`` is stable, so frames within the same category keep their
        # original relative order while text moves ahead of its tool call.
        ordered = sorted(
            iter_stream_chunk_events(data),
            key=lambda it: self._EVENT_ORDER.get(it.get("event_type"), 3),
        )
        for item in ordered:
            event_type = item.get("event_type")
            payload = item.get("payload") or {}
            if event_type == "tool_call":
                frames.append(self._on_tool_call(payload))
            elif event_type == "tool_result":
                frames.append(self._on_tool_result(payload))
            elif event_type == "node_message":
                frame = self._on_node_message(str(payload.get("message") or ""))
                if frame is not None:
                    frames.append(frame)
        return frames

    def _on_node_message(self, text: str) -> tuple[str, dict] | None:
        """Emit a ``text_delta`` for new assistant text only (deduped).

        ``updates`` mode delivers full message snapshots, and the same snapshot
        can surface twice (subgraph + parent). The client treats ``text_delta``
        as an append, so we send only the suffix beyond what we already streamed
        into the current bubble and drop exact-duplicate snapshots. Leaked
        tool-use JSON blocks are stripped first so they never reach the UI.
        """
        text = strip_tool_blocks(text)
        if not text or text == self._open_text:
            return None
        if self._open_text and text.startswith(self._open_text):
            delta = text[len(self._open_text) :]
        else:
            delta = text
        self._open_text = text
        self.final_text = text
        return ev.text_delta(delta)

    def _on_tool_call(self, payload: dict) -> tuple[str, dict]:
        # A tool call starts a new turn: the client closes the open assistant
        # bubble, so the next text begins a fresh one — reset the suffix tracker.
        self._open_text = ""
        self._tool_counter += 1
        tool_id = f"t{self._tool_counter}"
        tool_name = str(payload.get("tool_name") or "tool")
        self._pending_tools.append((tool_id, tool_name))
        args = payload.get("args")
        return ev.tool_use_start(
            tool_id=tool_id,
            tool_name=tool_name,
            tool_input=normalize_tool_input(tool_name, args),
        )

    def _on_tool_result(self, payload: dict) -> tuple[str, dict]:
        result_name = str(payload.get("tool_name") or "")
        tool_id, tool_name = self._match_pending(result_name)
        message = str(payload.get("message") or "")
        is_error = _looks_like_error(message)
        return ev.tool_use_end(
            tool_id=tool_id,
            tool_name=tool_name or result_name or "tool",
            success=not is_error,
            is_error=is_error,
            output_preview=message[:_PREVIEW_LIMIT],
        )

    def _match_pending(self, result_name: str) -> tuple[str, str]:
        """Pop the matching pending tool (by name, else oldest)."""
        for index, (tool_id, name) in enumerate(self._pending_tools):
            if result_name and name == result_name:
                self._pending_tools.pop(index)
                return tool_id, name
        if self._pending_tools:
            return self._pending_tools.pop(0)
        self._tool_counter += 1
        return f"t{self._tool_counter}", result_name


def extract_usage(chunk: Any) -> dict[str, int]:
    """Sum token usage from a chunk's messages (zeros when none present)."""
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
    mode, data = normalize_stream_chunk(chunk)
    if mode != "updates":
        return totals
    for node_data in extract_stream_node_outputs(data).values():
        for msg in node_data.get("messages", []) or []:
            meta = getattr(msg, "usage_metadata", None)
            if not meta:
                continue
            totals["input_tokens"] += int(meta.get("input_tokens", 0) or 0)
            totals["output_tokens"] += int(meta.get("output_tokens", 0) or 0)
            details = meta.get("input_token_details") or {}
            totals["cache_read_tokens"] += int(details.get("cache_read", 0) or 0)
    return totals
