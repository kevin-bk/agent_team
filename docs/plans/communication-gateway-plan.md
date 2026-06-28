# Communication Gateway for agent_team

Status: Proposed — architecture approved. v1 scope locked to **outbound-only**
(see §0.5). Reviewed against the live codebase on 2026-06-28; corrections folded
into §0.6.
Audience: coding agents implementing external communication, notifications, and
multi-channel chat for `community_plugins/agent_team`.

This document specifies **Communication Gateway**: a provider-agnostic subsystem
that connects Agent Team tasks to external human communication tools such as
Mattermost, Slack, email, webhooks, and future chat surfaces.

The first provider should be Mattermost. The first capability should be
notifications for human-needed states and completion. The architecture must also
support future two-way chat, replies mapped back into tasks, button/slash-command
actions, and task-linked external threads.

> **Reading order:** §0.5 (locked v1 scope) and §0.6 (codebase-grounded
> corrections) override anything later in this document if they conflict. The
> remaining sections (5–24) are the long-term north star, not the v1 contract.

## 0. Naming decision

Use **Communication Gateway** as the subsystem name.

Recommended labels:

- Subsystem/code package: `communication_gateway`
- UI settings section: `Communication Channels`
- Short UI label: `Channels`
- Capability: `Notifications`
- Capability: `Replies & actions`
- Provider: `Mattermost`

Avoid naming the whole subsystem `Notification Channels`. That is too narrow if
the platform will later support:

- chat back and forth with a task from Mattermost
- slash commands
- threaded replies mapped to task comments/questions
- external channel threads synced with task conversations
- provider-specific actions such as approve/retry/ack

Recommended mental model:

```text
Communication Gateway
├── Channels             # Mattermost, Slack, email, webhook, ...
├── Notifications        # outbound alerts
├── Human Actions        # approve, answer, ack, request changes, retry
├── Inbound Messages     # replies, slash commands, buttons/callbacks
└── External Threads     # Mattermost thread ↔ Agent Team task/conversation
```

## 0.5 Locked v1 scope (build this, defer the rest)

The full document is over-scoped for a first release. v1 is deliberately trimmed
to **outbound notifications only**, using a data model that mirrors the existing
**repository management** pattern (`features/repos/models.py`): a shared,
owner-scoped credential entity + an N-N link table with per-board overrides.

### v1 final decisions (locked 2026-06-28)

- **One channel per board** for v1 — but enforced at UI/validation, **not**
  schema. The schema is N-N so multi-channel later needs no migration.
- **User tagging ships in v1** (outbound mention only). Auto-match internal user
  → Mattermost user by **email**, with an admin form to override the username.
  No verified-authz mapping yet (that is v2/inbound).
- Tagging targets: events that need a person → tag the **assignee**; `complete`
  → tag **assignee + reporter (`created_by`)**. No match → send without tag.
- **No DM** in v1 — post into the channel + mention only.
- Bot token stored **plain** (no secret store exists, see §0.6.1), write-only via
  API (`has_token` boolean), masked in logs/journal.

### v1 data model (repo-style N-N — see §5 for full columns)

- `AgentTeamCommConnection` — **owner-scoped, shared** credential entity (mirrors
  `AgentTeamRepo`): provider, server_url, bot_token (write-only), default team.
  Register a Mattermost bot once, reuse across boards.
- `AgentTeamBoardChannel` — **N-N link** board ↔ connection (mirrors
  `AgentTeamBoardRepo`): channel id/name, `use_threads`, `event_allowlist`,
  `tag_mode`, `enabled`. UI enforces one active link per board for v1.
- `AgentTeamCommDelivery` — one send attempt; `channel_id` → `BoardChannel.id`.
- `AgentTeamCommUserLink` — **connection-scoped** user↔Mattermost mapping (the
  `mm_user_id` differs per server): connection_id, user_id, mm_user_id,
  mm_username, source=`auto|manual`. (v2 adds `verified` for inbound authz.)

### v1 UI (repo-style)

- An **owner-level "Communication connections" registry** (mirrors the Repos
  page): register server + bot token, and the **user-mapping form** (board
  member ↔ Mattermost username, auto by email + manual override). Configured
  once, reused by every board.
- **Board Settings → Channel section**: pick a registered connection, pick the
  channel, set `event_allowlist` + `tag_mode`, and **Send test**.

### v1 build checklist

- Provider via **bot account + REST API** (not incoming webhook), porting
  outbound/thread patterns from the existing adapter (§0.6.8), reading the
  per-connection token from the DB.
- One outbound dispatch chokepoint (§0.6.3) with `dedupe_key`.
- A Task Journal entry for each sent/failed notification.
- Test-send endpoint + the UI above.

**Deferred to later slices (designed in §5–§24, do not build for v1):**

- `AgentTeamHumanActionRequest`, `AgentTeamInboundMessage`,
  `AgentTeamExternalThread`; the `AgentTeamCommRule` table (v1 uses an
  `event_allowlist` on the link row instead).
