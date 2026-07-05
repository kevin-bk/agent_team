# =============================================================================
# agent_team / isolated-runtime image (single image, both strategies)
#
# One image that runs agent_team's direct-CLI engines inside an OpenSandbox
# sandbox, supporting BOTH execution strategies:
#
#   * oneshot     — SandboxedCliWorker exec's a non-interactive CLI command and
#                   parses its stream:
#                     claude → claude -p --output-format stream-json …
#                     codex  → codex exec --json …
#   * acp_sidecar — the host SidecarAcpWorker starts `agent-team-runtime-server`
#                   in the sandbox and relays the full ACP frame stream over a
#                   WebSocket (live plan checklist, tool cards, thinking, usage).
#
# The sidecar reuses ONLY the agent_team runtime subtree (ACP stack + protocol) —
# no `src/`, no core/plugins. ACP session state persists to a sandbox-local
# SQLite (AGENT_TEAM_ACP_STORE_DB), so it survives pause/resume next to the CLI.
#
# Bake every dependency into the image/snapshot — do NOT install on task start.
#
# BUILD CONTEXT: the plugin root (…/community_plugins/agent_team) so the runtime
# subtree + server are copyable. See scripts/build-runtime-images.sh:
#   docker build \
#     --platform linux/amd64 \
#     -t agent-team/agent-team-sandbox:v1 \
#     -f infra/runtime/images/full.Dockerfile \
#     .            # <-- context = plugin root
# =============================================================================

# Pin to the same OpenSandbox base the fleet uses. The plain :v1.0.2 upstream
# tag has historically been unreliable (missing clone3 syscall + execd
# entrypoint quirks → "Sandbox health check timed out"). Override at build time
# with --build-arg BASE_IMAGE=opensandbox/code-interpreter:<tag>.
ARG BASE_IMAGE=opensandbox/code-interpreter:v1.0.2.clone3.fix1
FROM ${BASE_IMAGE}

LABEL org.agent-team.profile="full"
LABEL org.agent-team.image-version="v1"
LABEL org.agent-team.runner-kind="oneshot,acp_sidecar"
LABEL org.agent-team.engines="claude,codex"

USER root

# --- CLI version pins ---------------------------------------------------------
# Bump deliberately and re-verify end-to-end: the stream-json / JSONL schemas
# can shift between releases and our parsers (cli_exec.py) track a known shape.
#   npm view @anthropic-ai/claude-code version
#   npm view @openai/codex version
ARG CLAUDE_CODE_VERSION=2.1.146
ARG CODEX_VERSION=latest

# Claude Code reads its config from CLAUDE_CONFIG_DIR when set; pinning it
# removes HOME-detection guesswork when the runtime exec's with an empty HOME.
ENV CLAUDE_CONFIG_DIR=/etc/claude-code

# --- OS tools the agents lean on ---------------------------------------------
# ripgrep powers Grep tools; git for diff/commit; jq for shell glue;
# bubblewrap + socat are needed if Claude Code's BashTool sandboxing kicks in.
# build-essential + python3-dev let pip/uv build native wheels for tasks whose
# Python deps ship no prebuilt wheel (psycopg, lxml, etc.).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep \
        fd-find \
        tree \
        jq \
        git \
        gh \
        curl \
        ca-certificates \
        bubblewrap \
        socat \
        nodejs \
        npm \
        build-essential \
        python3-dev \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# --- Coding CLIs --------------------------------------------------------------
# Claude Code (Anthropic) + Codex (OpenAI) via npm.
RUN npm install -g \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && claude --version \
    && codex --version

# --- Playwright + Chrome/Chromium + xvfb (agent-driven UI testing) ------------
# Agents run browser tests (e.g. embedded Shopify apps via test_env_setup)
# HEADED under a virtual display — headless triggers bot detection (Cloudflare
# blocks headless regardless of cookies). Browsers live in a shared,
# world-readable path so whatever uid the runtime drops to (and any
# @playwright/test version a repo installs) finds them instead of
# re-downloading ~150 MB per task. @playwright/mcp is baked too (binary:
# `playwright-mcp`), so a board's stdio MCP entry starts instantly instead of
# hitting the npm registry at task start.
#
# Branded Google Chrome is installed IN ADDITION to Chromium because
# (a) `playwright-mcp` defaults to the "chrome" channel — without it every
#     board would have to remember `--executable-path`, and agents "fix" the
#     gap with ad-hoc symlinks; and
# (b) branded Chrome fares better against bot detection than Chromium, which
#     is the whole point of headed testing on hardened sites.
#   usage: xvfb-run -a npx playwright test   |   xvfb-run -a playwright-mcp …
ARG PLAYWRIGHT_VERSION=1.61.1
ARG PLAYWRIGHT_MCP_VERSION=latest
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g \
        "playwright@${PLAYWRIGHT_VERSION}" \
        "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
    && npm cache clean --force \
    && playwright install --with-deps chromium \
    && playwright install chrome \
    && apt-get update && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX "${PLAYWRIGHT_BROWSERS_PATH}"

