"""Role-based access control for board & task endpoints.

This is the single source of truth for *who can do what* on a board. Endpoints
call one of the ``guard_*`` helpers with the minimum role they require; the
helper authenticates the request, resolves the target board (directly or via a
task / run / comment), computes the caller's effective role and rejects with a
``403`` when it is below the requirement.

Role hierarchy (ascending): ``viewer`` < ``editor`` < ``owner``. Admins are
treated as ``owner`` on every board. A user who is not the owner, not a member
and not an admin has *no* access at all — even read is denied.

Extending: to protect a new endpoint, resolve its board and call the matching
guard with the ``min_role`` you need. No per-endpoint role logic to copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamBoard, AgentTeamTask
from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import comments as comments_repo
from agent_team.features.board.repositories import members as members_repo
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.repositories import tasks as tasks_repo
from agent_team.web import auth_or_401, not_found

#: Role hierarchy — higher rank grants strictly more privileges.
ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def is_admin(user: Any) -> bool:
    role = getattr(user.role, "value", user.role)
    return str(role).lower() in {"admin", "super_admin"}


def role_at_least(role: str | None, minimum: str) -> bool:
    """Whether ``role`` satisfies the ``minimum`` required role."""
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK.get(minimum, 99)


def _forbidden(min_role: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"detail": f"This action requires '{min_role}' access on the board"},
    )


@dataclass
class BoardCtx:
    """Resolved board + caller for an authorized board request."""

    user: Any
    board: AgentTeamBoard
    role: str


@dataclass
class TaskCtx:
    """Resolved task (+ its board) + caller for an authorized task request."""

    user: Any
    task: AgentTeamTask
    board: AgentTeamBoard
    role: str


def _resolve_role(
    db: Session, user: Any, board: AgentTeamBoard, min_role: str
) -> tuple[str | None, JSONResponse | None]:
    """Compute the caller's role on ``board`` and reject if below ``min_role``."""
    role = members_repo.access_role(
        db, board, user_id=user.id, is_admin=is_admin(user)
    )
    if not role_at_least(role, min_role):
        return None, _forbidden(min_role)
    return role, None


def guard_board(
    db: Session, request: Request, board_id: str, *, min_role: str
) -> tuple[BoardCtx | None, JSONResponse | None]:
    """Authorize a request that targets a board directly by id."""
    user, err = auth_or_401(db, request)
    if err:
        return None, err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return None, not_found("Board not found")
    role, err = _resolve_role(db, user, board, min_role)
    if err:
        return None, err
    return BoardCtx(user=user, board=board, role=role or ""), None


def guard_task(
    db: Session, request: Request, task_id: str, *, min_role: str
) -> tuple[TaskCtx | None, JSONResponse | None]:
    """Authorize a request that targets a task; role is checked on its board."""
    user, err = auth_or_401(db, request)
    if err:
        return None, err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return None, not_found("Task not found")
    board = boards_repo.get_board(db, task.board_id)
    if board is None:
        return None, not_found("Board not found")
    role, err = _resolve_role(db, user, board, min_role)
    if err:
        return None, err
    return TaskCtx(user=user, task=task, board=board, role=role or ""), None


def guard_run(
    db: Session, request: Request, run_id: str, *, min_role: str
) -> tuple[Any | None, TaskCtx | None, JSONResponse | None]:
    """Authorize a request that targets a run; role is checked on its task's
    board. Returns ``(run, ctx, error)``."""
    run = runs_repo.get_run(db, run_id)
    if run is None:
        return None, None, not_found("Run not found")
    ctx, err = guard_task(db, request, run.task_id, min_role=min_role)
    if err:
        return None, None, err
    return run, ctx, None


def guard_comment(
    db: Session, request: Request, comment_id: str, *, min_role: str
) -> tuple[Any | None, TaskCtx | None, JSONResponse | None]:
    """Authorize a request that targets a comment; role is checked on its task's
    board. Returns ``(comment, ctx, error)``."""
    comment = comments_repo.get_comment(db, comment_id)
    if comment is None:
        return None, None, not_found("Comment not found")
    ctx, err = guard_task(db, request, comment.task_id, min_role=min_role)
    if err:
        return None, None, err
    return comment, ctx, None
