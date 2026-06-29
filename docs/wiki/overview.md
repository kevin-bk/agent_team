# Overview — what Agent Team is and why

Last updated: 2026-06-29 · [↩ index](index.md)

## Why it exists

Teams increasingly want **humans and AI coding agents working the same backlog**,
not agents bolted on as a side channel. Existing chat-with-an-agent UIs lose
context the moment the thread scrolls away, give no shared workspace, and have no
notion of "this task is planned / running / verified / done".

Agent Team is the answer: a **Jira-style task board** where you plan work, then
`@mention` an agent to pick a task up and run it **in a shared, per-task
workspace** you can browse. It is delivered as an **agent-manager community
plugin** — when enabled it adds a board UI, a REST API, and a set of agent tools;
when disabled, its routes are blocked and nothing else is affected.

## The mental model

```
Board ── has many ──► Task ── has a ──► Workspace (folder on disk)
  │                     │
  │                     ├── Conversation per (task, agent)  ──► Runs ──► RunEvents (stream)
  │                     ├── Comments / notes (some agent-visible)
  │                     ├── Journal (semantic decision timeline)
  │                     └── .agent-team/ planning artifacts (SPEC/PLAN/TASKS/…)
  │
  ├── assigned Agents (LLM graph agents and direct-CLI agents)
  ├── assigned Repos (real git repos; each task gets a working copy)
  ├── optional Jira config (import + sync)
  └── optional Channel (Mattermost/Slack notifications)
```

Two kinds of agent run through one contract (see
[`pages/runtime-and-runs.md`](pages/runtime-and-runs.md)):

- **LLM graph agents** — the core runtime builds a LangGraph agent (model + tools
  + MCP + middleware).
- **Direct-CLI agents** (`cli:*` aliases) — Claude Code / Cursor CLI / Codex CLI
  driven over **ACP**.

Either way, the agent's **file/shell tools are rooted at the task workspace**, so
collaborators share one directory instead of each agent having a private one.

## Three layers of behaviour (each additive)

1. **Chat (always on).** `@mention` → one run → one streamed answer. Unchanged by
   everything below.
2. **Autonomous loop (opt-in per task).** A controller drives a *generator* worker
   and an independent *evaluator* until the goal is **verified**, with budgets and
   a human-review gate. See [`pages/autonomous-loop.md`](pages/autonomous-loop.md).
3. **Strict planning (opt-in).** Before the loop runs, a planner+reviewer produce
   a durable **contract** (`SPEC.md`, `PLAN.md`, `TASKS.json`) that a human
   approves. See [`pages/planning-workflow.md`](pages/planning-workflow.md).

Surrounding these are the support systems: **repositories**, **board wiki**,
**task journal**, **Jira**, and the **communication gateway**.

## End-to-end example

> **Goal:** "Add a `GET /tasks/{id}/planning` endpoint that returns the latest
> approved plan."

1. **Intake.** A human creates the task on a board (or imports it from Jira),
   optionally assigns a code repo so the agent has the real codebase.
2. **Plan.** Human starts strict planning. The **planner** inspects the workspace
   and writes `.agent-team/SPEC.md` + `.agent-team/PLAN.md` (+ `TASKS.json`); an
   adversarial **reviewer** writes `PLAN_REVIEW.json`. Task state →
   `waiting_plan_approval`. No process is kept alive while it waits.
3. **Approve.** The human reads the spec/plan in the cockpit, edits if needed, and
   approves. State → `plan_approved`. Approval pins the artifact checksums.
4. **Execute.** The **generator** reads the approved artifacts and implements; the
   independent **evaluator** runs the project's tests/lint and writes
   `EVIDENCE.json`. The loop continues until the evaluator returns `pass` or a
   budget/needs-human guardrail trips. Every meaningful step is appended to the
   **task journal**.
5. **Notify.** If the board has a channel, a Mattermost/Slack message announces
   "plan ready for review" and "task complete", tagging the assignee. In v2 the
   human can approve or answer questions **from chat**.
6. **Publish.** If the repo allows pushing, the agent commits on its task branch
   (`agent/<task-key>`) and a human merges the PR on the git host.

## Where things live (top level)

```
agent_team/
├── plugin.py        # Plugin entry: models, routers, tools, menu, lifecycle hooks
├── router.py        # Top-level platform router
├── web.py / spa.py  # Auth helpers / serves the built SPA
├── db_migrations/   # SQL migrations (auto-applied by the core runner)
├── docs/            # plans/ (raw design briefs) + wiki/ (this knowledge base)
├── web-ui/          # React + Vite source for the SPA
├── static/          # Built SPA bundle (served by the plugin)
└── features/
    ├── board/       # boards, tasks, runs, runtime (workers + loop), jira, wiki
    ├── repos/       # board code repositories
    └── comm/        # communication gateway (notifications + inbound)
```

See [`architecture.md`](architecture.md) for how these fit together at runtime.
