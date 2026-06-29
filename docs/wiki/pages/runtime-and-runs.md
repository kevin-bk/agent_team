# Runtime & runs

Last updated: 2026-06-29 · [↩ index](../index.md) · Source:
[`../../plans/loop-engineering.md`](../../plans/loop-engineering.md),
`features/board/runtime/`

How one agent turn actually executes, and the abstraction that lets the LLM and
CLI paths share one contract.

## Why the `AgentWorker` abstraction

Originally there were **two completely separate execution code paths** —
`_run_graph` (LangGraph LLM agent) and `_run_direct_cli` (CLI over ACP) — that
only met at the event store. Any capability added to one (loop control,
evaluation, idle timeout) had to be reimplemented for the other.

**Decision:** unify both behind a single `AgentWorker` contract. A worker drives
**exactly one turn** and emits frames. "One turn" is the right granularity because
the inner think→act→observe loop is already owned by LangGraph (LLM) or by the CLI
itself (ACP) — we don't reimplement it.

```python
# runtime/workers/base.py
class WorkerRole(StrEnum):
    CHAT = "chat"; GENERATOR = "generator"; EVALUATOR = "evaluator"; SUMMARIZER = "summarizer"

class AgentWorker(Protocol):
    async def run_turn(self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event) -> TurnResult: ...
```

Two implementations under `runtime/workers/`:

- **`LlmGraphWorker`** (`llm_graph.py`) — wraps `graph_builder.build_graph` +
  checkpointer + `StreamTranslator`.
- **`AcpCliWorker`** (`acp_cli.py`) — wraps `DirectCliRun` over the shared ACP
  infra, with an **idle timeout** and a **permission mode** (`auto`/`read_only`).

`registry.resolve_worker(alias, role)` picks one: `cli:*` → `AcpCliWorker`, else
`LlmGraphWorker`. `local_backend._drive` collapses to: *load context → resolve
worker → `run_turn` → finalize*.

### Why `emit` instead of returning frames

The backend persists each frame as it arrives (so SSE can tail a live run) and
offloads large tool output out-of-band (`_persist_tool_output` → `ToolOutput`).
Passing an `emit` callback keeps that streaming behaviour and keeps the worker
ignorant of persistence. The **same callback is reused by the evaluator** and any
future worker.

## The pieces (what we keep, do not rebuild)

| Component | File | Role |
|---|---|---|
| Event store (append-only, monotonic `seq`) | `runtime/event_store.py` | source of truth for replay + SSE |
| Streaming event contract | `runtime/events.py` | wire frames (`text_delta`, `tool_use_*`, …) |
| LangGraph → frames translator | `runtime/translator.py` | LLM stream parsing |
| Run backend Protocol | `runtime/backend.py` | `start` / `cancel` / `reconcile_orphans` |
| Local backend (the driver) | `runtime/local_backend.py` | builds context, drives the worker, finalizes |
| In-process run registry | `runtime/registry.py` | same-process cancel fast-path |
| Thread bridge | `runtime/dispatch.py` | start runs from the autopilot thread; `capture_main_loop()` |
| Context builders | `runtime/context.py`, `runtime/cli_context.py` | per-turn agent input |
| Shared ACP infra | `plugins/ai_code/tools/_acp_base.py` | session pool, resume, reaper, permission routing |
| Run service | `runtime/run_service.py` | start/list/read runs at the service layer |

Because every code path emits the **same frames**, the frontend/SSE **never has
to change** as new workers and the loop are added.

## ACP specifics (direct-CLI agents)

- **Idle timeout** is enforced at the worker level (no `_acp_base` change): the
  deadline resets on *any* frame (text, thinking, tool start/progress, usage), so
  a steadily-working agent runs as long as it produces output; a wedged turn stops
  with a clear "no activity" error. A 3h hard ceiling is the absolute backstop.
- **Permission mode**: `auto` (approve everything, default) maps to
  `auto_approve=True`; `read_only` denies mutating permission requests.
- **Session resume**: `_acp_base` persists the ACP `session_id` keyed by
  `cli:<engine>::<thread_id>` and resumes via `session/load` on a cache miss
  (cold start / restart / dead subprocess). Local helpers live in `runtime/acp/`
  and `runtime/direct_acp.py`.

## Cancel & orphan recovery

- Same-process cancel: the registry flips the run's `cancel_event`.
- Cross-process cancel: the worker polls `is_cancel_requested` (DB) on a cadence.
- A run left non-terminal by a dead process is reconciled to `error` on the next
  `on_startup` (`reconcile_orphans_sync`).

## Related

- The loop that drives many turns to completion →
  [`autonomous-loop.md`](autonomous-loop.md)
- The tools agents get → [`agent-tools-and-autopilot.md`](agent-tools-and-autopilot.md)
