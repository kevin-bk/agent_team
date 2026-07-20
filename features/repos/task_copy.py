"""Per-task working copies of a board's assigned repos.

When an agent works a task it gets its own copy of each repo assigned to the
board, created with ``git clone --local <canonical> <task_dir>/<slug>``. Because
the source is a local path on the same filesystem, git **hardlinks** the object
store, so the only real disk cost is the checked-out working tree — and the copy
is fully independent (own branches/commits) without touching the canonical or
other tasks. No credentials are involved (the source is local).
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamTask
from agent_team.features.repos.models import AUTH_SSH, AUTH_TOKEN, AgentTeamRepo
from agent_team.features.repos.paths import canonical_path, task_copy_path
from agent_team.features.repos.repositories import repos_for_board

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 300.0

#: Legacy remote name from an earlier two-remote layout (origin=local mirror +
#: host=real remote). We now point ``origin`` straight at the real host, so this
#: is only removed for cleanup on copies created by the old code.
_LEGACY_HOST_REMOTE = "host"

#: Fallback commit identity when a repo configures none.
_DEFAULT_COMMITTER_NAME = "Agent Team"
_DEFAULT_COMMITTER_EMAIL = "agent-team@local"


def _deny_read_patterns(db: Session, board_id: str) -> list[str]:
    """Return the board's immutable deny-read patterns, if policy is bound."""
    from agent_team.features.board.runtime.project_policy import bound_bundle

    bundle = bound_bundle(db, board_id, required=False)
    if bundle is None:
        return []
    return list(bundle.documents()["paths.yaml"]["deny_read"])


def _sanitize_deny_read_paths(repo_dir: Path, patterns: list[str]) -> list[str]:
    """Remove untracked denied paths; fail closed on tracked denied paths.

    Canonical clones remain complete and host-only. In the agent-visible task
    copy, untracked denied paths are simply removed (symlinks unlinked, never
    followed). A *tracked* denied path cannot be made unreadable this way: its
    blob stays in the copy's ``.git`` object database, so an agent can still
    read it via ``git show HEAD:<path>``, ``git cat-file`` or ``git archive``
    even after the worktree file is gone. Rather than pretend such a secret is
    protected, preparation fails closed — the operator must untrack and rotate
    the secret, or add an explicit policy exception (remove the exact path from
    ``deny_read``).
    """
    if not patterns or not (repo_dir / ".git").exists():
        return []

    code, tracked_raw, err = _run_git("-C", str(repo_dir), "ls-files", "-z")
    if code != 0:
        raise RuntimeError(f"cannot enumerate tracked paths for sanitization: {err}")
    tracked = {path for path in tracked_raw.split("\0") if path}

    denied: set[str] = set()
    denied_dirs: set[str] = set()
    for current, dirs, files in os.walk(repo_dir, topdown=True, followlinks=False):
        if current == str(repo_dir):
            dirs[:] = [name for name in dirs if name != ".git"]
        kept_dirs: list[str] = []
        for name in dirs:
            absolute = os.path.join(current, name)
            relative = os.path.relpath(absolute, repo_dir).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                denied.add(relative)
                denied_dirs.add(relative)
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            absolute = os.path.join(current, name)
            relative = os.path.relpath(absolute, repo_dir).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                denied.add(relative)

    tracked_denied = sorted(
        path
        for path in tracked
        if path in denied
        or any(path.startswith(f"{directory}/") for directory in denied_dirs)
    )
    if tracked_denied:
        raise RuntimeError(
            "deny_read matches tracked file(s) whose content remains readable "
            "from the task copy's .git history (git show/cat-file/archive) even "
            "after worktree removal: "
            + ", ".join(tracked_denied[:20])
            + ". Untrack and rotate the secret, or add an explicit policy "
            "exception by removing the exact path from deny_read."
        )

    for relative in sorted(denied, key=lambda path: (path.count("/"), path), reverse=True):
        target = repo_dir / relative
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
    return sorted(denied)


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


