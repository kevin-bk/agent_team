# Agent Team — web UI

React + Vite + TypeScript single-page app for the **Agent Team** plugin. It talks
to the plugin's REST API (`/api/agent-team/...`) and streams runs over SSE, and is
served by the plugin under `/agent-team`.

## Stack

- **React 18 + Vite + TypeScript**
- **Tailwind CSS** + shadcn-style primitives (`src/components/ui`)
- **TanStack Query** for server state (`src/api/hooks.ts`)
- **@microsoft/fetch-event-source** — authenticated SSE run streaming
- **react-markdown + highlight.js** — message / description rendering
- **@atlaskit/pragmatic-drag-and-drop** — Kanban drag & drop

## Setup

```bash
npm install
cp .env.example .env        # set the env vars your deployment needs
npm run dev                 # http://localhost:5173 (proxies /api -> :8765)
```

`npm run dev` proxies `/api` to a running agent-manager (default
`http://127.0.0.1:8765`; override with `VITE_API_PROXY`).

## Build (for the plugin)

```bash
npm run build:agent-team
```

This builds the SPA with the `/agent-team/` base path (where the plugin mounts
it), writes to `dist-agent-team/`, then `scripts/copy-to-plugin.mjs` copies the
output into the plugin's `../static/` directory. Commit the updated `static/` so
the plugin ships a ready-to-serve bundle.

> `npm run build` (the plain build, output `dist/`) is the generic build with a
> root base path; use `build:agent-team` for this plugin.

## Layout

```
src/
  api/        REST client, SSE client, TanStack Query hooks, DTO types
  components/ Sidebar, shadcn-style ui primitives, Markdown, jira/ icons
  features/
    board/    Kanban board, task cockpit, agent chat, Jira import dialogs
```