- All inbound: replies, slash commands, interactive buttons.
- `verified` authz on `AgentTeamCommUserLink`; DM delivery.
- Comment-bridge and chatbot modes.

This matches the document's own §21 ordering — v1 is "stop after step 7".

## 0.6 Codebase-grounded corrections

These corrections come from checking the plan against the live code. Where they
conflict with §5–§24, **these win**.

### 0.6.1 No secret store exists — store the bot token like `jira_api_token`

The plan (§5.1, §19) assumes a "platform secret store or encrypted fields". That
facility does **not** exist in `agent_team`. Secrets today are stored **plain**:
`AgentTeamBoard.jira_api_token` is a plain column and `agent_mcp_json` holds raw
MCP credentials as plain JSON.

Therefore:

- Store the Mattermost bot token as a **dedicated plain column** on the channel
  (or board), mirroring `jira_api_token`. Do **not** block v1 on building
  encryption.
- Never echo the token in API responses. Expose only a boolean such as
  `has_token` (mirror `AgentTeamBoard.jira_has_token()`).
- Add the bot token to the **secret-masking** path so it is masked in logs and
  Task Journal, reusing the existing masking used for MCP secrets
  (`SecretMasker` / the `_collect_*_secrets` pattern in
  `runtime/task_journal.py`).

### 0.6.2 Event keys come from `LoopState` (verified) — but not all do

The §6 mapping is **correct** against `runtime/loop/status.py`: `PLANNING`,
`WAITING_PLAN_APPROVAL`, `PLAN_APPROVED`, `RUNNING`, `COMPLETE`,
`WAITING_FOR_HUMAN`, `PLAN_CHANGE_REQUESTED`, `WAITING_ANSWERS`, `FAILED`,
`CANCELLED` all exist. Key notifications off **`LoopState`**, not the terminal
outcome strings (`plan_change`, `needs_answers`, …) which `outcome_to_state()`
maps internally — keying off outcomes risks drift.

Caveat: `review_failed`, `budget_hit`, and `task_blocked` have **no dedicated
`LoopState`**. They surface as journal entries / task-graph events, so the
dispatcher must read **two sources** (loop state + journal), not just state.

### 0.6.3 One dispatch chokepoint, not five scattered hooks

§13 lists hooks across `router.py`, `planning.py`, `service.py`, `task_graph.py`
and `driver.py`. That is too much duplicate-send surface. For v1, dispatch from a
**single chokepoint**:

- Subscribe to the existing `loop.status` board-bus event (the same event the
  UI/SSE already consumes) for execution-state transitions, **or** derive
  notifications from newly written **Task Journal entries** (we already write
  entries at every relevant lifecycle point with `type`/`severity`).
- Add an explicit `notify_task_event(...)` call **only** where `loop.status`
  lacks payload the message needs (e.g. the list of open questions for
  `answers_required`).

This collapses the trigger surface to one subscriber + a couple of explicit
calls, and makes `dedupe_key` trivial to enforce.

### 0.6.4 Multi-process: inbound must go through the DB + existing HTTP endpoints

Loops run as **in-process asyncio tasks**; the board bus and SSE are
**in-process** too. With more than one worker, an inbound webhook can land on a
**different process** than the one running the loop. So (when inbound is built):

- Inbound resolves purely via DB records (the action-request row), never
  in-memory state.
- To act, inbound **calls the same service/HTTP endpoints the cockpit already
  calls** — `/planning/answer`, `/planning/approve`, `/loop/ack`,
  `/planning/request-changes` — which already work cross-process and already
  resume the loop. Do not re-implement loop resumption inside the gateway.

### 0.6.5 User mapping is the hard prerequisite for ANY inbound action

§10 buries this. Mapping a Mattermost user → internal Agent Team user (for
`authz.guard_task`) is the gating dependency for inbound. The core `User` model
may not carry a provider id, so add a small explicit mapping table:

```text
AgentTeamCommUserLink
  id
  provider                 # mattermost
  provider_user_id
  user_id                  # internal Agent Team user
  verified                 # bool (e.g. via email match or admin confirm)
  created_at
```

Rule: **no mapping → inbound actions are disabled for that user** (fall back to
"open the web UI" link). This table is a prerequisite of the inbound slice, not
an afterthought.

### 0.6.6 `approve_and_run` is web-only in early inbound

From a chat reply you cannot safely supply run params (agent/evaluator ids,
budgets). So the **inbound action set** is limited to:

- v1-inbound: `answer_questions`, `ack_complete`, `note`, and `approve_plan`
  (park at `plan_approved` only).
- `approve_and_run` stays **web-UI only** unless the board has explicitly saved
  default run params that are safe to reuse.

### 0.6.7 Privacy default is enforced, not optional

Default render must post **title + short reason + deep link only**. Never post
artifact bodies, full questions text dumps, journal bodies, or logs into a
channel by default (channels may be broad/public). Make this the hard default in
the render module, with an opt-in to include more.

