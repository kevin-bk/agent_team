"""Data access + serialization for repos and board↔repo assignments."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.board.keys import slugify
from agent_team.features.repos.models import (
    AgentTeamBoardRepo,
    AgentTeamRepo,
)
from agent_team.features.repos.schedule import clamp_interval, compute_next_pull_at
from agent_team.features.repos.schemas import (
    BoardRepoDTO,
    RepoCreate,
    RepoDTO,
    RepoUpdate,
)


def _unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    candidate = base
    suffix = 2
    while db.query(AgentTeamRepo.id).filter(AgentTeamRepo.slug == candidate).first():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def list_repos(
    db: Session, *, owner_id: str | None = None, include_archived: bool = False
) -> list[AgentTeamRepo]:
    q = db.query(AgentTeamRepo)
    if owner_id is not None:
        q = q.filter(AgentTeamRepo.owner_id == owner_id)
    if not include_archived:
        q = q.filter(AgentTeamRepo.archived.is_(False))
    return q.order_by(AgentTeamRepo.updated_at.desc()).all()


def get_repo(db: Session, repo_id: str) -> AgentTeamRepo | None:
    return db.query(AgentTeamRepo).filter(AgentTeamRepo.id == repo_id).first()


def create_repo(db: Session, *, owner_id: str | None, payload: RepoCreate) -> AgentTeamRepo:
    repo = AgentTeamRepo(
        owner_id=owner_id,
        name=payload.name.strip(),
        slug=_unique_slug(db, payload.name),
        git_url=payload.git_url.strip(),
        default_branch=(payload.default_branch or None),
        auth_type=payload.auth_type,
        auth_username=(payload.auth_username or None),
        auth_secret=(payload.auth_secret or None),
        schedule_mode=payload.schedule_mode,
        schedule_interval_seconds=clamp_interval(payload.schedule_interval_seconds),
        schedule_cron=(payload.schedule_cron or None),
        allow_push=bool(payload.allow_push),
        committer_name=(payload.committer_name or None),
        committer_email=(payload.committer_email or None),
    )
    repo.next_pull_at = compute_next_pull_at(
        mode=repo.schedule_mode,
        interval_seconds=repo.schedule_interval_seconds,
        cron=repo.schedule_cron,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def update_repo(db: Session, repo: AgentTeamRepo, payload: RepoUpdate) -> AgentTeamRepo:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        repo.name = data["name"].strip()
    if "git_url" in data and data["git_url"]:
        repo.git_url = data["git_url"].strip()
    if "default_branch" in data:
        repo.default_branch = data["default_branch"] or None
    if "auth_type" in data and data["auth_type"]:
        repo.auth_type = data["auth_type"]
    if "auth_username" in data:
        repo.auth_username = data["auth_username"] or None
    # Write-only secret: omitted = keep; "" or null = clear.
    if "auth_secret" in data:
        repo.auth_secret = data["auth_secret"] or None
    if "allow_push" in data and data["allow_push"] is not None:
        repo.allow_push = bool(data["allow_push"])
    if "committer_name" in data:
        repo.committer_name = data["committer_name"] or None
    if "committer_email" in data:
        repo.committer_email = data["committer_email"] or None
    if "archived" in data and data["archived"] is not None:
        repo.archived = bool(data["archived"])

    schedule_changed = False
    if "schedule_mode" in data and data["schedule_mode"]:
        repo.schedule_mode = data["schedule_mode"]
        schedule_changed = True
    if "schedule_interval_seconds" in data and data["schedule_interval_seconds"] is not None:
        repo.schedule_interval_seconds = clamp_interval(data["schedule_interval_seconds"])
        schedule_changed = True
    if "schedule_cron" in data:
        repo.schedule_cron = data["schedule_cron"] or None
        schedule_changed = True
    if schedule_changed:
        repo.next_pull_at = compute_next_pull_at(
            mode=repo.schedule_mode,
            interval_seconds=repo.schedule_interval_seconds,
            cron=repo.schedule_cron,
        )

    db.commit()
    db.refresh(repo)
    return repo


def delete_repo(db: Session, repo: AgentTeamRepo) -> None:
    db.delete(repo)
    db.commit()


def count_boards_for_repo(db: Session, repo_id: str) -> int:
    return (
        db.query(func.count(AgentTeamBoardRepo.id))
        .filter(AgentTeamBoardRepo.repo_id == repo_id)
        .scalar()
        or 0
    )


def boards_using_repo(db: Session, repo_id: str) -> list[str]:
    rows = (
        db.query(AgentTeamBoardRepo.board_id)
        .filter(AgentTeamBoardRepo.repo_id == repo_id)
        .all()
    )
    return [r[0] for r in rows]


# ── assignments ───────────────────────────────────────────────────────────


def list_assignments(db: Session, board_id: str) -> list[AgentTeamBoardRepo]:
    return (
        db.query(AgentTeamBoardRepo)
        .filter(AgentTeamBoardRepo.board_id == board_id)
        .order_by(AgentTeamBoardRepo.created_at.asc())
        .all()
    )


def repos_for_board(
    db: Session, board_id: str
) -> list[tuple[AgentTeamRepo, str | None, bool]]:
    """Return ``(repo, branch_override, allow_push)`` for each assigned repo.

    ``allow_push`` is this board's per-assignment opt-in; the *effective* push
    permission also requires ``repo.allow_push`` (the admin master gate).
    """
    rows = (
        db.query(
            AgentTeamRepo,
            AgentTeamBoardRepo.branch_override,
            AgentTeamBoardRepo.allow_push,
        )
        .join(AgentTeamBoardRepo, AgentTeamBoardRepo.repo_id == AgentTeamRepo.id)
        .filter(AgentTeamBoardRepo.board_id == board_id)
        .filter(AgentTeamRepo.archived.is_(False))
        .order_by(AgentTeamBoardRepo.created_at.asc())
        .all()
    )
    return [(repo, branch, bool(allow)) for repo, branch, allow in rows]


def get_assignment(
    db: Session, board_id: str, repo_id: str
) -> AgentTeamBoardRepo | None:
    return (
        db.query(AgentTeamBoardRepo)
        .filter(
            AgentTeamBoardRepo.board_id == board_id,
            AgentTeamBoardRepo.repo_id == repo_id,
        )
        .first()
    )


def assign_repo(
    db: Session,
    *,
    board_id: str,
    repo_id: str,
    branch_override: str | None = None,
    allow_push: bool | None = None,
) -> AgentTeamBoardRepo:
    existing = get_assignment(db, board_id, repo_id)
    if existing is not None:
        existing.branch_override = branch_override or None
        if allow_push is not None:
            existing.allow_push = bool(allow_push)
        db.commit()
        db.refresh(existing)
        return existing
    row = AgentTeamBoardRepo(
        board_id=board_id,
        repo_id=repo_id,
        branch_override=branch_override or None,
        allow_push=bool(allow_push),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def unassign_repo(db: Session, *, board_id: str, repo_id: str) -> bool:
    row = get_assignment(db, board_id, repo_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# ── serialization ──────────────────────────────────────────────────────────


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def serialize_repo(db: Session, repo: AgentTeamRepo) -> RepoDTO:
    return RepoDTO(
        id=repo.id,
        owner_id=repo.owner_id,
        name=repo.name,
        slug=repo.slug,
        git_url=repo.git_url,
        default_branch=repo.default_branch,
        auth_type=repo.auth_type,
        auth_username=repo.auth_username,
        has_secret=repo.has_secret(),
        schedule_mode=repo.schedule_mode,
        schedule_interval_seconds=repo.schedule_interval_seconds,
        schedule_cron=repo.schedule_cron,
        allow_push=repo.allow_push,
        committer_name=repo.committer_name,
        committer_email=repo.committer_email,
        clone_status=repo.clone_status,
        last_synced_at=_iso(repo.last_synced_at),
        last_sync_status=repo.last_sync_status,
        last_sync_error=repo.last_sync_error,
        next_pull_at=_iso(repo.next_pull_at),
        used_by_boards=count_boards_for_repo(db, repo.id),
        archived=repo.archived,
        created_at=_iso(repo.created_at),
        updated_at=_iso(repo.updated_at),
    )


def serialize_board_repo(
    db: Session,
    repo: AgentTeamRepo,
    branch_override: str | None,
    allow_push: bool = False,
) -> BoardRepoDTO:
    return BoardRepoDTO(
        repo=serialize_repo(db, repo),
        branch_override=branch_override,
        allow_push=bool(allow_push),
    )
