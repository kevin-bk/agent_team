# Planning workflow

Last updated: 2026-07-05 · [↩ index](../index.md) · Source:
[`../../plans/planning-workflow-upgrade.md`](../../plans/planning-workflow-upgrade.md),
[`../../plans/planning-workflow-implementation-decisions.md`](../../plans/planning-workflow-implementation-decisions.md),
[`../../plans/loop-quality-and-self-improvement.md`](../../plans/loop-quality-and-self-improvement.md),
`features/board/runtime/loop/planning*.py`

Turning a rough task into an **approved, durable contract** before autonomous
execution.

## Why

A lightweight "planner writes `PLAN.md`, generator reads it" phase is cheap but
not enough for serious autonomous work: a wrong plan gets implemented without
review, the human never approves scope/non-goals/acceptance criteria, and the
evaluator grades a vague objective. The upgrade makes **planning a contract, not a
transcript** — explicit artifacts both the generator and evaluator read, gated by
human approval.

Guiding principles: the human owns intent; agents inspect the real workspace
before planning; every planned item is independently verifiable; the evaluator
verifies evidence, not the generator's summary; plan changes are first-class; and
**the existing chat path is never affected**.

## The artifact contract (`.agent-team/`)

Planning is an **artifact set**, not a single file. Source of truth for strict
mode:

| Artifact | Purpose |
|---|---|
| `SPEC.md` | Human + engineering contract: goal, original request, context, in-scope, non-goals, constraints, **acceptance criteria**, verification expectations, open questions, assumptions, risks. |
| `PLAN.md` | The technical approach: files/components, alternatives, steps, data/API/UI changes, verification plan, rollback, risks. |
| `TASKS.json` | Machine-readable task graph (schema v1). Advisory by default, but **executable** when a run opts into task-graph mode (`task_graph=True`). Validated by `validate_tasks` (unique ids, known deps, acyclic, known statuses) on edit. |
| `PLAN_REVIEW.json` | The adversarial reviewer's verdict (`pass`/`fail`/`needs_human` + blocking issues + risk level). |
| `EVIDENCE.json` | The evaluator's durable verification record (verdict, score, commands+exit codes, changed files, missing, risks). |
| `PLAN_CHANGE_REQUEST.md` | An **active marker**: execution discovered the approved plan is wrong/unsafe and paused. |
| `QUESTIONS.json` | Structured agent questions (see "Questions" below). |
| `INTAKE.json` | The planner's risk intake (input type + 11 risk flags + reasons). **Advisory** — never required for approval; the backend derives the process *lane* from it (see "Lanes are enforced" below). |
| `archive/` | Where resolved markers (e.g. change requests) are moved. |

Helpers live in `runtime/loop/planning_artifacts.py` — they resolve paths
**inside the workspace only** (reject path traversal), read/write, compute etags,
and parse/validate JSON. Prompts live in one module
`runtime/loop/planning_prompts.py`.

### Parsed contracts vs read-as-text guidance

Two of these artifacts behave very differently, and the difference drives where
their *format* is owned:

- **Backend-parsed contracts** (`TASKS.json`, `EVIDENCE.json`, `QUESTIONS.json`,
  `PLAN_REVIEW.json`, the `JOURNAL_NOTES.jsonl` note schema, `PLAN_CHANGE_REQUEST`).
  The backend reads these as structured data and gates on them, so their schemas
  are **hardcoded in the backend** and versioned. They are the wire protocol
  between the agents and the loop; letting them drift would silently break the
  parser or independent verification.
- **Read-as-text guidance** (`SPEC.md` / `PLAN.md`). The backend only checks they
  exist (`REQUIRED_FOR_APPROVAL`) and shows them in the cockpit — it does **not**
  parse their headings. Their *section structure* is therefore guidance, owned by
  the planner prompt and the optional **`project-harness` skill** (see below),
  not by the backend.

### Risk lanes — the planning ("harness") skill

`build_planning_prompt` no longer hardcodes the SPEC/PLAN section list. Instead it
**defers to a planning skill** — by default `project-harness` — when present: the
planner classifies the task's risk into a `quick` / `normal` / `risk` lane and
structures `SPEC.md` / `PLAN.md` to the matching depth. A built-in fallback
essence (cover goal, scope, acceptance, verification … in SPEC; approach,
alternatives, data/API, rollback … in PLAN) keeps boards **without** the skill
working. The default skill lives as a sibling plugin
(`community_plugins/project-harness/`); it rides on top of this contract and
never invents new files. Lane *enforcement* in the backend (e.g. requiring a
confirming `QUESTIONS.json` for a hard-gate task) is intentionally deferred — see
[`decisions.md`](../decisions.md) D14 and the [roadmap](../roadmap.md).

