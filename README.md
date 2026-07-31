# Agent Team

Agent Team is an **agent orchestration workspace** for
[BSSCommerce/agent-manager](https://github.com/BSSCommerce/agent-manager): a
place where humans and AI agents plan, execute, verify, and review development
work on the same task board.

It started as a Jira-style board, but the current plugin is broader than that:
it combines task workspaces, real code repositories, direct CLI agents over ACP,
strict planning, autonomous generator/evaluator loops, verification evidence,
optional OpenSandbox isolation, a task journal, Jira sync, and Mattermost/Slack
human communication.

New to Agent Team? Start with the
[Vietnamese user guide](user-guide/README.md), including the required Claude/Codex and
Skill Packs setup before running the first task.

> Agent Team is an
> [agent-manager](https://github.com/BSSCommerce/agent-manager) **community
> plugin**. When enabled it adds a single-page board UI, REST APIs, database
> models, ASGI mounts, background tickers, and agent tool factories. When
> disabled, its routes and contributed tools are blocked without changing the
> rest of agent-manager.

---

## Highlights

- **Boards, tasks, and workspaces** - Kanban/List/Timeline views, rich task
  metadata, notes, attachments, and one shared folder per task.
- **Human + agent collaboration** - `@mention` an agent for a single turn, or run
  a planned task through an autonomous loop.
- **Strict planning** - a planner writes `.agent-team/SPEC.md`, `PLAN.md`, and
  `TASKS.json`; a human approves the contract before code is written.
- **Verified execution** - a generator implements, an independent evaluator
  checks evidence, and the backend only marks completion after a verified pass.
- **Command receipts** - approved verification commands are executed by the
  backend/runtime and stored as source/runtime-bound receipts; agents cannot fake
  a pass by writing a summary.
- **Real git repositories** - admins register canonical repo clones; each task
  gets an isolated working copy on branch `agent/<task-key>`, with optional
  push gates.
- **Direct CLI agents** - `cli:*` agents can run Claude Code, Codex, or Cursor via
  ACP, including board-provided MCP configuration.
- **Optional sandbox runtime** - run CLI agents inside one OpenSandbox sandbox per
  task, with pause/resume, idle GC, and `oneshot` or `acp_sidecar` strategies.
- **Task journal and friction log** - a durable semantic timeline of decisions,
  questions, approvals, verdicts, and process friction.
- **External human comms** - Mattermost/Slack outbound notifications are shipped;
  inbound action handling is partially implemented and ready for transport wiring.
- **Jira integration** - import/sync issues, comments, attachments, labels, types,
  priorities, and status.
- **Real-time cockpit** - run output, tool frames, loop state, files, diffs,
  planning artifacts, evidence, and journal entries stream into the UI.

---

## The 60-second model

```
Board
  ├── Members: humans + staffed agents
  ├── Repositories: canonical git clones assigned to the board
  ├── Skills / MCP: guidance and external tools available to agents
  ├── Optional channel: Mattermost/Slack notifications
  └── Tasks
      ├── Workspace: files, attachments, repo working copies
      ├── Conversation per (task, agent)
      ├── Runs: append-only event stream for live UI replay
      ├── .agent-team/: SPEC, PLAN, TASKS, EVIDENCE, receipts, questions
      ├── Journal: semantic decision timeline
      └── Loop state: planning -> approval -> running -> verified/needs-human
```

There are three additive ways to use a task:

1. **Plain chat** - `@mention` an agent, get one streamed answer, keep the shared
   workspace and conversation history.
2. **Strict planning** - ask a planner to turn a rough goal into a durable
   contract; a human reviews/edits/approves before execution.
3. **Autonomous loop** - run the approved goal through generator/evaluator
   attempts until it passes verification, hits a budget, or needs a human.

Plain chat remains available even when the heavier planning/loop systems are not
used.

---

## Development workflow

The intended high-rigor flow looks like this:

```text
Human creates/imports task
        |
        v
Assign agents, repos, skills, MCP, optional channel
        |
        v
Planner researches workspace and writes SPEC.md / PLAN.md / TASKS.json
        |
        v
Human reviews, edits, and approves the plan contract
        |
        v
Generator implements against the approved contract
        |
        v
Backend runs approved verification commands and mints receipts
        |
        v
Evaluator inspects diff, receipts, artifacts, and scenarios
        |
        +--> fail: evidence digest is sent back to generator for retry
        |
        +--> needs_human / budget cap: task pauses for review
        |
        +--> pass: backend marks task complete and notifies humans
```

Important ownership boundaries:

- The **planner** proposes the contract.
- The **human** owns intent and approval.
- The **generator** writes the implementation.
- The **backend/runtime** executes approved verification commands and records
  command receipts.
- The **evaluator** judges evidence, but the backend enforces that a pass must
  cite valid, fresh evidence.
- The **journal** records meaningful decisions and friction; it is not a raw log.

---

## Planning and verification artifacts

Strict planning uses a workspace-local `.agent-team/` directory:

| Artifact | Purpose |
|---|---|
| `SPEC.md` | Human/engineering contract: goal, scope, constraints, acceptance criteria, verification expectations. |
| `PLAN.md` | Technical approach, touched areas, rollback, risk, and verification plan. |
| `TASKS.json` | Machine-readable task graph; optionally executable in dependency order. |
| `PLAN_REVIEW.json` | Reviewer verdict for the proposed plan. |
| `EVIDENCE.json` | Evaluator's durable verification record. |
| `VERIFICATION_RECEIPTS.json` | Evaluator-readable projection of backend-minted receipt rows. |
| `QUESTIONS.json` | Blocking/non-blocking questions raised by agents. |
| `PLAN_CHANGE_REQUEST.md` | Active marker when execution discovers the approved plan is wrong or unsafe. |
| `JOURNAL.md` | Rendered task journal read-back for agents. |

`SPEC.md` and `PLAN.md` are guidance-oriented text files. The JSON artifacts are
backend-parsed contracts; their shape is owned by Agent Team and versioned in the
runtime code.

See:

- [Planning workflow](docs/wiki/pages/planning-workflow.md)
- [Autonomous loop](docs/wiki/pages/autonomous-loop.md)
- [Task journal](docs/wiki/pages/task-journal.md)

---

## Runtime and agents

Agent Team runs all workers through one event-streaming contract, so the cockpit
does not need to care whether the worker is a LangGraph agent, a direct CLI
agent, or a sandboxed CLI process.

| Worker type | How it runs |
|---|---|
| LLM graph agent | Built by the core runtime with the agent's model, tools, MCP, skills, and middleware. |
| Direct CLI agent | `cli:*` alias driven through ACP, e.g. Claude Code, Codex, or Cursor. |
| Sandboxed CLI agent | Direct CLI agent running inside OpenSandbox via `oneshot` or `acp_sidecar`. |

Each run appends structured frames to `AgentTeamRunEvent`, which the UI tails via
SSE. Follow-up turns reuse the same `(task, agent)` conversation and send only
the new delta to keep long conversations efficient.

See:

- [Runtime and runs](docs/wiki/pages/runtime-and-runs.md)
- [Isolated runtime](docs/wiki/pages/isolated-runtime.md)

---

## Repositories and task workspaces

Admins can register reusable repositories. Boards then assign the repos they
need, and each task receives local working copies inside its workspace:

```text
Canonical clone : workspaces/agent_team/_repos/<owner>/<repo-slug>/
Task copy       : <task-workspace>/<repo-slug>/
Task branch     : agent/<task-key>
```

The disk strategy is one canonical clone plus per-task `git clone --local`, so
Git objects are hardlinked instead of fully duplicated. Existing task copies are
kept across reruns so the agent keeps its branch history. A cockpit
"Re-prepare" action can intentionally reset and re-clone task copies.

Repositories may define an admin-controlled `bootstrap_command`, run once after a
fresh task clone is prepared. Push is guarded by repo/board policy and by a
pre-push hook that refuses default-branch pushes.

See [Board code repositories](docs/wiki/pages/repositories.md).

---

## Optional OpenSandbox runtime

The default runtime is local host execution. For stronger isolation, configure
Agent Team to run CLI agents inside OpenSandbox:

```bash
AGENT_TEAM_RUNTIME_PROVIDER=opensandbox
AGENT_TEAM_RUNTIME_STRATEGY=acp_sidecar   # or oneshot
AGENT_TEAM_RUNTIME_IMAGE=<registry>/agent-team-sandbox:v1
AGENT_TEAM_RUNTIME_IDLE_MINUTES=30
AGENT_TEAM_RUNTIME_WORKSPACE_MODE=mount
AGENT_TEAM_RUNTIME_STRICT=1
OPEN_SANDBOX_DOMAIN=https://<opensandbox-server>
OPEN_SANDBOX_API_KEY=<key>
```

The sandbox model is one sandbox per task. Sandboxes pause after a turn, resume
for the next one, and are garbage-collected when idle. The `acp_sidecar` strategy
runs an in-sandbox ACP bridge so live plan cards, tool frames, MCP traffic, and
thinking frames can stream back to the same cockpit UI.

For image build and server setup, see [infra/runtime](infra/runtime/README.md).

---

## Communication and Jira

Agent Team includes two integration layers around the board:

- **Jira** - per-board Jira settings, issue import, comment/attachment sync, and
  field mapping.
- **Communication gateway** - reusable Mattermost/Slack bot connections, per-board
  channels, outbound lifecycle notifications, and a foundation for inbound human
  actions like approve plan / answer questions / acknowledge completion.

Inbound action execution exists in the backend, but the provider websocket
transport is still pending.

See:

- [Jira integration](docs/wiki/pages/jira-integration.md)
- [Communication gateway](docs/wiki/pages/communication-gateway.md)

---

## Agent tools

When enabled, the plugin contributes these LangChain tool factories for LLM graph
agents. Each is enabled by default per agent and can be toggled in the agent tool
configuration.

| Tool | Key | Purpose |
|---|---|---|
| View Image | `enable_agent_team_view_image` | Return a workspace image as multimodal content so a vision-capable model can inspect screenshots or attachments. |
| Git Push | `enable_agent_team_git_push` | Push a board repo's task working copy to its remote, subject to repo and board push gates. |
| Set Task Status | `enable_agent_team_set_task_status` | Let an agent move its own task between board columns, useful for autopilot. |

Direct CLI agents do not receive these LangChain tools directly; they normally
use their native file/git/CLI capabilities or board-provided MCP servers.

See [Agent tools and autopilot](docs/wiki/pages/agent-tools-and-autopilot.md).

---

## Plugin anatomy

`AgentTeamPlugin` contributes:

| Hook | What it provides |
|---|---|
| `models()` | Board, task, run, event, repo, receipt, journal, comm, and planning tables. |
| `routers()` | Platform, board/task, repository, and communication APIs. |
| `tool_factories()` | View Image, Git Push, and Set Task Status tools. |
| `asgi_apps()` | The SPA at `/agent-team` plus an internal loop-capture app. |
| `menu_items()` | The Agent Team sidebar entry. |
| `on_startup()` | Orphan run reconciliation and repo-pull ticker startup. |
| `on_shutdown()` | Repo/autopilot ticker cleanup. |

The internal loop-capture ASGI app is intentionally mounted separately: it starts
the autopilot ticker inside a real serving worker's event loop, which avoids
capturing the wrong loop under PM2 or multi-worker uvicorn deployments.

---

## Project layout

```text
agent_team/
├── plugin.py                  # Plugin entry: models, routers, menu, tools, lifecycle
├── router.py                  # Top-level platform router
├── web.py / spa.py            # Auth helpers and built SPA serving
├── db_migrations/             # SQL migrations applied by the core runner
├── docs/
│   ├── wiki/                  # Curated subsystem documentation
│   └── plans/                 # Raw design briefs and implementation notes
├── infra/runtime/             # OpenSandbox image/server assets
├── web-ui/                    # React + Vite + TypeScript source
├── static/                    # Built SPA bundle served at /agent-team
└── features/
    ├── board/                 # Boards, tasks, cockpit, Jira, workspaces, loop
    │   └── runtime/
    │       ├── acp/           # ACP client/session/MCP bridge
    │       ├── credentials/   # Sandbox credential injection
    │       ├── loop/          # Planning, execution, evaluation, receipts
    │       ├── sandbox/       # Local/OpenSandbox runtime implementations
    │       └── workers/       # LLM graph, ACP CLI, sandboxed workers
    ├── repos/                 # Canonical repos, task copies, scheduler, push gates
    └── comm/                  # Mattermost/Slack connections and notifications
```

---

## Documentation map

Start with the curated wiki:

- [Overview](docs/wiki/overview.md)
- [Architecture](docs/wiki/architecture.md)
- [Data model](docs/wiki/data-model.md)
- [Runtime and runs](docs/wiki/pages/runtime-and-runs.md)
- [Planning workflow](docs/wiki/pages/planning-workflow.md)
- [Autonomous loop](docs/wiki/pages/autonomous-loop.md)
- [Task journal](docs/wiki/pages/task-journal.md)
- [Board code repositories](docs/wiki/pages/repositories.md)
- [Isolated runtime](docs/wiki/pages/isolated-runtime.md)
- [Communication gateway](docs/wiki/pages/communication-gateway.md)
- [Roadmap](docs/wiki/roadmap.md)

The `docs/wiki/` layer is the maintained knowledge base. The `docs/plans/` layer
contains raw design and implementation notes.

---

## Installation

1. Place this folder under the agent-manager `community_plugins/` directory.
2. Ensure `PLUGINS_EXTERNAL_DIR=community_plugins` in the agent-manager `.env`.
3. Install dependencies from the project root:

   ```bash
   uv run setup-dependencies
   ```

4. Start or restart agent-manager. The core migration runner applies
   `db_migrations/*.sql` automatically.
5. Enable **Agent Team** from the admin **Plugins** page.
6. Open `/agent-team/`, create a board, add agents, assign repos/skills/MCP as
   needed, and start with either a plain mention or a planned goal.

The plugin reuses the core session cookie. Unauthenticated API calls receive a
JSON `401`.

---

## Building the web UI

The frontend source lives in `web-ui/`. The plugin serves only the built bundle
from `static/`, so UI changes must be built and copied into the plugin:

```bash
cd community_plugins/agent_team/web-ui
npm install
npm run build:agent-team
```

`build:agent-team` runs TypeScript checking, builds Vite with the `/agent-team`
base path, writes `web-ui/dist-agent-team/`, and copies the result into
`../static/`.

For local UI development with hot reload against a running agent-manager:

```bash
cd community_plugins/agent_team/web-ui
npm run dev
```

---

## Development

Run backend tests:

```bash
PYTHONPATH=community_plugins uv run pytest community_plugins/agent_team/tests -q
```

Lint the plugin:

```bash
uv run ruff check community_plugins/agent_team
```

Run frontend checks:

```bash
cd community_plugins/agent_team/web-ui
npm run typecheck
npm run test
```

When changing runtime behaviour, update the relevant page under `docs/wiki/` as
well as this README if the public mental model changes.
