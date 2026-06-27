# Planning Workflow Implementation Decisions

Status: Proposed implementation guidance.
Audience: coding agents implementing the planning workflow upgrade for
`community_plugins/agent_team`.

This document answers follow-up questions raised after reading
`docs/plans/planning-workflow-upgrade.md`.

The short version:

- Implement the full vision in phases, but do not build throwaway phases.
- Work backwards from the final architecture, then deliver it forwards in safe
  slices.
- `v1` is the first production slice, not a prototype.
- `v2` and `v3` should extend the contracts introduced in `v1`, not replace
  them.

## 0. Current code facts validated

These are the relevant facts from the current implementation.

- `LoopState` is currently the persisted task-level autonomous loop state in
  `features/board/runtime/loop/status.py`.
- `AgentTeamTask.loop_state` is the database field exposed to the cockpit.
- `POST /tasks/{task_id}/loop` currently starts the full autonomous loop.
- The optional planner currently runs inside the same autonomous loop. It writes
  `.agent-team/PLAN.md` and then the generator starts.
- `WorkerPlanner` returns `None` if the plan file was not written. The loop then
  proceeds from the raw objective.
- Attempts and evaluations already exist:
  - `AgentTeamAttempt` stores loop iterations.
  - `AgentTeamEvaluation` stores the independent evaluator verdict.
- The evaluator is already conceptually independent from the generator. That is
  the correct direction and should be preserved.

Implication: the planning upgrade should reuse the current loop/evaluator
foundation, but should not block a live loop while waiting for human approval.

## 1. Decision: use one public lifecycle state

Question from coding agent:

> Two state tables may conflict. Current code has `LoopState`; the design doc
> also proposes a `PlanningSession` with many statuses. Should we use one state
> only?

Decision:

Use `task.loop_state` as the single canonical public lifecycle state for the
task cockpit.

Do not add a second top-level state machine that can answer "where is this task
now?" differently from `task.loop_state`.

Planning metadata may exist later, but it must be metadata, not a competing
lifecycle. Examples of acceptable metadata:

- artifact version
- approved version
- approved by
- approved at
- reviewer verdict
- artifact etag/checksum
- active plan version
- lock owner

Recommended `LoopState` expansion:

```text
null
planning
waiting_plan_approval
plan_approved
running
waiting_for_human
plan_change_requested
complete
failed
cancelled
```

Meaning:

- `null`: plain chat task or no visible autonomous lifecycle.
- `planning`: planner/reviewer is actively creating planning artifacts.
- `waiting_plan_approval`: planning artifacts exist and the system is stopped,
  waiting for human approval or requested changes.
- `plan_approved`: human approved the plan, but execution has not started yet.
- `running`: generator/evaluator loop is actively executing.
- `waiting_for_human`: guardrail, ambiguity, evaluator `needs_human`, or
  user-facing review needed.
- `plan_change_requested`: implementation discovered that the approved plan is
  wrong, unsafe, or insufficient.
- `complete`: evaluator verified the objective.
- `failed`: terminal failure.
- `cancelled`: human/system cancelled the loop.

Do not persist `drafting_spec`, `reviewing_plan`, or `drafting_tasks` as
independent task states in v1. Those are internal planning steps. In v1, while
those steps run, the public state can remain `planning`.

If a later version needs more detail, return it as a nested planning status:

```json
{
  "loop_state": "planning",
  "planning": {
    "step": "reviewing_plan",
    "artifact_version": 3,
    "review_verdict": "fail"
  }
}
```

The cockpit should still treat `loop_state` as the primary state.

## 2. Decision: evaluator owns completion

Question from coding agent:

> Who is allowed to mark a subtask complete? The generator or the evaluator?

Decision:

The evaluator, through backend-controlled state transition, owns completion.

The generator must not authoritatively mark work as complete.

Rules:

- Generator may claim a task/subtask as `in_progress`.
- Generator may report `ready_for_review`.
- Generator may summarize changed files and validation it attempted.
- Evaluator verifies actual workspace state, git diff, tests, lint, build, and
  acceptance criteria.
- Backend applies `complete` only after evaluator returns `pass`.
- If evaluator returns `fail`, backend keeps the task pending or marks it
  `needs_revision`.