### 0.6.8 Reuse the existing Mattermost adapter

There is already a working bot-API adapter at
`coding/hermes-agent/gateway/platforms/mattermost.py`: bearer bot token, threaded
posts via `root_id`, event handling. Port its **outbound + threading + payload**
patterns instead of writing from scratch — this confirms the plan's "bot API over
incoming webhook" choice (§8) and de-risks v1. The one change needed: that
adapter reads a single `MATTERMOST_TOKEN` env var; `agent_team` must instead read
the **per-board token from the DB** column added in §0.6.1.

## 1. Goals

The Communication Gateway should:

- notify humans when a task needs attention
- notify humans when a goal completes
- support Mattermost first, with clean provider abstraction
- support multiple configured channels per board
- support rules deciding which events go to which channels
- support human replies/actions from Mattermost when safe
- map external threads/messages back to tasks
- write important communication events into Task Journal
- avoid requiring humans to keep the web UI open and refresh it

The future end state:

- A task can have an external Mattermost thread.
- Agent Team can post updates into that thread.
- A human can reply in that thread.
- Safe replies become answers/comments/journal notes.
- Explicit commands/buttons can approve plans, answer questions, acknowledge
  completion, request changes, or resume execution.
- The same subsystem can later support Slack, email, Telegram, Discord, generic
  webhooks, or SMS.

## 2. Non-goals

Do not implement all provider-specific complexity at once.

Out of scope for the first implementation slice:

- full Slack support
- email inbound parsing
- arbitrary natural-language intent execution
- letting unauthenticated external users control tasks
- replacing the web cockpit
- replacing Task Journal, planning artifacts, or run transcripts
- fully syncing every task message into Mattermost in real time

The first provider should prove the provider interface, outbound notifications,
and a small safe inbound action set.

## 3. Relationship to existing systems

Existing systems and their roles:

- `task.loop_state`: canonical task lifecycle.
- Task Journal: durable semantic audit timeline.
- Planning artifacts: approved contract and evidence.
- Board event bus/SSE: browser realtime refetch hints.
- Activity/run transcripts: raw in-app history.
- Human notes/comments: discussion inside the task cockpit.

Communication Gateway should not replace these.

It should:

- subscribe to or be called from lifecycle/journal points
- send outbound messages externally
- store delivery/inbound/action records
- call existing task/planning APIs/helpers after validating external input
- append journal entries for notifications sent, inbound replies, accepted
  actions, ignored stale replies, and authorization failures

## 4. Core concepts

### 4.1 Channel

A configured external destination.

Examples:

- Mattermost channel `town-square`
- Mattermost private channel
- Mattermost DM to a user
- Slack channel
- email address/list
- generic webhook URL

### 4.2 Notification event

An internal event that may need outbound delivery.

Examples:

- `plan_approval_required`
- `answers_required`
- `plan_change_requested`
- `human_review_required`
- `goal_complete`
- `goal_failed`

### 4.3 Delivery

One attempt to send one notification event through one channel.

Tracks:

- provider
- target channel
- provider message id
- provider thread id
- status
- error
- dedupe key

### 4.4 Human action request

A specific thing a human can do from an external tool.

Examples:

- approve a plan
- answer one or more questions
- request changes
- acknowledge completion
- retry/resume execution
- add a note

Every actionable notification should create a `HumanActionRequest`. Inbound
replies/buttons/slash commands resolve this request only after validation.

### 4.5 Inbound message

Any incoming provider payload:

- Mattermost threaded reply
- slash command
- interactive button callback
- webhook callback

Inbound messages are stored before interpretation so debugging is possible.

### 4.6 External thread

A durable mapping between Agent Team and an external conversation thread.

Examples:

- Task `T-42` ↔ Mattermost channel `abc`, root post `post123`
- Task conversation `conv_x` ↔ Mattermost thread `post456`

This is the key for future multi-channel chat.

## 5. Data model

Add these models/tables. Names are intentionally provider-agnostic.

> **v1 supersedes the single-table design below — see §0.5.** v1 uses the
> repo-style N-N split: an owner-scoped `AgentTeamCommConnection` (credentials,
> like `AgentTeamRepo`) + a board↔connection link `AgentTeamBoardChannel`
> (per-board routing, like `AgentTeamBoardRepo`). The `AgentTeamCommChannel`
> single table sketched in 5.1 is **replaced** by that pair; `AgentTeamCommRule`
> (5.2) is replaced by an `event_allowlist` on the link row. `AgentTeamCommDelivery`
> (5.3) stays but its `channel_id` points at `AgentTeamBoardChannel.id`.
> `AgentTeamCommUserLink` ships in v1 (connection-scoped, tagging only — no
> `verified` flag yet). `AgentTeamHumanActionRequest` (5.4),
> `AgentTeamInboundMessage` (5.5), and `AgentTeamExternalThread` (5.6) are the
> **inbound** slice — defer them.

