"""Per-task working copies of a board's assigned repos.

When an agent works a task it gets its own copy of each repo assigned to the
board, created with ``git clone --local <canonical> <task_dir>/<slug>``. Because
the source is a local path on the same filesystem, git **hardlinks** the object
store, so the only real disk cost is the checked-out working tree — and the copy
is fully independent (own branches/commits) without touching the canonical or
other tasks. No credentials are involved (the source is local).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamTask
from agent_team.features.repos.models import AgentTeamRepo
from agent_team.features.repos.paths import canonical_path, task_copy_path
from agent_team.features.repos.repositories import repos_for_board

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 300.0

#: Fallback commit identity when a repo configures none.
_DEFAULT_COMMITTER_NAME = "Agent Team"
_DEFAULT_COMMITTER_EMAIL = "agent-team@local"


def _run_git(*args: str, timeout: float = _GIT_TIMEOUT) -> tuple[int, str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LANG": "C"}
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=timeout, env=env
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def task_branch_name(task: AgentTeamTask) -> str:
    """Per-task working branch, e.g. ``agent/t-142``.

    Agents commit/push on this branch (never directly on the tracked default
    branch), so each task's work is isolated and reviewable.
    """
    key = (task.human_key or task.id or "task").strip().lower()
    safe = re.sub(r"[^a-z0-9._-]+", "-", key).strip("-/") or "task"
    return f"agent/{safe}"


def _committer_identity(repo: AgentTeamRepo) -> tuple[str, str]:
    name = (repo.committer_name or "").strip() or _DEFAULT_COMMITTER_NAME
    email = (repo.committer_email or "").strip() or _DEFAULT_COMMITTER_EMAIL
    return name, email


def _configure_copy(dest: str, repo: AgentTeamRepo) -> None:
    """Set the commit identity on a task working copy (idempotent)."""
    name, email = _committer_identity(repo)
    _run_git("-C", dest, "config", "user.name", name)
    _run_git("-C", dest, "config", "user.email", email)


def prepare_task_repos(db: Session, task: AgentTeamTask) -> list[dict]:
    """Ensure each assigned, cloned repo has a working copy in the task folder.

    Returns a list of ``{slug, path, branch}`` for the copies that exist after
    this call (workspace-relative ``path``). Repos whose canonical clone is not
    ready yet are skipped (the scheduler/owner must clone them first).
    """
    prepared: list[dict] = []
    work_branch = task_branch_name(task)
    for repo, branch_override, bp_allow_push in repos_for_board(db, task.board_id):
        canonical = canonical_path(repo.owner_id, repo.slug)
        if not (canonical / ".git").exists():
            logger.info(
                "task %s: repo %s has no canonical clone yet; skipping copy",
                task.human_key,
                repo.slug,
            )
            continue
        dest = task_copy_path(task.workspace_path, repo.slug)
        base_branch = (branch_override or repo.default_branch or "").strip()
        if not (dest / ".git").exists():
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            code, _out, err = _run_git("clone", "--local", str(canonical), str(dest))
            if code != 0:
                logger.warning(
                    "task %s: local clone of %s failed: %s",
                    task.human_key,
                    repo.slug,
                    err[:300],
                )
                continue
            if base_branch:
                bcode, _, berr = _run_git("-C", str(dest), "checkout", base_branch)
                if bcode != 0:
                    logger.info(
                        "task %s: checkout %s in %s skipped: %s",
                        task.human_key,
                        base_branch,
                        repo.slug,
                        berr[:200],
                    )
            # Branch off onto a per-task working branch so the agent never
            # commits straight onto the tracked branch.
            _run_git("-C", str(dest), "checkout", "-B", work_branch)
        _configure_copy(str(dest), repo)
        prepared.append(
            {
                "slug": repo.slug,
                "path": repo.slug,
                "branch": work_branch,
                "base_branch": base_branch or None,
                # Effective push = admin master gate AND this board's opt-in.
                "can_push": bool(repo.allow_push and bp_allow_push),
            }
        )
    return prepared


def cleanup_task_repos(db: Session, task: AgentTeamTask) -> int:
    """Remove per-repo working copies from a task folder. Returns count removed."""
    removed = 0
    for repo, _branch, _allow in repos_for_board(db, task.board_id):
        dest = task_copy_path(task.workspace_path, repo.slug)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
            removed += 1
    return removed


def prepare_task_repos_by_id(task_id: str) -> list[dict]:
    """Prepare repos for a task, opening a fresh session (thread-safe).

    Used by the manual "prepare workspace" endpoint via ``asyncio.to_thread`` so
    the blocking local clones don't run on the request's session/event loop.
    """
    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).first()
        if task is None:
            return []
        return prepare_task_repos(db, task)
    finally:
        db.close()


def list_task_repo_dirs(db: Session, task: AgentTeamTask) -> list[dict]:
    """Return ``{slug, path, present}`` for assigned repos (for the cockpit)."""
    out: list[dict] = []
    for repo, _branch, _allow in repos_for_board(db, task.board_id):
        dest = task_copy_path(task.workspace_path, repo.slug)
        out.append(
            {"slug": repo.slug, "path": repo.slug, "present": (dest / ".git").exists()}
        )
    return out
