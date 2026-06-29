# Agent tools & autopilot

Last updated: 2026-06-29 · [↩ index](../index.md) · Source: `plugin.py`,
`features/board/runtime/`

## Agent tools

While the plugin is enabled it contributes three **tool factories** (registered in
`plugin.py :: tool_factories()`; the registry drops them when the plugin is
disabled). Each is on-by-default per agent and toggleable in the agent's tool
config.

| Tool | Key | What it does |
|---|---|---|
| **View Image** | `enable_agent_team_view_image` | returns a workspace image as a multimodal content block so a **vision-capable** model can actually *see* it (the text file tools can't). Impl: `runtime/image_tools.py`. |
| **Git Push** | `enable_agent_team_git_push` | pushes a board repo's task working copy to its remote using the repo's stored credentials — still gated per-repo by the admin `allow_push` policy. Impl: `runtime/git_tools.py`. |
| **Set Task Status** | `enable_agent_team_set_task_status` | lets the agent move its own task between the board's columns (review/done/blocked). Especially useful with autopilot. Impl: `runtime/status_tools.py`. |

> These are **LangChain tools** for LLM graph agents. Direct-CLI agents don't get
> them automatically; equivalent capability for CLI agents comes from their native
> file/git tools (e.g. a plain `git push`, see
> [`repositories.md`](repositories.md)) or, in future, MCP.

## Autopilot

`features/board/autopilot_scheduler.py` + `features/board/runtime/autopilot.py`.

**Why:** assigned tasks should be able to move themselves without a human
mentioning an agent every time — the board can run a column/queue on a schedule.

**How it runs (and why it's subtle):** the autopilot ticker is **not** started in
`on_startup` (which runs before the event loop and possibly in a non-serving
process). It is started from the **loop-capture app's lifespan** (see
[`../architecture.md`](../architecture.md)) so the ticker and the captured event
loop share a process, guaranteeing `dispatch_start` can hand runs to a live loop.
An `fcntl` lock ensures only one worker runs it under multi-worker deployments.

Runs are dispatched through `runtime/dispatch.py` (the thread→loop bridge) and
then go through the **same run lifecycle** as a manual mention
([`runtime-and-runs.md`](runtime-and-runs.md)). Autopilot can target the
autonomous loop for assigned tasks.

Per-task one-off/recurring schedules are stored in `AgentTeamTaskSchedule`
(`runtime/task_schedule.py`).

## Related

- The run lifecycle these feed into → [`runtime-and-runs.md`](runtime-and-runs.md)
- Push gating → [`repositories.md`](repositories.md)
