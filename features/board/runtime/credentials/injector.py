"""Compose a full :class:`InjectionPlan` from an account + provider descriptor.

Dispatch only: pick the backend (account override or provider default), run it
over every requirement of the provider, and fold the fragments together. Adding
a backend = register it here; adding a provider = extend :mod:`registry`.
"""

from __future__ import annotations

from agent_team.features.board.runtime.credentials.backends.base import (
    CredentialBackend,
    CredentialError,
)
from agent_team.features.board.runtime.credentials.backends.env_backend import (
    EnvBackend,
)
from agent_team.features.board.runtime.credentials.backends.mount_backend import (
    MountBackend,
)
from agent_team.features.board.runtime.credentials.backends.vault_backend import (
    VaultBackend,
)
from agent_team.features.board.runtime.credentials.registry import (
    default_backend_for,
    requirements_for,
)
from agent_team.features.board.runtime.credentials.spec import (
    InjectionPlan,
    ResolvedAccount,
)

#: Available injection backends.
_BACKENDS: dict[str, CredentialBackend] = {
    EnvBackend.name: EnvBackend(),
    MountBackend.name: MountBackend(),
    VaultBackend.name: VaultBackend(),
}


def build_plan(account: ResolvedAccount) -> InjectionPlan:
    """Build the injection plan for ``account``.

    :raises CredentialError: unknown provider, unwired backend, or a backend
        failing to resolve its material.
    """
    reqs = requirements_for(account.provider)
    if reqs is None:
        raise CredentialError(
            f"unknown credential provider {account.provider!r} "
            f"(account {account.name!r})"
        )

    backend_name = account.backend or default_backend_for(account.provider)
    backend = _BACKENDS.get(backend_name)
    if backend is None:
        raise CredentialError(
            f"unknown credential backend {backend_name!r} "
            f"(account {account.name!r}); known: {', '.join(sorted(_BACKENDS))}"
        )

    plan = InjectionPlan()
    for req in reqs:
        plan.merge(backend.plan(account, req))
    return plan
