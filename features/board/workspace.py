"""Per-task workspace folders on the host filesystem.

Each task owns ``<root>/<task_key>`` so agents working a task read and write
files there without colliding with other tasks. The root defaults to
``<project_root>/workspaces/agent_team`` and can be overridden with the
``AGENT_TEAM_WORKSPACE_ROOT`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.config import PROJECT_ROOT

_DEFAULT_ROOT = PROJECT_ROOT / "workspaces" / "agent_team"


def workspace_root() -> Path:
    """Return the absolute root directory that holds per-task folders."""
    override = os.environ.get("AGENT_TEAM_WORKSPACE_ROOT", "").strip()
    root = Path(override) if override else _DEFAULT_ROOT
    return root.expanduser().resolve()


def _safe_segment(value: str, label: str) -> str:
    """Validate ``value`` as a single safe path segment (no separators/traversal)."""
    safe = (value or "").strip().strip("/")
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        raise ValueError(f"unsafe {label} for workspace path: {value!r}")
    return safe


def workspace_path_for(board_slug: str, task_key: str) -> str:
    """Return a task's folder path, e.g. ``.../agent_team/team-alpha/T-142``.

    Tasks are grouped by board so collaborators on a board share one tree.
    Both segments are validated so they can never escape the workspace root.
    """
    safe_board = _safe_segment(board_slug, "board slug")
    safe_task = _safe_segment(task_key, "task key")
    return str(workspace_root() / safe_board / safe_task)


def ensure_task_workspace(path: str) -> str:
    """Create ``path`` (and parents) if missing, idempotently. Returns ``path``."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def resolve_in_workspace(workspace_path: str, rel: str) -> Path:
    """Resolve ``rel`` under ``workspace_path``, refusing any escape.

    ``rel`` may be either workspace-relative (as the file browser tree emits) or
    an absolute path that already points inside the workspace — the agent's file
    tools record absolute paths, so the cockpit's "open current file" button
    passes one back. Either way the result is constrained to the workspace.

    Returns the absolute path. Raises ``ValueError`` if the result would fall
    outside the task's workspace (path traversal) so file routes stay sandboxed.
    """
    base = Path(workspace_path).resolve()
    rel = rel or ""
    rel_path = Path(rel)
    if rel_path.is_absolute():
        candidate = rel_path.resolve()
    else:
        candidate = (base / rel.lstrip("/")).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"path escapes workspace: {rel!r}")
    return candidate


def build_tree(root: Path, rel: str = "", depth: int = 1, max_entries: int = 500) -> dict:
    """Build a shallow file tree under ``root/rel`` for the file browser."""
    start = resolve_in_workspace(str(root), rel)
    entries: list[dict] = []
    truncated = False

    def _walk(directory: Path, level: int) -> list[dict]:
        nonlocal truncated
        out: list[dict] = []
        if not directory.is_dir():
            return out
        try:
            children = sorted(
                directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError:
            return out
        for child in children:
            if len(out) >= max_entries:
                truncated = True
                break
            node: dict = {
                "name": child.name,
                "path": str(child.relative_to(Path(str(root)).resolve())),
                "kind": "dir" if child.is_dir() else "file",
            }
            if child.is_file():
                try:
                    node["size"] = child.stat().st_size
                except OSError:
                    node["size"] = None
            elif level > 1:
                node["children"] = _walk(child, level - 1)
            out.append(node)
        return out

    entries = _walk(start, max(1, depth))
    return {"root": str(root), "entries": entries, "truncated": truncated}
