# Data model

Last updated: 2026-06-29 · [↩ index](index.md)

Every persisted entity, grouped by subsystem. All tables are prefixed
`plugin_agent_team_…`. Models are registered in `plugin.py :: models()` and split
across three feature packages: `features/board/models.py`,
`features/repos/models.py`, `features/comm/models.py`.

> **Convention:** string PKs are 32-char uuid hex. Timestamps are timezone-aware.
> Secrets (tokens, keys) are stored as **plaintext, write-only** — the API exposes
> only a `has_*`/presence boolean and never returns the secret.

## Board core (`features/board/models.py`)

| Model | Table | Purpose |
|---|---|---|
| `AgentTeamKeySeq` | `…_key_seq` | per-board monotonic counter for human task keys (e.g. `ABC-12`). |
| `AgentTeamBoard` | `…_board` | a Kanban board: columns config, Jira config, autopilot config, starter prompt, skills. |
| `AgentTeamBoardMember` | `…_board_member` | board membership + role (owner/editor/viewer). |
| `AgentTeamTask` | `…_task` | the issue: type, status/column, priority, assignee, reporter, labels, description, **`objective`**, **`execution_mode`** (chat/autonomous), **`planning_mode`** (legacy_plan/strict_plan), **`loop_state`**, and **`planning_meta_json`** (approval metadata + remembered run params). |
| `AgentTeamConversation` | `…_conversation` | one thread per `(task, agent)` pair. |
| `AgentTeamRun` | `…_run` | one agent turn: status, **`role`** (chat/generator/evaluator/…), **`attempt_id`**, usage. |
| `AgentTeamRunEvent` | `…_run_event` | append-only stream frames (monotonic `seq`) — replay + SSE source of truth. |
| `AgentTeamComment` | `…_comment` | discussion notes/comments; visibility flag gates agent-visible vs people-only. |
| `AgentTeamActivity` | `…_activity` | board changelog entries. |
| `AgentTeamToolOutput` | `…_tool_output` | large tool output offloaded out-of-band from the event stream. |
| `AgentTeamAutopilot` | `…_autopilot` | per-board autopilot configuration. |
| `AgentTeamTaskSchedule` | `…_task_schedule` | scheduled/recurring task runs. |

### Autonomous loop entities

| Model | Table | Purpose |
|---|---|---|
| `AgentTeamAttempt` | `…_attempt` | one loop iteration (a generator turn + its evaluation). |
| `AgentTeamEvaluation` | `…_evaluation` | the independent evaluator's verdict for an attempt (`pass`/`fail`/`needs_human`, score, missing, evidence). |
| `AgentTeamVerificationReceipt` | `…_verification_receipt` | backend-owned proof for one approved command: repo/cwd, exit/duration/output hashes, source fingerprints, runtime identity, task/attempt/batch binding. |
| `AgentTeamJournalEntry` | `…_journal_entry` | the semantic decision timeline (see [`pages/task-journal.md`](pages/task-journal.md)). |

`Task.loop_state` is the **single canonical public lifecycle** (see
[`pages/autonomous-loop.md`](pages/autonomous-loop.md) for the state machine).
There is intentionally **no** competing top-level planning state machine —
planning info is metadata, `loop_state` is the truth.

## Repositories (`features/repos/models.py`)

| Model | Table | Purpose |
|---|---|---|
| `AgentTeamRepo` | `…_repo` | a first-class git repo: `owner_id`, `git_url`, auth (none/token/ssh, write-only), schedule (off/interval/cron), clone/sync status. |
| `AgentTeamBoardRepo` | `…_board_repo` | N-N link board↔repo: `branch_override`, `allow_push`, **`is_wiki`** (marks this repo as the board's LLM wiki). |

A repo is **not** board-scoped — it's many-to-many, scoped by owner. Details and
the per-task-copy strategy: [`pages/repositories.md`](pages/repositories.md).

## Communication gateway (`features/comm/models.py`)

Outbound (v1) and inbound (v2) live together. The model mirrors the repo pattern:
a shared, owner-scoped connection linked to boards N-N.

| Model | Table | Purpose |
|---|---|---|
| `AgentTeamCommConnection` | `…_comm_connection` | a provider account (Mattermost/Slack): `provider`, `server_url`, bot token (write-only). Owner-scoped, shareable across boards. |
| `AgentTeamBoardChannel` | `…_comm_board_channel` | a board's **single** channel (endpoint `/boards/{id}/channel` is singular) linking it to a shared connection: target `channel_id`, `use_threads`, `tag_mode`, event allowlist, enabled. One connection can back many boards. |
| `AgentTeamCommDelivery` | `…_comm_delivery` | a sent (or skipped/failed) notification, with `dedupe_key`. |
| `AgentTeamCommUserLink` | `…_comm_user_link` | maps an internal user → provider user id/handle; `source` (auto/manual) + **`verified`** (inbound authz gate). |
| `AgentTeamExternalThread` | `…_comm_external_thread` | **(v2)** maps a provider thread root (`root_id`/`thread_ts`) back to a task. |
| `AgentTeamHumanActionRequest` | `…_comm_action_request` | **(v2)** an outstanding "human must act" request behind an actionable notification. |
| `AgentTeamInboundMessage` | `…_comm_inbound_message` | **(v2)** raw inbound provider events, stored before interpretation for debug/replay. |

Full behaviour: [`pages/communication-gateway.md`](pages/communication-gateway.md).

## Migrations

The numbered `db_migrations/*.sql` files tell the data-model's history. A few
landmarks:

| Migration | Adds |
|---|---|
| `009`/`010` | repos + board-repo link, push policy |
| `015` | `board_repo.is_wiki` (Board Wiki) |
| `019`–`021` | loop engineering: attempts/evaluations, run role, `task.loop_state` |
| `023` | task planning fields |
| `024` | task journal |
| `025`–`027` | communication gateway: v1 tables, v2 inbound tables, `comm_user_link.verified` |

Adding a model requires: a migration file **and** registering the model in
`plugin.py :: models()` **and** (if the meta test asserts the table list)
updating `tests/test_agent_team.py`. See
[`guides/development.md`](guides/development.md).
