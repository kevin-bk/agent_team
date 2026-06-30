"""Git change detection + per-file diffs for a task's repo working copies.

A task copy lives at ``<task_workspace>/<slug>`` on branch ``agent/<task-key>``
(see :mod:`agent_team.features.repos.task_copy`). To show "what this task
changed", we compare the working tree against the **merge-base** with the repo's
base branch. ``git diff <merge_base>`` compares that commit's tree to the
*working tree*, so the result captures, in one shot:

- commits the agent made on its task branch,
- staged *and* unstaged uncommitted edits,
- (separately listed) brand-new untracked files,

regardless of which run/agent produced them (LLM tool calls, a direct-CLI agent
pushing with plain git, or many autonomous-loop attempts). That is the whole
point: the cockpit's "Changes" view reflects the **on-disk truth**, not a single
conversation's streamed tool calls.

Everything here is pure git over ``subprocess`` and is meant to run in a worker
thread (``asyncio.to_thread``). The only DB touch is :func:`repo_specs`, which
the caller runs on the request's session *before* handing the plain specs to the
thread (a SQLAlchemy ``Session`` must not be shared across threads).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamTask
from agent_team.features.repos.paths import task_copy_path
from agent_team.features.repos.repositories import repos_for_board
from agent_team.features.repos.task_copy import task_branch_name

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 60.0

#: Hard caps so a runaway changeset / huge file can't blow up the response.
MAX_FILES = 400
MAX_DIFF_BYTES = 2_000_000

#: Base-ref fallbacks tried (in order) when the assignment names no branch.
_DEFAULT_BASE_FALLBACKS = ("origin/HEAD", "main", "master")


# ── git plumbing ───────────────────────────────────────────────────────────


def _run_bytes(
    dest: Path, args: list[str], *, timeout: float = _GIT_TIMEOUT
) -> tuple[int, bytes, bytes]:
    """Run ``git -C <dest> <args>`` and return ``(code, stdout, stderr)`` bytes."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LANG": "C"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(dest), *args],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("git %s failed in %s", args[:1], dest, exc_info=True)
        return 1, b"", b""
    return proc.returncode, proc.stdout, proc.stderr


def _run(dest: Path, args: list[str], *, timeout: float = _GIT_TIMEOUT) -> tuple[int, str, str]:
    """Text variant of :func:`_run_bytes` (lossy utf-8 for porcelain output)."""
    code, out, err = _run_bytes(dest, args, timeout=timeout)
    return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _resolve_base_ref(dest: Path, base_branch: str | None) -> str | None:
    """First ref that exists in the copy: the assignment's branch, then defaults."""
    candidates: list[str] = []
    if base_branch:
        candidates.append(base_branch.strip())
    candidates.extend(_DEFAULT_BASE_FALLBACKS)
    for ref in candidates:
        if not ref:
            continue
        code, out, _ = _run(dest, ["rev-parse", "--verify", "--quiet", ref])
        if code == 0 and out.strip():
            return ref
    return None


def _diff_base(dest: Path, base_branch: str | None) -> str:
    """The commit the agent branch diverged from (merge-base), with fallbacks.

    Falls back to the base ref itself if there's no common ancestor, and finally
    to ``HEAD`` (which surfaces only the uncommitted working-tree edits) so the
    view degrades gracefully instead of erroring.
    """
    base_ref = _resolve_base_ref(dest, base_branch)
    if base_ref:
        code, out, _ = _run(dest, ["merge-base", base_ref, "HEAD"])
        if code == 0 and out.strip():
            return out.strip()
        return base_ref
    return "HEAD"


def _numstat_path(rest: str) -> str:
    """Extract the *new* path from a numstat tail, handling git's rename forms.

    Examples: ``"src/a.py"`` → ``src/a.py``; ``"old.py => new.py"`` → ``new.py``;
    ``"src/{old => new}/x.py"`` → ``src/new/x.py``.
    """
    rest = rest.strip()
    if "=>" not in rest:
        return rest
    if "{" in rest and "}" in rest:
        pre = rest[: rest.index("{")]
        mid = rest[rest.index("{") + 1 : rest.index("}")]
        post = rest[rest.index("}") + 1 :]
        new = mid.split("=>")[-1].strip()
        return (pre + new + post).replace("//", "/")
    return rest.split("=>")[-1].strip()


def _is_binary_bytes(data: bytes | None) -> bool:
    if data is None:
        return False
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _decode(data: bytes | None) -> tuple[str, bool]:
    """Return ``(text, is_binary)``; missing side → empty text, not binary."""
    if data is None:
        return "", False
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return "", True


