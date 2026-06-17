"""Translate LangGraph stream chunks into ``AgentEvent`` frames.

The run streams token-by-token. Three stream modes are consumed together:

* ``messages`` — the live source of visible output. Each chunk is an
  ``(AIMessageChunk, metadata)`` pair; assistant text becomes ``text_delta``
  frames and reasoning/thinking content becomes ``thinking`` frames, both
  emitted incrementally as the model generates them.
* ``updates`` — full node snapshots. Used only for the structured execution
  frames (``tool_use_start`` / ``tool_use_end``) and to capture the final
  assistant text (persisted as the run's final answer). The snapshot text is
  *not* re-streamed, since ``messages`` already streamed it token-by-token.
* ``custom`` — live progress emitted by AI-coding sub-agents (Claude/Codex/
  Cursor ACP). Surfaced as ``tool_use_progress`` on the running tool card so
  the user sees the sub-agent work as it happens.

Unknown modes are ignored so the stream degrades gracefully.
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
#: Chars of a tool result kept inline in the streamed frame. The full output is
#: persisted out-of-stream and lazy-loaded on demand (see ``local_backend`` /
#: the ``/runs/{id}/tools/{tool_id}/output`` endpoint), so this only bounds what
#: the timeline shows by default.
_PREVIEW_LIMIT = 2000

#: Structured content-block types that represent a tool invocation, never
#: visible prose. They are surfaced as ``tool_use_start`` frames, so any copy
#: that leaks into the assistant text must be dropped.
_TOOL_BLOCK_TYPES = frozenset(
    {"tool_use", "tool_call", "input_json_delta", "server_tool_use"}
)

#: Content-block types that carry the model's reasoning/thinking text.
_THINKING_BLOCK_TYPES = frozenset({"thinking", "reasoning", "reasoning_content"})


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
    ``tool_use`` blocks. A snapshot serializes each non-text block as a
    standalone JSON line, so a tool call would appear both as a proper tool
    frame and as a JSON line in the text. The tool frame is the source of
    truth; here we discard every line that parses to a tool-invocation block,
    leaving only the model's prose. Lines that are not such blocks are kept.
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


# --- message-chunk parsing -------------------------------------------------


def _looks_like_message(obj: Any) -> bool:
    """True for a LangChain message/-chunk (has ``content``, not a str/dict)."""
    return hasattr(obj, "content") and not isinstance(obj, (str, bytes, dict))


def _extract_message(data: Any) -> Any:
    """Peel ``messages``-mode wrappers down to the ``AIMessageChunk``.

    Depending on ``stream_mode``/``subgraphs`` the payload is either the raw
    ``(message, metadata)`` pair or ``(namespace, (message, metadata))``. The
    namespace wrapper is peeled until the message-like object is found.
    """
    cur = data
    for _ in range(4):
        if isinstance(cur, tuple) and len(cur) == 2:
            first, second = cur
            if _looks_like_message(first):
                return first
            cur = second
        else:
            break
    return cur if _looks_like_message(cur) else None


def _is_ai_message(msg: Any) -> bool:
    class_name = type(msg).__name__.lower()
    if "tool" in class_name or "human" in class_name or "system" in class_name:
        return False
    msg_type = str(getattr(msg, "type", "") or "").lower()
    return "ai" in class_name or msg_type in ("ai", "aimessagechunk")


def _split_message_deltas(msg: Any) -> tuple[str, str]:
    """Return ``(thinking, text)`` deltas carried by one message chunk.

    Handles plain-string content and Anthropic-style block lists; tool-use and
    JSON-delta blocks are skipped (they surface as tool frames via ``updates``).
    Provider reasoning carried in ``additional_kwargs`` is also collected.
    """
    content = getattr(msg, "content", None)
    text_parts: list[str] = []
    think_parts: list[str] = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    value = block.get("text")
                    if isinstance(value, str):
                        text_parts.append(value)
                elif block_type in _THINKING_BLOCK_TYPES:
                    value = (
                        block.get("thinking")
                        or block.get("reasoning")
                        or block.get("text")
                    )
                    if isinstance(value, str):
                        think_parts.append(value)
    extra = getattr(msg, "additional_kwargs", None)
    if isinstance(extra, dict):
        reasoning = extra.get("reasoning_content")
        if isinstance(reasoning, str):
            think_parts.append(reasoning)
    return "".join(think_parts), "".join(text_parts)


# --- custom (sub-agent) progress parsing -----------------------------------


def _unwrap_custom(data: Any) -> dict | None:
    """Peel any ``(namespace, payload)`` wrapper down to the custom dict."""
    cur = data
    for _ in range(4):
        if isinstance(cur, dict):
            return cur
        if isinstance(cur, tuple) and len(cur) == 2:
            cur = cur[1]
        else:
            break
    return cur if isinstance(cur, dict) else None


#: Status icons for an ACP sub-agent tool-call's terminal states. Non-terminal
#: pings (``in_progress`` / ``pending``) are skipped to keep the trail readable.
_ACP_STATUS_ICONS = {"completed": "\u2713", "failed": "\u2717"}


def _acp_progress_text(key: str, value: Any) -> str:
    """Map one AI-coding ACP custom event to a structured progress line, or "".

    Lines are kept on their own row (leading/trailing newlines) so the sub-agent
    trail reads as a list of actions rather than a flat run-on blob, and raw
    encodings (tool ids, statuses) never leak into the visible text.
    """
    if not isinstance(value, str) or not value:
        return ""
    # Order matters: ``_tool_start`` / ``_tool_progress`` must be matched before
    # the generic ``_progress`` suffix (``claude_acp_tool_progress`` also ends in
    # ``_progress``) or their raw ``\x00``-encoded payload leaks into the text.
    if key.endswith("_tool_start"):
        # Encoded as ``kind\x00title\x00tool_id`` (see ai_code _acp_base).
        parts = value.split("\x00")
        title = parts[1] if len(parts) > 1 else parts[0]
        return f"\n\u2192 {title}\n" if title else ""
    if key.endswith("_tool_progress"):
        # Encoded as ``tool_id\x00status\x00title``. Only mark terminal states.
        parts = value.split("\x00")
        status = parts[1] if len(parts) > 1 else ""
        title = parts[2] if len(parts) > 2 else ""
        icon = _ACP_STATUS_ICONS.get(status)
        if not icon:
            return ""
        return f"  {icon} {title}\n" if title else f"  {icon}\n"
    if key.endswith(("_progress", "_thought", "_plan")):
        return value
    # ``_usage`` is a noisy status ping — skip it.
    return ""


class StreamTranslator:
    """Stateful translator: pairs tool calls with results across chunks."""

    def __init__(self) -> None:
        self._tool_counter = 0
        #: FIFO of ``(tool_id, tool_name)`` awaiting their result frame.
        self._pending_tools: list[tuple[str, str]] = []
        #: Live progress chunks accumulated per tool id (AI-coding sub-agents).
        #: Merged into the tool's final output so the streamed action trail is
        #: not lost when the result frame replaces the live progress.
        self._tool_progress: dict[str, list[str]] = {}
        #: Last full assistant snapshot seen, surfaced as the final answer.
        self.final_text = ""

    #: Emit order within a single ``updates`` chunk: tool calls before results.
    _EVENT_ORDER = {"tool_call": 0, "tool_result": 1}

    def translate(self, chunk: Any) -> list[tuple[str, dict]]:
        """Return ``(event_type, data)`` frames produced by one stream chunk."""
        mode, data = normalize_stream_chunk(chunk)
        if mode == "messages":
            return self._on_messages_mode(data)
        if mode == "custom":
            return self._on_custom_mode(data)
        if mode == "updates":
            return self._on_updates_mode(data)
        return []

    # --- messages mode (live token stream) ---------------------------------

    def _on_messages_mode(self, data: Any) -> list[tuple[str, dict]]:
        msg = _extract_message(data)
        if msg is None or not _is_ai_message(msg):
            return []
        thinking_text, text = _split_message_deltas(msg)
        frames: list[tuple[str, dict]] = []
        if thinking_text:
            frames.append(ev.thinking(thinking_text))
        if text:
            frames.append(ev.text_delta(text))
        return frames

    # --- updates mode (tool frames + final-answer capture) -----------------

    def _on_updates_mode(self, data: Any) -> list[tuple[str, dict]]:
        frames: list[tuple[str, dict]] = []
        ordered = sorted(
            iter_stream_chunk_events(data),
            key=lambda it: self._EVENT_ORDER.get(it.get("event_type"), 2),
        )
        for item in ordered:
            event_type = item.get("event_type")
            payload = item.get("payload") or {}
            if event_type == "tool_call":
                frames.append(self._on_tool_call(payload))
            elif event_type == "tool_result":
                frames.append(self._on_tool_result(payload))
            elif event_type == "node_message":
                # The snapshot is the authoritative final answer; capture it
                # but do not emit a frame — the text already streamed live via
                # the ``messages`` mode. Leaked tool-use JSON is stripped.
                text = strip_tool_blocks(str(payload.get("message") or ""))
                if text:
                    self.final_text = text
        return frames

    def _on_tool_call(self, payload: dict) -> tuple[str, dict]:
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
        # AI-coding sub-agents (Claude/Codex/Cursor ACP) stream their action
        # trail (thoughts + ``→ tool`` lines + prose) only as live progress; the
        # result message is just the final prose. Use the accumulated trail as
        # the output so the tool-call steps survive after the run completes.
        output = self._tool_progress.pop(tool_id, [])
        output_text = "".join(output).strip() or message
        is_error = _looks_like_error(output_text)
        truncated = len(output_text) > _PREVIEW_LIMIT
        return ev.tool_use_end(
            tool_id=tool_id,
            tool_name=tool_name or result_name or "tool",
            success=not is_error,
            is_error=is_error,
            output_preview=output_text[:_PREVIEW_LIMIT],
            truncated=truncated,
            # Carried out-of-band for the backend to persist; only the full
            # output (when actually longer than the preview) is worth storing.
            output_full=output_text if truncated else None,
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

    # --- custom mode (sub-agent live progress) -----------------------------

    def _on_custom_mode(self, data: Any) -> list[tuple[str, dict]]:
        payload = _unwrap_custom(data)
        if not payload:
            return []
        # Attribute progress to the tool currently running (the ACP sub-agent
        # call). Without an open tool there is nowhere to show it, so skip.
        if not self._pending_tools:
            return []
        tool_id = self._pending_tools[-1][0]
        frames: list[tuple[str, dict]] = []
        for key, value in payload.items():
            chunk = _acp_progress_text(str(key), value)
            if chunk:
                self._tool_progress.setdefault(tool_id, []).append(chunk)
                frames.append(ev.tool_use_progress(tool_id=tool_id, chunk=chunk))
        return frames


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