- If evaluator returns `needs_human`, backend routes to human review.

For `TASKS.json`, do not rely on generator-edited status as authoritative.
Prefer one of these patterns:

1. Backend owns task status and rewrites `TASKS.json` after evaluator verdict.
2. `TASKS.json` stays immutable as the approved plan, while task progress is
   stored separately in DB or `.agent-team/TASK_PROGRESS.json`.

For v1, avoid introducing authoritative subtask status transitions. `TASKS.json`
can be advisory.

## 3. Decision: task graph scheduling is v2, not v1

Question from coding agent:

> The task graph is the heaviest change. Should v1 skip it?

Decision:

Yes. v1 should not rewrite the loop driver around task graph scheduling.

v1 should add the durable planning contract and approval gate while keeping the
execution loop close to the current implementation.

v1 behavior:

- Planner creates `SPEC.md`, `PLAN.md`, and optionally `TASKS.json`.
- Human approves the plan.
- Generator receives prompts pointing to approved artifacts.
- Generator works toward the whole approved objective, similar to the current
  loop.
- Evaluator grades the whole objective and plan, similar to the current loop.
- `TASKS.json`, if created, is advisory/checklist only.

v2 behavior:

- `TASKS.json` becomes executable.
- Backend validates dependency graph.
- Backend picks the next ready subtask.
- Generator executes only that subtask.
- Evaluator verifies that subtask.
- Backend marks the subtask complete.

This avoids a large rewrite in v1 while preserving the artifact contract needed
for v2.

## 4. Decision: planning and execution are separate commands

Question from coding agent:

> Human approval may take hours. Should planning and execution be two commands?

Decision:

Yes. Split them.

Do not keep a background loop, database connection, subprocess, ACP session, or
async task alive while waiting for human approval.

Recommended v1 API:

```text
POST /tasks/{task_id}/planning/start
GET  /tasks/{task_id}/planning
POST /tasks/{task_id}/planning/approve
POST /tasks/{task_id}/planning/request-changes
POST /tasks/{task_id}/planning/approve-and-run
```

Behavior:

- `planning/start`
  - starts planner/reviewer work
  - writes artifacts
  - stops completely
  - sets `loop_state = waiting_plan_approval`

- `planning/approve`
  - validates required artifacts
  - stamps approval metadata
  - sets `loop_state = plan_approved`
  - does not start execution unless explicitly requested

- `planning/approve-and-run`
  - validates approval
  - starts autonomous execution
  - sets `loop_state = running`

It is acceptable to implement `approve-and-run` as a convenience endpoint that
internally performs approval and then calls the loop starter.

Important: `POST /tasks/{task_id}/loop` should not silently start strict
planning and then wait for human. Strict planning must be a separate planning
job.

## 5. Decision: planner questions are file/artifact based, not live blocking

Question from coding agent:

> If planner needs to ask the human, where does it ask and how does the user
> answer?

Decision:

v1 should not implement a live question-answer protocol.

In v1:

- Planner writes unresolved questions into `SPEC.md` under `## Open Questions`.
- If there are blocking open questions, set `loop_state = waiting_plan_approval`
  or `waiting_for_human`.
- Human answers by editing the spec, adding a comment, or using a future UI
  field.
- Human clicks `request changes` or `re-plan`.
- Planner reruns and produces updated artifacts.

v2 may add `.agent-team/QUESTIONS.json` if the UI needs structured question
cards.

Suggested future schema:

```json
{
  "version": 1,
  "questions": [
    {
      "id": "Q1",
      "question": "Which branch should the PR target?",
      "reason": "The implementation endpoint needs a default base branch.",
      "blocking": true,
      "answer": null
    }
  ]
}
```

But v1 can use `SPEC.md#Open Questions` only.

## 6. Decision: active plan change request must be resolved or archived

Question from coding agent:

> If `PLAN_CHANGE_REQUEST.md` remains after human handles it, will the next loop
> stop again?

Decision:

Yes, that would be a bug. Treat `PLAN_CHANGE_REQUEST.md` as an active marker.

Rules:

