# Loop Quality & Self-Improvement — Design Doc

Status: **Proposed.** Foundation fixes (§0) implemented; §1–§3 not started.
Audience: engineers working on `agent_team/features/board/runtime/loop`,
`runtime/autopilot.py`, `runtime/task_journal.py`, and the comm gateway.

This doc proposes the next layer on top of the shipped autonomous loop. It is
organised in three parts, each independently shippable:

- **Part 1 — Loop intelligence.** Stop wasting attempts and make each retry
  sharper. (evidence-in-retry **shipped**; risk lanes via the project-harness;
  plateau detection considered and rejected)
- **Part 2 — Autonomy & operations.** Make unattended (autopilot) work actually
  trustworthy and make escalations fast to triage.
- **Part 3 — Self-improvement.** Turn per-task friction into cross-task learning
  that proposes its own improvement work — the "24/7 gets better over time"
  piece.

Everything here obeys the existing decisions: **additive** (chat untouched, D1),
**one public lifecycle** (`task.loop_state`, D4), **the evaluator/backend owns
completion** (D5), **planning and execution stay separate** (D6), **file-first
for CLI reach** (D7), and **phases are delivery slices, design for v3 / build v1
first** (D8).

---

## 0. Foundation already shipped (context)

Two correctness fixes landed first because they are bugs, not features:

- **0.1 — Evaluator spend now counts against the budget.** `Verdict` carries
  `eval_tokens`/`eval_cost_usd`; `WorkerEvaluator.evaluate` sets them and the
  driver / task-graph fold them into the `LoopLedger`. Before this, a
  `max_cost_usd` cap silently overshot by ~2× because every evaluator turn (a
  full agent run executing tests/build) was uncounted. See
  `runtime/loop/verdict.py`, `driver.py`, `service.py`, `task_graph.py`.
- **0.2 — A `pass` with no verification evidence is rejected.**
  `verdict.has_verification_evidence()` checks for concrete proof (`checks`
  free-text or a non-empty strict `commands` list); `WorkerEvaluator` downgrades
  an evidence-less `pass` to `fail` so the backend never marks a task `complete`
  on an asserted-but-unproven claim. This closes the exact failure mode the
  independent-evaluator design exists to prevent (D5).

The work below builds on these.

---

## Part 1 — Loop intelligence

### 1.1 Plateau / no-progress detection — **rejected (for now)**

> **Decision.** Not pursued: the only forward-progress signal we have is the
> evaluator's `score`, which is **self-reported by the evaluator agent with no
> rubric** (see `evaluator.py::_VERDICT_SHAPE` — the prompt asks for
> `"score": 0.0-1.0` with no guidance on how to assign it; `parse_verdict` only
> float-coerces and clamps it). A stall metric built on a vibe-number is
> unreliable. Revisit only if/when the evaluator is given a real scoring rubric
> (e.g. % of acceptance criteria met, % of commands exiting 0). The attempt +
> budget caps remain the backstop in the meantime. The original analysis is
> kept below for context.

**Problem.** `LoopController.on_attempt_finished` (`runtime/loop/controller.py`)
treats every non-`pass` verdict identically: it continues until `max_attempts`.
The evaluator's `score` is **persisted but never read**, and there is no notion
of "the agent is stuck". A genuinely-blocked task therefore burns the entire
attempt budget (and now, post-§0.1, the entire token/cost budget) repeating the
same failed approach.

> **Example.** A test fails because of a missing system dependency the agent
> can't install. Attempts 1→10 all return `score≈0.4` and `missing: "test_x
> still failing"`. Today the loop dutifully runs 10 generator + 10 evaluator
> turns before `capped`. With plateau detection it escalates after ~3, saving
> ~70% of the spend and getting the human involved while it still matters.

**Design.** Add a small, pure progress signal to the controller (keep it
I/O-free and unit-testable, matching the existing split):

- Track the last *K* verdicts' `score` and a normalised `missing` fingerprint
  (lowercased, whitespace-collapsed, hash).
- Declare a **plateau** when, over `plateau_window` (default 3) consecutive
  attempts, **either** the `missing` fingerprint is unchanged **or** `score` did
  not improve by at least `min_score_delta` (default 0.05).
- Optionally factor in an **empty-diff** signal: if the generator turn changed
  no files (see §1.2 evidence), count it as a non-progress attempt.
