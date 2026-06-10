"""Map a Jira issue payload onto agent_team task fields (Phase 1, pull only).

Pure mapping logic kept separate from the HTTP client and the router so it is
easy to unit-test. Mappings configured on the board take precedence; otherwise
we fall back to case-insensitive name matching, and leave a field unchanged
when nothing sensible matches (rather than guessing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_team.features.board.models import AgentTeamBoard

#: Task type vocabulary (mirror of the web UI's options).
_TASK_TYPES = {"task", "story", "bug", "epic", "subtask", "agent"}

#: Common Jira priority names → our 5-level scale. Covers the default scheme
#: (Highest…Lowest), the classic severity scheme (Blocker…Trivial), P1–P5 and
#: bare numeric schemes (1–5, as used by some custom priority sets).
_PRIORITY_ALIASES = {
    "highest": "highest", "blocker": "highest", "p1": "highest", "1": "highest",
    "high": "high", "critical": "high", "urgent": "high", "p2": "high", "2": "high",
    "medium": "medium", "major": "medium", "normal": "medium",
    "moderate": "medium", "p3": "medium", "3": "medium",
    "low": "low", "minor": "low", "p4": "low", "4": "low",
    "lowest": "lowest", "trivial": "lowest", "p5": "lowest", "5": "lowest",
}  # fmt: skip

#: Common Jira issue-type names → our type vocabulary. Keys are normalized
#: (lower-cased, spaces/hyphens stripped) before lookup.
_TYPE_ALIASES = {
    "task": "task",
    "story": "story", "userstory": "story",
    "feature": "story", "newfeature": "story", "improvement": "story",
    "enhancement": "story",
    "bug": "bug", "defect": "bug", "incident": "bug", "problem": "bug",
    "epic": "epic", "initiative": "epic",
    "subtask": "subtask",
    "spike": "task", "chore": "task",
}


def _norm(value: str) -> str:
    """Normalize a label for matching: lower-case, drop spaces/hyphens/underscores."""
    return value.strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def _lookup(mappings: dict, group: str, name: str) -> str | None:
    """Case-insensitive lookup in a configured mapping group."""
    table = mappings.get(group)
    if not isinstance(table, dict):
        return None
    for key, value in table.items():
        if key.strip().lower() == name.strip().lower() and isinstance(value, str):
            return value
    return None


def _match_column(board: AgentTeamBoard, name: str) -> str | None:
    """Find a board column whose name or key matches ``name`` (normalized)."""
    target = _norm(name)
    if not target:
        return None
    for col in board.columns():
        if _norm(col["name"]) == target or _norm(col["key"]) == target:
            return col["key"]
    return None


def build_task_changes(issue: dict, *, board: AgentTeamBoard) -> dict:
    """Return the task fields to update from a Jira issue.

    Only keys that should change are returned. ``status`` maps to a board
    column key; ``priority``/``task_type`` map to the local vocabularies. Title
    and labels always sync; description syncs (including clearing to empty).
    """
    fields = issue.get("fields") or {}
    mappings = board.jira_mappings()
    changes: dict = {}

    summary = (fields.get("summary") or "").strip()
    if summary:
        changes["title"] = summary

    # v2 returns description as plain text/wiki (or null). Always reflect it,
    # including an explicit clear to empty.
    changes["description"] = (fields.get("description") or "") or None

    status = fields.get("status") or {}
    status_name = (status.get("name") or "").strip()
    if status_name:
        # Prefer a configured/explicit status name match; otherwise fall back to
        # the (universal) status category so e.g. "Testing" → an In Progress
        # column even when no column is literally named "Testing".
        mapped = _lookup(mappings, "status", status_name) or _match_column(
            board, status_name
        )
        if mapped is None:
            category = ((status.get("statusCategory") or {}).get("name") or "").strip()
            if category:
                mapped = _lookup(mappings, "status", category) or _match_column(
                    board, category
                )
        if mapped:
            changes["status"] = mapped

    priority = fields.get("priority") or {}
    priority_name = (priority.get("name") or "").strip()
    if priority_name:
        mapped = _lookup(mappings, "priority", priority_name) or (
            _PRIORITY_ALIASES.get(priority_name.lower())
        )
        if mapped:
            changes["priority"] = mapped

    issuetype = fields.get("issuetype") or {}
    type_name = (issuetype.get("name") or "").strip()
    if type_name:
        normalized = _norm(type_name)
        mapped = (
            _lookup(mappings, "issuetype", type_name)
            or _TYPE_ALIASES.get(normalized)
            or (normalized if normalized in _TASK_TYPES else None)
        )
        if mapped:
            changes["task_type"] = mapped

    labels = fields.get("labels")
    if isinstance(labels, list):
        changes["labels"] = [str(x) for x in labels]

    return changes
