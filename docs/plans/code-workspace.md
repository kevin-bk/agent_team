# Code Workspace — task file review (Changes + Files)

Status: **implemented** · Owner: agent_team · Source mockups:
`docs/images/view_files_diff.png` (Changes), `docs/images/view_files_change.png` (Files)

## Why

When a CLI/loop agent works a large task it edits many files across one or more
repos and across **multiple runs/attempts**. The previous review surface was too
weak for that:

- `TaskFiles.tsx` — a bare lazy tree (no search, no "what changed", no status).
- `FileViewerModal.tsx` — opens **one file at a time** in a blocking modal →
  open/close churn when reviewing 20–30 files.
- `RunChanges.tsx` — a per-thread "Changes" tab **derived from streamed
  `write`/`edit` tool calls**. It only sees **one conversation** and only LLM
  tool calls; it misses direct-CLI agents (plain git), multiple loop attempts,
  and the real on-disk end state.

Goal: a dedicated **Code workspace** at task level — a `Changes | Files` surface
where **git is the source of truth**, all changed files are reviewable in one
place with folded side-by-side diffs, and browsing uses a searchable tree +
multi-tab viewer instead of a modal.

## Design principles

- One surface, a segmented toggle `Changes | Files`.
- Changes = git: list `{path, status A/M/D/R/U}` → one collapsible card per file,
  **lazy-fetch the diff on expand**.
- Diff editor (Monaco `DiffEditor`) with **folded unchanged regions**,
  side-by-side for modified files, inline for pure add/delete, a 3-way
  **old / diff / new** toggle, and markdown preview in old/new mode.
- Files view: **quick-access pills** ranked by importance, a collapsible tree
  (with a "Changed only" mode + status dots), and a content viewer with a
  **rich (markdown) / source** toggle, auto-refreshing as agents edit.
- Dark palette: a deep cool-grey scale (deep editor shell, low-chroma surfaces,
  hairline borders).

## Decisions taken

1. **Diff engine — Monaco** (`@monaco-editor/react` + `monaco-editor`). Bundled
   locally (no CDN) with only the base editor worker; read-only everywhere.
   Wrapped behind a `<TaskDiff>` component.
2. **Accent** — keep `--primary` (Jira blue). The global dark theme was retuned
   to a deep cool-grey palette in a follow-up pass.

## Architecture overview

```
TaskCockpit
 ├─ left rail: + "Code" thread  (next to Overview / Goal / Journal)
 └─ middle pane when thread === CODE:
     CodeWorkspace
      ├─ toolbar: [Changes | Files]  · repo ▾ · search · summary(+/-) · refresh
      ├─ ChangesView      (git-truth)
      │    └─ FileDiffCard*  (collapsible, lazy <TaskDiff>, old/diff/new, badge)
      └─ FilesView
           ├─ quick-access pills (priority-ranked changed files)
           ├─ WorkspaceTree (search · "changed only" · status dots · file icons)
           └─ FileTabsPane  (multi-tab) → FileContentViewer (source / markdown)
```

Clicking a file in the right-hand "Artifacts" panel opens it as a **tab in the
Code workspace** instead of the blocking modal (the modal is kept for note
attachments and other contexts). The right panel auto-collapses while the Code
thread is open so it reads as a full-width review surface.

---

## Phase 1 — Backend: git change API

Module `features/repos/diff_service.py` (pure git, all via `asyncio.to_thread`),
reusing `task_copy.task_branch_name` and the base branch
(`branch_override || repo.default_branch`) from `repos_for_board`.

Per repo copy at `<workspace>/<slug>` on branch `agent/<task-key>`:

- **Changed files:** `merge_base = git merge-base <base> HEAD`; then
  - tracked: `git diff --numstat -M <merge_base>` (+ `--name-status -M` for
    A/M/D/R and rename old→new) — covers committed **and** working-tree edits.
  - untracked: `git ls-files --others --exclude-standard` → status `U` (added).
  - per entry: `{ repo, path, old_path?, status, additions, deletions, binary }`.
- **One file diff:** `{ original, modified, status, binary, truncated }`
  - `original` = `git show <merge_base>:<path>` (empty for A/U),
  - `modified` = read working-tree file (empty for D),
  - mark `binary` when bytes aren't UTF-8 (skip text diff, show placeholder).

Endpoints (in `features/board/router.py`, `viewer` guard, `_task_workspace`):

