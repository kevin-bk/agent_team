# Task Journal for agent_team

Status: **Implemented (Slices 1–3 + recap injection).**
Audience: coding agents implementing durable task memory and auditability in
`community_plugins/agent_team`.

This document specifies the **Task Journal** feature: an append-only semantic
timeline for important decisions, assumptions, questions, approvals, plan
changes, verification outcomes, and human/agent notes across a task.

## Implementation status (what shipped)

- **Slice 1 — Backend core.** `db_migrations/024_task_journal.sql`
  (`plugin_agent_team_journal_entry`), `AgentTeamJournalEntry` model + constants,
  `repositories/journal.py` (`append_entry` with task-local monotonic `seq`,
  `list_entries` filter/paginate, `serialize_entry`), best-effort
  `runtime/task_journal.py` (`record` / `record_with` / `refs`), DTOs
  (`JournalEntryDTO`, `JournalEntryCreate`), and `GET`/`POST
  /tasks/{id}/journal`. System-authored entries are wired at ~15 lifecycle
  points across the router, planning job, loop driver, and task-graph
  orchestrator (planning start, artifact edit, approve, request-changes,
  approve-and-run, answer, generator plan-change/questions, evaluator verdict,
  task started/complete/blocked, final verify, terminal outcome).
- **Slice 2 — Agent note inbox.** `.agent-team/JOURNAL_NOTES.jsonl` (append-only
  JSONL) with `read_journal_notes` / `archive_journal_notes` in
  `planning_artifacts.py`; `task_journal.ingest_agent_notes` reads → archives
  immediately (no double-ingest) → masks the agent's MCP secrets → de-dupes a
  batch → appends as `agent` entries. Ingest runs after every planner, reviewer,
  generator, and evaluator turn. Prompt discipline (`JOURNAL_DISCIPLINE`) is
  injected into the planner/generator/evaluator prompts (not `TASK.md`, since
  chat runs do not ingest).
- **Slice 3 — UI.** `cockpit/JournalPanel.tsx` — a `Journal` thread with a
  timeline (actor icon, severity rail, type/phase chips, reference chips) +
  type/phase/severity filters + a manual human-note composer. Wired into
  `TaskCockpit` and invalidated on `loop.status` SSE events.
- **Durable memory (read-back).** Instead of inlining the whole journal in every
  prompt, the backend **mirrors the full journal to a workspace file** that the
  agent reads on demand. `task_journal.write_journal_file` renders all entries to
  `.agent-team/JOURNAL.md` (regenerated before each generator turn by
  `BackendGenerator`), and `JOURNAL_DISCIPLINE` carries a light pointer telling
  the agent to read that file first for prior decisions/assumptions/risks. This
  keeps prompts small while giving the agent the complete decision history when
  it needs it; any agent with file tools (LLM or direct-CLI) can open it. The
  agent never queries the DB.

## 0. Naming decision

Use **Task Journal** as the feature name.

Recommended labels:

- UI section: `Journal`
- UI subtitle: `Decisions & notes`
- Internal module: `task_journal.py`
- Model/table: `AgentTeamJournalEntry`
- API prefix: `/tasks/{task_id}/journal`
- Optional workspace mirror: `.agent-team/JOURNAL.jsonl`
- Agent note inbox: `.agent-team/JOURNAL_NOTES.jsonl`

Avoid using `Ledger` as the primary code name. The loop runtime already has
`LoopLedger` in `runtime/loop/budget.py` for token/cost/runtime accounting, so a
generic `Ledger` name would be ambiguous. The product idea is still a "sổ cái",
but the product/code term should be `Journal`.

## 1. Why this exists

The current system already has several durable records:

- Run events in `runtime/event_store.py`: raw per-run stream frames for replay
  and SSE.
- Goal/Activity transcript: what each planner/generator/evaluator said or did.
- Planning artifacts in `.agent-team/`: `SPEC.md`, `PLAN.md`, `TASKS.json`,
  `PLAN_REVIEW.json`, `QUESTIONS.json`, `PLAN_CHANGE_REQUEST.md`,
  `EVIDENCE.json`.
