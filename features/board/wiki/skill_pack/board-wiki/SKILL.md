---
name: board-wiki
description: How to read and contribute to this board's knowledge base (Board Wiki). Use when a task could benefit from prior knowledge, or when finished work produces knowledge worth keeping.
version: 0.2.0
tags: [knowledge-base, wiki, memory]
---

# Board Wiki

This board keeps a shared, LLM-maintained **knowledge base** inside one of the
git repositories checked out in your workspace — the one labelled **"board wiki"**
in the *Code repositories* section of your task context. It is the board's
"second brain": summaries, entity pages, concepts, and decisions accumulated
across tasks so you don't start from scratch every time.

The wiki is **just a git repo of markdown files**. You read it and contribute to
it the same way you work any repo: edit files and commit on your task branch. A
human reviews and merges your branch — so you are an **editor proposing changes,
not the final authority**.

## Start at `index.md` — it is the source of truth for conventions

**Always open the wiki folder and read its `index.md` first.** A real wiki
documents *itself*: `index.md` is the router/catalog (every page + a one-line
summary) and usually also describes the wiki's own conventions — how pages are
structured, the status legend, anchor/cross-reference rules, when to split a
page, and which file tracks open items.

**Follow the conventions the wiki's own `index.md` describes — do not impose a
layout of your own.** Every mature wiki organizes differently (flat doc files
at the root, a `pages/` subfolder, per-section anchors, a `_verification-queue`
or `_open-followups` file as its log, etc.). Discover the existing shape and
match it. The "Default layout" below is only a starting point for a wiki that is
still empty.

## Query — before you work

1. Open the wiki folder and read `index.md`.
2. From the index, identify the 1–4 relevant pages and read only those (at this
   scale, reading `index.md` then following its links/grepping is enough — no
   search engine needed). Expand via the index's cross-references when needed.
3. Use what you find and cite the page in your work.

## Contribute — after you produce knowledge

When a task yields knowledge worth keeping (a decision, a reusable summary, a
discovered fact, a comparison):

1. Add or update the relevant page **using the wiki's existing structure and
   formatting** (match the surrounding docs — heading layout, status/“last
   updated” lines, naming). Prefer updating/relating an existing page over
   creating a near-duplicate.
2. Update `index.md` (add/adjust the page's catalog line + any cross-references)
   and record the change wherever the wiki keeps its log/changelog (if it has
   one).
3. Separate **facts** (verifiable) from **inferences** (your synthesis) and
   **open questions** — the wiki is only trustworthy if these are not blurred.
   Mirror however the wiki already marks unverified claims.
4. **Commit on your task branch** (you are already on it). Do **not** switch to
   or commit on the repo's default branch.
5. If your knowledge contradicts an existing page, update that page and state the
   contradiction explicitly in the commit message — don't silently overwrite.

### Publishing

- LLM agents: use the `git_push` tool to publish your task branch (it pushes
  with managed credentials). A human reviews the diff and merges it.
- Direct-CLI agents: commit locally; publishing the branch is handled outside
  this chat. A human reviews and merges.

Either way the human merge is the gate — never assume your change is published
until it lands on the wiki's default branch.

## Default layout (only for an empty wiki)

If the wiki is empty or has no `index.md` yet, bootstrap a simple, conventional
layout and grow from there:

- `index.md` — the catalog: every page + a one-line summary, plus a short note
  on how pages are organized. **Always present; read first.**
- `pages/` — the wiki pages (entity / concept / summary / comparison /
  decision). See `references/page-formats.md` for starter shapes.
- a log file (e.g. `log.md`) — append-only, one line per change.

Once the wiki has its own shape, that shape wins over this default.

## Rules

- Treat the wiki as a normal repo: real files, real commits, real diffs.
- **Read `index.md` first; follow the wiki's own conventions over any default.**
- Always commit wiki changes on your **task branch**, never the default branch.
- Prefer updating/relating existing pages over creating near-duplicates.
- Keep pages concise and interlinked; keep `index.md` current.