- If `.agent-team/PLAN_CHANGE_REQUEST.md` exists, strict execution should pause.
- After human resolves it, remove or archive the active marker.
- Archive path can be:
  `.agent-team/archive/plan-change-requests/{timestamp}.md`
- Only the active path should block execution.

Recommended resolve endpoint:

```text
POST /tasks/{task_id}/planning/resolve-change-request
```

Behavior:

- archives current `.agent-team/PLAN_CHANGE_REQUEST.md`
- clears active marker
- stores human decision
- moves state back to `waiting_plan_approval` or `plan_approved`
- requires re-approval if the approved artifacts changed

## 7. Decision: artifact editing needs a lock/version guard

Question from coding agent:

> If human edits plan in UI while agent writes it, data can be lost. Should we
> lock?

Decision:

Yes.

v1 locking rules:

- While `loop_state = planning`, agent may write planning artifacts; UI should be
  read-only or show "planning in progress".
- While `loop_state = waiting_plan_approval`, agent must not write artifacts;
  human may edit/request changes.
- While `loop_state = plan_approved`, artifacts are frozen unless approval is
  cleared.
- While `loop_state = running`, UI should show artifacts read-only.
- If a human edits an approved artifact, approval must be invalidated.

Use a simple version or etag in v1.

Recommended artifact metadata:

```json
{
  "path": ".agent-team/PLAN.md",
  "etag": "sha256:...",
  "updated_at": "2026-06-27T00:00:00Z",
  "updated_by": "planner|human|reviewer",
  "approved": true
}
```

Edit endpoint should require the last seen etag:

```text
PUT /tasks/{task_id}/planning/artifacts/{artifact_name}
If-Match: sha256:...
```

If etag does not match, return conflict and ask UI to reload.

## 8. Important clarification: phases are delivery slices, not throwaway builds

Concern:

> If v1 and v2 are separate, what if v2 needs to destroy v1?

Answer:

Do not build v1 as a throwaway prototype.

Build v1 as a thin production slice of the final architecture.

Principle:

```text
Work backwards from v3. Implement forwards from v1.
```

This means v1 introduces the stable contracts that v2 and v3 will reuse:

- artifact layout
- artifact helper/service
- planning API names
- planning modes
- approval metadata
- lock/version token
- expanded `LoopState`
- prompt module
- evaluator-owned completion principle

v2 and v3 then add deeper behavior behind those contracts.

## 9. Version plan

### 9.1 v0: current behavior

Current behavior:

- User starts loop.
- Optional planner writes `.agent-team/PLAN.md`.
- Generator reads plan and works.
- Evaluator grades attempts.
- Planner failure is fail-open.
- No approval gate.

Keep this as `legacy_plan` until strict planning is stable.

### 9.2 v1: strict planning foundation

Goal:

Add human-approved planning without rewriting the core generator/evaluator loop.

Must implement:

- artifact layout:
  - `.agent-team/SPEC.md`
  - `.agent-team/PLAN.md`
  - `.agent-team/TASKS.json`
  - `.agent-team/PLAN_REVIEW.json`
  - `.agent-team/EVIDENCE.json`
  - `.agent-team/PLAN_CHANGE_REQUEST.md`
- artifact helper/service:
  - read/write known artifacts
  - prevent path traversal
  - validate JSON where applicable
  - compute etag/checksum
  - expose metadata
- public planning API:
  - `planning/start`
  - `planning/get`
  - `planning/approve`
  - `planning/request-changes`
  - `planning/approve-and-run`
- planning modes:
  - `legacy_plan`
  - `strict_plan`
- approval gate:
  - strict execution refuses to run without approved artifacts
- prompt contracts:
  - planner creates spec/plan/tasks
  - reviewer reviews
  - generator reads approved artifacts
  - evaluator checks approved artifacts
- state:
  - use `task.loop_state` as canonical lifecycle
- compatibility:
  - existing chat/mentions unaffected
  - existing loop without planner unaffected
  - legacy planner path available

Should not implement yet:

- executable task graph scheduler
- durable leases
- PR automation
- full artifact version history
- live question-answer protocol

v1 acceptance criteria:

