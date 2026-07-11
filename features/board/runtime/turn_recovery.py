"""Turn-aware workspace checkpoints for planner and generator retries.

The agent session is useful context, but it is not a recovery log: a cancelled
turn may or may not be present in the provider's loaded history.  This module
therefore checkpoints the real workspace immediately before each recoverable
turn.  If that run errors, its next same-role successor receives a compact,
machine-generated hand-off describing the surviving file delta and the last
observed tool activity.

Snapshots are deliberately lightweight and non-invasive.  For each task repo we
store HEAD plus signatures for files already dirty/untracked; clean files are
detected later through Git status or a HEAD change.  The real index is never
modified and no hidden commit/tree objects are created.  Files outside task repos
(planning artifacts and attachments) are tracked with a bounded manifest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from agent_team.features.board.models import (
    RUN_ROLE_GENERATOR,
    RUN_ROLE_PLANNER,
    AgentTeamRun,
    AgentTeamRunEvent,
)
from agent_team.features.board.runtime.events import (
    EVENT_TOOL_USE_END,
    EVENT_TOOL_USE_PROGRESS,
    EVENT_TOOL_USE_START,
    RUN_CANCELLED,
    RUN_ERROR,
)

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1
RECOVERABLE_ROLES = frozenset({RUN_ROLE_PLANNER, RUN_ROLE_GENERATOR})

_GIT_TIMEOUT_SECONDS = 30
_MAX_TRACKED_PATHS = 5_000
_MAX_HASH_BYTES = 20 * 1024 * 1024
_MAX_PROMPT_PATHS = 100
_MAX_PROMPT_TOOLS = 3
_MAX_TOOL_TEXT = 500

# Generated/dependency trees outside task repos are not product state.  Pruning
# them also keeps a checkpoint bounded when a skill pack or local toolchain is
# materialised in the workspace root.
_LOOSE_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".claude",
        ".cursor",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)
_LOOSE_EXCLUDED_FILES = frozenset(
    {
        # Re-rendered by the backend before every direct-CLI turn.
        ".agent-team/TASK.md",
        "AGENTS.md",
        "CLAUDE.md",
    }
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_git(repo_path: str, *args: str) -> tuple[int, bytes, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LANG": "C"}
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, b"", str(exc)
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode, proc.stdout or b"", stderr


def _nul_paths(payload: bytes) -> list[str]:
    return [os.fsdecode(part) for part in payload.split(b"\0") if part]


def _file_signature(path: str) -> dict[str, Any]:
    """Return a stable, JSON-safe signature without following symlinks."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"state": "deleted"}
    except OSError as exc:
        return {"state": "unreadable", "error": type(exc).__name__}

    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(path).encode("utf-8", errors="surrogateescape")
        except OSError as exc:
            return {
                "state": "unreadable",
                "kind": "symlink",
                "mode": mode,
                "error": type(exc).__name__,
            }
        return {
            "state": "present",
            "kind": "symlink",
            "mode": mode,
            "size": len(target),
            "sha256": hashlib.sha256(target).hexdigest(),
        }
    if not stat.S_ISREG(info.st_mode):
        return {
            "state": "present",
            "kind": "other",
            "mode": mode,
            "size": int(info.st_size),
        }

    signature: dict[str, Any] = {
        "state": "present",
        "kind": "file",
        "mode": mode,
        "size": int(info.st_size),
    }
    if info.st_size > _MAX_HASH_BYTES:
        # Large build artifacts should not be read into the checkpoint path.  The
        # size + nanosecond mtime is sufficient to flag a later change.
        signature["mtime_ns"] = int(info.st_mtime_ns)
        return signature
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        signature.update({"state": "unreadable", "error": type(exc).__name__})
        return signature
    signature["sha256"] = digest.hexdigest()
    return signature


def _normalise_repo_paths(workspace_path: str, repo_paths: Iterable[str]) -> list[tuple[str, str]]:
    workspace = os.path.realpath(workspace_path)
    found: dict[str, str] = {}
    candidates = list(repo_paths)
    # Recovery remains bounded if repo preparation failed to return metadata but
    # existing task clones are still on disk. Task repos are normally immediate
    # children; also support a workspace that is itself a Git worktree.
    if os.path.exists(os.path.join(workspace, ".git")):
        candidates.append(workspace)
    try:
        candidates.extend(
            entry.path
            for entry in os.scandir(workspace)
            if entry.is_dir(follow_symlinks=False)
            and os.path.exists(os.path.join(entry.path, ".git"))
        )
    except OSError:
        pass

    for raw in candidates:
        if not raw:
            continue
        absolute = raw if os.path.isabs(raw) else os.path.join(workspace, raw)
        absolute = os.path.realpath(absolute)
        try:
            common = os.path.commonpath([workspace, absolute])
        except ValueError:
            continue
        if common != workspace or not os.path.isdir(absolute):
            continue
        code, root_bytes, _err = _run_git(absolute, "rev-parse", "--show-toplevel")
        if code != 0:
            continue
        root = os.path.realpath(os.fsdecode(root_bytes.strip()))
        try:
            if os.path.commonpath([workspace, root]) != workspace:
                continue
        except ValueError:
            continue
        rel = os.path.relpath(root, workspace).replace(os.sep, "/")
        found[rel] = root
    return sorted(found.items())


