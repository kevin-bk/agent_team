# Loop Engineering for agent_team — Design Doc (Phase 0 → 3)

Status: Phase 0–3 implemented.
Audience: engineers working on `agent_team/features/board/runtime` and the loop layer.

Implemented so far:

- Phase 0 — `runtime/workers/` (`base`, `llm_graph`, `acp_cli`, `registry`);
  `local_backend._drive` resolves a worker and streams via `emit`.
- Phase 1 — `AcpCliWorker` + `DirectCliRun` gain a permission mode (`auto` /
  `read_only`) and an idle timeout.
- Phase 2 — `runtime/loop/` (`verdict`, `controller`, `evaluator`, `driver`,
  `service`); models `objective`/`execution_mode` on Task, `role`/`attempt_id`
  on Run, plus `AgentTeamAttempt` / `AgentTeamEvaluation`; migrations
  `019`/`020`; endpoint `POST /tasks/{id}/loop`.
- Phase 3 — `runtime/loop/budget.py` (`LoopBudget` + `LoopLedger`) and
  `runtime/loop/status.py` (`LoopState` + `LoopStatus` + `outcome_to_state`);
  `Task.loop_state` (migration `021`); the driver enforces token/cost/runtime
  guardrails, publishes a live `loop.status` to the board bus at each lifecycle
  point, and routes a guardrail/`needs_human` stop to `WaitingForHuman` (never a
  silent finish). The `/tasks/{id}/loop` endpoint accepts the budget.

Remaining integration (frontend): a cockpit progress chip driven by the
`loop.status` bus event, the human-review panel for `WaitingForHuman`, and
optionally auto-moving the task's board column on terminal states.

## 0. Why

Today a "run" is **one turn**: a user mention (or autopilot/schedule) creates one
`AgentTeamRun`, the backend drives **either** a LangGraph LLM agent **or** a direct
CLI agent over ACP, streams frames into the event store, and stops. There is no
loop that:

- verifies the agent actually completed the task (no independent evaluator), and
- decides whether to retry / continue / ask a human and persists that decision.

We also have **two completely separate execution code paths** (`_run_graph` vs
`_run_direct_cli` inside `local_backend._drive`) that only meet at the event
store. Adding capabilities (loop control, evaluation, confirmation) to one path
does not help the other.

This doc introduces:

1. A single **`AgentWorker`** abstraction so the LLM agent and the CLI agent are
   driven through one contract (Phase 0).
2. A stronger **CLI worker** (`AcpCliWorker`) — idle timeout, permission policy —
   reusing the existing shared ACP infrastructure (Phase 1).
3. An **autonomous loop layer** (controller + independent evaluator + attempts)
   that turns a task into a verified result, on top of the existing event store
   and SSE (Phase 2).
4. A **task state machine + guardrails** (budget/retry caps, human-review gate)
   wired into the board and autopilot (Phase 3).

**Hard constraint that shapes everything:** the chat path (single-turn,
interactive) must keep working exactly as today. Autonomous behaviour is
*additive* and only engages when a task opts into it.

## 1. What we keep (do not rebuild)

The current architecture already has the right foundations; the loop layer is
built on top of them, not instead of them.

| Component | File | Role kept |
|---|---|---|
| Event store (append-only, monotonic `seq`) | `runtime/event_store.py` | source of truth for replay + SSE |
| Streaming event contract | `runtime/events.py` | wire frames (`text_delta`, `tool_use_*`, …) |
| LangGraph → frames translator | `runtime/translator.py` | LLM stream parsing |
| Run backend Protocol | `runtime/backend.py` | `start` / `cancel` / `reconcile_orphans` |
| In-process run registry | `runtime/registry.py` | same-process cancel fast-path |
| Thread bridge | `runtime/dispatch.py` | start runs from the autopilot thread |
| Context builders | `runtime/context.py`, `runtime/cli_context.py` | per-turn agent input |
| Shared ACP infra (pool, resume, reaper, permission routing) | `plugins/ai_code/tools/_acp_base.py` | the ACP session manager |
| Models | `board/models.py` | Board / Task / Conversation / Run / RunEvent |

The streaming contract and the event store mean **the frontend / SSE never has
to change** as we add new workers and the loop — every new code path still emits
the same `AgentTeamRunEvent` frames.

## 2. Core abstraction: `AgentWorker`

