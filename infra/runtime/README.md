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
infra/runtime/.env.example                    # runtime vars → copy into agent-manager/.env
infra/runtime/docker-compose.opensandbox.yml  # run an OpenSandbox server on your host
infra/runtime/docker-compose.postgres.yml     # run the app Postgres (reuses existing volume)
infra/runtime/postgres/schema.sql             # first-boot DB init (fresh volume only)
infra/runtime/opensandbox/config.toml.example # server config template (copy → config.toml)
infra/runtime/opensandbox/config.toml         # your server config (gitignored — holds api_key)
scripts/build-runtime-images.sh               # build/push helper
```

There is **one** image (`agent-team-sandbox`, built from `full.Dockerfile`) that
supports both execution strategies
(`oneshot` and `acp_sidecar`). The ACP sidecar reuses only the `agent_team`
runtime subtree (ACP stack + protocol) baked into the image — no `src/`, no
`core`/`plugins`.

## Recommended ready-to-use image

For the fastest setup, use the maintained public image:

```bash
docker pull k3v1nbk/agent-team-sandbox:latest
```

It already contains Node.js, Python, Git and common CLI utilities, Claude Code,
Codex, the ACP sidecar, and the full runtime testing/browser toolchain described
below. Configure it explicitly in the app environment:

```bash
AGENT_TEAM_RUNTIME_IMAGE=k3v1nbk/agent-team-sandbox:latest
```

The image contains tools, **not account credentials**. Prefer Claude/Codex
subscription accounts in AI Code Factory and mount their
`CLAUDE_CONFIG_DIR`/`CODEX_HOME` into the sandbox. This is usually substantially
more cost-efficient for continuous coding-agent workloads than metered API
usage, subject to the subscription plan's terms and limits.

Use the build script in this repository when you need custom packages, pinned
CLI versions, a private registry, or organization-specific hardening.

## Run an OpenSandbox server

On the host that should run the task sandboxes (can be the same box as the app):

```bash
cd infra/runtime
cp .env.example .env                                  # see the ⚠️ note below
cp opensandbox/config.toml.example opensandbox/config.toml   # then set [server] api_key
docker compose -f docker-compose.opensandbox.yml up -d
curl http://localhost:8090/                           # readiness check
```

> `opensandbox/config.toml` is **gitignored** because it holds the server
> `api_key`. Keep secrets there (and in your local `.env`), never in the
> committed `*.example` files.

> ⚠️ **Two different `.env` files — don't confuse them.**
> The `.env` you just created **here (`infra/runtime/.env`) is read only by
> `docker compose`**, and the compose file substitutes exactly **one** variable
> from it: `OPENSANDBOX_BIND_ADDR` (the interface `8090` binds to). Every
> `AGENT_TEAM_RUNTIME_*` / `OPEN_SANDBOX_*` line in the file is **ignored by
> compose** — it does not configure the app.
>
> The **app** (the FastAPI process) reads its own env from **`agent-manager/.env`
> (the repo root)** via `core/config.py` (`load_dotenv()` + pydantic `env_file`).
> So to actually turn on the isolated runtime, copy the `AGENT_TEAM_RUNTIME_*` +
> `OPEN_SANDBOX_DOMAIN` + `OPEN_SANDBOX_API_KEY` lines from `.env.example` into
> **`agent-manager/.env`**, then restart the app. See `.env.example` for every
> variable with inline docs.

## App database (Postgres)

`docker-compose.postgres.yml` runs the app's PostgreSQL (pgvector) container. It
is **separate** from the OpenSandbox server above. It **reuses the existing data
volume** (the DB used to be started from `~/deep-agent/infra`) so nothing is
lost — both the `deep_agent` and `agent` databases stay intact. The app is
unchanged; it still connects via `DATABASE_URL=…@localhost:5432/agent` in
`agent-manager/.env`.

Migrate it here (run on the DB host, once):

```bash
cd infra/runtime
cp .env.example .env                                   # set PGDATA_VOLUME / DB_* below

docker volume ls | grep pgdata                         # 1) confirm the REAL volume name
                                                       #    (usually infra_pgdata; else set
                                                       #    PGDATA_VOLUME in .env)
(cd ~/deep-agent/infra && docker compose down)         # 2) stop the old owner (keeps data)
docker compose -f docker-compose.postgres.yml up -d    # 3) start it from here

# 4) verify both databases survived
docker compose -f docker-compose.postgres.yml exec postgres psql -U deep_agent -l
```

> **Safety.** The volume is `external: true` — this compose never creates or
> wipes it (not even `down -v`). A wrong `PGDATA_VOLUME` just fails `up` with
> "external volume not found"; your data is never touched.
>
> After moving, do **not** start deep-agent's own Postgres again — it would
> fight over port 5432 and the volume. deep-agent keeps working by connecting to
> this same `localhost:5432`.

Optional adminer DB browser ships in the same file → `http://127.0.0.1:8081`.

## Build

On the build server (must be able to reach Docker + npm + the OpenSandbox base
image):

```bash
# Build the "full" image → agent-team/agent-team-sandbox:v1 (+ :latest)
./scripts/build-runtime-images.sh full

# Push under your registry/account → myuser/agent-team-sandbox:v1 (+ :latest)
REGISTRY=myuser PUSH=1 ./scripts/build-runtime-images.sh full

# …and prune old untagged (<none>) images afterwards to save disk
REGISTRY=myuser PUSH=1 PRUNE=1 ./scripts/build-runtime-images.sh full

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
  state uses a SQLite store (`AGENT_TEAM_ACP_STORE_DB`); the image bakes a
  sandbox-local default, but the app repoints it at create time to the
  bind-mounted per-task host state dir (`/var/lib/agent-team/state`) so
  claude/codex sessions survive a sandbox kill.
- Mount points: `/workspace` (task working copy), `/skills`,
  `/var/lib/agent-team/state` (per-task persistent state).

Everything is baked in — nothing is installed on task start.

## Wire it up

Point the process env at the provider + image (see
`features/board/runtime/sandbox/config.py` for the full list):

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox
AGENT_TEAM_RUNTIME_IMAGE=k3v1nbk/agent-team-sandbox:latest
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

Both strategies use the **same** `agent-team-sandbox` image — the sidecar is baked in.

Enable the sidecar strategy:

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox
AGENT_TEAM_RUNTIME_STRATEGY=acp_sidecar
AGENT_TEAM_RUNTIME_IMAGE=k3v1nbk/agent-team-sandbox:latest
AGENT_TEAM_RUNTIME_SIDECAR_PORT=8871      # in-sandbox server port (proxied to host)
```

How it works: on the first turn the host starts `agent-team-runtime-server`
inside the sandbox (idempotent), resolves its port through the OpenSandbox proxy
(`get_endpoint`), opens a WebSocket, and relays every ACP frame to the cockpit
unchanged. ACP session state persists to a SQLite via the stdlib-only store
backend (no app code in the image). The image bakes a sandbox-local default
(`AGENT_TEAM_ACP_STORE_DB=/var/lib/agent-team/acp-sessions.db`), but the app
overrides it at sandbox create to
`/var/lib/agent-team/state/acp-sessions.db` — a bind mount of the per-task
host dir `<workspace parent>/.sandbox-state/<task>` — so the session mapping
(and with the mounted `~/.claude`/`~/.codex` login dirs, the CLI session
itself) survives not just pause/resume but a full sandbox kill.

## CLI secrets

Each engine still needs its own model credentials inside the sandbox
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` / Codex auth). Pass them through the
runtime profile's `env` (masked in streamed output) or via the OpenSandbox
server config — do **not** bake secrets into the image.
