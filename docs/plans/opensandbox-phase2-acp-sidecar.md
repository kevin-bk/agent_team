# OpenSandbox Phase 2 — ACP sidecar bridge (Strategy B)

Status: **implemented** (code + image + tests). Needs a live OpenSandbox server
to validate end-to-end.

Companion to `opensandbox-runtime-implementation-plan.md` (Phase 1). Phase 1 gave
per-task isolation with a one-shot CLI (`SandboxedCliWorker`). Phase 2 restores
**full ACP fidelity** inside that isolation.

---

## 1. Why

Phase 1 runs the CLI non-interactively (`claude -p …`, `codex exec --json`) and
parses stdout. That loses the ACP richness the cockpit renders on the host path:

- live **plan checklist** (`plan_update`),
- interactive **permission** answering,
- **MCP passthrough** (per-board MCP servers),
- the exact tool-card / thinking / usage stream `DirectCliRun` produces.

For a 24/7 loop most runs are unattended, so Phase 1 is fine as the default. Phase
2 is opt-in for boards/tasks that want parity with the host ACP experience while
still being isolated + idle/pausable.

## 2. Shape

```
host process                          sandbox (one per task)
────────────                          ──────────────────────
SidecarAcpWorker                      agent-team-runtime-server (FastAPI)
  prepare_task_sandbox() ───────────►   (started on demand, idempotent)
  open_sidecar_channel() ─ exec ────►   GET  /healthz
  get_endpoint(port) ── proxy ──────►   WS   /acp
  websockets.connect(ws_url)             │
     └─ send turn_request ──────────────┤► DirectCliRun.stream_frames(cancel)
     ◄──── frame / frame / … ───────────┤     (same class as the host path)
     ◄──── result ──────────────────────┘
  emit(event,data) → SSE → cockpit
  pause_task_sandbox()
```

Key idea: the sidecar reuses **the same `DirectCliRun`** the host uses, just
executed next to the workspace + CLI binary. Every `(event_type, data)` frame is
relayed to `emit` unchanged, so the frontend needs zero changes.

## 3. Pieces (all under `features/board/runtime/`)

| Piece | File | Role |
|---|---|---|
| Strategy flag | `sandbox/config.py` | `runtime_strategy` (`oneshot` \| `acp_sidecar`), `sidecar_port`, `is_acp_sidecar` |
| Wire protocol | `sandbox/sidecar_protocol.py` | JSON messages `turn` / `cancel` / `hello` / `frame` / `result` / `error` (shared host+server) |
| Channel setup | `sandbox/service.py` | `open_sidecar_channel()` — start server (idempotent) + resolve WS URL via `get_endpoint` |
| Host worker | `workers/sidecar_acp.py` | `SidecarAcpWorker` — connect, stream, cancel, mask, pause |
| Dispatch | `workers/registry.py` | `is_acp_sidecar → SidecarAcpWorker`, else `is_sandboxed → SandboxedCliWorker`, else host `AcpCliWorker` |
| Server | `infra/runtime/server/agent_team_runtime_server.py` | in-sandbox FastAPI bridge reusing `DirectCliRun` |
| Image | `infra/runtime/images/full.Dockerfile` | single image: CLIs + server + `agent_team` runtime subtree + stdlib SQLite store |
| Session store | `acp/store.py` | pluggable: host `core.database`, sandbox stdlib-`sqlite3` (`AGENT_TEAM_ACP_STORE_DB`) |

## 4. Wire protocol

One WebSocket per turn (`/acp`). Messages are compact JSON objects keyed by
`type`:

- host → server: `turn` (engine, prompt, cwd, thread_id, auto_approve,
  idle_timeout_seconds, mcp_config, secrets); `cancel`.
- server → host: `hello` (engines); `frame` (`event`, `data` — one AgentEvent);
  `result` (final_text, cancelled, ok, usage, cli_usage_text); `error`.

Versioned via `PROTOCOL_VERSION`; keep host + image in lockstep.