def _ensure_work_branch(dest: str, work_branch: str) -> None:
    """Make sure the working copy sits on its per-task branch.

    Runs on every prepare (not just first clone) so copies created before this
    logic existed — or left on the default branch — are switched onto the task
    branch. An existing task branch is *checked out* (its history is preserved);
    only a missing one is created from the current HEAD.
    """
    code, current, _ = _run_git("-C", dest, "rev-parse", "--abbrev-ref", "HEAD")
    if code == 0 and current == work_branch:
        return
    exists, _, _ = _run_git(
        "-C", dest, "rev-parse", "--verify", "--quiet", f"refs/heads/{work_branch}"
    )
    if exists == 0:
        _run_git("-C", dest, "checkout", work_branch)
    else:
        _run_git("-C", dest, "checkout", "-B", work_branch)


#: Sandbox-portable token credential files.  They live under ``.git`` so they
#: are never committed; unlike the legacy DB-backed helper they use no host-only
#: Python path and therefore also work when the workspace is mounted at
#: ``/workspace`` inside OpenSandbox.
_TOKEN_CRED_FILE = "at_git_credential"
_TOKEN_HELPER_FILE = "at_git_cred_helper.sh"
_LEGACY_CRED_FILE = "at_cred.json"


def _token_helper_body() -> str:
    """Return a tiny Git credential helper that reads its sibling 0600 file."""
    return (
        "#!/bin/sh\n"
        "[ \"$1\" = get ] || exit 0\n"
        "helper_dir=$(dirname \"$0\")\n"
        f"cat \"$helper_dir/{_TOKEN_CRED_FILE}\"\n"
    )


def _pre_push_hook_body(protected: list[str]) -> str:
    """A `pre-push` hook that refuses pushes to protected (default) branches.

    Runs for **every** ``git push`` (direct-CLI agent or LLM), so the task-branch
    -only rule is enforced locally regardless of who pushes — the local half of
    the human merge gate. The protected list always includes the common defaults
    as a safety net even when the tracked branch is unknown.
    """
    refs = " ".join(f"refs/heads/{b}" for b in dict.fromkeys([*protected, "main", "master"]) if b)
    return (
        "#!/bin/sh\n"
        "# Managed by agent_team: agents push their task branch, never the default.\n"
        f'protected="{refs}"\n'
        "while read -r local_ref local_sha remote_ref remote_sha; do\n"
        '  for p in $protected; do\n'
        '    if [ "$remote_ref" = "$p" ]; then\n'
        '      echo "agent-team: refusing to push to protected branch '
        '${remote_ref#refs/heads/}; push your task branch instead." >&2\n'
        "      exit 1\n"
        "    fi\n"
        "  done\n"
        "done\n"
        "exit 0\n"
    )


def _install_pre_push_hook(dest: Path, protected: list[str]) -> None:
    hooks_dir = dest / ".git" / "hooks"
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-push"
        hook.write_text(_pre_push_hook_body(protected), encoding="utf-8")
        hook.chmod(0o755)
    except OSError:
        logger.warning("task: failed to install pre-push hook in %s", dest, exc_info=True)


