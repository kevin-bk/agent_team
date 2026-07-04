"""``vault`` backend — inject the secret at egress via OpenSandbox Credential Vault.

For ``header_token`` credentials whose auth is header-based and refresh-free
(Claude's OAuth token, plain API keys). The real secret **never enters the
sandbox**: the sandbox only gets a placeholder env; a MITM egress proxy swaps in
the real ``Authorization`` (or apiKey) header on requests to the provider host.

Produces:
* ``env``               — ``static_env`` + a non-secret placeholder token so the
  CLI still emits a header for the proxy to replace.
* ``vault_credentials`` — the real secret (read from the host env), stored at the
  proxy only.
* ``vault_bindings``    — host/path match → which credential + auth type.
* ``network_rules``     — allow the provider host(s).
* ``needs_credential_proxy = True`` — the sandbox must be created with the proxy.

Requires opensandbox SDK with Credential Vault (>= 0.1.13, verified) and a server
+ egress in ``dns+nft`` mode configured on the host.
"""

from __future__ import annotations

import os

from agent_team.features.board.runtime.credentials.backends.base import (
    CredentialError,
)
from agent_team.features.board.runtime.credentials.models import (
    AgentTeamCredentialAccount,
)
from agent_team.features.board.runtime.credentials.spec import (
    CredentialRequirement,
    InjectionPlan,
)


class VaultBackend:
    name = "vault"

    def plan(
        self,
        account: AgentTeamCredentialAccount,
        req: CredentialRequirement,
    ) -> InjectionPlan:
        if req.kind != "header_token":
            raise CredentialError(
                f"vault backend only supports header_token credentials, "
                f"got {req.kind!r} for {req.name!r}"
            )
        if not req.hosts:
            raise CredentialError(
                f"credential {req.name!r} has no hosts to bind the vault to"
            )

        material = account.material_ref()
        host_env = material.get("secret_env")
        if not host_env:
            raise CredentialError(
                f"account {account.name!r} needs material_ref.secret_env "
                f"(host env var holding the real {req.name} secret)"
            )
        secret = os.environ.get(host_env)
        if not secret:
            raise CredentialError(
                f"host env {host_env!r} (for account {account.name!r}) is unset "
                f"or empty"
            )

        plan = InjectionPlan(env=dict(req.static_env), needs_credential_proxy=True)
        # Placeholder so the CLI still sends an auth header; the proxy overwrites
        # it with the real secret. The real value never lands in the sandbox.
        if req.secret_sandbox_env:
            plan.env[req.secret_sandbox_env] = f"vault-managed-{req.name}"

        plan.vault_credentials.append(
            {"name": req.name, "source": {"value": secret, "type": "inline"}}
        )

        auth: dict[str, str] = {
            "type": req.auth_type or "bearer",
            "credential": req.name,
        }
        if (req.auth_type or "bearer") == "apiKey":
            if not req.header_name:
                raise CredentialError(
                    f"credential {req.name!r} uses apiKey auth but has no header_name"
                )
            auth["name"] = req.header_name

        plan.vault_bindings.append(
            {
                "name": req.name,
                "match": {
                    "hosts": list(req.hosts),
                    "paths": list(req.paths),
                    "schemes": ["https"],
                },
                "auth": auth,
            }
        )
        plan.network_rules.extend(
            {"action": "allow", "target": h} for h in req.hosts
        )
        return plan
