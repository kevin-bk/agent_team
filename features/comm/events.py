"""Notification event types and the mapping from loop lifecycle to events.

The dispatcher keys off :class:`LoopState` (the persisted, canonical lifecycle),
not the transient terminal outcome strings — see the plan's §0.6.2. Events that
have no dedicated loop state (reviewer fail, budget hit, blocked subtask) are
emitted explicitly from journal/task-graph call sites in a later slice.
"""

from __future__ import annotations

from agent_team.features.board.runtime.loop.status import LoopState

#: v1 notification event types.
EVENT_PLAN_APPROVAL_REQUIRED = "plan_approval_required"
EVENT_ANSWERS_REQUIRED = "answers_required"
EVENT_PLAN_CHANGE_REQUESTED = "plan_change_requested"
EVENT_HUMAN_REVIEW_REQUIRED = "human_review_required"
EVENT_GOAL_COMPLETE = "goal_complete"
EVENT_GOAL_FAILED = "goal_failed"
EVENT_GOAL_CANCELLED = "goal_cancelled"

#: The events a board can route in v1 (used by the settings UI allowlist).
V1_EVENT_TYPES = (
    EVENT_PLAN_APPROVAL_REQUIRED,
    EVENT_ANSWERS_REQUIRED,
    EVENT_PLAN_CHANGE_REQUESTED,
    EVENT_HUMAN_REVIEW_REQUIRED,
    EVENT_GOAL_COMPLETE,
    EVENT_GOAL_FAILED,
    EVENT_GOAL_CANCELLED,
)

#: Events that need a human to act → tag the assignee. ``goal_complete`` tags
#: both assignee and reporter; failures/cancel use the channel's tag_mode default.
_NEEDS_PERSON = frozenset(
    {
        EVENT_PLAN_APPROVAL_REQUIRED,
        EVENT_ANSWERS_REQUIRED,
        EVENT_PLAN_CHANGE_REQUESTED,
        EVENT_HUMAN_REVIEW_REQUIRED,
    }
)

#: Loop states that should produce an outbound event. States like ``planning`` /
#: ``running`` / ``plan_approved`` are intentionally silent.
_STATE_TO_EVENT: dict[LoopState, str] = {
    LoopState.WAITING_PLAN_APPROVAL: EVENT_PLAN_APPROVAL_REQUIRED,
    LoopState.WAITING_ANSWERS: EVENT_ANSWERS_REQUIRED,
    LoopState.PLAN_CHANGE_REQUESTED: EVENT_PLAN_CHANGE_REQUESTED,
    LoopState.WAITING_FOR_HUMAN: EVENT_HUMAN_REVIEW_REQUIRED,
    LoopState.COMPLETE: EVENT_GOAL_COMPLETE,
    LoopState.FAILED: EVENT_GOAL_FAILED,
    LoopState.CANCELLED: EVENT_GOAL_CANCELLED,
}


def event_for_state(state: str) -> str | None:
    """Map a persisted ``loop_state`` value onto a notification event, or ``None``."""
    try:
        key = LoopState(state)
    except ValueError:
        return None
    return _STATE_TO_EVENT.get(key)


def event_needs_person(event_type: str) -> bool:
    """Whether an event is a human-needed prompt (drives default tagging)."""
    return event_type in _NEEDS_PERSON


def dedupe_key(*, task_id: str, event_type: str, state: str, attempt: int) -> str:
    """Build the idempotency key for a delivery.

    A repeated lifecycle publish for the same task/state/attempt collapses to one
    notification. ``attempt`` distinguishes successive human-needed pauses (e.g.
    two separate question rounds) so the second still notifies.
    """
    return f"{task_id}:{event_type}:{state}:{attempt}"
