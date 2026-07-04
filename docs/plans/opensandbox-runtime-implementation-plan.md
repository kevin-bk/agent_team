# OpenSandbox Runtime — Implementation Plan (agent_team)

Status: Active implementation plan.
Companion to the north-star design: [`opensandbox-isolated-runtime.md`](opensandbox-isolated-runtime.md).
Audience: engineers (human + agent) implementing task-scoped isolated execution
for `community_plugins/agent_team`.

This plan turns the north-star doc into concrete, buildable slices grounded in
the **current code**. It reuses the mature sandbox layer already proven in the
sibling `deep-agent` project instead of rewriting it.

---

## 0. Goal (what "done" means for the first milestone)

- Each task can run its coding agent inside a **task-scoped OpenSandbox**, not on
  the host process.
- A task's sandbox **pauses / is reaped when idle** so an idle task costs no
  CPU/RAM (the user's headline requirement).
- **Strict isolation never silently falls back to host.**
- The **frontend / SSE / event store / journal / Code-review** surfaces do **not
  change** — the sandboxed worker emits the exact same `AgentEvent` frames.

---

## 1. Why this is low-risk (the seam already exists)

The runtime already unifies every execution path behind one contract:

```
local_backend._drive → resolve_worker(alias, role) → worker.run_turn(ctx, emit, cancel)
                                                        └─ emits AgentEvent frames → event_store → SSE
```

So adding isolation = **adding one more `AgentWorker`** and choosing it in
`resolve_worker`. Nothing downstream of `emit` needs to know a sandbox exists.

Reused, near-verbatim, from `deep-agent` (`deep_agent/sandbox/`):

| deep-agent file | What we port | Why it is ready |
|---|---|---|
| `sandbox/base.py` | `Sandbox` ABC, `ExecResult`, error hierarchy, state machine | Provider-agnostic, no deep-agent coupling |
| `sandbox/opensandbox.py` | `OpenSandboxRuntime`: keepalive TTL renew, **idle-close**, **pause/resume/resume_existing**, volume mounts, `env_blocklist`, exception mapping, `opensandbox 0.1.9` UNSET-vs-None Volume fix | Battle-tested against the same SDK version this repo pins |
| `sandbox/local.py` | `LocalSandbox` (host, no isolation) for tests / dev parity | — |
| `sandbox/manager.py` | `SandboxManager`: **one sandbox per task**, capacity semaphore, **idle GC reaper**, `pin_until` | Directly implements "per-task + idle" |

What we do **not** take from deep-agent: its Strategy-A stream-json coupling as
the *only* option. We keep a path to the ACP sidecar (Phase 2) so agent_team can
exceed deep-agent on richness.

---

## 2. The core problem & our staged answer

ACP is **bidirectional JSON-RPC over stdio** on a long-lived subprocess.
OpenSandbox `commands.run()` streams stdout of a command — it is not a stdio
duplex. So `DirectCliRun` cannot be dropped into a sandbox unchanged.

| Phase | How the CLI runs in the sandbox | Trade-off |
|---|---|---|
| **Phase 1 (this plan)** | **Strategy A** — one-shot non-interactive command (`claude -p … --output-format stream-json`) via `sandbox.exec_shell(..., on_stdout=…)`; parse stream → frames | Loses ACP interactive permission / live plan checklist / MCP passthrough. Acceptable: agent_team CLI runs are already unattended `auto_approve=True`. |
| **Phase 2 (later)** | **Strategy B** — `agent-team-runtime-server` sidecar in the image owns the ACP subprocess, bridges frames to host over WebSocket | Restores full ACP richness; needs a custom image + protocol. |

Phase 1 proves the **isolation boundary + idle/pause** cheaply; Phase 2 restores
fidelity without touching the loop.

---

## 3. Target module layout (new files under the plugin)

```
features/board/runtime/
├── sandbox/                     # NEW — ported + adapted from deep-agent
│   ├── __init__.py
│   ├── base.py                  # Sandbox ABC, ExecResult, errors  (port)
│   ├── local.py                 # LocalSandbox                      (port)
│   ├── opensandbox.py           # OpenSandboxRuntime                (port, keep 0.1.9 fixes)
│   ├── manager.py               # SandboxManager (per-task + GC)    (port, strip profile deps)
│   ├── config.py                # RuntimeProfile / VolumeMount dataclasses (NEW, replaces pydantic profile)
│   ├── factory.py               # build_sandbox(profile)            (adapt)
│   └── cli_exec.py              # one-shot argv + stream parser → AgentEvent frames (NEW)
└── workers/
    └── sandboxed_cli.py         # NEW — SandboxedCliWorker(AgentWorker)
```

Deliberately **not** reusing `src/plugins/sandbox_integration/` as the runtime —
it is a LangChain tool surface only (confirmed) and cannot host the CLI.

---

## 4. Configuration model

Follow the existing board JSON-column precedent (`agent_mcp_json`).

- **Env defaults** (works with zero DB change, good for the first bring-up):
  - `AGENT_TEAM_RUNTIME_PROVIDER` = `local` (default) | `opensandbox`
  - `AGENT_TEAM_RUNTIME_IMAGE`, `AGENT_TEAM_RUNTIME_CPU`, `AGENT_TEAM_RUNTIME_MEMORY`
  - `AGENT_TEAM_RUNTIME_IDLE_MINUTES` (default 30), `AGENT_TEAM_RUNTIME_TIMEOUT_MINUTES` (default 180)
  - `AGENT_TEAM_RUNTIME_STRICT` = `0`/`1`
  - `OPEN_SANDBOX_DOMAIN` / `OPEN_SANDBOX_API_KEY` (already used by `sandbox_integration`)
- **Board override** (Phase 1b): add `runtime_profile_json` column on
  `AgentTeamBoard` + accessor `runtime_profile()`, migration
  `db_migrations/NNN_board_runtime_profile.sql` (mirrors `022_board_agent_mcp.sql`).
- **Resolution order:** run request → task → board → env default.

`RuntimeProfile` dataclass (config.py) fields:
`provider, image, snapshot_id, cpu, memory, timeout_minutes, idle_timeout_minutes,
ready_timeout_seconds, workspace_mode(mount|sync), volume_mode, network_policy,
env, env_blocklist, strict_isolation, pool_enabled, pool_max_idle`.

---

## 5. Execution flow (Phase 1, mount mode, one task)

```
mention/loop → local_backend._drive
  resolve_worker(alias, role):
    provider == local       → AcpCliWorker / LlmGraphWorker   (unchanged)
    provider == opensandbox → SandboxedCliWorker
       SandboxManager.open_for_task(task_id):
          acquire capacity slot → build_sandbox(profile)
          mount host workspace_path → /workspace  (mount mode)
          sandbox.open()  (keepalive starts; renews TTL, idle-closes)
       argv = cli_exec.build_argv(engine, prompt, workdir=/workspace)
       await sandbox.exec_shell(argv, cwd=/workspace, on_stdout=translate→emit)
       accumulate usage/final_text from parsed events
       sandbox.pause()            # sleep after the turn → save resources
       return TurnResult(...)
  finalize run exactly as today (event_store, SSE, journal)
```

Idle/pause is delivered by two layers already present in the ported code:
- `OpenSandboxRuntime._keepalive_loop`: renews TTL while used; **kills when idle
  > idle_timeout**.
- `SandboxManager` GC: reaps sandboxes untouched past `idle_ttl_seconds`;
  `pin_until` keeps one warm while a task waits for human review.

Between runs we `pause()`; on the next mention we `resume_existing(sandbox_id)`
(the id is persisted — see §7) or open fresh.

---

## 6. Strict-mode & failure handling (no silent host fallback)

- If `strict_isolation` and sandbox `open()` / health fails →
  mark run `error` with a clear message; the loop sets task
  `waiting_for_human` (or `failed`). **Never** run the CLI on the host.
- Non-strict + explicit `allow_fallback` → run local, but the run/journal must
  record "fell back to local runtime".
- CLI missing in image, OOM, timeout, network-denied → surface as a
  human-visible run error (do not mark the task complete).

---

## 7. Sandbox session tracking (state)

Phase 1a: keep the `sandbox_id` in memory in `SandboxManager` (survives within a
process). Phase 1b: persist per-task/run in a small table
`AgentTeamSandboxSession` (task_id, run_id, sandbox_id, status, image,
workspace_mode, timestamps, last_error) so:
- pause/resume survives a process restart (`resume_existing`);
- the cockpit can show sandbox id / status / logs.

Distinct from `LoopState` (that is public task lifecycle; this is runtime
metadata).

---

## 8. Secrets & git in the sandbox

- Inject only per-run minimum secrets as **files** under
  `/run/agent-team/secrets/` (0600), not broad env; keep `env_blocklist`
  (e.g. `ANTHROPIC_API_KEY` when using OAuth).
- The host `git_cred_helper` will **not** work inside the sandbox (it points at
  host Python paths). Provide sandbox-native credentials: write a temp helper /
  `.netrc` / `GIT_SSH_COMMAND` scoped to the run; never persist into the worktree.
- Reuse `SecretMasker` at the emit boundary so tokens never reach frames/journal.

---

## 9. Runtime image (operator work, tracked here)

`agent-team/runtime-*` images built from a Linux base with: bash/git/curl/jq,
Python+uv, Node+npm, common build tools, the chosen coding CLIs, and (Phase 2)
`agent-team-runtime-server`. Profiles: `base`, `web`, `python`, `full`.
Bake deps into the image/snapshot; do **not** install on every task start.

---

## 10. Slices & acceptance

| Slice | Deliverable | Acceptance test |
|---|---|---|
| **S1** | Port `sandbox/` (base, local, opensandbox, manager, config, factory) | Unit tests: LocalSandbox exec; OpenSandboxRuntime open/exec/pause/resume/close against a **fake SDK**; manager per-task + GC idle reap |
| **S2** | `cli_exec.py` one-shot argv + stream parser (Claude first) | Parser maps a recorded `stream-json` transcript → correct frame sequence |
| **S3** | `SandboxedCliWorker` + `resolve_worker` wiring + config resolution | With provider=local, behaviour byte-identical (parity test). With provider=opensandbox + fake sandbox, `run_turn` emits frames + pauses after turn |
| **S4** | Strict-mode no-fallback + run-error surfacing | Sandbox open failure in strict mode → run `error`, host CLI never spawned (assert) |
| **S5** | Board `runtime_profile_json` column + migration + cockpit read-only status panel | Profile resolves board→env; cockpit shows provider/sandbox id/status |
| **S6** (later) | ACP sidecar image + `agent-team-runtime-server` + Strategy-B provider | Full ACP frames preserved through the bridge |

S1–S4 need no live OpenSandbox server (fakes). S5+ needs a local Docker-backed
OpenSandbox server + a runtime image.

---

## 11. Decisions taken (autonomous, revisit if wrong)

- **Engine first: Claude** (`claude -p --output-format stream-json`) — deep-agent
  ships a proven parser to port; lowest risk. Codex/Cursor one-shot parsers follow.
- **Mount mode first** (local Docker OpenSandbox) — maps onto the existing
  per-task host workspace + Code-review git diff with no changes. Sync mode is
  designed-for but implemented later.
- **CLI agents first**; LLM graph agents keep running on host in Phase 1 (harder
  to isolate in-process) and show a "running on host" badge.
- **Config via env first, board column second** — unblocks bring-up without a
  migration, then promotes to per-board config.

---

## 12. Open items to confirm with the owner

- Which engine is the priority for the first sandboxed run (Claude vs Codex —
  Codex is the current "owned" ACP default)?
- Local Docker-backed OpenSandbox first, or straight to remote/K8s?
- Can we build & host a custom `agent-team-runtime` image (needed for real runs)?
- Which secrets must exist in the first board's sandbox (LLM key, git token)?