- `task.loop_state`: the canonical public task lifecycle.
- `AgentTeamActivity`: board changelog entries.
- `LoopLedger`: resource accounting for budget guardrails.

Task Journal is different. It is a curated semantic timeline. It records the
meaningful points a future human or agent needs to understand why the task went
the way it did.

Do not use the journal as a replacement for:

- `task.loop_state` lifecycle
- planning artifacts as approved contract
- `EVIDENCE.json` as verification record
- run events/transcripts as raw replay source
- `LoopLedger` as resource accounting

The journal should link to those sources through references.

## 2. Core principles

- Append-only by default. Do not edit old entries except for admin cleanup.
- If an entry is wrong, append a `correction` entry with `supersedes`.
- Keep entries concise and semantic.
- Do not dump full prompts, long stdout, full test logs, or raw transcripts.
- Backend is the authority that writes accepted journal entries.
- Agents may suggest notes, but backend validates and normalises before the
  journal accepts them.
- Journal entries should be useful for:
  - a human opening the task tomorrow
  - a resumed agent
  - a summary/retrospective agent
  - PR description generation
  - debugging stuck loops

## 3. Entry model

Add a DB table/model:

```text
AgentTeamJournalEntry
- id: string primary key
- task_id: string, indexed, FK plugin_agent_team_task.id cascade delete
- seq: integer, task-local monotonic sequence
- actor_type: string  # human | agent | system
- actor_id: string nullable
- phase: string
- type: string
- title: string
- body: text
- severity: string  # info | warning | blocking
- refs_json: text   # JSON object
- metadata_json: text # JSON object
- supersedes_id: string nullable
- created_at: datetime
```

Use task-local `seq` so the journal can paginate and render in stable order.
Allocate `seq` by locking the latest row for a task or by a task-level counter.
If this is too much for the first migration, order by `(created_at, id)` and add
`seq` in a follow-up, but `seq` is preferred.

Recommended constants:

```python
JOURNAL_ACTOR_HUMAN = "human"
JOURNAL_ACTOR_AGENT = "agent"
JOURNAL_ACTOR_SYSTEM = "system"

JOURNAL_SEVERITY_INFO = "info"
JOURNAL_SEVERITY_WARNING = "warning"
JOURNAL_SEVERITY_BLOCKING = "blocking"
```

### 3.1 Entry types

Controlled `type` values:

```text
decision
assumption
question
answer
approval
plan_review
plan_change
verdict
state_change
risk
note
artifact_update
task_progress
summary
correction
```

### 3.2 Phase values

Controlled `phase` values:

```text
intake
planning
review
approval
execution
verification
change_request
result
system
```

### 3.3 JSON shape

External DTO / optional JSONL shape:

```json
{
  "id": "journal_...",
  "task_id": "task_...",
  "seq": 12,
  "created_at": "2026-06-28T10:00:00Z",
  "actor_type": "human|agent|system",
  "actor_id": "user-id-or-agent-alias",
  "phase": "planning|review|approval|execution|verification|change_request|result",
  "type": "decision|assumption|question|answer|approval|plan_review|plan_change|verdict|state_change|risk|note|artifact_update|task_progress|summary|correction",
  "title": "Plan approved",
  "body": "The human approved SPEC.md and PLAN.md for strict execution.",
  "severity": "info|warning|blocking",
  "refs": {
    "run_id": null,
    "attempt_id": null,
    "artifacts": [".agent-team/SPEC.md", ".agent-team/PLAN.md"],
    "artifact_etags": {
      "SPEC.md": "sha256:...",
      "PLAN.md": "sha256:..."
    },
    "files": []
  },
  "metadata": {},
  "supersedes": null
}
```

Validation:

- `title`: required, max 200 chars.
- `body`: optional but should be non-empty for manual/agent notes, max 10,000
  chars.
- `phase`, `type`, `severity`, `actor_type`: must be known values.
- `refs` and `metadata`: JSON object only.
- `refs.artifacts` and `refs.files`: workspace-relative strings only; never
  absolute paths.
- Oversized agent-proposed notes should be rejected or truncated with a warning.

