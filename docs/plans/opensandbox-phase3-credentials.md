# OpenSandbox Phase 3 — Generic Credential System

> **Status:** Đợt 1 (env+mount), Đợt 2 (network policy), Đợt 3 (vault), and
> Đợt 4 (UI + docs) done in code + unit tests (fake SDK). Remaining: Đợt 5
> (multi-account pool). SDK verified: `opensandbox==0.1.13` (PyPI latest; there
> is **no** 0.2.x SDK) exposes all required models — `NetworkPolicy`,
> `NetworkRule`, `CredentialProxyConfig`, `Credential`/`CredentialBinding` and
> the `credential_vault` API; `create()` accepts `network_policy` +
> `credential_proxy`. Live verification against a real server + egress `dns+nft`
> is pending (operator, on-server).
> **Scope:** Inject coding-agent credentials (Claude Code, Codex, and future
> GitHub/GitLab/API secrets) into the isolated OpenSandbox runtime through a
> single, provider-agnostic, backend-pluggable layer.
> **Related:** `opensandbox-isolated-runtime.md` (Phase 1),
> `opensandbox-phase2-acp-sidecar.md` (Phase 2), wiki `isolated-runtime.md`.

---

## 1. Problem

A coding CLI inside a sandbox needs credentials to authenticate, but the current
runtime has **no wired path** to get them there:

- `RuntimeProfile.env` is never populated with model creds.
- `env` is excluded from the board overlay (`validate_overlay`).
- `prepare_task_sandbox` passes no `extra_env`.

So a real run would start `claude` / `codex` with no auth and fail. We also want
this solved **generically** so future credentials (GitHub tokens, private APIs)
reuse the same machinery, and so the two coding agents don't need bespoke code.

## 2. Key technical constraint

**OpenSandbox Credential Vault only injects HTTP *headers* (bearer / apiKey /
basic / customHeaders) at egress — it cannot fill request *body* params.** This
splits providers:

| Provider | Subscription auth | Vault-friendly? |
|---|---|---|
| **Claude Code** | `CLAUDE_CODE_OAUTH_TOKEN` — static ~1yr token, sent as `Authorization: Bearer`, **no refresh** | ✅ Yes — real token never enters sandbox |
| **Codex** | `$CODEX_HOME/auth.json` — short-lived access token **refreshed via `POST /token` (refresh_token in body)**, file rewritten on refresh | ❌ No — needs the real writable `auth.json` inside the sandbox |

Therefore there is no single mechanism for both. We build **one generic layer**
with **pluggable backends**, and each provider selects the right backend.

## 3. Security model

- **Secrets never live plaintext in the DB.** `CredentialAccount.material_ref`
  stores only a *reference*: a host env-var name (token) or a host path (dir).
  The real secret is read from the host at inject time.
- **Vault (best):** sandbox gets a *fake* env; egress sidecar swaps in the real
  header. Real secret never enters the sandbox. Used for Claude + any API key.
- **Mount (forced for Codex):** the real `auth.json` is mounted (writable, so
  refresh persists). The agent process *can* read it — "outside workspace" does
  **not** protect it (root + Bash in `bypassPermissions` reads any path). The
  mitigations are:
  - **Network egress `defaultAction=deny` + allowlist** the provider host(s)
    only → the agent cannot exfiltrate the secret even if it reads it. **This is
    non-negotiable whenever a real secret lives in the sandbox.**
  - Ephemeral per-task sandbox, secure container runtime (gVisor/Kata) for
    host isolation, `env_blocklist` to scrub stray secrets.

## 4. Architecture

```
features/board/runtime/credentials/
  __init__.py
  models.py          # ORM: AgentTeamCredentialAccount (host-side registry)
  spec.py            # dataclasses: CredentialRequirement, InjectionPlan (SDK-free)
  registry.py        # provider -> [CredentialRequirement]  (claude, codex, ...)
  backends/
    base.py          # CredentialBackend protocol + errors
    env_backend.py   # header_token -> real token env (Đợt 1)
    mount_backend.py # config_dir  -> writable mount + target-dir env (Đợt 1)
    vault_backend.py # header_token -> fake env + network rules + vault bindings (Đợt 3)
  injector.py        # provider + backend + account -> InjectionPlan
  service.py         # resolve_account / CRUD helpers (lazy core.database)
```

- **`spec.py` is SDK-free and DB-free** (only imports `VolumeMount` from
  `sandbox/config.py`) so it never leaks heavy deps into the sidecar image import
  closure.
- **Both workers (`sandboxed_cli` Strategy A, ACP sidecar Strategy B) are
  untouched** — they call `prepare_task_sandbox`, where injection happens.

### Data model — `AgentTeamCredentialAccount` (`plugin_agent_team_credential_account`)

| field | meaning |
|---|---|
| `id` (uuid hex) / `name` (unique) / `description` | identity |
| `provider` | `claude` \| `codex` \| … → maps to `registry.PROVIDER_REQUIREMENTS` |
| `backend` | `env` \| `mount` \| `vault` \| `""` (=" provider default") |
| `material_ref_json` | reference only: `{"secret_env": "HOST_ENV_NAME"}` or `{"host_path": "/…"}` / `{"pvc_claim": "…"}` |
| `enabled` / `weight` / `max_concurrency` | flags + hooks for the future pool/failover |
| `created_at` / `updated_at` | tz-aware |

### Descriptor — the only provider-specific bit

