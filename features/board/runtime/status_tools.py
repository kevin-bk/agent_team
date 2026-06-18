"""Agent tool: let an agent move its own task between board columns.

An agent runs inside a per-task workspace; this tool resolves *which* task that
workspace belongs to (the same way the git/file tools do), validates the
requested column against the task's board, and updates the task status — so an
agent can mark its work ``review``/``done``/``blocked`` itself instead of
relying solely on the run's terminal status.

It is contributed via the plugin's ``tool_factories()`` so it only exists while
the ``agent_team`` plugin is enabled. The workspace root is bound at graph-build
time through the same context-local override the standard file tools honor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_root(agent_alias: str, settings: dict[str, str]) -> str | None:
    """Resolve the task workspace root (same resolver as the file/git tools)."""
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
        db.query(AgentTeamTask).filter(AgentTeamTask.workspace_path == root).first()
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


def _resolve_column(columns: list[dict], requested: str) -> dict | None:
    """Match a requested column by key or (case-insensitive) display name."""
    want = (requested or "").strip()
    if not want:
        return None
    lowered = want.lower()
    for col in columns:
        if col["key"] == want:
            return col
    for col in columns:
        if col["key"].lower() == lowered or col["name"].lower() == lowered:
            return col
    return None


def get_status_tools(agent_alias: str, settings: dict[str, str]) -> list[Any]:
    """Create the ``set_task_status`` tool for an agent (empty if langchain absent)."""
    try:
        from langchain_core.tools import tool
    except ImportError:
        return []

    root = _resolve_root(agent_alias, settings)

    @tool(parse_docstring=True)
    def set_task_status(status: str) -> str:
        """Move your current task to a different column on its board.

        Use this to reflect your progress — e.g. move the task to a review or
        done column when you finish, or a blocked column when you are stuck.
        Only columns that exist on the task's board are accepted.

        Args:
            status: The target column, given as its key (e.g. ``review``) or its
                display name (e.g. ``In Review``).
        """
        from agent_team.features.board.board_events import get_board_bus
        from agent_team.features.board.repositories import (
            activity as activity_repo,
        )
        from agent_team.features.board.repositories import boards as boards_repo
        from core.database.base import SessionLocal

        if not root:
            return "No workspace is configured for this agent."

        db = SessionLocal()
        try:
            task = _find_task(db, root)
            if task is None:
                return "Could not resolve the task for this workspace."
            board = boards_repo.get_board(db, task.board_id)
            if board is None:
                return "Could not resolve the board for this task."

            columns = board.columns()
            target = _resolve_column(columns, status)
            if target is None:
                names = ", ".join(f"{c['name']} ({c['key']})" for c in columns)
                return f"Unknown column '{status}'. Valid columns: {names}."

            if task.status == target["key"]:
                return f"Task is already in '{target['name']}'."

            previous = task.status
            task.status = target["key"]
            activity_repo.record(
                db,
                task_id=task.id,
                actor_id=None,
                kind=activity_repo.AGENT_STATUS_CHANGED,
                data={"from": previous, "to": target["key"], "agent": agent_alias},
            )
            db.commit()

            bus = get_board_bus()
            bus.publish(
                task.board_id,
                {
                    "type": "task.moved",
                    "board_id": task.board_id,
                    "task_id": task.id,
                    "status": target["key"],
                },
            )
            bus.publish(
                task.board_id,
                {
                    "type": "task.updated",
                    "board_id": task.board_id,
                    "task_id": task.id,
                },
            )
            return f"Moved task to '{target['name']}'."
        finally:
            db.close()

    return [set_task_status]
