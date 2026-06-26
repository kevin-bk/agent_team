"""Duck-typed extraction of text / params / output from ACP payloads.

ACP content and tool-call payloads arrive as either pydantic models or decoded
dicts and their schema drifts between engine versions, so every accessor here
tries both attribute and key access and tolerates missing fields.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

#: Upper bound (chars) on the params summary carried inline on a stream event.
TOOL_PARAM_LIMIT = 2000

#: ``raw_input`` keys holding the single most useful param to show, in priority
#: order: a shell command, then a file path, then a search/fetch target.
_TOOL_INPUT_KEYS = (
    "command",
    "cmd",
    "abs_path",
    "path",
    "file_path",
    "pattern",
    "query",
    "url",
)

_PLAN_ICONS = {"completed": "✓", "in_progress": "▶", "pending": "☐"}

#: Map an ACP plan-entry status to the three states the checklist UI renders.
_PLAN_STATUS = {
    "completed": "done",
    "done": "done",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "running": "in_progress",
    "pending": "todo",
    "todo": "todo",
}


def text_from_content_block(block: Any) -> str:
    """Best-effort plain text from an ACP content block (model / dict / list)."""
    if block is None:
        return ""
    text = getattr(block, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(block, dict):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
        return ""
    if isinstance(block, (list, tuple)):
        return "".join(text_from_content_block(item) for item in block)
    return ""


def _dual_get(obj: Any, *names: str) -> Any:
    """First non-``None`` value among ``names`` (dict key or attribute)."""
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def tool_input_summary(raw_input: Any) -> str:
    """One-line description of a tool's params (its command, path, …), or ``""``."""
    if raw_input is None:
        return ""
    if isinstance(raw_input, str):
        return raw_input.strip()
    data: Any = raw_input
    if not isinstance(data, dict):
        dump = getattr(data, "model_dump", None)
        data = dump(by_alias=True) if callable(dump) else None
    if not isinstance(data, dict):
        return str(raw_input).strip()
    if not data:
        return ""
    for key in _TOOL_INPUT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            return "\n".join(str(item) for item in value)
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(data).strip()


def _tool_content_block_text(block: Any) -> str:
    block_type = str(_dual_get(block, "type") or "")
    if block_type == "diff":
        path = str(_dual_get(block, "path") or "")
        new_text = str(_dual_get(block, "new_text", "newText") or "")
        return f"{path}\n{new_text}".strip()
    if block_type == "terminal":
        return ""
    inner = _dual_get(block, "content")
    return text_from_content_block(inner if inner is not None else block)


def _raw_output_text(raw_output: Any) -> str:
    if raw_output is None:
        return ""
    if isinstance(raw_output, str):
        return raw_output.strip()
    if isinstance(raw_output, dict):
        for key in ("output", "stdout", "text", "content", "result"):
            value = raw_output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        try:
            return json.dumps(raw_output, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(raw_output).strip()
    return str(raw_output).strip()


def tool_output_text(update: Any) -> str:
    """Best-effort output text of a tool call from its content + raw output."""
    parts = [
        _tool_content_block_text(block)
        for block in (getattr(update, "content", None) or [])
    ]
    text = "\n".join(part for part in parts if part).strip()
    return text or _raw_output_text(getattr(update, "raw_output", None))


def strip_nul(text: str) -> str:
    r"""Drop NULs so a value survives the ``\x00``-delimited event encoding."""
    return text.replace("\x00", " ")


def format_plan_entries(entries: Any) -> str:
    lines: list[str] = []
    for entry in entries or []:
        status = str(getattr(entry, "status", "") or "")
        title = str(
            getattr(entry, "title", "") or getattr(entry, "description", "") or ""
        )
        if title:
            lines.append(f"{_PLAN_ICONS.get(status, '☐')} {title}")
    return "\n".join(lines)


def plan_entries(
    entries: Any, *, mask: Callable[[str], str] | None = None
) -> list[dict]:
    """Normalise ACP plan entries into ``[{"title", "status"}]`` for the UI.

    Status is collapsed to the three states the checklist renders
    (``todo`` / ``in_progress`` / ``done``); unknown statuses default to
    ``todo``. ``mask`` (when given) is applied to each title so secrets that
    leaked into a plan item never reach the stream.
    """
    out: list[dict] = []
    for entry in entries or []:
        raw_status = str(getattr(entry, "status", "") or "").strip().lower()
        title = str(
            getattr(entry, "title", "") or getattr(entry, "description", "") or ""
        ).strip()
        if not title:
            continue
        if mask is not None:
            title = mask(title)
        out.append({"title": title, "status": _PLAN_STATUS.get(raw_status, "todo")})
    return out


# --- permission option selection ------------------------------------------


def _option_id(option: Any) -> str | None:
    if isinstance(option, dict):
        value = option.get("optionId") or option.get("option_id")
    else:
        value = getattr(option, "option_id", None) or getattr(option, "optionId", None)
    return str(value) if value is not None else None


def _option_kind(option: Any) -> str:
    if isinstance(option, dict):
        value = option.get("kind", "")
    else:
        value = getattr(option, "kind", "")
    return str(value or "").lower()


def pick_permission_option_id(options: Any) -> str | None:
    """Choose an 'allow' option id, preferring allow-always, else the first option."""
    fallback: str | None = None
    for option in options or []:
        option_id = _option_id(option)
        if option_id is None:
            continue
        kind = _option_kind(option)
        if kind == "allow_always":
            return option_id
        if kind.startswith("allow") and fallback is None:
            fallback = option_id
    if fallback is not None:
        return fallback
    for option in options or []:
        option_id = _option_id(option)
        if option_id is not None:
            return option_id
    return None
