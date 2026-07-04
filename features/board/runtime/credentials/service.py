"""Host-side credential-account resolution + the runtime injection facade.

``core.database`` is imported lazily so importing this module never pulls the app
DB into a light import closure (e.g. the sidecar image). The single entry point
used by the runtime is :func:`build_injection_for`.
"""

from __future__ import annotations

import logging

from agent_team.features.board.runtime.credentials.injector import build_plan
from agent_team.features.board.runtime.credentials.models import (
    AgentTeamCredentialAccount,
)
from agent_team.features.board.runtime.credentials.spec import InjectionPlan

logger = logging.getLogger(__name__)


def resolve_account(account_ref: str) -> AgentTeamCredentialAccount | None:
    """Load an **enabled** account by id or name; ``None`` if missing or disabled.

    Detached from the session on purpose — callers only read scalar fields +
    ``material_ref()`` (no lazy relationships), so a short-lived session is safe.
    """
    ref = (account_ref or "").strip()
    if not ref:
        return None

    from core.database.base import SessionLocal

    with SessionLocal() as db:
        row = (
            db.query(AgentTeamCredentialAccount)
            .filter(
                (AgentTeamCredentialAccount.id == ref)
                | (AgentTeamCredentialAccount.name == ref)
            )
            .first()
        )
        if row is None or not row.enabled:
            return None
        db.expunge(row)
        return row


def build_injection_for(account_ref: str | None) -> InjectionPlan | None:
    """Resolve ``account_ref`` and build its :class:`InjectionPlan`.

    Returns ``None`` when no account is referenced (feature stays opt-in and the
    runtime behaves exactly as before). Propagates :class:`CredentialError` when
    an account IS referenced but is missing/disabled/misconfigured — failing loud
    beats provisioning an unauthenticated sandbox.
    """
    if not account_ref:
        return None

    account = resolve_account(account_ref)
    if account is None:
        from agent_team.features.board.runtime.credentials.backends.base import (
            CredentialError,
        )

        raise CredentialError(
            f"credential account {account_ref!r} not found or disabled"
        )
    return build_plan(account)
