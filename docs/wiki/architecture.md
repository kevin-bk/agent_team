# Architecture

Last updated: 2026-07-31 · [↩ index](index.md)

How the plugin is wired into agent-manager, and how a single agent turn flows
from an HTTP request to live UI updates.

## 1. Plugin anatomy (`plugin.py`)

`AgentTeamPlugin(PluginBase)` is the single entry point the core discovers. It
contributes:

| Hook | What it returns / does |
|---|---|
| `meta()` | name `agent_team`, version, description. |
| `models()` | every SQLAlchemy model (see [`data-model.md`](data-model.md)); the core registers them on one shared `Base`. |
| `routers()` | `platform_router`, `board_router`, `repos_router`, `comm_router`. |
| `tool_factories()` | the agent tools `view_image`, `git_push`, `set_task_status` (see [`pages/agent-tools-and-autopilot.md`](pages/agent-tools-and-autopilot.md)). |
| `asgi_apps()` | the SPA at `/agent-team`, plus an internal **loop-capture app** at `/_agent_team_internal`. |
| `menu_items()` | the "Agent Team" sidebar entry. |
| `on_startup()` / `on_shutdown()` | reconcile orphaned runs; start/stop the **repo-pull ticker**. |

When the plugin is **disabled**, `PluginDisabledMiddleware` blocks its routes and
the registry drops its tool factories — nothing else in agent-manager changes.

### The loop-capture app (a subtle but important detail)

`on_startup` runs at `create_app` time — **before the event loop exists**, and
under `uvicorn --workers`/PM2 possibly in a process that never serves the app. A
ticker started there could capture the wrong (or no) loop, so dispatched runs
would never execute.

The fix: a tiny mounted FastAPI app (`_build_loop_capture_app`) whose **lifespan**
runs *inside* a real serving worker's loop. It calls `capture_main_loop()` and
starts the **autopilot ticker** there, guaranteeing the ticker and the captured
loop share a process. The autopilot ticker's own `fcntl` lock still ensures only
one worker runs it. (The repo-pull ticker stays in `on_startup` because it does
pure-sync work and never touches the loop.)

## 2. Schema management

The plugin **never** runs `CREATE TABLE` itself for new columns. Schema lives in
`db_migrations/*.sql`, applied automatically by the **core migration runner**
before plugins start. New *tables* are also auto-created from the ORM metadata on
startup, so migrations use `IF NOT EXISTS` / `skip_if_*` directives and are a
no-op when the ORM already made the table. See
[`guides/development.md`](guides/development.md) for the migration conventions
(including the comment/semicolon parsing gotcha).

## 3. The run lifecycle (one agent turn)

This is the heart of the system. A "run" is **one turn** of one agent against one
task.

```
POST /tasks/{id}/mentions           (or autopilot / loop driver)
   │
   ▼
open/reuse Conversation for (task, agent)  ──►  create AgentTeamRun (status=running)
   │
   ▼
local_backend._drive(run):
   1. load run context (task header, description, agent-visible notes,
      workspace path, prepared repos)            ── runtime/context.py / cli_context.py
   2. resolve worker by agent alias               ── runtime/workers/registry.py
        cli:*  → AcpCliWorker (ACP)
        else   → LlmGraphWorker (LangGraph)
   3. worker.run_turn(ctx, emit, cancel)
        every frame  ── emit ──►  event_store.append_event(seq, type, data)
   4. finalize: final_answer, token/cost usage, terminal status
   │
   ▼
AgentTeamRunEvent rows  ──►  SSE stream tailing the event store  ──►  live UI
```

Key properties:

- **Append-only event store** (`runtime/event_store.py`) with a monotonic `seq`
  per run is the **source of truth** for both replay and live SSE. The frontend
  never has to change as new workers/loops are added — they all emit the same
  `AgentTeamRunEvent` frames (`text_delta`, `tool_use_*`, `usage`, …).
- **`emit` callback, not return values.** The worker is handed an `emit` closure
  so it stays ignorant of persistence (and large tool output is offloaded
  out-of-band). The same callback is reused by the evaluator and any future
  worker.
- **Delta-only follow-ups.** Re-mentioning reuses the same conversation thread, so
  prior context stays in history; each new turn carries only the *delta* (new
  notes / changed description), keeping the prompt prefix cache-friendly.
- **Cancel & orphan recovery.** An in-process registry (`runtime/registry.py`)
  gives a same-process cancel fast-path; cross-process cancel is polled via the
  DB. Runs left non-terminal by a dead process are reconciled to `error` on the
  next `on_startup`.

See [`pages/runtime-and-runs.md`](pages/runtime-and-runs.md) for the worker
contract in detail.

## 4. Processes, tickers, and concurrency

Three background concerns run *inside* the serving process(es):

| Ticker | Where started | Lock | Job |
|---|---|---|---|
| **Repo-pull** (`features/repos/scheduler.py`) | `on_startup` | `fcntl` file lock | scheduled `git pull` of canonical repo clones |
| **Autopilot** (`features/board/autopilot_scheduler.py`) | loop-capture app lifespan | `fcntl` file lock | dispatch agent runs for assigned tasks on a schedule |

Both use an `fcntl` file lock so that under multi-worker deployments only one
worker actually runs the ticker. Git/subprocess work is pushed to
`asyncio.to_thread` so it never blocks the event loop.

## 5. Auth

The plugin reuses the **core session cookie**. Unauthenticated API calls get a
JSON `401`. Per-resource authorization (admin vs board owner/editor/viewer) is in
`features/board/authz.py` and enforced in the routers — see the role rules called
out on each subsystem page.

## 6. Frontend

`web-ui/` is a React + Vite + TypeScript SPA. The plugin serves only the **built
bundle** from `static/`. The API client/hooks/types live under `web-ui/src/api/`
(`client.ts`, `hooks.ts`, `types.ts`) and the board cockpit under
`web-ui/src/features/`. Rebuild with `npm run build:agent-team` (copies
`dist-agent-team/` → `../static/`). The board and task views update live over SSE.

The top-level **Guide** view is also part of this SPA. Its source content remains
in `user-guide/*.md`; Vite imports the Markdown and screenshots at build time,
and the reader serves them at `/agent-team/guide/:slug`. It does not introduce a
runtime Markdown endpoint. See
[`pages/user-guide.md`](pages/user-guide.md) for the content contract and build
pipeline.
