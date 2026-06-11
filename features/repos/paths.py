"""Filesystem paths for canonical repo clones and per-task working copies.

Canonical clones live under the owner's folder so repos are scoped per owner and
shared across every board they're assigned to::

    <workspace_root>/_repos/<owner_seg>/<repo_slug>/

Per-task working copies are created next to the task's other files::

    <task_workspace>/<repo_slug>/

All segments are validated so they can never escape the workspace root.
"""

from __future__ import annotations

from pathlib import Path

from agent_team.features.board.workspace import _safe_segment, workspace_root

#: Parent folder (under the workspace root) that holds every canonical clone.
REPOS_DIRNAME = "_repos"
#: Fallback owner segment when a repo has no owner (e.g. owner user deleted).
SHARED_OWNER_SEG = "_shared"


def repos_root() -> Path:
    """Absolute root that holds per-owner canonical clones."""
    return workspace_root() / REPOS_DIRNAME


def owner_segment(owner_id: str | None) -> str:
    """Return a safe path segment for an owner id (fallback for null owners)."""
    raw = (owner_id or "").strip()
    if not raw:
        return SHARED_OWNER_SEG
    return _safe_segment(raw, "owner id")


def canonical_path(owner_id: str | None, slug: str) -> Path:
    """Absolute path of a repo's canonical clone for ``owner_id``."""
    safe_slug = _safe_segment(slug, "repo slug")
    return repos_root() / owner_segment(owner_id) / safe_slug


def task_copy_path(task_workspace_path: str, slug: str) -> Path:
    """Absolute path of a repo's per-task working copy inside the task folder."""
    safe_slug = _safe_segment(slug, "repo slug")
    base = Path(task_workspace_path).resolve()
    target = (base / safe_slug).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"repo copy path escapes task workspace: {slug!r}")
    return target


def pull_lock_path() -> Path:
    """Path of the cross-process lock guarding the scheduled-pull ticker."""
    return repos_root() / ".pull.lock"