A worker drives **exactly one turn** of an agent against a task and emits frames.
"One turn" is the natural granularity here because the inner think→act→observe
loop is already owned by LangGraph (LLM agent) or by the CLI itself (ACP agent) —
we should not reimplement it.

```python
# runtime/workers/base.py

class WorkerRole(StrEnum):
    CHAT = "chat"            # interactive, one turn, no loop
    GENERATOR = "generator"  # does the task inside the autonomous loop
    EVALUATOR = "evaluator"  # independently grades the result
    SUMMARIZER = "summarizer"

@dataclass
class TurnContext:
    agent_alias: str
    prompt: str               # the user-message text for this turn
    workspace_path: str
    thread_id: str            # checkpointer thread id / ACP session key
    role: WorkerRole = WorkerRole.CHAT

# emit(event_type, data) -> awaitable; in practice = event_store.append_event
EmitFn = Callable[[str, dict], Awaitable[None]]

@dataclass
class TurnResult:
    final_text: str
    cancelled: bool
    usage: dict                       # input/output/total/cache_read tokens
    cli_usage_text: str | None = None # CLI context-window gauge, if any

class AgentWorker(Protocol):
    async def run_turn(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult: ...
```

Two implementations:

- `LlmGraphWorker` — wraps `graph_builder.build_graph` + `make_checkpointer` +
  `StreamTranslator`. This is the current `_run_graph` body verbatim, moved.
- `AcpCliWorker` — wraps `DirectCliRun`. This is the current `_run_direct_cli`
  body, moved, then enhanced in Phase 1.

Resolution lives in a small registry:

```python
# runtime/workers/registry.py
def resolve_worker(agent_alias: str, role: WorkerRole = WorkerRole.CHAT) -> AgentWorker:
    if is_direct_cli_alias(agent_alias):
        return AcpCliWorker(engine=engine_for_alias(agent_alias))
    return LlmGraphWorker()
```

`local_backend._drive` collapses to: load context → resolve worker → `run_turn`
→ finalize. The `cancelled` / token-accounting / finalize logic is unchanged.

### Why `emit` instead of returning frames

The backend already persists each frame as it arrives (so SSE can tail a live
run) and offloads large tool output out-of-band (`_persist_tool_output`). Passing
an `emit` callback keeps that streaming behaviour and keeps the worker ignorant
of persistence details — the same callback is reused by the evaluator and any
future worker.

## 3. Phase 0 — AgentWorker abstraction (no behaviour change)

**Goal:** unify the two execution paths behind `AgentWorker`. Pure refactor.

Files:

```
runtime/workers/
  __init__.py
  base.py        # WorkerRole, TurnContext, TurnResult, AgentWorker, EmitFn
  llm_graph.py   # LlmGraphWorker  (from _run_graph)
  acp_cli.py     # AcpCliWorker    (from _run_direct_cli, wraps DirectCliRun)
  registry.py    # resolve_worker()
```

Changes to `local_backend.py`:

- `_drive` builds a `TurnContext` from the loaded run context and an `emit`
  closure that calls `_persist_tool_output` then `event_store.append_event`.
- It calls `resolve_worker(agent_alias, WorkerRole.CHAT).run_turn(ctx, emit, handle.cancel_event)`.
- `_run_graph` and `_run_direct_cli` are deleted; their bodies live in the
  workers. `_finish_done` / `_finish_cancelled` / token accounting are untouched.

**Behaviour invariants (must hold after Phase 0):**

- A `cli:*` alias still talks straight to the CLI over ACP; any other alias still
  runs through its LangGraph graph.
- The exact same frames are emitted in the same order; `final_answer`, token
  totals, `cli_usage_text`, cancel semantics, orphan reconcile all unchanged.
- Cross-process cancel polling (`is_cancel_requested`) still happens on the same
  cadence (the poll lives in the worker loop now, behaviour identical).

**Tests:** existing `tests/test_agent_team.py` must pass unchanged. Add a unit
test that `resolve_worker("cli:claude")` → `AcpCliWorker` and
`resolve_worker("some-agent")` → `LlmGraphWorker`.

## 4. Phase 1 — stronger CLI worker

**Goal:** make `AcpCliWorker` more robust, copying patterns proven in reference
ACP agents, **without** modifying the shared `ai_code/_acp_base` manager (other
plugins depend on it) and **without** changing the chat UX.

### 4.1 Idle-based turn timeout

Today a direct turn has a single hard ceiling (`_DIRECT_ACP_TURN_TIMEOUT_SECONDS`
= 3h). A turn that is genuinely progressing but slow can be killed; a turn that
silently wedged still waits up to 3h.