## 4. Storage strategy

Use DB as source of truth.

Add optional workspace mirror:

```text
.agent-team/JOURNAL.jsonl
```

Rules:

- DB journal is authoritative.
- JSONL mirror is an export/workspace affordance, useful for CLI agents and
  portability.
- If mirror writing fails, do not fail the task. Log and continue.
- Mirror should contain accepted DB journal entries, not raw agent suggestions.

Agent note inbox:

```text
.agent-team/JOURNAL_NOTES.jsonl
```

Rules:

- Agents can append suggested semantic notes here.
- Backend ingests this file after planner/generator/evaluator runs.
- Backend validates, dedupes, writes accepted entries into DB, mirrors them to
  `JOURNAL.jsonl`, then archives or truncates the inbox.
- The inbox is not source of truth.

Archive suggested notes after ingestion:

```text
.agent-team/archive/journal-notes/{run_id}.jsonl
```

## 5. Backend modules

Add:

```text
features/board/repositories/journal.py
features/board/runtime/task_journal.py
```

Repository responsibilities:

- append entry
- list entries with pagination/filtering
- serialize DTO
- optionally allocate task-local `seq`

Runtime helper responsibilities:

- append system entries best-effort
- validate agent note suggestions
- ingest `.agent-team/JOURNAL_NOTES.jsonl`
- archive/truncate note inbox
- mirror accepted entries to `.agent-team/JOURNAL.jsonl`
- build refs for artifacts/runs/attempts

Suggested helper API:

```python
def append_journal_entry(
    *,
    task_id: str,
    actor_type: str,
    actor_id: str | None,
    phase: str,
    type: str,
    title: str,
    body: str = "",
    severity: str = "info",
    refs: dict | None = None,
    metadata: dict | None = None,
    supersedes_id: str | None = None,
) -> None:
    """Best-effort append. Never breaks the main workflow."""


def ingest_agent_notes(
    *,
    task_id: str,
    workspace_path: str,
    run_id: str | None,
    attempt_id: str | None,
    actor_id: str | None,
    phase: str,
) -> int:
    """Read JOURNAL_NOTES.jsonl, accept valid notes, archive inbox, return count."""
```

Best-effort rule:

- Journal append failures should not fail planning/execution.
- Validation failures for agent suggestions should not fail the run.
- Manual human note API errors should return proper HTTP errors.

## 6. Agent note mechanism

Because this project prioritises direct CLI agents (Claude Code, Cursor CLI,
Codex CLI), the universal mechanism should be file-based. LangChain tools are
available to LLM graph agents, but direct CLI agents do not automatically receive
those tools unless exposed through MCP. Therefore:

1. v1 of this feature: system-authored entries + file inbox for agent notes.
2. Later: optional `journal_note` ToolFactory for LLM graph agents.
3. Later: optional MCP tool for direct CLI agents.

### 6.1 File inbox prompt

Add this discipline to strict planner/generator/evaluator prompts and direct CLI
task context:

```text
Journal discipline:
- Record only important decisions, assumptions, risks, human questions, plan changes, and verification findings.
- Do not journal routine progress, raw logs, full prompts, or long command output.
- Keep each note short and useful to a future human or agent.
- If you need to record a note, append one JSON object per line to `.agent-team/JOURNAL_NOTES.jsonl`.
- The backend validates and imports accepted notes into the task journal.

Allowed JSONL shape:
{"type":"decision|assumption|risk|note|question|plan_change|verdict","title":"Short title","body":"Short useful note","severity":"info|warning|blocking","refs":{"artifacts":[],"files":[]}}
```

Do not tell agents that writing the inbox directly updates the UI. Say it is a
suggestion that the backend imports.

### 6.2 Agent note validation

Accepted agent-suggested types:

```text
decision
assumption
risk
note
question
plan_change
verdict
```

Map unsupported types to `note` or reject them.

Dedupe strategy:

- Compute a hash from `(type, title, body, run_id)`.
- Ignore exact duplicates in one ingestion.
- Optionally ignore duplicates already accepted for the same run.

Size limits:

