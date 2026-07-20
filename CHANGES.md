# Agent Team — feature & fix log

One section per commit, newest last. Each section records what was built, the
bugs found (root cause) and how they were fixed, so the git log and this file
stay in lockstep.

---

## 1. Isolated runtime: offline ACP adapters, credential volumes, workspace permissions, dependency seeds

**Features**

- The full runtime image bakes both ACP adapters (`claude-agent-acp` pinned via
  `CLAUDE_AGENT_ACP_VERSION` build-arg, `codex-acp`) and selects them with
  `AI_CODE_*_ACP_COMMAND` + an explicitly empty `AI_CODE_*_ACP_ARGS`. Host runs
  keep the `npx` fallback.
- Optional pre-created credential volumes per provider
  (`AGENT_TEAM_RUNTIME_CLAUDE_CREDENTIAL_VOLUME`,
  `AGENT_TEAM_RUNTIME_CODEX_CREDENTIAL_VOLUME`) take precedence over the AI Code
  pool's host `config_dir`; mount-backend credential PVCs use
  `create_if_not_exists=false` so a typo fails sandbox creation instead of
  producing an empty volume and a misleading auth error.
- `OPEN_SANDBOX_API_KEY_FILE` lets operators keep the OpenSandbox key out of
  `.env` (mode-0600 secret file).
- Project-specific runtime images: `scripts/build-project-runtime-image.sh`
  + `infra/runtime/project-deps.Dockerfile` bake an immutable `node_modules`
  seed under `/opt/agent-team/project-deps/<slug>`; the sandbox service links
  task clones to it per `AGENT_TEAM_RUNTIME_DEPENDENCY_SEEDS` (JSON slug→path,
  unsafe entries ignored). Each sandbox has its own container overlay — no
  shared writable dependency state. Yarn Classic is baked as a real binary
  (Corepack shims cannot create their cache under the strict uid's
  `HOME=/nonexistent`).

**Bugs → root cause → fix**

- *Planner died offline with `Connection closed` / npm `ENOTFOUND`*: runtime
  argument resolution treated an explicitly empty `AI_CODE_*_ACP_ARGS` as
  unset and silently fell back to the host-oriented `npx` package args, which
  need registry DNS/egress. Both ACP resolvers (`acp/engines.py`,
  `direct_acp.py`) now distinguish unset from empty.
- *Strict runs got `EACCES` writing the task workspace*: OpenSandbox drops
  `CAP_DAC_OVERRIDE`, so its uid is classified as "other" against host-owned
  files; POSIX ACLs are rejected (`EINVAL`) on the host filesystem. The
  per-task workspace is now prepared with scoped mode bits before every
  isolated mount (no symlink following), and the turn-release handoff uses
  `chmod -R a+rwX` instead of `chown` (uid 0 without `CAP_CHOWN` cannot chown
  host-owned paths).
- *Resumed runs failed re-`chmod`ing files created by the mapped sandbox uid*:
  permission preparation is now idempotent — it skips paths that already carry
  the required bits.

---

## 2. Loop control flow: fail-closed evaluator, resumable blocked tasks, planner contract prompts

**Features**

- Verification command entries support an optional repo-relative `cwd`
  (`{repo, cwd?, command}`), normalized fail-closed (no absolute paths, no
  `..`); dedup keys, labels and receipt matching are cwd-aware.
- Receipt pass/fail honours a policy-declared expected exit code instead of
  hard-coding 0.
- The planner prompt states the `required_evidence` enum (`commands`,
  `criteria`, `scenarios`, `artifacts`) and routes command/scope details to
  their proper fields, so plans stop failing schema validation with free text.

**Bugs → root cause → fix**

- *Evaluator infrastructure errors burned the whole attempt budget*: the loop
  was fail-open — an evaluator exception (or a `None` verdict from an adapter
  that converts failures instead of raising) triggered another builder attempt
  with no reviewer feedback. Seven redundant builder turns ran against one
  broken evaluator. Both paths now stop immediately at `needs_human` with a
  blocking journal entry; the completed builder turn is counted once.