def _repo_snapshot(rel_path: str, repo_path: str) -> dict[str, Any]:
    code, head_bytes, head_error = _run_git(repo_path, "rev-parse", "--verify", "HEAD")
    head = os.fsdecode(head_bytes.strip()) if code == 0 else None

    dirty_paths: set[str] = set()
    errors: list[str] = []
    if head:
        code, payload, error = _run_git(
            repo_path, "diff", "--name-only", "-z", "HEAD", "--"
        )
        if code == 0:
            dirty_paths.update(_nul_paths(payload))
        else:
            errors.append(f"git diff: {error}")
    else:
        code, payload, error = _run_git(repo_path, "ls-files", "-z")
        if code == 0:
            dirty_paths.update(_nul_paths(payload))
        elif head_error:
            errors.append(f"git head: {head_error}")
        if code != 0:
            errors.append(f"git ls-files: {error}")

    code, payload, error = _run_git(
        repo_path, "ls-files", "--others", "--exclude-standard", "-z"
    )
    untracked = set(_nul_paths(payload)) if code == 0 else set()
    if code != 0:
        errors.append(f"git untracked: {error}")
    dirty_paths.update(untracked)

    truncated = len(dirty_paths) > _MAX_TRACKED_PATHS
    manifest: dict[str, dict[str, Any]] = {}
    for rel in sorted(dirty_paths)[:_MAX_TRACKED_PATHS]:
        signature = _file_signature(os.path.join(repo_path, rel))
        if rel in untracked and signature.get("state") == "present":
            signature["git_state"] = "untracked"
        elif signature.get("state") == "deleted":
            signature["git_state"] = "deleted"
        else:
            signature["git_state"] = "modified"
        manifest[rel.replace(os.sep, "/")] = signature

    result: dict[str, Any] = {
        "path": rel_path,
        "head": head,
        "dirty": manifest,
        "truncated": truncated,
    }
    if errors:
        result["warnings"] = errors[:5]
    return result


def _is_repo_subtree(rel_dir: str, repo_rels: set[str]) -> bool:
    rel_dir = rel_dir.replace(os.sep, "/")
    if "." in repo_rels:
        return True
    return any(rel_dir == repo or rel_dir.startswith(f"{repo}/") for repo in repo_rels)


def _loose_manifest(workspace_path: str, repo_rels: set[str]) -> tuple[dict[str, Any], bool]:
    manifest: dict[str, Any] = {}
    if not os.path.isdir(workspace_path):
        return manifest, False

    truncated = False
    for root, dirs, files in os.walk(workspace_path, topdown=True, followlinks=False):
        root_rel = os.path.relpath(root, workspace_path)
        root_rel = "" if root_rel == "." else root_rel.replace(os.sep, "/")
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            child_rel = f"{root_rel}/{dirname}".strip("/")
            if dirname in _LOOSE_EXCLUDED_DIRS or _is_repo_subtree(child_rel, repo_rels):
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs

        for filename in sorted(files):
            rel = f"{root_rel}/{filename}".strip("/")
            if rel in _LOOSE_EXCLUDED_FILES or _is_repo_subtree(rel, repo_rels):
                continue
            if len(manifest) >= _MAX_TRACKED_PATHS:
                truncated = True
                return manifest, truncated
            manifest[rel] = _file_signature(os.path.join(root, filename))
    return manifest, truncated


def capture_workspace(
    workspace_path: str, *, repo_paths: Iterable[str] = ()
) -> dict[str, Any]:
    """Capture a bounded, non-mutating checkpoint of the current workspace."""
    normalised = _normalise_repo_paths(workspace_path, repo_paths)
    repo_rels = {rel for rel, _absolute in normalised}
    repos = [_repo_snapshot(rel, absolute) for rel, absolute in normalised]
    loose, loose_truncated = _loose_manifest(workspace_path, repo_rels)
    return {
        "version": SNAPSHOT_VERSION,
        "captured_at": _now_iso(),
        "repos": repos,
        "loose": loose,
        "loose_truncated": loose_truncated,
    }


