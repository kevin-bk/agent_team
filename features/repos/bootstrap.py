"""Repository bootstrap commands executed inside a task runtime.

An administrator may attach one deterministic, non-interactive setup command
to a registered repository (for example ``npm ci`` or ``uv sync``).  Each task
clone runs that command before its first agent turn.  The success marker lives
under the clone's ``.git`` directory, so it never dirties product source and is
automatically discarded by reset/re-clone.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import posixpath
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_team.features.board.runtime.sandbox.base import Sandbox, SandboxError
from agent_team.features.repos.paths import task_copy_path

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10 * 60
_MAX_ERROR_OUTPUT = 4000
_locks: dict[str, asyncio.Lock] = {}


class RepoBootstrapError(SandboxError):
    """A configured repository setup command could not complete."""


@dataclass(frozen=True)
class RepoBootstrapSpec:
    slug: str
    command: str


def _timeout_seconds() -> int:
    raw = (os.environ.get("AGENT_TEAM_REPO_BOOTSTRAP_TIMEOUT_SECONDS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return max(30, min(value, 60 * 60))


def _configured_specs(board_id: str) -> list[RepoBootstrapSpec]:
    """Load configured commands without retaining detached ORM objects."""
    if not board_id:
        return []
    from agent_team.features.repos.repositories import repos_for_board
    from core.database.base import SessionLocal

    with SessionLocal() as db:
        rows = repos_for_board(db, board_id)
        return [
            RepoBootstrapSpec(repo.slug, command)
            for repo, _branch, _push, _wiki in rows
            if (command := (repo.bootstrap_command or "").strip())
        ]


def _fingerprint(spec: RepoBootstrapSpec) -> str:
    payload = json.dumps(
        {"version": 1, "slug": spec.slug, "command": spec.command},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _marker_path(repo_path: Path) -> Path:
    return repo_path / ".git" / "agent-team" / "bootstrap.json"


def _marker_matches(marker: Path, fingerprint: str) -> bool:
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return payload.get("version") == 1 and payload.get("fingerprint") == fingerprint


def _write_marker(marker: Path, spec: RepoBootstrapSpec, fingerprint: str) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "repo": spec.slug,
        "fingerprint": fingerprint,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(f"{json.dumps(payload, sort_keys=True)}\n", encoding="utf-8")
    temporary.replace(marker)


def _execution_cwd(
    sandbox: Sandbox,
    *,
    profile,
    host_workspace_path: str,
    slug: str,
) -> str:
    if getattr(sandbox, "is_remote", False):
        return posixpath.join(profile.workspace_mount_path, slug)
    return str(task_copy_path(host_workspace_path, slug))


def _failure_detail(stdout: str, stderr: str) -> str:
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if not combined:
        return "no command output"
    return combined[-_MAX_ERROR_OUTPUT:]


async def _run_one(
    sandbox: Sandbox,
    spec: RepoBootstrapSpec,
    *,
    profile,
    host_workspace_path: str,
) -> None:
    repo_path = task_copy_path(host_workspace_path, spec.slug)
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        raise RepoBootstrapError(
            f"Repository bootstrap could not find prepared task repo {spec.slug!r}"
        )
    fingerprint = _fingerprint(spec)
    marker = _marker_path(repo_path)
    if _marker_matches(marker, fingerprint):
        return

    logger.info("agent_team runtime: bootstrapping task repo=%s", spec.slug)
    result = await sandbox.exec_shell(
        spec.command,
        cwd=_execution_cwd(
            sandbox,
            profile=profile,
            host_workspace_path=host_workspace_path,
            slug=spec.slug,
        ),
        timeout_seconds=_timeout_seconds(),
    )
    if result.exit_code != 0 or result.timed_out:
        timeout_note = " (timed out)" if result.timed_out else ""
        raise RepoBootstrapError(
            f"Repository bootstrap failed for {spec.slug!r}{timeout_note} "
            f"with exit code {result.exit_code}: "
            f"{_failure_detail(result.stdout, result.stderr)}"
        )
    try:
        _write_marker(marker, spec, fingerprint)
    except OSError as exc:
        raise RepoBootstrapError(
            f"Repository bootstrap succeeded for {spec.slug!r}, but its task-clone "
            f"success marker could not be written: {exc}"
        ) from exc


async def run_repo_bootstraps(
    sandbox: Sandbox,
    *,
    task_id: str,
    board_id: str,
    profile,
    host_workspace_path: str,
) -> None:
    """Run every configured repo bootstrap once for this task clone.

    The in-process lock prevents the worker, verifier, and manual-runtime path
    from racing the same bootstrap. Cross-process retries remain safe because
    the success marker is written atomically and commands are required to be
    deterministic/idempotent.
    """
    specs = _configured_specs(board_id)
    if not specs:
        return
    lock = _locks.setdefault(task_id, asyncio.Lock())
    async with lock:
        for spec in specs:
            await _run_one(
                sandbox,
                spec,
                profile=profile,
                host_workspace_path=host_workspace_path,
            )
