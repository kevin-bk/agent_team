"""``env`` backend — forward a real token into the sandbox environment.

For ``header_token`` credentials (raw API keys — e.g. a future GitHub token).
The coding agents authenticate via a login folder (``mount`` backend), so this
backend is not on their default path. The real secret is read from a **host**
env var named by the account's ``material_ref["secret_env"]`` and placed into the
sandbox env under the requirement's ``secret_sandbox_env``.

Trade-off: the real token lives inside the sandbox (readable by the agent). Use
the ``vault`` backend when the auth is header-based and refresh-free to keep the
secret out of the sandbox entirely.
"""

from __future__ import annotations

import os

from agent_team.features.board.runtime.credentials.backends.base import (
    CredentialError,
)
from agent_team.features.board.runtime.credentials.spec import (
    CredentialRequirement,
    InjectionPlan,
    ResolvedAccount,
)


class EnvBackend:
    name = "env"

    def plan(
        self,
        account: ResolvedAccount,
        req: CredentialRequirement,
    ) -> InjectionPlan:
        if req.kind != "header_token":
            raise CredentialError(
                f"env backend only supports header_token credentials, "
                f"got {req.kind!r} for {req.name!r}"
            )
        if not req.secret_sandbox_env:
            raise CredentialError(
                f"credential {req.name!r} has no secret_sandbox_env to set"
            )

        material = account.material_ref()
        host_env = material.get("secret_env")
        if not host_env:
            raise CredentialError(
                f"account {account.name!r} needs material_ref.secret_env "
                f"(host env var holding the {req.name} secret)"
            )
        secret = os.environ.get(host_env)
        if not secret:
            raise CredentialError(
                f"host env {host_env!r} (for account {account.name!r}) is unset "
                f"or empty"
            )

        plan = InjectionPlan(env=dict(req.static_env))
        plan.env[req.secret_sandbox_env] = secret
        # Carry the hosts so Đợt 2 can allow them even in env mode.
        plan.network_rules.extend(
            {"action": "allow", "target": h} for h in req.hosts
        )
        return plan