### 5.1 `AgentTeamCommChannel`

```text
id
board_id
provider                         # mattermost | slack | email | webhook
kind                             # channel | dm | webhook | email
display_name
target_id                        # provider-specific channel/user/webhook id
target_name                      # readable channel/user name
config_json                      # provider-specific config, no raw secrets if avoidable
enabled
created_by
created_at
updated_at
```

Provider-specific Mattermost config examples:

```json
{
  "server_url": "https://mattermost.example.com",
  "team_id": "team123",
  "channel_id": "channel123",
  "channel_name": "agent-team",
  "bot_user_id": "bot123",
  "use_threads": true
}
```

Secrets such as bot token/signing token: see **§0.6.1**. There is no secret
store in this codebase — store the token as a dedicated plain column (mirroring
`AgentTeamBoard.jira_api_token`), expose only a `has_token` boolean via the API,
and add it to the secret-masking path. Do not put raw tokens in `config_json`.

### 5.2 `AgentTeamCommRule`

```text
id
board_id
channel_id
event_type                       # plan_approval_required, goal_complete, ...
enabled
filters_json                     # optional: task labels, assignee, priority, type
created_at
updated_at
```

Rules decide which events go to which channels. A board can route:

- all human-needed events to `#agent-team`
- high-priority only to `#urgent`
- completion only to a quieter channel
- personal tasks to DM in the future

### 5.3 `AgentTeamCommDelivery`

```text
id
task_id
board_id
channel_id
journal_entry_id nullable
human_action_request_id nullable
event_type
provider
target_id
provider_message_id nullable
provider_thread_id nullable
status                           # queued | sent | failed | skipped
dedupe_key
payload_json
error
created_at
sent_at
updated_at
```

`dedupe_key` prevents repeated alerts when a state is re-published or the same
event is processed twice.

Suggested dedupe:

```text
{task_id}:{event_type}:{loop_state}:{artifact_etag_or_attempt_id_or_journal_seq}
```

### 5.4 `AgentTeamHumanActionRequest`

```text
id
task_id
board_id
kind                             # approve_plan | answer_questions | resolve_plan_change | ack_complete | request_changes | retry | note
status                           # open | resolved | expired | cancelled
expected_state                   # waiting_plan_approval, waiting_answers, complete, ...
allowed_user_ids_json
payload_json                     # questions, artifact etags, run params, etc.
response_json
created_by                       # system or user
resolved_by nullable
notification_delivery_id nullable
expires_at nullable
created_at
resolved_at nullable
updated_at
```

This table is the safety boundary for inbound replies. Do not parse arbitrary
Mattermost text directly into loop actions without an open action request.

### 5.5 `AgentTeamInboundMessage`

```text
id
provider                         # mattermost
provider_event_type              # post_created | slash_command | interactive_action
provider_user_id
provider_channel_id
provider_post_id nullable
provider_root_id nullable
raw_payload_json
text
matched_task_id nullable
matched_action_request_id nullable
status                           # received | ignored | accepted | failed
reason nullable
created_at
processed_at nullable
```

Store inbound first, process second. This makes debugging and replay possible.

### 5.6 `AgentTeamExternalThread`

```text
id
board_id
task_id nullable
conversation_id nullable
provider                         # mattermost
provider_channel_id
provider_root_id                 # Mattermost root post id
provider_thread_id nullable
title
status                           # active | archived
created_by
created_at
updated_at
```

One task can have multiple external threads later, but v1 can enforce one active
Mattermost thread per task per channel.

## 6. Event types

Start with these notification event types:

```text
plan_approval_required
answers_required
plan_change_requested
human_review_required
goal_complete
goal_failed
goal_cancelled
task_blocked
budget_hit
review_failed
```

Recommended mapping from existing state/outcomes:

```text
LoopState.WAITING_PLAN_APPROVAL  -> plan_approval_required
LoopState.WAITING_ANSWERS        -> answers_required
LoopState.PLAN_CHANGE_REQUESTED  -> plan_change_requested
LoopState.WAITING_FOR_HUMAN      -> human_review_required
LoopState.COMPLETE               -> goal_complete
LoopState.FAILED                 -> goal_failed
LoopState.CANCELLED              -> goal_cancelled
```

Task graph:

```text
subtask blocked                  -> task_blocked
budget/cost/runtime guardrail    -> budget_hit
reviewer verdict fail            -> review_failed
```

Do not notify on every `running` update.

## 7. Provider interface

Create a provider-agnostic interface:

```python
@dataclass
class CommMessage:
    title: str
    body: str
    url: str | None = None
    severity: str = "info"
    actions: list[CommAction] = field(default_factory=list)
    thread_key: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class DeliveryResult:
    ok: bool
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    error: str | None = None


class CommunicationProvider(Protocol):
    provider = "mattermost"

    async def send(self, channel: CommChannel, message: CommMessage) -> DeliveryResult: ...
    async def update(self, delivery: CommDelivery, message: CommMessage) -> DeliveryResult: ...
    async def parse_inbound(self, request: Request) -> InboundMessage: ...
```

