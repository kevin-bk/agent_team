"""REST API for code repositories and board↔repo assignments.

Managing repos (create/edit/credentials/schedule/clone/pull/delete) is
**admin-only** and **owner-scoped**: an admin only sees and edits the repos they
own. Assigning a repo to a board requires board **owner/editor**.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import members as members_repo
from agent_team.features.repos import git_service
from agent_team.features.repos import repositories as repos_repo
from agent_team.features.repos.schedule import is_valid_cron
from agent_team.features.repos.schemas import (
    AssignRepoRequest,
    RepoCreate,
    RepoStatusDTO,
    RepoUpdate,
)
from agent_team.web import API_PREFIX, auth_or_401, not_found
from core.database.base import get_db

router = APIRouter(prefix=API_PREFIX, tags=["agent-team-repos"])

_EDITOR_ROLES = {"owner", "editor"}


def _is_admin(user) -> bool:
    role = getattr(user.role, "value", user.role)
    return str(role).lower() in {"admin", "super_admin"}


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": detail})


def _validate_schedule(mode: str | None, cron: str | None) -> JSONResponse | None:
    if mode == "cron" and not is_valid_cron(cron):
        return _bad_request("A valid cron expression is required for cron mode.")
    return None


# ── repo management (admin, owner-scoped) ───────────────────────────────────


@router.get("/repos")
async def list_repos(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    repos = repos_repo.list_repos(db, owner_id=user.id)
    return [repos_repo.serialize_repo(db, r) for r in repos]


@router.post("/repos")
async def create_repo(
    payload: RepoCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    bad = _validate_schedule(payload.schedule_mode, payload.schedule_cron)
    if bad:
        return bad
    repo = repos_repo.create_repo(db, owner_id=user.id, payload=payload)
    return repos_repo.serialize_repo(db, repo)


def _owned_repo_or_error(db: Session, repo_id: str, user):
    repo = repos_repo.get_repo(db, repo_id)
    if repo is None:
        return None, not_found("Repository not found")
    if repo.owner_id != user.id and not _is_admin(user):
        return None, _forbidden("Not your repository")
    # Owner scope: even admins only manage their own repos here.
    if repo.owner_id != user.id:
        return None, _forbidden("Not your repository")
    return repo, None


@router.patch("/repos/{repo_id}")
async def update_repo(
    repo_id: str, payload: RepoUpdate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    repo, rerr = _owned_repo_or_error(db, repo_id, user)
    if rerr:
        return rerr
    mode = payload.schedule_mode if payload.schedule_mode is not None else repo.schedule_mode
    cron = payload.schedule_cron if payload.schedule_cron is not None else repo.schedule_cron
    bad = _validate_schedule(mode, cron)
    if bad:
        return bad
    repo = repos_repo.update_repo(db, repo, payload)
    return repos_repo.serialize_repo(db, repo)


@router.delete("/repos/{repo_id}")
async def delete_repo(repo_id: str, request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    repo, rerr = _owned_repo_or_error(db, repo_id, user)
    if rerr:
        return rerr
    board_ids = repos_repo.boards_using_repo(db, repo_id)
    if board_ids:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Repository is assigned to boards; unassign it first.",
                "board_ids": board_ids,
            },
        )
    repos_repo.delete_repo(db, repo)
    return {"ok": True}


@router.post("/repos/{repo_id}/clone")
async def clone_repo(repo_id: str, request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    repo, rerr = _owned_repo_or_error(db, repo_id, user)
    if rerr:
        return rerr
    result = await asyncio.to_thread(
        git_service.sync_repo_by_id, repo_id, force_clone=True
    )
    db.expire_all()
    fresh = repos_repo.get_repo(db, repo_id)
    return {
        "ok": result.ok,
        "action": result.action,
        "message": result.message,
        "repo": repos_repo.serialize_repo(db, fresh) if fresh else None,
    }


@router.post("/repos/{repo_id}/pull")
async def pull_repo(repo_id: str, request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    repo, rerr = _owned_repo_or_error(db, repo_id, user)
    if rerr:
        return rerr
    result = await asyncio.to_thread(git_service.sync_repo_by_id, repo_id)
    db.expire_all()
    fresh = repos_repo.get_repo(db, repo_id)
    return {
        "ok": result.ok,
        "action": result.action,
        "message": result.message,
        "repo": repos_repo.serialize_repo(db, fresh) if fresh else None,
    }


@router.get("/repos/{repo_id}/status")
async def repo_status(repo_id: str, request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    repo, rerr = _owned_repo_or_error(db, repo_id, user)
    if rerr:
        return rerr
    info = await asyncio.to_thread(git_service.repo_status, repo)
    return RepoStatusDTO(repo_id=repo_id, **info)


# ── board assignments (board owner/editor) ──────────────────────────────────


def _board_editor_or_error(db: Session, board_id: str, user):
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return None, not_found("Board not found")
    role = members_repo.effective_role(
        db, board, user_id=user.id, is_admin=_is_admin(user)
    )
    if role not in _EDITOR_ROLES:
        return None, _forbidden("Board owner or editor required")
    return board, None


@router.get("/boards/{board_id}/repos")
async def list_board_repos(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board, berr = _board_editor_or_error(db, board_id, user)
    if berr:
        return berr
    assigned = [
        repos_repo.serialize_board_repo(db, repo, branch, allow_push)
        for repo, branch, allow_push in repos_repo.repos_for_board(db, board_id)
    ]
    assigned_ids = {bri.repo.id for bri in assigned}
    available = [
        repos_repo.serialize_repo(db, r)
        for r in repos_repo.list_repos(db, owner_id=user.id)
        if r.id not in assigned_ids
    ]
    return {"assigned": assigned, "available": available}


@router.post("/boards/{board_id}/repos")
async def assign_board_repo(
    board_id: str,
    payload: AssignRepoRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board, berr = _board_editor_or_error(db, board_id, user)
    if berr:
        return berr
    repo = repos_repo.get_repo(db, payload.repo_id)
    if repo is None:
        return not_found("Repository not found")
    assignment = repos_repo.assign_repo(
        db,
        board_id=board_id,
        repo_id=repo.id,
        branch_override=payload.branch_override,
        allow_push=payload.allow_push,
    )
    return repos_repo.serialize_board_repo(
        db, repo, assignment.branch_override, assignment.allow_push
    )


@router.delete("/boards/{board_id}/repos/{repo_id}")
async def unassign_board_repo(
    board_id: str, repo_id: str, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board, berr = _board_editor_or_error(db, board_id, user)
    if berr:
        return berr
    repos_repo.unassign_repo(db, board_id=board_id, repo_id=repo_id)
    return {"ok": True}
