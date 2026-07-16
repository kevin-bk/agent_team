"""Live loop status + the task state machine it drives.

``LoopStatus`` is a small snapshot a driver publishes at each lifecycle point
(start, each attempt, terminal) so a UI can show a progress chip. ``LoopState``
is the persisted task state; :func:`outcome_to_state` maps a terminal loop
outcome onto it.

State machine:

    ReadyForAgent → [Planning] → Running → (Complete | NeedsRevision→Running |
                                            WaitingForHuman | Failed)

An optional planning phase runs first (``Planning``) when the loop is started
with a planner; it then transitions to ``Running``. A guardrail stop (attempt
cap, token/cost/runtime budget) or a ``needs_human`` verdict routes to
:attr:`LoopState.WAITING_FOR_HUMAN` — never a silent finish.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LoopState(StrEnum):
    """Persisted lifecycle state of a task's autonomous loop.

    This is the single canonical public lifecycle for the cockpit. The planning
    states sit *before* execution: a strict-planning task drafts artifacts
    (``PLANNING``), stops for human review (``WAITING_PLAN_APPROVAL``), is
    approved (``PLAN_APPROVED``), then runs (``RUNNING``).
    """

    PLANNING = "planning"
    #: A human stopped the active planner turn to add guidance. Partial
    #: artifacts and the planner conversation are preserved for a later resume.
    PLANNING_PAUSED = "planning_paused"
    #: Planning artifacts exist and the system has stopped, waiting for a human
    #: to approve them or request changes. No process is kept alive here.
    WAITING_PLAN_APPROVAL = "waiting_plan_approval"
    #: A human approved the plan; execution has not started yet.
    PLAN_APPROVED = "plan_approved"
    RUNNING = "running"
    COMPLETE = "complete"
    WAITING_FOR_HUMAN = "waiting_for_human"
    #: Execution discovered the approved plan is wrong/unsafe and paused for a
    #: human to revise it (the marker artifact gates continuation).
    PLAN_CHANGE_REQUESTED = "plan_change_requested"
    #: An agent (planner or generator) raised blocking questions and the phase
    #: paused for a human to answer them via the cockpit's question cards.
    WAITING_ANSWERS = "waiting_answers"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class LoopStatus:
    """A snapshot of a loop's progress, for live publishing."""

    task_id: str
    state: LoopState
    attempt: int
    max_attempts: int
    objective: str
    #: Terminal outcome string (e.g. ``complete`` / ``capped`` / ``budget`` /
    #: ``needs_human`` / ``cancelled``), set once the loop ends.
    outcome: str | None = None
    total_tokens: int = 0


#: Terminal outcomes that need a human to look before anything else happens.
_HUMAN_OUTCOMES = frozenset({"capped", "budget", "needs_human", "stalled"})


def outcome_to_state(outcome: str) -> LoopState:
    """Map a terminal loop outcome onto the persisted task state."""
    if outcome == "complete":
        return LoopState.COMPLETE
    if outcome == "cancelled":
        return LoopState.CANCELLED
    if outcome == "plan_change":
        return LoopState.PLAN_CHANGE_REQUESTED
    if outcome == "needs_answers":
        return LoopState.WAITING_ANSWERS
    if outcome in _HUMAN_OUTCOMES:
        return LoopState.WAITING_FOR_HUMAN
    return LoopState.FAILED
