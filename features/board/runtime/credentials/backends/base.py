"""Credential backend protocol + shared errors.

A backend turns one :class:`CredentialRequirement` (for a given account) into an
:class:`InjectionPlan` fragment. Backends are pure/stateless: they read the
account's ``material_ref`` and the host environment, and never mutate global
state — so they are trivially unit-testable and safe to compose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_team.features.board.runtime.credentials.spec import (
        CredentialRequirement,
        InjectionPlan,
        ResolvedAccount,
    )


class CredentialError(RuntimeError):
    """A credential could not be resolved/injected (misconfig or missing secret).

    Raised loud rather than degrading silently: a sandbox that starts without its
    credential would only fail deeper (auth error mid-run) and waste resources.
    """


class CredentialBackend(Protocol):
    """Produces an :class:`InjectionPlan` fragment for one requirement."""

    #: Stable identifier used by accounts + the injector registry.
    name: str

    def plan(
        self,
        account: ResolvedAccount,
        req: CredentialRequirement,
    ) -> InjectionPlan:
        """Build the injection fragment for ``req`` using ``account``'s material.

        :raises CredentialError: when the requirement is unsupported by this
            backend or the referenced secret/material is missing.
        """
        ...
