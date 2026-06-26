"""The optional planning phase: analyse the task and write a structured plan.

Before the generator/evaluator loop begins, a *planning* turn (its own
worker/run) can explore the workspace and write a concise, structured
``PLAN.md``. The loop then hands that plan to the generator **by reference** —
the opening prompt points at the file and the generator reads it — rather than
inlining the plan text, so a long plan never bloats every turn and the
generator always sees the plan's current state on disk.

The plan is captured the same way the evaluator's verdict is: the agent writes
a known file in the workspace. Planning is best-effort — if the file is not
produced the loop simply proceeds from the raw objective (fail-open).
"""

from __future__ import annotations

from typing import Protocol

#: The sections a plan must contain, in order. Kept small and execution-focused
#: so it sharpens the objective without turning into a design essay.
PLAN_STRUCTURE: list[tuple[str, str]] = [
    (
        "OBJECTIVE",
        "Restate the goal in one or two sentences, in clear operational terms.",
    ),
    (
        "CONTEXT",
        "The relevant files, components and constraints found in the workspace.",
    ),
    (
        "APPROACH",
        "The chosen approach at a high level, with a one-line rationale.",
    ),
    (
        "IMPLEMENTATION STEPS",
        "An ordered list of steps; each step states its goal, method and the "
        "file(s) it touches.",
    ),
    (
        "TESTING AND VALIDATION",
        "Exactly how completion is verified — the tests/build/checks to run and "
        "what success looks like. These are the acceptance criteria.",
    ),
]


def format_plan_structure() -> str:
    """Render :data:`PLAN_STRUCTURE` as a numbered spec for the prompt."""
    sections = [
        f"{i}. {title}\n   {desc}"
        for i, (title, desc) in enumerate(PLAN_STRUCTURE, 1)
    ]
    return "The plan must use exactly this structure:\n\n" + "\n\n".join(sections)


#: Instruction prepended to a planning turn. The agent must research first and
#: write a plan — it must not start implementing the change in this turn.
PLANNER_SYSTEM = (
    "You are a planning agent. Analyse the task below against the real state of "
    "the workspace and produce a concise, well-researched implementation plan. "
    "Explore efficiently (read the relevant files, search the codebase) and base "
    "the plan on what you actually find, not assumptions.\n\n"
    "Do NOT implement the change in this turn: make no edits to source files and "
    "run no build/deploy steps. Your only output is the plan file. Keep the plan "
    "strictly in scope — no extra features or unrelated ideas."
)


def build_plan_prompt(objective: str, *, plan_path: str) -> str:
    """Compose the planning turn's user message.

    ``plan_path`` is a workspace-relative path the agent must write the plan to;
    it is also what the loop later points the generator at.
    """
    objective = (objective or "").strip() or "(no explicit objective given)"
    return (
        f"{PLANNER_SYSTEM}\n\n"
        f"## Task\n{objective}\n\n"
        f"## Plan format\n{format_plan_structure()}\n\n"
        "## How to deliver the plan\n"
        f"Write the plan as Markdown to the file `{plan_path}` (create the parent "
        "directory if needed). Overwrite any existing content so the file holds "
        "only the plan, with one `#` heading per section above. Then end your "
        "reply with a one-line confirmation that the plan was written.\n\n"
        "Now research the workspace and write the plan."
    )


class Planner(Protocol):
    """Produces a plan file for the objective before the loop starts.

    Returns the workspace-relative path of the plan it wrote, or ``None`` when
    no plan could be produced (the loop then proceeds from the raw objective).
    """

    async def plan(
        self, *, objective: str, workspace_path: str
    ) -> str | None: ...
