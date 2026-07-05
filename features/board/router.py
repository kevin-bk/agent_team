"""REST API for the board feature (boards and tasks)."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from agent_team.features.board import attachments, authz
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
    JiraPreviewBody,
    JiraPreviewItem,
    JiraPreviewResponse,
    JiraSyncBody,
    JournalEntryCreate,
    LoopAttemptDTO,
    LoopEvaluationDTO,
    LoopInfoDTO,
    LoopResumeCreate,
    LoopTaskDTO,
    MentionCreate,
    MentionResponse,
    PlanningAnswerCreate,
    PlanningArtifactDTO,
    PlanningArtifactEdit,
    PlanningInfoDTO,
    PlanningQuestionDTO,
    PlanningRunCreate,
    PlanningStartCreate,
    SkillPackDTO,
    TaskCreate,
    TaskMove,
    TaskScheduleHistoryItem,
    TaskScheduleUpdate,
    TaskUpdate,
    TypingBody,
)
from agent_team.web import API_PREFIX, auth_or_401, bad_request, not_found
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
    is_admin = _is_admin(user)
    # Only surface boards the caller can actually access (owner/member/admin).
    visible: list[tuple] = []
    for b in boards_repo.list_boards(db):
        role = members_repo.access_role(db, b, user_id=user.id, is_admin=is_admin)
        if role is not None:
            visible.append((b, role))
    counts = boards_repo.task_counts_by_board(db, [b.id for b, _ in visible])
    return [
        boards_repo.serialize_board(
            b, task_count=counts.get(b.id, 0), my_role=role
        )
        for b, role in visible
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
    board = ctx.board
    counts = boards_repo.task_counts_by_board(db, [board.id])
    return boards_repo.serialize_board(
        board, task_count=counts.get(board.id, 0), my_role=ctx.role
    )


@router.patch("/boards/{board_id}")
async def update_board(
    board_id: str, payload: BoardUpdate, request: Request, db: Session = Depends(get_db)
):
    # Board configuration (settings, agents, Jira, autopilot wiring, archive) is
    # owner-only.
    ctx, err = authz.guard_board(db, request, board_id, min_role="owner")
    if err:
        return err
    board = ctx.board
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
    if payload.agent_mcp is not None:
        from agent_team.features.board.runtime.direct_acp import known_cli_aliases

        known = known_cli_aliases()
        cleaned: dict[str, dict] = {}
        for alias, cfg in payload.agent_mcp.items():
            if alias not in known:
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"unknown CLI target for MCP: {alias}"},
                )
            if not isinstance(cfg, dict):
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"MCP config for {alias} must be an object"},
                )
            servers = cfg.get("mcpServers", {})
            if not isinstance(servers, dict):
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"mcpServers for {alias} must be an object"},
                )
            # Drop aliases configured with no servers so storage stays tidy.
            if servers:
                cleaned[alias] = {"mcpServers": servers}
        board.agent_mcp_json = json.dumps(cleaned)
    if payload.starter_prompt is not None:
        board.starter_prompt = payload.starter_prompt.strip()
    if payload.planning_conventions is not None:
        board.planning_conventions = payload.planning_conventions.strip()
    if payload.planning_skill is not None:
        from agent_team.features.board.runtime import skills as skills_rt

        planning_skill = payload.planning_skill.strip()
        if planning_skill:
            known = {p["name"] for p in skills_rt.list_available_packs()}
            if planning_skill not in known:
                return JSONResponse(
                    status_code=422,
                    content={"detail": f"unknown planning skill pack: {planning_skill}"},
                )
        board.planning_skill = planning_skill
    if payload.planning_auto_approve_quick is not None:
        board.planning_auto_approve_quick = bool(payload.planning_auto_approve_quick)
    if payload.runtime_profile is not None:
        from agent_team.features.board.runtime.sandbox.config import validate_overlay

        cleaned_rt, rt_err = validate_overlay(payload.runtime_profile)
        if rt_err:
            return JSONResponse(status_code=422, content={"detail": rt_err})
        board.runtime_profile_json = json.dumps(cleaned_rt)
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
    if payload.jira_sync_status is not None:
        board.jira_sync_status = payload.jira_sync_status
    if "jira_api_token" in fields_set:
        # Omit the field to keep the stored token; send "" to clear it.
        token = (payload.jira_api_token or "").strip()
        board.jira_api_token = token or None
    db.commit()
    db.refresh(board)
    counts = boards_repo.task_counts_by_board(db, [board.id])
    return boards_repo.serialize_board(
        board, task_count=counts.get(board.id, 0), my_role=ctx.role
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.get("/boards/{board_id}/tasks")
async def list_tasks(board_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err
    return _create_task(db, board=ctx.board, payload=payload, actor_id=ctx.user.id)


@router.post("/tasks")
async def create_task_flat(
    payload: TaskCreate, request: Request, db: Session = Depends(get_db)
):
    if not payload.board_id:
        return JSONResponse(status_code=400, content={"detail": "board_id is required"})
    ctx, err = authz.guard_board(db, request, payload.board_id, min_role="editor")
    if err:
        return err
    return _create_task(db, board=ctx.board, payload=payload, actor_id=ctx.user.id)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    ctx, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    return tasks_repo.serialize_task(ctx.task)


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str, payload: TaskUpdate, request: Request, db: Session = Depends(get_db)
):
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task

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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task
    board = ctx.board
    if not board.jira_enabled:
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
    board_id: str,
    request: Request,
    payload: JiraPreviewBody | None = Body(default=None),
    db: Session = Depends(get_db),
):
    """List issues for import, flagging which already exist as tasks.

    Without a body, queries the configured Jira project (newest first, capped at
    ``_JIRA_PREVIEW_LIMIT``). When ``jira_keys`` is supplied, previews exactly
    those issues (across any project) so the user can confirm before importing.
    Each row is marked *new* or *update* depending on whether a task is linked.
    """
    ctx, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err
    board = ctx.board
    if not board.jira_enabled:
        return JSONResponse(
            status_code=422,
            content={"detail": "Jira sync is not enabled for this board"},
        )

    # Normalise any explicit keys (deduped, upper-cased, capped).
    keys: list[str] = []
    if payload and payload.jira_keys:
        seen: set[str] = set()
        for raw in payload.jira_keys:
            k = (raw or "").strip().upper()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
        keys = keys[:_JIRA_PREVIEW_LIMIT]

    project_key = (board.jira_project_key or "").strip()
    if not keys and not project_key:
        return JSONResponse(
            status_code=422,
            content={"detail": "Set a Jira project key first"},
        )

    try:
        client = jira_service.build_client(board)
        if keys:
            jql = jira_service.build_keys_jql(keys)
        else:
            jql = jira_service.build_search_jql(
                project_key, board.jira_sync_filter()
            )
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    board = ctx.board
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    board = ctx.board
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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task
    board = ctx.board
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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
    board = ctx.board
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err
    board = ctx.board
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    board = ctx.board
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
    ctx, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    task = ctx.task
    from agent_team.features.repos.task_copy import list_task_repo_dirs

    return list_task_repo_dirs(db, task)


@router.get("/tasks/{task_id}/runtime")
async def get_task_runtime(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Read-only runtime snapshot for a task (provider, isolation, sandbox state)."""
    ctx, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    from agent_team.features.board.runtime.sandbox.service import describe_runtime

    task = ctx.task
    return describe_runtime(task_id=task.id, board_id=task.board_id)


