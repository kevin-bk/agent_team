# Communication gateway

Last updated: 2026-06-29 · [↩ index](../index.md) · Source:
[`../../plans/communication-gateway-plan.md`](../../plans/communication-gateway-plan.md),
`features/comm/`

Provider-agnostic external human communication: **outbound notifications (v1,
shipped)** and **inbound two-way chat actions (v2, foundation shipped)**.

## Why

Humans don't live in the cockpit. When a task needs review, finishes, or asks a
question, they should hear about it where they already are (Mattermost/Slack) —
and, eventually, **act on it from chat** (approve a plan, answer questions) without
opening the web UI.

## Design decisions

- **Provider-agnostic via a registry.** A `ProviderDescriptor`
  (`features/comm/providers/registry.py`) declares each provider's configurable
  fields; the UI renders forms dynamically from it. Adding a provider = add a
  descriptor + an implementation, no UI rewrite. Two providers ship:
  `mattermost.py`, `slack.py`, behind the `CommunicationProvider` protocol in
  `providers/base.py`.
- **Shared-connection, repository-style model** (mirrors board repos): a shared,
  owner-scoped `AgentTeamCommConnection` (the bot account) can back **many**
  boards; each board has **one** `AgentTeamBoardChannel` (its target channel +
  overrides). The connection is the reusable credential; the channel is per-board.
- **Generalised mentions.** A `Mention(user_id, handle)` lets each provider format
  tags its own way (Mattermost `@handle` vs Slack `<@USERID>`), resolved from
  internal users via email and cached in `AgentTeamCommUserLink`.
- **Secrets** are plaintext, write-only; API exposes only `has_token`; logs/journal
  mask them.
- **Dedupe.** `dedupe_key` on a delivery prevents repeated events from spamming.
- **Bot API, not webhooks** — so we get back the post id + thread root id that v2
  inbound needs for reply mapping.

## v1 — outbound notifications (shipped)

Lifecycle events (LoopState transitions etc.) map to notification event types; a
board's channel has an **event allowlist**, a `tag_mode` (mention assignee /
creator / none), and a `use_threads` toggle (group a task's updates into one
thread). Modules: `service.py` (orchestration), `render.py` (message body),
`tagging.py` (mentions), `events.py` (event types), `refs.py` (deep links),
`repositories.py`, `router.py`.

### Endpoints (`/comm/*` and board channel)

- `GET /comm/providers`, `GET /comm/event-types`
- `/comm/connections` CRUD (owner/admin scoped; delete guarded if in use)
- `/boards/{id}/channel` get/put/delete + `POST /boards/{id}/channel/test`
  (send a test notification)
- user-mapping endpoints (auto-match by email + manual override)

The **"Send test"** button lives in `web-ui/.../comm/BoardChannelDialog.tsx`.

### Mattermost gotcha (auth)

The REST client (`providers/mattermost.py`) must send
`X-Requested-With: XMLHttpRequest` and a stripped bearer token, or Mattermost's
CSRF guard rejects bot-token posts with **401**. A **403** on `POST /api/v4/posts`
almost always means the bot isn't a member of the channel, **or** the `channel_id`
belongs to a different server than the connection's `server_url` (channel ids are
per-instance). The provider surfaces Mattermost's own error `message` to make this
actionable. (Reference implementation: `coding/deep-agent`'s
`integrations/mattermost/api.py`.)

## v2 — inbound chat actions (foundation shipped)

Goal: a human replies in the thread and the task advances — approve the plan,
answer questions, acknowledge completion, leave a note — no web UI.

- **`inbound.py`** is the brain: resolve the provider user → internal user
  (**only if `verified`**), authorize, dispatch to `human_actions`, and manage the
  lifecycle of inbound messages + action requests.
- **`human_actions.py`** (`runtime/loop/`) holds the reusable action logic
  (`approve_plan`, `ack_loop`, `answer_questions`) **extracted from the web router**
  so chat and the cockpit share one implementation. `approve_plan` parks at
  `plan_approved` only — it never auto-starts execution (that stays web-only).
- **Models:** `AgentTeamExternalThread` (thread root → task),
  `AgentTeamHumanActionRequest` (the outstanding "human must act" request),
  `AgentTeamInboundMessage` (raw events, stored before interpretation for debug).
- **Authz gate:** `AgentTeamCommUserLink.verified` — only a verified mapping may
  act on a task from chat. Email-auto-matched links are backfilled `verified=TRUE`
  (migration `027`); manual overrides start untrusted.
- **Answer UX:** the human writes a single free-text reply; the backend maps it to
  all currently-unanswered questions plus a general note.

The **transport** (a websocket worker listening for `posted` events, filtering the
bot's own posts via `bot_user_id` from `/users/me`) is **not yet wired** — it's the
next v2 step. See [`../roadmap.md`](../roadmap.md).

## Tests

`tests/test_communication_gateway.py`, `test_communication_router.py`,
`test_communication_inbound.py`.

## Related

- The actions chat triggers → [`autonomous-loop.md`](autonomous-loop.md),
  [`planning-workflow.md`](planning-workflow.md)
