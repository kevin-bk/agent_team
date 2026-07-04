# OpenSandbox Phase 3 — Generic Credential System

> **Status:** Đợt 1 (env+mount), Đợt 2 (network policy), Đợt 3 (vault) done in
> code + unit tests (fake SDK). **Đợt 4 reworked:** the standalone credential
> registry (ORM `AgentTeamCredentialAccount` + `/api/credential-accounts` UI) was
> **removed** in favour of **reusing the AI Code Factory Environment Pool** — see
> §4. Credential material is now driven by the **board's staffed coding agents**
> (`cli_target_ids`) and their **remote MCP** config (`agent_mcp`). Remaining:
> Đợt 5 (pool load-balancing/failover, inherited from `ai_code`).
> SDK verified: `opensandbox==0.1.13` (PyPI latest; there is **no** 0.2.x SDK)
> exposes all required models — `NetworkPolicy`, `NetworkRule`,
> `CredentialProxyConfig`, `Credential`/`CredentialBinding` and the
> `credential_vault` API; `create()` accepts `network_policy` + `credential_proxy`.
> Live verification against a real server + egress `dns+nft` is pending (operator).
> **Scope:** Inject coding-agent credentials (Claude Code, Codex, and future
> API-key providers) into the isolated OpenSandbox runtime through a single,
> provider-agnostic, backend-pluggable layer.
> **Related:** `opensandbox-isolated-runtime.md` (Phase 1),
> `opensandbox-phase2-acp-sidecar.md` (Phase 2), wiki `isolated-runtime.md`.

---

## 1. Problem

A coding CLI inside a sandbox needs credentials to authenticate, but the base
runtime has **no wired path** to get them there, so a real run would start
`claude` / `codex` with no auth and fail. We want this solved **generically** so
future credentials reuse the same machinery, and so the two coding agents don't
need bespoke code — **and without a second account registry**, since the
**AI Code Factory** already stores every Claude/Codex login folder.

## 2. Key technical constraint

**OpenSandbox Credential Vault only injects HTTP *headers* (bearer / apiKey /
basic / customHeaders) at egress — it cannot fill request *body* params, and it
cannot supply an on-disk login folder.** Both coding agents authenticate through
a **subscription login folder**, so both use the **mount** backend:

| Provider | Subscription auth | Backend |
|---|---|---|
| **Claude Code** | `claude /login` writes `CLAUDE_CONFIG_DIR` (`~/.claude`) | **mount** (writable config dir) |
| **Codex** | `codex login` writes `$CODEX_HOME/auth.json`, refreshed + rewritten in place | **mount** (writable config dir) |

The `header_token` shape (env / vault backends) is **kept in the codebase** for
future raw-API-key providers (e.g. a GitHub token → bearer to `api.github.com`),
but no coding agent uses it by default.

## 3. Security model

- **Secrets never live plaintext in `agent_team`'s DB.** The credential material
  is a **host path** to the login folder, owned by the AI Code Factory pool
  (`AI*Environment.config_dir`). `agent_team` only reads a reference to it.
- **Mount (used for both agents):** the login folder is mounted **writable** (so
  token refresh persists). The agent process *can* read it — "outside workspace"
  does **not** protect it (root + Bash in `bypassPermissions` reads any path).
  The mitigation is **network egress `defaultAction=deny` + allowlist** of the
  provider host(s) only → the agent cannot exfiltrate the secret even if it reads
  it. **Non-negotiable whenever a real secret lives in the sandbox** (enforced by
  `strict_isolation`). Plus: ephemeral per-task sandbox, secure container runtime
  (gVisor/Kata), `env_blocklist` to scrub stray secrets.
- **Vault (for future header tokens):** sandbox gets a *fake* env; the egress
  sidecar swaps in the real header. Real secret never enters the sandbox.

## 4. Architecture