# --- Headless config for Claude Code -----------------------------------------
# `--permission-mode bypassPermissions` still requires the acceptance flag on
# disk, otherwise the CLI prints an interactive confirmation and blocks on
# stdin. Disable the CLI's own bwrap sandbox (we are already isolated). Write
# to CLAUDE_CONFIG_DIR + every plausible HOME so it's picked up whatever uid
# the runtime uses.
RUN mkdir -p "${CLAUDE_CONFIG_DIR}" /home/sandbox /root \
    && printf '%s\n' \
        '{' \
        '  "sandbox": {' \
        '    "enabled": false,' \
        '    "failIfUnavailable": false,' \
        '    "autoAllowBashIfSandboxed": false,' \
        '    "allowUnsandboxedCommands": true' \
        '  }' \
        '}' \
       > "${CLAUDE_CONFIG_DIR}/settings.json" \
    && for home_dir in /root /home/sandbox; do \
           printf '%s\n' \
               '{' \
               '  "bypassPermissionsModeAccepted": true,' \
               '  "hasCompletedOnboarding": true' \
               '}' \
              > "${home_dir}/.claude.json"; \
       done \
    && chmod -R a+rX "${CLAUDE_CONFIG_DIR}" /root/.claude.json /home/sandbox/.claude.json

# --- Python toolchain the agent often reaches for -----------------------------
# `python`→`python3` so scripts that call bare `python` work. `uv`/`uvx` is the
# standard runner for Python-based stdio MCP servers (e.g. `uvx mcp-server-git`)
# and gives tasks a fast installer/venv manager. Pinned for reproducible builds.
#
# The base image's system python3 ships WITHOUT pip ("No module named pip"), so
# install uv via its standalone installer (no pip needed), then let uv install
# the rest into the system env (--break-system-packages sidesteps PEP 668 on
# newer Debian/Ubuntu bases).
ARG UV_VERSION=0.5.11
RUN curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" \
        | env UV_INSTALL_DIR=/usr/local/bin sh \
    && ln -sf "$(command -v python3)" /usr/local/bin/python \
    && uv --version \
    && uvx --version \
    && uv pip install --system --break-system-packages \
        ruff==0.6.9 \
        pytest==8.3.3 \
        requests==2.32.3

# --- ACP sidecar (Strategy B) -------------------------------------------------
# The in-sandbox ACP bridge server + the agent_team runtime subtree it needs.
# Only the ACP stack + sidecar protocol are shipped — NOT src/ / core / plugins.
# Session state persists to a SQLite (see acp/store.py). The path below is only
# the sandbox-local FALLBACK — prepare_task_sandbox overrides it at create time
# to /var/lib/agent-team/state/acp-sessions.db, a bind-mounted per-task host
# dir, so sessions survive a sandbox kill (container env wins over image ENV).
ENV PYTHONPATH=/opt/agent-team
ENV PYTHONUNBUFFERED=1
ENV AGENT_TEAM_ACP_STORE_DB=/var/lib/agent-team/acp-sessions.db

COPY infra/runtime/server/sidecar-requirements.txt /tmp/sidecar-requirements.txt
RUN uv pip install --system --break-system-packages -r /tmp/sidecar-requirements.txt

# Runtime subtree (import closure of the sidecar server). Keeping these as
# separate COPYs preserves the package tree without dragging the whole plugin.
COPY __init__.py                        /opt/agent-team/agent_team/__init__.py
COPY features/__init__.py               /opt/agent-team/agent_team/features/__init__.py
COPY features/board/__init__.py         /opt/agent-team/agent_team/features/board/__init__.py
COPY features/board/runtime/__init__.py /opt/agent-team/agent_team/features/board/runtime/__init__.py
COPY features/board/runtime/events.py   /opt/agent-team/agent_team/features/board/runtime/events.py
COPY features/board/runtime/acp         /opt/agent-team/agent_team/features/board/runtime/acp
COPY features/board/runtime/sandbox     /opt/agent-team/agent_team/features/board/runtime/sandbox
COPY infra/runtime/server/agent_team_runtime_server.py /opt/agent-team/agent_team_runtime_server.py

# Console entrypoint on PATH: `agent-team-runtime-server --host 0.0.0.0 --port 8871`
RUN printf '%s\n' \
        '#!/usr/bin/env bash' \
        'exec python3 /opt/agent-team/agent_team_runtime_server.py "$@"' \
        > /usr/local/bin/agent-team-runtime-server \
    && chmod 0755 /usr/local/bin/agent-team-runtime-server \
    && mkdir -p /var/lib/agent-team && chmod 0777 /var/lib/agent-team

# --- Workspace + mount points -------------------------------------------------
# /workspace              — task working copy (uploaded or bind-mounted)
# /skills                 — skill packs materialised by agent_team
RUN mkdir -p /workspace /skills /tmp/agent-scratch \
    && chmod 1777 /tmp/agent-scratch \
    && chmod 0777 /workspace /skills

WORKDIR /workspace

# Do NOT pin USER — the OpenSandbox runtime decides which uid to drop to when
# it exec's commands via the SDK. Hard-coding USER conflicts with that handoff.

# --- Smoke check --------------------------------------------------------------
# Fail the build early if a critical binary is missing (bad mirror / yanked
# package) or if the sidecar's import graph is broken — not on the first task.
RUN claude --version \
    && codex --version \
    && rg --version \
    && git --version \
    && node --version \
    && python --version \
    && uv --version \
    && uvx --version \
    && playwright --version \
    && playwright-mcp --version \
    && google-chrome --version \
    && xvfb-run --help > /dev/null \
    && python3 -c "import agent_team.features.board.runtime.acp as a; \
import agent_team.features.board.runtime.sandbox.sidecar_protocol as p; \
print('acp engines:', sorted(a.ENGINES)); print('sidecar proto v', p.PROTOCOL_VERSION)"

EXPOSE 8871

# Default entrypoint stays the OpenSandbox upstream one; the Sandbox SDK injects
# exec_shell commands at runtime, so we don't override it. For oneshot the host
# exec's the CLI directly; for acp_sidecar it starts agent-team-runtime-server on
# demand (see service.open_sidecar_channel).
