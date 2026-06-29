# Maintaining this wiki (a guide for future LLMs)

Last updated: 2026-06-29 · [↩ index](../index.md)

> **Read this before you edit any wiki page.** This wiki is read by both humans
> and AI agents to understand `agent_team`. Stale or wrong docs are worse than
> missing docs — an agent will *act* on them. Your job when updating is to make
> the wiki match the **code as it is today**, in the wiki's own style.

## 0. The golden rule

**Code is the source of truth; the wiki is a curated summary of it.** Never
update a wiki page from memory, from a chat thread, or from the design briefs in
[`../../plans/`](../../plans/) alone — those are *proposals* and the
implementation often diverges from them. Always open the real modules and confirm
the claim before you write it.

When code and the wiki disagree, **the code wins** — fix the wiki.

## 1. The two layers (don't confuse them)

| Layer | Path | What it is | Who edits |
|---|---|---|---|
| **Raw** | `docs/plans/*.md` | long-form design briefs (often "Proposed"/"Status: …"). Historical intent + deep rationale. | append-only-ish; rarely rewritten |
| **Curated** | `docs/wiki/` (this) | short, interlinked, *current* truth. One concept per page. | kept in sync with code |

A wiki page **links to** its plan brief as the deep source; it does **not**
duplicate 1,400 lines of it. If you find yourself pasting a whole plan into the
wiki, stop — summarise and link instead.

## 2. When to update the wiki

Update it whenever you (or a task you reviewed) change behaviour, not just code
shape:

- New/changed endpoint, model field, artifact path, state value, config flag.
- A decision that changes "why we do it this way" → also add/adjust
  [`../decisions.md`](../decisions.md).
- A subsystem ships a phase that the roadmap listed as "next" → move it in
  [`../roadmap.md`](../roadmap.md).
- A new term that a reader won't know → add a one-liner to
  [`../glossary.md`](../glossary.md).
- A whole new subsystem → add a `pages/<name>.md` **and** register it in the
  [`../index.md`](../index.md) Map.

Pure refactors with **no behaviour change** usually need no wiki edit (but check
that the page doesn't name a moved/renamed module).

## 3. The workflow (how to actually do it)

This is the exact loop a previous audit used; follow it.

### Step 1 — Find the right page
Open [`../index.md`](../index.md) → the Map tells you which page owns the topic.
One concept lives on one page; if your change spans two, edit both.

### Step 2 — Read the real code (verify, don't assume)
Read the modules the page is about. Efficient moves:

- **Endpoints:** grep the routers for the route decorators rather than reading top
  to bottom:
  ```
  Grep  pattern: @router\.(get|post|put|patch|delete)\(   glob: **/features/**/router.py   (-A 1)
  ```
- **Models / fields / constants:** read `features/*/models.py`; grep for the
  constant groups (e.g. `LoopState`, `PLANNING_MODE_`, `RUN_ROLE_`, `*_PATH`).
- **Logic / flow:** read the owning module(s). Cross-check the page's claims one by
  one against what the code does (signatures, field names, defaults, branches).
- **"Is X wired?"** grep for the caller, not just the definition — a function can
  exist (`comm/inbound.py`) while nothing invokes it yet (no transport).

The map of which files back which page:

| Page | Read these |
|---|---|
| `boards-tasks-workspaces` | `features/board/{router,models,workspace,csv_tasks,attachments}.py`, `repositories/` |
| `runtime-and-runs` | `features/board/runtime/{local_backend,backend,event_store,events,translator,registry,dispatch}.py`, `runtime/workers/*` |
| `autonomous-loop` | `features/board/runtime/loop/{driver,controller,evaluator,verdict,budget,status,service,task_graph}.py` |
| `planning-workflow` | `runtime/loop/{planning,planning_artifacts,planning_prompts}.py`, `board/router.py` (`/planning/*`), `board/models.py` |
| `task-journal` | `runtime/task_journal.py`, `repositories/journal.py`, `board/router.py` (`/journal`) |
| `repositories` | `features/repos/*` (esp. `task_copy.py`, `git_service.py`, `scheduler.py`) |
| `board-wiki` | `features/board/wiki/*`, `runtime/local_backend.py` (wiki skill wiring) |
| `jira-integration` | `features/board/jira/*` |
| `communication-gateway` | `features/comm/*` (`router,service,inbound,models,providers/*`) |
| `agent-tools-and-autopilot` | `plugin.py`, `runtime/{image_tools,git_tools,status_tools}.py`, `autopilot_scheduler.py` |

### Step 3 — Edit, keeping the house style
- Lead with **why** (problem + decision), then **how** (modules/data/flow), then
  **gotchas / edge cases**, then **Related** links. Don't drop a wall of code.
- Use backticks for files/symbols; paths are relative to the plugin root
  (`community_plugins/agent_team/`).
- Be **semantic, not a transcript**: no raw logs, no full file dumps. Link to code.
- Prefer linking a term to the glossary over re-explaining it.
- Update the page's `Last updated:` date.

### Step 4 — Keep the wiki self-consistent
After editing a page, check the ripple:
- Did you add a page? → add it to [`../index.md`](../index.md) Map.
- Did you change a fact mirrored elsewhere (e.g. an endpoint list, a state name)?
  → grep the wiki for it and fix every copy:
  ```
  Grep  pattern: <the old fact>   path: docs/wiki
  ```
- New term? → [`../glossary.md`](../glossary.md). New decision? →
  [`../decisions.md`](../decisions.md). Phase shipped? → [`../roadmap.md`](../roadmap.md).

### Step 5 — Verify links
Run the broken-link sweep from `docs/wiki/`:

```bash
bad=0
while IFS= read -r f; do d=$(dirname "$f");
  while IFS= read -r link; do
    case "$link" in http*|"#"*) continue;; esac
    t="${link%%#*}"; [ -z "$t" ] && continue
    [ ! -e "$d/$t" ] && { echo "BROKEN: $f -> $link"; bad=1; }
  done < <(rg -o '\]\(([^)]+)\)' -r '$1' "$f")
done < <(find . -name '*.md')
[ "$bad" = 0 ] && echo "All internal links resolve."
```

## 4. Definition of done (checklist)

- [ ] Every claim I wrote was confirmed against current code (not plans/memory).
- [ ] The page leads with *why*, then *how*; no raw transcript/log dumps.
- [ ] `Last updated:` bumped on every page I touched.
- [ ] New page registered in `index.md`; new term in `glossary.md`; new decision in
      `decisions.md`; shipped phase moved in `roadmap.md`.
- [ ] Facts duplicated elsewhere in the wiki were updated consistently.
- [ ] Internal links resolve (Step 5 prints "All internal links resolve.").
- [ ] If I changed a doc that quotes code (e.g. `guides/development.md` commands),
      the command still works.

## 5. Anti-patterns (don't)

- ❌ Copy a plan brief verbatim into the wiki.
- ❌ Write "v2 will…" for something already implemented (check `task_graph.py`
  before calling task-graph "future").
- ❌ Invent endpoints/fields from the design doc that aren't in the router/model.
- ❌ Add a page without registering it in `index.md` (orphan page = invisible).
- ❌ Leave a term undefined that a fresh reader would trip on.

## Related

- The catalog you route from → [`../index.md`](../index.md)
- How to build/test/add a feature → [`development.md`](development.md)
- Why the wiki exists at all (the LLM-Wiki pattern) →
  [`../pages/board-wiki.md`](../pages/board-wiki.md)