```
features/board/runtime/credentials/
  __init__.py
  spec.py            # dataclasses: CredentialRequirement, ResolvedAccount,
                     #   InjectionPlan (SDK-free, DB-free)
  registry.py        # provider -> [CredentialRequirement]  (claude, codex, ...)
  backends/
    base.py          # CredentialBackend protocol + errors (consumes ResolvedAccount)
    env_backend.py   # header_token -> real token env  (future API keys)
    mount_backend.py # config_dir  -> writable mount + target-dir env  (both agents)
    vault_backend.py # header_token -> fake env + network rules + vault bindings
  injector.py        # ResolvedAccount + provider descriptor -> InjectionPlan
  ai_code_source.py  # resolve a provider's ResolvedAccount from the AI Code pool
  service.py         # build_injection_for_board(board_id) -> merged InjectionPlan
```

- **`spec.py` is SDK-free and DB-free** (only imports `VolumeMount`) so it never
  leaks heavy deps into the sidecar image import closure.
- **`ResolvedAccount`** is the ORM-free hand-off between the account *source* and
  the *backends*. Today the only source is `ai_code_source` (config_dir →
  `material={"host_path": ...}`), but any source can produce one.
- **No `agent_team` credential ORM / migration / REST / admin UI.** Removed;
  credentials are configured in **AI Code Factory** (accounts/pool) + the board's
  existing **Agents** settings (which agents + their MCP).
- **Both workers (`sandboxed_cli` Strategy A, ACP sidecar Strategy B) pass
  `board_id`** to `prepare_task_sandbox`, where injection happens.

### Board-driven resolution (`service.build_injection_for_board`)

1. Load the board; read `cli_target_ids()` → map each `cli:<engine>` alias to a
   provider (`claude`, `codex`) via `acp.engines.engine_for_alias`.
