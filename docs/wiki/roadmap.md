# Roadmap

Last updated: 2026-06-29 · [↩ index](index.md)

What's shipped, what's next, and the phasing per subsystem. Keep this current —
it's the first place an agent should look to avoid re-proposing finished work.

## Status at a glance

| Subsystem | Status |
|---|---|
| Boards / tasks / workspaces / conversations | ✅ shipped |
| Run runtime + `AgentWorker` (LLM + ACP CLI) | ✅ shipped |
| Autonomous loop (controller + evaluator + budgets + state machine) | ✅ shipped (some cockpit polish ongoing) |
| Task-graph execution (`TASKS.json` scheduled in dependency order, opt-in) | ✅ shipped |
| Strict planning (contract + approval gate + UI + question/plan-change workflow) | ✅ shipped |
| Task journal (slices 1–3 + read-back) | ✅ shipped |
| Board repositories (clone/pull/per-task copy/push gate) | ✅ shipped |
| Board wiki (`is_wiki` repo + skill pack) | ✅ phase 1 shipped |
| Jira import + sync | ✅ shipped |
| Communication gateway — **v1 outbound** (Mattermost + Slack) | ✅ shipped |
| Communication gateway — **v2 inbound** | 🚧 foundation shipped; transport pending |
| Autopilot scheduler | ✅ shipped |

## Next up

### Communication gateway v2 — inbound transport
The models, executor (`comm/inbound.py`), reusable `human_actions`, and the
`verified` authz gate are in place. **Pending:** a websocket worker that listens to
the provider (Mattermost `wss://…/api/v4/websocket?token=…`, `posted` events,
filtering the bot's own posts via `bot_user_id`) and feeds inbound messages into
the executor. Reference: `coding/deep-agent` `integrations/mattermost`. See
[`pages/communication-gateway.md`](pages/communication-gateway.md).

### Planning workflow v2 — mostly shipped, remaining polish
The big v2 pieces already landed: executable `TASKS.json` (dependency-ordered
scheduler, generator works one task at a time, backend marks complete after the
evaluator), `validate_tasks` (unique ids/known deps/acyclic/known statuses),
structured questions (`QUESTIONS.json` + answer endpoint), and the plan-change
pause. Remaining: a dedicated `resolve-change-request` endpoint/archive flow
(today re-approval clears it) and richer per-task progress reporting. See
[`../plans/planning-workflow-implementation-decisions.md`](../plans/planning-workflow-implementation-decisions.md)
§9.3.

### Planning workflow v3 — production orchestration
Durable loop jobs (lease/heartbeat/restart recovery), artifact version history +
audit trail, multi-agent role orchestration, richer evidence, PR automation,
metrics, retrospectives. §9.4 of the same doc.

### Board wiki — next phases
In-app "Wiki Review/Merge" panel (merge a task's wiki branch from the cockpit), a
`wiki_search` tool + `scripts/wiki.py` engine when `index.md` + grep stops
scaling, and later autopilot ingest-on-done + scheduled lint. See
[`../plans/board-wiki.md`](../plans/board-wiki.md) "Phasing".

### Loop engineering — remaining integration
Cockpit progress chip from the `loop.status` bus event, the `WaitingForHuman`
review panel, and optionally auto-moving the task's board column on terminal
states. Confirm-risky CLI permission mode becomes feasible with the loop layer
present. See [`../plans/loop-engineering.md`](../plans/loop-engineering.md) §6.

### Task journal — later
Optional `journal_note` LangChain tool for LLM agents, an MCP tool for CLI agents,
and a summarizer/retrospective agent that writes `JOURNAL_SUMMARY.md`.

## Principle for all phases

> Design the contracts for v3. Implement the smallest useful slice in v1. Turn on
> deeper behaviour in v2. Add production durability in v3.

Phases are **delivery slices, not throwaway builds** — never ship a v1 that v2 must
delete (see [`decisions.md`](decisions.md) D8).
