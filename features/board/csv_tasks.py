"""CSV import/export of board tasks.

Pure, router-agnostic logic kept here so it is easy to unit-test:

* :func:`export_tasks_csv` renders a board's tasks as a CSV string.
* :func:`plan_import` parses an uploaded CSV into a list of :class:`RowPlan`
  (validate only, no DB writes) — the dry-run *preview*.
* :func:`apply_import` executes the non-error plans, creating or updating tasks.

Only one field is required on import: ``title``. Every other column is optional
and falls back to a sensible default (status → the board's first column, type →
``task``, etc.), with a human-readable warning attached to the row. Validation is
deliberately lenient: a bad optional value degrades to a default rather than
failing the whole row, so a partially-correct CSV still imports cleanly.

Identity for upsert is the per-board ``human_key``: a row whose ``human_key``
matches an existing task updates it; otherwise a new task is created (any
``human_key`` in the file is ignored on create — keys are system-managed).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from agent_team.features.board.jira.sync import (
    _PRIORITY_ALIASES,
    _TASK_TYPES,
    _match_column,
    _norm,
)
from agent_team.features.board.models import AgentTeamBoard, AgentTeamTask
from agent_team.features.board.repositories import tasks as tasks_repo

#: Canonical column order for export (and the names import recognises).
EXPORT_HEADERS = [
    "human_key",
    "title",
    "description",
    "task_type",
    "status",
    "priority",
    "labels",
    "assignee_email",
    "jira_key",
    "archived",
    "created_at",
    "updated_at",
]

#: Separator for the multi-value ``labels`` cell (``,`` is the CSV delimiter).
_LABEL_SEP = ";"

#: Upper bound on rows a single import accepts, to bound work per request.
MAX_IMPORT_ROWS = 2000

#: Truthy spellings accepted in the ``archived`` column.
_TRUE_WORDS = frozenset({"1", "true", "yes", "y", "t"})

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_ERROR = "error"


class CsvImportError(ValueError):
    """Raised when the CSV is structurally unusable (e.g. no ``title`` column)."""


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _emails_by_id(db: Session, user_ids: set[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    from core.database.models import User

    rows = db.query(User.id, User.email).filter(User.id.in_(user_ids)).all()
    return {uid: email for uid, email in rows if email}


def export_tasks_csv(
    db: Session, board: AgentTeamBoard, *, include_archived: bool
) -> str:
    """Render the board's tasks as a CSV string (header + one row per task)."""
    rows = tasks_repo.list_tasks(
        db, board_id=board.id, include_archived=include_archived
    )
    emails = _emails_by_id(db, {t.assignee_id for t in rows if t.assignee_id})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_HEADERS, extrasaction="ignore")
    writer.writeheader()
    for task in rows:
        writer.writerow(
            {
                "human_key": task.human_key,
                "title": task.title,
                "description": task.description or "",
                "task_type": task.task_type,
                "status": task.status,
                "priority": task.priority or "",
                "labels": _LABEL_SEP.join(task.labels()),
                "assignee_email": emails.get(task.assignee_id or "", ""),
                "jira_key": task.jira_key or "",
                "archived": "true" if task.archived else "false",
                "created_at": task.created_at.isoformat() if task.created_at else "",
                "updated_at": task.updated_at.isoformat() if task.updated_at else "",
            }
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@dataclass
class RowPlan:
    """One CSV row resolved against the board (preview = the plan, not the write)."""

    line: int
    action: str
    title: str = ""
    human_key: str | None = None
    warnings: list[str] = field(default_factory=list)
    #: Resolved fields applied on create/update (empty for an error row).
    values: dict = field(default_factory=dict)
    #: Existing task id when ``action == update``.
    task_id: str | None = None

    @property
    def message(self) -> str:
        return "; ".join(self.warnings)

    def as_dict(self) -> dict:
        return {
            "line": self.line,
            "action": self.action,
            "title": self.title,
            "human_key": self.human_key,
            "message": self.message,
        }


def _decode(data: bytes) -> str:
    """Decode upload bytes as UTF-8, tolerating a BOM (Excel) and CRLF."""
    return data.decode("utf-8-sig", errors="replace")


def _read_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """Return ``(header, data_rows)``; raises if there is no ``title`` column."""
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        raise CsvImportError("The CSV is empty.")
    header = [h.strip().lower() for h in rows[0]]
    if "title" not in header:
        raise CsvImportError("The CSV must have a 'title' column.")
    return header, rows[1:]


def _resolve_status(board: AgentTeamBoard, raw: str, warnings: list[str]) -> str:
    columns = board.columns()
    default = columns[0]["key"] if columns else "todo"
    if not raw:
        return default
    key = _match_column(board, raw)
    if key:
        return key
    warnings.append(f"unknown status '{raw}', using '{default}'")
    return default


def _resolve_priority(raw: str, warnings: list[str]) -> str | None:
    if not raw:
        return None
    mapped = _PRIORITY_ALIASES.get(raw.strip().lower())
    if mapped:
        return mapped
    warnings.append(f"unknown priority '{raw}', leaving empty")
    return None


def _resolve_type(raw: str, warnings: list[str]) -> str:
    if not raw:
        return "task"
    normalized = _norm(raw)
    if normalized in _TASK_TYPES:
        return normalized
    warnings.append(f"unknown task_type '{raw}', using 'task'")
    return "task"


def _resolve_labels(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(_LABEL_SEP) if part.strip()]


def _resolve_assignee(
    db: Session, raw: str, warnings: list[str]
) -> str | None:
    if not raw:
        return None
    from core.database.models import User

    user = db.query(User).filter(User.email == raw).first()
    if user is None:
        warnings.append(f"no user with email '{raw}', leaving unassigned")
        return None
    return user.id


def plan_import(
    db: Session, board: AgentTeamBoard, data: bytes
) -> list[RowPlan]:
    """Parse + validate an uploaded CSV into row plans (no DB writes).

    Raises :class:`CsvImportError` only for whole-file problems (empty file, no
    ``title`` column, too many rows). Per-row issues become an ``error`` plan or
    a warning on a create/update plan.
    """
    header, data_rows = _read_rows(_decode(data))
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise CsvImportError(
            f"Too many rows ({len(data_rows)}); the limit is {MAX_IMPORT_ROWS}."
        )
    index = {name: i for i, name in enumerate(header)}

    def cell(row: list[str], name: str) -> str:
        i = index.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    existing_by_key = {
        t.human_key: t
        for t in tasks_repo.list_tasks(db, board_id=board.id, include_archived=True)
    }

    plans: list[RowPlan] = []
    for offset, row in enumerate(data_rows):
        line = offset + 2  # +1 for the header, +1 for 1-based numbering
        title = cell(row, "title")
        if not title:
            plans.append(RowPlan(line=line, action=ACTION_ERROR, warnings=["missing title"]))
            continue

        warnings: list[str] = []
        values = {
            "title": title,
            "description": cell(row, "description") or None,
            "task_type": _resolve_type(cell(row, "task_type"), warnings),
            "status": _resolve_status(board, cell(row, "status"), warnings),
            "priority": _resolve_priority(cell(row, "priority"), warnings),
            "labels": _resolve_labels(cell(row, "labels")),
            "assignee_id": _resolve_assignee(db, cell(row, "assignee_email"), warnings),
        }
        archived_raw = cell(row, "archived")
        if archived_raw:
            values["archived"] = archived_raw.strip().lower() in _TRUE_WORDS

        key = cell(row, "human_key")
        match = existing_by_key.get(key) if key else None
        if match is not None:
            plans.append(
                RowPlan(
                    line=line,
                    action=ACTION_UPDATE,
                    title=title,
                    human_key=match.human_key,
                    warnings=warnings,
                    values=values,
                    task_id=match.id,
                )
            )
        else:
            if key:
                warnings.append(f"no task '{key}' on this board, creating a new one")
            plans.append(
                RowPlan(
                    line=line,
                    action=ACTION_CREATE,
                    title=title,
                    human_key=None,
                    warnings=warnings,
                    values=values,
                )
            )
    return plans


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _apply_update(task: AgentTeamTask, values: dict) -> None:
    import json

    task.title = values["title"]
    task.description = values["description"]
    task.task_type = values["task_type"]
    task.status = values["status"]
    task.priority = values["priority"]
    task.labels_json = json.dumps(list(values["labels"]))
    task.assignee_id = values["assignee_id"]
    if "archived" in values:
        task.archived = bool(values["archived"])


def apply_import(
    db: Session,
    board: AgentTeamBoard,
    plans: list[RowPlan],
    *,
    actor_id: str | None,
) -> tuple[ImportResult, bool]:
    """Execute create/update plans; returns ``(result, any_changes)``.

    Each created/updated task is recorded in the activity log. The caller owns
    the commit and the board-bus broadcast (``any_changes`` tells it whether a
    refresh is worth publishing).
    """
    from agent_team.features.board.repositories import activity as activity_repo

    result = ImportResult()
    for plan in plans:
        if plan.action == ACTION_ERROR:
            result.skipped += 1
            result.errors.append(f"row {plan.line}: {plan.message or 'invalid row'}")
            continue
        try:
            if plan.action == ACTION_UPDATE and plan.task_id:
                task = tasks_repo.get_task(db, plan.task_id)
                if task is None:
                    result.skipped += 1
                    result.errors.append(f"row {plan.line}: task vanished")
                    continue
                _apply_update(task, plan.values)
                db.flush()
                activity_repo.record(
                    db,
                    task_id=task.id,
                    actor_id=actor_id,
                    kind=activity_repo.TASK_UPDATED,
                    data={"source": "csv_import"},
                )
                result.updated += 1
            else:
                task = tasks_repo.create_task(
                    db,
                    board_id=board.id,
                    title=plan.values["title"],
                    description=plan.values["description"],
                    status=plan.values["status"],
                    assignee_id=plan.values["assignee_id"],
                    labels=plan.values["labels"],
                    priority=plan.values["priority"],
                    task_type=plan.values["task_type"],
                    created_by=actor_id,
                )
                if plan.values.get("archived"):
                    task.archived = True
                    db.flush()
                activity_repo.record(
                    db,
                    task_id=task.id,
                    actor_id=actor_id,
                    kind=activity_repo.TASK_CREATED,
                    data={"title": task.title, "status": task.status, "source": "csv_import"},
                )
                result.created += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
            result.skipped += 1
            result.errors.append(f"row {plan.line}: {exc}")

    any_changes = result.created > 0 or result.updated > 0
    return result, any_changes