2. For each provider, `ai_code_source.resolve_account_for_provider` picks an
   **enabled** `AI*Environment` with a non-empty `config_dir` (ordered by
   `weight` desc) → a `ResolvedAccount` (mount). Missing pool account ⇒ that
   provider is skipped (falls back to the image's ambient login).
3. `injector.build_plan` runs the mount backend per provider; the resulting
   `InjectionPlan`s are **merged** into one — so a multi-agent board mounts
   Claude *and* Codex into the **same** task sandbox.
4. Remote (**http/sse**) MCP hosts from `agent_mcp()` are added to the egress
   allow-list so board-configured MCP servers keep working under a network
   policy. stdio (`command`) MCP servers are skipped (they run in-sandbox).

### Descriptor — the only provider-specific bit

```python
PROVIDER_REQUIREMENTS = {
  "claude": [CredentialRequirement(
      name="claude-config", kind="config_dir", hosts=["api.anthropic.com"],
      target_dir_env="CLAUDE_CONFIG_DIR", mount_path="/root/.claude",
      static_env={"IS_SANDBOX": "1"})],
  "codex": [CredentialRequirement(
      name="codex-home", kind="config_dir",
      hosts=["chatgpt.com", "api.openai.com", "auth.openai.com"],
      target_dir_env="CODEX_HOME", mount_path="/root/.codex")],
}
DEFAULT_BACKEND_BY_PROVIDER = {"claude": "mount", "codex": "mount"}
```

Adding a future GitHub token = one `header_token` entry (host `api.github.com`,
bearer) + a source that yields its `ResolvedAccount` → runs through the **vault**
backend for free, no coding-agent code touched.

## 5. Integration seams (grounded in current code)

- `sandbox/service.py :: prepare_task_sandbox(board_id=...)`: **lazily** import
  `build_injection_for_board`, build the merged `InjectionPlan`, merge `plan.env`
  into `extra_env` + `plan.mounts` into `extra_mounts`, resolve the network
  policy (base ⊕ plan rules ⊕ MCP hosts), and (fresh open only) `write_vault(...)`.
  Lazy import keeps `core.database` + `ai_code` out of the sidecar image closure.
- `workers/sidecar_acp.py` + `workers/sandboxed_cli.py`: pass `board_id=ctx.board_id`.
- `sandbox/opensandbox.py`: pass `network_policy` + `credential_proxy`; add
  `write_vault(...)`; `_import_sdk` provides `NetworkPolicy`, `NetworkRule`,
  `CredentialProxyConfig`, `Credential`, `CredentialBinding`.

## 6. SDK & infra

> **Two independent version lines** — don't conflate them:
> | Component | Where | Latest | Vault needs |
> |---|---|---|---|
> | Python **SDK** `opensandbox` | app / `requirements.txt` | **0.1.13** (no 0.2.x) | 0.1.13 ✓ |
> | **server** image `opensandbox/server` | `docker-compose` | **v0.2.1** | **>= v0.2.0** |
> | **egress** image `opensandbox/egress` | `config.toml` | v1.1.3 | >= v1.1.1 + `dns+nft` |
> | **execd** image `opensandbox/execd` | `config.toml` | v1.0.20 | — |

- `src/plugins/sandbox_integration/requirements.txt`: `opensandbox 0.1.13`
  (PyPI latest; **no 0.2.x SDK exists** — the earlier "0.2.1" was the *server
  image* line). All vault/network models present (verified via `_import_sdk`).
- `docker-compose.opensandbox.yml`: server image `v0.2.1` (required for
  `credentialProxy`).
- `infra/runtime/opensandbox/config.toml`: `egress.image = v1.1.3` (>= v1.1.1),
  `execd_image = v1.0.20`, `[egress].mode = "dns+nft"` (required by Vault).
- Operator setup: register the Claude/Codex accounts in **AI Code Factory**
  (each with a `config_dir` login folder), then staff the board with the matching
  `cli:` agents. `mount`-mode config dirs must be under the server's
  `allowed_host_paths`.

## 7. Phasing

| Đợt | Content | Runnable outcome |
|---|---|---|
| **1. Core** | package + spec/registry/injector + `env` & `mount` backends + wire `config`/`prepare_task_sandbox` | Codex + Claude work via mounted login folder. No SDK/egress bump needed. |
| **2. Network policy** | wire `network_policy` into `opensandbox.open()`, deny-by-default + allowlist merged from plan | egress allowlist on every credentialed sandbox |
| **3. Vault** | `vault_backend`, `write_vault()`, `credential_proxy`, `_import_sdk` models | future header-token providers → zero-secret |
| **4. Reuse AI Code pool** | `ResolvedAccount` + `ai_code_source` + `build_injection_for_board` (multi-provider merge + MCP http allowlist); **remove** the standalone registry/UI | credentials driven by AI Code Factory + board Agents settings |
| **5. (later)** | pool load-balancing/failover (inherited from `ai_code` `weight`/`max_concurrency`) | scale multiple accounts |

### Đợt 4 — as built (reuse, not registry)

- **Source:** `ai_code_source.resolve_account_for_provider(provider)` reads the
  AI Code Factory environment models (`AIClaudeCodeEnvironment`,
  `AICodexEnvironment`, `AICursorEnvironment`), picks an enabled one with a
  `config_dir`, and returns a mount `ResolvedAccount`. Best-effort + lazy: no
  pool ⇒ `None`, never raises.
- **Board wiring:** `build_injection_for_board(board_id)` merges one plan per
  staffed provider + remote-MCP allow rules. Called from `prepare_task_sandbox`.
- **Removed:** ORM `AgentTeamCredentialAccount`, migration `030_credential_account`
  (replaced by `030_drop_credential_account`), `router.py`/`schemas.py`/
  `repository.py`, `plugin.py` model+router registration, the `/credentials`
  admin page + dialog + nav + API client/hooks/types, and the board settings
  **Credential account** dropdown + `RuntimeProfile.credential_account`.

## 8. Risks & mitigations

- **No pool account for a staffed agent** → that provider is skipped and the run
  uses the image's ambient login; under `strict_isolation` an unauthenticated run
  is surfaced by the agent, not silently masked.
- **Egress dns+nft not ready on the server** → mount mode still works; egress
  policy degrades to allow-all unless `strict_isolation` forces deny.
- **Codex `auth.json` write race** across sandboxes sharing an account →
  per-account `max_concurrency` in the AI Code pool, or copy-per-task.
- **Secret leakage** → only host-path references stored; tests assert secrets
  never appear in plan env values; `env_blocklist` scrubs strays.