def _repo_by_path(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("repos")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("path")): row
        for row in rows
        if isinstance(row, dict) and row.get("path") is not None
    }


def _git_head_delta(repo_path: str, old_head: str | None, new_head: str | None) -> set[str]:
    if not old_head or not new_head or old_head == new_head:
        return set()
    code, payload, _error = _run_git(
        repo_path, "diff", "--name-only", "-z", old_head, new_head, "--"
    )
    return set(_nul_paths(payload)) if code == 0 else set()


def _current_change_kind(signature: dict[str, Any] | None, *, fallback: str) -> str:
    if signature is None:
        return fallback
    if signature.get("state") == "deleted" or signature.get("git_state") == "deleted":
        return "deleted"
    if signature.get("git_state") == "untracked":
        return "added"
    return "modified"


def compare_snapshots(
    workspace_path: str,
    before: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Return only changes that happened after ``before`` was captured."""
    changes: dict[str, str] = {}
    repo_heads: list[dict[str, Any]] = []
    old_repos = _repo_by_path(before)
    new_repos = _repo_by_path(current)

    for repo_rel in sorted(set(old_repos) | set(new_repos)):
        old = old_repos.get(repo_rel)
        new = new_repos.get(repo_rel)
        prefix = "" if repo_rel == "." else f"{repo_rel}/"
        if old is None:
            for rel, signature in (new or {}).get("dirty", {}).items():
                changes[f"{prefix}{rel}"] = _current_change_kind(signature, fallback="added")
            continue
        if new is None:
            changes[repo_rel] = "repository_removed"
            continue

        old_head = old.get("head")
        new_head = new.get("head")
        if old_head != new_head:
            repo_heads.append({"path": repo_rel, "before": old_head, "after": new_head})
            repo_path = os.path.join(workspace_path, repo_rel)
            for rel in _git_head_delta(repo_path, old_head, new_head):
                signature = (new.get("dirty") or {}).get(rel)
                changes[f"{prefix}{rel}"] = _current_change_kind(
                    signature, fallback="modified"
                )

        old_dirty = old.get("dirty") if isinstance(old.get("dirty"), dict) else {}
        new_dirty = new.get("dirty") if isinstance(new.get("dirty"), dict) else {}
        for rel in sorted(set(old_dirty) | set(new_dirty)):
            previous = old_dirty.get(rel)
            now = new_dirty.get(rel)
            if previous == now:
                continue
            path = f"{prefix}{rel}"
            if now is None:
                # It left the dirty set. A HEAD delta above records a commit;
                # otherwise the turn restored/removed pre-existing dirty state.
                kind = "deleted" if previous.get("git_state") == "untracked" else "restored"
                changes.setdefault(path, kind)
            else:
                changes[path] = _current_change_kind(now, fallback="modified")

    old_loose = before.get("loose") if isinstance(before.get("loose"), dict) else {}
    new_loose = current.get("loose") if isinstance(current.get("loose"), dict) else {}
    for rel in sorted(set(old_loose) | set(new_loose)):
        previous = old_loose.get(rel)
        now = new_loose.get(rel)
        if previous == now:
            continue
        if previous is None:
            changes[rel] = "added"
        elif now is None or now.get("state") == "deleted":
            changes[rel] = "deleted"
        else:
            changes[rel] = "modified"

    return {
        "version": SNAPSHOT_VERSION,
        "captured_at": _now_iso(),
        "changed_files": [
            {"path": path, "change": changes[path]} for path in sorted(changes)
        ],
        "repo_heads": repo_heads,
        "snapshot_truncated": bool(
            before.get("loose_truncated")
            or current.get("loose_truncated")
            or any(row.get("truncated") for row in old_repos.values())
            or any(row.get("truncated") for row in new_repos.values())
        ),
    }


def _event_data(row: AgentTeamRunEvent) -> dict[str, Any]:
    try:
        value = json.loads(row.data or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _merge_tool(existing: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    if data.get("tool_name"):
        merged["name"] = str(data["tool_name"])
    tool_input = data.get("input")
    if isinstance(tool_input, dict) and tool_input:
        previous_input = merged.get("input") if isinstance(merged.get("input"), dict) else {}
        merged["input"] = {**previous_input, **tool_input}
    return merged


def event_recovery_summary(db: Session, run_id: str) -> dict[str, Any]:
    """Summarise persisted tool boundaries; never infer unobserved completion."""
    rows = (
        db.query(AgentTeamRunEvent)
        .filter(AgentTeamRunEvent.run_id == run_id)
        .order_by(AgentTeamRunEvent.seq.asc())
        .all()
    )
    open_tools: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    for row in rows:
        data = _event_data(row)
        tool_id = str(data.get("tool_id") or "")
        if row.type == EVENT_TOOL_USE_START and tool_id:
            open_tools[tool_id] = _merge_tool(
                {"tool_id": tool_id, "started_seq": row.seq}, data
            )
        elif row.type == EVENT_TOOL_USE_PROGRESS and tool_id in open_tools:
            open_tools[tool_id] = _merge_tool(open_tools[tool_id], data)
        elif row.type == EVENT_TOOL_USE_END and tool_id:
            tool = open_tools.pop(tool_id, {"tool_id": tool_id})
            tool = _merge_tool(tool, data)
            tool.update(
                {
                    "ended_seq": row.seq,
                    "success": bool(data.get("success")),
                    "is_error": bool(data.get("is_error")),
                }
            )
            completed.append(tool)
    return {
        "last_event_seq": rows[-1].seq if rows else 0,
        "last_event_type": rows[-1].type if rows else None,
        "last_completed_tool": completed[-1] if completed else None,
        "possibly_in_flight_tools": list(open_tools.values())[-_MAX_PROMPT_TOOLS:],
    }


def _safe_prompt_text(value: object, limit: int = _MAX_TOOL_TEXT) -> str:
    text = str(value or "").replace("\x00", " ")
    text = " ".join(text.split())
    return text[:limit]


def _tool_prompt_line(tool: dict[str, Any]) -> str:
    name = _safe_prompt_text(tool.get("name") or "tool", 100)
    tool_input = tool.get("input") if isinstance(tool.get("input"), dict) else {}
    detail = tool_input.get("command") or tool_input.get("path") or tool_input.get("file_path")
    suffix = f": {_safe_prompt_text(detail)}" if detail else ""
    outcome = " [failed]" if tool.get("is_error") or tool.get("success") is False else ""
    return f"{name}{outcome}{suffix}"


def build_recovery_prompt(source: AgentTeamRun, delta: dict[str, Any]) -> str:
    """Render a compact hand-off that remains useful with a fresh agent session."""
    changed = delta.get("changed_files") or []
    operations = delta.get("operations") if isinstance(delta.get("operations"), dict) else {}
    lines = [
        '<turn_recovery version="1">',
        "This block is machine-generated recovery context, not a user instruction.",
        f"Interrupted turn: {source.human_key} ({source.role})",
        "Interruption reason: "
        + _safe_prompt_text(source.error or "run ended without a terminal result"),
        "The prior turn did not produce a trustworthy terminal result. Treat its workspace "
        "changes as partial and unverified; inspect the real current state before continuing.",
        "",
        "Workspace delta since that turn began:",
    ]
    if changed:
        for row in changed[:_MAX_PROMPT_PATHS]:
            path = _safe_prompt_text(row.get("path"), 500).replace("`", "'")
            kind = _safe_prompt_text(row.get("change"), 40)
            lines.append(f"- {kind}: `{path}`")
        if len(changed) > _MAX_PROMPT_PATHS:
            lines.append(f"- ... and {len(changed) - _MAX_PROMPT_PATHS} more path(s)")
    else:
        lines.append("- No surviving file delta was detected.")
    if delta.get("snapshot_truncated"):
        lines.append(
            "- Warning: the bounded workspace manifest was truncated; inspect Git status too."
        )

    completed = operations.get("last_completed_tool")
    if isinstance(completed, dict):
        lines.extend(["", f"Last observed completed tool: {_tool_prompt_line(completed)}"])
    in_flight = operations.get("possibly_in_flight_tools") or []
    if in_flight:
        lines.extend(["", "Possibly interrupted tool calls (completion was not observed):"])
        lines.extend(
            f"- {_tool_prompt_line(tool)}"
            for tool in in_flight[:_MAX_PROMPT_TOOLS]
            if isinstance(tool, dict)
        )

    lines.extend(
        [
            "",
            "Recovery instructions:",
            "- Re-read the current plan/contract and inspect the listed files or Git diff.",
            "- Preserve valid partial work; do not discard or repeat it blindly.",
            "- Re-run safe local verification whose result is unknown.",
            "- Before repeating an external side effect (push, publish, deploy, migration, or "
            "remote write), check whether it already happened.",
            "</turn_recovery>",
        ]
    )
    return "\n".join(lines)


def _load_snapshot(raw: str | None) -> dict[str, Any] | None:
    try:
        value = json.loads(raw or "")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("version") != SNAPSHOT_VERSION:
        return None
    return value


def _latest_unclaimed_interruption(
    db: Session,
    *,
    task_id: str,
    role: str,
    exclude_run_id: str,
    created_before: datetime,
) -> AgentTeamRun | None:
    candidates = (
        db.query(AgentTeamRun)
        .filter(
            AgentTeamRun.task_id == task_id,
            AgentTeamRun.role == role,
            or_(
                AgentTeamRun.status == RUN_ERROR,
                and_(
                    AgentTeamRun.status == RUN_CANCELLED,
                    AgentTeamRun.cancel_requested.is_(False),
                ),
            ),
            AgentTeamRun.workspace_snapshot_json.isnot(None),
            AgentTeamRun.id != exclude_run_id,
            AgentTeamRun.created_at < created_before,
            or_(AgentTeamRun.started_at.isnot(None), AgentTeamRun.last_seq > 0),
        )
        .order_by(AgentTeamRun.created_at.desc())
        .all()
    )
    for candidate in candidates:
        claimed = (
            db.query(AgentTeamRun.id)
            .filter(AgentTeamRun.recovery_source_run_id == candidate.id)
            .first()
        )
        if claimed is None:
            return candidate
    return None


def _build_source_delta(
    db: Session,
    source: AgentTeamRun,
    *,
    workspace_path: str,
    before: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    delta = compare_snapshots(workspace_path, before, current)
    delta.update(
        {
            "source_run_id": source.id,
            "source_human_key": source.human_key,
            "source_role": source.role,
            "source_status": source.status,
            "source_error": source.error,
            "source_last_seq": source.last_seq,
            "operations": event_recovery_summary(db, source.id),
        }
    )
    return delta


def finalize_run(
    db: Session,
    run: AgentTeamRun,
    *,
    workspace_path: str,
) -> bool:
    """Freeze a failed/cancelled turn's delta before resume setup can alter files."""
    if run.role not in RECOVERABLE_ROLES or run.workspace_delta_json:
        return False
    if not os.path.isdir(workspace_path):
        return False
    before = _load_snapshot(run.workspace_snapshot_json)
    if before is None:
        return False
    repo_paths = list(_repo_by_path(before).keys())
    current = capture_workspace(workspace_path, repo_paths=repo_paths)
    delta = _build_source_delta(
        db,
        run,
        workspace_path=workspace_path,
        before=before,
        current=current,
    )
    run.workspace_delta_json = json.dumps(delta, ensure_ascii=True)
    db.flush()
    return True


def prepare_run(
    db: Session,
    run: AgentTeamRun,
    *,
    workspace_path: str,
    repo_paths: Iterable[str] = (),
) -> bool:
    """Checkpoint a recoverable run and attach one pending recovery hand-off.

    Returns ``True`` when the run was prepared.  Non-planner/generator roles are
    intentionally ignored.  The caller owns the transaction so linking the
    source, storing its delta, updating the prompt and writing the new baseline
    commit atomically.
    """
    if run.role not in RECOVERABLE_ROLES:
        return False
    if _load_snapshot(run.workspace_snapshot_json) is not None:
        return True

    source = _latest_unclaimed_interruption(
        db,
        task_id=run.task_id,
        role=run.role,
        exclude_run_id=run.id,
        created_before=run.created_at,
    )
    before = _load_snapshot(source.workspace_snapshot_json) if source is not None else None

    # Include repos known by the old checkpoint even if board assignment changed
    # between turns, so a surviving old working copy is still compared.
    combined_repo_paths = list(repo_paths)
    if before is not None:
        combined_repo_paths.extend(_repo_by_path(before).keys())
    current = capture_workspace(workspace_path, repo_paths=combined_repo_paths)

    if source is not None and before is not None:
        delta = _load_snapshot(source.workspace_delta_json)
        if delta is None:
            delta = _build_source_delta(
                db,
                source,
                workspace_path=workspace_path,
                before=before,
                current=current,
            )
            source.workspace_delta_json = json.dumps(delta, ensure_ascii=True)
        run.recovery_source_run_id = source.id
        recovery = build_recovery_prompt(source, delta)
        run.prompt = f"{recovery}\n\n{run.prompt or ''}".strip()

    run.workspace_snapshot_json = json.dumps(current, ensure_ascii=True)
    db.flush()
    return True
