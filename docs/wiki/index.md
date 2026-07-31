# Agent Team — Wiki (knowledge base)

> **What this is.** A curated, interlinked knowledge base for the `agent_team`
> plugin. It is written for **both humans and AI agents**: read this `index.md`
> first, follow it to the 1–3 pages relevant to your task, use + cite them.
>
> The pattern is Andrej Karpathy's *LLM Wiki* — the same idea the plugin itself
> ships as the per-board **Board Wiki** feature (see
> [`pages/board-wiki.md`](pages/board-wiki.md)). Knowledge is **compiled once and
> kept current** instead of disappearing into chat threads and PR descriptions.

Last updated: 2026-07-31 · Owner: `agent_team` plugin

---

## How to use this wiki

1. **Start here.** This file is the router/catalog. Skim the map below, then open
   only the pages you need.
2. **Why → how.** Every page leads with *why the thing exists* (the problem and
   the decision), then *how it is implemented* (modules, data, flow), then
   *gotchas*.
3. **Three layers of docs.** This `wiki/` is the **curated technical** layer.
   Deep, long-form design briefs live in [`../plans/`](../plans/); the
   Vietnamese [`../../user-guide/`](../../user-guide/) teaches product concepts
   and workflows to end users. See
   [`pages/user-guide.md`](pages/user-guide.md) for the boundaries.
4. **Cite code, don't paraphrase it blindly.** Paths are relative to the plugin
   root (`community_plugins/agent_team/`). When a wiki claim and the code
   disagree, the code wins — fix the wiki.

## How to maintain it (for agents and humans)

> **Updating the wiki? Read [`guides/maintaining-the-wiki.md`](guides/maintaining-the-wiki.md) first** —
> it's the step-by-step workflow (which code to read to verify each claim, where
> to edit, and the done-checklist). The quick rules:

- **Append, keep current.** When you ship a feature or change a decision, update
  the relevant page **and** this catalog. Bump the page's "Last updated".
- **One concept per page.** If a page grows two unrelated halves, split it and
  add both to the catalog.
- **Decisions are first-class.** A non-obvious "why we did X not Y" belongs in
  [`decisions.md`](decisions.md) (and is linked from the feature page).
- **Don't invent structure silently.** If you add a new page, register it in the
  **Map** below so the next reader (human or agent) can find it.
- **Keep it semantic, not a transcript.** No raw logs, no full file dumps; link
  to the code instead.

---

## Map

### Start here
| Page | What you'll learn |
|---|---|
| [`overview.md`](overview.md) | What Agent Team is, the mental model, and one end-to-end example. |
| [`architecture.md`](architecture.md) | Plugin anatomy, the run lifecycle, event store + SSE, processes & tickers. |
| [`data-model.md`](data-model.md) | Every table/model, grouped by subsystem, and how they relate. |
| [`glossary.md`](glossary.md) | One-line definitions for the vocabulary used across pages. |

### Subsystems (one page each)
| Page | Subsystem | Raw design source |
|---|---|---|
| [`pages/boards-tasks-workspaces.md`](pages/boards-tasks-workspaces.md) | Boards, tasks, conversations, per-task workspaces, notes/attachments, the Code review surface (git Changes + Files) | `README.md` |
| [`pages/runtime-and-runs.md`](pages/runtime-and-runs.md) | Run backend, `AgentWorker` abstraction, event store, streaming/SSE, ACP | [`../plans/loop-engineering.md`](../plans/loop-engineering.md) |
| [`pages/isolated-runtime.md`](pages/isolated-runtime.md) | Per-task isolated execution via OpenSandbox: idle/pause, one-shot (Phase 1) + ACP sidecar (Phase 2), runtime profiles, images | [`../plans/opensandbox-runtime-implementation-plan.md`](../plans/opensandbox-runtime-implementation-plan.md), [`../plans/opensandbox-phase2-acp-sidecar.md`](../plans/opensandbox-phase2-acp-sidecar.md) |
| [`pages/autonomous-loop.md`](pages/autonomous-loop.md) | The controller/evaluator loop, budgets, the task state machine | [`../plans/loop-engineering.md`](../plans/loop-engineering.md) |
| [`pages/planning-workflow.md`](pages/planning-workflow.md) | Strict planning, the `.agent-team/` artifact contract, approval gate | [`../plans/planning-workflow-upgrade.md`](../plans/planning-workflow-upgrade.md), [`../plans/planning-workflow-implementation-decisions.md`](../plans/planning-workflow-implementation-decisions.md) |
| [`pages/task-journal.md`](pages/task-journal.md) | The semantic, append-only decision timeline + agent note inbox | [`../plans/task-journal-plan.md`](../plans/task-journal-plan.md) |
| [`pages/repositories.md`](pages/repositories.md) | Board code repos: clone, scheduled pull, per-task copy, credentials | [`../plans/board-repositories.md`](../plans/board-repositories.md) |
| [`pages/board-wiki.md`](pages/board-wiki.md) | The per-board LLM-maintained knowledge base (a repo marked `is_wiki`) | [`../plans/board-wiki.md`](../plans/board-wiki.md) |
| [`pages/jira-integration.md`](pages/jira-integration.md) | Per-board Jira config, import, and field/comment/attachment sync | `README.md` |
| [`pages/communication-gateway.md`](pages/communication-gateway.md) | Outbound notifications (v1) + inbound chat actions (v2) via Mattermost/Slack | [`../plans/communication-gateway-plan.md`](../plans/communication-gateway-plan.md) |
| [`pages/agent-tools-and-autopilot.md`](pages/agent-tools-and-autopilot.md) | `view_image` / `git_push` / `set_task_status` tools + the autopilot scheduler | — |

### Cross-cutting
| Page | What you'll learn |
|---|---|
| [`guides/maintaining-the-wiki.md`](guides/maintaining-the-wiki.md) | **How to update this wiki**: which code to read to verify a claim, where to edit, and the done-checklist. Read before editing any page. |
| [`guides/development.md`](guides/development.md) | Build/test/lint, and the recipe for adding a feature end-to-end (model → migration → repo → router → web-ui). |
| [`pages/user-guide.md`](pages/user-guide.md) | The Vietnamese user guide, its in-app Markdown reader, build pipeline, content contract, and maintenance checklist. |
| [`decisions.md`](decisions.md) | The key architectural decisions and *why* (ADR-style). |
| [`roadmap.md`](roadmap.md) | What's shipped, what's next, and the phasing for each subsystem. |

---

## The 60-second model

`agent_team` is an **agent-manager community plugin**: a Jira-style board where
**people and AI agents collaborate on the same tasks**. A **board** holds
**tasks**; each task has its own **workspace** folder. `@mention` an agent on a
task and it **runs** against that workspace, streaming output live over SSE.

On top of that single-turn "chat" foundation sit the additive systems this wiki
documents: an **autonomous loop** (run a goal to verified completion), a
**strict planning** contract (spec → plan → approve → execute → verify), a
**task journal** (durable decision memory), **board repositories** (real git
repos, per-task copies), a per-board **LLM wiki**, **Jira** import/sync, and a
provider-agnostic **communication gateway** (Mattermost/Slack notifications and,
in v2, two-way chat actions).

Every one of those is **additive**: plain chat keeps working exactly as before;
the heavier behaviour only engages when a task opts into it.