- max line size: 20 KB
- max notes per ingestion: 50
- title max: 200 chars
- body max: 10,000 chars

## 7. System-authored entries

Append automatic journal entries at meaningful lifecycle points.

### 7.1 Planning

When `planning/start` is called:

- type: `state_change`
- phase: `planning`
- title: `Planning started`
- refs: planner/reviewer aliases when available

When planner raises blocking questions:

- type: `question`
- phase: `planning`
- severity: `blocking`
- refs: `.agent-team/QUESTIONS.json`, planner run id

When required artifacts are drafted:

- type: `artifact_update`
- phase: `planning`
- title: `Planning artifacts drafted`
- refs: `SPEC.md`, `PLAN.md`, `TASKS.json`, planner run id

When planner fails to produce required artifacts:

- type: `risk`
- phase: `planning`
- severity: `blocking`
- title: `Planning failed`

### 7.2 Review

When reviewer writes `PLAN_REVIEW.json`:

- type: `plan_review`
- phase: `review`
- severity: `info` for pass, `warning` for fail, `blocking` for needs_human
- title: `Plan review: pass|fail|needs_human`
- refs: `PLAN_REVIEW.json`, reviewer run id

### 7.3 Approval

When human approves:

- type: `approval`
- phase: `approval`
- actor_type: `human`
- actor_id: user id
- title: `Plan approved`
- refs: approved artifact etags

When human edits an approved artifact and approval is invalidated:

- type: `artifact_update`
- phase: `approval`
- severity: `warning`
- title: `Approval invalidated by artifact edit`

When human requests changes:

- type: `decision`
- phase: `approval`
- title: `Plan changes requested`
- body: human feedback

### 7.4 Questions and answers

When human answers questions:

- type: `answer`
- phase: planning or execution depending on `run_params`
- actor_type: `human`
- refs: archived `QUESTIONS.json`
- body: compact Q/A summary and optional note

### 7.5 Execution and task graph

When strict execution starts:

- type: `state_change`
- phase: `execution`
- title: `Execution started`
- refs: generator/evaluator aliases, approved artifact etags

When task graph starts a task:

- type: `task_progress`
- phase: `execution`
- title: `Started T1: <title>`
- refs: `TASKS.json`, attempt/run when known

When evaluator passes a graph task and backend marks it complete:

- type: `task_progress`
- phase: `verification`
- title: `Completed T1: <title>`
- refs: `TASKS.json`, `EVIDENCE.json`, attempt id

When a task is blocked:

- type: `risk`
- phase: `execution`
- severity: `blocking`
- title: `Blocked T1: <title>`

### 7.6 Plan change

When generator creates `PLAN_CHANGE_REQUEST.md`:

- type: `plan_change`
- phase: `change_request`
- severity: `blocking`
- title: `Plan change requested`
- refs: `PLAN_CHANGE_REQUEST.md`, run id, attempt id

When human resolves/re-approves:

- type: `approval`
- phase: `change_request`
- title: `Plan change resolved`
- refs: archived change request and updated artifact etags

### 7.7 Verification and result

When evaluator writes `EVIDENCE.json`:

- type: `verdict`
- phase: `verification`
- title: `Evaluator verdict: pass|fail|needs_human`
- refs: `EVIDENCE.json`, evaluator run id, attempt id
- metadata: score, checked_tasks, changed_files count, command count

When loop hits budget/attempt guardrail:

- type: `risk`
- phase: `execution`
- severity: `blocking`
- title: `Loop stopped by guardrail`
- metadata: guardrail name, tokens/cost/runtime if available

When loop completes:

- type: `state_change`
- phase: `result`
- title: `Goal complete`

When loop fails/cancels:

- type: `state_change`
- phase: `result`
- severity: `warning`
- title: `Goal failed` or `Goal cancelled`

## 8. Ingestion points in current code

Recommended insertion points:

- `router.py`
  - `start_task_planning`
  - `edit_task_planning_artifact`
  - `_approve_plan`
  - `request_task_planning_changes`
  - `approve_and_run_task_planning`
  - `answer_task_planning`

