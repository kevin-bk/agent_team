"""REST API for the board feature (boards and tasks)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from agent_team.features.board import attachments
from agent_team.features.board import workspace as ws_module
from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.jira import service as jira_service
from agent_team.features.board.jira.client import JiraError
from agent_team.features.board.jira.sync import build_task_changes
from agent_team.features.board.repositories import activity as activity_repo
from agent_team.features.board.repositories import autopilot as autopilot_repo
from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import comments as comments_repo
from agent_team.features.board.repositories import conversations as conversations_repo
from agent_team.features.board.repositories import members as members_repo
from agent_team.features.board.repositories import messages as messages_repo
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.repositories import task_schedule as schedule_repo
from agent_team.features.board.repositories import tasks as tasks_repo
from agent_team.features.board.repositories import tool_outputs as tool_outputs_repo
from agent_team.features.board.runtime import event_store, run_service
from agent_team.features.board.runtime.backend import get_run_backend
from agent_team.features.board.runtime.events import TERMINAL_RUN_STATUSES
from agent_team.features.board.schemas import (
    AddMemberBody,
    AutopilotRecentItem,
    AutopilotSummaryDTO,
    AutopilotUpdate,
    BoardCreate,
    BoardUpdate,
    CommentCreate,
    CommentUpdate,
    CsvImportPreview,
    CsvImportResult,
    CsvImportRow,
    JiraImportBody,
    JiraPreviewItem,
    JiraPreviewResponse,
    JiraSyncBody,
    MentionCreate,
    MentionResponse,
    SkillPackDTO,
    TaskCreate,
    TaskMove,
    TaskScheduleHistoryItem,
    TaskScheduleUpdate,
    TaskUpdate,
    TypingBody,
)
from agent_team.web import API_PREFIX, auth_or_401, not_found
from core.database.base import SessionLocal, get_db


def _is_admin(user) -> bool:
    role = getattr(user.role, "value", user.role)
    return str(role).lower() in {"admin", "super_admin"}

router = APIRouter(prefix=API_PREFIX, tags=["agent-team-board"])

#: SSE poll cadence and keepalive cadence (in poll ticks) for run tailing.
_SSE_POLL_SECONDS = 0.4
_SSE_KEEPALIVE_TICKS = 25
#: Idle interval before the board stream emits an SSE keepalive comment.
_BOARD_KEEPALIVE_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------


@router.get("/boards")
async def list_boards(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    # Capture the app's main loop here (a high-traffic async endpoint) so the
    # autopilot ticker thread can dispatch runs onto it shortly after boot.
    from agent_team.features.board.runtime.dispatch import capture_main_loop

    capture_main_loop()
    boards = boards_repo.list_boards(db)
    counts = boards_repo.task_counts_by_board(db, [b.id for b in boards])
    is_admin = _is_admin(user)
    return [
        boards_repo.serialize_board(
            b,
            task_count=counts.get(b.id, 0),
            my_role=members_repo.effective_role(db, b, user_id=user.id, is_admin=is_admin),
        )
        for b in boards
    ]


@router.post("/boards")
async def create_board(
    payload: BoardCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.create_board(
        db,
        name=payload.name,
        description=payload.description,
        columns=payload.columns,
        owner_id=user.id,
    )
    members_repo.add_member(db, board_id=board.id, user_id=user.id, role="owner")
    db.commit()
    db.refresh(board)
    return boards_repo.serialize_board(board, task_count=0, my_role="owner")


@router.get("/boards/{board_id}")
async def get_board(board_id: str, request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    counts = boards_repo.task_counts_by_board(db, [board.id])
    my_role = members_repo.effective_role(
        db, board, user_id=user.id, is_admin=_is_admin(user)
    )
    return boards_repo.serialize_board(
        board, task_count=counts.get(board.id, 0), my_role=my_role
    )


@router.patch("/boards/{board_id}")
async def update_board(
    board_id: str, payload: BoardUpdate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    if payload.name is not None:
        board.name = payload.name.strip()
    if payload.description is not None:
        board.description = payload.description or None
    if payload.columns is not None:
        board.columns_json = json.dumps(
            [{"key": c.key, "name": c.name} for c in payload.columns]
        )
    if payload.agent_ids is not None:
        if payload.agent_ids:
            from core.agents.models import Agent

            known = {
                alias
                for (alias,) in db.query(Agent.alias).filter(Agent.alias.is_not(None))
            }
            unknown = [a for a in payload.agent_ids if a not in known]
            if unknown:
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"unknown agent id(s): {', '.join(unknown)}"},
                )
        board.agents_json = json.dumps(payload.agent_ids)
    if payload.cli_target_ids is not None:
        from agent_team.features.board.runtime.direct_acp import known_cli_aliases

        known = known_cli_aliases()
        unknown = [a for a in payload.cli_target_ids if a not in known]
        if unknown:
            return JSONResponse(
                status_code=422,
                content={"detail": f"unknown CLI target(s): {', '.join(unknown)}"},
            )
        board.cli_targets_json = json.dumps(payload.cli_target_ids)
    if payload.skill_ids is not None:
        from agent_team.features.board.runtime import skills as skills_rt

        known = {p["name"] for p in skills_rt.list_available_packs()}
        unknown = [s for s in payload.skill_ids if s not in known]
        if unknown:
            return JSONResponse(
                status_code=422,
                content={"detail": f"unknown skill pack(s): {', '.join(unknown)}"},
            )
        board.skills_json = json.dumps(payload.skill_ids)
    if payload.starter_prompt is not None:
        board.starter_prompt = payload.starter_prompt.strip()
    if payload.archived is not None:
        board.archived = payload.archived
    # ── Jira config ──────────────────────────────────────────────────────
    fields_set = payload.model_fields_set
    if payload.jira_enabled is not None:
        board.jira_enabled = payload.jira_enabled
    if "jira_base_url" in fields_set:
        board.jira_base_url = (payload.jira_base_url or "").strip() or None
    if "jira_email" in fields_set:
        board.jira_email = (payload.jira_email or "").strip() or None
    if "jira_project_key" in fields_set:
        board.jira_project_key = (payload.jira_project_key or "").strip() or None
    if payload.jira_mappings is not None:
        board.jira_mappings_json = json.dumps(payload.jira_mappings)
    if payload.jira_sync_filter is not None:
        board.jira_sync_filter_json = json.dumps(payload.jira_sync_filter)
    if "jira_api_token" in fields_set:
        # Omit the field to keep the stored token; send "" to clear it.
        token = (payload.jira_api_token or "").strip()
        board.jira_api_token = token or None
    db.commit()
    db.refresh(board)
    counts = boards_repo.task_counts_by_board(db, [board.id])
    my_role = members_repo.effective_role(
        db, board, user_id=user.id, is_admin=_is_admin(user)
    )
    return boards_repo.serialize_board(
        board, task_count=counts.get(board.id, 0), my_role=my_role
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.get("/boards/{board_id}/tasks")
async def list_tasks(board_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    tasks = tasks_repo.list_tasks(db, board_id=board_id)
    return [tasks_repo.serialize_task(t) for t in tasks]


def _create_task(db: Session, *, board, payload: TaskCreate, actor_id: str):
    """Shared task-creation logic for both the nested and flat routes."""
    valid_statuses = {c["key"] for c in board.columns()}
    status = payload.status or "todo"
    if status not in valid_statuses:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Unknown status '{status}' for this board"},
        )

    task = tasks_repo.create_task(
        db,
        board_id=board.id,
        title=payload.title,
        description=payload.description,
        status=status,
        assignee_id=payload.assignee_id,
        labels=payload.labels,
        priority=payload.priority,
        task_type=payload.task_type or "task",
        agent_assignee=payload.agent_assignee,
        created_by=actor_id,
    )
    activity_repo.record(
        db,
        task_id=task.id,
        actor_id=actor_id,
        kind=activity_repo.TASK_CREATED,
        data={"title": task.title, "status": task.status},
    )
    db.commit()
    db.refresh(task)
    get_board_bus().publish(
        board.id,
        {"type": "task.created", "board_id": board.id, "task_id": task.id},
    )
    return tasks_repo.serialize_task(task)


@router.post("/boards/{board_id}/tasks")
async def create_task(
    board_id: str, payload: TaskCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    return _create_task(db, board=board, payload=payload, actor_id=user.id)


@router.post("/tasks")
async def create_task_flat(
    payload: TaskCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not payload.board_id:
        return JSONResponse(status_code=400, content={"detail": "board_id is required"})
    board = boards_repo.get_board(db, payload.board_id)
    if board is None:
        return not_found("Board not found")
    return _create_task(db, board=board, payload=payload, actor_id=user.id)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    return tasks_repo.serialize_task(task)


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str, payload: TaskUpdate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")

    changes: dict[str, dict] = {}

    def _set(field: str, new_value, old_value):
        if new_value != old_value:
            changes[field] = {"from": old_value, "to": new_value}

    if payload.title is not None:
        _set("title", payload.title.strip(), task.title)
        task.title = payload.title.strip()
    if "description" in payload.model_fields_set:
        # Distinguish "field omitted" from an explicit null: the client sends
        # ``description: null`` to clear the description.
        task.description = payload.description or None
    if payload.status is not None:
        _set("status", payload.status, task.status)
        task.status = payload.status
        # A human re-triaging the task clears any autopilot back-off so it can be
        # auto-picked again (e.g. moving a failed task back to the source column).
        task.autopilot_attempts = 0
        task.autopilot_resume_after = None
    if payload.task_type is not None:
        _set("task_type", payload.task_type, task.task_type)
        task.task_type = payload.task_type
    if payload.assignee_id is not None:
        new_assignee = payload.assignee_id or None
        _set("assignee_id", new_assignee, task.assignee_id)
        task.assignee_id = new_assignee
    if "agent_assignee" in payload.model_fields_set:
        # Explicit "" clears the agent owner; clearing also lifts any back-off.
        new_agent = (payload.agent_assignee or "").strip() or None
        _set("agent_assignee", new_agent, task.agent_assignee)
        task.agent_assignee = new_agent
        task.autopilot_attempts = 0
        task.autopilot_resume_after = None
    if payload.labels is not None:
        task.labels_json = json.dumps(list(payload.labels))
    if "priority" in payload.model_fields_set:
        # Like description: an explicit ``priority: null`` clears the field.
        new_priority = payload.priority or None
        _set("priority", new_priority, task.priority)
        task.priority = new_priority
    if payload.archived is not None:
        _set("archived", payload.archived, task.archived)
        task.archived = payload.archived

    if changes:
        activity_repo.record(
            db,
            task_id=task.id,
            actor_id=user.id,
            kind=activity_repo.TASK_UPDATED,
            data={"changes": changes},
        )
    db.commit()
    db.refresh(task)
    get_board_bus().publish(
        task.board_id,
        {"type": "task.updated", "board_id": task.board_id, "task_id": task.id},
    )
    return tasks_repo.serialize_task(task)


@router.post("/tasks/{task_id}/jira/sync")
async def sync_task_from_jira(
    task_id: str,
    payload: JiraSyncBody,
    request: Request,
    db: Session = Depends(get_db),
):
    """Pull a linked Jira issue's fields onto the task (Phase 1, one-way)."""
    user, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    board = boards_repo.get_board(db, task.board_id)
    if board is None or not board.jira_enabled:
        return JSONResponse(
            status_code=422,
            content={"detail": "Jira sync is not enabled for this board"},
        )

    key = (payload.jira_key or "").strip() or task.jira_key
    if not key:
        return JSONResponse(
            status_code=422,
            content={"detail": "No Jira issue key to sync — set one first"},
        )

    try:
        client = jira_service.build_client(board)
        jira_service.apply_issue_to_task(
            db, board=board, task=task, client=client, key=key, actor_id=user.id
        )
    except JiraError as exc:
        # Jira's own 4xx (auth/not-found) are config problems the user can fix;
        # network/5xx are upstream failures.
        status = 400 if exc.status_code else 502
        return JSONResponse(status_code=status, content={"detail": exc.message})

    db.commit()
    db.refresh(task)
    get_board_bus().publish(
        task.board_id,
        {"type": "task.updated", "board_id": task.board_id, "task_id": task.id},
    )
    return tasks_repo.serialize_task(task)


