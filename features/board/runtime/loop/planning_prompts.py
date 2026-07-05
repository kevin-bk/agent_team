"""Prompt templates for the strict (contract-driven) planning workflow.

These are intentionally strict and file-based: a planner researches the
workspace and writes durable artifacts, an optional reviewer grades them, and
the strict generator/evaluator both work from the approved artifacts rather than
the raw objective. Keeping every prompt here (instead of scattered in service
code) makes them easy to version and test.

The artifact *contract* (paths, JSON schemas, phase rules) is fixed, but the
*guidance* layer is per-board tunable: a board's ``planning_conventions`` text
is injected into every phase via :func:`conventions_block`, and its
``planning_skill`` replaces the default ``project-harness`` pack as the owner of
the SPEC/PLAN section structure.
"""

from __future__ import annotations

from agent_team.features.board.runtime.loop import planning_artifacts as A

#: Explicit phase banner placed as the FIRST line of every loop prompt so the
#: agent (and anyone reading a transcript) can tell at a glance which lifecycle
#: phase it is acting in. Kept short and stable: ``PHASE: <NAME>``.
PHASE_PLAN = "PHASE: PLAN"
PHASE_REVIEW = "PHASE: REVIEW"
PHASE_IMPLEMENT = "PHASE: IMPLEMENT"
PHASE_VERIFY = "PHASE: VERIFY"

#: Shared escape hatch: rather than guessing a materially-impacting decision, an
#: agent writes structured blocking questions for the human and stops. Reused by
#: the planner and the strict/task-graph generator preambles.
ASK_QUESTIONS_INSTRUCTION = (
    "If a decision would materially change the work and you cannot pick a safe "
    f"default, do NOT guess. Write `{A.QUESTIONS_PATH}` (schema version 1) and "
    "then END YOUR TURN IMMEDIATELY — do not make any further edits or run other "
    "steps in this turn; wait for the human's answer before doing anything "
    "else:\n\n"
    "{\n"
    '  "version": 1,\n'
    '  "questions": [\n'
    "    {\n"
    '      "id": "Q1",\n'
    '      "question": "Concise decision you need from the human",\n'
    '      "reason": "Why it blocks/changes the work",\n'
    '      "blocking": true,\n'
    '      "options": ["Option A", "Option B"]\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Offer concrete `options` when there is a small set of sensible choices "
    "(the human can always answer with something else). Only ask questions whose "
    "answers materially change the implementation; prefer a stated assumption for "
    "low-impact gaps."
)

#: Skill pack the planner defers to for SPEC/PLAN structure when the board does
#: not choose its own (see ``AgentTeamBoard.planning_skill``).
DEFAULT_HARNESS_SKILL = "project-harness"


def _skill_folder(name: str) -> str:
    """Workspace folder for a skill pack name (mirrors ``skills._safe_dir_name``)."""
    return name.replace("/", "-").replace("\\", "-").strip("-") or "skill"


def conventions_block(conventions: str | None) -> str:
    """Render a board's planning conventions as a prompt section (`""` = none).

    This is the per-team escape hatch: humans write their own best practices
    (section styles, review bars, house rules) once on the board and every
    strict-planning phase sees them. The block explicitly subordinates itself to
    the artifact contract so a convention can never rename the artifact files or
    change the JSON schemas the backend parses.
    """
    text = (conventions or "").strip()
    if not text:
        return ""
    return (
        "## Team conventions (set by this board's humans)\n"
        "Follow these conventions for HOW you write and structure your work — "
        "they express this team's best practices and take precedence over "
        "generic style guidance. They do NOT override the required artifact "
        "file paths, JSON schemas, phase rules, or safety rules stated "
        "elsewhere in this prompt.\n\n"
        f"{text}"
    )


def _with_conventions(prompt: str, conventions: str | None) -> str:
    """Append the conventions section to a prompt when the board set one."""
    block = conventions_block(conventions)
    return f"{prompt}\n\n{block}" if block else prompt