- `runtime/loop/planning.py`
  - after planner run completes
  - after questions detected
  - after reviewer run completes
  - after missing artifacts/failure
  - call `ingest_agent_notes` for planner/reviewer runs

- `runtime/loop/driver.py`
  - after generator turn completes, ingest agent notes for generator run
  - after evaluator completes, ingest agent notes for evaluator run
  - when outcomes are `plan_change`, `needs_answers`, budget/capped, complete

- `runtime/loop/task_graph.py`
  - when a graph task is set `in_progress`
  - when it is set `complete`
  - when it is set `blocked`
  - when final verification passes/fails

- `runtime/loop/service.py`
  - when strict execution starts
  - when loop status sink persists terminal states

Prefer central helper calls over direct DB writes scattered everywhere.

## 9. API

Add DTOs:

```python
class JournalEntryDTO(BaseModel):
    id: str
    task_id: str
    seq: int | None = None
    actor_type: str
    actor_id: str | None = None
    phase: str
    type: str
    title: str
    body: str
    severity: str
    refs: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    supersedes_id: str | None = None
    created_at: str | None = None


class JournalEntryCreate(BaseModel):
    type: str = "note"
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=10000)
    severity: str = "info"
    refs: dict = Field(default_factory=dict)
```

Endpoints:

```text
GET  /tasks/{task_id}/journal
POST /tasks/{task_id}/journal
```

`GET` query params:

```text
limit: int = 100
before_seq: int | None
after_seq: int | None
type: str | None
phase: str | None
severity: str | None
```

`POST`:

- human/editor creates manual note entries
- actor_type = `human`
- phase defaults to current task phase or `system`
- type defaults to `note`

Optional later:

```text
POST /tasks/{task_id}/journal/summarize
```

This can run a summarizer agent that writes `.agent-team/JOURNAL_SUMMARY.md` or
updates `.agent-team/RETROSPECTIVE.md`.

## 10. UI

Add a `Journal` surface in the task cockpit.

Recommended placement:

- Goal cockpit: add a `Journal` section near `Activity`.
- Overview: optionally show human-visible journal timeline below notes.
- Right artifact panel remains file-oriented; do not hide Journal only in the
  file tree.

Timeline card fields:

- icon by type
- title
- actor
- time
- phase badge
- severity badge
- short body
- refs as chips (`SPEC.md`, `PLAN.md`, `EVIDENCE.json`, run, attempt)

Filters:

- type
- phase
- severity

Manual note composer:

- title
- body
- type defaults to `note`
- severity defaults to `info`

UX rules:

- Journal is not raw transcript. Keep raw `Activity` / `GoalTranscript`
  separate.
- Blocking entries should stand out.
- Journal entries should be compact by default, expandable when body/refs are
  long.
- If the task is complete, Result card may show the latest `verdict` and link to
  Journal.

## 11. Prompt changes

Add journal discipline to:

- planner prompt
- reviewer prompt
- strict generator preamble
- task graph preamble
- strict evaluator prompt
- direct CLI task context in `.agent-team/TASK.md`

Prompt block:

```text
## Task Journal

This task has a semantic journal for important decisions and notes.

Use it sparingly. Record only information that a future human or agent should remember:
- important decisions
- assumptions that shape implementation
- risks or blockers
- human questions
- plan changes
- verification findings

Do not record routine progress, raw logs, long command output, full prompts, or full transcripts.

To suggest a journal note, append one JSON object per line to `.agent-team/JOURNAL_NOTES.jsonl`:

{"type":"decision|assumption|risk|note|question|plan_change|verdict","title":"Short title","body":"Short useful note","severity":"info|warning|blocking","refs":{"artifacts":[],"files":[]}}

The backend validates suggested notes and imports accepted entries into the Task Journal.
```

For direct CLI context, include this in `.agent-team/TASK.md` so Claude/Cursor/
Codex CLI can use the same file-based mechanism.

## 12. Optional tool support

After file-based journal notes work, add an optional tool for LLM graph agents:

```text
journal_note
```

Tool args:

```json
{
  "type": "decision",
  "title": "Use existing loop_state",
  "body": "The cockpit already treats task.loop_state as canonical.",
  "severity": "info",
  "refs": {
    "files": [],
    "artifacts": []
  }
}
```