- On plateau → `Done(OUTCOME_NEEDS_HUMAN)` early (routes to
  `WAITING_FOR_HUMAN` via `outcome_to_state`, never a silent fail — consistent
  with the existing guardrail philosophy in `status.py`).

**Landing points.**
- `controller.py`: new fields `plateau_window`, `min_score_delta`; a ring buffer
  of `(score, missing_fp)`; the plateau branch inside `on_attempt_finished`.
- `service.py` / API: thread the two knobs through `start_autonomous_loop` and
  the `PlanningRunCreate` body alongside `max_attempts`.
- Journal a distinct entry (`type="state_change"`, `severity="warning"`,
  title `"Loop stopped — no progress detected"`) so it reads differently from a
  plain attempt cap.

**Why score-driven, not just repeat-count.** Score lets us also do the *opposite*
later (v2): a `fail` at `score≥0.9` is "almost there" → allow one extra targeted
attempt even past a tight cap, while `score≤0.2` repeated → escalate fast. Ship
the repeat/score-stall detector in v1; keep the asymmetric policy for v2.

### 1.2 Feed evaluator evidence back into the retry prompt — **shipped**

> **Status.** Implemented. `verdict.format_evidence_digest` renders failed
> commands (failures first, with exit codes), free-text `checks`, and `risks`
> into a capped digest; `LoopController._followup` appends it under "What the
> evaluator observed (address this directly)". Pure functions, unit-tested
> (`test_format_evidence_digest`, `test_loop_controller_relays_evidence_in_followup`).

**Problem.** `LoopController._followup` only injects `verdict.missing` (prose).
But the evaluator usually ran commands and observed concrete failures, captured
in `verdict.evidence` (`checks` / strict `commands[]` with `cmd`/`exit_code`/
`summary`). That signal is discarded; the generator re-derives it from scratch.

> **Example.** The evaluator ran `npm run build` and saw `TS2345 ... at
> src/cart.ts:42`. Today attempt N+1 only hears "build not passing yet"; with
> the evidence relayed it hears the exact error + file:line and fixes it in one
> shot instead of re-investigating.

**Design.** Extend `_followup(verdict)` to render a compact **evidence digest**
when present: the failed commands (cmd + exit code + one-line summary) and any
`risks`, truncated to a budget (e.g. ≤1.5k chars) so the prompt stays small and
cache-friendly (D: continuation is a normal user message, no prompt-prefix
churn). Pure function change — no new I/O.

**Landing points.**
- `controller.py::_followup`: accept the structured evidence (already on
  `Verdict`) and format the digest.
- A tiny formatter (`verdict.py::format_evidence_digest`) so it's unit-tested in
  isolation and reused by §2.3 (escalation summary).

### 1.3 Risk lanes via the **project-harness** (graduated process by risk)

**Problem.** `max_attempts`, evaluator depth, and whether a planner/reviewer runs
are **fixed per task** regardless of blast radius. A copy tweak and a DB
migration get the same machinery — wasteful on trivial work, unsafe on dangerous
work.

> **Example A (waste).** "Change button label 'Sign in' → 'Log in'" goes through
> full strict planning + reviewer + an evaluator that runs the whole test suite
> = 4 agent turns for one line.
>
> **Example B (risk).** "Alter the `payment` table schema" gets the *same* depth
> as the label change — no mandatory decision record, no deeper evidence.

**Solution: a `risk_lane ∈ {quick, normal, risk}` on the task**, chosen by a
deterministic checklist, used to pick an execution **profile** — *not* a new
state machine (`task.loop_state` stays canonical, D4).

#### The two-part split (this is the load-bearing design decision)

The lane system has two kinds of component with opposite requirements, so they
live in two places:

- **(A) Methodology — the `project-harness` (an injected workspace artifact).**
  The risk checklist, the lane definitions, the per-lane "what to do",
  intake/decision instructions, and story/decision templates are **content**
  that should be editable without a code deploy, versioned in git, and readable
  by both LLM and CLI agents (file-first, D7). It lives in a **`project-harness`
  repo** marked on the board and **injected into each task workspace** — exactly
  the existing pattern for the board wiki (`AgentTeamBoardRepo.is_wiki`) and the
  `wiki/skill_pack`. **Improving the methodology = updating that repo; no plugin
  release.** This makes the project-harness the shared home for the later
  context-rules (Part-3-adjacent) and friction rules (Part 3) too.