### Lanes are enforced, not just prompted

The planner writes `.agent-team/INTAKE.json` (required output #4 of the planner
prompt; the project-harness skill teaches the same file). The backend
**recomputes** the lane from the flags — it never trusts an agent-written
`lane` field — using the exact rule of the skill's `classify.py` (mirrored in
`planning_artifacts.classify_lane`, with a parity test): any hard-gate flag
(auth / authorization / data_model / secrets_config / audit_security /
external_systems) ⇒ `risk`; otherwise 0–1 flags ⇒ `quick`, 2–3 ⇒ `normal`,
4+ ⇒ `risk`. A missing or malformed intake means *no lane* and the workflow
behaves exactly as before lanes existed.

What the lane changes:

- **Journal + cockpit visibility**: an `intake`-phase journal entry records the
  lane (warning severity for `risk`), and the cockpit's review panel shows a
  lane badge with the hard gates on hover.
- **`risk` without a reviewer** logs a warning journal entry — the rigor the
  lane asks for is missing a leg; it surfaces rather than blocks.
- **`quick` + board opt-in ⇒ auto-approval**: when the board enables
  *Auto-approve quick-lane plans* (`planning_auto_approve_quick`, default off),
  a first-draft quick-lane plan whose reviewer (if any) said `pass` is stamped
  with a **system** approval (same artifact validation + etag pinning as a
  human click, `approved_by = system:quick-lane`) and parks at
  `plan_approved`. Re-drafts after a human requested changes or answered
  blocking questions are **never** auto-approved — once a human engaged, they
  get the final look. Execution start remains an explicit action.

### Per-board tuning: conventions, planning skill, repo templates

Each board can shape the **content** of the planning artifacts without forking
backend prompts. The artifact contract itself (paths, JSON schemas, lifecycle)
stays backend-owned and is never overridable.

- **`planning_conventions`** (Board settings → *Planning* → *Team conventions*):
  free-text house rules, injected into **every** strict phase — planner,
  reviewer, generator (whole-objective *and* per-task), evaluator — via
  `planning_prompts.conventions_block`. The block explicitly subordinates itself
  to the artifact contract, so a convention can style SPEC sections or raise the
  review bar but can never rename artifact files or change schemas.
