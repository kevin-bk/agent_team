# Key decisions (why we built it this way)

Last updated: 2026-06-30 · [↩ index](index.md)

ADR-style log of the cross-cutting decisions that shape the plugin. Each entry is
*decision → why → consequence*. Feature-local decisions live on the feature page;
these are the ones that touch multiple subsystems.

## D1 — Chat stays sacred; everything else is additive

**Decision:** the single-turn chat/mention path must keep working exactly as
today; the loop, planning, journal, etc. only engage when a task opts in.
**Why:** it's the proven, interactive UX; regressing it to add autonomy would be a
net loss. **Consequence:** every new system is gated (`execution_mode`,
`strict_plan`, `is_wiki`, channel enabled) and the same `AgentTeamRunEvent` frame
contract is preserved so the UI never has to fork.

## D2 — One worker contract for LLM and CLI agents

**Decision:** unify `_run_graph` and `_run_direct_cli` behind a single
`AgentWorker.run_turn(ctx, emit, cancel)`. **Why:** two divergent execution paths
meant every capability was implemented twice. **Consequence:** the loop, evaluator,
idle-timeout, and permission modes are written once; new workers just emit frames.
See [`pages/runtime-and-runs.md`](pages/runtime-and-runs.md).

## D3 — The event store is the single source of truth

**Decision:** an append-only, monotonic-`seq` event store backs both replay and
live SSE; workers `emit` rather than return. **Why:** one mechanism for "what
happened" serves history, the live UI, and out-of-band large-output offload.
**Consequence:** the frontend is decoupled from execution internals.

## D4 — `task.loop_state` is the only public lifecycle

**Decision:** do not add a second top-level planning state machine; planning
detail is metadata. **Why:** two state tables would disagree about "where is this
task now?". **Consequence:** the cockpit always reads one field; planning adds
states *to* `LoopState` (`planning`, `waiting_plan_approval`, …). See
[`pages/autonomous-loop.md`](pages/autonomous-loop.md).

## D5 — The evaluator (backend), not the generator, owns completion

**Decision:** the generator may claim `in_progress`/`ready_for_review`; only the
backend marks `complete`, and only after an **independent** evaluator returns
`pass` with evidence. **Why:** a generator grading itself is the classic failure
mode. **Consequence:** `EVIDENCE.json`, evaluator-as-separate-worker, and
"verified completion" semantics.

## D6 — Planning is a contract; planning and execution are separate jobs

**Decision:** strict planning produces durable artifacts that a human approves;
`planning/start` runs and **stops** (no process kept alive for hours), execution is
a separate command. **Why:** human approval can take hours; a wrong plan must not
auto-execute; strict mode must not fail open. **Consequence:** the
`.agent-team/` artifact set, approval that pins checksums, the etag edit guard, and
"work backwards from v3, implement forwards from v1" phasing. See
[`pages/planning-workflow.md`](pages/planning-workflow.md).

## D7 — File-first mechanisms for universal (LLM + CLI) reach

**Decision:** capabilities that must work for direct-CLI agents are **file-based**
(planning artifacts, the journal note inbox `JOURNAL_NOTES.jsonl`, the journal
read-back file `JOURNAL.md`, the board-wiki skill). **Why:** direct-CLI agents
don't get LangChain tools; only files + git are truly universal. **Consequence:**
backend ingests/validates files rather than relying on tool calls; LangChain/MCP
tools are an *optional* convenience layer.

## D8 — Phases are delivery slices, not throwaway prototypes

**Decision:** ship the stable contracts first (artifact paths, API names, modes,
approval metadata, expanded `LoopState`), then extend them. **Why:** "do v1
quickly and rewrite later" wastes the v1 work. **Consequence:** the executable
task-graph scheduler has since been built **on top of** the v1 `TASKS.json`
contract (opt-in, no rewrite); durable leases, artifact version history, and PR
automation remain forward-compatible extensions still to come.

## D9 — N-N, owner-scoped, repository-style data model for shared resources