- *Resume returned HTTP 200 but ran zero attempts*: the task-graph scheduler
  only selects `pending` tasks, while a failed run leaves `blocked` markers;
  dependent tasks then had unmet dependencies and the graph immediately
  returned `needs_human`. Starting a graph (an explicit human Resume) now
  resets both `in_progress` and `blocked` unfinished tasks to `pending`;
  completed tasks stay untouched.

---

## 3. Enforced project policy: immutable bundles, allowlists, gates, provenance

**Features**

- `AgentTeamProjectPolicyBundle`: immutable, backend-owned policy release
  (parsed `project.yaml` / `evidence.yaml` / `paths.yaml` + original file
  SHA-256s + bundle digest). Admin API to upload/list; boards bind one bundle
  (`policy_bundle_id`) — no binding keeps the historical advisory behaviour.
- Fail-closed validation: single `project_key`, `schema_version: 1`, enforced
  `evidence`/`paths`, structured command allowlist (argv + logical cwd +
  timeout, no shell metacharacters), relative-glob path lists (`deny_read`,
  `protected_write`, `allowed_write`, `append_only`, `risk_triggers`).
- Approval snapshot: artifact etags, TASKS contract etag, policy identity,
  graph caps (`planning_max_tasks`, `planning_max_total_attempts`), pinned
  skill-pack digests and the approved source state. Rehash gates re-validate
  before execution, before the trusted runner and before every evaluator.
- Runtime command gate: every planned verification command must match the
  bundle allowlist (argv template + cwd) at approval AND per batch at runtime;
  receipts carry policy id/digest provenance; policy timeout caps exec time.
- Deny-read sanitization: task repo copies drop policy-denied paths
  (tracked ones via `skip-worktree` so the diff stays clean); enforced
  workspaces are re-scanned fail-closed before approval and evaluation.
- Risk lane: changes matching `paths.yaml risk_triggers` (e.g. new DB
  migrations/schema) require explicit human acceptance stamped at approval
  (`accept_risk_lane` on approve / approve-and-run; system quick-lane
  approvals can never grant it). Without it the path gate downgrades the
  verdict and routes to human re-approval.
- Strict aliases can disable the generic `set_task_status` tool; goal
  snapshots report attempts/caps and report missing usage/cost as `unknown`
  instead of a fabricated 0; loop API exposes `attention_reason` so the
  cockpit can distinguish "needs review" from infrastructure stops.

**Bugs → root cause → fix**

- *T-4: evaluator failed three straight attempts with "runner failed to
  produce a receipt"*: the runtime allowlist lookup recomputed a command's cwd
  with `working_directory.removeprefix(f"{repo}/")`; for repo-root commands
  `working_directory` IS the repo slug (no slash), so the prefix strip was a
  no-op and the gate compared `cwd="spf-baseapp"`-style values against the
  policy's `cwd="."` — no match → `PolicyError` → the whole receipt batch
  aborted before executing anything. Extracted `policy_cwd()` mapping the
  repo-root case back to `"."` (+ regression test).
- *Answering execution questions wedged enforced runs at "SPEC.md changed
  after approval"*: the answer flow intentionally appends human-approved
  clarifications to SPEC.md, which the enforced rehash gate read as
  post-approval drift. The answer handler (a journaled human action) now
  re-stamps the SPEC etag after appending clarifications.
- *`approve_plan` returned HTTP 500 for an unavailable skill pack*:
  `skills.pinned_manifest` raises plain `ValueError`, which escaped the
  `PolicyError`-only handler. The gate now catches `ValueError` (PolicyError
  subclasses it) and surfaces an actionable approval error.
- *`serialize_board` could raise `DetachedInstanceError`*: the policy digest
  comes from a lazy relationship, but the serializer is also used with
  detached rows (and its comment claimed no relationship existed). Guarded the
  lazy access; detached rows expose only the binding id.

