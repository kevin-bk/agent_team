# agent_team — isolated runtime images

Sandbox images that run agent_team's direct-CLI engines (`claude`, `codex`)
inside an [OpenSandbox](https://github.com/) sandbox, one per task, with
idle/pause to save resources.

This directory is **operator work**: the app never builds images at runtime.
Build them once on your Docker host, push to a registry the OpenSandbox server
can pull from, then point the runtime profile at the tag.

## Layout

```
infra/runtime/images/full.Dockerfile          # single image: CLIs + ACP sidecar
infra/runtime/server/                         # agent-team-runtime-server (sidecar)
infra/runtime/.env.example                    # runtime env defaults (copy → app .env)
infra/runtime/docker-compose.opensandbox.yml  # run an OpenSandbox server on your host
infra/runtime/opensandbox/config.toml         # server config mounted by the compose file
scripts/build-runtime-images.sh               # build/push helper
```

There is **one** image (`runtime-full`) that supports both execution strategies
(`oneshot` and `acp_sidecar`). The ACP sidecar reuses only the `agent_team`
runtime subtree (ACP stack + protocol) baked into the image — no `src/`, no
`core`/`plugins`.

## Run an OpenSandbox server

On the host that should run the task sandboxes (can be the same box as the app):

```bash
cd infra/runtime
cp .env.example .env                                  # edit binding / keys
docker compose -f docker-compose.opensandbox.yml up -d
curl http://localhost:8090/                           # readiness check
```

Then set `OPEN_SANDBOX_DOMAIN` in the app's `.env` to this server's URL. See
`.env.example` for every runtime variable with inline docs.

## Build

On the build server (must be able to reach Docker + npm + the OpenSandbox base
image):

```bash
# Build the "full" image → agent-team/runtime-full:v1 (+ :latest)
./scripts/build-runtime-images.sh full

# Push under your registry/account
REGISTRY=myuser PUSH=1 ./scripts/build-runtime-images.sh full

# Pin CLI versions baked into the image
CLAUDE_CODE_VERSION=2.1.146 CODEX_VERSION=0.9.0 \
  ./scripts/build-runtime-images.sh full
```

> **Apple Silicon note.** Keep the default `BUILD_PLATFORM=linux/amd64`. Running
> ARM binaries under QEMU on an amd64 OpenSandbox host makes `execd` hang
> ("Sandbox health check timed out"). Set `BUILD_PLATFORM=linux/arm64` only when
> the server itself is arm64.

## What's in the image

- OpenSandbox base (`opensandbox/code-interpreter`, clone3/fix1 tag).
- Node + npm, Python, git, gh, ripgrep, fd, jq, tree, curl, bubblewrap, socat.
- CLIs: `claude` (`@anthropic-ai/claude-code`), `codex` (`@openai/codex`).
- Claude Code headless config baked in (bypass-permissions accepted, its own
  bwrap sandbox disabled — we are already isolated).
- ACP sidecar: `agent-team-runtime-server` on PATH + the `agent_team` runtime
  subtree (ACP stack) + `fastapi`/`uvicorn`/`agent-client-protocol`. Session
  state lives in a sandbox-local SQLite (`AGENT_TEAM_ACP_STORE_DB`).
- Mount points: `/workspace` (task working copy), `/skills`.

Everything is baked in — nothing is installed on task start.

## Wire it up

Point the process env at the provider + image (see
`features/board/runtime/sandbox/config.py` for the full list):

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox
AGENT_TEAM_RUNTIME_IMAGE=myuser/runtime-full:v1     # default: agent-team/runtime-full:latest
OPEN_SANDBOX_DOMAIN=https://<your-opensandbox-server>
OPEN_SANDBOX_API_KEY=<key>

# Optional tuning
AGENT_TEAM_RUNTIME_CPU=2
AGENT_TEAM_RUNTIME_MEMORY_MB=4096
AGENT_TEAM_RUNTIME_IDLE_MINUTES=30        # pause a sandbox after N idle minutes
AGENT_TEAM_RUNTIME_WORKSPACE_MODE=mount   # mount | sync
AGENT_TEAM_RUNTIME_STRICT=1               # fail the run instead of host fallback
```

A board can override any of these via its `runtime_profile_json` column
(migration `029_board_runtime_profile.sql`); board values overlay the env
defaults. The cockpit **Runtime** card shows the effective profile and the live
sandbox state per task.

## Two runtime strategies

`AGENT_TEAM_RUNTIME_STRATEGY` selects how a `cli:` engine is driven *inside* the
sandbox:

| Strategy | Worker | Fidelity |
|---|---|---|
| `oneshot` (default) | `SandboxedCliWorker` | Non-interactive print mode; text / tool / usage frames. Enough for unattended runs. |
| `acp_sidecar` | `SidecarAcpWorker` | Full ACP: live plan checklist, tool cards, thinking, MCP passthrough — same `DirectCliRun` as the host, run next to the workspace. |

Both strategies use the **same** `runtime-full` image — the sidecar is baked in.

Enable the sidecar strategy:

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox
AGENT_TEAM_RUNTIME_STRATEGY=acp_sidecar
AGENT_TEAM_RUNTIME_IMAGE=myuser/runtime-full:v1
AGENT_TEAM_RUNTIME_SIDECAR_PORT=8871      # in-sandbox server port (proxied to host)
```

How it works: on the first turn the host starts `agent-team-runtime-server`
inside the sandbox (idempotent), resolves its port through the OpenSandbox proxy
(`get_endpoint`), opens a WebSocket, and relays every ACP frame to the cockpit
unchanged. ACP session state persists to a **sandbox-local SQLite**
(`AGENT_TEAM_ACP_STORE_DB=/var/lib/agent-team/acp-sessions.db`, baked into the
image) via the stdlib-only store backend, so it survives pause/resume next to the
CLI without dragging any app code into the image.

## CLI secrets

Each engine still needs its own model credentials inside the sandbox
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` / Codex auth). Pass them through the
runtime profile's `env` (masked in streamed output) or via the OpenSandbox
server config — do **not** bake secrets into the image.
