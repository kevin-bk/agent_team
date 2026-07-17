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
