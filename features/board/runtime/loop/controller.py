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

from agent_team.features.board.runtime.loop.verdict import (
    LoopVerdict,
    Verdict,
    format_evidence_digest,
)

#: Loop outcomes (the terminal decision).
OUTCOME_COMPLETE = "complete"
OUTCOME_CAPPED = "capped"
OUTCOME_NEEDS_HUMAN = "needs_human"
#: Stopped early because progress stalled — N attempts in a row scored 0 (or the
#: generator run kept failing, e.g. a provider rate/credit limit). Routed to a
#: human rather than burning the whole attempt cap against a wall.
OUTCOME_STALLED = "stalled"

#: Default number of consecutive zero-progress attempts that trips the stall
#: guard. Chosen so a transient blip (one bad turn) is tolerated, but a genuine
#: wall (e.g. the generator producing nothing every turn) stops fast.
DEFAULT_MAX_ZERO_STREAK = 3

#: Legacy/non-strict loops do not receive the strict planning preamble, so the
#: opening prompt must still tell the model which responsibility it owns. Run
#: metadata already records ``generator``; this banner makes the role visible
#: to the agent itself as well.
CODING_AGENT_ROLE_PREAMBLE = (
    "ROLE: CODING AGENT\n\n"
    "You are the coding and implementation agent. Own the requested code, test, "
    "and delivery work; do not grade your own completion."
)


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
        preamble: str | None = None,
        max_zero_streak: int = DEFAULT_MAX_ZERO_STREAK,
    ) -> None:
        self._objective = (objective or "").strip()
        self._max_attempts = max(1, max_attempts)
        self._attempts = 0
        #: Stop after this many consecutive zero-progress attempts (0 disables).
        #: A "zero-progress" attempt is one the generator failed to run at all
        #: (e.g. provider limit) or whose verdict scored 0; any positive score
        #: resets the streak. This is the backstop for the "stuck at 0%" wall.
        self._max_zero_streak = max(0, max_zero_streak)
        self._zero_streak = 0
        #: Workspace-relative path of a plan written by the planning phase. When
        #: set, prompts point the generator at the file (handoff by reference)
        #: instead of relying on the inline objective alone.
        self._plan_path = (plan_path or "").strip() or None
        #: Optional instruction block prepended to the opening prompt. Strict
        #: planning uses it to point the generator at the approved contract and
        #: forbid silent scope expansion; when set it replaces the plan
        #: reference in the opening (the follow-up still cites the plan).
        self._preamble = (preamble or "").strip() or None

    @property
    def attempts(self) -> int:
        return self._attempts

    def start(self) -> str:
        """Return the opening generator prompt (the objective + a clear ask)."""
        objective = self._objective or "Complete the task described in the workspace."
        if self._preamble:
            plan_ref = f"{self._preamble}\n\n"
        elif self._plan_path:
            plan_ref = (
                f"{CODING_AGENT_ROLE_PREAMBLE}\n\n"
                f"A detailed implementation plan has been written to `{self._plan_path}`. "
                "Read it first and implement every step in it.\n\n"
            )
        else:
            plan_ref = f"{CODING_AGENT_ROLE_PREAMBLE}\n\n"
        return (
            f"{objective}\n\n"
            f"{plan_ref}"
            "Work autonomously until every requirement is fully done and verified. "
            "Inspect the real current state of the workspace rather than relying on "
            "memory, and gather authoritative evidence by running the relevant "
            "tests/build/checks. When you believe it is complete, state clearly what "
            "you changed and how you verified it."
        )

    def on_attempt_finished(
        self, verdict: Verdict | None, *, errored: bool = False
    ) -> LoopStep:
        """Record one finished attempt and decide the next step.

        ``errored`` flags an attempt whose generator run did not complete (e.g. a
        provider rate/credit limit): there is no work to grade, so it counts as
        zero progress toward the stall guard.
        """
        self._attempts += 1
        if verdict is not None and verdict.verdict == LoopVerdict.PASS:
            self._zero_streak = 0
            return Done(OUTCOME_COMPLETE)
        if verdict is not None and verdict.verdict == LoopVerdict.NEEDS_HUMAN:
            return Done(OUTCOME_NEEDS_HUMAN)

        # Track the zero-progress streak. A failed generator run, or a graded
        # attempt that scored 0, is "no progress"; any positive score resets it.
        # A missing verdict (the evaluator could not grade) leaves the streak
        # untouched — that is the existing fail-open path, not evidence of a wall.
        if errored or (verdict is not None and verdict.score <= 0):
            self._zero_streak += 1
        elif verdict is not None and verdict.score > 0:
            self._zero_streak = 0

        if self._max_zero_streak and self._zero_streak >= self._max_zero_streak:
            return Done(OUTCOME_STALLED)
        if self._attempts >= self._max_attempts:
            return Done(OUTCOME_CAPPED)
        return Continue(self._followup(verdict))

    def _followup(self, verdict: Verdict | None) -> str:
        missing = (verdict.missing if verdict is not None else "").strip()
        # Relay the evaluator's concrete findings (failed commands, checks,
        # risks) so the next attempt can act on the actual error instead of
        # re-deriving it from the prose `missing` alone.
        evidence = (
            format_evidence_digest(verdict.evidence) if verdict is not None else ""
        )
        evidence_block = (
            f"\n\nWhat the evaluator observed (address this directly):\n{evidence}"
            if evidence
            else ""
        )
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
                f"{missing}"
                f"{evidence_block}\n\n"
                f"{reinspect}"
            )
        return (
            "The task does not appear complete and verified yet. Review what is "
            f"still missing against the objective.{evidence_block}\n\n{reinspect}"
        )
