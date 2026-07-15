# Autonomous loop

Last updated: 2026-06-30 · [↩ index](../index.md) · Source:
[`../../plans/loop-engineering.md`](../../plans/loop-engineering.md),
[`../../plans/loop-quality-and-self-improvement.md`](../../plans/loop-quality-and-self-improvement.md),
`features/board/runtime/loop/`

Turning a task into a **verified** result, not just one streamed answer.

## Why

A plain run is one turn: mention → run → stop. There is no mechanism that
**verifies** the agent actually completed the task, and no place that **decides**
whether to retry / continue / ask a human. The autonomous loop adds exactly that,
**on top of** the existing event store and SSE — and chat mode is untouched
(autonomy is additive and only engages when a task opts in via
`execution_mode = "autonomous"`).

## The loop, in one diagram

```
controller.start() → first prompt (the objective)
   │
   ▼   ┌──────────────────────────────────────────────────────┐
   └──►│ generator turn (AgentWorker, role=generator)          │  frames → event store → SSE
       │   ▼                                                    │
       │ evaluator.evaluate(objective, transcript, workspace)  │  runs tests/lint/build
       │   ▼                                                    │
       │ controller.on_attempt_finished(...) → Continue | Done │
       └──────────────────────────────────────────────────────┘
   Continue(followup) → next user message in the SAME thread
   Done(outcome)      → set terminal loop_state; publish status; stop
```

Modules under `runtime/loop/`:

| File | Responsibility |
|---|---|
| `controller.py` | **No-I/O** decision logic: first prompt, continue-vs-stop. Testable in isolation. |
| `driver.py` | The I/O loop: run generator → evaluate → apply the controller's decision → publish status. |
| `evaluator.py` | The independent verifier worker. |
| `verdict.py` | The structured verdict shape, evaluator usage, legacy evidence checks, schema-v2 verification-contract and workspace-artifact validation, plus the compact evidence digest relayed into retries. |
| `verification_runner.py` | Resolves only approved commands, executes them in the task runtime, and mints source/runtime-bound database receipts. |
| `budget.py` | `LoopBudget` (caps) + `LoopLedger` (running token/cost/runtime accounting). |
| `status.py` | `LoopState` (persisted) + `LoopStatus` (live snapshot) + `outcome_to_state`. |
| `service.py` | Entry points: `start_autonomous_loop`, the planner wrapper, etc. |
| `planner.py` / `planning.py` / `planning_prompts.py` / `planning_artifacts.py` | the planning phase (see [`planning-workflow.md`](planning-workflow.md)). |
| `task_graph.py` | **executable** `TASKS.json` scheduling (opt-in): runs the plan one task at a time in dependency order, reusing `run_loop` for each task's sub-loop. |
| `human_actions.py` | reusable approve/ack/answer logic shared by the web router **and** the inbound comm gateway. |

## Key decisions

- **The evaluator is independent and owns completion.** The generator must not
  grade its own work. The evaluator is a separate worker (can be a different
  model) instructed to *disprove* completion — "assume broken until proven
  otherwise" — and to inspect the diff, trusted command receipts, and real UI or
  scenario evidence, not just read the transcript. The **backend** applies `complete` only after the evaluator
  returns `pass`. The generator may at most claim `in_progress` / `ready_for_review`.
- **A `pass` must be evidence-backed, or it is downgraded.** `service.py`
  downgrades any `pass` that carries no verification evidence (no commands/checks
  run) to `fail` via `_downgrade_unverified_pass` — a rubber-stamp without
  evidence is not a verified completion (this is what makes D5 real). On a
  `fail`, the controller relays a compact **evidence digest**
  (`format_evidence_digest`) into the next attempt's prompt so the generator
  fixes the exact gap instead of guessing. See
  [`decisions.md`](../decisions.md) D13.
- **Strict tasks can declare verification contracts.** Profiles and planned
  commands live on each `TASKS.json` task. The backend runner, not the evaluator,
  executes those approved commands in their declared assigned repo working
  directories and stores receipts tied to repo/cwd, source, and runtime
  fingerprints. Legacy string commands still run from the workspace root. The
  backend downgrades `pass` when a receipt is
  missing/failing/stale, a criterion is unmapped, a UI/AI scenario is absent, or
  a referenced workspace artifact is missing/unsafe. Legacy plans
  without this block remain valid, but strict passes still require successful
  commands with explicit exit codes.
- **Continuation is a normal user message** appended to the same thread — no
  system-prompt mutation, no toolset swap — so the prompt cache stays warm.