## 5. Lifecycle & control

- **Start**: `open_sidecar_channel` execs an idempotent shell snippet — if
  `/healthz` already answers it exits, else `nohup agent-team-runtime-server &`
  and polls health (≤70s, under a 90s exec budget) for cold start.
- **Endpoint**: `get_endpoint(sidecar_port)` → coerced to `ws(s)://…/acp`.
- **Cancel**: host polls the DB + the in-turn `cancel` event; on cancel it sends
  a `cancel` message; the server drives `DirectCliRun`'s graceful cancel and
  returns a `result`.
- **Idle/pause**: after each turn the sandbox is paused (`pause_task_sandbox`);
  the manager's idle-GC and the runtime's idle-close still apply.
- **Session state**: ACP persists sessions via the pluggable `acp/store.py`.
  Inside the sandbox `AGENT_TEAM_ACP_STORE_DB=/var/lib/agent-team/acp-sessions.db`
  selects a **stdlib-`sqlite3`** backend (no `core`/`plugins` import); the table
  is created lazily on first use, so per-task session state survives pause/resume
  next to the CLI.

## 6. Image

**One** image, `runtime-full`, supports both strategies. On top of the CLIs it
adds `fastapi`/`uvicorn`/`agent-client-protocol`, the `agent-team-runtime-server`
console script, and only the **`agent_team` runtime subtree** (ACP stack +
`sidecar_protocol` + `events`) — **not** `src/`, `core`, or `plugins`. Build
context is the **plugin root**.

This was decoupled deliberately: the sole app coupling was `acp/store.py`
(session persistence). Making that backend pluggable (stdlib SQLite in-sandbox)
removed the need to ship `src/` + `pydantic-settings`/`dotenv`, so the sidecar
adds a small, dependency-light layer to the one image.

## 7. Enable

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox
AGENT_TEAM_RUNTIME_STRATEGY=acp_sidecar
AGENT_TEAM_RUNTIME_IMAGE=<registry>/runtime-full:v1
AGENT_TEAM_RUNTIME_SIDECAR_PORT=8871
OPEN_SANDBOX_DOMAIN=https://<server>
OPEN_SANDBOX_API_KEY=<key>
```

Per-board override via `runtime_profile_json` (`{"runtime_strategy":"acp_sidecar"}`).
The cockpit **Runtime** card shows `Mode: ACP sidecar (full)` vs `one-shot CLI`.

## 8. Tests

`tests/test_sandbox_runtime.py`:

- protocol roundtrip;
- `SidecarAcpWorker` relays frames from an in-process fake WS server (asserts
  frame count, final_text, usage, pause);
- strict-mode failure surfaces an error and never falls back to host.

Not covered by unit tests (needs live infra): real `get_endpoint` proxy shape,
in-sandbox server cold start, ACP subprocess over the bridge.

## 9. Validation checklist (operator, on the server)

1. `build-runtime-images.sh full`; push to a reachable registry.
2. Set the env above on a staging board; run a `cli:claude`/`cli:codex` task.
3. Confirm the cockpit shows live plan checklist + tool cards (not just text).
4. Kill a run mid-flight → cancel propagates; sandbox pauses after.
5. Second run on the same task resumes the paused sandbox (state preserved).
6. Leave idle > `idle_timeout` → sandbox is reaped; next run reopens cleanly.

## 10. Follow-ups

Done since the first draft:

- ✅ Board-settings UI to edit the runtime profile (provider/strategy/resources).
- ✅ Manual pause/kill controls in the Runtime card (guarded while a run is live).
- ✅ Single image + slimmer sidecar: vendor only the ACP subtree (no `src/`);
  session store decoupled to a stdlib SQLite backend.

Open:

- Sync workspace mode (vs bind mount) for remote OpenSandbox hosts.
- Optionally promote the runtime subtree to a standalone `agent-team-runtime`
  wheel (this refactor already made that a file-move, not a rewrite).