- Planning can run and stop without starting execution.
- Human can approve later.
- Approved plan can start execution.
- Strict execution refuses missing/unapproved artifacts.
- Planner/reviewer artifacts are visible through API.
- Artifact edits are guarded by etag or version.
- Generator prompt references `SPEC.md` and `PLAN.md`.
- Evaluator prompt references approved artifacts and actual evidence.

### 9.3 v2: executable task graph and structured human loops

Goal:

Turn `TASKS.json` from advisory checklist into execution contract.

Must implement:

- strict `TASKS.json` validation:
  - unique task ids
  - known dependencies
  - no cycles
  - valid statuses
  - non-empty acceptance criteria
  - validation commands where possible
- deterministic scheduler:
  - pick first pending task whose dependencies are complete
  - mark active task `in_progress`
  - prompt generator only for active task
  - evaluator verifies active task
  - backend marks complete after evaluator pass
- structured question workflow:
  - either `SPEC.md#Open Questions` plus UI support
  - or `.agent-team/QUESTIONS.json`
- structured plan change workflow:
  - active marker pauses execution
  - resolve endpoint archives marker
  - changed artifacts invalidate approval
- better progress model:
  - `TASKS.json` approved plan remains stable
  - progress is backend-owned or written only after evaluator verdict
- UI:
  - task graph progress
  - active task
  - blocking question cards
  - plan change request panel

v2 acceptance criteria:

- Generator cannot mark a subtask complete directly.
- Evaluator/backend owns subtask completion.
- Dependency ordering is respected.
- Cycles/invalid dependencies are rejected.
- Plan change request pauses loop and can be resolved.
- Human answers/re-plan flow works without keeping processes alive.

### 9.4 v3: production orchestration

Goal:

Make the platform robust for long-running autonomous work.

Must implement:

- durable loop jobs:
  - job row
  - lease owner
  - heartbeat
  - lease expiry
  - restart reconciliation
  - resume or safe failover
- durable run ownership:
  - only one worker claims a planning/execution job
  - stale jobs can be reclaimed
- artifact version history:
  - every approval pins a plan version
  - execution stores approved version id/checksum
  - evidence links to plan version and attempt
- audit trail:
  - who approved
  - who edited
  - which agent planned
  - which agent reviewed
  - which agent executed
  - evaluator verdicts
  - plan change history
- multi-agent roles:
  - planner
  - plan reviewer
  - generator
  - evaluator
  - summarizer/retrospective agent
- evidence improvements:
  - commands with exit codes
  - changed files from git diff
  - screenshots for UI work
  - test output summaries
  - risk flags
- PR automation:
  - branch
  - commit
  - pull request
  - attach evidence
  - attach plan and spec summary
- metrics:
  - attempts
  - duration
  - tokens
  - cost
  - pass/fail rate
  - stuck reasons
- retrospective:
  - `.agent-team/RETROSPECTIVE.md`
  - suggested prompt/test/repo-guidance improvements

v3 can add tables for jobs, leases, artifact versions, and audit events. It
still must keep `task.loop_state` as the canonical cockpit lifecycle.

## 10. Stable contracts to introduce in v1

These contracts should be designed as final-ish from the start.

### 10.1 Artifact paths

```text
.agent-team/SPEC.md
.agent-team/PLAN.md
.agent-team/TASKS.json
.agent-team/PLAN_REVIEW.json
.agent-team/EVIDENCE.json
.agent-team/PLAN_CHANGE_REQUEST.md
.agent-team/archive/
```

Do not hard-code "plan means only `PLAN.md`". Treat planning as an artifact set.

### 10.2 Planning modes

```text
legacy_plan
strict_plan
```

`legacy_plan` preserves current behavior.

`strict_plan` requires approved artifacts before execution.

### 10.3 Approval metadata

Minimum v1 metadata:

```json
{
  "approved": true,
  "approved_at": "2026-06-27T00:00:00Z",
  "approved_by": "user-id",
  "artifact_etags": {
    "SPEC.md": "sha256:...",
    "PLAN.md": "sha256:...",
    "TASKS.json": "sha256:..."
  }
}
```

Execution must know which artifact etags were approved. Do not blindly execute
whatever happens to be the latest file on disk if it differs from the approved
version.

### 10.4 Prompt module

Put planning prompts in one module, for example:

```text
features/board/runtime/loop/planning_prompts.py
```