- **Fail-open judging.** Evaluator/judge failures are treated as "continue"; the
  attempt budget is the backstop so a broken judge can't wedge progress.
- **Never a silent finish.** A guardrail stop (attempt cap, token/cost/runtime
  budget) or a `needs_human` verdict routes to `WAITING_FOR_HUMAN`, not a silent
  success/fail. `Done` distinguishes genuine `complete` from `capped`.
- **A real user message mid-loop preempts** the continuation and pauses the loop
  for that turn (then it re-judges).

## The task state machine (`LoopState`)

`Task.loop_state` is the **single canonical public lifecycle** — there is no
competing planning state machine. Values (`runtime/loop/status.py`):

```
                ┌─ planning ──► waiting_plan_approval ──► plan_approved ─┐
ReadyForAgent ──┤                     ▲ (request changes)                ▼
                └──────────────────────────────────────────────────► running
running ──► complete
running ──► (fail, budget left) ──► running         # NeedsRevision
running ──► waiting_for_human                        # guardrail / needs_human / review
running ──► plan_change_requested                    # approved plan turned out wrong
running ──► waiting_answers                           # agent raised blocking questions
running ──► failed | cancelled
```

The driver publishes a live `loop.status` to the **board bus** at each lifecycle
point so the cockpit shows a progress chip; the UI invalidates on the SSE event.

## Two execution strategies

`start_autonomous_loop(..., task_graph=bool)` chooses how the approved plan runs:

- **Whole-objective (default).** `run_loop` drives one generator against the whole
  plan and the evaluator grades the whole `SPEC.md`.
- **Task-graph (opt-in, `task_graph=True`).** `task_graph.py` treats `TASKS.json`
  as an executable contract: it schedules tasks in dependency order, runs a scoped
  generator+critic sub-loop per task, and marks each task `complete` **on disk**
  (the on-disk `TASKS.json` status is the single source of truth for progress, so
  the cockpit and a resumed run agree). All sub-loops share one resource ledger so
  the budget spans the whole graph; a final whole-SPEC verification pass closes it
  out.

## Guardrails (`LoopBudget` + attempt cap)

`LoopBudget` (`budget.py`) holds the resource caps: `max_tokens`, `max_cost_usd`,
`max_wall_seconds` (`None`/`0` = unbounded). `LoopLedger` accumulates spend and
`exceeded()` returns the first cap hit (`"tokens"`/`"cost"`/`"runtime"`). The
**attempt cap** (`max_attempts`) lives on the controller (it shapes the continue
decision), separate from the resource budget. On any cap the loop **hard-stops →
`waiting_for_human`**, never a silent `failed`.

The ledger counts **both** sides of every attempt: the generator's spend **and**
the evaluator's own `eval_tokens`/`eval_cost_usd` (`driver.py` and `task_graph.py`
call `ledger.add(...)` for the verdict). Verification is not free, so a cheap
generator with an expensive evaluator no longer silently overruns the budget.

When the loop hard-stops without a verified pass (`capped`/`budget`, or a
task-graph block), `driver._finish` / the task-graph blocked branch also record a
**`friction`** journal entry (carrying the last evidence digest) so the blocker
surfaces on the board's Friction page → [`task-journal.md`](task-journal.md).

## Entry points

There is **no** `POST /tasks/{id}/loop`. A task reaches the loop through the
strict-planning flow:

1. `POST /tasks/{id}/planning/start` — drafts artifacts, parks at
   `waiting_plan_approval` (sets `execution_mode=autonomous`, `planning_mode=strict_plan`).
2. `POST /tasks/{id}/planning/approve-and-run` — approves **and** calls
   `start_autonomous_loop`. The run params live on the `PlanningRunCreate` body:
   `agent_id` (generator), `evaluator_id`, `task_graph` (bool), `max_attempts`,
   `max_tokens`, `max_cost_usd`, `max_wall_seconds`. They're remembered in the
   task's planning meta so an execution-phase question pause can resume with the
   same agents/budget.

Loop control: `GET /tasks/{id}/loop` (state + attempts/verdicts + graph tasks),
`POST /tasks/{id}/loop/cancel`, `POST /tasks/{id}/loop/ack` (clear a finished
loop's state). Plain chat `POST /tasks/{id}/mentions` is unchanged → a single
`run_turn`, no loop.

## Related

- The contract the loop executes against → [`planning-workflow.md`](planning-workflow.md)
- The decision memory the loop writes → [`task-journal.md`](task-journal.md)
- The worker contract → [`runtime-and-runs.md`](runtime-and-runs.md)