Register it through plugin `tool_factories()` like `set_task_status`.

Do not rely on this as the only mechanism because direct CLI agents do not get
LangChain tools by default. If direct CLI tool support is desired, expose the
same operation through MCP later.

## 13. Tests

### 13.1 Model/repository tests

- Append entry creates task-local sequence.
- Entries list oldest/newest as expected.
- Filters by type/phase/severity work.
- `refs_json` and `metadata_json` decode to dicts.
- Append is best-effort helper safe.

### 13.2 Agent note ingestion tests

- Valid `JOURNAL_NOTES.jsonl` lines import into DB.
- Invalid JSON lines are ignored/reported without failing run.
- Unknown type is rejected or mapped to `note`.
- Oversized title/body is truncated or rejected.
- Duplicate notes in one ingestion are deduped.
- Inbox is archived/truncated after ingestion.
- Accepted notes are mirrored to `JOURNAL.jsonl` when mirror is enabled.
- Unsafe absolute/outside refs are rejected.

### 13.3 Workflow integration tests

- Planning start appends journal entry.
- Planner questions append blocking question entry.
- Plan approval appends approval entry with artifact etags.
- Human answer appends answer entry.
- Request changes appends decision entry.
- Execution start appends state_change entry.
- Task graph task started/completed/blocked appends progress entries.
- Plan change request appends blocking plan_change entry.
- Evaluator verdict appends verdict entry.
- Complete/failed/cancelled terminal state appends result entry.

### 13.4 API tests

- Viewer can GET journal.
- Editor can POST manual note.
- Viewer cannot POST manual note.
- Pagination works.
- Filters work.
- Unknown task returns not found/auth error using existing router conventions.

### 13.5 UI tests

- Journal timeline renders entries.
- Blocking entries are visually distinct.
- Ref chips render artifacts/runs/attempts.
- Filters reduce the list.
- Manual note composer posts an entry.
- Empty journal state is clear and quiet.

## 14. Implementation order

1. Add DB migration and model `AgentTeamJournalEntry`.
2. Register model in `plugin.py`.
3. Add repository `features/board/repositories/journal.py`.
4. Add schemas/DTOs.
5. Add runtime helper `features/board/runtime/task_journal.py`.
6. Add `GET/POST /tasks/{task_id}/journal`.
7. Add system-authored entries in planning endpoints and planning job.
8. Add agent note inbox constants/helpers.
9. Add `ingest_agent_notes` after planner/reviewer/generator/evaluator runs.
10. Add prompt/context journal discipline.
11. Add task graph progress journal entries.
12. Add UI Journal timeline and manual note composer.
13. Add optional `JOURNAL.jsonl` mirror.
14. Add optional `journal_note` tool.
15. Add summarizer/retrospective workflow later.

## 15. Acceptance criteria

The feature is complete when:

- Every task can show a Journal timeline.
- Backend appends key lifecycle events automatically.
- Humans can add manual journal notes.
- Agents can suggest notes via `.agent-team/JOURNAL_NOTES.jsonl`.
- Backend validates and imports suggested notes.
- Journal entries link to artifacts/runs/attempts where applicable.
- Journal does not replace `loop_state`, artifacts, evidence, run events, or
  `LoopLedger`.
- Existing planning/execution/chat behavior remains compatible.

## 16. Future summary agent

After journal entries are reliable, add a summarizer agent.

Inputs:

- journal entries
- `SPEC.md`
- `PLAN.md`
- latest `EVIDENCE.json`
- relevant refs only when needed

Outputs:

- `.agent-team/JOURNAL_SUMMARY.md`
- or `.agent-team/RETROSPECTIVE.md`

Prompt:

```text
You are a task historian. Summarize the Task Journal into a concise handoff for a future human or agent.

Use journal entries as the primary source. Follow artifact/run references only when needed.

Include:
- final outcome
- key decisions
- assumptions
- human answers
- plan changes
- verification evidence
- unresolved risks
- suggested durable improvements

Do not include raw transcripts or long logs.
```