Prompts should be versioned or at least named clearly:

```text
SPEC_DISCOVERY_SYSTEM
PLAN_DRAFT_SYSTEM
PLAN_REVIEW_SYSTEM
GENERATOR_STRICT_SYSTEM
EVALUATOR_STRICT_SYSTEM
PLAN_CHANGE_REQUEST_SYSTEM
```

### 10.5 Artifact service

Create a small service/helper before UI work:

```text
features/board/runtime/loop/planning_artifacts.py
```

Responsibilities:

- resolve artifact paths inside workspace
- reject path traversal
- read/write artifacts
- compute etags
- parse JSON
- validate schemas
- archive active markers

This prevents v2/v3 from scattering file logic across router, UI handlers, and
loop service.

## 11. Recommended implementation order

If implementing all phases in one larger effort, still land changes in this
order.

1. Extend `LoopState` values and UI labels.
2. Add artifact constants/helpers.
3. Add prompt module.
4. Add v1 planning endpoints.
5. Add strict planning approval metadata.
6. Add artifact etag/version guard.
7. Wire approved artifacts into current generator/evaluator prompts.
8. Add strict-mode tests.
9. Add `TASKS.json` schema and validation.
10. Implement v2 scheduler.
11. Make evaluator/backend own subtask completion.
12. Add question and plan-change workflows.
13. Add artifact archive/resolve behavior.
14. Add durable job/lease system.
15. Add artifact version history and audit trail.
16. Add PR, metrics, evidence, and retrospective improvements.

Do not begin with UI. Durable artifacts and API contracts should come first.

## 12. Copy-paste instruction for the coding agent

Use this as the implementation instruction:

```md
Implement the planning workflow upgrade without a big-bang rewrite.

Important: phases are delivery slices, not throwaway prototypes. Work backwards
from the v3 architecture, but implement forwards from v1.

## Non-negotiable decisions

1. `task.loop_state` is the canonical public lifecycle state. Do not add a
   competing top-level planning state machine.
2. Planning and execution are separate jobs. Do not keep a background process,
   DB connection, ACP session, or loop task alive while waiting for human
   approval.
3. Generator must not authoritatively mark work complete. Evaluator/backend owns
   completion.
4. v1 must not rewrite execution around task graph scheduling. `TASKS.json` may
   exist in v1, but it is advisory until v2.
5. Artifact paths and API names introduced in v1 should be stable enough for
   v2/v3.
6. Strict planning must not fail open to raw objective execution.
7. Existing chat/mention flow and existing autonomous loop without strict
   planning must keep working.

## v1 implementation

- Add planning artifact service/helper.
- Add artifact paths for SPEC, PLAN, TASKS, REVIEW, EVIDENCE, and
  PLAN_CHANGE_REQUEST.
- Add prompt module for planner, reviewer, strict generator, strict evaluator,
  and plan change request.
- Add planning endpoints:
  - start planning
  - get planning artifacts/status
  - approve
  - request changes
  - approve and run
- Add approval metadata with artifact etags/checksums.
- Add simple artifact edit lock/version guard.
- Extend `LoopState` to include planning approval states.
- Wire approved `SPEC.md` and `PLAN.md` into generator/evaluator prompts.
- Keep current generator/evaluator loop shape.
- Keep legacy planner mode available.

## v2 implementation

- Make `TASKS.json` executable.
- Add dependency validation.
- Add deterministic next-task scheduler.
- Generator works only on active task.
- Evaluator verifies active task.
- Backend marks active task complete only after evaluator pass.
- Add question workflow.
- Add plan change request resolve/archive workflow.

## v3 implementation

- Add durable job leases and restart recovery.
- Add artifact version history and audit trail.
- Add multi-agent role orchestration.
- Add richer evidence, PR automation, metrics, and retrospective.

Do not implement v1 as a temporary shortcut that v2 must delete. v1 is the
foundation subset of the final architecture.
```

## 13. Summary for maintainers

The right strategy is not "do v1 quickly and rewrite later".

The right strategy is:

```text
Design the contracts for v3.
Implement the smallest useful slice in v1.
Turn on task graph behavior in v2.
Add production durability in v3.
```

This keeps the work incremental without making the early phases disposable.