### Review follow-up (fixed in this branch)

Findings from `review.md` addressed here:

- **[P0] Command allowlist shell-injection bypass** — `command_policy` now
  rejects ALL shell-control characters on the resolved command
  (`_SHELL_META_RESOLVED`: `; & | < > ( ) $ \` newline/CR`), so the validation
  model matches shell execution. `settings;id`, `settings&id`, `settings\nid`,
  `$(id)` and backtick payloads no longer pass the allowlist.
- **[P1] Path/risk gate was defined but never called** — the strict evaluator's
  PASS path now runs `_strict_policy_issues`, which diffs the candidate against
  the approved baseline (`turn_recovery.compare_snapshots`, catching committed
  AND dirty changes), maps to the policy repo, and downgrades a PASS on any
  protected/out-of-scope/append-only/un-accepted-risk-lane change.
- **[P1] `planning_max_total_attempts` was snapshot but not enforced** —
  `run_autonomous_loop` now reads the approved `graph_limits` and passes
  `max_total_attempts` into `run_task_graph`.
- **[P1] `deny_read` did not protect tracked secrets** — the task-copy sanitizer
  now fails closed when a tracked path matches `deny_read` (its blob stays in
  `.git`); untracked denied paths are still removed. The operator must untrack/
  rotate or add an explicit policy exception.
- **[P1] Plan reviewer could taint the approval baseline** — fixed upstream in
  `feat/planning-reviewer-workflow` (reviewer drift now voids the verdict).

---

## 4. Strict evaluator isolation: backend review packet + disposable reviewer workspace

**Features**

- Strict evaluator prompts no longer take any builder narrative. The backend
  builds the review packet itself: approved contract etags, base vs candidate
  heads, source digest, bounded diffs, untracked manifest, trusted receipt
  ids, policy identity and pinned skill digests.
- The evaluator agent runs against a disposable copy of the task workspace
  (`workspace_override_path`, never accepted from the public run API);
  builder journal/narrative files are removed from the copy and per-agent MCP
  config is withheld from reviewer/evaluator roles. Only a parseable verdict
  artifact is promoted back; all other reviewer writes are discarded with the
  copy.

**Bugs → root cause → fix**

- *The disposable workspace was silently a no-op in the exact mode enforced
  boards require (OpenSandbox strict)*: task sandboxes are keyed by `task_id`
  and their mounts are fixed at creation, so the evaluator reused the
  builder's sandbox and saw the original workspace — or worse, a fresh task
  sandbox would permanently mount the disposable directory and then have it
  `rmtree`d from under it. Runs with a workspace override now execute in an
  ephemeral sandbox keyed by `run_id` (`TurnContext.ephemeral_workspace`) that
  is killed — not paused — after the turn, in both the sidecar-ACP and
  sandboxed-CLI workers (+ regression test).
- *Copying the workspace crashed on dependency-seed symlinks*: `node_modules`
  links point into the runtime image and dangle on the host; `copytree` was
  following them. It now copies links as links (`symlinks=True`).
- *The strict uid could not list the review copy*: `mkdtemp` creates the root
  0700 while the sandbox accesses the mount as "other". The root is widened to
  0755 at creation (ordinary workspaces already carry the read bit).

---

## 5. MCP is shared with reviewer/evaluator roles

An earlier iteration blocked all MCP for verification roles, which broke
legitimate checks (deploy-log MCP, read-only DB verification). Decision:
reviewer/evaluator receive the same per-agent MCP config as every other role.
Safe because verdicts are gated by backend trusted receipts (real exit codes;
a reviewer PASS without passing receipts is downgraded) and enforced
allowlists reject shell metacharacters, so log-poisoning (`… || echo done`)
cannot enter the verified command set. Operators adding side-effect MCP
servers to a board should remember the reviewer sees them too.
