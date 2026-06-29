# Board Wiki (per-board LLM knowledge base)

Last updated: 2026-06-29 · [↩ index](../index.md) · Source:
[`../../plans/board-wiki.md`](../../plans/board-wiki.md),
`features/board/wiki/`

A persistent, LLM-maintained **second brain** per board — and the same pattern
this very wiki follows.

## Why

Without it, every task starts from scratch and every good answer disappears into
a conversation thread. The Board Wiki sits between the raw work (tasks, artifacts,
repos, Jira) and the agents: knowledge is **compiled once and kept current** —
agents read it before working and contribute pages back. The pattern is Karpathy's
*LLM Wiki*: the LLM owns the bookkeeping (summaries, cross-references,
consistency), the human stays editor-in-chief, and the wiki is just a folder of
markdown.

## Key decision: the wiki IS a git repo

The plugin already manages git repos per board with per-task branches and a
human-merge gate. So the Board Wiki is **not a bespoke folder + inbox** — it is
simply **one of the board's assigned repos, marked `is_wiki`** (the flag is
per board↔repo assignment). This gives, for free:

- **File-first, every engine** — LLM and direct-CLI agents read/edit it with
  native file/git tools.
- **Diff control + human merge gate** — agents edit on their task branch; a human
  reviews the diff and merges a normal PR. Full git history, blame, rollback. No
  inbox, no mirror.
- **Cross-task sharing** — every task copy clones the same canonical repo, so
  reviewed knowledge propagates on the next pull while unmerged work stays
  isolated.

## Discover-first: the wiki documents itself

The skill is **idea-file style** — it does **not** impose a layout. The agent
**reads the wiki's own `index.md` first and follows whatever conventions it
already uses** (status legend, anchors, splitting rules, its own log file). A
fixed "pages/ + log.md + front-matter" template would fight a real wiki's
structure. Only when a wiki is **empty** does the agent bootstrap a simple
fallback (`index.md` catalog + `pages/` + a `log.md`) and grow from there — never
the backend, so we never auto-commit into a user's repo.

## How it wires into a run

`prepare_task_repos` tags each prepared repo with `is_wiki`. In
`local_backend._load_run_context`, if any prepared repo is a wiki, the bundled
**`board-wiki` skill pack** (`features/board/wiki/skill_pack/board-wiki/`) is
materialised and appended to the skills manifest (advertised to both the
direct-CLI brief and the LLM manifest). The repos context blocks label the wiki
repo and add a one-paragraph instruction. `wiki/service.py` ships
`materialize_wiki_skill`.

If no repo is marked wiki → **zero behaviour change** (skill not materialised).

## Recommended: one wiki repo per board

Karpathy's pattern is one knowledge base per domain; multiple wiki repos fragment
knowledge and break cross-references. Allow several only with a real reason (e.g.
public vs private), and the skill names which repo is the wiki.

## Related

- The repo machinery this rides on → [`repositories.md`](repositories.md)
- Future: in-app wiki merge panel + `wiki_search` → [`../roadmap.md`](../roadmap.md)