def _configure_push_to_host(
    dest: Path,
    repo: AgentTeamRepo,
    task: AgentTeamTask,
    *,
    can_push: bool,
) -> None:
    """Point the copy's single ``origin`` remote straight at the real host.

    The copy is created with ``git clone --local`` (cheap, hardlinked objects);
    we then repoint ``origin`` to the real remote URL so there is **one** remote
    and a plain ``git push`` reaches the host (not a local mirror). For token auth
    a sandbox-portable credential helper + 0600 credential file are materialised
    in ``.git``.  This intentionally trades secret isolation for compatibility
    with OpenSandbox and is suitable for the demo runtime; Credential Vault is
    the hardened follow-up. For SSH a key file is materialised in ``.git`` (kept
    out of the work tree). Best-effort: failures only mean push falls back to the
    (gated) ``git_push`` tool.
    """
    from agent_team.features.repos import git_service

    dest_s = str(dest)
    try:
        url = git_service._effective_url(repo)
        if not url:
            return
        # One remote only: origin → real host. ``set-url`` if origin exists (it
        # does after ``clone``), else add it.
        code, _out, _err = _run_git("-C", dest_s, "remote", "set-url", "origin", url)
        if code != 0:
            _run_git("-C", dest_s, "remote", "add", "origin", url)
        # Tidy up the legacy two-remote layout (origin=local mirror + host).
        _run_git("-C", dest_s, "remote", "remove", _LEGACY_HOST_REMOTE)
        _run_git("-C", dest_s, "config", "--unset", "remote.pushDefault")
        _run_git("-C", dest_s, "config", "push.autoSetupRemote", "true")

        git_dir = dest / ".git"
        if repo.auth_type == AUTH_TOKEN:
            cred_file = git_dir / _TOKEN_CRED_FILE
            helper_file = git_dir / _TOKEN_HELPER_FILE
            # Clean copies prepared by the old host-only Python helper.
            (git_dir / _LEGACY_CRED_FILE).unlink(missing_ok=True)
            # Make our helper authoritative. Git accumulates credential helpers
            # across system/global/local config and queries them in order, so an
            # inherited helper (e.g. macOS ``osxkeychain``) can answer *first* with
            # a stale/read-only token and shadow ours — that surfaces as a push
            # 403 even though our DB token has write access. Resetting the list
            # with an empty value discards inherited helpers for this copy, then we
            # add ours as the only one.
            _run_git("-C", dest_s, "config", "--unset-all", "credential.helper")
            _run_git("-C", dest_s, "config", "credential.helper", "")
            secret = (repo.auth_secret or "").strip()
            user = (repo.auth_username or "").strip() or "x-access-token"
            if can_push and secret and not any(c in user + secret for c in "\r\n"):
                cred_file.write_text(
                    f"username={user}\npassword={secret}\n", encoding="utf-8"
                )
                cred_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
                helper_file.write_text(_token_helper_body(), encoding="utf-8")
                helper_file.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                # Git runs shell helpers from the repository root, so this path
                # stays valid on the host and at /workspace inside OpenSandbox.
                _run_git(
                    "-C", dest_s, "config", "--add", "credential.helper",
                    f"!./.git/{_TOKEN_HELPER_FILE}",
                )
            else:
                cred_file.unlink(missing_ok=True)
                helper_file.unlink(missing_ok=True)
                if can_push and secret:
                    logger.warning(
                        "task %s: refusing token credential with newline for %s",
                        task.human_key,
                        repo.slug,
                    )
        elif repo.auth_type == AUTH_SSH and can_push and (repo.auth_secret or "").strip():
            # SSH cannot use a credential helper; the key must be a file. Keep it
            # inside .git (never committed) with 0600 perms.
            key = git_dir / "at_ssh_key"
            secret = repo.auth_secret.strip()
            key.write_text(secret if secret.endswith("\n") else secret + "\n", encoding="utf-8")
            key.chmod(stat.S_IRUSR | stat.S_IWUSR)
            _run_git(
                "-C", dest_s, "config", "core.sshCommand",
                f"ssh -i {shlex.quote(str(key))} "
                "-o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes",
            )
    except OSError:
        logger.warning("task %s: configure push-to-host for %s failed",
                       task.human_key, repo.slug, exc_info=True)


