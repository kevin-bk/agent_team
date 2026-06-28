"""Message rendering for notification events.

Privacy default (plan §0.6.7): a notification carries a **title + short reason +
deep link** only. Never dump artifact bodies, full question text, or logs into a
channel. Keep this module the single place where outbound text is shaped.
"""

from __future__ import annotations

from agent_team.features.comm.events import (
    EVENT_ANSWERS_REQUIRED,
    EVENT_GOAL_CANCELLED,
    EVENT_GOAL_COMPLETE,
    EVENT_GOAL_FAILED,
    EVENT_HUMAN_REVIEW_REQUIRED,
    EVENT_PLAN_APPROVAL_REQUIRED,
    EVENT_PLAN_CHANGE_REQUESTED,
)

#: Per-event (title suffix, one-line reason, severity).
_TEMPLATES: dict[str, tuple[str, str, str]] = {
    EVENT_PLAN_APPROVAL_REQUIRED: (
        "plan is ready for approval",
        "The agent drafted a plan and paused for your review.",
        "info",
    ),
    EVENT_ANSWERS_REQUIRED: (
        "needs your answer",
        "The agent paused with a blocking question instead of guessing.",
        "warning",
    ),
    EVENT_PLAN_CHANGE_REQUESTED: (
        "needs a plan change",
        "The agent found the approved plan may be wrong or unsafe and paused.",
        "warning",
    ),
    EVENT_HUMAN_REVIEW_REQUIRED: (
        "needs human review",
        "Execution stopped at a guardrail and needs a human to look.",
        "warning",
    ),
    EVENT_GOAL_COMPLETE: (
        "complete",
        "The goal was verified complete.",
        "success",
    ),
    EVENT_GOAL_FAILED: (
        "failed",
        "The loop ended without a verified result.",
        "error",
    ),
    EVENT_GOAL_CANCELLED: (
        "was cancelled",
        "The run was cancelled.",
        "info",
    ),
}


def render_event(*, event_type: str, task_key: str, task_title: str) -> tuple[str, str, str]:
    """Return ``(title, body, severity)`` for an event.

    ``title`` is a short headline (``T-42 needs your answer``); ``body`` is a
    single neutral reason line. No task internals beyond key + title.
    """
    suffix, reason, severity = _TEMPLATES.get(
        event_type, ("update", "The task state changed.", "info")
    )
    headline = f"{task_key} {suffix}"
    body_lines = [reason]
    if task_title:
        body_lines.append(f"> {task_title}")
    return headline, "\n".join(body_lines), severity


def task_deep_link(
    *, deep_link_base: str | None, board_slug: str, task_key: str
) -> str | None:
    """Build the cockpit deep link for a task, or ``None`` without a base URL.

    Mirrors the SPA route ``/agent-team/boards/{slug}/tasks/{key}``.
    """
    if not deep_link_base:
        return None
    base = deep_link_base.rstrip("/")
    return f"{base}/agent-team/boards/{board_slug}/tasks/{task_key}"