Implementation can be synchronous internally if that matches the app, but keep
the provider boundary narrow.

Provider registry:

```python
def resolve_provider(name: str) -> CommunicationProvider:
    if name == "mattermost":
        return MattermostProvider()
    ...
```

## 8. Mattermost provider

Mattermost should be the first provider.

Recommended integration mode:

- Use bot account + REST API for outbound messages.
- Use interactive messages/buttons for explicit actions where available.
- Use slash commands for reliable command-style inbound actions.
- Use outgoing webhook or plugin webhook for threaded reply ingestion.

Why bot API over incoming webhook:

- need provider post id
- need thread root id
- need update/reply support
- need DM support later
- need reliable mapping for inbound replies

Mattermost outbound message should include:

- task key/title
- event title
- short reason
- current state
- action request id embedded in action context
- deep link to Agent Team task
- concise action buttons when safe
- reply instructions when free-text is acceptable

Example:

```text
T-42 needs your answer

Agent paused instead of guessing.

Question:
Which target branch should this PR use?

Actions:
[Answer in Agent Team] [Open task]

You can also reply in this thread:
answer: <your answer>
```

For approval:

```text
T-42 plan is ready for approval

Reviewer verdict: pass
Artifacts: SPEC.md, PLAN.md, TASKS.json

Actions:
[Approve] [Request changes] [Open task]

For safety, casual replies are added as notes. Use a button or slash command to approve.
```

## 9. Human action safety rules

Inbound actions must be conservative.

Never let casual text automatically do high-impact actions unless it is an
explicit command in an open action request thread and the user is authorized.

### 9.1 Safe auto-map

These can be auto-mapped from a reply in the correct external thread:

- `answer_questions`: reply starting with `answer:` or a button/modal answer
- `ack_complete`: reply `ack` / `acknowledge` or button
- `note`: reply starting with `note:`

### 9.2 Explicit command or button required

These require a button, slash command, or exact command phrase:

- approve plan
- approve and run
- resolve plan change
- retry/resume execution
- cancel
- request changes

Examples:

```text
/agent-team approve T-42
/agent-team approve-and-run T-42
/agent-team request-changes T-42 Keep it read-only.
/agent-team answer T-42 Q1 Use Postgres.
/agent-team ack T-42
```

### 9.3 Never auto-run

Ignore or convert to journal note when:

- reply is `ok`, `sure`, `looks good` without explicit command
- reply is from an unauthorized Mattermost user
- action request is stale/resolved/expired
- task state no longer matches expected state
- reply is in wrong channel/thread
- multiple open actions could match
- provider signature/token validation fails

Every ignored inbound action should be recorded in Task Journal if it is relevant
to task debugging.

## 10. Inbound routing

Inbound processing pipeline:

```text
provider request
  -> verify provider signature/token
  -> store AgentTeamInboundMessage(status=received)
  -> map provider user to Agent Team user
  -> find ExternalThread by channel/root post
  -> find open HumanActionRequest
  -> parse reply/button/slash command
  -> authz.guard_task role check
  -> state check
  -> execute action
  -> mark action request resolved
  -> update delivery/inbound status
  -> append Task Journal entry
  -> optionally reply/update Mattermost thread
```

User mapping options (see **§0.6.5** — this is a hard prerequisite, use the
`AgentTeamCommUserLink` table):

- primary: Mattermost user id stored in `AgentTeamCommUserLink`
- fallback: verified email match
- fallback: username mapping only if explicitly configured

Do not allow unknown external users to resolve actions. **No mapping → inbound
actions disabled** for that user (return a "open the web UI" link instead).

## 11. Actions and handlers

### 11.1 `answer_questions`

Expected task state:

- `waiting_answers`

Handler:

- parse answers
- call existing planning answer logic or shared service equivalent
- archive `QUESTIONS.json`
- resume planning/execution
- journal: `Human answered via Mattermost`

Inbound text examples:

```text
answer: Use Postgres.
answer Q1: Use Postgres.
Q1: Use Postgres.
```

If multiple questions are open, prefer button/modal or require `Q1:` prefixes.

### 11.2 `approve_plan`

Expected task state:

- `waiting_plan_approval`
- or `plan_change_requested` after revised artifacts

Handler:

- require explicit button/slash command
- call approval service
- state becomes `plan_approved`
- journal: `Plan approved via Mattermost`

Do not start execution unless command is `approve-and-run`.

### 11.3 `approve_and_run`

Expected task state:

- `waiting_plan_approval`
- `plan_approved`
- `plan_change_requested`

Handler:

- require explicit button/slash command
- call approve + start loop with remembered/default run params
- if run params are missing, ask user to open web UI or include command args

Recommendation (see **§0.6.6**):

