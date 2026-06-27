"""Durable planning artifacts: the contract between human, planner and loop.

Strict planning persists a small set of files inside the task workspace under
``.agent-team/``. These are the source of truth the generator and evaluator both
read, and the surface a human reviews/edits/approves before any execution runs.

This module owns all artifact file I/O so the router, planning service and loop
never hand-roll paths. Every path is resolved *inside* the workspace and any
attempt to escape it (``..`` or absolute) is rejected, because some artifact
contents (e.g. ``TASKS.json``) are written by an agent and must not be trusted to
point outside the workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

#: Workspace-relative directory all planning artifacts live under.
ARTIFACT_DIR = ".agent-team"

#: The source-of-truth artifact paths (workspace-relative).
SPEC_PATH = f"{ARTIFACT_DIR}/SPEC.md"
PLAN_PATH = f"{ARTIFACT_DIR}/PLAN.md"
TASKS_PATH = f"{ARTIFACT_DIR}/TASKS.json"
PLAN_REVIEW_PATH = f"{ARTIFACT_DIR}/PLAN_REVIEW.json"
EVIDENCE_PATH = f"{ARTIFACT_DIR}/EVIDENCE.json"
PLAN_CHANGE_REQUEST_PATH = f"{ARTIFACT_DIR}/PLAN_CHANGE_REQUEST.md"
ARCHIVE_DIR = f"{ARTIFACT_DIR}/archive"

#: Artifacts that gate a strict approval. SPEC and PLAN are required; TASKS is
#: advisory in v1 (validated when present, but not required to approve).
APPROVAL_ARTIFACTS: tuple[str, ...] = (SPEC_PATH, PLAN_PATH, TASKS_PATH)
REQUIRED_FOR_APPROVAL: tuple[str, ...] = (SPEC_PATH, PLAN_PATH)

#: Editable artifacts addressable by short name through the edit endpoint.
EDITABLE_ARTIFACTS: dict[str, str] = {
    "SPEC.md": SPEC_PATH,
    "PLAN.md": PLAN_PATH,
    "TASKS.json": TASKS_PATH,
}


class ArtifactError(ValueError):
    """Raised when an artifact path is unsafe or content is invalid."""


@dataclass(frozen=True)
class ArtifactMeta:
    """Metadata for one planning artifact on disk."""

    path: str
    exists: bool
    etag: str | None
    size: int
    updated_at: str | None


def _safe_abs(workspace_path: str, rel_path: str) -> str:
    """Resolve ``rel_path`` strictly inside ``workspace_path``.

    Rejects absolute paths and any path that escapes the workspace root, so an
    agent-written reference can never read or clobber files outside the task
    workspace.
    """
    if not workspace_path:
        raise ArtifactError("workspace path is not set")
    rel = (rel_path or "").strip()
    if not rel or os.path.isabs(rel) or rel.startswith("~"):
        raise ArtifactError(f"unsafe artifact path: {rel_path!r}")
    root = os.path.realpath(workspace_path)
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ArtifactError(f"artifact path escapes workspace: {rel_path!r}")
    return target


def etag(text: str) -> str:
    """Content etag (sha256) used for optimistic-concurrency edits."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def read_text(workspace_path: str, rel_path: str) -> str | None:
    """Read an artifact's text, or ``None`` if it does not exist."""
    abs_path = _safe_abs(workspace_path, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def write_text(workspace_path: str, rel_path: str, content: str) -> str:
    """Write an artifact (creating parent dirs) and return its new etag."""
    abs_path = _safe_abs(workspace_path, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return etag(content)


def exists(workspace_path: str, rel_path: str) -> bool:
    """Whether an artifact file exists and is non-empty."""
    text = read_text(workspace_path, rel_path)
    return bool(text and text.strip())


def read_json(workspace_path: str, rel_path: str) -> object | None:
    """Parse a JSON artifact, or ``None`` when missing/blank/invalid."""
    text = read_text(workspace_path, rel_path)
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def metadata(workspace_path: str, rel_path: str) -> ArtifactMeta:
    """Return on-disk metadata for one artifact."""
    try:
        abs_path = _safe_abs(workspace_path, rel_path)
    except ArtifactError:
        return ArtifactMeta(path=rel_path, exists=False, etag=None, size=0, updated_at=None)
    text = read_text(workspace_path, rel_path)
    if text is None:
        return ArtifactMeta(path=rel_path, exists=False, etag=None, size=0, updated_at=None)
    try:
        mtime = os.path.getmtime(abs_path)
        updated = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    except OSError:
        updated = None
    return ArtifactMeta(
        path=rel_path,
        exists=True,
        etag=etag(text),
        size=len(text.encode("utf-8")),
        updated_at=updated,
    )


def all_metadata(workspace_path: str) -> list[ArtifactMeta]:
    """Metadata for every known planning artifact (existing or not)."""
    paths = (
        SPEC_PATH,
        PLAN_PATH,
        TASKS_PATH,
        PLAN_REVIEW_PATH,
        EVIDENCE_PATH,
        PLAN_CHANGE_REQUEST_PATH,
    )
    return [metadata(workspace_path, p) for p in paths]


def approved_etags(workspace_path: str) -> dict[str, str]:
    """Etags of the approval artifacts that currently exist on disk.

    Keyed by short name (``SPEC.md``…) to match the approval metadata snapshot.
    """
    out: dict[str, str] = {}
    for name, rel in EDITABLE_ARTIFACTS.items():
        text = read_text(workspace_path, rel)
        if text is not None and text.strip():
            out[name] = etag(text)
    return out


def missing_required(workspace_path: str) -> list[str]:
    """Required-for-approval artifacts that are absent or blank."""
    return [p for p in REQUIRED_FOR_APPROVAL if not exists(workspace_path, p)]


def validate_tasks(data: object) -> list[str]:
    """Validate a ``TASKS.json`` document; return a list of error strings.

    Advisory in v1 (the loop does not schedule from it yet), but validated when
    present so a malformed task graph surfaces early instead of silently in v2.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["TASKS.json must be a JSON object"]
    if data.get("version") != 1:
        errors.append("TASKS.json: unsupported or missing 'version' (expected 1)")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return errors + ["TASKS.json: 'tasks' must be a list"]

    ids: set[str] = set()
    valid_status = {"pending", "in_progress", "complete", "blocked", "skipped"}
    deps: dict[str, list[str]] = {}
    for i, task in enumerate(tasks):
        where = f"TASKS.json: task[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{where} must be an object")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not tid.strip():
            errors.append(f"{where} missing string 'id'")
            continue
        if tid in ids:
            errors.append(f"{where} duplicate id {tid!r}")
        ids.add(tid)
        status = task.get("status", "pending")
        if status not in valid_status:
            errors.append(f"{where} unknown status {status!r}")
        depends = task.get("depends_on") or []
        if not isinstance(depends, list):
            errors.append(f"{where} 'depends_on' must be a list")
            depends = []
        deps[tid] = [str(d) for d in depends]

    for tid, dep_ids in deps.items():
        for dep in dep_ids:
            if dep not in ids:
                errors.append(f"TASKS.json: task {tid!r} depends on unknown id {dep!r}")

    if _has_cycle(deps):
        errors.append("TASKS.json: dependency graph has a cycle")
    return errors


def _has_cycle(deps: dict[str, list[str]]) -> bool:
    """Whether the dependency graph contains a cycle (DFS, three-colour)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in deps}

    def visit(node: str) -> bool:
        colour[node] = GRAY
        for nxt in deps.get(node, []):
            if nxt not in colour:
                continue
            if colour[nxt] == GRAY:
                return True
            if colour[nxt] == WHITE and visit(nxt):
                return True
        colour[node] = BLACK
        return False

    return any(colour[node] == WHITE and visit(node) for node in deps)


def archive_change_request(workspace_path: str) -> str | None:
    """Move an active ``PLAN_CHANGE_REQUEST.md`` into the archive.

    Only the active marker path gates execution; archiving it (rather than
    deleting) keeps the history while clearing the gate. Returns the archive
    path, or ``None`` when there was no active marker.
    """
    text = read_text(workspace_path, PLAN_CHANGE_REQUEST_PATH)
    if not text or not text.strip():
        return None
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    rel_dest = f"{ARCHIVE_DIR}/plan-change-requests/{stamp}.md"
    write_text(workspace_path, rel_dest, text)
    try:
        os.remove(_safe_abs(workspace_path, PLAN_CHANGE_REQUEST_PATH))
    except OSError:
        pass
    return rel_dest
