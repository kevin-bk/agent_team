# Plan — Board code repositories (clone, schedule pull, per-task copy)

Status: in progress
Owner: agent_team plugin
Last updated: 2026-06-11

## Goal

Let an **owner** register code repositories (private, needing credentials),
keep them up to date with a **scheduled pull**, and give each **task** an
**independent working copy** so an agent can code in isolation — without wasting
disk and without leaking credentials into the agent's workspace.

## Decisions (locked)

- **Repo is a first-class entity**, not board-scoped. Many-to-many: a board has
  many repos; a repo can be assigned to many boards.
- **Scope by owner**: each repo has `owner_id`. The canonical clone lives under
  the owner's folder; at task-copy time we resolve the owner's canonical folder.
- **Permissions**: managing repos (create/edit/credentials/schedule/clone/pull/
  delete) is **admin-only**. Assigning/unassigning a repo to a board requires
  board **owner/editor**. Using a repo (task copy) is allowed for board members.
- **Schedule is per-repo** (off / interval / cron).
- **Scheduler runs in-process** via the plugin `on_startup` asyncio ticker
  (deep-agent `CronTicker` style). No PM2. `croniter` (already in the repo) +
  `fcntl` file lock (stdlib) to avoid double-fire across workers. Git commands
  run in `asyncio.to_thread`.
- **Disk strategy**:
  - Board/owner level: one **canonical clone** per repo, pulled on schedule.
  - Task level: `git clone --local <canonical> <task_dir>` → hardlinked
    `.git/objects`, so per-task cost ≈ working tree only.
- **Credentials**:
  - Stored in DB, **write-only** (API never returns the secret; only presence).
  - Plaintext, consistent with the existing Jira token (`jira_api_token`).
  - HTTPS token via `git -c http.extraHeader="Authorization: Bearer <token>"`
    and/or SSH key via `GIT_SSH_COMMAND` — never persisted into `.git/config`.
  - Token is used **only** for the canonical (clone + scheduled pull). Task
    copies use `--local` from a local path, so the token never reaches the
    agent's workspace.

## Paths

- Canonical: `workspaces/agent_team/_repos/<owner_seg>/<repo_slug>/`
- Task copy: `<task workspace>/<repo_slug>/` (task workspace =
  `workspaces/agent_team/<board_slug>/<task_key>/`)

`<owner_seg>` = a safe segment derived from `owner_id` (fallback `"_shared"` if
null). Reuse `workspace._safe_segment` rules.

## Data model

New table `plugin_agent_team_repo`:

| column | type | notes |
|---|---|---|
| id | String(32) PK | uuid hex |
| owner_id | String(36) FK users.id SET NULL, index | repo owner (scope) |
| name | String(255) | display name |
| slug | String(64) unique index | safe path segment |
| git_url | String(1024) | https or ssh url |
| default_branch | String(255) nullable | empty = remote HEAD |
| auth_type | String(16) default 'none' | none / token / ssh |
| auth_username | String(255) nullable | optional (https) |
| auth_secret | Text nullable | PAT or SSH private key (write-only) |
| schedule_mode | String(16) default 'off' | off / interval / cron |
| schedule_interval_seconds | Integer default 3600 | clamp 60..604800 |
| schedule_cron | String(128) nullable | croniter expression |
| clone_status | String(16) default 'absent' | absent / cloning / cloned / error |
| last_synced_at | DateTime tz nullable | last successful pull |
| last_sync_status | String(16) nullable | ok / failed |
| last_sync_error | Text nullable | trimmed error message |
| next_pull_at | DateTime tz nullable | scheduler cursor (advance-first) |
| archived | Boolean default false | |
| created_at / updated_at | DateTime tz | |

New junction `plugin_agent_team_board_repo`:

| column | type | notes |
|---|---|---|
| id | String(32) PK | |
| board_id | String(32) FK board CASCADE, index | |
| repo_id | String(32) FK repo CASCADE, index | |
| branch_override | String(255) nullable | optional per-board branch |
| created_at | DateTime tz | |

Unique `(board_id, repo_id)`.

Models go in a new module `features/repos/models.py` (registered in
`plugin.models()`), keeping `board/models.py` focused. Helper methods:
`has_secret()`, `canonical_path()`, `schedule()` decode.

## Migration

`db_migrations/009_repos.sql` — create both tables with
`-- migrate: skip_if_table_exists ...` guards (follow existing directive style).
Portable types (no dialect JSON). Indexes on owner_id, slug, board_id, repo_id.

## Backend modules (new feature package `features/repos/`)

```
features/repos/
  __init__.py
  models.py            # AgentTeamRepo, AgentTeamBoardRepo
  schemas.py           # RepoCreate/RepoUpdate/RepoDTO, BoardRepoDTO, AssignRequest
  repositories.py      # data-access (list_for_owner, get, create, update, ...)
  git_service.py       # clone/pull/status with credential injection (to_thread)
  paths.py             # canonical_path(owner_id, slug), task copy path helpers
  task_copy.py         # prepare_task_repos(task) -> git clone --local; cleanup
  scheduler.py         # RepoPullTicker (asyncio + croniter + fcntl lock)
  router.py            # /repos CRUD (admin) + /boards/{id}/repos assign endpoints
```