Add an **idle timeout** enforced at the worker level (no `_acp_base` change): the
worker already drains the ACP progress queue in a loop, so it tracks the
timestamp of the last received frame. If no frame arrives for
`idle_timeout_seconds` (default e.g. 600s, configurable), the worker flips the
cancel event → the run stops with a clear "no activity" error. The 3h hard
ceiling stays as an absolute backstop.

Deadline resets on **any** activity (assistant text, thinking, tool start/
progress, usage) — i.e. a steadily-working agent runs as long as it keeps
producing output.

### 4.2 Permission policy

Today the CLI is launched with `auto_approve=True` hard-coded. Introduce an
`AcpPermissionMode` resolved from board config:

```python
class AcpPermissionMode(StrEnum):
    AUTO = "auto"          # approve everything (current behaviour, the default)
    READ_ONLY = "read_only"# deny mutating permission requests
```

For Phase 1 the policy maps to the existing `auto_approve` bool that
`_acp_base` already honours (`_should_approve`). `AUTO` keeps today's behaviour;
`READ_ONLY` passes `auto_approve=False` so the manager returns `cancelled` for
permission requests. True interactive "confirm-risky" mid-turn confirmation is
out of scope for Phase 1 (an ACP `prompt()` is one blocking call; pausing it for
human input needs the loop layer) and is revisited in Phase 3.

The mode is read from a new optional board field `cli_permission_mode`
(default `auto`), threaded through `TurnContext`.

### 4.3 Session resume (already present — documented)

`_acp_base` already persists the ACP `session_id` in a durable store and resumes
it via `session/load` on a cache miss (cold start / restart / dead subprocess),
keyed by `cli:<engine>::<thread_id>`. No change needed; this satisfies
"resume across restart". We add a short note + a test asserting the key shape.

### 4.4 MCP forwarding (deferred within Phase 1)

`_acp_base.new_session` currently passes `mcp_servers=[]`. Forwarding the board's
MCP connectors into the CLI session is valuable but requires a config model and a
small `_acp_base` change (accept an `mcp_servers` arg). Tracked as a Phase 1
follow-up / Phase 2 dependency, not implemented in the first cut.

**Behaviour invariant:** with `cli_permission_mode=auto` and a sane idle timeout,
an existing direct-CLI chat behaves exactly as before (idle timeout only fires on
true inactivity).

## 5. Phase 2 — autonomous loop layer

**Goal:** a task can be run to *verified* completion by a controller that drives
a generator worker, runs an independent evaluator, and decides continue/stop —
persisting every attempt. Chat mode is untouched.

### 5.1 New entities

```python
# Task: add columns
objective: str | None              # acceptance criteria, free text
execution_mode: str                # "chat" (default) | "autonomous"

# Run: add columns
role: str                          # chat | generator | evaluator | summarizer
attempt_id: str | None

# New: one loop iteration (generator turn + its evaluation)
class AgentTeamAttempt:
    id; task_id; attempt_no; status; created_at; ended_at

# New: independent verdict for an attempt
class AgentTeamEvaluation:
    id; task_id; attempt_id; run_id
    verdict: str                   # pass | fail | needs_human
    score: float                   # 0..1
    missing: str                   # what remains (fed back as the next prompt)
    evidence_json: str             # commands run / outputs / checks
    created_at
```

All additive; existing rows default `execution_mode="chat"`, `role="chat"`.
Schema via `db_migrations/*.sql` (the plugin creates new tables on startup, but
new columns on existing tables need a migration — follow the repo convention).

### 5.2 Controller (no I/O) + driver (I/O)

Split the decision logic from the I/O so the same logic is testable and reusable
(sync driver now, async server task later):

```python
# loop/controller.py
class LoopController:
    """Judges completion and decides continue-vs-stop. Performs NO I/O."""
    def __init__(self, objective, judge_llm, *, max_attempts=10): ...
    def start(self) -> str: ...                       # first prompt (the objective)
    def on_attempt_finished(self, events) -> LoopStep: # Continue(followup) | Done(outcome)

# loop/driver.py
async def run_loop(task_id, generator: AgentWorker, evaluator: Evaluator, controller):
    emit/send first prompt
    while True:
        run generator turn (persists frames, SSE as usual)
        verdict = evaluator.evaluate(objective, transcript, workspace)
        step = controller.on_attempt_finished(...)
        if Done: set terminal state; break
        send step.followup as the next user message
```

