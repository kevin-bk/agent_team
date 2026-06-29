# Boards, tasks & workspaces

Last updated: 2026-06-29 · [↩ index](../index.md) · Source: `README.md`,
`features/board/`

The foundation everything else builds on.

## Why

A board gives humans and agents a **shared, structured backlog** instead of
ad-hoc chats. The key insight is the **per-task workspace**: every task gets its
own folder that agents read and write with their normal file/shell tools and that
a human can browse/preview/edit. Collaboration happens in **one directory**, not
in each agent's private sandbox.

## Concepts

- **Board** (`AgentTeamBoard`) — configurable columns, Board/List/Timeline views.
  Holds assigned agents, repos, optional Jira config, optional channel, an
  autopilot config, a starter prompt, and skills.
- **Task** (`AgentTeamTask`) — issue types (story, bug, epic, task, subtask,
  agent), priority, assignee, reporter, labels, a Markdown description, a threaded
  discussion. Human key like `ABC-12` from `AgentTeamKeySeq`.
- **Conversation** (`AgentTeamConversation`) — one thread per `(task, agent)`, so
  each agent keeps its own history on the same task.
- **Comments/notes** (`AgentTeamComment`) — notes (with file attachments) for
  agents; each note's **visibility** can be toggled so some stay people-only and
  are never sent to the agent.

## Workspace

Resolved by `features/board/workspace.py`:

```
workspaces/agent_team/<board_slug>/<task_key>/
```

When a run starts, the agent's file/shell tools are **rooted here**. Assigned
repos are checked out as subfolders (see
[`repositories.md`](repositories.md)); planning artifacts live under
`.agent-team/` (see [`planning-workflow.md`](planning-workflow.md)). Attachments
are stored workspace-backed via `features/board/attachments.py`.

## Agent context per run

Each run feeds the agent a compact **task header** (title, key, type, status),
the description, the workspace path, and any **agent-visible** notes (with
workspace-relative pointers to attached files). People-only notes are never sent.
Follow-up turns send only the **delta** to keep the prompt prefix cache-friendly.
Context builders: `runtime/context.py` (LLM) and `runtime/cli_context.py`
(direct-CLI brief, e.g. `.agent-team/TASK.md`).

## Data-access layout

Repository-style data access lives in `features/board/repositories/` — one module
per entity (`boards.py`, `tasks.py`, `runs.py`, `comments.py`, `conversations.py`,
`members.py`, `messages.py`, `attempts.py`, `journal.py`, `activity.py`,
`autopilot.py`, `task_schedule.py`, `tool_outputs.py`). The router
(`features/board/router.py`) is thin and delegates to these + to runtime services.

## Smaller features worth knowing

- **CSV import/export** (`features/board/csv_tasks.py`):
  `GET /boards/{id}/tasks/export.csv`, and a two-step import
  (`POST …/tasks/import/preview` → `POST …/tasks/import`).
- **Workspace file browser API** — the cockpit's Artifacts panel is backed by
  `GET /tasks/{id}/files/tree`, `GET …/files` (+ `…/files/raw`),
  `PUT …/files` (write), `DELETE …/files`. Paths are resolved inside the task
  workspace only.
- **Attachments** — task attachments (`POST/DELETE /tasks/{id}/attachments`) and
  comment attachments (`…/comment-attachments`), stored workspace-backed.
- **Per-agent thread controls** — list/reset a `(task, agent)` conversation,
  list its messages, and a typing indicator (`…/agents/{id}/…`).
- **Live SSE** — `GET /boards/{id}/stream` (board events) and
  `GET /runs/{id}/events` (a run's frames). The cockpit invalidates on these.
- **Task scheduling** — one-off/recurring runs via `…/schedule` (+ `…/schedule/history`),
  stored in `AgentTeamTaskSchedule`.
- **Repo prep** — `GET /tasks/{id}/repos`, `POST /tasks/{id}/repos/prepare`
  (also lazily prepared before the first run). See [`repositories.md`](repositories.md).

## Authorization

Roles are owner / editor / viewer (`features/board/authz.py`). Viewers read;
editors mutate tasks/notes and drive agents; owners manage membership and board
config. Mutating endpoints check the caller's board role.

## Related

- Running an agent on a task → [`runtime-and-runs.md`](runtime-and-runs.md)
- Driving a task to verified completion → [`autonomous-loop.md`](autonomous-loop.md)
- Importing tasks from Jira → [`jira-integration.md`](jira-integration.md)