- **`planning_skill`** (Board settings → *Planning* → *Planning skill*): the
  skill pack that owns the SPEC/PLAN structure guidance, replacing the default
  `project-harness`. The router validates it against known packs on save, the
  backend always materialises it into task workspaces (even when it isn't in the
  board's regular `skill_ids`), and `build_planning_prompt` points the planner
  at its workspace folder.
- **Repo-native templates**: the planner prompt also tells the agent to prefer
  conventions the repository itself ships (`CONTRIBUTING`, `docs/` spec/RFC/ADR
  templates, `AGENTS.md`) for the *content* of `SPEC.md`/`PLAN.md`.

Both board fields are loaded best-effort (`loop/service.py ::
_board_planning_settings`) — a missing board or DB hiccup degrades to the
defaults, never a failed run.

### How the planning skill actually reaches the agent

Two separate layers, easy to conflate:

1. **The prompt only *references* the skill** — `build_planning_prompt` writes
   "use the `<skill>` skill in this workspace (see `.claude/skills/<folder>/`)"
   plus a fallback essence. The skill's content is **never inlined** into the
   prompt.
2. **The files arrive via skill-pack materialisation**, not via the board's
   repository. Every run, `local_backend` calls `materialize_skills()`, which
   copies packs from the **skill_packs plugin catalog** (`<repo>-skill-packs/
   shared/` + git sources added in the UI) into the workspace's
   `.claude/skills/` and `.cursor/skills/`. A pack is copied when the board
   ticks it in *Skills* — or, for the planning skill, always (forced by
   `planning_skill`, P2).

Consequence: `community_plugins/project-harness/` is only the pack's **source
code** living in this repo — it is *not* automatically in the catalog. An
operator must import it as a skill pack (copy into the packs root or add as a
git source); until then, planners silently run on the fallback essence and the
workspace has no `.claude/skills/` folder. Lane enforcement (INTAKE.json) still
works either way because the planner prompt asks for the intake directly.

The board's *repository* plays no part in skill delivery — it is checked out
into the workspace for the agent to work on, and the planner is merely told to
prefer repo-native templates (P3 above) for artifact content.

## The lifecycle (mapped onto `loop_state`)

```
intake → planning ──► waiting_plan_approval ──(approve)──► plan_approved ──► running ──► verification ──► complete
                            ▲   │ (request changes / edit invalidates approval)
                            └───┘
running ──► plan_change_requested   (approved plan turned out wrong; resolve + re-approve)
running ──► waiting_answers          (blocking questions; human answers, re-plan)
```

There is **one** public lifecycle (`task.loop_state`); planning detail is
metadata, not a competing state machine.

## Non-negotiable decisions (the "why")

These came out of an explicit decisions doc and shape the implementation:

1. **`task.loop_state` is canonical.** No second top-level planning state machine.
2. **Planning and execution are separate jobs.** No background loop / DB
   connection / ACP session / async task is kept alive while waiting for human
   approval — `planning/start` runs, writes artifacts, and **stops**.
3. **Evaluator/backend owns completion.** The generator never authoritatively
   marks work complete.
4. **Task-graph execution is opt-in, not the default.** `TASKS.json` is advisory
   for whole-objective runs; a run can opt into deterministic task-graph
   scheduling (`task_graph=True`, see [`autonomous-loop.md`](autonomous-loop.md)).
5. **v1 contracts (artifact paths, API names, modes, approval metadata, expanded
   `LoopState`, prompt module) are stable** — v2/v3 extend them, never replace.
6. **Strict planning must not fail open** to raw-objective execution.
7. **Approval pins artifact checksums.** Execution must know which etags were
   approved; editing an approved artifact **invalidates approval**.

## Modes

- `legacy_plan` (`PLANNING_MODE_LEGACY`) — the original behaviour: planner writes
  `PLAN.md`; a missing plan fails open to the raw objective. This is the model
  **default** for a task's `planning_mode`.
- `strict_plan` (`PLANNING_MODE_STRICT`) — requires `SPEC.md` + `PLAN.md` (+
  `TASKS.json`) and human approval before execution; missing/invalid artifacts
  route to human review.

> Note: the `POST /tasks/{id}/planning/start` endpoint currently always sets
> `strict_plan` — the strict flow *is* the planning UX. `legacy_plan` remains the
> default state for tasks that were never planned and for the lightweight
> in-loop planner (`runtime/loop/planner.py`).

## API (v1)

```
POST /tasks/{id}/planning/start            # plan, then stop at waiting_plan_approval
GET  /tasks/{id}/planning                  # status + artifact text/JSON + approval metadata
POST /tasks/{id}/planning/approve          # validate + stamp approval (no execution)
POST /tasks/{id}/planning/request-changes  # store feedback, back to draft
POST /tasks/{id}/planning/approve-and-run  # approve + start execution
PUT  /tasks/{id}/planning/artifacts/{name} # edit (If-Match etag guard)
POST /tasks/{id}/planning/answer           # answer questions (see below)
```

Artifact edits use an **etag/`If-Match` guard** so a human edit and an agent write
can't silently clobber each other. While `loop_state = planning` the UI is
read-only; while `waiting_plan_approval` the agent must not write.

## Phase tags in prompts

Each phase's prompt begins with an explicit banner (`PHASE: PLAN` / `REVIEW` /
`IMPLEMENT` / `VERIFY`) so the agent (and anyone debugging) always knows which
phase is active. Injected in `planning_prompts.py`.

## Questions (the simplified UX)

Agents emit questions to `.agent-team/QUESTIONS.json` with a `blocking` flag:

- **Blocking** questions pause the phase → `loop_state = waiting_answers`; the
  cockpit shows answer cards. The chosen UX: the human writes **one free-text
  reply** that the backend maps to all currently-unanswered questions (plus a
  general note) — no per-question prefixes needed.
- **Non-blocking** questions never pause (the agent picked a safe default). They
  are surfaced **read-only** in the "Plan ready for review" panel
  (`PlanningPanel.tsx :: NotedQuestions`) so a human can catch a wrong assumption
  and "Request changes" instead of discovering it after execution.

## UI

`web-ui/src/features/board/cockpit/PlanningPanel.tsx` renders the states: no
plan, planning running, spec/plan/tasks tabs, review failed, waiting for approval,
approved, executing, plan-change-requested, complete. Tabs for Spec/Plan/Tasks/
Review/Evidence; risky actions are explicit.

## Related

- The loop that executes the approved plan → [`autonomous-loop.md`](autonomous-loop.md)
- What gets recorded along the way → [`task-journal.md`](task-journal.md)
- Future phases → [`../roadmap.md`](../roadmap.md)