def _changed_files(dest: Path, diff_base: str) -> list[tuple[str, dict]]:
    """Per-file change records for ``diff <diff_base>`` + untracked files.

    Status uses git's letters: ``A`` added, ``M`` modified, ``D`` deleted,
    ``R`` renamed, ``U`` untracked. Binary files report ``additions == 0`` and
    ``deletions == 0`` with ``binary == True``.
    """
    records: dict[str, dict] = {}
    order: list[str] = []

    code, out, _ = _run(
        dest, ["diff", "--name-status", "-M", "--find-renames", diff_base]
    )
    if code == 0:
        for line in out.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            letter = parts[0][:1]
            if letter == "R" and len(parts) >= 3:
                new = parts[2]
                records[new] = {
                    "status": "R",
                    "old_path": parts[1],
                    "additions": 0,
                    "deletions": 0,
                    "binary": False,
                }
                order.append(new)
            elif len(parts) >= 2:
                path = parts[-1]
                # Treat copies (C) as additions for review purposes.
                status = letter if letter in ("A", "M", "D") else (
                    "A" if letter == "C" else "M"
                )
                records[path] = {
                    "status": status,
                    "additions": 0,
                    "deletions": 0,
                    "binary": False,
                }
                order.append(path)

    code, out, _ = _run(dest, ["diff", "--numstat", "-M", "--find-renames", diff_base])
    if code == 0:
        for line in out.split("\n"):
            if not line.strip():
                continue
            cols = line.split("\t", 2)
            if len(cols) < 3:
                continue
            added, deleted, rest = cols
            path = _numstat_path(rest)
            rec = records.get(path)
            if rec is None:
                rec = {"status": "M", "additions": 0, "deletions": 0, "binary": False}
                records[path] = rec
                order.append(path)
            if added == "-" or deleted == "-":
                rec["binary"] = True
            else:
                rec["additions"] = int(added)
                rec["deletions"] = int(deleted)

    code, out, _ = _run(dest, ["ls-files", "--others", "--exclude-standard"])
    if code == 0:
        for line in out.split("\n"):
            path = line.strip()
            if not path or path in records:
                continue
            try:
                data = (dest / path).read_bytes()
            except OSError:
                data = None
            records[path] = {
                "status": "U",
                "additions": 0,
                "deletions": 0,
                "binary": _is_binary_bytes(data),
            }
            order.append(path)

    return [(p, records[p]) for p in order]


def _safe_target(dest: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` inside the repo copy, rejecting traversal escapes."""
    base = dest.resolve()
    target = (base / rel_path).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes repo copy: {rel_path!r}")
    return target


# ── public API ──────────────────────────────────────────────────────────────


def repo_specs(db: Session, task: AgentTeamTask) -> list[dict]:
    """Plain ``{slug, base_branch}`` specs for the task's assigned repos.

    Runs on the caller's DB session (cheap query); the resulting list carries no
    ORM objects so it is safe to hand to a worker thread.
    """
    specs: list[dict] = []
    for repo, branch_override, _allow, _is_wiki in repos_for_board(db, task.board_id):
        specs.append(
            {
                "slug": repo.slug,
                "base_branch": (branch_override or repo.default_branch or None),
            }
        )
    return specs


def compute_changes(
    workspace_path: str, work_branch: str, specs: list[dict]
) -> dict:
    """Aggregate per-repo changesets for a task. Pure git; run in a thread."""
    repos: list[dict] = []
    files: list[dict] = []
    truncated = False
    for spec in specs:
        slug = spec["slug"]
        base_branch = spec.get("base_branch")
        dest = task_copy_path(workspace_path, slug)
        present = (dest / ".git").exists()
        repos.append(
            {
                "slug": slug,
                "base_branch": base_branch,
                "branch": work_branch,
                "present": present,
            }
        )
        if not present:
            continue
        diff_base = _diff_base(dest, base_branch)
        for path, info in _changed_files(dest, diff_base):
            if len(files) >= MAX_FILES:
                truncated = True
                break
            files.append({"repo": slug, "path": path, **info})
        if truncated:
            break
    return {"repos": repos, "files": files, "truncated": truncated}


def compute_file_diff(workspace_path: str, spec: dict, rel_path: str) -> dict:
    """``{original, modified, status, binary, truncated}`` for one file.

    ``original`` is the file's content at the diff base (empty for added/
    untracked), ``modified`` is the current working-tree content (empty for
    deleted). Binary files return empty text with ``binary == True``.
    """
    slug = spec["slug"]
    base_branch = spec.get("base_branch")
    dest = task_copy_path(workspace_path, slug)
    target = _safe_target(dest, rel_path)
    diff_base = _diff_base(dest, base_branch)

    code, original_b, _ = _run_bytes(dest, ["show", f"{diff_base}:{rel_path}"])
    if code != 0:
        original_b = None  # not present at base → added/untracked
    modified_b: bytes | None = None
    if target.is_file():
        try:
            modified_b = target.read_bytes()
        except OSError:
            modified_b = None

    if original_b is not None and modified_b is None:
        status = "D"
    elif original_b is None and modified_b is not None:
        status = "A"
    else:
        status = "M"

    original, o_binary = _decode(original_b)
    modified, m_binary = _decode(modified_b)
    binary = o_binary or m_binary
    if binary:
        return {
            "repo": slug,
            "path": rel_path,
            "original": "",
            "modified": "",
            "status": status,
            "binary": True,
            "truncated": False,
        }

    truncated = (
        len(original.encode("utf-8")) > MAX_DIFF_BYTES
        or len(modified.encode("utf-8")) > MAX_DIFF_BYTES
    )
    return {
        "repo": slug,
        "path": rel_path,
        "original": original,
        "modified": modified,
        "status": status,
        "binary": False,
        "truncated": truncated,
    }