#: Standing discipline: agents append meaningful moments to the journal inbox as
#: they work. This is a durable record (it survives the agent's own context
#: compaction) that a human or a later session can read back, so keep entries
#: short and decision-grade — not a verbose log. Reused across planner/generator/
#: evaluator prompts.
JOURNAL_DISCIPLINE = (
    f"This task keeps a durable journal. If `{A.JOURNAL_FILE_PATH}` exists, read "
    "it first for the prior decisions, assumptions and risks recorded so far, and "
    "do not contradict or re-decide them without a stated reason. "
    "Then, whenever you make a notable decision, rely on a key assumption, hit a "
    "risk, or change direction, append ONE JSON object per line to "
    f"`{A.JOURNAL_NOTES_PATH}` (JSONL — append only, never rewrite the file): "
    '{"type": "decision|assumption|risk|note", "title": "short summary", '
    '"body": "why / detail", "severity": "info|warning|blocking"}. '
    "Keep it concise and meaningful; do not log routine steps and never put "
    "secrets in a note."
)

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

#: Lightweight fallback structure for SPEC.md / PLAN.md, used only when the
#: ``project-harness`` skill is not available in the workspace. The skill owns
#: the authoritative, risk-graded section structure (the backend only reads
#: these files as text and shows them — it does not parse their headings), so we
#: keep just a one-line essence here instead of a rigid section list.
_SPEC_FALLBACK = (
    "goal, original request, scope/non-goals, acceptance criteria, verification "
    "expectations, assumptions and risks"
)
_PLAN_FALLBACK = (
    "approach, alternatives considered, files/components, implementation steps, "
    "data and API changes, verification plan, and rollback/recovery"
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

#: Risk-intake shape the planner writes so the backend can derive the lane
#: (mirrors ``planning_artifacts.RISK_FLAGS`` / the project-harness skill).
_INTAKE_SCHEMA = (
    "{\n"
    '  "input_type": "feature|change|bugfix|maintenance|initiative",\n'
    '  "flags": {\n'
    '    "auth": false, "authorization": false, "data_model": false,\n'
    '    "secrets_config": false, "audit_security": false,\n'
    '    "external_systems": false, "public_contracts": false,\n'
    '    "cross_platform": false, "existing_behavior": false,\n'
    '    "weak_proof": false, "multi_domain": false\n'
    "  },\n"
    '  "reasons": {"<flag>": "one line why that flag is true"}\n'
    "}"
)


def build_planning_prompt(
    objective: str,
    *,
    task_id: str,
    workspace_path: str,
    repo: str | None = None,
    conventions: str | None = None,
    harness_skill: str | None = None,
) -> str:
    """Compose the planner turn that writes SPEC.md, PLAN.md and TASKS.json.

    ``conventions`` (the board's planning house rules) and ``harness_skill``
    (the board's structure-guidance pack, default ``project-harness``) let each
    team shape the artifact *content* without changing the artifact contract.
    """
    objective = (objective or "").strip() or "(no explicit objective given)"
    skill = (harness_skill or "").strip() or DEFAULT_HARNESS_SKILL
    folder = _skill_folder(skill)
    conv = conventions_block(conventions)
    conv_section = f"{conv}\n\n" if conv else ""
    return (
        f"{PHASE_PLAN}\n\n"
        f"{PLANNER_SYSTEM}\n\n"
        f"## Task\n{objective}\n\n"
        "## Available context\n"
        f"- Task id: {task_id}\n"
        f"- Workspace path: {workspace_path}\n"
        f"- Repository: {repo or 'unknown'}\n\n"
        "## Required outputs\n"
        f"Write these files (create parent dirs; overwrite existing content):\n\n"
        f"1. `{A.SPEC_PATH}` — the human-readable contract.\n"
        f"2. `{A.PLAN_PATH}` — the engineering plan.\n"
        f"3. `{A.TASKS_PATH}` — a machine-readable task list (schema version 1):\n\n"
        f"{_TASKS_SCHEMA}\n\n"
        f"4. `{A.INTAKE_PATH}` — an honest risk intake. Mark each flag true/false "
        "with a one-line reason for every true flag:\n\n"
        f"{_INTAKE_SCHEMA}\n\n"
        "The backend derives the task's process lane (quick/normal/risk) from "
        "these flags, so never understate risk to speed things up — flags like "
        "auth, data_model or secrets_config force the careful lane by design.\n\n"
        f"Use the `{skill}` skill in this workspace (see the skills "
        f"manifest / `.claude/skills/{folder}/`) to classify the task's risk "
        "and structure `SPEC.md` and `PLAN.md` to the right depth and sections. If "
        f"that skill is unavailable, still cover {_SPEC_FALLBACK} in `SPEC.md` and "
        f"{_PLAN_FALLBACK} in `PLAN.md`.\n\n"
        "If the repository itself ships engineering conventions or document "
        "templates (e.g. `CONTRIBUTING`, `docs/` spec/RFC/ADR templates, an "
        "`AGENTS.md` with house rules), prefer and follow those formats for the "
        "CONTENT of `SPEC.md`/`PLAN.md` — the file paths above stay as required.\n\n"
        "Keep each task small and independently verifiable. Use repo-relative "
        "paths. Do not implement. When done, end your reply with a one-line "
        "confirmation that all three files were written.\n\n"
        f"{conv_section}"
        f"## Journal\n{JOURNAL_DISCIPLINE}\n\n"
        f"## When you are blocked\n{ASK_QUESTIONS_INSTRUCTION}"
    )


#: System preamble for the optional adversarial plan reviewer.
REVIEWER_SYSTEM = (
    "You are an adversarial plan reviewer. Your job is to prevent wasted "
    "autonomous work. Review the SPEC, PLAN and task list and block anything an "
    "autonomous generator could not execute or an independent evaluator could "
    "not verify."
)


def build_review_prompt(*, conventions: str | None = None) -> str:
    """Compose the reviewer turn that grades the drafted artifacts to JSON.

    ``conventions`` lets the reviewer grade against the board's own house rules
    too (a plan that ignores the team's stated practices is a legitimate issue).
    """
    conv = conventions_block(conventions)
    conv_section = f"\n\n{conv}" if conv else ""
    return (
        f"{PHASE_REVIEW}\n\n"
        f"{REVIEWER_SYSTEM}{conv_section}\n\n"
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
    f"{PHASE_IMPLEMENT}\n\n"
    "This task runs under an approved plan. Before editing, read the approved "
    f"contract in `{A.SPEC_PATH}` and the plan in `{A.PLAN_PATH}`. Implement the "
    "plan exactly; keep changes within the approved scope. If you discover the "
    "approved plan is wrong, unsafe or insufficient, write "
    f"`{A.PLAN_CHANGE_REQUEST_PATH}` explaining the failed assumption instead of "
    "silently changing scope. If instead you only need a decision from the human "
    f"to proceed, write `{A.QUESTIONS_PATH}` with blocking questions "
    "(schema version 1: a list of {id, question, reason, blocking, options}) "
    "rather than guessing. In either case, END YOUR TURN IMMEDIATELY after "
    "writing the file — do not make further edits or run other steps in this "
    f"turn; wait for the human.\n\n{JOURNAL_DISCIPLINE}"
)


def strict_generator_preamble(*, conventions: str | None = None) -> str:
    """The strict generator preamble, plus the board's conventions when set."""
    return _with_conventions(GENERATOR_STRICT_PREAMBLE, conventions)


#: Resume note injected into the generator preamble when execution restarts
#: *after a plan change* (the human revised the contract in response to the
#: agent's change request). Makes the revision explicit so the agent re-reads
#: the updated plan instead of re-filing the same change request.
PLAN_REVISED_NOTE = (
    "The approved plan was revised by a human in response to your earlier change "
    f"request. The contract has changed — re-read `{A.SPEC_PATH}` and "
    f"`{A.PLAN_PATH}` and proceed with the updated plan. Do not re-file the same "
    "change request unless a genuinely new blocking problem remains."
)


def build_resume_preamble(reason: str | None = None) -> str:
    """A short note that re-grounds a resumed loop in the durable artifacts.

    A resumed loop must continue from where it stopped, not restart. Rather than
    resending the whole original objective (wasteful, and it invites redoing
    finished work), we point the agent at the on-disk source of truth — the
    approved contract/plan and the live task list with its per-task status — and
    tell it to pick up the first unfinished task. This is what makes a resume
    work even when the loop is handed to a *different* agent than the one that
    started it (e.g. swapping off a rate-limited engine): the files carry the
    context the new agent's fresh conversation does not have.
    """
    why = f" The previous run stopped because: {reason.strip()}." if reason else ""
    return (
        "RESUMING a previously-started run — continue from where it stopped, do "
        f"NOT start over.{why}\n\n"
        f"Re-read the approved contract `{A.SPEC_PATH}` and plan `{A.PLAN_PATH}` "
        f"for what must be done, and inspect `{A.TASKS_PATH}` for progress: every "
        "task already marked `complete` is DONE — do not redo it. Inspect the "
        "real current state of the workspace (do not rely on memory), then "
        "continue with the first unfinished task and drive the objective to a "
        "verified completion."
    )


#: Standing instruction prepended to every per-task generator turn in task-graph
#: execution. Scopes the agent to the single current task and keeps the same
#: change-request escape hatch as whole-objective strict mode.
TASK_GRAPH_PREAMBLE = (
    f"{PHASE_IMPLEMENT}\n\n"
    "You are executing ONE task of an approved plan. Read the approved contract "
    f"`{A.SPEC_PATH}` and plan `{A.PLAN_PATH}` for context, but implement ONLY "
    "the current task described above — do not start other tasks or expand "
    "scope. If the approved plan is wrong, unsafe or insufficient for this task, "
    f"write `{A.PLAN_CHANGE_REQUEST_PATH}` explaining the problem "
    "instead of silently changing scope. If you only need a decision from the "
    f"human to proceed, write `{A.QUESTIONS_PATH}` with blocking "
    "questions (schema version 1) rather than guessing. In either case, END "
    "YOUR TURN IMMEDIATELY after writing the file — do not make further edits "
    f"or run other steps in this turn; wait for the human.\n\n{JOURNAL_DISCIPLINE}"
)


def task_graph_preamble(*, conventions: str | None = None) -> str:
    """The per-task generator preamble, plus the board's conventions when set."""
    return _with_conventions(TASK_GRAPH_PREAMBLE, conventions)


def _bullets(items: list[str]) -> str:
    """Render a list as markdown bullets, or a placeholder when empty."""
    cleaned = [i.strip() for i in items if i and i.strip()]
    return "\n".join(f"- {i}" for i in cleaned) if cleaned else "- (none specified)"


def build_task_objective(task: dict) -> str:
    """Compose the per-task objective the generator loop works against.

    Carries the single task's contract (objective, files, acceptance, validation)
    so the generator focuses on exactly this unit of the plan.
    """
    tid = str(task.get("id") or "task")
    title = str(task.get("title") or tid)
    objective = str(task.get("objective") or "").strip() or "(see acceptance criteria)"
    files = [str(f) for f in (task.get("files") or [])]
    acceptance = [str(a) for a in (task.get("acceptance") or [])]
    validation = [str(v) for v in (task.get("validation") or [])]
    files_line = ", ".join(files) if files else "(discover from the plan)"
    return (
        f"## Current task: {tid} — {title}\n"
        f"{objective}\n\n"
        f"Files likely involved: {files_line}\n\n"
        f"Acceptance criteria (this task is done only when all hold):\n"
        f"{_bullets(acceptance)}\n\n"
        f"Validation to run:\n{_bullets(validation)}"
    )


def build_task_evaluator_prompt(
    *,
    task: dict,
    generator_summary: str,
    verdict_path: str,
    conventions: str | None = None,
) -> str:
    """Compose the evaluator turn that grades ONE task against its acceptance."""
    tid = str(task.get("id") or "task")
    title = str(task.get("title") or tid)
    summary = (generator_summary or "").strip() or "(the agent provided no summary)"
    acceptance = [str(a) for a in (task.get("acceptance") or [])]
    validation = [str(v) for v in (task.get("validation") or [])]
    return (
        f"{PHASE_VERIFY}\n\n"
        "You are an independent verifier grading a SINGLE task of an approved "
        "plan. Assume it is incomplete until proven otherwise, and verify "
        "evidence rather than trusting the agent's summary.\n\n"
        "## Task under review\n"
        f"- Id: {tid}\n- Title: {title}\n"
        f"- Agent summary: {summary}\n"
        f"- Approved contract: `{A.SPEC_PATH}` · plan: `{A.PLAN_PATH}`\n\n"
        "## Acceptance criteria for THIS task\n"
        f"{_bullets(acceptance)}\n\n"
        "## Validation to run yourself\n"
        f"{_bullets(validation)}\n\n"
        "## Verify against\n"
        "- only this task's acceptance criteria (ignore other tasks)\n"
        "- the actual git diff / current workspace state\n"
        "- actual test/build/lint output you run yourself\n"
        f"- any `{A.CLARIFICATIONS_HEADING}` section in `{A.SPEC_PATH}` is "
        "human-approved scope — honour it, do not penalise the agent for "
        "following it\n\n"
        "## Required output\n"
        f"Write `{verdict_path}` (schema version 1):\n\n"
        "{\n"
        '  "version": 1,\n'
        '  "verdict": "pass|fail|needs_human",\n'
        '  "score": 0.0,\n'
        f'  "checked_tasks": ["{tid}"],\n'
        '  "commands": [{"cmd": "...", "exit_code": 0, "summary": "..."}],\n'
        '  "changed_files": ["..."],\n'
        '  "missing": ["..."],\n'
        '  "risks": ["..."]\n'
        "}\n\n"
        "Use `pass` only when every acceptance criterion for this task is "
        "provably met. A `pass` with failed commands is invalid unless the "
        "failures are explicitly non-blocking and explained under risks. Use "
        "`needs_human` only when a person must decide. Then end your reply with "
        'a single line of JSON as a fallback: {"verdict": "pass|fail|'
        'needs_human", "score": 0.0, "missing": "short note"}\n\n'
        f"{_conv_section(conventions)}"
        f"## Journal\n{JOURNAL_DISCIPLINE}"
    )


def _conv_section(conventions: str | None) -> str:
    """The conventions block as a trailing prompt section (`""` when unset)."""
    block = conventions_block(conventions)
    return f"{block}\n\n" if block else ""


def build_answers_addendum(answered: list[dict], note: str | None = None) -> str:
    """Render answered questions (+ an optional human note) as a prompt block.

    Injected into the re-plan (planner) or resume (generator) prompt so the
    agent proceeds with the human's decisions instead of re-asking. ``answered``
    rows are the normalised question dicts with a non-empty ``answer``.
    """
    lines: list[str] = ["## Human answers to your questions"]
    for q in answered:
        ans = str(q.get("answer") or "").strip()
        if not ans:
            continue
        lines.append(f"- Q ({q.get('id')}): {q.get('question')}")
        lines.append(f"  A: {ans}")
    note = (note or "").strip()
    if note:
        lines.append(f"\nAdditional note from the human:\n{note}")
    lines.append(
        "\nProceed using these decisions. Do not re-ask answered questions."
    )
    return "\n".join(lines)


def build_strict_evaluator_prompt(
    *,
    objective: str,
    generator_summary: str,
    verdict_path: str,
    conventions: str | None = None,
) -> str:
    """Compose the strict evaluator turn, grading against approved artifacts."""
    objective = (objective or "").strip() or "(no explicit objective given)"
    summary = (generator_summary or "").strip() or "(the agent provided no summary)"
    return (
        f"{PHASE_VERIFY}\n\n"
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
        "- actual test/build/lint output you run yourself\n"
        f"- any `{A.CLARIFICATIONS_HEADING}` section in `{A.SPEC_PATH}` is "
        "human-approved scope — honour it, do not penalise the agent for "
        "following it\n\n"
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
        '"missing": "short note"}\n\n'
        f"{_conv_section(conventions)}"
        f"## Journal\n{JOURNAL_DISCIPLINE}"
    )
