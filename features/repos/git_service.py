"""Git operations for canonical repo clones (clone / pull / status).

Credentials are injected only at git-time (an ``http.extraHeader`` Basic header
for token auth, or ``GIT_SSH_COMMAND`` for an SSH key) and are **never** written
into ``.git/config``, so the canonical clone on disk holds no secret. Per-task
working copies are made with ``git clone --local`` from a local path and so need
no credentials at all (see ``task_copy``).

The DB-mutating entrypoint :func:`sync_repo_by_id` opens its own ``Session`` and
is therefore safe to call from a worker thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_team.features.repos.models import (
    AUTH_SSH,
    AUTH_TOKEN,
    AgentTeamRepo,
)
from agent_team.features.repos.paths import canonical_path
from agent_team.features.repos.schedule import compute_next_pull_at

logger = logging.getLogger(__name__)

_CLONE_TIMEOUT = 600.0
_GIT_TIMEOUT = 180.0
#: Default Basic-auth username for token auth (works for GitHub/GitLab PATs;
#: Bitbucket app passwords need the real username, so let the user override).
_DEFAULT_TOKEN_USER = "x-access-token"


@dataclass
class GitOpResult:
    ok: bool
    action: str  # clone | pull | noop
    message: str


def _run_git(
    *args: str, cwd: str | None = None, env: dict | None = None, timeout: float = _GIT_TIMEOUT
) -> tuple[int, str, str]:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LANG": "C"}
    if env:
        full_env.update(env)
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


@contextmanager
def _auth(repo: AgentTeamRepo) -> Iterator[tuple[list[str], dict]]:
    """Yield ``(extra_git_args, env)`` carrying credentials; clean up after."""
    extra: list[str] = []
    env: dict[str, str] = {}
    tmp_key: str | None = None
    secret = (repo.auth_secret or "").strip()
    try:
        if repo.auth_type == AUTH_TOKEN and secret:
            user = (repo.auth_username or "").strip() or _DEFAULT_TOKEN_USER
            basic = base64.b64encode(f"{user}:{secret}".encode()).decode("ascii")
            extra = ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
        elif repo.auth_type == AUTH_SSH and secret:
            fd, tmp_key = tempfile.mkstemp(prefix="at_repo_key_")
            with os.fdopen(fd, "w") as fh:
                fh.write(secret if secret.endswith("\n") else secret + "\n")
            os.chmod(tmp_key, stat.S_IRUSR | stat.S_IWUSR)
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {tmp_key} -o StrictHostKeyChecking=accept-new "
                "-o IdentitiesOnly=yes"
            )
        yield extra, env
    finally:
        if tmp_key:
            try:
                os.unlink(tmp_key)
            except OSError:
                pass


def _redact(message: str, repo: AgentTeamRepo) -> str:
    secret = (repo.auth_secret or "").strip()
    if secret and secret in message:
        message = message.replace(secret, "***")
    return message


def _clone(repo: AgentTeamRepo, dest: Path) -> tuple[int, str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _auth(repo) as (extra, env):
        args = [*extra, "clone"]
        branch = (repo.default_branch or "").strip()
        if branch:
            args += ["--branch", branch]
        args += [repo.git_url, str(dest)]
        return _run_git(*args, env=env, timeout=_CLONE_TIMEOUT)


def _pull(repo: AgentTeamRepo, dest: Path) -> tuple[int, str, str]:
    with _auth(repo) as (extra, env):
        code, out, err = _run_git(
            *extra, "-C", str(dest), "fetch", "--prune", env=env, timeout=_CLONE_TIMEOUT
        )
        if code != 0:
            return code, out, err
        return _run_git(
            *extra, "-C", str(dest), "pull", "--ff-only", env=env, timeout=_CLONE_TIMEOUT
        )


def push_branch(repo: AgentTeamRepo, work_dir: str, branch: str) -> GitOpResult:
    """Push ``HEAD`` of a task working copy to ``branch`` on the real remote.

    Credentials are injected only here (in the trusted backend), never stored in
    the task copy's ``.git/config`` — the agent that owns the working copy can
    commit locally but cannot reach the remote without going through this.
    """
    refspec = f"HEAD:refs/heads/{branch}"
    with _auth(repo) as (extra, env):
        code, out, err = _run_git(
            *extra,
            "-C",
            work_dir,
            "push",
            repo.git_url,
            refspec,
            env=env,
            timeout=_CLONE_TIMEOUT,
        )
    ok = code == 0
    msg = _redact((out or err or ("ok" if ok else "push failed")).strip(), repo)[:2000]
    return GitOpResult(ok, "push", msg)


def repo_status(repo: AgentTeamRepo) -> dict:
    """Return ``{is_git, branch, last_commit}`` for the canonical clone."""
    dest = canonical_path(repo.owner_id, repo.slug)
    if not (dest / ".git").exists():
        return {"is_git": False}
    _, branch, _ = _run_git("-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD")
    _, commit, _ = _run_git("-C", str(dest), "log", "-1", "--format=%h %s")
    return {
        "is_git": True,
        "branch": branch or None,
        "last_commit": commit or None,
    }


def sync_repo_by_id(repo_id: str, *, force_clone: bool = False) -> GitOpResult:
    """Clone (if missing/forced) or fast-forward pull a repo; persist status.

    Opens its own DB session so it is safe under ``asyncio.to_thread``.
    """
    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        repo = db.query(AgentTeamRepo).filter(AgentTeamRepo.id == repo_id).first()
        if repo is None:
            return GitOpResult(False, "noop", "repo not found")

        dest = canonical_path(repo.owner_id, repo.slug)
        is_cloned = (dest / ".git").exists()

        if force_clone or not is_cloned:
            action = "clone"
            repo.clone_status = "cloning"
            db.commit()
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            code, out, err = _clone(repo, dest)
        else:
            action = "pull"
            code, out, err = _pull(repo, dest)

        ok = code == 0
        now = datetime.now(UTC)
        if ok:
            repo.clone_status = "cloned"
            repo.last_sync_status = "ok"
            repo.last_sync_error = None
            repo.last_synced_at = now
        else:
            repo.clone_status = "cloned" if is_cloned and not force_clone else "error"
            repo.last_sync_status = "failed"
            repo.last_sync_error = _redact((err or out or "git failed"), repo)[:2000]
        repo.next_pull_at = compute_next_pull_at(
            mode=repo.schedule_mode,
            interval_seconds=repo.schedule_interval_seconds,
            cron=repo.schedule_cron,
            base=now,
        )
        db.commit()
        msg = _redact((out or err or "ok").strip(), repo)[:2000]
        return GitOpResult(ok, action, msg)
    except subprocess.TimeoutExpired:
        db.rollback()
        return GitOpResult(False, "noop", "git timed out")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("repo sync failed for %s", repo_id)
        return GitOpResult(False, "noop", str(exc)[:2000])
    finally:
        db.close()
