"""Data access + serialization for credential accounts (admin CRUD).

Kept separate from :mod:`service` (which does *runtime* resolution/injection):
this module is pure DB + presentation, no host env reads except the best-effort
``ready`` probe used to flag misconfigured accounts in the UI.
"""

from __future__ import annotations

import json
import os

from sqlalchemy.orm import Session

from agent_team.features.board.runtime.credentials.models import (
    AgentTeamCredentialAccount,
)
from agent_team.features.board.runtime.credentials.registry import (
    PROVIDER_REQUIREMENTS,
    default_backend_for,
)
from agent_team.features.board.runtime.credentials.schemas import (
    CredentialAccountCreate,
    CredentialAccountUpdate,
    CredentialProviderInfo,
)

#: Backends offered per credential kind (first = the sensible default).
_BACKENDS_BY_KIND: dict[str, list[str]] = {
    "header_token": ["env", "vault"],
    "config_dir": ["mount"],
}
#: material_ref keys the UI should collect per kind.
_MATERIAL_KEYS_BY_KIND: dict[str, list[str]] = {
    "header_token": ["secret_env"],
    "config_dir": ["host_path", "pvc_claim"],
}
_PROVIDER_LABELS = {"claude": "Claude Code", "codex": "Codex"}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def provider_infos() -> list[CredentialProviderInfo]:
    """Form metadata: providers, their valid backends, and material_ref keys."""
    out: list[CredentialProviderInfo] = []
    for provider, reqs in PROVIDER_REQUIREMENTS.items():
        kind = reqs[0].kind if reqs else "header_token"
        out.append(
            CredentialProviderInfo(
                provider=provider,
                label=_PROVIDER_LABELS.get(provider, provider),
                backends=valid_backends_for(provider) or ["env"],
                material_keys=_MATERIAL_KEYS_BY_KIND.get(kind, ["secret_env"]),
            )
        )
    return out


def valid_backends_for(provider: str) -> list[str] | None:
    """Backends valid for ``provider`` (first = default), or ``None`` if unknown."""
    reqs = PROVIDER_REQUIREMENTS.get(provider)
    if reqs is None:
        return None
    kind = reqs[0].kind if reqs else "header_token"
    default = default_backend_for(provider)
    backends = list(_BACKENDS_BY_KIND.get(kind, ["env"]))
    if default in backends:
        backends.remove(default)
    backends.insert(0, default)
    return backends


def _material_ready(backend: str, material: dict[str, str]) -> bool:
    """Best-effort probe: does the referenced material resolve on this host?"""
    if backend in ("env", "vault"):
        return bool(os.environ.get(material.get("secret_env", "")))
    if backend == "mount":
        host_path = material.get("host_path")
        if host_path:
            return os.path.isdir(host_path)
        return bool(material.get("pvc_claim"))  # named volume — trust it exists
    return False


def serialize_account(account: AgentTeamCredentialAccount) -> dict:
    material = account.material_ref()
    effective = account.backend or default_backend_for(account.provider)
    return {
        "id": account.id,
        "name": account.name,
        "description": account.description or "",
        "provider": account.provider,
        "backend": account.backend or "",
        "effective_backend": effective,
        "material_ref": material,
        "enabled": bool(account.enabled),
        "weight": int(account.weight),
        "max_concurrency": int(account.max_concurrency),
        "ready": _material_ready(effective, material),
        "created_at": _iso(account.created_at),
        "updated_at": _iso(account.updated_at),
    }


def list_accounts(db: Session) -> list[AgentTeamCredentialAccount]:
    return (
        db.query(AgentTeamCredentialAccount)
        .order_by(AgentTeamCredentialAccount.created_at.desc())
        .all()
    )


def get_account(db: Session, account_id: str) -> AgentTeamCredentialAccount | None:
    return (
        db.query(AgentTeamCredentialAccount)
        .filter(AgentTeamCredentialAccount.id == account_id)
        .first()
    )


def name_exists(db: Session, name: str, *, exclude_id: str | None = None) -> bool:
    q = db.query(AgentTeamCredentialAccount.id).filter(
        AgentTeamCredentialAccount.name == name
    )
    if exclude_id:
        q = q.filter(AgentTeamCredentialAccount.id != exclude_id)
    return q.first() is not None


def create_account(
    db: Session, payload: CredentialAccountCreate
) -> AgentTeamCredentialAccount:
    account = AgentTeamCredentialAccount(
        name=payload.name.strip(),
        description=payload.description.strip(),
        provider=payload.provider.strip(),
        backend=(payload.backend or "").strip(),
        material_ref_json=json.dumps(payload.material_ref or {}),
        enabled=payload.enabled,
        weight=payload.weight,
        max_concurrency=payload.max_concurrency,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def update_account(
    db: Session,
    account: AgentTeamCredentialAccount,
    payload: CredentialAccountUpdate,
) -> AgentTeamCredentialAccount:
    if payload.name is not None:
        account.name = payload.name.strip()
    if payload.description is not None:
        account.description = payload.description.strip()
    if payload.provider is not None:
        account.provider = payload.provider.strip()
    if payload.backend is not None:
        account.backend = payload.backend.strip()
    if payload.material_ref is not None:
        account.material_ref_json = json.dumps(payload.material_ref)
    if payload.enabled is not None:
        account.enabled = payload.enabled
    if payload.weight is not None:
        account.weight = payload.weight
    if payload.max_concurrency is not None:
        account.max_concurrency = payload.max_concurrency
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, account: AgentTeamCredentialAccount) -> None:
    db.delete(account)
    db.commit()