**Decision:** repos and comm connections are **first-class, owner-scoped entities**
linked to boards via a junction table with per-board overrides. **Why:** consistent
with how the codebase already models repos; avoids duplicating a credential per
board. **Consequence:** `AgentTeamBoardRepo` / `AgentTeamBoardChannel` link tables,
admin-manages-resource + editor-assigns-to-board permission split.

## D10 — Secrets: plaintext, write-only, masked

**Decision:** tokens/keys (Jira, repo auth, bot tokens) are stored plaintext but
the API exposes only a presence boolean (`has_secret`/`has_token`) and logs/journal
mask them; credentials are injected at git/HTTP time, never written into
`.git/config` or the task workspace. **Why:** consistent, pragmatic baseline; keeps
secrets off the agent's disk. **Consequence:** *treat the database as sensitive* —
this is an honest bar-raiser, not a sandbox.

## D11 — Provider-agnostic via a registry

**Decision:** external providers (Mattermost/Slack) are declared by a
`ProviderDescriptor`; the UI renders forms from it and a `Mention` abstraction
hides per-provider tag formats. **Why:** the first cut was hardcoded to Mattermost
and couldn't grow. **Consequence:** adding a provider is descriptor + impl, no UI
rewrite. See [`pages/communication-gateway.md`](pages/communication-gateway.md).

## D12 — Reuse human-action logic across web and chat

**Decision:** approve/ack/answer logic lives in
`runtime/loop/human_actions.py`, called by **both** the web router and the inbound
comm executor. **Why:** loop-resumption logic is intricate; re-implementing it for
chat would drift. **Consequence:** chat and cockpit always behave identically;
`approve_plan` from chat parks at `plan_approved` (never auto-runs).

## D13 — Verification must be evidence-backed and budgeted

**Decision:** the evaluator's own token/cost is added to the loop ledger, and a
`pass` verdict that carries **no verification evidence** (no commands/checks run)
is downgraded to `fail`; the evidence is relayed into the next attempt's prompt.
**Why:** (1) evaluator spend was invisible, so budgets undercounted real cost; (2)
an evaluator can rubber-stamp `pass` without actually running anything — D5's
"verified completion" is only real if evidence is *required*, not optional.
**Consequence:** `verdict.eval_tokens`/`eval_cost_usd` + `ledger.add(...)` in
`driver.py`/`task_graph.py`; `has_verification_evidence` +
`_downgrade_unverified_pass` in `service.py`; `format_evidence_digest` relayed by
`controller.py`. See [`pages/autonomous-loop.md`](pages/autonomous-loop.md) and the
plan [`../plans/loop-quality-and-self-improvement.md`](../plans/loop-quality-and-self-improvement.md).

## D14 — Backend owns parsed contracts; process methodology lives in a skill

**Decision:** artifacts the backend **parses** (`TASKS.json`, `EVIDENCE.json`,
`QUESTIONS.json`, `PLAN_REVIEW.json`, the journal note schema,
`PLAN_CHANGE_REQUEST`) stay hardcoded and versioned in the backend; guidance the
backend only **reads-as-text-and-shows** (the `SPEC.md`/`PLAN.md` section
structure) is deferred to the optional **`project-harness` skill**, with a
built-in fallback in the planner prompt. The deciding question is simply *"does the
backend parse it?"* **Why:** a parsed schema that drifts in an external repo
silently breaks the parser, and the evaluator/reviewer must verify **independently
of any skill** — so contracts stay backend. Prose structure, by contrast, benefits
from no-deploy iteration. **Consequence:** `build_planning_prompt` no longer
hardcodes `_SPEC_STRUCTURE`/`_PLAN_STRUCTURE`; the `project-harness` skill owns risk
lanes + lane-graded depth (created as a sibling plugin, board enablement pending);
backend lane *enforcement* is deferred. See
[`pages/planning-workflow.md`](pages/planning-workflow.md) and the
[roadmap](roadmap.md).
