"""Agent tool: push a board repo's task working copy to its real remote.

An agent works in a per-task copy of each board repo (``<workspace>/<slug>``)
created with ``git clone --local``; that copy's ``origin`` points at the local
canonical clone and holds **no credentials**. So the agent can ``git commit``
freely but cannot reach the real remote on its own. ``git_push`` closes that gap
*through the trusted backend*: it resolves the repo from the task, checks the
admin's per-repo ``allow_push`` policy, and pushes using the stored credential —
which the agent never sees.

Contributed via the plugin's ``tool_factories()`` so it only exists while the
``agent_team`` plugin is enabled. The workspace root is bound at graph-build time
through the same context-local override the standard file tools honor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_root(agent_alias: str, settings: dict[str, str]) -> str | None:
    """Resolve the task workspace root (same resolver as the file tools)."""
    try:
        from plugins.standard_tools.tools.file_tools import _resolve_work_dir

        return (_resolve_work_dir(agent_alias, settings) or "").strip() or None
    except ImportError:
        try:
            from plugins.standard_tools.tools.workspace_override import (
                get_workspace_override,
            )

            return (get_workspace_override() or "").strip() or None
        except ImportError:
            return None


def _find_task(db, root: str):
    from agent_team.features.board.models import AgentTeamTask

    task = (
        db.query(AgentTeamTask)
        .filter(AgentTeamTask.workspace_path == root)
        .first()
    )
    if task is not None:
        return task
    # Fall back to a resolved-path comparison (override vs stored may differ by
    # symlink/normalisation).
    target = Path(root).resolve()
    for cand in db.query(AgentTeamTask).filter(AgentTeamTask.archived.is_(False)).all():
        if cand.workspace_path and Path(cand.workspace_path).resolve() == target:
            return cand
    return None


def get_git_tools(agent_alias: str, settings: dict[str, str]) -> list[Any]:
    """Create the ``git_push`` tool for an agent (empty if langchain absent)."""
    try:
        from langchain_core.tools import tool
    except ImportError:
        return []

    root = _resolve_root(agent_alias, settings)

    @tool(parse_docstring=True)
    def git_push(repo: str, message: str | None = None, branch: str | None = None) -> str:
        """Push your committed changes in a board repository to its remote.

        Publishes work you did inside a repo folder in your workspace. The push
        uses the repository's stored credentials, managed by an admin — you never
        need a token or key. Pushing only works for repos an admin has enabled,
        and only onto your task branch (never the repo's default branch).

        Args:
            repo: The repo folder name in your workspace, e.g. ``web-ui``.
            message: Optional. If given, stage all changes and commit with this
                message before pushing. Omit if you already committed.
            branch: Optional target branch. Defaults to your task branch.
        """
        from agent_team.features.repos import git_service
        from agent_team.features.repos.repositories import repos_for_board
        from agent_team.features.repos.task_copy import _run_git, task_branch_name

        if not root:
            return "No workspace is configured for this agent."
        slug = (repo or "").strip().strip("/")
        if not slug or "/" in slug or "\\" in slug or ".." in slug:
            return f"Invalid repo name: {repo!r}"
        dest = (Path(root) / slug).resolve()
        base = Path(root).resolve()
        if base not in dest.parents:
            return f"Repo path is outside the workspace: {repo}"
        if not (dest / ".git").is_dir():
            return (
                f"Repo '{slug}' is not prepared in this task yet. "
                "Ask to prepare the workspace or check the repo name."
            )

        from core.database.base import SessionLocal

        db = SessionLocal()
        try:
            task = _find_task(db, root)
            if task is None:
                return "Could not resolve the task for this workspace."
            match = next(
                (
                    (r, br, allow)
                    for r, br, allow in repos_for_board(db, task.board_id)
                    if r.slug == slug
                ),
                None,
            )
            if match is None:
                return f"Repo '{slug}' is not assigned to this board."
            repo_obj, branch_override, board_allow_push = match
            if not repo_obj.allow_push:
                return (
                    f"Pushing is disabled for '{slug}'. An admin must enable "
                    "push for this repository."
                )
            if not board_allow_push:
                return (
                    f"Pushing '{slug}' is not enabled for this board. A board "
                    "owner/editor must turn it on for this board."
                )

            target = (branch or "").strip() or task_branch_name(task)
            tracked = (branch_override or repo_obj.default_branch or "").strip()
            if tracked and target == tracked:
                return (
                    f"Refusing to push to the tracked branch '{tracked}'. "
                    "Push to your task branch instead."
                )

            if message and message.strip():
                _run_git("-C", str(dest), "add", "-A")
                ccode, _cout, cerr = _run_git(
                    "-C", str(dest), "commit", "-m", message.strip()
                )
                if ccode != 0 and "nothing to commit" not in (cerr or "").lower():
                    return f"Commit failed: {cerr[:500]}"

            result = git_service.push_branch(repo_obj, str(dest), target)
            if result.ok:
                return f"Pushed '{slug}' to '{target}'. {result.message}".strip()
            return f"Push failed for '{slug}': {result.message}"
        finally:
            db.close()

    return [git_push]