- For Mattermost inbound, expose `Approve` and `Open task` only.
- Keep `Approve & run` **web-UI only** unless the board has explicitly saved
  default run params that are safe to reuse. From a chat reply you cannot supply
  agent/evaluator ids and budgets safely.

### 11.4 `request_changes`

Expected task state:

- `waiting_plan_approval`
- `plan_change_requested`
- `waiting_for_human`

Handler:

- text after command becomes feedback
- call request-changes/re-plan path
- journal: `Changes requested via Mattermost`

Example:

```text
request changes: keep this behind a feature flag
```

### 11.5 `ack_complete`

Expected task state:

- `complete`
- optionally `failed` / `cancelled`

Handler:

- call existing ack/close behavior or shared service equivalent
- journal: `Completion acknowledged via Mattermost`

### 11.6 `note`

Expected task state:

- any

Handler:

- append Task Journal note
- optionally create visible task comment if configured

Example:

```text
note: Customer prefers the minimal UI path.
```

## 12. Notification generation

Add a service:

```text
features/board/runtime/communication_gateway/
```

Suggested files:

```text
models live in features/board/models.py
repositories/communication.py
runtime/communication_gateway/events.py
runtime/communication_gateway/service.py
runtime/communication_gateway/providers/base.py
runtime/communication_gateway/providers/mattermost.py
runtime/communication_gateway/inbound.py
runtime/communication_gateway/render.py
```

Public helper:

```python
def notify_task_event(
    *,
    task_id: str,
    event_type: str,
    title: str,
    body: str,
    severity: str = "info",
    journal_entry_id: str | None = None,
    refs: dict | None = None,
    action_kind: str | None = None,
    action_payload: dict | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Best-effort queue/send notification based on board rules."""
```

This function should:

1. Load task and board.
2. Find enabled rules/channels for event.
3. Create `HumanActionRequest` when action is needed.
4. Create delivery rows with dedupe.
5. Send via provider or enqueue for worker.
6. Update delivery status.
7. Append journal entry for sent/failed if useful.

For the first implementation, synchronous best-effort send is acceptable, but a
queue/outbox table should exist so failed sends can be retried.

## 13. Trigger points in current code

> **v1 override (see §0.6.3):** do **not** scatter hooks across the five files
> below. Use a single chokepoint — subscribe to the `loop.status` board-bus
> event (and/or new Task Journal entries) with one dispatcher, plus an explicit
> `notify_task_event(...)` call only where the event lacks needed payload (e.g.
> the open-questions list). Key off `LoopState` (§0.6.2), and remember
> `review_failed` / `budget_hit` / `task_blocked` come from journal/task-graph,
> not a loop state. The list below documents *where the signals originate*, not a
> mandate to edit all five files.

Recommended triggers:

- `router.py`
  - after planning job parks at `waiting_plan_approval`
  - after approval/request changes/answer handlers
  - after manual journal note if rule configured

- `runtime/loop/planning.py`
  - planner questions detected -> `answers_required`
  - planning artifacts drafted -> `plan_approval_required`
  - reviewer fail/needs_human -> `review_failed`

- `runtime/loop/service.py`
  - loop status terminal transitions
  - `complete`, `waiting_for_human`, `plan_change_requested`, `waiting_answers`

- `runtime/loop/task_graph.py`
  - subtask blocked -> `task_blocked`

- `runtime/loop/driver.py`
  - budget/capped -> `budget_hit`
  - complete -> `goal_complete`
  - plan_change -> `plan_change_requested`
  - needs_answers -> `answers_required`

Avoid duplicate sends by using delivery `dedupe_key`.

## 14. External thread model

For future chat, every task can have one or more external threads.

Thread creation options:

1. Notification creates a thread if none exists.
2. User manually links a Mattermost thread to a task.
3. Slash command creates/links:

```text
/agent-team link T-42
/agent-team start T-42
```

Thread behavior:

- Once linked, future notifications for that task can reply into the same thread.
- Replies in that thread can become inbound messages.
- A thread can map to task-level journal/comments, not necessarily to a specific
  agent conversation.
- Future mode can map a thread to a specific conversation if you want chatbot
  behavior.

Recommended v1:

- Create a Mattermost root post per notification event needing action.
- Store root id in delivery and external thread.
- Reuse same task thread for subsequent events if channel config says
  `use_threads=true`.

Recommended v2:

- Add explicit "Start external thread" command/button.
- Show linked external threads in task details.

## 15. Chat mode roadmap

The gateway should support these modes over time.

### 15.1 Notification mode

Outbound only except action buttons.

Use case:

- notify human to open web UI
- complete notifications

### 15.2 Action mode

Outbound + constrained inbound.

Use case:

- answer questions
- approve plan
- request changes
- acknowledge complete

### 15.3 Comment bridge mode

Inbound replies become task comments/journal notes.

Use case:

- humans discuss task in Mattermost
- agents see synced comments in `.agent-team/TASK.md`

Rules:

- only from linked threads
- only from authorized users or configured channel allowlist
- avoid echo loops

