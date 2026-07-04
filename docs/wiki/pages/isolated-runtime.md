# Isolated runtime (OpenSandbox)

Last updated: 2026-07-02 · [↩ index](../index.md) · Source:
[`../../plans/opensandbox-runtime-implementation-plan.md`](../../plans/opensandbox-runtime-implementation-plan.md),
[`../../plans/opensandbox-phase2-acp-sidecar.md`](../../plans/opensandbox-phase2-acp-sidecar.md),
`features/board/runtime/sandbox/`

Give each task its **own isolated execution environment** (an OpenSandbox
sandbox) that can **idle/pause** when no agent is running on it — so a 24/7 loop
across many tasks doesn't melt the host and costs nothing while idle. Isolation
is **opt-in per process/board**; leave it off and everything runs on the host
exactly as before.

## Why & locked decisions

- **Seam = `AgentWorker`.** Isolation plugs in at the worker layer (see
  [`runtime-and-runs.md`](runtime-and-runs.md)); the event store, SSE, and UI are
  untouched. Only `cli:*` agents are isolated — LLM-graph agents run in-process.
- **Provider-agnostic.** A `RuntimeProfile` (resolved from env, then a board
  override) decides `provider` (`local` | `opensandbox`), image, resources, idle
  timeout, workspace mode, strategy. `local` = current host behaviour.
- **One sandbox per task**, reused across a task's runs; **paused after each
  turn**, resumed on the next, GC-reaped when idle. Sibling tasks never share a
  workspace.
- **Two execution strategies** (why: ACP is bidirectional over stdio, but a
  sandbox exposes a one-shot command API):
  - **`oneshot` (Phase 1, default)** — run the CLI non-interactively
    (`claude -p … --output-format stream-json`, `codex exec --json`) and parse
    the stream into the same frames. Enough for unattended runs.
  - **`acp_sidecar` (Phase 2)** — an in-sandbox server owns the ACP subprocess
    and bridges the **full** ACP frame stream (live plan checklist, tool cards,
    thinking, MCP passthrough) to the host over a WebSocket.
- **Strict isolation never silently falls back to the host.** If the sandbox
  can't be prepared and `strict_isolation` is set (or `allow_fallback` is off),
  the turn errors; only an explicit `allow_fallback` profile runs the host CLI
  (and records that it did).
- **Reuse over reinvention.** The sandbox layer (base/local/opensandbox/manager)
  is ported from the sibling `deep-agent` project; the Phase-2 sidecar reuses the
  **same `DirectCliRun`** the host ACP path uses.

## Modules (`features/board/runtime/sandbox/`)

| File | Role |
|---|---|
| `base.py` | `Sandbox` ABC + `ExecResult` + error hierarchy; `exec_shell`, file ops, `open/close/pause/resume`. |
| `local.py` | `LocalSandbox` — host subprocess, **no isolation** (dev/tests/parity). |
| `opensandbox.py` | `OpenSandboxRuntime` — the SDK-backed isolated runtime: keepalive/TTL renew, idle-close, pause/resume, volume mounts. |
| `config.py` | `RuntimeProfile` + `VolumeMount` dataclasses; `profile_from_env()`. |
| `factory.py` | `build_sandbox(profile, …)` → concrete runtime. |
| `manager.py` | `SandboxManager` — per-task tracking, capacity cap, **idle-GC** + `pin_until`. |
| `service.py` | process-wide manager, `resolve_profile` (board overlay → env), `prepare_task_sandbox` (open/resume/reopen), `pause_task_sandbox`, `kill_task_sandbox`, `open_sidecar_channel`, `describe_runtime`. |
| `cli_exec.py` | Phase-1 one-shot specs + stream→frame translators (`claude`, `codex`, `cursor`). |
| `sidecar_protocol.py` | Phase-2 host⇄server WebSocket JSON protocol. |

Workers (`features/board/runtime/workers/`): `registry.resolve_worker` picks
`SidecarAcpWorker` (acp_sidecar) → `SandboxedCliWorker` (oneshot) → host
`AcpCliWorker`, keyed on the resolved profile.

## Flow (one turn)

1. `resolve_worker(alias, role, board_id)` resolves the profile and returns the
   worker for the strategy.
2. Worker calls `prepare_task_sandbox(task_id, workspace, profile)` → open a
   fresh sandbox (bind-mount the task workspace at `/workspace`), resume a paused
   one, or reopen a dead one.
3. **oneshot:** `exec_shell(argv)` with an `on_stdout` line callback →
   `cli_exec` translator → `emit(event, data)`.
   **acp_sidecar:** `open_sidecar_channel` (start server, resolve `ws://…/acp`)
   → send `turn` → relay `frame`s → `result`.
4. Cancel: poll the DB + the turn's cancel event; oneshot stops waiting,
   acp_sidecar sends a `cancel` message.
5. `pause_task_sandbox(task_id)` — idle task now costs nothing.

