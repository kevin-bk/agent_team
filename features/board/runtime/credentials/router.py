"""Admin REST API for credential accounts (the isolated-runtime identity registry).

Managing credential accounts is **admin-only**. No secret ever crosses this API:
only references (host env-var name / host path) are stored and returned.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from agent_team.features.board.runtime.credentials import repository as repo
from agent_team.features.board.runtime.credentials.registry import (
    PROVIDER_REQUIREMENTS,
)
from agent_team.features.board.runtime.credentials.schemas import (
    CredentialAccountCreate,
    CredentialAccountUpdate,
)
from agent_team.web import API_PREFIX, auth_or_401, not_found
from core.database.base import get_db

router = APIRouter(prefix=API_PREFIX, tags=["agent-team-credentials"])


def _is_admin(user) -> bool:
    role = getattr(user.role, "value", user.role)
    return str(role).lower() in {"admin", "super_admin"}


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": detail})


def _validate_provider_backend(provider: str, backend: str) -> str | None:
    """Return an error message, or None if the provider/backend combo is valid."""
    valid = repo.valid_backends_for(provider)
    if valid is None:
        allowed = ", ".join(sorted(PROVIDER_REQUIREMENTS))
        return f"unknown provider {provider!r} (allowed: {allowed})"
    if backend and backend not in valid:
        return (
            f"backend {backend!r} is not valid for provider {provider!r} "
            f"(allowed: {', '.join(valid)})"
        )
    return None


@router.get("/credential-accounts/providers")
async def list_credential_providers(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    return [p.model_dump() for p in repo.provider_infos()]


@router.get("/credential-accounts")
async def list_credential_accounts(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    return [repo.serialize_account(a) for a in repo.list_accounts(db)]


@router.post("/credential-accounts")
async def create_credential_account(
    payload: CredentialAccountCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    bad = _validate_provider_backend(payload.provider, payload.backend)
    if bad:
        return _bad_request(bad)
    if repo.name_exists(db, payload.name.strip()):
        return _bad_request(f"an account named {payload.name.strip()!r} already exists")
    account = repo.create_account(db, payload)
    return repo.serialize_account(account)


@router.patch("/credential-accounts/{account_id}")
async def update_credential_account(
    account_id: str,
    payload: CredentialAccountUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    account = repo.get_account(db, account_id)
    if account is None:
        return not_found("Credential account not found")
    provider = payload.provider if payload.provider is not None else account.provider
    backend = payload.backend if payload.backend is not None else account.backend
    bad = _validate_provider_backend(provider, backend or "")
    if bad:
        return _bad_request(bad)
    if payload.name is not None and repo.name_exists(
        db, payload.name.strip(), exclude_id=account_id
    ):
        return _bad_request(f"an account named {payload.name.strip()!r} already exists")
    account = repo.update_account(db, account, payload)
    return repo.serialize_account(account)


@router.delete("/credential-accounts/{account_id}")
async def delete_credential_account(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    account = repo.get_account(db, account_id)
    if account is None:
        return not_found("Credential account not found")
    repo.delete_account(db, account)
    return {"ok": True}
