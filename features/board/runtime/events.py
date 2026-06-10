"""Run lifecycle constants and the streaming event contract.

The ``AgentEvent`` frame types are the source-of-truth wire contract between the
backend and the web frontend. The backend only emits the subset it can build
from the LangGraph stream; the frontend treats unknown/advanced types as
optional so it degrades gracefully.

Each frame is a plain ``dict`` so it serializes directly to the SSE ``data``
field. The persisted event row carries ``run_id``, ``seq`` and ``type``; the
``data`` payload holds the type-specific fields below.
"""

from __future__ import annotations

# --- Run status -----------------------------------------------------------

RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_DONE = "done"
RUN_ERROR = "error"
RUN_CANCELLED = "cancelled"

#: Statuses a run can no longer leave.
TERMINAL_RUN_STATUSES = frozenset({RUN_DONE, RUN_ERROR, RUN_CANCELLED})

# --- Event types ----------------------------------------------------------

EVENT_RUN_START = "run_start"
EVENT_TEXT_DELTA = "text_delta"
EVENT_THINKING = "thinking"
EVENT_TOOL_USE_START = "tool_use_start"
EVENT_TOOL_USE_PROGRESS = "tool_use_progress"
EVENT_TOOL_USE_END = "tool_use_end"
EVENT_USAGE = "usage"
EVENT_ERROR = "error"
EVENT_FINAL_ANSWER = "final_answer"
EVENT_RUN_END = "run_end"
EVENT_HEARTBEAT = "heartbeat"


def run_start(*, agent_alias: str) -> tuple[str, dict]:
    return EVENT_RUN_START, {"agent_alias": agent_alias}


def text_delta(text: str) -> tuple[str, dict]:
    return EVENT_TEXT_DELTA, {"text": text}


def thinking(text: str) -> tuple[str, dict]:
    return EVENT_THINKING, {"text": text}


def tool_use_start(*, tool_id: str, tool_name: str, tool_input: dict | None) -> tuple[str, dict]:
    return EVENT_TOOL_USE_START, {
        "tool_id": tool_id,
        "tool_name": tool_name,
        "input": tool_input or {},
    }


def tool_use_progress(*, tool_id: str, chunk: str) -> tuple[str, dict]:
    return EVENT_TOOL_USE_PROGRESS, {"tool_id": tool_id, "chunk": chunk}


def tool_use_end(
    *,
    tool_id: str,
    tool_name: str,
    success: bool,
    is_error: bool,
    output_preview: str,
    duration_ms: int | None = None,
) -> tuple[str, dict]:
    return EVENT_TOOL_USE_END, {
        "tool_id": tool_id,
        "tool_name": tool_name,
        "success": success,
        "is_error": is_error,
        "output_preview": output_preview,
        "duration_ms": duration_ms,
    }


def usage(usage_dict: dict) -> tuple[str, dict]:
    return EVENT_USAGE, {"usage": usage_dict}


def error(*, error_class: str, message: str, recoverable: bool = False) -> tuple[str, dict]:
    return EVENT_ERROR, {
        "error_class": error_class,
        "message": message,
        "recoverable": recoverable,
    }


def final_answer(content: str) -> tuple[str, dict]:
    return EVENT_FINAL_ANSWER, {"content": content}


def run_end(*, status: str, final_answer: str | None = None) -> tuple[str, dict]:
    return EVENT_RUN_END, {"status": status, "final_answer": final_answer}