- `GET /tasks/{id}/changes` → `{ repos: [{slug, base_branch, branch, present}],
  files: [ChangeEntry], truncated }` (cap ~300 files).
- `GET /tasks/{id}/changes/diff?repo=<slug>&path=<rel>` → `FileDiff`.

Edge cases: no repos assigned → `{repos:[], files:[]}` (FE falls back to the
existing tool-derived `RunChanges`); repo copy missing → skip; huge file → cap
bytes + `truncated`; rename → carry `old_path`.

## Phase 2 — Frontend data layer

- `api/types.ts`: `GitChangeStatus = "A"|"M"|"D"|"R"|"U"`, `TaskChangeEntry`,
  `TaskChangeRepo`, `TaskChangesResponse`, `TaskFileDiff`.
- `api/client.ts`: `getTaskChanges(taskId)`, `getTaskChangeDiff(taskId, repo, path)`.
- `api/hooks.ts`: `useTaskChanges(taskId)`, `useTaskChangeDiff({taskId, repo,
  path, enabled})` (lazy, `staleTime` ~5m). Invalidated on run/loop SSE events.

## Phase 3 — Diff primitive `<TaskDiff>`

`components/TaskDiff.tsx` over Monaco. Props: `original`, `modified`, `path`,
`mode: "diff"|"old"|"new"`. `diff` renders a side-by-side (or inline)
`DiffEditor` with `hideUnchangedRegions`; `old`/`new` render a single editor.
Height auto-grows to content (capped). Themes `agent-team-dark/-light` defined in
`lib/monaco-setup.ts`; language from `lib/monacoLanguage.ts`.

## Phase 4 — Code workspace shell + Changes view

Dir `features/board/cockpit/code/`:

- `CodeWorkspace.tsx` — toolbar (Changes/Files toggle, repo select, search,
  `+x −y` summary, refresh); lifts open-file state so Changes can deep-link a
  file into a Files tab.
- `ChangesView.tsx` — header summary + list of `FileDiffCard`; empty/loading/
  "no repo → see thread Changes" states; groups by repo when multi-repo.
- `FileDiffCard.tsx` — collapsible header (status badge A/M/D/R/U, path, `±`),
  lazy `useTaskChangeDiff` on expand, `<TaskDiff>` body, old/diff/new toggle,
  deleted/binary placeholders, "open as tab" action.
- `changeMeta.tsx` — status label/colour metadata + `StatusBadge`.

TaskCockpit wiring: `CODE` constant + a `ThreadItem` ("Code", sub
"N files changed" from `useTaskChanges`), render `<CodeWorkspace>` in the middle
section.

## Phase 5 — Files view

- `filePriority.ts` — priority ranking (entrypoints first).
- `FilesView.tsx` — quick-access pills + a Changed/All toggle + tree on the
  left; tab pane on the right.
- `WorkspaceTree.tsx` — `changed` mode (synthetic, fully-searchable tree from the
  change set + status dots) and `all` mode (lazy real workspace tree with status
  dots overlaid).
- `FileTabsPane.tsx` — open multiple files as tabs (close per-tab).
- `FileContentViewer.tsx` — read-only viewer (image / Monaco source /
  markdown rich-or-source / binary download).

## Phase 6 — Live updates & integration polish

- `BoardEventsContext` invalidates `taskChanges` + the file tree on run/loop SSE
  events, so the Code workspace stays live as agents run.
- Right-panel Artifacts clicks open a tab in the Code workspace.
- The "Code" thread badge (N files changed) updates live.

## Phase 7 — Styling pass

Global `.dark` theme retuned to a deep cool-grey palette
(`--background`/`--surface-*`/`--border`/`--code-bg`), with Monaco's editor
background matched to `--code-bg` so the editor reads one tier deeper than the
surrounding cards. The right panel collapses while the Code thread is open.

## Phase 8 — Tests & docs

- BE tests (`tests/test_agent_team.py`): seed a tmp git repo with committed +
  uncommitted + untracked + deleted + renamed changes; assert the change list +
  a sample diff; assert the viewer guard.
- FE unit tests: `filePriority`, synthetic change-tree grouping.
- Update the wiki (boards/tasks/workspaces file-browser section + index map).

## Rollout order

P1 → P2 → P3 → P4 (ship Changes first — biggest win) → P5 → P6 → P7 → P8.
