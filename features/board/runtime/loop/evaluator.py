"""The evaluator contract and prompt for independently grading an attempt.

The generator must never grade its own work, so evaluation runs as a separate
turn (its own worker/run) instructed to *disprove* completion: assume the work
is broken until verified by evidence (tests/lint/build/acceptance criteria). The
evaluator returns its verdict as a JSON object that :func:`parse_verdict` reads.
"""

from __future__ import annotations

from typing import Protocol

from agent_team.features.board.runtime.loop.verdict import Verdict

#: Instruction prepended to an evaluator turn. The strict JSON contract at the
#: end is what :func:`~...loop.verdict.parse_verdict` reads back.
EVALUATOR_SYSTEM = (
    "You are an independent verifier. Another agent attempted the task below; "
    "your job is to decide whether it is genuinely complete. Be skeptical: assume "
    "the work is incomplete or broken until proven otherwise by concrete "
    "evidence. Where possible, actually verify — run the project's tests, linters "
    "and build, and check the acceptance criteria against the files in the "
    "workspace — rather than trusting the agent's summary.\n\n"
    "When done, end your reply with a single JSON object on its own line:\n"
    '{"verdict": "pass|fail|needs_human", "score": 0.0-1.0, '
    '"missing": "what still has to be done (empty if pass)", '
    '"evidence": {"checks": "what you ran and saw"}}\n\n'
    "Use \"needs_human\" only when a person must decide (risky/ambiguous change, "
    "or the work cannot be verified here)."
)


def build_evaluator_prompt(objective: str, generator_summary: str) -> str:
    """Compose the evaluator turn's user message."""
    objective = (objective or "").strip() or "(no explicit objective given)"
    summary = (generator_summary or "").strip() or "(the agent produced no summary)"
    return (
        f"{EVALUATOR_SYSTEM}\n\n"
        f"## Objective / acceptance criteria\n{objective}\n\n"
        f"## What the agent reported doing\n{summary}\n\n"
        "Now verify and return your verdict."
    )


class Evaluator(Protocol):
    """Grades one attempt against the objective.

    Implementations may run a worker turn, execute checks, or both. Returning
    ``None`` means "could not evaluate" — the controller treats that as fail-open
    (keep iterating), with the attempt budget as the backstop.
    """

    async def evaluate(
        self, *, objective: str, generator_summary: str, workspace_path: str
    ) -> Verdict | None: ...