### git_service.py
- `_credential_env_and_args(repo)` → returns `(extra_git_args, env, tempfiles)`:
  - token: `["-c", f"http.extraHeader=Authorization: Bearer {secret}"]`
  - ssh: write key to a `chmod 600` temp file, `env["GIT_SSH_COMMAND"]=...`
  - always `env["GIT_TERMINAL_PROMPT"]="0"`
- `clone_repo(repo)`: into canonical path; sets `clone_status`.
- `pull_repo(repo)`: `git fetch --prune` + `git pull --ff-only` (or
  `reset --hard origin/<branch>` only if explicitly chosen — default ff-only).
- `repo_status(repo)`: branch, last commit, behind/ahead vs origin.
- All subprocess calls wrapped via `asyncio.to_thread` when called from async.
- Clean up temp key files in `finally`.

### task_copy.py
- `prepare_task_repos(db, task)`:
  - resolve assigned repos via junction (board_id → repos).
  - for each: ensure canonical exists (skip/log if `clone_status != cloned`).
  - `git clone --local <canonical> <task_dir>/<slug>`; checkout
    `branch_override or default_branch` if set.
  - return list of prepared repo dirs (workspace-relative) for context.
- `cleanup_task_repos(task)`: rm the per-repo dirs (on archive/delete).
- Lazy hook: called from the run path before the first turn if not prepared.

### scheduler.py — `RepoPullTicker`
- `start()` (from `on_startup`): acquire `fcntl` lock at
  `workspaces/agent_team/_repos/.pull.lock`; if held, skip (another worker owns
  it). `asyncio.create_task(_run_loop())`.
- `_run_loop`: every `tick_interval` (default 60s):
  - query repos with `schedule_mode != 'off'` and `next_pull_at <= now`.
  - **advance `next_pull_at` first** (interval → now+interval; cron → croniter
    next) to get at-most-once; skip if overdue beyond grace.
  - `await asyncio.to_thread(pull_repo, repo)`; update last_synced_at/status.
- `aclose()` (from `on_shutdown`): cancel, drain, release lock.
- On schedule change (via API) recompute `next_pull_at`.

## API (router.py)

Admin-only (guard with `_is_admin`):
- `GET    /repos` — list repos for current owner (admin sees own; optionally all)
- `POST   /repos` — create (name, git_url, branch, auth, schedule)
- `PATCH  /repos/{id}` — update; secret write-only (omit = keep, ""/null = clear)
- `DELETE /repos/{id}` — block if assigned to any board (or force unassign)
- `POST   /repos/{id}/clone` — clone/refresh canonical (async; returns status)
- `POST   /repos/{id}/pull` — pull now
- `GET    /repos/{id}/status` — branch/commit/sync info

Board owner/editor:
- `GET    /boards/{board_id}/repos` — list assigned (+ available to assign)
- `POST   /boards/{board_id}/repos` — assign `{repo_id, branch_override?}`
- `DELETE /boards/{board_id}/repos/{repo_id}` — unassign

DTOs never include `auth_secret`; expose `has_secret: bool`, `auth_type`.

## Frontend (web-ui inside agent_team)

1. `src/api/types.ts` + `client.ts` + `hooks.ts`: repo DTOs, CRUD + assign hooks.
2. New page **Repositories** (sidebar entry next to Boards):
   `src/features/repos/ReposPage.tsx` — table (name, url, branch, clone status,
   last synced, schedule, used-by-N), actions Add/Edit/Clone/Pull/Remove.
   `RepoDialog.tsx` — create/edit form incl. auth (None/Token/SSH, write-only
   with "Configured"+Clear) and schedule (Off/Interval/Cron) + PAT help link.
3. Board: header **Code** button (`GitBranch`) → `BoardReposDialog.tsx`
   (multi-select assign from registry + link to Repositories page). Hidden if
   `!canEdit`.
4. TaskCockpit: **Code workspace** card — Prepare/list copied repos (branch,
   path → file viewer), Sync from board, Reset/Remove.
5. `npm run build:agent-team`.

## Tests (tests/test_agent_team.py)

- migration/model: tables exist; create repo; assign/unassign; unique.
- DTO never leaks `auth_secret`; `has_secret` reflects state.
- git_service: clone a local bare/source repo (no network) → cloned; pull ff.
- credential arg builder: token → http.extraHeader; ssh → GIT_SSH_COMMAND +
  temp key cleaned up; secret never in `.git/config`.
- task_copy: `prepare_task_repos` produces `<task>/<slug>/.git`; hardlinked
  objects (same inode) vs canonical; cleanup removes dirs.
- scheduler: `next_pull_at` advance for interval + cron; ticker fires due repo
  once (advance-first); lock prevents second ticker.
- permissions: non-admin blocked on /repos; non-editor blocked on assign.

## Edge cases / notes

- Multi-worker: only the lock holder runs the ticker.
- Repo deleted while assigned → block with 409 + list of boards, or cascade
  unassign (decision: block).
- `git clone --local` requires same filesystem for hardlinks; both under
  `workspaces/agent_team/` so OK.
- Shared dep caches (npm/uv/pip) are out of scope for v1 but noted as a future
  disk optimization.
- Never log `auth_secret`.

## Build order

BE: 1) migration + models (+ register) → 2) schemas + repositories →
3) paths + git_service → 4) router CRUD + assign → 5) task_copy + run hook →
6) scheduler + on_startup/on_shutdown wiring → 7) tests + ruff.
FE: 8) types/client/hooks → 9) Repositories page + RepoDialog →
10) Board Code/assign dialog → 11) TaskCockpit Code workspace → 12) build.
