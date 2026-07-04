"""One-shot CLI execution inside a sandbox (Strategy A) + stream → frame parsing.

A *direct-CLI* agent normally talks ACP over a long-lived stdio subprocess. That
duplex transport does not survive OpenSandbox's one-shot ``commands.run`` model,
so for the isolated runtime we run the CLI in **non-interactive print mode** and
parse its structured stream into the same ``AgentEvent`` frames the ACP path
emits. The frontend/SSE therefore need no change.

Each engine contributes:

* :meth:`CliExecSpec.build_argv` — the non-interactive command line.
* :meth:`CliExecSpec.translate` — a stateful translator turning one output line
  into zero or more ``(event_type, data)`` frames.

Claude is implemented first (its ``--output-format stream-json`` wire format is
well documented and battle-tested in the sibling deep-agent project). Codex and
Cursor specs are stubbed with a clear error until their print-mode formats are
wired — see ``docs/plans/opensandbox-runtime-implementation-plan.md`` §11.
"""

from __future__ import annotations

import json
import shlex
from typing import Protocol

from agent_team.features.board.runtime import events as ev

#: Chars of a tool result shown inline; longer output is offloaded out-of-stream.
_OUTPUT_PREVIEW_LIMIT = 2000


class TurnParseResult:
    """Accumulated outcome of parsing one CLI turn's stream."""

    __slots__ = ("final_text", "usage", "cost_usd", "session_id", "error_kind", "ok")

    def __init__(self) -> None:
        self.final_text: str = ""
        self.usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
        }
        self.cost_usd: float = 0.0
        self.session_id: str | None = None
        self.error_kind: str | None = None
        self.ok: bool = True


class CliTranslator(Protocol):
    """Stateful per-turn translator: one output line → zero or more frames."""

    def on_line(self, line: str) -> list[tuple[str, dict]]: ...

    def finalize(self) -> list[tuple[str, dict]]: ...

    @property
    def result(self) -> TurnParseResult: ...


class CliExecSpec(Protocol):
    """How to run one engine non-interactively inside a sandbox."""

    engine: str

    def build_argv(self, *, prompt: str, workdir: str) -> list[str]: ...

    def new_translator(self) -> CliTranslator: ...


# ---------------------------------------------------------------------------
# Claude Code (`claude -p ... --output-format stream-json`)
# ---------------------------------------------------------------------------


