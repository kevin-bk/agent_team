# Board code repositories

Last updated: 2026-06-30 · [↩ index](../index.md) · Source:
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
| `task_copy.py` | `prepare_task_repos(db, task)` → `git clone --local` + checkout branch; `cleanup_task_repos`; `reset_task_repos_by_id` (pull canonical + re-clone). |
| `bootstrap.py` | Runs each repo's optional setup command inside the task runtime once per fresh clone. |
| `scheduler.py` | `RepoPullTicker` (asyncio + `croniter` + `fcntl` lock). |
| `git_cred_helper.py` | legacy host-only DB credential helper, retained for compatibility. |
| `router.py` | `/repos` CRUD (admin) + `/boards/{id}/repos` assign endpoints. |

## Per-task branch & publishing

Each task copy works on branch `agent/<task-key>` and **never touches the default
branch**. To let a human review/merge as a normal PR, the work must reach the
**real remote**, so `prepare_task_repos`:

- creates the copy with `git clone --local`, then **repoints its single `origin`
  remote at the real host URL** (so a plain `git push` reaches the host);
- installs a **pre-push hook** that refuses pushes to the default branch — any
  push can only publish the task branch;
- for token auth, a small portable helper and its credential file are
  materialised inside `.git` (0600, never committed) **only if** the repo's
  `allow_push` master gate **and** the board opt-in are on. This works from both
  the host and an OpenSandbox `/workspace` mount, but is a demo-mode trade-off:
  a determined sandbox agent can read the token. SSH keys are likewise
  materialised inside `.git` (0600, never committed).

Result: direct-CLI agents publish with a plain `git push`; LLM agents use plain
`git push` or the explicit `git_push` tool (see
[`agent-tools-and-autopilot.md`](agent-tools-and-autopilot.md)). Honest caveat:
the helper runs inside the workspace, so it raises the bar but does not sandbox a
determined agent.

## Re-running a task: copy is kept, not refreshed

`prepare_task_repos` runs before **every** run (`local_backend`) but is
**idempotent**: if the task copy already exists it is *not* re-cloned — the agent
keeps working on its `agent/<task-key>` branch (history preserved), and **new
commits on the default branch are NOT pulled in**. The scheduler pulls only the
**canonical** clone; an existing task copy never auto-syncs to it. So a long-lived
task can drift behind the default branch (and accumulate merge conflicts at push
time). The copy is only re-created fresh — thus picking up the latest default
branch — when it is deleted (`cleanup_task_repos`, on task archive/delete) or via
an explicit reset.

**Reset (re-prepare):** `POST /tasks/{id}/repos/reset` →
`reset_task_repos_by_id` **pulls each canonical clone, then removes the task
copies and re-clones them** from the now-updated canonical. It is **destructive**
— un-pushed work on the task branch is discarded. Surfaced as a **"Re-prepare"**
button in the cockpit's *Code workspace* card (`TaskRepoCard.tsx`), behind a
confirm dialog.

## Automatic task bootstrap

A repository may define one optional `bootstrap_command`, for example:

```bash
npm ci --prefer-offline --no-audit
```

After task repos are prepared and the task sandbox is open, Agent Team executes
the command with that repository as `cwd`, before the first agent turn. A
successful run writes a fingerprint marker under the task clone's `.git`
directory, so later turns and sandbox pause/resume do not rerun it or dirty
source. A reset/re-clone removes the marker and runs setup again; editing the
configured command also changes the fingerprint and triggers a new run.

Bootstrap commands are administrator-controlled shell commands. Keep them
deterministic, non-interactive, and safe to retry. Failure blocks the agent turn
with the repository slug, exit code, timeout state, and a bounded output tail.
The default timeout is 10 minutes and can be changed with
`AGENT_TEAM_REPO_BOOTSTRAP_TIMEOUT_SECONDS` (30–3600 seconds).

## Scheduler

`RepoPullTicker` acquires an `fcntl` lock (so only one worker runs it), then every
~60s queries repos whose `next_pull_at <= now`, **advances `next_pull_at` first**
(at-most-once), and pulls in a thread. Started from `on_startup`; schedule changes
recompute `next_pull_at`.

## Related

- A repo marked `is_wiki` becomes the board's knowledge base →
  [`board-wiki.md`](board-wiki.md)