## Config (env; board can override via `runtime_profile_json`)

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox        # default: local (host)
AGENT_TEAM_RUNTIME_STRATEGY=oneshot            # oneshot | acp_sidecar
AGENT_TEAM_RUNTIME_IMAGE=<reg>/runtime-full:v1 # one image for both strategies
AGENT_TEAM_RUNTIME_IDLE_MINUTES=30             # pause→reap idle sandboxes
AGENT_TEAM_RUNTIME_WORKSPACE_MODE=mount        # mount | sync
AGENT_TEAM_RUNTIME_STRICT=1                    # no host fallback
AGENT_TEAM_RUNTIME_CREDENTIAL_ACCOUNT=         # optional: default credential account name
OPEN_SANDBOX_DOMAIN=https://<server>
OPEN_SANDBOX_API_KEY=<key>
```

Board overlay: `runtime_profile_json` (migration `029_board_runtime_profile.sql`,
accessor `Board.runtime_profile()`) — any `RuntimeProfile` field overlays the env
default. `GET /api/tasks/{id}/runtime` (`describe_runtime`) powers the cockpit
**Runtime** card (provider / mode / image / resources / live sandbox state).

## Credentials (`features/board/runtime/credentials/`)

A coding CLI inside a sandbox needs auth. Instead of baking secrets into images,
register a **credential account** (admin, `Credentials` nav → `/credentials`)
and point a board at it (Board settings → isolated runtime → **Credential
account**, or `AGENT_TEAM_RUNTIME_CREDENTIAL_ACCOUNT`). See
[`../../plans/opensandbox-phase3-credentials.md`](../../plans/opensandbox-phase3-credentials.md).

- **No secret in the DB or API** — an account stores only a *reference*
  (`material_ref`): a host **env-var name** (`secret_env`) or a host **path**
  (`host_path` / `pvc_claim`). The real value is read from the host at inject time.
- **Provider-driven, backend-pluggable** (`registry.PROVIDER_REQUIREMENTS`):
  - **Claude Code** → `header_token`. Default backend **`env`** (injects
    `CLAUDE_CODE_OAUTH_TOKEN`, infra-free) — flip to **`vault`** once the server
    has Credential Vault egress so the real token never enters the sandbox (the
    egress proxy adds the `Authorization` header).
  - **Codex** → `config_dir`. Backend **`mount`** — the writable `$CODEX_HOME`
    (`auth.json`) is mounted so token refresh persists. Because a real secret
    then lives in the sandbox, **network egress is `deny`-by-default + allowlist**
    the provider hosts only, so a compromised agent can't exfiltrate it.
- **Adding a provider** = one descriptor entry; a new `header_token` provider
  (e.g. GitHub) reuses the `vault` backend with no coding-agent code changes.
- `prepare_task_sandbox` resolves the account (lazy `core.database` import),
  builds an `InjectionPlan` (env + mounts + network rules + vault bindings), and
  passes `network_policy`/`credential_proxy` to `opensandbox.open()`; vault
  bindings are written post-open via `write_vault()`.

## Image (`infra/runtime/`)

- `runtime-full` — **one** image for both strategies: claude + codex CLIs +
  `agent-team-runtime-server` + the `agent_team` runtime subtree (ACP stack +
  protocol only — no `src/`/`core`/`plugins`). ACP session state persists in a
  sandbox-local SQLite via the stdlib store backend (`AGENT_TEAM_ACP_STORE_DB`).

Built by `scripts/build-runtime-images.sh` (operator runs on their server; the
app never builds images at runtime). Build context = the plugin root. See
[`../../infra/runtime/README.md`](../../infra/runtime/README.md).

## Gotchas

- **Idle-GC must be running.** `prepare_task_sandbox` calls `manager.start_gc()`
  (idempotent); without a running loop the idle backstop is dead.
- **Dead tracked sandbox.** If a tracked sandbox is `closed`/`broken` (runtime
  idle-closed, or lost on restart) or resume fails, `prepare_task_sandbox` drops
  it and opens fresh — never returns a dead handle.
- **Sidecar cold start.** First `acp_sidecar` turn imports the ACP subtree +
  lazily creates the local SQLite; `open_sidecar_channel` allows ~70s health
  window. (No app/`src` import — lighter than the old app-source approach.)
- **Secrets** are masked in streamed frames, but each engine still needs its own
  model credentials **inside** the sandbox (via the profile `env` / server
  config) — never bake secrets into the image.
- **Phase 2 needs live infra.** Unit tests cover the protocol + host relay with a
  fake WS server; the real `get_endpoint` proxy + in-sandbox ACP path must be
  validated against a live OpenSandbox server.
- **SDK pin `opensandbox==0.1.13`** (PyPI latest; **no 0.2.x SDK** — "0.2.1" was
  a mix-up with the server image line). Import `Volume`/`Host`/`PVC` + vault
  models from the SDK *domain* layer `opensandbox.models.sandboxes`
  (UNSET-vs-None fix); see `opensandbox.py` header. Install plugin deps with
  `uv run setup-dependencies` (NOT bare `uv sync`, which prunes plugin packages).
- **ACP session store is pluggable** (`acp/store.py`). Host uses `core.database`
  (`plugin_ai_acp_sessions`); set `AGENT_TEAM_ACP_STORE_DB=<path>` to switch to a
  stdlib-`sqlite3` backend with **zero** `core`/`plugins` imports — this is what
  lets the sidecar image ship only the runtime subtree (no `src/`).