class _ClaudeTranslator:
    """Map Claude Code ``stream-json`` lines to ``AgentEvent`` frames.

    Wire layout (``--verbose --include-partial-messages``)::

        {"type":"system","subtype":"init",...}
        {"type":"stream_event","event":{"type":"content_block_delta",
                                        "delta":{"type":"text_delta","text":"Hi"}}}
        {"type":"tool_use","id":"toolu_1","name":"Bash","input":{"command":"ls"}}
        {"type":"tool_result","tool_use_id":"toolu_1","content":"a.py b.py"}
        {"type":"message_delta","usage":{"input_tokens":12,"output_tokens":7}}
        {"type":"result","subtype":"success","result":"Done.","total_cost_usd":0.08,...}
    """

    def __init__(self) -> None:
        self._result = TurnParseResult()
        self._counter = 0
        #: Claude tool_use id → our synthetic (tool_id, tool_name).
        self._open: dict[str, tuple[str, str]] = {}

    @property
    def result(self) -> TurnParseResult:
        return self._result

    def on_line(self, line: str) -> list[tuple[str, dict]]:
        line = line.strip()
        if not line or not line.startswith("{"):
            return []
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        ptype = payload.get("type")
        if ptype == "stream_event":
            return self._stream_event(payload)
        if ptype == "tool_use":
            return self._tool_use(payload)
        if ptype == "tool_result":
            return self._tool_result(payload)
        if ptype == "message_delta":
            return self._usage(payload)
        if ptype == "result":
            return self._final(payload)
        return []

    def finalize(self) -> list[tuple[str, dict]]:
        """Close any tool cards still open when the stream ends."""
        frames = [
            ev.tool_use_end(
                tool_id=tool_id,
                tool_name=tool_name,
                success=True,
                is_error=False,
                output_preview="",
            )
            for tool_id, tool_name in self._open.values()
        ]
        self._open.clear()
        return frames

    def _stream_event(self, payload: dict) -> list[tuple[str, dict]]:
        event = payload.get("event") or {}
        delta = event.get("delta") if isinstance(event, dict) else None
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            txt = delta.get("text")
            if isinstance(txt, str) and txt:
                return [ev.text_delta(txt)]
        return []

    def _tool_use(self, payload: dict) -> list[tuple[str, dict]]:
        name = str(payload.get("name") or "tool")
        acp_id = str(payload.get("id") or "")
        self._counter += 1
        tool_id = f"c{self._counter}"
        if acp_id:
            self._open[acp_id] = (tool_id, name)
        tool_input = payload.get("input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        return [ev.tool_use_start(tool_id=tool_id, tool_name=name, tool_input=tool_input)]

    def _tool_result(self, payload: dict) -> list[tuple[str, dict]]:
        acp_id = str(payload.get("tool_use_id") or "")
        opened = self._open.pop(acp_id, None)
        if opened is None:
            return []
        tool_id, tool_name = opened
        content = payload.get("content")
        if isinstance(content, list):
            preview = "\n".join(
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        elif isinstance(content, str):
            preview = content
        else:
            preview = ""
        is_error = bool(payload.get("is_error"))
        truncated = len(preview) > _OUTPUT_PREVIEW_LIMIT
        return [
            ev.tool_use_end(
                tool_id=tool_id,
                tool_name=tool_name,
                success=not is_error,
                is_error=is_error,
                output_preview=preview[:_OUTPUT_PREVIEW_LIMIT],
                truncated=truncated,
                output_full=preview if truncated else None,
            )
        ]

    def _usage(self, payload: dict) -> list[tuple[str, dict]]:
        usage = payload.get("usage") or {}
        self._result.usage["input_tokens"] += int(usage.get("input_tokens") or 0)
        self._result.usage["output_tokens"] += int(usage.get("output_tokens") or 0)
        return []

    def _final(self, payload: dict) -> list[tuple[str, dict]]:
        subtype = str(payload.get("subtype") or "success")
        self._result.final_text = str(payload.get("result") or "")
        self._result.cost_usd = float(payload.get("total_cost_usd") or 0.0)
        self._result.session_id = payload.get("session_id")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        if usage:
            self._result.usage["input_tokens"] = int(
                usage.get("input_tokens") or self._result.usage["input_tokens"]
            )
            self._result.usage["output_tokens"] = int(
                usage.get("output_tokens") or self._result.usage["output_tokens"]
            )
        self._result.usage["total_tokens"] = (
            self._result.usage["input_tokens"] + self._result.usage["output_tokens"]
        )
        if subtype != "success":
            self._result.ok = False
            self._result.error_kind = subtype
        return [ev.usage(dict(self._result.usage))]


class _ClaudeExecSpec:
    engine = "claude"

    def build_argv(self, *, prompt: str, workdir: str) -> list[str]:
        return [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "bypassPermissions",
        ]

    def new_translator(self) -> CliTranslator:
        return _ClaudeTranslator()


# ---------------------------------------------------------------------------
# Codex (`codex exec --json`)
# ---------------------------------------------------------------------------


class _CodexTranslator:
    """Map Codex ``codex exec --json`` JSONL top-level events to frames.

    Wire layout (one JSON object per line)::

        {"type":"thread.started","thread_id":"..."}
        {"type":"turn.started"}
        {"type":"item.started","item":{"id":"i1","type":"command_execution","command":"ls"}}
        {"type":"item.completed","item":{"id":"i1","type":"command_execution",
                                         "exit_code":0,"aggregated_output":"a.py"}}
        {"type":"item.completed","item":{"id":"i3","type":"agent_message","text":"Done."}}
        {"type":"turn.completed","usage":{"input_tokens":..,"output_tokens":..}}
        {"type":"turn.failed","error":{...}}  |  {"type":"error","message":"..."}

    Codex does not stream text deltas in ``exec --json`` — an ``agent_message`` is
    a whole message, so it is emitted as one ``text_delta`` and taken as the final
    text (the last one wins).
    """

    def __init__(self) -> None:
        self._result = TurnParseResult()
        #: Codex item id → (tool_id, tool_name) for command cards opened on start.
        self._open: dict[str, tuple[str, str]] = {}
        self._counter = 0

    @property
    def result(self) -> TurnParseResult:
        return self._result

    def on_line(self, line: str) -> list[tuple[str, dict]]:
        line = line.strip()
        if not line or not line.startswith("{"):
            return []
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        etype = payload.get("type")
        if etype == "item.started":
            return self._item_started(payload.get("item") or {})
        if etype in ("item.completed", "item.updated"):
            return self._item_done(payload.get("item") or {}, updated=etype == "item.updated")
        if etype == "turn.completed":
            return self._usage(payload.get("usage") or {})
        if etype in ("turn.failed", "error"):
            self._result.ok = False
            self._result.error_kind = _codex_error_text(payload)
            return [
                ev.error(
                    error_class="CodexError",
                    message=self._result.error_kind or "codex failed",
                )
            ]
        return []

    def finalize(self) -> list[tuple[str, dict]]:
        frames = [
            ev.tool_use_end(
                tool_id=tool_id, tool_name=tool_name, success=True,
                is_error=False, output_preview="",
            )
            for tool_id, tool_name in self._open.values()
        ]
        self._open.clear()
        return frames

    def _new_tool_id(self) -> str:
        self._counter += 1
        return f"x{self._counter}"

    def _item_started(self, item: dict) -> list[tuple[str, dict]]:
        itype = item.get("type")
        item_id = str(item.get("id") or "")
        if itype == "command_execution":
            tool_id = self._new_tool_id()
            if item_id:
                self._open[item_id] = (tool_id, "shell")
            command = str(item.get("command") or "")
            return [
                ev.tool_use_start(
                    tool_id=tool_id, tool_name="shell", tool_input={"command": command}
                )
            ]
        return []

    def _item_done(self, item: dict, *, updated: bool) -> list[tuple[str, dict]]:
        itype = item.get("type")
        item_id = str(item.get("id") or "")
        if itype == "agent_message":
            text = str(item.get("text") or "")
            if not text:
                return []
            self._result.final_text = text
            return [ev.text_delta(text)]
        if itype == "reasoning":
            text = str(item.get("text") or "")
            return [ev.thinking(text)] if text else []
        if itype == "todo_list":
            entries = _codex_todo_entries(item)
            return [ev.plan_update(entries)] if entries else []
        if itype == "command_execution":
            if updated:
                return []
            opened = self._open.pop(item_id, None)
            tool_id, tool_name = opened or (self._new_tool_id(), "shell")
            output = str(item.get("aggregated_output") or item.get("output") or "")
            exit_code = item.get("exit_code")
            is_error = bool(exit_code) if exit_code is not None else False
            frames: list[tuple[str, dict]] = []
            if opened is None:
                command = str(item.get("command") or "")
                frames.append(
                    ev.tool_use_start(
                        tool_id=tool_id,
                        tool_name=tool_name,
                        tool_input={"command": command},
                    )
                )
            truncated = len(output) > _OUTPUT_PREVIEW_LIMIT
            frames.append(
                ev.tool_use_end(
                    tool_id=tool_id, tool_name=tool_name, success=not is_error,
                    is_error=is_error, output_preview=output[:_OUTPUT_PREVIEW_LIMIT],
                    truncated=truncated, output_full=output if truncated else None,
                )
            )
            return frames
        if itype in ("file_change", "mcp_tool_call", "web_search") and not updated:
            # These arrive only as item.completed — render as a one-shot card.
            tool_id = self._new_tool_id()
            name = {
                "file_change": "edit",
                "mcp_tool_call": "mcp",
                "web_search": "web_search",
            }[itype]
            summary = str(item.get("text") or item.get("query") or item.get("summary") or "")
            return [
                ev.tool_use_start(tool_id=tool_id, tool_name=name, tool_input={}),
                ev.tool_use_end(
                    tool_id=tool_id, tool_name=name, success=True, is_error=False,
                    output_preview=summary[:_OUTPUT_PREVIEW_LIMIT],
                ),
            ]
        return []

    def _usage(self, usage: dict) -> list[tuple[str, dict]]:
        self._result.usage["input_tokens"] = int(usage.get("input_tokens") or 0)
        self._result.usage["output_tokens"] = int(usage.get("output_tokens") or 0)
        self._result.usage["cache_read_tokens"] = int(usage.get("cached_input_tokens") or 0)
        self._result.usage["total_tokens"] = (
            self._result.usage["input_tokens"] + self._result.usage["output_tokens"]
        )
        return [ev.usage(dict(self._result.usage))]


def _codex_error_text(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "codex turn failed")
    return str(payload.get("message") or err or "codex turn failed")


def _codex_todo_entries(item: dict) -> list[dict]:
    """Best-effort map a Codex ``todo_list`` item to plan_update entries."""
    raw = item.get("items") or item.get("todos") or []
    entries: list[dict] = []
    if isinstance(raw, list):
        for t in raw:
            if not isinstance(t, dict):
                continue
            title = str(t.get("text") or t.get("title") or "")
            if not title:
                continue
            if t.get("completed") is True or t.get("status") == "completed":
                status = "done"
            elif t.get("status") in ("in_progress", "running"):
                status = "in_progress"
            else:
                status = "todo"
            entries.append({"title": title, "status": status})
    return entries


class _CodexExecSpec:
    engine = "codex"

    def build_argv(self, *, prompt: str, workdir: str) -> list[str]:
        # We are already inside an isolated sandbox, so bypass Codex's own
        # approval prompts + inner sandbox. ``--skip-git-repo-check`` lets it run
        # in a plain workspace dir that may not be a git repo yet.
        return [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            prompt,
        ]

    def new_translator(self) -> CliTranslator:
        return _CodexTranslator()


# ---------------------------------------------------------------------------
# Cursor (`cursor-agent -p --output-format stream-json`)
# ---------------------------------------------------------------------------


class _CursorTranslator:
    """Map Cursor ``cursor-agent --output-format stream-json`` NDJSON to frames.

    Wire layout::

        {"type":"system",...}
        {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Hi"}]}}
        {"type":"tool_call","subtype":"started","call_id":"c1","tool_call":{"readToolCall":{"args":{...}}}}
        {"type":"tool_call","subtype":"completed","call_id":"c1","tool_call":{"readToolCall":{"result":{...}}}}
        {"type":"result","result":"...","session_id":"..."}

    Without ``--stream-partial-output`` each ``assistant`` line is a *complete*
    message between tool calls, so it maps to one ``text_delta``. The canonical
    final answer is the ``result`` event.
    """

    def __init__(self) -> None:
        self._result = TurnParseResult()
        self._open: dict[str, tuple[str, str]] = {}
        self._counter = 0

    @property
    def result(self) -> TurnParseResult:
        return self._result

    def on_line(self, line: str) -> list[tuple[str, dict]]:
        line = line.strip()
        if not line or not line.startswith("{"):
            return []
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        etype = payload.get("type")
        if etype == "assistant":
            return self._assistant(payload)
        if etype == "tool_call":
            return self._tool_call(payload)
        if etype == "result":
            return self._final(payload)
        return []

    def finalize(self) -> list[tuple[str, dict]]:
        frames = [
            ev.tool_use_end(
                tool_id=tool_id, tool_name=tool_name, success=True,
                is_error=False, output_preview="",
            )
            for tool_id, tool_name in self._open.values()
        ]
        self._open.clear()
        return frames

    def _assistant(self, payload: dict) -> list[tuple[str, dict]]:
        message = payload.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        text = ""
        if isinstance(content, list):
            text = "".join(
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        if not text:
            return []
        return [ev.text_delta(text)]

    def _tool_call(self, payload: dict) -> list[tuple[str, dict]]:
        subtype = payload.get("subtype")
        call_id = str(payload.get("call_id") or "")
        tool_call = payload.get("tool_call") or {}
        name = "tool"
        body: dict = {}
        if isinstance(tool_call, dict) and tool_call:
            name = next(iter(tool_call.keys()))
            body = tool_call.get(name) or {}
            # ``readToolCall`` → ``read``
            if name.endswith("ToolCall"):
                name = name[: -len("ToolCall")]
        if subtype == "started":
            self._counter += 1
            tool_id = f"u{self._counter}"
            if call_id:
                self._open[call_id] = (tool_id, name)
            args = body.get("args") if isinstance(body, dict) else None
            return [
                ev.tool_use_start(
                    tool_id=tool_id,
                    tool_name=name,
                    tool_input=args if isinstance(args, dict) else {},
                )
            ]
        if subtype == "completed":
            opened = self._open.pop(call_id, None)
            tool_id, tool_name = opened or (f"u{self._counter}", name)
            result = body.get("result") if isinstance(body, dict) else None
            preview, is_error = _cursor_tool_result(result)
            truncated = len(preview) > _OUTPUT_PREVIEW_LIMIT
            return [
                ev.tool_use_end(
                    tool_id=tool_id, tool_name=tool_name, success=not is_error,
                    is_error=is_error, output_preview=preview[:_OUTPUT_PREVIEW_LIMIT],
                    truncated=truncated, output_full=preview if truncated else None,
                )
            ]
        return []

    def _final(self, payload: dict) -> list[tuple[str, dict]]:
        self._result.final_text = str(payload.get("result") or "")
        self._result.session_id = payload.get("session_id")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        frames: list[tuple[str, dict]] = []
        if usage:
            self._result.usage["input_tokens"] = int(usage.get("input_tokens") or 0)
            self._result.usage["output_tokens"] = int(usage.get("output_tokens") or 0)
            self._result.usage["total_tokens"] = (
                self._result.usage["input_tokens"] + self._result.usage["output_tokens"]
            )
            frames.append(ev.usage(dict(self._result.usage)))
        subtype = payload.get("subtype")
        if subtype and subtype != "success":
            self._result.ok = False
            self._result.error_kind = str(subtype)
        return frames


def _cursor_tool_result(result: object) -> tuple[str, bool]:
    """Return ``(preview, is_error)`` from a Cursor tool result object."""
    if not isinstance(result, dict):
        return "", False
    if "success" in result:
        inner = result.get("success") or {}
        content = inner.get("content") if isinstance(inner, dict) else None
        return (str(content or ""), False)
    if "error" in result:
        inner = result.get("error") or {}
        msg = inner.get("message") if isinstance(inner, dict) else inner
        return (str(msg or "error"), True)
    return (str(result)[:_OUTPUT_PREVIEW_LIMIT], False)


class _CursorExecSpec:
    engine = "cursor"

    def build_argv(self, *, prompt: str, workdir: str) -> list[str]:
        # ``--force`` auto-approves all tool permissions (unattended runs).
        return [
            "cursor-agent",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--force",
        ]

    def new_translator(self) -> CliTranslator:
        return _CursorTranslator()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_SPECS: dict[str, CliExecSpec] = {
    "claude": _ClaudeExecSpec(),
    "codex": _CodexExecSpec(),
    "cursor": _CursorExecSpec(),
}


def get_exec_spec(engine: str) -> CliExecSpec:
    """Return the one-shot exec spec for ``engine``.

    Wired engines: claude, codex, cursor. Raises :class:`NotImplementedError`
    for anything else (add a :class:`CliExecSpec` or use the ACP sidecar).
    """
    spec = _SPECS.get(engine)
    if spec is None:
        raise NotImplementedError(
            f"Sandboxed one-shot exec is not yet wired for engine {engine!r}. "
            "Supported: " + ", ".join(sorted(_SPECS)) + ". "
            "Add a CliExecSpec (build_argv + translator) for it, or use the ACP "
            "sidecar runtime (Phase 2)."
        )
    return spec


def command_string(argv: list[str]) -> str:
    """Quote an argv into a single shell command string for ``exec_shell``."""
    return " ".join(shlex.quote(a) for a in argv)