# NOTE: registered BEFORE ``/runtime/{action}`` — FastAPI matches in
# registration order, so the catch-all would otherwise swallow ``exec``.
@router.post("/tasks/{task_id}/runtime/exec")
async def exec_task_runtime(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    """Run one shell command in the task's live sandbox (manual debugging).

    Editor+; requires a warm sandbox (never opens one). This is a one-off
    command runner, not an interactive shell.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    command = str(body.get("command") or "")
    timeout = body.get("timeout_seconds")
    from agent_team.features.board.runtime.sandbox import service as sandbox_service

    result = await sandbox_service.exec_in_task_sandbox(
        task.id,
        command,
        timeout_seconds=float(timeout) if timeout else None,
    )
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"detail": result.get("error")})
    return result


@router.post("/tasks/{task_id}/runtime/{action}")
async def control_task_runtime(
    task_id: str, action: str, request: Request, db: Session = Depends(get_db)
):
    """Manually ``pause`` or ``kill`` a task's sandbox from the cockpit.

    Refused with 409 while any run is queued/running so a live CLI agent is never
    torn down mid-turn. ``pause`` suspends (resumable next turn); ``kill`` discards
    the environment (reprovisioned from scratch next turn).
    """
    if action not in ("pause", "kill"):
        return JSONResponse(status_code=404, content={"detail": "unknown runtime action"})
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task
    if runs_repo.has_active_run(db, task.id):
        return JSONResponse(
            status_code=409,
            content={"detail": "an agent is currently running on this task"},
        )
    from agent_team.features.board.runtime.sandbox import service as sandbox_service

    if action == "pause":
        await sandbox_service.pause_task_sandbox(task.id)
    else:
        await sandbox_service.kill_task_sandbox(task.id)
    return sandbox_service.describe_runtime(task_id=task.id, board_id=task.board_id)


# ---------------------------------------------------------------------------
# Admin: sandboxes overview (manage + analytics)
# ---------------------------------------------------------------------------


@router.get("/admin/sandboxes")
async def admin_list_sandboxes(request: Request, db: Session = Depends(get_db)):
    """Admin-only overview of every sandbox (tracked, persisted, orphans)."""
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return JSONResponse(status_code=403, content={"detail": "Admin only"})
    from agent_team.features.board.runtime.sandbox.admin import (
        list_sandboxes_overview,
    )

    return await list_sandboxes_overview()


# Registered BEFORE ``/{action}`` so the catch-all doesn't swallow ``exec``.
@router.post("/admin/sandboxes/{sandbox_id}/exec")
async def admin_sandbox_exec(
    sandbox_id: str, request: Request, db: Session = Depends(get_db)
):
    """Admin-only: run one shell command in a tracked, open sandbox by id."""
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return JSONResponse(status_code=403, content={"detail": "Admin only"})
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    command = str(body.get("command") or "")
    timeout = body.get("timeout_seconds")
    from agent_team.features.board.runtime.sandbox.admin import sandbox_admin_exec

    result = await sandbox_admin_exec(
        sandbox_id, command, timeout_seconds=float(timeout) if timeout else None
    )
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"detail": result.get("error")})
    return result


@router.post("/admin/sandboxes/{sandbox_id}/{action}")
async def admin_sandbox_action(
    sandbox_id: str, action: str, request: Request, db: Session = Depends(get_db)
):
    """Admin-only ``pause``/``kill`` for any sandbox by id (incl. orphans)."""
    if action not in ("pause", "kill"):
        return JSONResponse(status_code=404, content={"detail": "unknown action"})
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return JSONResponse(status_code=403, content={"detail": "Admin only"})
    from agent_team.features.board.runtime.sandbox.admin import sandbox_admin_action

    result = await sandbox_admin_action(sandbox_id, action)
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"detail": result.get("error")})
    return result


@router.post("/tasks/{task_id}/repos/prepare")
async def prepare_task_repos_endpoint(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    from agent_team.features.repos.task_copy import prepare_task_repos_by_id

    prepared = await asyncio.to_thread(prepare_task_repos_by_id, task_id)
    return {"prepared": prepared}


@router.post("/tasks/{task_id}/repos/reset")
async def reset_task_repos_endpoint(
    task_id: str, request: Request, db: Session = Depends(get_db)
):
    """Re-prepare a task's repo copies: pull canonical, then re-clone from scratch.

    Destructive — discards un-pushed work in the old copy. Used to refresh a
    long-lived task whose copy has drifted behind the default branch.
    """
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    from agent_team.features.repos.task_copy import reset_task_repos_by_id

    prepared = await asyncio.to_thread(reset_task_repos_by_id, task_id)
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
    _, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
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
    ctx, err = authz.guard_board(db, request, board_id, min_role="owner")
    if err:
        return err
    board = ctx.board

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
    _, err = authz.guard_board(db, request, board_id, min_role="editor")
    if err:
        return err

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
    _, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err

    from datetime import datetime

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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    board = ctx.board

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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err

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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task

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


@router.get("/tasks/{task_id}/loop")
async def get_task_loop(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Snapshot of a task's autonomous loop: state + attempts with verdicts.

    Drives the cockpit's loop panel — the live state, the human-review banner,
    and the attempt/evaluation timeline all read from here (refreshed on each
    ``loop.status`` board event).
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    task = ctx.task

    from agent_team.features.board.models import (
        RUN_ROLE_EVALUATOR,
        RUN_ROLE_GENERATOR,
        RUN_ROLE_PLANNER,
    )
    from agent_team.features.board.repositories import attempts as attempts_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime.loop.service import is_loop_running

    attempts = attempts_repo.list_attempts_for_task(db, task_id)
    evaluations = attempts_repo.list_evaluations_for_task(db, task_id)
    # Link each iteration to the generator run that did its work + the
    # conversation holding the transcript, so the cockpit can embed it inline.
    gen_run_by_attempt = {
        a.id: runs_repo.get_attempt_run(
            db, attempt_id=a.id, role=RUN_ROLE_GENERATOR
        )
        for a in attempts
    }
    # The critic (evaluator) runs in its own fresh conversation per iteration; it
    # is tagged with the attempt it graded, so its verification transcript can be
    # shown next to the verdict rather than the generator's build transcript.
    crit_run_by_attempt = {
        a.id: runs_repo.get_attempt_run(
            db, attempt_id=a.id, role=RUN_ROLE_EVALUATOR
        )
        for a in attempts
    }

    by_attempt: dict[str, list] = {}
    for ev in evaluations:
        crit = crit_run_by_attempt.get(ev.attempt_id)
        by_attempt.setdefault(ev.attempt_id, []).append(
            LoopEvaluationDTO(
                id=ev.id,
                attempt_id=ev.attempt_id,
                run_id=ev.run_id,
                verdict=ev.verdict,
                score=ev.score,
                missing=ev.missing,
                evidence=ev.evidence(),
                conversation_id=crit.conversation_id if crit is not None else None,
                created_at=ev.created_at.isoformat() if ev.created_at else None,
            )
        )
    # The generator reuses one conversation across all iterations, so any
    # iteration's generator run points at the same continuous transcript; take
    # the most recent one available. Carry its agent alias too, so the cockpit
    # can name the builder.
    generator_run = next(
        (
            run
            for a in reversed(attempts)
            if (run := gen_run_by_attempt.get(a.id)) is not None
        ),
        None,
    )
    generator_conversation_id = (
        generator_run.conversation_id if generator_run is not None else None
    )
    critic_run = next(
        (
            run
            for a in reversed(attempts)
            if (run := crit_run_by_attempt.get(a.id)) is not None
        ),
        None,
    )
    planner_run = runs_repo.get_latest_task_run_by_role(
        db, task_id=task_id, role=RUN_ROLE_PLANNER
    )
    active_run = runs_repo.get_active_loop_run(db, task_id=task_id)
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    graph_tasks = [
        LoopTaskDTO(
            id=t["id"],
            title=t["title"],
            status=t["status"],
            depends_on=t["depends_on"],
        )
        for t in artifacts.task_list(task.workspace_path)
    ]
    return LoopInfoDTO(
        task_id=task_id,
        execution_mode=task.execution_mode or "chat",
        loop_state=task.loop_state,
        objective=task.objective,
        is_running=is_loop_running(task_id),
        generator_conversation_id=generator_conversation_id,
        planner_conversation_id=(
            planner_run.conversation_id if planner_run is not None else None
        ),
        planner_run_id=planner_run.id if planner_run is not None else None,
        planner_agent_id=planner_run.agent_alias if planner_run is not None else None,
        generator_agent_id=(
            generator_run.agent_alias if generator_run is not None else None
        ),
        evaluator_agent_id=critic_run.agent_alias if critic_run is not None else None,
        active_run_id=active_run.id if active_run is not None else None,
        active_conversation_id=(
            active_run.conversation_id if active_run is not None else None
        ),
        active_role=active_run.role if active_run is not None else None,
        active_agent_id=active_run.agent_alias if active_run is not None else None,
        can_resume=(
            bool(task.planning_meta().get("run_params"))
            and (task.loop_state or "") in _RESUMABLE_STATES
            and not is_loop_running(task_id)
        ),
        attempts=[
            LoopAttemptDTO(
                id=a.id,
                attempt_no=a.attempt_no,
                status=a.status,
                outcome=a.outcome,
                created_at=a.created_at.isoformat() if a.created_at else None,
                ended_at=a.ended_at.isoformat() if a.ended_at else None,
                run_id=(gr.id if (gr := gen_run_by_attempt.get(a.id)) else None),
                conversation_id=(
                    gr.conversation_id
                    if (gr := gen_run_by_attempt.get(a.id))
                    else None
                ),
                critic_run_id=(
                    cr.id if (cr := crit_run_by_attempt.get(a.id)) else None
                ),
                critic_conversation_id=(
                    cr.conversation_id
                    if (cr := crit_run_by_attempt.get(a.id))
                    else None
                ),
                evaluations=by_attempt.get(a.id, []),
            )
            for a in attempts
        ],
        tasks=graph_tasks,
    )


@router.post("/tasks/{task_id}/loop/cancel")
async def cancel_task_loop(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Ask a running loop to stop after its current attempt."""
    _ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    from agent_team.features.board.runtime.loop.service import cancel_loop

    return {"ok": cancel_loop(task_id), "task_id": task_id}


@router.post("/tasks/{task_id}/loop/ack")
async def ack_task_loop(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Acknowledge a finished loop, clearing its state from the cockpit.

    Used after a human has reviewed a ``waiting_for_human``/``complete``/
    ``failed`` outcome; refuses while a loop is still running.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    from agent_team.features.board.runtime.loop import human_actions

    try:
        human_actions.ack_loop(db, ctx.task, ctx.user)
    except human_actions.ActionError as e:
        return bad_request(str(e))
    return {"ok": True, "task_id": task_id}


#: Loop states a stopped run can be resumed from, with a human-readable reason
#: woven into the resume preamble so the agent knows why it's picking back up.
_RESUMABLE_STATES: dict[str, str] = {
    "waiting_for_human": "the run stopped for human review (e.g. stalled at 0%, "
    "hit the attempt cap, or exceeded its budget)",
    "failed": "the run failed",
    "cancelled": "the run was cancelled",
}


@router.post("/tasks/{task_id}/loop/resume")
async def resume_task_loop(
    task_id: str,
    payload: LoopResumeCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Resume a stopped loop from where it left off — not a fresh restart.

    Reuses the run parameters remembered at approve-and-run time and continues
    the same execution: completed tasks in ``TASKS.json`` are skipped, so the
    loop picks up the first unfinished one. ``agent_id`` / ``evaluator_id`` may
    override the builder / critic for this resume (e.g. to swap off a rate-limited
    engine). The resumed generator is handed a short preamble that re-grounds it
    in the approved artifacts rather than the whole original objective.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task

    from agent_team.features.board.runtime.loop.service import is_loop_running

    if is_loop_running(task_id):
        return bad_request("A loop is already running for this task.")

    meta = task.planning_meta()
    rp = meta.get("run_params")
    if not rp:
        return bad_request(
            "No previous run to resume — approve and run the plan first."
        )
    reason = _RESUMABLE_STATES.get(task.loop_state or "")
    if reason is None:
        return bad_request(
            "This task has no stopped run to resume "
            f"(state: {task.loop_state or 'none'})."
        )

    # Optional agent swap (e.g. off a rate-limited engine). Persist the override
    # into run_params so the cockpit and any later resume use the new agents.
    agent_alias = (payload.agent_id or "").strip() or rp["agent_id"]
    evaluator_alias = (payload.evaluator_id or "").strip() or rp["evaluator_id"]
    swapped = agent_alias != rp["agent_id"] or evaluator_alias != rp["evaluator_id"]
    rp["agent_id"] = agent_alias
    rp["evaluator_id"] = evaluator_alias
    meta["run_params"] = rp
    task.planning_meta_json = json.dumps(meta, ensure_ascii=False)

    from agent_team.features.board.runtime import task_journal

    task_journal.record_with(
        db,
        task_id=task_id,
        phase="execution",
        type="state_change",
        title="Execution resumed by human",
        actor_id=ctx.user.id,
        actor_type="human",
        metadata={
            "agent_id": agent_alias,
            "evaluator_id": evaluator_alias,
            "resumed_from": task.loop_state,
            "agents_swapped": swapped,
        },
    )
    db.commit()

    from agent_team.features.board.runtime.dispatch import capture_main_loop
    from agent_team.features.board.runtime.loop import planning_prompts
    from agent_team.features.board.runtime.loop.budget import LoopBudget
    from agent_team.features.board.runtime.loop.service import start_autonomous_loop

    capture_main_loop()
    start_autonomous_loop(
        task_id=task_id,
        agent_alias=agent_alias,
        evaluator_alias=evaluator_alias,
        objective=task.objective or "",
        max_attempts=int(rp.get("max_attempts", 10)),
        budget=LoopBudget(
            max_tokens=rp.get("max_tokens"),
            max_cost_usd=rp.get("max_cost_usd"),
            max_wall_seconds=rp.get("max_wall_seconds"),
        ),
        strict=True,
        task_graph=bool(rp.get("task_graph", True)),
        resume_note=planning_prompts.build_resume_preamble(reason),
    )
    return {"ok": True, "task_id": task_id}


def _planning_info(task) -> PlanningInfoDTO:
    """Build the planning snapshot DTO from a task + its on-disk artifacts."""
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
    from agent_team.features.board.runtime.loop.planning import is_planning_running

    meta = task.planning_meta()
    dtos: list[PlanningArtifactDTO] = []
    # Editable artifacts plus the change-request note carry their text so the
    # cockpit can render/edit them (the note is read-only, shown as an alert).
    readable = set(artifacts.EDITABLE_ARTIFACTS.values()) | {
        artifacts.PLAN_CHANGE_REQUEST_PATH,
        artifacts.QUESTIONS_PATH,
    }
    for m in artifacts.all_metadata(task.workspace_path):
        content = (
            artifacts.read_text(task.workspace_path, m.path)
            if (m.exists and m.path in readable)
            else None
        )
        dtos.append(
            PlanningArtifactDTO(
                path=m.path,
                exists=m.exists,
                etag=m.etag,
                size=m.size,
                updated_at=m.updated_at,
                content=content,
            )
        )
    # Lane comes from the on-disk intake (source of truth, survives restarts);
    # auto_approved from the approval metadata stamped at planning time.
    lane_info = artifacts.intake_lane(task.workspace_path)
    return PlanningInfoDTO(
        task_id=task.id,
        loop_state=task.loop_state,
        planning_mode=task.planning_mode,
        objective=task.objective,
        is_planning=is_planning_running(task.id),
        approved=bool(meta.get("approved")),
        approved_by=meta.get("approved_by"),
        approved_at=meta.get("approved_at"),
        review_verdict=meta.get("review_verdict"),
        lane=lane_info.lane,
        lane_hard_gates=list(lane_info.hard_gates),
        auto_approved=bool(meta.get("auto_approved")),
        last_error=meta.get("last_error"),
        artifacts=dtos,
        questions=[
            PlanningQuestionDTO(
                id=q["id"],
                question=q["question"],
                reason=q["reason"],
                blocking=q["blocking"],
                options=q["options"],
                answer=q["answer"],
            )
            for q in artifacts.read_questions(task.workspace_path)
        ],
    )


@router.post("/tasks/{task_id}/planning/start")
async def start_task_planning(
    task_id: str, payload: PlanningStartCreate, request: Request, db: Session = Depends(get_db)
):
    """Run the strict planning phase: draft artifacts, then park for approval.

    This does not start execution. The planner (and optional reviewer) write the
    SPEC/PLAN/TASKS artifacts; the task ends at ``waiting_plan_approval`` for a
    human to review, edit and approve. No process is kept alive after drafting.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task

    from agent_team.features.board.models import (
        PLANNING_MODE_STRICT,
        TASK_EXEC_MODE_AUTONOMOUS,
    )
    from agent_team.features.board.runtime.loop.service import is_loop_running

    if is_loop_running(task_id):
        return bad_request("A loop is running — cancel it before planning.")

    objective = (payload.objective or task.objective or task.description or "").strip()
    task.objective = objective
    task.execution_mode = TASK_EXEC_MODE_AUTONOMOUS
    task.planning_mode = PLANNING_MODE_STRICT

    from agent_team.features.board.runtime import task_journal

    task_journal.record_with(
        db,
        task_id=task_id,
        phase="planning",
        type="state_change",
        title="Planning started",
        body=objective,
        actor_id=ctx.user.id,
        actor_type="human",
        metadata={"planner_id": payload.planner_id, "reviewer_id": payload.reviewer_id or None},
    )
    db.commit()

    from agent_team.features.board.runtime.dispatch import capture_main_loop
    from agent_team.features.board.runtime.loop.planning import start_planning_job

    capture_main_loop()
    start_planning_job(
        task_id=task_id,
        planner_alias=payload.planner_id,
        objective=objective,
        reviewer_alias=payload.reviewer_id or None,
    )
    return {"ok": True, "task_id": task_id}


@router.get("/tasks/{task_id}/planning")
async def get_task_planning(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Snapshot of a task's strict planning phase: state, approval, artifacts."""
    ctx, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    return _planning_info(ctx.task)


@router.put("/tasks/{task_id}/planning/artifacts/{artifact_name}")
async def edit_task_planning_artifact(
    task_id: str,
    artifact_name: str,
    payload: PlanningArtifactEdit,
    request: Request,
    db: Session = Depends(get_db),
):
    """Replace an editable artifact's content, guarded by an ``If-Match`` etag.

    Only allowed while the plan is awaiting approval (the agent must not be
    writing concurrently). Editing an approved artifact invalidates approval.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task

    from agent_team.features.board.models import PLANNING_MODE_STRICT
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
    from agent_team.features.board.runtime.loop.planning import is_planning_running
    from agent_team.features.board.runtime.loop.status import LoopState

    rel = artifacts.EDITABLE_ARTIFACTS.get(artifact_name)
    if rel is None:
        return bad_request(f"Artifact {artifact_name!r} is not editable.")
    if is_planning_running(task_id) or task.loop_state not in (
        LoopState.WAITING_PLAN_APPROVAL.value,
        LoopState.PLAN_APPROVED.value,
    ):
        return bad_request("Artifacts can only be edited while awaiting approval.")

    current = artifacts.read_text(task.workspace_path, rel) or ""
    if_match = request.headers.get("If-Match")
    if if_match and if_match != artifacts.etag(current):
        return JSONResponse(
            status_code=409,
            content={"detail": "Artifact changed since you loaded it; reload."},
        )
    if artifact_name == "TASKS.json":
        try:
            errors = artifacts.validate_tasks(json.loads(payload.content or "{}"))
        except json.JSONDecodeError as exc:
            return bad_request(f"TASKS.json is not valid JSON: {exc}")
        if errors:
            return bad_request("; ".join(errors))
    new_etag = artifacts.write_text(task.workspace_path, rel, payload.content)

    from agent_team.features.board.runtime import task_journal

    # A human edit invalidates a prior approval — it must be re-approved.
    invalidated = False
    if task.planning_mode == PLANNING_MODE_STRICT:
        meta = task.planning_meta()
        if meta.get("approved"):
            meta.update({"approved": False, "approved_by": None, "approved_at": None})
            task.planning_meta_json = json.dumps(meta, ensure_ascii=False)
            invalidated = True
        if task.loop_state == LoopState.PLAN_APPROVED.value:
            task.loop_state = LoopState.WAITING_PLAN_APPROVAL.value
        task_journal.record_with(
            db,
            task_id=task_id,
            phase="planning",
            type="artifact_update",
            title=f"Edited {artifact_name}"
            + (" (approval invalidated)" if invalidated else ""),
            actor_id=ctx.user.id,
            actor_type="human",
            severity="warning" if invalidated else "info",
            refs=task_journal.refs(artifacts=[rel]),
            metadata={"etag": new_etag},
        )
        db.commit()
    return {"ok": True, "path": rel, "etag": new_etag}


def _approve_plan(ctx, db) -> object | None:
    """Validate artifacts and stamp approval metadata; return an error or None."""
    from agent_team.features.board.runtime.loop import human_actions

    try:
        human_actions.approve_plan(db, ctx.task, ctx.user)
    except human_actions.ActionError as e:
        return bad_request(str(e))
    return None


@router.post("/tasks/{task_id}/planning/approve")
async def approve_task_planning(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Approve the drafted plan (does not start execution)."""
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    approve_err = _approve_plan(ctx, db)
    if approve_err:
        return approve_err
    return _planning_info(ctx.task)


@router.post("/tasks/{task_id}/planning/request-changes")
async def request_task_planning_changes(
    task_id: str,
    request: Request,
    payload: PlanningStartCreate | None = Body(default=None),
    db: Session = Depends(get_db),
):
    """Clear approval and re-draft the plan with the remembered planner.

    A human comment can refine the objective; the planner re-runs and the task
    returns to ``waiting_plan_approval``.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task

    meta = task.planning_meta()
    planner_id = (payload.planner_id if payload else None) or meta.get("planner_id")
    if not planner_id:
        return bad_request("No planner is set for this task; start planning first.")
    reviewer_id = (payload.reviewer_id if payload else None) or meta.get("reviewer_id")
    feedback = (payload.objective if payload else None) or ""

    meta.update({"approved": False, "approved_by": None, "approved_at": None})
    task.planning_meta_json = json.dumps(meta, ensure_ascii=False)

    from agent_team.features.board.runtime import task_journal

    task_journal.record_with(
        db,
        task_id=task_id,
        phase="review",
        type="plan_review",
        title="Human requested plan changes",
        body=feedback.strip(),
        actor_id=ctx.user.id,
        actor_type="human",
        severity="warning",
    )
    db.commit()

    # Re-planning supersedes any active change-request marker; archive it so a
    # later run does not re-trip the pause gate on the freshly drafted plan.
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    artifacts.archive_change_request(task.workspace_path)

    objective = task.objective or ""
    if feedback.strip():
        objective = f"{objective}\n\n## Requested changes\n{feedback.strip()}"

    from agent_team.features.board.runtime.dispatch import capture_main_loop
    from agent_team.features.board.runtime.loop.planning import start_planning_job

    capture_main_loop()
    start_planning_job(
        task_id=task_id,
        planner_alias=planner_id,
        objective=objective,
        reviewer_alias=reviewer_id or None,
        # The human explicitly asked for changes — they get the final look at
        # the re-draft even on a quick-lane auto-approve board.
        allow_auto_approve=False,
    )
    return {"ok": True, "task_id": task_id}


@router.post("/tasks/{task_id}/planning/approve-and-run")
async def approve_and_run_task_planning(
    task_id: str, payload: PlanningRunCreate, request: Request, db: Session = Depends(get_db)
):
    """Approve the plan and immediately start strict autonomous execution."""
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    task = ctx.task

    from agent_team.features.board.runtime.loop.service import is_loop_running
    from agent_team.features.board.runtime.loop.status import LoopState

    if is_loop_running(task_id):
        return bad_request("A loop is already running for this task.")

    # Approving out of a plan-change pause means the human revised the contract;
    # tell the resumed generator explicitly so it re-reads the updated plan
    # instead of re-filing the same change request (captured before _approve_plan
    # clears the state).
    was_plan_change = task.loop_state == LoopState.PLAN_CHANGE_REQUESTED.value

    approve_err = _approve_plan(ctx, db)
    if approve_err:
        return approve_err

    # Remember the run parameters so an execution-phase question pause can resume
    # the loop with the same agents/budget once the human answers.
    meta = task.planning_meta()
    meta["run_params"] = {
        "agent_id": payload.agent_id,
        "evaluator_id": payload.evaluator_id,
        "task_graph": payload.task_graph,
        "max_attempts": payload.max_attempts,
        "max_tokens": payload.max_tokens,
        "max_cost_usd": payload.max_cost_usd,
        "max_wall_seconds": payload.max_wall_seconds,
    }
    task.planning_meta_json = json.dumps(meta, ensure_ascii=False)

    from agent_team.features.board.runtime import task_journal

    task_journal.record_with(
        db,
        task_id=task_id,
        phase="execution",
        type="state_change",
        title="Execution resumed after plan change" if was_plan_change else "Execution started",
        actor_id=ctx.user.id,
        actor_type="human",
        metadata={
            "agent_id": payload.agent_id,
            "evaluator_id": payload.evaluator_id,
            "task_graph": payload.task_graph,
            "max_attempts": payload.max_attempts,
            "resumed_from_plan_change": was_plan_change,
        },
    )
    db.commit()

    from agent_team.features.board.runtime.dispatch import capture_main_loop
    from agent_team.features.board.runtime.loop import planning_prompts
    from agent_team.features.board.runtime.loop.budget import LoopBudget
    from agent_team.features.board.runtime.loop.service import start_autonomous_loop

    capture_main_loop()
    start_autonomous_loop(
        task_id=task_id,
        agent_alias=payload.agent_id,
        evaluator_alias=payload.evaluator_id,
        objective=task.objective or "",
        max_attempts=payload.max_attempts,
        budget=LoopBudget(
            max_tokens=payload.max_tokens,
            max_cost_usd=payload.max_cost_usd,
            max_wall_seconds=payload.max_wall_seconds,
        ),
        strict=True,
        task_graph=payload.task_graph,
        resume_note=planning_prompts.PLAN_REVISED_NOTE if was_plan_change else None,
    )
    return {"ok": True, "task_id": task_id}


@router.post("/tasks/{task_id}/planning/answer")
async def answer_task_planning(
    task_id: str,
    payload: PlanningAnswerCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Answer an agent's blocking questions, then resume the paused phase.

    Persists the answers into ``QUESTIONS.json``, archives it (clearing the
    gate), and resumes: a planning-phase pause re-plans with the answers in
    context; an execution-phase pause restarts the loop with the remembered run
    parameters. The answered Q&A (plus an optional note) is injected into the
    agent's prompt so it proceeds with the human's decisions.
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err

    from agent_team.features.board.runtime.loop import human_actions

    try:
        resumed = human_actions.answer_questions(
            db, ctx.task, ctx.user, answers=payload.answers, note=payload.note
        )
    except human_actions.ActionError as e:
        return bad_request(str(e))
    return {"ok": True, "task_id": task_id, "resumed": resumed}


@router.get("/boards/{board_id}/frictions")
async def list_board_frictions(
    board_id: str,
    request: Request,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Friction signals across the whole board, newest first.

    Friction = a "this was harder than it should have been" note (missing tests,
    stale docs, ambiguous scope, a repeated manual step). Agents log them as they
    work and the loop auto-emits one when a task is capped/budget-blocked. This is
    a read-only tracking list; a human reviews and acts on it.
    """
    _, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
    from agent_team.features.board.repositories import journal as journal_repo

    rows = journal_repo.list_board_friction(db, board_id, limit=limit)
    return [journal_repo.serialize_board_friction(e, t) for e, t in rows]


@router.get("/tasks/{task_id}/journal")
async def list_task_journal(
    task_id: str,
    request: Request,
    type: str | None = None,
    phase: str | None = None,
    severity: str | None = None,
    after_seq: int | None = None,
    before_seq: int | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """The task's semantic journal: an append-only timeline of key moments.

    Optional ``type``/``phase``/``severity`` filter the entries; ``after_seq``/
    ``before_seq`` page through them (entries come back in ascending ``seq``).
    """
    ctx, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    from agent_team.features.board.repositories import journal as journal_repo

    entries = journal_repo.list_entries(
        db,
        task_id,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
        type=type,
        phase=phase,
        severity=severity,
    )
    return [journal_repo.serialize_entry(e) for e in entries]


@router.post("/tasks/{task_id}/journal")
async def add_task_journal_note(
    task_id: str,
    payload: JournalEntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Append a manual human note to the task journal."""
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    from agent_team.features.board.models import JOURNAL_ACTOR_HUMAN
    from agent_team.features.board.repositories import journal as journal_repo

    entry = journal_repo.append_entry(
        db,
        task_id=task_id,
        actor_type=JOURNAL_ACTOR_HUMAN,
        actor_id=ctx.user.id,
        phase="system",
        type=payload.type,
        title=payload.title,
        body=payload.body,
        severity=payload.severity,
        refs=payload.refs,
    )
    db.commit()
    db.refresh(entry)
    return journal_repo.serialize_entry(entry)


@router.get("/tasks/{task_id}/runs")
async def list_runs(
    task_id: str, request: Request, agent_id: str | None = None, db: Session = Depends(get_db)
):
    """Runs for a task, optionally narrowed to one agent (``?agent_id=``)."""
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    runs = runs_repo.list_runs_for_task(db, task_id, agent_alias=agent_id)
    return [runs_repo.serialize_run(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, db: Session = Depends(get_db)):
    run, _ctx, err = authz.guard_run(db, request, run_id, min_role="viewer")
    if err:
        return err
    return runs_repo.serialize_run(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request, db: Session = Depends(get_db)):
    _run, _ctx, err = authz.guard_run(db, request, run_id, min_role="editor")
    if err:
        return err
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
    _run, _ctx, err = authz.guard_run(db, request, run_id, min_role="viewer")
    if err:
        return err
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
        _run, _ctx, err = authz.guard_run(db, request, run_id, min_role="viewer")
        if err:
            return err
    finally:
        db.close()

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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task
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
    comment, ctx, err = authz.guard_comment(db, request, comment_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task
    if comment.task_id != task_id or comment.deleted_at is not None:
        return not_found("Comment not found")
    if comment.author_id != user.id and not authz.is_admin(user):
        return JSONResponse(
            status_code=403, content={"detail": "Only the author can edit a note"}
        )
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
    comment, ctx, err = authz.guard_comment(db, request, comment_id, min_role="editor")
    if err:
        return err
    if comment.task_id != task_id or comment.deleted_at is not None:
        return not_found("Comment not found")
    # Author can delete their own; board owners (and admins) can delete any.
    if comment.author_id != ctx.user.id and ctx.role != "owner":
        return JSONResponse(
            status_code=403,
            content={"detail": "Only the author or a board owner can delete a note"},
        )
    return _soft_delete_comment_and_publish(db, comment)


@router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, request: Request, db: Session = Depends(get_db)):
    """Flat-path delete, kept for older bundled clients."""
    comment, ctx, err = authz.guard_comment(db, request, comment_id, min_role="editor")
    if err:
        return err
    if comment.deleted_at is not None:
        return not_found("Comment not found")
    if comment.author_id != ctx.user.id and ctx.role != "owner":
        return JSONResponse(
            status_code=403,
            content={"detail": "Only the author or a board owner can delete a note"},
        )
    return _soft_delete_comment_and_publish(db, comment)


# ---------------------------------------------------------------------------
# Activity changelog
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/activity")
async def list_activity(task_id: str, request: Request, db: Session = Depends(get_db)):
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
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
        _ctx, err = authz.guard_board(db, request, board_id, min_role="viewer")
        if err:
            return err
    finally:
        db.close()

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
    ctx, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
    board = ctx.board

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
    _, err = authz.guard_board(db, request, board_id, min_role="owner")
    if err:
        return err

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
    _, err = authz.guard_board(db, request, board_id, min_role="owner")
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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    attempts = conversations_repo.list_attempts(db, task_id=task_id, agent_alias=agent_id)
    return [conversations_repo.serialize_attempt(c) for c in attempts]


@router.post("/tasks/{task_id}/agents/{agent_id}/reset")
async def reset_thread(
    task_id: str, agent_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
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
    ctx, err = authz.guard_task(db, request, task_id, min_role="editor")
    if err:
        return err
    user = ctx.user
    task = ctx.task
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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
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
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
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
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
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
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
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
# Task code changes (git diff of the task's repo copies vs their base branch)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/changes")
async def task_changes(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Aggregated git changeset for every repo copy in the task workspace.

    Reflects on-disk truth (commits + staged/unstaged edits + untracked files)
    on the ``agent/<task-key>`` branch vs its base, so it covers any agent / run
    / direct-CLI push — unlike the thread-local, tool-call-derived "Changes" tab.
    """
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    from agent_team.features.repos import diff_service
    from agent_team.features.repos.task_copy import task_branch_name

    specs = diff_service.repo_specs(db, task)
    work_branch = task_branch_name(task)
    return await asyncio.to_thread(
        diff_service.compute_changes, task.workspace_path, work_branch, specs
    )


@router.get("/tasks/{task_id}/changes/diff")
async def task_change_diff(
    task_id: str,
    request: Request,
    repo: str,
    path: str,
    db: Session = Depends(get_db),
):
    """Old/new content for a single changed file (lazy-loaded per file)."""
    _, err = authz.guard_task(db, request, task_id, min_role="viewer")
    if err:
        return err
    task = _task_workspace(db, task_id)
    if task is None:
        return not_found("Task not found")
    from agent_team.features.repos import diff_service

    specs = diff_service.repo_specs(db, task)
    spec = next((s for s in specs if s["slug"] == repo), None)
    if spec is None:
        return not_found("Repo not assigned to this task")
    try:
        return await asyncio.to_thread(
            diff_service.compute_file_diff, task.workspace_path, spec, path
        )
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "invalid path"})


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
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
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
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
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
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
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
    _, err = authz.guard_task(db, request, task_id, min_role="editor")
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
