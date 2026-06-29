# Jira integration

Last updated: 2026-06-29 · [↩ index](../index.md) · Source: `README.md`,
`features/board/jira/`

Two-way bridge between a board and a Jira project.

## Why

Many teams already track work in Jira. Rather than forcing a migration, a board
can **import** Jira issues as tasks and **sync** them, so agents work the real
backlog and humans keep their Jira workflow.

## Configuration

Per board (the **Jira** dialog): base URL, account email, API token, project key.
The token is stored on the board record, write-only like every other secret —
*treat your database as sensitive*.

## What import does

- **Preview + select.** Browse the project's issues with Jira-native filters
  (issue type, status category, updated-within), tick the ones to import, and
  watch progress as they land in the first column.
- **Import by key.** A quick field pulls a single issue without scanning the list.
- **Field mapping.** Summary, description, status, priority, type, and labels map
  onto the task. **Comments** and **issue attachments** are imported too.
- **Inline images.** `!image.png!` markup in a description/comment is rewritten to
  point at the downloaded workspace file so it renders in place.
- **Re-import** updates linked tasks (and edited comments) and refreshes
  attachments.

## Modules (`features/board/jira/`)

| File | Role |
|---|---|
| `client.py` | the Jira REST client. |
| `service.py` | import orchestration (preview, select, create/update tasks, download attachments). |
| `sync.py` | field/comment/attachment mapping + re-import/sync logic. |

Board-level sync status is tracked (migration `017_board_jira_sync_status.sql`);
task↔issue linkage on the task (`005`/`008`).

## Related

- Where imported attachments/images live → [`boards-tasks-workspaces.md`](boards-tasks-workspaces.md)
