"""Prompt templates for the strict (contract-driven) planning workflow.

These are intentionally strict and file-based: a planner researches the
workspace and writes durable artifacts, an optional reviewer grades them, and
the strict generator/evaluator both work from the approved artifacts rather than
the raw objective. Keeping every prompt here (instead of scattered in service
code) makes them easy to version and test.
"""

from __future__ import annotations

from agent_team.features.board.runtime.loop import planning_artifacts as A

#: System preamble for the planning turn. The planner must research first and
#: only write artifacts — it must not implement the change.
PLANNER_SYSTEM = (
    "You are a senior product-engineering planning agent. Turn a rough task into "
    "a precise, well-researched implementation contract before any code changes.\n\n"
    "Rules:\n"
    "- Do NOT edit source files and do NOT run build/deploy steps in this turn.\n"
    "- Inspect the real workspace (read relevant files, search the codebase) and "
    "base the plan on what you actually find, not assumptions.\n"
    "- Keep scope tight: no extra features or unrelated ideas.\n"
    "- Make acceptance criteria concrete and verifiable, never vague like "
    '"works well".\n'
    "- If a missing decision would materially change the implementation, record "
    "it under Open Questions instead of guessing; otherwise state a safe "
    "assumption and proceed."
)

_SPEC_STRUCTURE = (
    "# SPEC\n\n"
    "## Goal\n## Original Request\n## Current Context\n## In Scope\n"
    "## Non Goals\n## Constraints\n## Acceptance Criteria\n"
    "## Verification Expectations\n## Open Questions\n## Assumptions\n## Risks"
)

_PLAN_STRUCTURE = (
    "# PLAN\n\n"
    "## Summary\n## Files And Components\n## Approach\n## Alternatives Considered\n"
    "## Implementation Steps\n## Data And API Changes\n## UI Changes\n"
    "## Verification Plan\n## Rollback Or Recovery\n## Risks"
)

_TASKS_SCHEMA = (
    "{\n"
    '  "version": 1,\n'
    '  "status": "draft",\n'
    '  "tasks": [\n'
    "    {\n"
    '      "id": "T1",\n'
    '      "title": "Short imperative task",\n'
    '      "status": "pending",\n'
    '      "depends_on": [],\n'
    '      "objective": "What this task accomplishes",\n'
    '      "files": ["relative/path.py"],\n'
    '      "acceptance": ["Concrete observable condition"],\n'
    '      "validation": ["exact command where possible"],\n'
    '      "risk": "low"\n'
    "    }\n"
    "  ]\n"
    "}"
)


def build_planning_prompt(
    objective: str,
    *,
    task_id: str,
    workspace_path: str,
    repo: str | None = None,
) -> str:
    """Compose the planner turn that writes SPEC.md, PLAN.md and TASKS.json."""
    objective = (objective or "").strip() or "(no explicit objective given)"
    return (
        f"{PLANNER_SYSTEM}\n\n"
        f"## Task\n{objective}\n\n"
        "## Available context\n"
        f"- Task id: {task_id}\n"
        f"- Workspace path: {workspace_path}\n"
        f"- Repository: {repo or 'unknown'}\n\n"
        "## Required outputs\n"
        f"Write these files (create parent dirs; overwrite existing content):\n\n"
        f"1. `{A.SPEC_PATH}` — the human-readable contract, using exactly this "
        f"structure:\n\n{_SPEC_STRUCTURE}\n\n"
        f"2. `{A.PLAN_PATH}` — the engineering plan, using exactly this "
        f"structure:\n\n{_PLAN_STRUCTURE}\n\n"
        f"3. `{A.TASKS_PATH}` — a machine-readable task list (schema version 1):\n\n"
        f"{_TASKS_SCHEMA}\n\n"
        "Keep each task small and independently verifiable. Use repo-relative "
        "paths. Do not implement. When done, end your reply with a one-line "
        "confirmation that all three files were written."
    )


#: System preamble for the optional adversarial plan reviewer.
REVIEWER_SYSTEM = (
    "You are an adversarial plan reviewer. Your job is to prevent wasted "
    "autonomous work. Review the SPEC, PLAN and task list and block anything an "
    "autonomous generator could not execute or an independent evaluator could "
    "not verify."
)


