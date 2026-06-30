# Task Journal

Last updated: 2026-06-30 · [↩ index](../index.md) · Source:
[`../../plans/task-journal-plan.md`](../../plans/task-journal-plan.md) · Status:
**implemented (slices 1–3 + recap injection + friction page)**

A durable, append-only **semantic timeline** of the decisions, assumptions,
questions, approvals, plan changes, and verdicts across a task.

## Why

The system already has many durable records — run events, planning artifacts,
`loop_state`, board activity, the `LoopLedger`. None of them answer, for a human
opening the task tomorrow or a resumed agent: *"why did this task go the way it
did?"* Run events are raw replay; artifacts are the contract; the ledger is
accounting. The **Journal** is the curated middle layer: the meaningful points
someone needs to understand the task's history. It **links to** those other
sources via references rather than replacing them.

## Principles

- **Append-only.** Don't edit old entries; if one is wrong, append a `correction`
  with `supersedes`.
- **Semantic, not a transcript.** No full prompts, long stdout, or raw logs.
- **Backend is the authority.** Agents *suggest* notes; the backend validates,
  normalises, de-dupes, and masks secrets before accepting them.
- **Best-effort.** A journal append failure must never break planning/execution.

## Entry model (`AgentTeamJournalEntry`)

`task_id` + task-local monotonic `seq`, `actor_type` (human/agent/system),
`actor_id`, `phase`, `type`, `title`, `body`, `severity` (info/warning/blocking),
`refs_json`, `metadata_json`, `supersedes_id`, `created_at`.

- **Types:** `decision`, `assumption`, `question`, `answer`, `approval`,
  `plan_review`, `plan_change`, `verdict`, `state_change`, `risk`, `friction`,
  `note`, `artifact_update`, `task_progress`, `summary`, `correction`.
- **Phases:** `intake`, `planning`, `review`, `approval`, `execution`,
  `verification`, `change_request`, `result`, `system`.

## Modules

- `features/board/repositories/journal.py` — `append_entry` (task-local `seq`),
  `list_entries` (filter/paginate), `serialize_entry`.
- `features/board/runtime/task_journal.py` — best-effort `record` helpers,
  `ingest_agent_notes`, and `write_journal_file`.

## How agents contribute (file inbox)

Because direct-CLI agents don't get LangChain tools, the mechanism is
**file-based and universal**:

1. The agent appends one JSON object per line to
   `.agent-team/JOURNAL_NOTES.jsonl` (prompt discipline `JOURNAL_DISCIPLINE` is
   injected into planner/generator/evaluator prompts — *not* `TASK.md`, since chat
   runs don't ingest).
2. After every planner/reviewer/generator/evaluator turn, `ingest_agent_notes`
   reads the inbox → **archives it immediately** (no double-ingest) → masks the
   agent's MCP secrets → de-dupes the batch → appends accepted lines as `agent`
   entries.

System-authored entries are wired at ~15 lifecycle points (planning start,
artifact edit, approve, request-changes, approve-and-run, answer, generator
plan-change/questions, evaluator verdict, task started/complete/blocked, final
verify, terminal outcome).

## Friction (self-improvement signal)

A **`friction`** entry records that work was *harder than it should have been* —
missing tests/fixtures, stale docs, ambiguous scope, a repeated manual step, or a
task the loop could not verify. It is a defect in the **environment/process**
(which slows the *next* task too), not a bug in the current task's product.

- **Sources.** Agents log them via the note inbox (`{"type":"friction", …}`,
  guided by the `project-harness` skill). The loop also **auto-emits** one when a
  task ends without a verified pass: `driver._finish` on `capped`/`budget`
  (carrying the last evaluator's evidence digest) and the task-graph blocked
  branch.
- **Board-level rollup.** `journal.list_board_friction(board_id)` joins each
  entry to its task (the journal is otherwise per-task) so friction can be listed
  across the whole board, newest first.
- **API & UI.** `GET /boards/{id}/frictions` →
  `web-ui/.../board/FrictionPanel.tsx`, a read-only **Friction** tab on the board.
- **Deliberately simple.** No automatic grouping, prioritisation, or card
  creation — a human reviews the list and turns the recurring ones into a fix (see
  [`decisions.md`](../decisions.md) D15).

## Durable memory (read-back)

Instead of inlining the whole journal in every prompt, the backend **mirrors the
full journal to a workspace file**: `task_journal.write_journal_file` renders all
entries to `.agent-team/JOURNAL.md` (regenerated before each generator turn), and
the prompt carries a light pointer telling the agent to read that file first for
prior decisions/assumptions/risks. Small prompts, complete history on demand, any
file-capable agent can read it, the agent never queries the DB.

## API & UI

- `GET /tasks/{id}/journal` (filter by type/phase/severity, paginate) ·
  `POST /tasks/{id}/journal` (human/editor manual note).
- `GET /boards/{id}/frictions` — board-wide `friction` rollup for the Friction tab.
- `web-ui/.../cockpit/JournalPanel.tsx` — a timeline (actor icon, severity rail,
  type/phase chips, ref chips) + filters + a manual-note composer; invalidated on
  `loop.status` SSE events. `web-ui/.../board/FrictionPanel.tsx` — the board-level
  Friction tab.

## Related

- The lifecycle that produces most entries → [`autonomous-loop.md`](autonomous-loop.md)
- The artifacts entries reference → [`planning-workflow.md`](planning-workflow.md)
