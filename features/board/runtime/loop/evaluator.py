"""The evaluator contract and prompt for independently grading an attempt.

The generator must never grade its own work, so evaluation runs as a separate
turn (its own worker/run) instructed to *disprove* completion: assume the work
is broken until verified by evidence (tests/lint/build/acceptance criteria).

The verdict is captured two ways, in priority order: the evaluator writes a JSON
object to a known file in the workspace (robust for CLI agents whose stdout is
noisy), and also echoes it in its reply. The caller reads the file first and
falls back to :func:`parse_verdict` over the text.
"""

from __future__ import annotations

from typing import Protocol

from agent_team.features.board.runtime.loop.verdict import Verdict

#: JSON shape both the file and the echoed reply must use.
_VERDICT_SHAPE = (
    '{"verdict": "pass|fail|needs_human", "score": 0.0-1.0, '
    '"missing": "what still has to be done (empty if pass)", '
    '"evidence": {"checks": "what you ran and saw"}}'
)

#: Instruction prepended to an evaluator turn. The strict JSON contract is what
#: :func:`~...loop.verdict.parse_verdict` reads back from either the file or text.
EVALUATOR_SYSTEM = (
    "ROLE: INDEPENDENT EVALUATOR\n\n"
    "You are an independent verifier. Another agent attempted the task below; "
    "your job is to decide whether it is genuinely complete. Be skeptical: assume "
    "the work is incomplete or broken until proven otherwise by concrete "
    "evidence. Where possible, actually verify — run the project's tests, linters "
    "and build, and check the acceptance criteria against the files in the "
    "workspace — rather than trusting the agent's summary.\n\n"
    "Use \"needs_human\" only when a person must decide (risky/ambiguous change, "
    "or the work cannot be verified here)."
)


def build_evaluator_prompt(
    objective: str,
    generator_summary: str,
    *,
    verdict_path: str | None = None,
) -> str:
    """Compose the evaluator turn's user message.

    When ``verdict_path`` is given (a workspace-relative path), the evaluator is
    told to write its verdict there as the authoritative output; it must also
    echo the same JSON in its reply as a fallback.
    """
    objective = (objective or "").strip() or "(no explicit objective given)"
    summary = (generator_summary or "").strip() or "(the agent produced no summary)"
    if verdict_path:
        output_instr = (
            "## How to report the verdict\n"
            f"Write a single JSON object to the file `{verdict_path}` "
            "(create the parent directory if needed). Overwrite any existing "
            "content so the file holds only the JSON. Use exactly this shape:\n"
            f"{_VERDICT_SHAPE}\n"
            "Then also end your reply with the same JSON object on its own line "
            "as a fallback.\n\n"
        )
    else:
        output_instr = (
            "## How to report the verdict\n"
            "End your reply with a single JSON object on its own line, using "
            f"exactly this shape:\n{_VERDICT_SHAPE}\n\n"
        )
    return (
        f"{EVALUATOR_SYSTEM}\n\n"
        f"## Objective / acceptance criteria\n{objective}\n\n"
        f"## What the agent reported doing\n{summary}\n\n"
        f"{output_instr}"
        "Now verify and report your verdict."
    )


class Evaluator(Protocol):
    """Grades one attempt against the objective.

    Implementations may run a worker turn, execute checks, or both. Returning
    ``None`` means "could not evaluate" — the controller treats that as fail-open
    (keep iterating), with the attempt budget as the backstop.
    """

    async def evaluate(
        self,
        *,
        objective: str,
        generator_summary: str,
        workspace_path: str,
        attempt_id: str | None = None,
    ) -> Verdict | None: ...
