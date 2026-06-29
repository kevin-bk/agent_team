# Glossary

Last updated: 2026-06-29 · [↩ index](index.md)

One-line definitions for the vocabulary used across this wiki.

- **Board** — a Kanban board; the top-level container for tasks, assigned agents,
  repos, Jira config, and a channel.
- **Task** — an issue on a board; owns a workspace folder and a `loop_state`.
- **Workspace** — the per-task folder on disk where agents read/write with their
  file/shell tools. Path: `workspaces/agent_team/<board_slug>/<task_key>/`.
- **Conversation** — one chat thread per `(task, agent)` pair.
- **Run** — exactly **one turn** of one agent against a task. Has a `role`.
- **RunEvent** — an append-only stream frame (`text_delta`, `tool_use_*`, …) with
  a monotonic `seq`; the source of truth for replay + SSE.
- **Worker** — the abstraction that drives one turn. `LlmGraphWorker` (LangGraph)
  or `AcpCliWorker` (direct CLI over ACP).
- **ACP** — Agent Client Protocol; how direct-CLI agents (Claude Code, Cursor
  CLI, Codex CLI) are driven. Shared infra in `plugins/ai_code/tools/_acp_base.py`.
- **Direct-CLI agent** — an agent whose alias is `cli:*`; runs through ACP rather
  than a LangGraph graph.
- **StreamTranslator** — `runtime/translator.py`; parses a LangGraph LLM stream
  into the standard `AgentTeamRunEvent` frames so LLM and CLI paths look identical.
- **Checkpointer** — LangGraph's per-thread conversation-state store; lets a
  follow-up turn resume a conversation with its full prior history.
- **MCP** — Model Context Protocol; the standard for plugging external
  tools/connectors into an agent (e.g. a board's MCP servers, or a future journal
  tool for CLI agents).
- **Board bus** — the in-process pub/sub (`features/board/board_events.py`,
  `get_board_bus()`) that fans board/task/run/loop events out to the SSE streams
  the cockpit tails (e.g. `loop.status`, `run.started`).
- **Skill pack / skills manifest** — a bundled folder of agent instructions (e.g.
  `board-wiki`) materialised into the workspace (`.claude/skills` / `.cursor/skills`)
  and advertised to the agent via the skills manifest (`runtime/skills.py`).
- **Autonomous loop** — controller + generator + independent evaluator that drives
  a task to *verified* completion.
- **Generator** — the worker role that implements the task inside the loop.
- **Evaluator** — an independent worker that grades the result with evidence;
  **owns completion** (the generator cannot mark work complete).
- **Controller** — the no-I/O decision logic: continue vs stop vs done.
- **Budget / `LoopBudget` / `LoopLedger`** — guardrails (attempts, tokens, cost,
  runtime) and the running accounting against them.
- **`loop_state`** — the single canonical public lifecycle state of a task
  (`planning`, `waiting_plan_approval`, `plan_approved`, `running`,
  `waiting_for_human`, `plan_change_requested`, `waiting_answers`, `complete`,
  `failed`, `cancelled`).
- **Strict planning** — the mode where a planner+reviewer produce an approved
  artifact contract before execution; opposite of `legacy_plan`.
- **Planning artifacts** — the files under `.agent-team/` that form the contract:
  `SPEC.md`, `PLAN.md`, `TASKS.json`, `PLAN_REVIEW.json`, `EVIDENCE.json`,
  `PLAN_CHANGE_REQUEST.md`, `QUESTIONS.json`.
- **Task Journal** — an append-only *semantic* timeline of decisions, assumptions,
  questions, approvals, verdicts. Distinct from raw run events.
- **Agent note inbox** — `.agent-team/JOURNAL_NOTES.jsonl`; agents append
  suggested notes, the backend validates + ingests them.
- **Repo (board)** — a registered git repository; canonical clone is pulled on a
  schedule, each task gets a cheap `--local` working copy on branch
  `agent/<task-key>`.
- **Board Wiki** — a board repo marked `is_wiki`: an LLM-maintained knowledge base
  (Karpathy *LLM Wiki* pattern).
- **Communication gateway** — the provider-agnostic subsystem for external human
  communication: outbound notifications (v1) and inbound chat actions (v2).
- **Connection** — a provider account (Mattermost/Slack bot token), owner-scoped.
- **Channel** — a board↔connection link with a target channel + event allowlist.
- **`dedupe_key`** — key on a delivery that prevents repeated events from spamming.
- **`verified`** — flag on a user link that gates whether a chat reply may *act*
  on a task (inbound authorization).
- **Autopilot** — the scheduler that dispatches agent runs for assigned tasks.
- **Tool factory** — how the plugin contributes agent tools (`view_image`,
  `git_push`, `set_task_status`); only offered while the plugin is enabled.