#: Cap on how many issues a single preview pulls from a project.
_JIRA_PREVIEW_LIMIT = 300


@router.post("/boards/{board_id}/jira/sync/preview")
async def preview_board_jira_sync(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    """List a project's issues for import, flagging which already exist as tasks.

    Queries the configured Jira project (newest first, capped at
    ``_JIRA_PREVIEW_LIMIT``) so the user can pick which issues to pull in. Each
    row is marked *new* or *update* depending on whether a task is already linked.
    """
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    if not board.jira_enabled:
        return JSONResponse(
            status_code=422,
            content={"detail": "Jira sync is not enabled for this board"},
        )
    project_key = (board.jira_project_key or "").strip()
    if not project_key:
        return JSONResponse(
            status_code=422,
            content={"detail": "Set a Jira project key first"},
        )

    try:
        client = jira_service.build_client(board)
        jql = jira_service.build_search_jql(project_key, board.jira_sync_filter())
        issues = client.search_issues(jql, max_results=_JIRA_PREVIEW_LIMIT)
    except JiraError as exc:
        status = 400 if exc.status_code else 502
        return JSONResponse(status_code=status, content={"detail": exc.message})

    # Map already-linked keys (including archived) → their human key for labels.
    linked = {
        t.jira_key: t
        for t in tasks_repo.list_tasks(db, board_id=board.id, include_archived=True)
        if t.jira_key
    }

    columns = {c["key"]: c["name"] for c in board.columns()}
    items: list[JiraPreviewItem] = []
    for issue in issues:
        key = issue.get("key")
        if not key:
            continue
        fields = issue.get("fields") or {}
        # Reuse the sync mapper so type/priority/status match what import would set.
        changes = build_task_changes(issue, board=board)
        status_key = changes.get("status")
        status_label = (
            columns.get(status_key)
            if status_key
            else (fields.get("status") or {}).get("name")
        )
        existing = linked.get(key)
        items.append(
            JiraPreviewItem(
                jira_key=key,
                title=(fields.get("summary") or key),
                jira_type=(fields.get("issuetype") or {}).get("name"),
                jira_priority=(fields.get("priority") or {}).get("name"),
                task_type=changes.get("task_type"),
                priority=changes.get("priority"),
                status=status_label,
                exists=existing is not None,
                human_key=existing.human_key if existing else None,
            )
        )

    return JiraPreviewResponse(items=items)


@router.post("/boards/{board_id}/jira/import")
async def import_issue_from_jira(
    board_id: str,
    payload: JiraImportBody,
    request: Request,
    db: Session = Depends(get_db),
):
    """Import a single Jira issue: update the linked task, or create a new one.

    New tasks land in the board's first column (the Jira status mapping may then
    move them). Called once per selected issue so the UI can show progress.
    """
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    if not board.jira_enabled:
        return JSONResponse(
            status_code=422,
            content={"detail": "Jira sync is not enabled for this board"},
        )

    key = payload.jira_key.strip()
    try:
        client = jira_service.build_client(board)
        issue = client.get_issue(key)
    except JiraError as exc:
        status = 400 if exc.status_code else 502
        return JSONResponse(status_code=status, content={"detail": exc.message})

    task = tasks_repo.get_task_by_jira_key(db, board_id=board.id, jira_key=key)
    created = task is None
    if created:
        columns = board.columns()
        first_col = columns[0]["key"] if columns else "todo"
        task = tasks_repo.create_task(
            db,
            board_id=board.id,
            title=key,
            description=None,
            status=first_col,
            assignee_id=None,
            labels=None,
            priority=None,
            created_by=user.id,
        )

    jira_service.apply_issue_to_task(
        db,
        board=board,
        task=task,
        client=client,
        key=key,
        actor_id=user.id,
        issue=issue,
    )
    db.commit()
    db.refresh(task)
    get_board_bus().publish(
        board.id,
        {
            "type": "task.created" if created else "task.updated",
            "board_id": board.id,
            "task_id": task.id,
        },
    )
    return tasks_repo.serialize_task(task)


@router.post("/boards/{board_id}/jira/sync")
async def sync_board_from_jira(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    """Pull every linked task on the board that matches its sync filter."""
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    if not board.jira_enabled:
        return JSONResponse(
            status_code=422,
            content={"detail": "Jira sync is not enabled for this board"},
        )

    flt = board.jira_sync_filter()
    include_archived = not flt.get("exclude_archived", True)
    tasks = tasks_repo.list_tasks(
        db, board_id=board.id, include_archived=include_archived
    )

    try:
        result = jira_service.sync_board(
            db, board=board, tasks=tasks, actor_id=user.id
        )
    except JiraError as exc:
        status = 400 if exc.status_code else 502
        return JSONResponse(status_code=status, content={"detail": exc.message})

    db.commit()
    # No task_id → every open board view refreshes its task list.
    get_board_bus().publish(board.id, {"type": "task.updated", "board_id": board.id})
    return result.as_dict()


@router.post("/tasks/{task_id}/move")
async def move_task(
    task_id: str, payload: TaskMove, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    board = boards_repo.get_board(db, task.board_id)
    if board is not None and payload.status not in {c["key"] for c in board.columns()}:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Unknown status '{payload.status}' for this board"},
        )
    previous_status = task.status
    task.status = payload.status
    task.position = payload.position
    if previous_status != payload.status:
        # A human moving the card clears autopilot back-off (re-eligible to pick).
        task.autopilot_attempts = 0
        task.autopilot_resume_after = None
        activity_repo.record(
            db,
            task_id=task.id,
            actor_id=user.id,
            kind=activity_repo.TASK_MOVED,
            data={"from": previous_status, "to": payload.status},
        )
    db.commit()
    db.refresh(task)
    get_board_bus().publish(
        task.board_id,
        {
            "type": "task.moved",
            "board_id": task.board_id,
            "task_id": task.id,
            "status": payload.status,
        },
    )
    return tasks_repo.serialize_task(task)


@router.delete("/tasks/{task_id}")
async def archive_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    task.archived = True
    board_id = task.board_id
    # Reclaim disk: drop the per-task repo working copies (re-created on demand if
    # the task runs again). Best-effort — never block archiving on cleanup.
    try:
        from agent_team.features.repos.task_copy import cleanup_task_repos

        cleanup_task_repos(db, task)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "agent_team: failed to clean task repos for %s", task_id
        )
    db.commit()
    get_board_bus().publish(
        board_id,
        {"type": "task.deleted", "board_id": board_id, "task_id": task_id},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# CSV import / export of tasks
# ---------------------------------------------------------------------------


@router.get("/boards/{board_id}/tasks/export.csv")
async def export_board_tasks_csv(
    board_id: str,
    request: Request,
    include_archived: bool = False,
    db: Session = Depends(get_db),
):
    """Download the board's tasks as CSV (one row per task)."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    from agent_team.features.board import csv_tasks

    body = csv_tasks.export_tasks_csv(db, board, include_archived=include_archived)
    filename = f"{board.slug or 'board'}-tasks.csv"
    return Response(
        # Prepend a BOM so Excel opens UTF-8 (e.g. accents) correctly.
        content="\ufeff" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_preview(plans) -> CsvImportPreview:
    rows = [CsvImportRow(**p.as_dict()) for p in plans]
    return CsvImportPreview(
        rows=rows,
        total=len(rows),
        creates=sum(1 for p in plans if p.action == "create"),
        updates=sum(1 for p in plans if p.action == "update"),
        errors=sum(1 for p in plans if p.action == "error"),
    )


@router.post("/boards/{board_id}/tasks/import/preview")
async def preview_board_tasks_csv(
    board_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Dry-run an upload: validate every row and report create/update/error."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    from agent_team.features.board import csv_tasks

    data = await file.read()
    try:
        plans = csv_tasks.plan_import(db, board, data)
    except csv_tasks.CsvImportError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return _csv_preview(plans)


@router.post("/boards/{board_id}/tasks/import")
async def import_board_tasks_csv(
    board_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Apply an upload: create/update tasks, then refresh the board."""
    user, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    from agent_team.features.board import csv_tasks

    data = await file.read()
    try:
        plans = csv_tasks.plan_import(db, board, data)
    except csv_tasks.CsvImportError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    result, any_changes = csv_tasks.apply_import(db, board, plans, actor_id=user.id)
    db.commit()
    if any_changes:
        # No task_id → every open board view refreshes its task list.
        get_board_bus().publish(
            board.id, {"type": "task.updated", "board_id": board.id}
        )
    return CsvImportResult(**result.as_dict())


# ---------------------------------------------------------------------------
# Task code repositories (working copies)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/repos")
async def list_task_repos(task_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    from agent_team.features.repos.task_copy import list_task_repo_dirs

    return list_task_repo_dirs(db, task)


@router.post("/tasks/{task_id}/repos/prepare")
async def prepare_task_repos_endpoint(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    from agent_team.features.repos.task_copy import prepare_task_repos_by_id

    prepared = await asyncio.to_thread(prepare_task_repos_by_id, task_id)
    return {"prepared": prepared}


# ---------------------------------------------------------------------------
# Agents (mentionable)
# ---------------------------------------------------------------------------


@router.get("/agents")
async def list_agents(request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    from core.agents.models import Agent

    rows = (
        db.query(Agent)
        .filter(Agent.alias.is_not(None))
        .order_by(Agent.alias.asc())
        .all()
    )
    return [
        {
            "id": row.alias,
            "display_name": getattr(row, "name", None) or row.alias,
            "description": getattr(row, "description", None) or "",
            "avatar_url": getattr(row, "avatar_url", None),
            "model": getattr(row, "model", None),
            "mentionable": True,
            "enabled": bool(getattr(row, "enabled", True)),
            "status": None,
        }
        for row in rows
    ]


@router.get("/cli-targets")
async def list_cli_targets(request: Request, db: Session = Depends(get_db)):
    """Direct CLI engines (Claude/Cursor/Codex) chattable without the LLM.

    These are not agents: each is addressed by a synthetic ``cli:<engine>`` alias
    and driven straight over ACP. ``available`` hints whether the engine's launch
    command is installed on this host.
    """
    _, err = auth_or_401(db, request)
    if err:
        return err
    from agent_team.features.board.runtime.direct_acp import available_targets

    return available_targets()


@router.get("/skills", response_model=list[SkillPackDTO])
async def list_skills(request: Request, db: Session = Depends(get_db)):
    """Skill packs available to assign to a board's direct-CLI agents.

    Sourced from the core ``skill_packs`` catalog (shared dir + git sources).
    Returns an empty list when the ``skill_packs`` plugin is not installed.
    """
    _, err = auth_or_401(db, request)
    if err:
        return err
    from agent_team.features.board.runtime import skills as skills_rt

    return [SkillPackDTO(**p) for p in skills_rt.list_available_packs()]


# ---------------------------------------------------------------------------
# Autopilot: per-board auto-pickup of assigned tasks on a schedule
# ---------------------------------------------------------------------------


@router.get("/boards/{board_id}/autopilot")
async def get_autopilot(board_id: str, request: Request, db: Session = Depends(get_db)):
    """Return the board's autopilot config (a disabled default if never set)."""
    # Polled by the autopilot dialog/panel — a reliable place to capture the
    # app's main loop so the ticker thread can dispatch runs.
    from agent_team.features.board.runtime.dispatch import capture_main_loop

    capture_main_loop()
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")
    row = autopilot_repo.get_or_create(db, board_id)
    db.commit()
    return autopilot_repo.serialize(row)


@router.put("/boards/{board_id}/autopilot")
async def update_autopilot(
    board_id: str,
    payload: AutopilotUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Patch the board's autopilot config and reseed the schedule cursor."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")

    row = autopilot_repo.get_or_create(db, board_id)
    column_keys = {c["key"] for c in board.columns()}

    # Status fields reference stable column *keys*; reject unknown ones so the
    # config can never point at a column that does not exist on this board.
    for field in ("source_status", "working_status", "done_status", "error_status"):
        value = getattr(payload, field)
        if value is not None and value not in column_keys:
            return JSONResponse(
                status_code=422,
                content={"detail": f"{field} '{value}' is not a column on this board"},
            )

    if payload.schedule_mode is not None:
        row.schedule_mode = payload.schedule_mode
    if payload.interval_seconds is not None:
        row.interval_seconds = payload.interval_seconds
    if payload.cron is not None:
        row.cron = payload.cron.strip() or None
    if payload.timezone is not None:
        row.timezone = payload.timezone
    for field in ("source_status", "working_status", "done_status", "error_status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    if payload.board_concurrency is not None:
        row.board_concurrency = payload.board_concurrency
    if payload.default_agent_concurrency is not None:
        row.default_agent_concurrency = payload.default_agent_concurrency
    if payload.agent_concurrency is not None:
        autopilot_repo.set_agent_concurrency(row, payload.agent_concurrency)
    if payload.error_cooldown_seconds is not None:
        row.error_cooldown_seconds = payload.error_cooldown_seconds
    if payload.max_attempts is not None:
        row.max_attempts = payload.max_attempts
    if "prompt_template" in payload.model_fields_set:
        row.prompt_template = (payload.prompt_template or "").strip() or None
    if payload.routing_rules is not None:
        autopilot_repo.set_routing_rules(row, payload.routing_rules)
    if payload.enabled is not None:
        row.enabled = payload.enabled

    # Cron validity check (interval is already clamped by the schema).
    from agent_team.features.board.runtime import autopilot as autopilot_rt

    if row.enabled and row.schedule_mode == "cron" and not autopilot_rt.is_valid_cron(row.cron):
        return JSONResponse(
            status_code=422,
            content={"detail": "A valid cron expression is required for cron schedules"},
        )

    # Reseed the cursor whenever the config changes: enabled + scheduled → next
    # due time; otherwise clear it so the ticker skips this board.
    row.next_run_at = autopilot_rt.compute_next_run_at(row) if row.enabled else None
    db.commit()
    db.refresh(row)
    return autopilot_repo.serialize(row)


@router.post("/boards/{board_id}/autopilot/route")
async def autopilot_route(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    """Apply routing rules once: auto-assign agents to unassigned source tasks."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")

    from agent_team.features.board.runtime import autopilot as autopilot_rt

    assigned = autopilot_rt.route_now(db, board_id)
    db.commit()
    return {"assigned": assigned}


@router.get("/boards/{board_id}/autopilot/summary")
async def autopilot_summary(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    """Live, read-only autopilot status for the board status panel."""
    # Polled live by the status panel — capture the app's main loop so the
    # ticker thread can dispatch runs even if `GET /boards` was never hit.
    from agent_team.features.board.runtime.dispatch import capture_main_loop

    capture_main_loop()
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")

    from datetime import UTC, datetime

    from agent_team.features.board.models import AgentTeamRun, AgentTeamTask

    row = autopilot_repo.get_or_create(db, board_id)
    db.commit()

    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    auto_runs = (
        db.query(AgentTeamRun)
        .join(AgentTeamTask, AgentTeamRun.task_id == AgentTeamTask.id)
        .filter(
            AgentTeamTask.board_id == board_id,
            AgentTeamRun.trigger == "autopilot",
        )
    )
    in_flight = auto_runs.filter(
        AgentTeamRun.status.notin_(tuple(TERMINAL_RUN_STATUSES))
    ).count()
    runs_today = auto_runs.filter(AgentTeamRun.created_at >= day_start).count()

    recent_rows = (
        auto_runs.order_by(AgentTeamRun.created_at.desc()).limit(8).all()
    )
    recent = [
        AutopilotRecentItem(
            task_id=r.task_id,
            human_key=t.human_key if (t := tasks_repo.get_task(db, r.task_id)) else "",
            title=t.title if t else "",
            status=t.status if t else "",
            agent=r.agent_alias,
            run_status=r.status,
            at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in recent_rows
    ]

    return AutopilotSummaryDTO(
        enabled=row.enabled,
        schedule_mode=row.schedule_mode,
        next_run_at=row.next_run_at.isoformat() if row.next_run_at else None,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        in_flight=in_flight,
        board_concurrency=row.board_concurrency,
        runs_today=runs_today,
        recent=recent,
    )


# ---------------------------------------------------------------------------
# Task schedule: recurring cron-driven agent runs for a single task
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/schedule")
async def get_task_schedule(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    """Return the task's recurring-run schedule (a disabled default if unset)."""
    # Polled by the schedule dialog — a reliable place to capture the app's main
    # loop so the ticker thread can dispatch scheduled runs.
    from agent_team.features.board.runtime.dispatch import capture_main_loop

    capture_main_loop()
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    row = schedule_repo.get_or_create(db, task_id)
    db.commit()
    return schedule_repo.serialize(row)


@router.put("/tasks/{task_id}/schedule")
async def update_task_schedule(
    task_id: str,
    payload: TaskScheduleUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Patch a task's recurring-run schedule and reseed its cron cursor."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    board = boards_repo.get_board(db, task.board_id)
    if board is None:
        return not_found("Board not found")

    from agent_team.features.board.runtime import task_schedule as schedule_rt

    row = schedule_repo.get_or_create(db, task_id)

    if payload.agent_alias is not None:
        agent = payload.agent_alias.strip() or None
        if agent is not None:
            staffed = set(board.agent_ids()) | set(board.cli_target_ids())
            if agent not in staffed:
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"agent '{agent}' is not staffed on this board"},
                )
        row.agent_alias = agent
    if payload.cron is not None:
        row.cron = payload.cron.strip() or None
    if payload.timezone is not None:
        row.timezone = payload.timezone
    if "prompt" in payload.model_fields_set:
        row.prompt = (payload.prompt or "").strip() or None
    if payload.conversation_mode is not None:
        row.conversation_mode = payload.conversation_mode
    if payload.enabled is not None:
        row.enabled = payload.enabled

    if row.enabled:
        if not schedule_rt.is_valid_cron(row.cron):
            return JSONResponse(
                status_code=422,
                content={"detail": "A valid cron expression is required to enable a schedule"},
            )
        if not (row.agent_alias or "").strip():
            return JSONResponse(
                status_code=422,
                content={"detail": "An agent must be selected to enable a schedule"},
            )

    # Reseed the cursor: enabled + valid cron → next due time; else clear it so
    # the ticker skips this task.
    row.next_run_at = schedule_rt.compute_next_run_at(row) if row.enabled else None
    db.commit()
    db.refresh(row)
    return schedule_repo.serialize(row)


@router.get("/tasks/{task_id}/schedule/history")
async def task_schedule_history(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    """Return recent runs started by this task's schedule (newest first)."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")

    from agent_team.features.board.models import AgentTeamRun

    rows = (
        db.query(AgentTeamRun)
        .filter(
            AgentTeamRun.task_id == task_id,
            AgentTeamRun.trigger == "schedule",
        )
        .order_by(AgentTeamRun.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        TaskScheduleHistoryItem(
            run_id=r.id,
            human_key=r.human_key,
            agent_id=r.agent_alias,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else None,
            ended_at=r.ended_at.isoformat() if r.ended_at else None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Runs: mention an agent, list/inspect runs, stream and cancel
# ---------------------------------------------------------------------------


def _prompt_with_attachments(task, body: str, attachment_ids: list[str] | None) -> str:
    """Append workspace-relative pointers for any attached files to the prompt.

    The files already live in the task workspace, so the agent can open them
    with its file tools; listing the paths makes them discoverable in-context.
    """
    if not attachment_ids or not task.workspace_path:
        return body
    files = attachments.resolve_chat_attachments(task.workspace_path, attachment_ids)
    if not files:
        return body
    lines = [f"- `{f['path']}` ({f['filename']})" for f in files]
    pointers = "\n".join(lines)
    return f"{body}\n\nAttached files (in the task workspace):\n{pointers}"


@router.post("/tasks/{task_id}/mentions")
async def create_mention(
    task_id: str, payload: MentionCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")

    prompt = _prompt_with_attachments(task, payload.body, payload.attachment_ids)
    run, _conversation = run_service.create_run_for_task(
        db,
        task_id=task_id,
        agent_alias=payload.agent_id,
        prompt=prompt,
        trigger="mention",
        actor_id=user.id,
    )
    activity_repo.record(
        db,
        task_id=task_id,
        actor_id=user.id,
        kind=activity_repo.MENTION_CREATED,
        data={"agent_id": payload.agent_id, "run_id": run.id, "run_key": run.human_key},
    )
    db.commit()
    db.refresh(run)
    conversation_id = run.conversation_id

    await get_run_backend().start(run.id)
    get_board_bus().publish(
        task.board_id,
        {
            "type": "run.started",
            "board_id": task.board_id,
            "task_id": task.id,
            "agent_id": payload.agent_id,
            "run_id": run.id,
            "actor_id": user.id,
        },
    )
    return MentionResponse(
        run=runs_repo.serialize_run(run),
        conversation_id=conversation_id or "",
        stream_url=f"{API_PREFIX}/runs/{run.id}/events",
    )


@router.get("/tasks/{task_id}/runs")
async def list_runs(
    task_id: str, request: Request, agent_id: str | None = None, db: Session = Depends(get_db)
):
    """Runs for a task, optionally narrowed to one agent (``?agent_id=``)."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    runs = runs_repo.list_runs_for_task(db, task_id, agent_alias=agent_id)
    return [runs_repo.serialize_run(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    run = runs_repo.get_run(db, run_id)
    if run is None:
        return not_found("Run not found")
    return runs_repo.serialize_run(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    run = runs_repo.get_run(db, run_id)
    if run is None:
        return not_found("Run not found")
    ok = await get_run_backend().cancel(run_id)
    return {"ok": ok, "status": event_store.get_run_status(run_id)}


@router.get("/runs/{run_id}/tools/{tool_id}/output")
async def get_tool_output(
    run_id: str, tool_id: str, request: Request, db: Session = Depends(get_db)
):
    """Full text of one tool result, loaded on demand when a card is expanded.

    The streamed frame only carries a short preview, so the complete output is
    fetched here only for the specific tool the user chose to expand.
    """
    _, err = auth_or_401(db, request)
    if err:
        return err
    if runs_repo.get_run(db, run_id) is None:
        return not_found("Run not found")
    output = tool_outputs_repo.get_tool_output(run_id, tool_id)
    if output is None:
        return not_found("Tool output not found")
    return output


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request):
    """Server-sent events for a run: replay from the cursor, then tail.

    The cursor comes from the ``Last-Event-ID`` header (set automatically by the
    browser on reconnect) or an ``?after=`` query param, so a dropped connection
    or page reload resumes without losing or duplicating frames.
    """
    db = SessionLocal()
    try:
        _, err = auth_or_401(db, request)
        if err:
            return err
        exists = runs_repo.get_run(db, run_id) is not None
    finally:
        db.close()
    if not exists:
        return not_found("Run not found")

    cursor_raw = request.headers.get("Last-Event-ID") or request.query_params.get("after", "0")
    try:
        after = int(cursor_raw)
    except (TypeError, ValueError):
        after = 0

    return StreamingResponse(
        _event_stream(run_id, after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_frame(seq: int, event_type: str, data: dict) -> str:
    return f"id: {seq}\nevent: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_named(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(run_id: str, after: int):
    cursor = after
    idle_ticks = 0
    while True:
        frames = await asyncio.to_thread(event_store.list_events, run_id, cursor)
        if frames:
            for frame in frames:
                cursor = frame["seq"]
                yield _sse_frame(frame["seq"], frame["type"], frame["data"])
            idle_ticks = 0
            continue

        status = await asyncio.to_thread(event_store.get_run_status, run_id)
        if status in TERMINAL_RUN_STATUSES:
            # Drain any frames that landed between the last fetch and going terminal.
            tail = await asyncio.to_thread(event_store.list_events, run_id, cursor)
            for frame in tail:
                cursor = frame["seq"]
                yield _sse_frame(frame["seq"], frame["type"], frame["data"])
            yield _sse_named("end", {"status": status})
            return

        idle_ticks += 1
        if idle_ticks % _SSE_KEEPALIVE_TICKS == 0:
            yield ": keepalive\n\n"
        await asyncio.sleep(_SSE_POLL_SECONDS)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/comments")
async def list_comments(task_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    comments = comments_repo.list_comments(db, task_id)
    authors = comments_repo.resolve_authors(db, comments)
    return [
        comments_repo.serialize_comment(c, authors.get(c.author_id) if c.author_id else None)
        for c in comments
    ]


@router.post("/tasks/{task_id}/comments")
async def create_comment(
    task_id: str, payload: CommentCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    if not payload.body.strip() and not payload.attachments:
        return JSONResponse(
            status_code=422,
            content={"detail": "comment body or attachments required"},
        )
    comment = comments_repo.create_comment(
        db,
        task_id=task_id,
        author_id=user.id,
        body=payload.body,
        attachments=payload.attachments,
        visible_to_agents=payload.visible_to_agents,
    )
    activity_repo.record(
        db,
        task_id=task_id,
        actor_id=user.id,
        kind=activity_repo.COMMENT_ADDED,
        data={"comment_id": comment.id},
    )
    db.commit()
    db.refresh(comment)
    get_board_bus().publish(
        task.board_id,
        {
            "type": "comment.created",
            "board_id": task.board_id,
            "task_id": task_id,
            "comment_id": comment.id,
        },
    )
    return comments_repo.serialize_comment(comment, user)


@router.patch("/tasks/{task_id}/comments/{comment_id}")
async def update_comment(
    task_id: str,
    comment_id: str,
    payload: CommentUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Edit a note's body and/or agent visibility. Author (or admin) only."""
    user, err = auth_or_401(db, request)
    if err:
        return err
    comment = comments_repo.get_comment(db, comment_id)
    if comment is None or comment.task_id != task_id or comment.deleted_at is not None:
        return not_found("Comment not found")
    if comment.author_id != user.id and not _is_admin(user):
        return JSONResponse(
            status_code=403, content={"detail": "Only the author can edit a note"}
        )
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    comments_repo.update_comment(
        db, comment, body=payload.body, visible_to_agents=payload.visible_to_agents
    )
    db.commit()
    db.refresh(comment)
    get_board_bus().publish(
        task.board_id,
        {
            "type": "comment.updated",
            "board_id": task.board_id,
            "task_id": task_id,
            "comment_id": comment.id,
        },
    )
    return comments_repo.serialize_comment(comment, user)


def _soft_delete_comment_and_publish(db: Session, comment) -> dict:
    """Soft-delete a comment, then broadcast ``comment.deleted`` to the board."""
    task = tasks_repo.get_task(db, comment.task_id)
    comments_repo.soft_delete_comment(db, comment)
    db.commit()
    if task is not None:
        get_board_bus().publish(
            task.board_id,
            {
                "type": "comment.deleted",
                "board_id": task.board_id,
                "task_id": comment.task_id,
                "comment_id": comment.id,
            },
        )
    return {"ok": True}


@router.delete("/tasks/{task_id}/comments/{comment_id}")
async def delete_task_comment(
    task_id: str, comment_id: str, request: Request, db: Session = Depends(get_db)
):
    """Task-scoped delete — the path shape the current web client calls."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    comment = comments_repo.get_comment(db, comment_id)
    if comment is None or comment.task_id != task_id or comment.deleted_at is not None:
        return not_found("Comment not found")
    return _soft_delete_comment_and_publish(db, comment)


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, request: Request, db: Session = Depends(get_db)):
    """Flat-path delete, kept for older bundled clients."""
    _, err = auth_or_401(db, request)
    if err:
        return err
    comment = comments_repo.get_comment(db, comment_id)
    if comment is None or comment.deleted_at is not None:
        return not_found("Comment not found")
    return _soft_delete_comment_and_publish(db, comment)


# ---------------------------------------------------------------------------
# Activity changelog
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/activity")
async def list_activity(task_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    return [
        activity_repo.serialize_activity(a) for a in activity_repo.list_activity(db, task_id)
    ]


# ---------------------------------------------------------------------------
# Board stream: notify clients to refetch when the board changes
# ---------------------------------------------------------------------------


@router.get("/boards/{board_id}/stream")
async def stream_board(board_id: str, request: Request):
    # Capture the app's main loop here too: this SSE handler runs whenever a
    # board is viewed (even when the board *list* endpoint is never hit, e.g.
    # deep-linking or a hidden board list), so the autopilot ticker thread can
    # dispatch runs without depending on `GET /boards` being called first.
    from agent_team.features.board.runtime.dispatch import capture_main_loop

    capture_main_loop()
    db = SessionLocal()
    try:
        _, err = auth_or_401(db, request)
        if err:
            return err
        exists = boards_repo.get_board(db, board_id) is not None
    finally:
        db.close()
    if not exists:
        return not_found("Board not found")

    return StreamingResponse(
        _board_stream(board_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _board_stream(board_id: str):
    """Tail the board bus, forwarding typed hint frames the FE switches on.

    Each frame is sent as a JSON ``data:`` line (no event name) carrying its own
    ``type``; this matches ``subscribeBoardEvents`` on the client.
    """
    bus = get_board_bus()
    queue = bus.subscribe(board_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_BOARD_KEEPALIVE_SECONDS
                )
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        bus.unsubscribe(board_id, queue)


# ---------------------------------------------------------------------------
# Board members
# ---------------------------------------------------------------------------


@router.get("/boards/{board_id}/members")
async def list_members(board_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")

    rows = members_repo.list_members(db, board_id)
    result = [members_repo.serialize_member(m, u) for m, u in rows]

    # Boards created before membership existed only carry ``owner_id``; surface
    # the owner as an implicit member so the UI shows them and the add-picker
    # excludes them (no adding yourself when you already own the board).
    member_ids = {r.user_id for r in result}
    if board.owner_id and board.owner_id not in member_ids:
        from core.database.models import User

        owner = db.query(User).filter(User.id == board.owner_id).first()
        if owner is not None:
            from agent_team.features.board.schemas import BoardMemberDTO

            result.insert(
                0,
                BoardMemberDTO(
                    board_id=board_id,
                    user_id=owner.id,
                    role="owner",
                    email=owner.email,
                    display_name=owner.full_name or owner.username,
                    avatar_url=None,
                ),
            )
    return result


@router.post("/boards/{board_id}/members")
async def add_member(
    board_id: str, payload: AddMemberBody, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    board = boards_repo.get_board(db, board_id)
    if board is None:
        return not_found("Board not found")

    from core.database.models import User

    target = None
    if payload.user_id:
        target = db.query(User).filter(User.id == payload.user_id).first()
    elif payload.email:
        target = db.query(User).filter(User.email == payload.email).first()
    if target is None:
        return not_found("User not found")

    member = members_repo.add_member(
        db, board_id=board_id, user_id=target.id, role=payload.role
    )
    db.commit()
    return members_repo.serialize_member(member, target)


@router.delete("/boards/{board_id}/members/{user_id}")
async def remove_member(
    board_id: str, user_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    members_repo.remove_member(db, board_id=board_id, user_id=user_id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-agent threads on a task: attempts, reset, typing, message history
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/agents/{agent_id}/conversations")
async def list_attempts(
    task_id: str, agent_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    attempts = conversations_repo.list_attempts(db, task_id=task_id, agent_alias=agent_id)
    return [conversations_repo.serialize_attempt(c) for c in attempts]


@router.post("/tasks/{task_id}/agents/{agent_id}/reset")
async def reset_thread(
    task_id: str, agent_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    conv = conversations_repo.reset_conversation(db, task_id=task_id, agent_alias=agent_id)
    db.commit()
    db.refresh(conv)
    return conversations_repo.serialize_attempt(conv)


@router.post("/tasks/{task_id}/agents/{agent_id}/typing")
async def set_typing(
    task_id: str,
    agent_id: str,
    payload: TypingBody,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    task = tasks_repo.get_task(db, task_id)
    if task is None:
        return not_found("Task not found")
    # Pure presence: broadcast an ephemeral "X is typing…" hint, never persisted.
    # ``state`` must be forwarded — a "stop" ping (sent on send/blur) clears the
    # indicator on other clients instead of renewing it.
    get_board_bus().publish(
        task.board_id,
        {
            "type": "agent.typing",
            "board_id": task.board_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "actor_id": user.id,
            "user_name": getattr(user, "full_name", None) or user.username,
            "state": payload.state,
        },
    )
    return {"ok": True}


def _agent_display(db: Session, agent_alias: str) -> str:
    """Resolve an agent's display name, falling back to its alias."""
    from agent_team.features.board.runtime.direct_acp import (
        display_name_for_alias,
        is_direct_cli_alias,
    )

    if is_direct_cli_alias(agent_alias):
        return display_name_for_alias(agent_alias)
    from core.agents.models import Agent

    row = db.query(Agent).filter(Agent.alias == agent_alias).first()
    if row is None:
        return agent_alias
    return getattr(row, "name", None) or agent_alias


@router.get("/tasks/{task_id}/agents/{agent_id}/messages")
async def list_agent_messages(
    task_id: str, agent_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    conversation = conversations_repo.get_active_conversation(
        db, task_id=task_id, agent_alias=agent_id
    )
    if conversation is None:
        return []
    return messages_repo.list_thread_messages(
        db, conversation=conversation, agent_display=_agent_display(db, agent_id)
    )


@router.get("/tasks/{task_id}/agents/{agent_id}/conversations/{conv_id}/messages")
async def list_attempt_messages(
    task_id: str,
    agent_id: str,
    conv_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    conversation = conversations_repo.get_conversation(db, conv_id)
    if conversation is None or conversation.task_id != task_id:
        return not_found("Conversation not found")
    return messages_repo.list_thread_messages(
        db, conversation=conversation, agent_display=_agent_display(db, agent_id)
    )


# ---------------------------------------------------------------------------
# Task workspace files (read/write within the task's sandboxed folder)
# ---------------------------------------------------------------------------


def _task_workspace(db: Session, task_id: str):
    task = tasks_repo.get_task(db, task_id)
    if task is None or not task.workspace_path:
        return None
    return task


@router.get("/tasks/{task_id}/files/tree")
async def workspace_tree(
    task_id: str,
    request: Request,
    path: str = "",
    depth: int = 1,
    db: Session = Depends(get_db),
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    root = Path(task.workspace_path)
    if not root.is_dir():
        return {"root": str(root), "entries": [], "truncated": False}
    try:
        return ws_module.build_tree(root, path, depth)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "invalid path"})


@router.get("/tasks/{task_id}/files")
async def workspace_file(
    task_id: str, request: Request, path: str, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    try:
        target = ws_module.resolve_in_workspace(task.workspace_path, path)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "invalid path"})
    if not target.is_file():
        return not_found("File not found")
    data = target.read_bytes()
    try:
        content = data.decode("utf-8")
        encoding = "utf-8"
        truncated = False
    except UnicodeDecodeError:
        content = ""
        encoding = "binary"
        truncated = True
    return {
        "path": path,
        "content": content,
        "size": len(data),
        "encoding": encoding,
        "truncated": truncated,
    }


@router.get("/tasks/{task_id}/files/raw")
async def workspace_file_raw(
    task_id: str, request: Request, path: str, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    try:
        target = ws_module.resolve_in_workspace(task.workspace_path, path)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "invalid path"})
    if not target.is_file():
        return not_found("File not found")
    from fastapi.responses import FileResponse

    return FileResponse(str(target))


@router.put("/tasks/{task_id}/files")
async def workspace_write(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    body = await request.json()
    rel = str(body.get("path") or "")
    content = str(body.get("content") or "")
    try:
        target = ws_module.resolve_in_workspace(task.workspace_path, rel)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "invalid path"})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": rel,
        "content": content,
        "size": len(content.encode("utf-8")),
        "encoding": "utf-8",
        "truncated": False,
    }


@router.delete("/tasks/{task_id}/files")
async def workspace_delete(
    task_id: str, request: Request, path: str, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    try:
        target = ws_module.resolve_in_workspace(task.workspace_path, path)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "invalid path"})
    # Never delete the workspace root itself, only entries inside it.
    if target == Path(task.workspace_path).resolve():
        return JSONResponse(status_code=400, content={"detail": "invalid path"})
    if target.is_dir():
        shutil.rmtree(target)
    elif target.is_file():
        target.unlink()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Attachments: uploads land in the task workspace (chat under _attachments/,
# comments under _notes/) so agents can open them and file routes can serve them
# ---------------------------------------------------------------------------


async def _save_uploads(task, files: list[UploadFile], subdir: str) -> list[dict]:
    """Persist uploaded files into the task workspace and return their DTOs."""
    ws_module.ensure_task_workspace(task.workspace_path)
    saved: list[dict] = []
    for upload in files:
        content = await upload.read()
        saved.append(
            attachments.save_attachment(
                task.workspace_path,
                subdir=subdir,
                filename=upload.filename or "file",
                content=content,
                media_type=upload.content_type or "application/octet-stream",
            )
        )
    return saved


@router.post("/tasks/{task_id}/attachments")
async def upload_task_attachments(
    task_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    return await _save_uploads(task, files, attachments.CHAT_DIR)


@router.delete("/tasks/{task_id}/attachments/{attachment_id}")
async def delete_task_attachment(
    task_id: str, attachment_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    ok = attachments.delete_attachment(
        task.workspace_path, subdir=attachments.CHAT_DIR, att_id=attachment_id
    )
    return {"ok": ok}


@router.post("/tasks/{task_id}/comment-attachments")
async def upload_comment_attachments(
    task_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    return await _save_uploads(task, files, attachments.COMMENT_DIR)


@router.delete("/tasks/{task_id}/comment-attachments")
async def delete_comment_attachment(
    task_id: str, request: Request, path: str = "", db: Session = Depends(get_db)
):
    _, err = auth_or_401(db, request)
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    # Comment attachments are addressed by their workspace-relative path; derive
    # the upload id (``_notes/<id>/<file>``) so the whole folder is removed.
    parts = (path or "").strip("/").split("/")
    att_id = parts[1] if len(parts) >= 2 and parts[0] == attachments.COMMENT_DIR else ""
    ok = attachments.delete_attachment(
        task.workspace_path, subdir=attachments.COMMENT_DIR, att_id=att_id
    )
    return {"ok": ok}
