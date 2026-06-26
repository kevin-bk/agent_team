"""The loop controller: pure continue-vs-stop decisions (no I/O).

Given an objective and the verdict of the latest attempt, the controller decides
whether to run another generator attempt (and with what follow-up prompt) or to
finish. It performs no I/O so the decision rules are unit-testable in isolation;
the driver owns all persistence and run execution.

Decision rules:

* A ``pass`` verdict finishes as ``complete``.
* A ``needs_human`` verdict finishes as ``needs_human``.
* A ``fail`` (or a missing verdict — the evaluator could not grade, so we
  **fail open** and keep going) continues, unless the attempt budget is
  exhausted, in which case it finishes as ``capped``.

The continuation prompt is a normal user message appended to the same thread —
no system-prompt or toolset changes — so the cacheable prior prefix is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

#: Loop outcomes (the terminal decision).
OUTCOME_COMPLETE = "complete"
OUTCOME_CAPPED = "capped"
OUTCOME_NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True)
class Continue:
    """Run another generator attempt with this follow-up message."""

    followup: str


@dataclass(frozen=True)
class Done:
    """Stop the loop with this outcome."""

    outcome: str


LoopStep = Continue | Done


class LoopController:
    """Decides whether to keep iterating, given the latest attempt's verdict."""

    def __init__(
        self,
        objective: str,
        *,
        max_attempts: int = 10,
        plan_path: str | None = None,
    ) -> None:
        self._objective = (objective or "").strip()
        self._max_attempts = max(1, max_attempts)
        self._attempts = 0
        #: Workspace-relative path of a plan written by the planning phase. When
        #: set, prompts point the generator at the file (handoff by reference)
        #: instead of relying on the inline objective alone.
        self._plan_path = (plan_path or "").strip() or None

    @property
    def attempts(self) -> int:
        return self._attempts

    def start(self) -> str:
        """Return the opening generator prompt (the objective + a clear ask)."""
        objective = self._objective or "Complete the task described in the workspace."
        plan_ref = (
            f"A detailed implementation plan has been written to `{self._plan_path}`. "
            "Read it first and implement every step in it.\n\n"
            if self._plan_path
            else ""
        )
        return (
            f"{objective}\n\n"
            f"{plan_ref}"
            "Work autonomously until every requirement is fully done and verified. "
            "Inspect the real current state of the workspace rather than relying on "
            "memory, and gather authoritative evidence by running the relevant "
            "tests/build/checks. When you believe it is complete, state clearly what "
            "you changed and how you verified it."
        )

    def on_attempt_finished(self, verdict: Verdict | None) -> LoopStep:
        """Record one finished attempt and decide the next step."""
        self._attempts += 1
        if verdict is not None and verdict.verdict == LoopVerdict.PASS:
            return Done(OUTCOME_COMPLETE)
        if verdict is not None and verdict.verdict == LoopVerdict.NEEDS_HUMAN:
            return Done(OUTCOME_NEEDS_HUMAN)
        if self._attempts >= self._max_attempts:
            return Done(OUTCOME_CAPPED)
        return Continue(self._followup(verdict))

    def _followup(self, verdict: Verdict | None) -> str:
        missing = (verdict.missing if verdict is not None else "").strip()
        plan_ref = (
            f" Consult the plan in `{self._plan_path}` for the remaining steps."
            if self._plan_path
            else ""
        )
        reinspect = (
            "Inspect the real current state of the workspace (do not rely on "
            "memory). For each remaining requirement, make concrete progress and "
            "gather authoritative evidence by running the relevant tests/build/"
            "checks. Keep the full objective intact and finish only once every "
            f"requirement is provably satisfied.{plan_ref}"
        )
        if missing:
            return (
                "The task is not complete yet. Outstanding work:\n\n"
                f"{missing}\n\n"
                f"{reinspect}"
            )
        return (
            "The task does not appear complete and verified yet. Review what is "
            f"still missing against the objective. {reinspect}"
        )