```python
PROVIDER_REQUIREMENTS = {
  "claude": [CredentialRequirement(
      name="anthropic-oauth", kind="header_token",
      hosts=["api.anthropic.com"], paths=["/v1/*"],
      secret_sandbox_env="CLAUDE_CODE_OAUTH_TOKEN", auth_type="bearer",
      static_env={"IS_SANDBOX": "1"})],
  "codex": [CredentialRequirement(
      name="codex-home", kind="config_dir",
      hosts=["chatgpt.com", "api.openai.com", "auth.openai.com"],
      target_dir_env="CODEX_HOME", mount_path="/root/.codex")],
}
```

Adding GitHub later = one `header_token` entry (host `api.github.com`, bearer) →
runs through the **vault** backend for free, no coding-agent code touched.

## 5. Integration seams (grounded in current code)

- `sandbox/config.py`: `RuntimeProfile` gains `credential_account: str | None`;
  add it to `OVERLAY_FIELDS` (string, validated by the existing `else` branch).
- `sandbox/service.py :: prepare_task_sandbox`: **lazily** import the injector,
  resolve the account from `profile.credential_account`, build the
  `InjectionPlan`, and merge `plan.env` into `extra_env` + `plan.mounts` into
  `extra_mounts` before `manager.open_for_task(...)`. (Lazy import keeps
  `core.database` out of the sidecar image closure.)
- `sandbox/factory.py` + `sandbox/opensandbox.py`: **Đợt 2/3** — pass
  `network_policy` + `credential_proxy`; add `write_vault(...)`; extend
  `_import_sdk` with `NetworkPolicy`, `NetworkRule`, `CredentialProxyConfig`,
  `Credential`, `CredentialBinding`.

## 6. SDK & infra

> **Two independent version lines** — don't conflate them:
> | Component | Where | Latest | Vault needs |
> |---|---|---|---|
> | Python **SDK** `opensandbox` | app / `requirements.txt` | **0.1.13** (no 0.2.x) | 0.1.13 ✓ |
> | **server** image `opensandbox/server` | `docker-compose` | **v0.2.1** | **>= v0.2.0** |
> | **egress** image `opensandbox/egress` | `config.toml` | v1.1.3 | >= v1.1.1 + `dns+nft` |
> | **execd** image `opensandbox/execd` | `config.toml` | v1.0.20 | — |

- `src/plugins/sandbox_integration/requirements.txt`:
  `opensandbox 0.1.9 → 0.1.13` (PyPI latest; **no 0.2.x SDK exists** — the
  earlier "0.2.1" was the *server image* line). All vault/network models are
  present in SDK 0.1.13 (verified via `_import_sdk`).
- `docker-compose.opensandbox.yml`: server image `v0.1.13 → v0.2.1`
  (v0.2.x is required for `credentialProxy`).
- `infra/runtime/opensandbox/config.toml`: `egress.image = v1.1.3` (>= v1.1.1),
  `execd_image = v1.0.20`, `[egress].mode = "dns+nft"` (required by Vault).
- Docs: how to get `claude setup-token` (Claude) and `codex login` /
  `codex login --device-auth` (Codex), and how to register accounts.

## 7. Phasing

| Đợt | Content | Runnable outcome |
|---|---|---|
| **1. Core** | package + model + spec/registry/injector + `env` & `mount` backends + migration + wire `config`/`prepare_task_sandbox` (no vault) | Codex works (mount), Claude works (env token). No SDK/egress bump needed. |
| **2. Network policy** | wire `network_policy` into `opensandbox.open()`, deny-by-default + allowlist merged from plan | egress allowlist on every credentialed sandbox |
| **3. Vault** | bump SDK, `vault_backend`, `write_vault()`, `credential_proxy`, `_import_sdk` models | Claude → zero-secret; GitHub/GitLab reuse instantly |
| **4. UI + docs** | Credential Accounts admin page (references only, no secrets) + board dropdown + wiki | operable via UI |
| **5. (later)** | multi-account pool + failover (uses `weight`/`max_concurrency`) | scale multiple accounts |

### Đợt 4 — as built

- **REST (admin-only):** `features/board/runtime/credentials/router.py` →
  `GET/POST/PATCH/DELETE /api/credential-accounts` + `GET .../providers`.
  `schemas.py` (Pydantic), `repository.py` (CRUD + `provider_infos()` +
  best-effort `ready` probe). Registered in `plugin.routers()`.
- **No secret crosses the API** — only references (`secret_env` name, `host_path`,
  `pvc_claim`). `ready` flags whether the referenced material resolves on the host.
- **Web UI:** `features/credentials/CredentialAccountsPage.tsx` +
  `CredentialAccountDialog.tsx`, nav entry `Credentials` (admin), route
  `/credentials`. Form is provider-driven (provider → valid backends +
  reference fields). API client/hooks/types wired.
- **Board dropdown:** `BoardSettingsDialog` isolated-runtime section gains a
  **Credential account** select (Default + registered accounts; keeps an
  unknown current value for non-admin owners). Persists to
  `runtime_profile.credential_account`.

## 8. Risks & mitigations

- **SDK bump breaks the volume workaround** → keep `_import_sdk` resilient; test
  the volume path after bump; Đợt 1 only uses env+mount which don't change.
- **Egress dns+nft not ready on the server** → Đợt 1–2 don't depend on it;
  Claude runs via `env` backend meanwhile; flipping to `vault` is only a
  `backend` field change (no rewrite).
- **Codex `auth.json` write race** across sandboxes sharing an account →
  per-account `max_concurrency` (already in the model) or copy-per-task.
- **Secret leakage** → DB stores references only; tests assert secrets never
  appear in logs/responses; `env_blocklist` scrubs strays.
