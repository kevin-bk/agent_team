# Plan — Board Wiki (an LLM-maintained knowledge base per board)

Status: phase 1 in progress (wiki = a git repo)
Owner: agent_team plugin
Last updated: 2026-06-22

## Goal

Give each board a persistent, LLM-maintained **knowledge base** ("second brain")
that sits between the raw work (tasks, artifacts, repos, Jira) and the agents.
Instead of every task starting from scratch and every good answer disappearing
into a conversation thread, knowledge is **compiled once and kept current**:
agents read the board wiki before working and contribute pages back to it.

The pattern is Andrej Karpathy's *LLM Wiki* (see `coding/llm-wiki/llm-wiki.md`):
the LLM owns the bookkeeping (summaries, cross-references, consistency), the
human stays editor-in-chief. The wiki is just a folder of markdown files.

## Key decision: the wiki IS a git repo

agent_team already manages git repositories per board (`features/repos/`): an
owner registers a repo, it is cloned/pulled on a schedule, and each task gets its
own working copy on a **task branch** (`agent/<task-key>`) — agents commit there
and **never touch the default branch**; publishing is gated by an admin/board
`allow_push` policy (`git_push` tool).

So the board wiki is **not a bespoke folder + inbox**. It is simply **one (or
more) of the board's assigned repos, marked as the wiki**. This gives us, for
free:

- **File-first, works for every engine** — both LLM and direct-CLI agents read
  and edit the repo with their native file/git tools (no special tooling, no
  divergent path).
- **Diff control + human merge gate** — agents edit wiki pages and commit on
  their task branch; a human reviews the diff and merges (a normal PR on the git
  host). This is the "LLM proposes, human disposes" gate, with full git history,
  blame, and rollback. No `inbox`, no mirror, no copy-in/out.
- **Cross-task sharing** — every task's working copy is cloned from the same
  canonical repo, so reviewed knowledge propagates to all tasks on the next clone
  /pull, while unmerged work stays isolated on its task branch.

## Architecture — three layers (Karpathy → agent_team)

| LLM Wiki layer | agent_team |
|---|---|
| **raw/** (immutable sources) | task descriptions, notes/attachments, Jira import, run outputs |
| **wiki/** (LLM-owned, interlinked md) | a board repo marked `is_wiki`, checked out per task on the task branch |
| **schema** (how to operate the wiki) | the bundled **`board-wiki` skill pack** (read first, page formats, commit-on-task-branch, conventions) |

### Wiki repo layout (conventional, agent-bootstrapped)

The agent creates these on first use if missing (no backend seeding):

- `index.md` — catalog: every page + a one-line summary.
- `log.md` — append-only changelog (`## [YYYY-MM-DD] kind | title`).
- `pages/` — entity / concept / summary / comparison / decision pages.

### Operations

- **Query** — agent reads the wiki repo's `index.md`, drills into `pages/`, uses
  + cites them. At this scale `index.md` + grep is enough (no vector DB).
- **Ingest/contribute** — agent adds/updates `pages/` + `index.md` + `log.md`,
  commits on its task branch. Human reviews + merges into the default branch.
- **Lint** (future) — a scheduled autopilot job opens a branch with health-check
  fixes (contradictions, stale claims, orphans) for human review.

## Multiple wiki repos?

Supported (the `is_wiki` flag is per board↔repo assignment, and `prepare_task_repos`
clones every assigned repo). **Recommended default: one wiki repo per board** —
Karpathy's pattern is one knowledge base per domain; multiple repos fragment
knowledge and break cross-references. Allow several only with a real reason (e.g.
public vs. private knowledge); the skill names which repo is the wiki.

## Implementation

### Data model / migration

`db_migrations/015_board_repo_wiki.sql` (style follows `013`/`010`):

```sql
-- migrate: skip_if_table_missing plugin_agent_team_board_repo
-- migrate: skip_if_column_exists plugin_agent_team_board_repo is_wiki
ALTER TABLE plugin_agent_team_board_repo ADD COLUMN is_wiki BOOLEAN NOT NULL DEFAULT 0;
```

`AgentTeamBoardRepo.is_wiki: Mapped[bool]` (default `False`). Surfaced through
`AssignRepoRequest`, `BoardRepoDTO`, `serialize_board_repo`, `assign_repo`,
`repos_for_board` (now a 4-tuple `(repo, branch_override, allow_push, is_wiki)`),
and the assign endpoint.

### Run path

`prepare_task_repos` tags each prepared repo dict with `is_wiki`. In
`local_backend._load_run_context`, after skill materialisation, if any prepared
repo `is_wiki` we materialise the bundled `board-wiki` skill pack and append it to
the skills manifest (so the direct-CLI brief and the LLM Codex manifest advertise
it). The repos context blocks (`context._format_repos`, `cli_context._render_repos_block`)
label the wiki repo and add a one-paragraph instruction.

### Skill pack `features/board/wiki/skill_pack/board-wiki/`

`SKILL.md` (read-first, page formats, commit-on-task-branch, publish via
`git_push`/human merge) + `references/page-formats.md`. `wiki/service.py` only
ships `materialize_wiki_skill`.

### Frontend

`BoardReposDialog` gets a **"Use as wiki"** toggle per assigned repo
(`is_wiki` on the assign request / DTO).

## Phasing

- **This change:** `is_wiki` flag end-to-end + skill pack + run-path wiring + UI
  toggle. Diff control = git PR on the host (no in-app merge UI yet).
- **Next:** an in-app "Wiki Review/Merge" panel (merge a task's wiki branch into
  the default branch from the cockpit); a `wiki_search` tool + `scripts/wiki.py`
  shared engine when `index.md` + grep stops scaling.
- **Later:** autopilot ingest-on-done + scheduled lint; a reviewer-agent
  "Quality Gate" that triages a wiki branch before the human merges; qmd (BM25 +
  vector + re-rank) when the wiki is genuinely large.

## Tests (tests/test_agent_team.py)

- `is_wiki` round-trips: `assign_repo` persists it; `repos_for_board` returns the
  4-tuple; `serialize_board_repo` / `BoardRepoDTO` expose it.
- `prepare_task_repos` marks the wiki repo's prepared dict with `is_wiki`.
- repos context blocks label the wiki repo and emit the guidance line.
- `materialize_wiki_skill` lands `board-wiki/SKILL.md` in `.claude/skills` and
  `.cursor/skills` and returns a manifest row.

## Edge cases / notes

- No repo marked wiki → zero behaviour change (skill not materialised).
- The agent commits wiki pages on its task branch only (mirrors `git_tools`,
  which refuses the default branch).
- Wiki repo bootstrapping (`index.md`/`log.md`/`pages/`) is done by the agent via
  the skill, not the backend, so we never auto-commit into a user's repo.
- A repo can be a wiki on one board and a plain code repo on another (the flag is
  per assignment, not per repo).