- **(B) Enforcement — backend code + board config (never the workspace).**
  Computing the lane from the flags, pinning it on the task, mapping lane →
  profile, applying the budget, and the completion gate **must** be backend
  logic. The workspace is **agent-writable**, so if a gate or budget lived in a
  workspace file an agent could weaken its own gate — breaking the
  evaluator-owns-completion invariant (D5). The lane→profile mapping is **board
  config in the DB** (tunable via UI), not a workspace file.

```
┌─ project-harness repo (injected into workspace) ─┐  update repo = update method
│  risk checklist (11 flags) · lane definitions ·  │  (no deploy; LLM+CLI readable)
│  intake guidance · story/decision templates ·    │
│  preview classifier script                       │
└──────────────────┬────────────────────────────────┘
                   │ planner reads → fills flags → writes INTAKE.json
                   ▼
┌─ backend code + board config (DB) ────────────────┐  tamper-proof, UI-tunable
│  compute lane (count + hard-gate) · pin on task   │
│  map lane→profile · apply budget · completion gate │
└─────────────────────────────────────────────────────┘
```

#### Where it runs in the lifecycle

No new agent step (that would add a model turn + latency). Classification folds
into the **planning phase**, enforcement into **execution**:

| Step | Who | Where |
|---|---|---|
| Fill the risk checklist (needs semantic judgement) | **planner** (already reads the workspace + writes SPEC's Risks) | planning phase → writes `.agent-team/INTAKE.json` |
| Compute the lane from the flags (must be deterministic/auditable) | **backend code** | on reading the planner's artifact |
| Approve / override the lane | **human** | at `waiting_plan_approval` (lane shown in the plan) |
| Apply the profile (reviewer?, attempts, evaluator strictness, decision-note requirement) | **backend code** | at `approve-and-run` + during the loop |

Tasks that never enter planning (plain chat, direct autopilot) default to
`normal`.

#### The deterministic classifier (lives in the project-harness, computed in code)

Eleven binary risk flags the planner marks (definitions in the project-harness so
they can evolve without a deploy):

`auth`, `authorization`, `data_model`, `secrets_config`, `audit_security`,
`external_systems`, `public_contracts`, `cross_platform`, `existing_behavior`,
`weak_proof`, `multi_domain`.

Lane rule (computed in backend code over the planner's flags):

```
0–1 flags          → quick  (or normal if code impact is non-trivial)
2–3 flags          → normal
4+ flags           → risk
any HARD GATE flag → risk    (regardless of count)
```

**Hard gates** (one is enough): `auth`, `authorization`, `data_model`
(loss/migration), `secrets_config` (secrets/credentials/env), `audit_security`,
`external_systems`, or any change that **weakens validation**. Hard gates are
re-checked in code from task labels/objective signals — an agent cannot
self-classify dangerous work as `quick` to skip rigor, and a lane may only be
**tightened** by a human, never loosened below §0.2.

#### Lane → profile

| Lane | Planner/Reviewer | `max_attempts` | Evaluator | Completion gate |
|---|---|---|---|---|
| `quick` | skip planner | 1–2 | light (lint/build) | `pass` + evidence (§0.2) — **never skipped** |
| `normal` | planner; reviewer optional | ~5 (today's default) | full tests/build | `pass` + evidence (§0.2) |
| `risk` | planner **and** reviewer required | ~5 | full + must list `commands` with exit codes | `pass` + evidence **+** a durable decision note in the journal |

> Note: even `quick` keeps the evaluator + evidence gate — it only gets *lighter*,
> never bypassed, so the verified-completion invariant (D5) holds for every lane.

#### How the backend drives the agent per lane

All three lanes run the **same loop machinery** (`LoopController` +
`driver.run_loop` / `task_graph`); the lane only changes config knobs, never the
lifecycle (D4). After each attempt the driver sends the continuation **followup**
(a normal user message, D1 — now carrying the §1.2 evidence digest) until the
lane's completion gate is met.

- **`quick`** — backend skips heavy planning: no PLAN/TASKS generation, no plan
  approval gate. It calls `driver.run_loop(objective, max_attempts=1–2)` with a
  *light* evaluator (lint/build). The agent gets the objective prompt → works →
  one evaluator turn → `pass`+evidence completes; otherwise 1–2 retries then
  `capped`/`needs_human`. Evidence gate still applies.
- **`normal`** — today's full flow: `planning.py` produces SPEC → PLAN → TASKS →
  human approve → `task_graph.run_task_graph` with `max_attempts≈5`, full
  tests/build evaluator, optional reviewer; per-task generator+evaluator loop
  plus the final whole-SPEC verify.
- **`risk`** — `normal` plus three tightenings the backend toggles in the
  profile: (1) **reviewer required** — backend will not move to `plan_approved`
  without a reviewer pass; (2) **strict evaluator addendum** — a `pass` whose
  `commands` are empty or have non-zero exit codes is downgraded to `fail` by
  `WorkerEvaluator` (on top of the shipped §0.2 gate); (3)
  **`require_decision_note`** — the completion gate also checks for a durable
  `decision` journal entry; if missing, the driver sends a followup demanding it
  (or routes to `needs_human`) rather than completing.

**Landing points.**
- **project-harness repo role:** a board-repo marker beside `is_wiki` (e.g.
  `AgentTeamBoardRepo.is_harness`) + injection of its files into the task
  workspace, reusing the wiki/`skill_pack` injection path. Seeded with a default
  checklist + lane docs + templates + the preview classifier script.
- **Shared pure classifier:** one function `classify_lane(flags) → lane` used by
  both the project-harness preview script (advisory) and the backend
  (authoritative); the backend never trusts the workspace script's output.
- **Model:** `risk_lane` on `AgentTeamTask` (+ migration), pinned in
  `planning_meta_json`; lane→profile defaults as **board config**
  (`AgentTeamAutopilot`/board settings) so they're UI-tunable.
- **`planning_prompts.py`:** the planner prompt points at the project-harness
  checklist and asks for `.agent-team/INTAKE.json` (flags + reasons) *before*
  PLAN/TASKS; a `risk`-lane evaluator addendum that **rejects** a `pass` whose
  `commands` are empty or have non-zero exit codes (tightening §0.2 for the
  dangerous lane only).
- **`planning.py` / `service.py`:** gate on `INTAKE.json`, compute+pin the lane,
  then map lane → `{run_reviewer, max_attempts, evaluator_strictness,
  require_decision_note}` and route (`quick` → light `run_loop`; `normal`/`risk`
  → full PLAN/TASKS + graph). Replaces hard-coded defaults; does **not** branch
  the lifecycle.
- **UI:** a lane chip on the card + a lane selector / override in the planning
  panel.

---

## Part 2 — Autonomy & operations

### 2.1 Autopilot completion must be *verified*, not just *finished*

**Problem — the biggest gap for 24/7 unattended work.** Plain autopilot runs a
**single turn** with no evaluator. `autopilot.on_run_finished`
(`runtime/autopilot.py`) moves a task to `done_status` purely on `RUN_DONE`:

```478:483:community_plugins/agent_team/features/board/runtime/autopilot.py
        if status == RUN_DONE:
            if in_working:
                task.status = row.done_status
                moved_to = row.done_status
            task.autopilot_attempts = 0
            task.autopilot_resume_after = None
```

`RUN_DONE` means "the agent finished its turn", **not** "the work is correct".
The carefully-built `verified completion` of the loop (D5) is simply not used by
autopilot, so the Done column can fill with unverified work.

> **Example.** Overnight autopilot picks up 12 tasks. Every agent ends its turn
> with "Done!". All 12 cards move to Done. In the morning, 4 of them have failing
> builds. You trusted the board and it lied — because nothing graded the work.

**Design.** Add a per-board (or per-lane, §1.3) **autopilot verification mode**:

- `direct` (today's behaviour) — single turn, `RUN_DONE` → Done. Keep as default
  for boards that want speed over assurance (additive, D1).
- `verified` (opt-in) — autopilot starts an **autonomous loop**
  (`start_autonomous_loop`) instead of a single run, so the evaluator gates
  completion. The board column then follows the loop outcome: `complete` → Done,
  `needs_human`/`capped`/`budget` → a `review` column (configurable), `failed` →
  error column.
- Minimum-viable middle ground if a full loop is too heavy for some boards: keep
  the single generator turn but append **one evaluator turn** before the
  Done-move, reusing `WorkerEvaluator`.

**Landing points.**
- `models.py`: `AgentTeamAutopilot.verification_mode` + an optional
  `review_status` column key (+ migration).
- `autopilot._claim_and_start`: when `verified`, launch the loop instead of a
  plain run (it already has `agent`; needs an `evaluator_alias` — add to
  autopilot config).
- `autopilot.on_run_finished`: for verified runs, branch on the loop outcome /
  `task.loop_state` rather than raw `RUN_DONE`.

### 2.2 Autopilot retries with context (stop blind repeats)

**Problem.** On `RUN_ERROR`, autopilot bumps `autopilot_attempts`, sets a fixed
cooldown, and later **re-runs the identical seed prompt** with no memory of why
it failed:

```484:491:community_plugins/agent_team/features/board/runtime/autopilot.py
        elif status == RUN_ERROR:
            if in_working:
                task.status = row.error_status
                moved_to = row.error_status
            task.autopilot_attempts = (task.autopilot_attempts or 0) + 1
            task.autopilot_resume_after = now + timedelta(
                seconds=max(0, int(row.error_cooldown_seconds))
            )
```

> **Example.** A run errors because a command needs network the sandbox blocks.
> After the cooldown autopilot runs the same prompt → same failure → repeat to
> `max_attempts`. Three identical failures, zero new information.

**Design.**
- On `RUN_ERROR`, capture a short failure reason (last error frame / run error
  field) into the task (or journal) and **prepend it** to the next auto-run's
  seed prompt ("Your previous attempt failed with: …; address this first.").
- Detect **repeated near-identical errors** (a normalised error fingerprint —
  lowercased, whitespace-collapsed hash) and
  **escalate early** to the error/review column instead of exhausting
  `max_attempts` on the same wall.

**Landing points.** `autopilot.py` (`on_run_finished` capture +
`_claim_and_start` prompt assembly); store last-error on the task or a journal
entry (`type="risk"`).

### 2.3 Escalation summary on `needs_human` / `capped` / `budget`

**Problem.** When the loop parks at `WAITING_FOR_HUMAN`, the driver journals a
generic one-liner and `notify_loop_state` fires a bare state ping. The human
must reconstruct what happened from the transcript of N generator + N evaluator
turns.

> **Example.** 02:00 the loop caps. 09:00 you get a Slack ping: "Task ABC-12
> needs human." You open it and read 10 attempts to understand it was blocked on
> a missing test fixture. A one-paragraph summary would have made it a 30-second
> decision.

**Design.** On any terminal escalation, generate a compact **escalation
summary** and (a) write it as a journal entry and (b) include it in the comm
notification payload:

- attempts used / budget consumed (now accurate thanks to §0.1),
- last verdict `score` + `missing`,
- the evidence digest (§1.2 formatter — failed commands),
- a suggested next action ("provide fixture X", "approve scope", "raise budget").

**Landing points.**
- `driver.py::_finish` (and `task_graph._terminal`): build the summary from the
  last `Verdict` + `LoopLedger` and journal it.
- `comm/service.py::notify_loop_state`: accept and forward the summary so
  actionable notifications (v2 inbound) carry context.

---

## Part 3 — Self-improvement (cross-task learning)

### 3.1 The problem: knowledge is trapped per-task

`journal.list_entries` queries **one `task_id`**. Every decision, risk and
friction signal lives in a silo. Nothing rolls up across tasks, so a **systemic**
problem is invisible.

> **Example.** This week three different tasks all end `blocked` for the same
> underlying reason — the repo has no test fixtures and no documented way to run
> tests, so the evaluator can never gather evidence. Each task's journal records
> it locally; no one connects the dots. The *right* response is a single
> improvement task: "add test fixtures + a `how to run tests` doc", which would
> unblock all future work on that board.

This is the missing **process-improvement** loop: each task should not only
produce a *product* delta (its code) but also, when it hits friction, a *process*
delta that makes the next task easier.

### 3.2 Design: friction capture → rollup → proposed backlog tasks

Reuse what already exists (DB journal + autopilot scheduler); **do not** add a
separate datastore or CLI.

**(a) Capture friction as a first-class journal type.** Agents already append to
the `JOURNAL_NOTES.jsonl` inbox (`task_journal.ingest_agent_notes`) and prompts
carry `JOURNAL_DISCIPLINE`. Add `friction` to the allowed note/journal types and
extend the discipline text: "if something was missing, ambiguous, stale, or a
repeated manual step, log a `friction` note naming the concrete pain." The
backend also emits `friction` automatically on `blocked` / `capped` / `budget`
terminal states, carrying the evidence digest (§1.2). *What counts as friction*
and the recurrence threshold are guidance that belongs in the **project-harness**
(§1.3) so they can evolve without a deploy.

**(b) Cross-task rollup query.** Add `journal_repo.list_board_friction(board_id,
since=…)` (the journal table is per-task but joinable to `task → board`). Group
by a normalised fingerprint of the friction title/body (lowercased,
whitespace-collapsed, hashed) to surface **repeated** patterns and their count.

**(c) Deterministic proposal pass (a retrospective tick).** A scheduled job
(piggyback on `autopilot_scheduler`) runs, e.g., daily per board:

1. Pull friction since the last run; cluster by fingerprint.
2. For any cluster seen ≥ `recurrence_threshold` (default 2), draft an
   **improvement task** in the board's backlog/source column: title = the
   pattern, body = the evidence (which tasks, how often, sample friction text),
   labelled `process-improvement`, left **unassigned and unstarted** so a human
   triages it (propose → human review → implement; the system never silently
   changes its own process).
3. Record the proposal in the journal so it isn't re-proposed next run
   (idempotency via a stored "last rollup seq/time" per board).

> **Example, continued.** After the third "no test fixtures" friction note this
> week, the daily rollup auto-creates a card: *"[process-improvement] Repo lacks
> test fixtures + run-tests docs — blocked US-101, US-108, US-114 (3×)"* in
> Backlog. You glance at it, approve, and one task fixes the root cause for every
> future task. The tool got better on its own.

**(d) Health/drift signal (optional, v2).** A cheap board "drift" score from the
same data — e.g. count of open `blocked` tasks, friction-clusters without a
proposal, tasks `complete` without evidence (should be ~0 after §0.2) — shown as
a board KPI. This is the operator's "is my 24/7 agent getting *more* autonomous?"
dashboard: track human-intervention rate and evaluator pass-rate over time.

**Landing points.**
- `models.py`: add `"friction"` to `JOURNAL_TYPES`.
- `repositories/journal.py`: `list_board_friction(...)` + a fingerprint helper.
- `runtime/` new module `retrospective.py`: the rollup + proposal logic (pure-ish,
  testable); a tick entry called from `autopilot_scheduler.py`.
- `planning_prompts.py::JOURNAL_DISCIPLINE`: mention `friction`.
- Driver/task-graph terminal paths: emit `friction` on non-complete outcomes.

---

## Phasing (design v3, build v1 — D8)

**v1 (highest value / lowest risk, mostly contained in the loop layer):**
- ~~§1.1 plateau detection~~ — rejected (score is not a reliable signal).
- §1.2 evidence-in-retry (pure formatter) — **shipped**.
- §2.3 escalation summary (reuses §1.2 + ledger).
- §3.2 (a)+(b): `friction` journal type + cross-task rollup query.

**v2 (touches models/migrations/UI):**
- §1.3 risk lanes + the **project-harness** repo role (`is_harness` injection,
  `risk_lane` model, lane→profile board config, INTAKE.json, UI chip).
- §2.1 verified autopilot (autopilot verification mode + review column).
- §3.2 (c): scheduled proposal pass writing backlog cards (friction rules hosted
  in the project-harness).

**v3 (production polish):**
- §2.2 context-aware autopilot retries + early escalation.
- §3.2 (d) board drift/health KPIs + intervention-rate trend.

Each phase ships a stable contract the next extends — never a v1 that v2 must
delete.

## Non-goals / guardrails

- **No second lifecycle.** All new behaviour lives in metadata/profiles;
  `task.loop_state` stays the single public lifecycle (D4).
- **No separate datastore or CLI.** Self-improvement reuses the journal DB +
  autopilot scheduler, not a SQLite/Rust sidecar.
- **Chat stays sacred (D1).** Every item is opt-in (lane, verification mode,
  retrospective enabled) and leaves the single-turn `@mention` path untouched.
- **Evaluator still owns completion (D5).** Risk lanes can only *tighten* the
  evidence gate, never loosen it below §0.2.
