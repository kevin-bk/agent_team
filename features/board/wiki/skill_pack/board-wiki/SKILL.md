---
name: board-wiki
description: How to read and contribute to this board's knowledge base (Board Wiki). Use when a task could benefit from prior knowledge, or when finished work produces knowledge worth keeping.
version: 0.1.0
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

## Layout (inside the wiki repo)

A wiki repo uses a simple, conventional layout. If the repo is empty or missing
these, create them:

- `index.md` — the catalog: every page with a one-line summary. **Read first.**
- `log.md` — append-only changelog: one line per change,
  `## [YYYY-MM-DD] kind | title` (kind = ingest / query / lint).
- `pages/` — the wiki pages (entity / concept / summary / comparison /
  decision). See `references/page-formats.md`.

## Query — before you work

1. Open the wiki repo folder and read `index.md`.
2. Open the relevant pages under `pages/` (at this scale, reading `index.md`
   then grepping `pages/` is enough — no search engine needed).
3. Use what you find and cite the page in your work.

## Contribute — after you produce knowledge

When a task yields knowledge worth keeping (a decision, a reusable summary, a
discovered fact, a comparison):

1. Add or update markdown under `pages/` (one topic per file, `kebab-case.md`).
2. Update `index.md` (add/adjust the page's catalog line) and append a `log.md`
   entry.
3. Follow `references/page-formats.md`. Separate **facts** (verifiable) from
   **inferences** (your synthesis) and **open questions** — the wiki is only
   trustworthy if these are not blurred.
4. **Commit on your task branch** (you are already on it). Do **not** switch to
   or commit on the repo's default branch.
5. If your knowledge contradicts an existing page, update that page and state the
   contradiction explicitly in the commit message — don't silently overwrite.

### Publishing

- Commit locally; publishing the branch is handled outside
  this chat. A human reviews and merges.

Either way the human merge is the gate — never assume your change is published
until it lands on the wiki's default branch.

## Rules

- Treat the wiki as a normal repo: real files, real commits, real diffs.
- Always commit wiki changes on your **task branch**, never the default branch.
- Prefer updating/relating existing pages over creating near-duplicates.
- Keep pages concise and interlinked; keep `index.md` and `log.md` current.
