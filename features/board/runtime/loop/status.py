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
    """Persisted lifecycle state of a task's autonomous loop."""

    PLANNING = "planning"
    RUNNING = "running"
    COMPLETE = "complete"
    WAITING_FOR_HUMAN = "waiting_for_human"
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
_HUMAN_OUTCOMES = frozenset({"capped", "budget", "needs_human"})


def outcome_to_state(outcome: str) -> LoopState:
    """Map a terminal loop outcome onto the persisted task state."""
    if outcome == "complete":
        return LoopState.COMPLETE
    if outcome == "cancelled":
        return LoopState.CANCELLED
    if outcome in _HUMAN_OUTCOMES:
        return LoopState.WAITING_FOR_HUMAN
    return LoopState.FAILED