def prepare_task_repos(db: Session, task: AgentTeamTask) -> list[dict]:
    """Ensure each assigned, cloned repo has a working copy in the task folder.

    Returns a list of ``{slug, path, branch}`` for the copies that exist after
    this call (workspace-relative ``path``). Repos whose canonical clone is not
    ready yet are skipped (the scheduler/owner must clone them first).
    """
    prepared: list[dict] = []
    work_branch = task_branch_name(task)
    deny_read = _deny_read_patterns(db, task.board_id)
    for repo, branch_override, bp_allow_push, is_wiki in repos_for_board(db, task.board_id):
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
        # Ensure the per-task working branch on every run (not only on first
        # clone) so the agent never commits straight onto the tracked branch and
        # pre-existing copies stuck on the default branch get switched over.
        _ensure_work_branch(str(dest), work_branch)
        _configure_copy(str(dest), repo)
        # Wire a plain ``git push`` to reach the real host (gated), and block the
        # default branch locally. Runs every prepare so it self-heals/idempotent.
        can_push = bool(repo.allow_push and bp_allow_push)
        _configure_push_to_host(dest, repo, task, can_push=can_push)
        _install_pre_push_hook(dest, [base_branch] if base_branch else [])
        sanitized = _sanitize_deny_read_paths(dest, deny_read)
        if sanitized:
            logger.info(
                "task %s: sanitized %d deny-read path(s) from %s: %s",
                task.human_key,
                len(sanitized),
                repo.slug,
                ", ".join(sanitized[:20]),
            )
        prepared.append(
            {
                "slug": repo.slug,
                "path": repo.slug,
                "branch": work_branch,
                "base_branch": base_branch or None,
                # The runtime executes this before the first agent turn. Keep
                # only the boolean in prompt metadata so a long shell command
                # does not waste model context on every task.
                "bootstrap_configured": bool(
                    (getattr(repo, "bootstrap_command", None) or "").strip()
                ),
                # Effective push = admin master gate AND this board's opt-in.
                "can_push": can_push,
                # Marks the board's knowledge base so the run can advertise it.
                "is_wiki": bool(is_wiki),
            }
        )
    return prepared


def cleanup_task_repos(db: Session, task: AgentTeamTask) -> int:
    """Remove per-repo working copies from a task folder. Returns count removed."""
    removed = 0
    for repo, _branch, _allow, _is_wiki in repos_for_board(db, task.board_id):
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


def reset_task_repos_by_id(task_id: str, *, pull_canonical: bool = True) -> list[dict]:
    """Re-create a task's repo working copies from scratch (opens its own session).

    Pulls each board repo's **canonical** clone first (so the fresh copy picks up
    the latest default-branch commits), removes the existing per-task copies, then
    re-clones them via :func:`prepare_task_repos`. **Destructive**: any work in the
    old copy that was not pushed (the `agent/<task-key>` branch, uncommitted
    changes) is discarded. Canonical pulls are best-effort — a failed pull just
    means the re-clone uses the canonical as-is.
    """
    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).first()
        if task is None:
            return []
        if pull_canonical:
            from agent_team.features.repos import git_service

            for repo, _branch, _allow, _is_wiki in repos_for_board(db, task.board_id):
                try:
                    git_service.sync_repo_by_id(repo.id)
                except Exception:  # noqa: BLE001 — pull is best-effort
                    logger.warning(
                        "task %s: canonical pull of %s before reset failed",
                        task.human_key,
                        repo.slug,
                        exc_info=True,
                    )
        cleanup_task_repos(db, task)
        return prepare_task_repos(db, task)
    finally:
        db.close()


def list_task_repo_dirs(db: Session, task: AgentTeamTask) -> list[dict]:
    """Return ``{slug, path, present}`` for assigned repos (for the cockpit)."""
    out: list[dict] = []
    for repo, _branch, _allow, is_wiki in repos_for_board(db, task.board_id):
        dest = task_copy_path(task.workspace_path, repo.slug)
        out.append(
            {
                "slug": repo.slug,
                "path": repo.slug,
                "present": (dest / ".git").exists(),
                "is_wiki": bool(is_wiki),
            }
        )
    return out