### 15.4 Chatbot mode

Inbound mention or slash command creates an agent turn.

Use case:

- human asks task agent a question from Mattermost
- agent replies back to Mattermost thread

This is the riskiest mode and should be opt-in per board/channel/task.

Required pieces:

- external thread ↔ task conversation mapping
- inbound message creates `AgentTeamRun` with trigger `external_chat`
- response streams or final answer posts back to Mattermost
- loop prevention and rate limits
- permissions and audit trail

Do not implement chatbot mode before action mode is stable.

## 16. Provider extensibility

Every provider should implement the same conceptual features, but not every
provider supports every capability.

Capability flags:

```json
{
  "outbound_messages": true,
  "threads": true,
  "interactive_actions": true,
  "slash_commands": true,
  "inbound_replies": true,
  "dm": true,
  "message_update": true
}
```

Provider examples:

- Mattermost: outbound, threads, slash commands, interactive actions, replies,
  DMs.
- Slack: similar, later.
- Email: outbound, inbound replies possible but weaker action mapping.
- Generic webhook: outbound only by default.
- Telegram/Discord: chat and actions possible later.

Rules:

- Notification rules can target providers with `outbound_messages`.
- Human action requests require provider support for actions/replies or a web UI
  fallback link.
- Chatbot mode requires inbound replies and external thread mapping.

## 17. Settings UI

Board settings should include `Communication Channels`.

Screens:

1. Channel list
   - provider
   - display name
   - target
   - enabled
   - test button

2. Add Mattermost channel
   - server URL
   - bot token/credential
   - team/channel id or picker
   - use threads toggle
   - inbound secret/token

3. Notification rules
   - event type
   - channel
   - enabled
   - filters

4. Incoming actions
   - enable replies
   - enable slash commands
   - enable approve buttons
   - allowed users / role mapping

5. Test notification
   - send a test message
   - show delivery status/error

Task cockpit additions:

- show linked external threads
- show last notification delivery status
- show open human action requests
- show external replies in Journal or comments

## 18. Message templates

Render templates in one module, not inline in provider code.

Suggested templates:

### 18.1 Plan approval

```text
{task_key} plan is ready for approval

{task_title}

Reviewer: {review_verdict}
Artifacts: SPEC.md, PLAN.md, TASKS.json

Open: {task_url}
```

Actions:

- `Approve`
- `Request changes`
- `Open task`

### 18.2 Answers required

```text
{task_key} needs your answer

Agent paused instead of guessing.

{question_summary}

Reply with:
answer: <your answer>

Open: {task_url}
```

Actions:

- `Answer in Agent Team`
- `Open task`

### 18.3 Plan change requested

```text
{task_key} needs a plan change

The agent found the approved plan may be wrong or unsafe.

Summary:
{plan_change_summary}

Open: {task_url}
```

Actions:

- `Open task`
- `Request changes`
- `Approve revised plan` only if already re-drafted and safe

### 18.4 Human review required

```text
{task_key} needs human review

Reason:
{reason}

Latest verdict:
{verdict_summary}

Open: {task_url}
```

### 18.5 Complete

```text
{task_key} complete

Goal verified complete.

Evidence:
{evidence_summary}

Open: {task_url}
```

Actions:

- `Acknowledge`
- `Open task`

## 19. Security and reliability

Security:

- Verify Mattermost inbound token/signature.
- Map external user to internal user.
- Enforce board/task role authorization.
- Require exact state match before resolving action.
- Require idempotency on inbound callbacks.
- Store raw inbound payload for audit, but mask secrets.
- Never expose bot token to agents.
- Avoid posting sensitive artifact contents to public channels by default.

Reliability:

- Delivery rows should be idempotent.
- Failed sends should be retryable.
- Provider timeouts should not fail loop execution.
- Notification should be best-effort unless a future board policy marks it
  required.
- Use dedupe keys to avoid spam from repeated `loop.status` events.
- For multi-process deployments, a real queue/worker is preferred over
  process-local send.

Privacy:

- Channel config should warn when sending to public channels.
- Message templates should include summaries and links, not full secrets/logs.
- Human answers from external channels become part of Task Journal and possibly
  SPEC clarifications; make that visible.

## 20. Task Journal integration

Every meaningful gateway event should append or reference Task Journal:

- notification sent
- notification failed
- external reply received
- external reply ignored
- human action accepted
- human action rejected
- plan approved via Mattermost
- question answered via Mattermost
- completion acknowledged via Mattermost

Journal refs:

```json
{
  "delivery_id": "...",
  "action_request_id": "...",
  "inbound_message_id": "...",
  "provider": "mattermost",
  "provider_channel_id": "...",
  "provider_post_id": "..."
}
```

Do not duplicate all inbound text into Journal if it becomes a task comment.
Use concise journal entries with refs.

## 21. Implementation order

Recommended slices. **Steps 1–7 are v1 (outbound-only, §0.5); stop there before
starting inbound.** Note step 1 below is trimmed vs the original — v1 only adds
the two outbound tables.