Decision rules (proven defaults):

- The continuation prompt is a **normal user message** appended to the same
  conversation thread — no system-prompt mutation, no toolset swap, so prompt
  caching of the prior prefix stays intact.
- Judge/evaluator failures are **fail-open** (treat as "continue"); the
  attempt-budget is the backstop so a broken judge cannot wedge progress.
- A real user message arriving mid-loop **preempts** the continuation and pauses
  the loop for that turn (then we re-judge, so a user message that completes the
  goal still ends it).
- `Done` distinguishes genuine `complete` from `capped` (hit `max_attempts`) so
  the UI never has to guess whether a silent finish meant success.

### 5.3 Independent evaluator

The generator must not grade its own work. The evaluator is a separate worker
(can be a different runtime/model) instructed to **disprove** completion:

> Assume the implementation is broken until proven otherwise. Pass only when the
> behaviour is verified by evidence (tests/lint/build/acceptance criteria).

The evaluator should *act* (run the project's test/lint/build commands in the
task workspace), not just read the transcript, and return a structured verdict
(`pass | fail | needs_human` + `missing` + evidence). Its turn streams into the
event store like any run (with `role=evaluator`) so the cockpit shows the audit.

### 5.4 Entry points

- New endpoint `POST /tasks/{id}/run` with `execution_mode=autonomous` starts the
  loop (queues a generator run, then the driver takes over).
- Autopilot (Phase 3) can target autonomous mode for assigned tasks.
- Chat `POST /tasks/{id}/mentions` is unchanged → single `run_turn`, no loop.

## 6. Phase 3 — state machine + guardrails

**Goal:** make the loop safe and observable; wire it into the board.

### 6.1 Task state machine

```
ReadyForAgent → Running(generator) → Evaluating
Evaluating --pass--> Done
Evaluating --fail & budget left--> NeedsRevision → Running
Evaluating --needs_human / risky / low score--> WaitingForHuman
Running/Evaluating --error/timeout/budget exhausted--> Failed
```

`Task.loop_state` holds the machine state; it maps onto board column **keys**
(reuse the autopilot column-mapping mechanism). New first-class columns:
`Evaluating`, `NeedsRevision`, `WaitingForHuman`.

### 6.2 Guardrails

```python
class LoopBudget:
    max_attempts: int
    max_runtime_seconds_per_attempt: int
    max_tokens_per_task: int | None
    max_cost_usd_per_task: float | None
    max_concurrent_runs: int
```

Read per board (with per-task override). On any cap the loop **hard-stops** →
`WaitingForHuman` (not silently `Failed`). Token/cost totals already accumulate
on each run (`usage`, ACP `PromptResponse.usage`); the controller sums across the
task's attempts.

### 6.3 Human-review gate

`WaitingForHuman` is a first-class state, surfaced in the cockpit with: summary,
changed files, commands run, evaluator verdict, risk flags, and approve / reject
/ request-changes actions. Require human review when: migrations / auth / billing
/ infra changed, large diff, evaluator low confidence, tests could not run, or
the attempt budget was exhausted.

### 6.4 Confirmation for CLI (revisited)

With the loop layer present, a "confirm-risky" CLI permission mode becomes
feasible: a permission request can be surfaced as a pending event and the turn
paused until a human responds, mirroring the loop's human-review plumbing.

## 7. Migration / rollout order

| Phase | Risk | Chat mode | Ships independently |
|---|---|---|---|
| 0 — worker abstraction | low (pure refactor) | unchanged | yes |
| 1 — stronger CLI worker | low–med | unchanged (auto + idle) | yes |
| 2 — loop + evaluator | med | unaffected (additive) | yes |
| 3 — state machine + guardrails | med–high | unaffected | yes |

Each phase is independently shippable and leaves chat mode working. Phase 0 and 1
are implemented together in the first cut; 2 and 3 follow.

## 8. Testing strategy

- Phase 0: existing suite green + `resolve_worker` unit test + a fake worker that
  asserts `run_turn` is called with the right `TurnContext` and that frames it
  emits land in the event store.
- Phase 1: idle-timeout fires on simulated inactivity; `cli_permission_mode`
  threads to the `auto_approve` bool; session key shape asserted.
- Phase 2: controller decision table (continue/done/capped, fail-open) tested in
  isolation (no I/O); driver tested with fake generator + fake evaluator.
- Phase 3: state-machine transitions + budget hard-stop unit tests.