def build_review_prompt() -> str:
    """Compose the reviewer turn that grades the drafted artifacts to JSON."""
    return (
        f"{REVIEWER_SYSTEM}\n\n"
        "## Inputs\n"
        f"- `{A.SPEC_PATH}`\n- `{A.PLAN_PATH}`\n- `{A.TASKS_PATH}`\n\n"
        "## Look for\n"
        "- ambiguous acceptance criteria\n- missing or weak validation\n"
        "- tasks that are too broad\n- incorrect file assumptions\n"
        "- risky migrations without rollback\n- hidden human decisions\n"
        "- places the evaluator could not verify completion\n\n"
        "## Required output\n"
        f"Write a single JSON object to `{A.PLAN_REVIEW_PATH}` with this shape:\n\n"
        "{\n"
        '  "version": 1,\n'
        '  "verdict": "pass|fail|needs_human",\n'
        '  "blocking_issues": ["..."],\n'
        '  "suggested_fixes": ["..."],\n'
        '  "risk_level": "low|medium|high",\n'
        f'  "reviewed_artifacts": ["{A.SPEC_PATH}", "{A.PLAN_PATH}", "{A.TASKS_PATH}"]\n'
        "}\n\n"
        "Use `fail` when the planner can revise without a human; use "
        "`needs_human` when a product or safety decision is required. Then end "
        "your reply with the same verdict JSON on its own line as a fallback. Do "
        "not implement."
    )


#: Prepended to the strict generator's prompts (opening + follow-ups). It points
#: the generator at the approved contract and forbids silent scope expansion.
GENERATOR_STRICT_PREAMBLE = (
    "This task runs under an approved plan. Before editing, read the approved "
    f"contract in `{A.SPEC_PATH}` and the plan in `{A.PLAN_PATH}`. Implement the "
    "plan exactly; keep changes within the approved scope. If you discover the "
    "approved plan is wrong, unsafe or insufficient, stop and write "
    f"`{A.PLAN_CHANGE_REQUEST_PATH}` explaining the failed assumption instead of "
    "silently changing scope."
)


def build_strict_evaluator_prompt(
    *,
    objective: str,
    generator_summary: str,
    verdict_path: str,
) -> str:
    """Compose the strict evaluator turn, grading against approved artifacts."""
    objective = (objective or "").strip() or "(no explicit objective given)"
    summary = (generator_summary or "").strip() or "(the agent provided no summary)"
    return (
        "You are an independent verifier. Assume the implementation is "
        "incomplete until proven otherwise, and verify evidence rather than "
        "trusting the agent's summary.\n\n"
        "## Inputs\n"
        f"- Objective: {objective}\n"
        f"- Agent summary: {summary}\n"
        f"- Approved contract: `{A.SPEC_PATH}`\n"
        f"- Approved plan: `{A.PLAN_PATH}`\n"
        f"- Task list: `{A.TASKS_PATH}`\n\n"
        "## Verify against\n"
        "- the acceptance criteria in the SPEC\n"
        "- the actual git diff / current workspace state\n"
        "- actual test/build/lint output you run yourself\n\n"
        "## Required output\n"
        f"Write `{A.EVIDENCE_PATH}` (schema version 1):\n\n"
        "{\n"
        '  "version": 1,\n'
        '  "verdict": "pass|fail|needs_human",\n'
        '  "score": 0.0,\n'
        '  "checked_tasks": ["T1"],\n'
        '  "commands": [{"cmd": "...", "exit_code": 0, "summary": "..."}],\n'
        '  "changed_files": ["..."],\n'
        '  "missing": ["..."],\n'
        '  "risks": ["..."]\n'
        "}\n\n"
        "A `pass` with failed commands is invalid unless the failures are "
        "explicitly non-blocking and explained under risks. `changed_files` "
        "should come from git diff, not memory. Use `needs_human` only when a "
        "person must decide or safe verification is impossible here.\n\n"
        "Then end your reply with a single line of JSON as a fallback: "
        '{"verdict": "pass|fail|needs_human", "score": 0.0, '
        '"missing": "short note"}'
    )