1. Add models/migration for **channels + deliveries only** (defer rules,
   action requests, inbound messages, external threads, user links — see §0.5).
   Use an `event_allowlist` in channel `config_json` instead of a rules table.
2. Add provider interface and Mattermost provider skeleton (port from the
   existing adapter, §0.6.8; per-board token from DB, §0.6.1).
3. Add settings API for channels (rules deferred).
4. Add test notification endpoint.
5. Add outbound notification service with delivery dedupe, driven by the single
   chokepoint in §0.6.3.
6. Trigger outbound notifications for:
   - `waiting_plan_approval`
   - `waiting_answers`
   - `plan_change_requested`
   - `waiting_for_human`
   - `complete`
7. Add Task Journal entries for sent/failed notifications.

   --- end of v1 ---

8. Add Mattermost inbound webhook endpoint with verification (begins inbound;
   `AgentTeamCommUserLink` from §0.6.5 is a prerequisite).
9. Store inbound messages and map to external thread/action request.
10. Implement safe actions:
    - answer questions
    - acknowledge complete
    - add note
11. Implement explicit actions:
    - approve plan
    - request changes
    - approve and run only with safe remembered run params
12. Add UI for channel settings/rules/deliveries.
13. Add external thread linking.
14. Add comment bridge mode.
15. Add chatbot mode later.

## 22. Tests

### 22.1 Provider tests

- Mattermost message renderer includes task key/title/link.
- Provider send success records message id/thread id.
- Provider failure records error and does not crash workflow.
- Provider inbound verification rejects bad token/signature.
- Provider parse handles slash command, button callback, and thread reply.

### 22.2 Routing tests

- `waiting_plan_approval` creates `plan_approval_required` delivery.
- `waiting_answers` creates `answers_required` action request.
- `plan_change_requested` creates blocking notification.
- `complete` creates completion notification.
- Duplicate event with same dedupe key does not send twice.
- Disabled channel/rule does not send.

### 22.3 Human action tests

- Authorized reply answers questions and resumes correct phase.
- Unauthorized reply is ignored/rejected and journaled.
- Stale action request does not run.
- Wrong task state does not run.
- Ambiguous casual reply becomes note or ignored, not approval.
- Button approve resolves approval once.
- Duplicate callback is idempotent.

### 22.4 External thread tests

- Delivery creates external thread mapping.
- Later notification reuses thread when configured.
- Inbound reply maps to task through root post.
- Reply from unlinked thread is ignored or prompts link command.

### 22.5 UI tests

- Board settings can create Mattermost channel.
- Test notification shows success/failure.
- Rules can be toggled.
- Task cockpit shows linked external thread.
- Open action requests are visible.

## 23. Acceptance criteria

**v1 (outbound-only) is complete when:**

- A board can configure at least one Mattermost channel (bot token stored as a
  plain column, exposed only as `has_token`; see §0.6.1).
- A board can choose which task events notify that channel (via
  `event_allowlist`).
- Human-needed states and completion send Mattermost notifications, driven by a
  single dispatch chokepoint (§0.6.3).
- Deliveries are persisted with provider message ids and errors.
- Duplicate lifecycle events do not spam the channel (`dedupe_key`).
- Task Journal records notification sent/failed outcomes.
- Provider timeouts/failures never fail loop execution (best-effort).
- The provider abstraction can support Slack/email/webhook later without
  rewriting loop/planning code.
- Existing web UI/SSE/Task Journal behavior remains compatible.

**Full feature (inbound + actions) is complete when:**

- A board can configure at least one Mattermost channel.
- A board can choose which task events notify that channel.
- Human-needed states and completion send Mattermost notifications.
- Deliveries are persisted with provider message ids and errors.
- Duplicate lifecycle events do not spam the channel.
- Task Journal records notification and inbound action outcomes.
- Mattermost inbound replies/callbacks can safely answer questions and
  acknowledge completion.
- Approval/run actions require explicit command/button and internal authz.
- The provider abstraction can support Slack/email/webhook later without
  rewriting loop/planning code.
- Existing web UI/SSE/Task Journal behavior remains compatible.

## 24. Future chatbot mode

After notifications/actions are stable, add chatbot mode.

Proposed behavior:

- A Mattermost thread can be linked to a task.
- Human mentions the bot or uses slash command:

```text
/agent-team ask T-42 What is blocking this?
```

- Gateway creates an `AgentTeamRun` with trigger `external_chat`.
- Agent answer is posted back to the Mattermost thread.
- If the run writes journal notes or asks questions, normal mechanisms apply.

Safety:

- opt-in per board/channel
- rate limited
- only board members can trigger runs
- clear bot identity
- no autonomous execution from casual chat unless explicitly requested
- all messages journaled or linked as comments

This mode is powerful, but it should be built after notification/action routing
has reliable auth, mapping, idempotency, and external thread support.
