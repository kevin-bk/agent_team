# Board code repositories

Last updated: 2026-06-29 · [↩ index](../index.md) · Source:
[`../../plans/board-repositories.md`](../../plans/board-repositories.md),
`features/repos/`

Let an owner register real git repos, keep them fresh on a schedule, and give
each task an **isolated working copy** — without wasting disk or leaking
credentials into the agent's workspace.

## Why & locked decisions

- **A repo is a first-class entity**, not board-scoped. Many-to-many: a board has
  many repos; a repo serves many boards (mirrors how the comm gateway works too).
- **Scoped by owner** (`owner_id`); the canonical clone lives under the owner's
  folder.
- **Permissions:** managing repos (create/edit/creds/schedule/clone/pull/delete)
  is **admin-only**; assigning a repo to a board needs board owner/editor; using a
  task copy is allowed for board members.
- **Disk strategy (the trick):** one **canonical clone** per repo (pulled on
  schedule) + per task `git clone --local <canonical> <task_dir>` →
  **hardlinked `.git/objects`**, so each task copy costs ≈ the working tree only.
- **Credentials:** stored in DB, plaintext, **write-only** (API exposes only
  `has_secret`). Used **only** for the canonical clone/pull via
  `git -c http.extraHeader="Authorization: Bearer <token>"` or `GIT_SSH_COMMAND` —
  **never written into `.git/config`**, never into the task copy.

## Paths

```
Canonical : workspaces/agent_team/_repos/<owner_seg>/<repo_slug>/
Task copy : <task workspace>/<repo_slug>/
```

## Modules (`features/repos/`)

| File | Role |
|---|---|
| `models.py` | `AgentTeamRepo`, `AgentTeamBoardRepo` (link: `branch_override`, `allow_push`, `is_wiki`). |
| `repositories.py` | data access (`list_for_owner`, `get`, `create`, `repos_for_board`, …). |
| `git_service.py` | clone/pull/status with credential injection, all via `asyncio.to_thread`; temp SSH key cleaned in `finally`. |
| `paths.py` | canonical + task-copy path helpers. |
| `task_copy.py` | `prepare_task_repos(db, task)` → `git clone --local` + checkout branch; `cleanup_task_repos`. |
| `scheduler.py` | `RepoPullTicker` (asyncio + `croniter` + `fcntl` lock). |
| `git_cred_helper.py` | a bundled git credential helper that fetches the token live from the DB at push time. |
| `router.py` | `/repos` CRUD (admin) + `/boards/{id}/repos` assign endpoints. |

## Per-task branch & publishing

Each task copy works on branch `agent/<task-key>` and **never touches the default
branch**. To let a human review/merge as a normal PR, the work must reach the
**real remote**, so `prepare_task_repos`:

- creates the copy with `git clone --local`, then **repoints its single `origin`
  remote at the real host URL** (so a plain `git push` reaches the host);
- installs a **pre-push hook** that refuses pushes to the default branch — any
  push can only publish the task branch;
- token auth is provided live by `git_cred_helper.py` **only if** the repo's
  `allow_push` master gate **and** the board opt-in are on (the secret never lands
  in the workspace). SSH keys are materialised inside `.git` (0600, never
  committed).

Result: direct-CLI agents publish with a plain `git push`; LLM agents use plain
`git push` or the explicit `git_push` tool (see
[`agent-tools-and-autopilot.md`](agent-tools-and-autopilot.md)). Honest caveat:
the helper runs inside the workspace, so it raises the bar but does not sandbox a
determined agent.

## Scheduler

`RepoPullTicker` acquires an `fcntl` lock (so only one worker runs it), then every
~60s queries repos whose `next_pull_at <= now`, **advances `next_pull_at` first**
(at-most-once), and pulls in a thread. Started from `on_startup`; schedule changes
recompute `next_pull_at`.

## Related

- A repo marked `is_wiki` becomes the board's knowledge base →
  [`board-wiki.md`](board-wiki.md)
