# Development guide

Last updated: 2026-07-31 · [↩ index](../index.md)

How to build, test, lint, and add a feature end-to-end the way this codebase
expects.

## Build / test / lint

From the agent-manager repo root:

```bash
# Backend tests (the plugin's suite)
PYTHONPATH=community_plugins uv run pytest community_plugins/agent_team/tests/test_agent_team.py -q
# or a focused file
uv run pytest community_plugins/agent_team/tests/test_communication_gateway.py -q

# Lint
uv run ruff check community_plugins/agent_team
```

Web UI (from `web-ui/`):

```bash
npm install                 # first time
npm run build:agent-team    # vite build (base /agent-team/) → copies dist into ../static/
npm run dev                 # local hot reload at http://localhost:5173 (proxies API)
```

Always commit the updated `static/` so the plugin works without a build step on
deploy. After UI edits, type-check with `npx tsc --noEmit`.

> **Sandbox note:** a few tests touch git/network and only pass outside the
> sandbox. If a git-credential test fails in a restricted run, re-run it with full
> permissions before assuming a regression.

## Adding a feature end-to-end (the recipe)

The codebase has a consistent shape. To add, say, a new entity + endpoint:

1. **Model** — add the SQLAlchemy model to the right `features/*/models.py`. Use a
   32-char uuid hex PK, tz-aware timestamps. Secrets are plaintext + write-only
   (expose only `has_*`).
2. **Register it** in `plugin.py :: models()`. If
   `tests/test_agent_team.py::test_plugin_meta_models_and_menu` asserts the table
   list, update that list too.
3. **Migration** — add `db_migrations/NNN_name.sql`. New *tables* should use
   `CREATE TABLE IF NOT EXISTS` (the ORM auto-creates them on startup; the
   migration is then a no-op). New *columns* on existing tables need the migration
   with `skip_if_column_exists` / `skip_if_table_missing` directives.
4. **Repository** — add data-access functions in `features/*/repositories*` (one
   module per entity for board; a single `repositories.py` for repos/comm). Keep
   SQL out of the router.
5. **Schemas** — add Pydantic DTOs in `schemas.py`. DTOs must never leak secrets.
6. **Router** — add thin endpoints that check the board role
   (`features/board/authz.py`) and delegate to repositories/services. Reuse shared
   error helpers (`bad_request`, etc.).
7. **Web UI** — add types in `web-ui/src/api/types.ts`, a client method in
   `client.ts`, a hook in `hooks.ts`, then the component under
   `web-ui/src/features/`. Build with `npm run build:agent-team`.
8. **Tests** — add a focused test file under `tests/`. Router tests monkeypatch
   auth and use an in-memory DB; remember to import `features/board/models` so FK
   targets are registered.
9. **Wiki** — update the relevant page in `docs/wiki/` (and this guide / the
   catalog if you added a subsystem).
10. **User guide** — when behaviour changes what an end user must understand,
    configure, click, approve, or verify, update the relevant file in
    `user-guide/`. See
    [`../pages/user-guide.md`](../pages/user-guide.md) for the reader contract.

## Updating the user guide

The in-app Guide is compiled from `user-guide/*.md`; it is not read dynamically
by the backend. After editing guide content:

```bash
cd community_plugins/agent_team/web-ui
npm run typecheck
npm run test
npm run build:agent-team
```

The last command regenerates `static/`, which must be committed. A new chapter
also needs a metadata entry in
`web-ui/src/features/guide/guideContent.ts` and an updated expected document
count in `guideContent.test.ts`.

## Gotchas worth knowing

- **Migration parser + semicolons in comments.** The migration runner splits on
  `;`. A semicolon inside a comment that follows a blank line after the directives
  can be misread as a statement separator. Keep comments immediately after the
  directives and avoid stray semicolons in comments (this bit `027`).
- **`ruff` import order (`I001`) and line length (`E501`).** Run `ruff check --fix`
  for imports; wrap long f-strings manually (you can't `.strip()` past 100 cols on
  one line — pull the value into a variable first).
- **Don't start tickers in `on_startup` if they need the loop.** Use the
  loop-capture app's lifespan (see [`../architecture.md`](../architecture.md)).
- **Frames are the contract.** New execution paths must emit the same
  `AgentTeamRunEvent` frames so the SSE/UI keep working unchanged.
- **Workspace path safety.** Anything that resolves a path from JSON/user input
  must reject traversal and stay inside the task workspace (see
  `runtime/loop/planning_artifacts.py`).

## Test files map

| File | Covers |
|---|---|
| `tests/test_agent_team.py` | core plugin: models/menu, boards/tasks/runs, repos, wiki skill, jira, autopilot. |
| `tests/test_planning_workflow.py` | planning prompts, artifact validation, planning states. |
| `tests/test_task_journal.py` | journal repo, agent-note ingestion, lifecycle entries. |
| `tests/test_acp_owned_engine.py` | ACP/direct-CLI engine wiring. |
| `tests/test_communication_gateway.py` | provider send/render, delivery. |
| `tests/test_communication_router.py` | `/comm/*` + board channel endpoints, authz. |
| `tests/test_communication_inbound.py` | v2 inbound: verified resolver, repos, executor, `human_actions`. |
