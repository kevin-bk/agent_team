"""Durable snapshots for approved goal contracts and their proof.

The active workspace is intentionally mutable: a re-plan overwrites SPEC/PLAN/
TASKS and a later goal keeps using the same repository copies. Goal-run rows are
the audit boundary. The approved plan snapshot is immutable; execution and
workspace snapshots are refreshed as that contract runs so the cockpit can
show both the live goal and historical completed goals.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.board.models import (
    AgentTeamAttempt,
    AgentTeamEvaluation,
    AgentTeamGoalRun,
    AgentTeamRun,
    AgentTeamTask,
    AgentTeamVerificationReceipt,
)
from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

PLAN_PATHS = (
    artifacts.SPEC_PATH,
    artifacts.PLAN_PATH,
    artifacts.TASKS_PATH,
    artifacts.INTAKE_PATH,
    artifacts.PLAN_REVIEW_PATH,
)

_ACTIVE_STATUSES = {
    "approved",
    "running",
    "waiting_answers",
    "waiting_for_human",
    "plan_change_requested",
}
_TERMINAL_OUTCOMES = {"complete", "failed", "cancelled"}
_MAX_SNAPSHOT_DIFF_BYTES = 2_000_000
_MAX_SNAPSHOT_FILE_BYTES = 300_000


def _loads(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _plan_snapshot(workspace_path: str) -> dict:
    rows: list[dict[str, Any]] = []
    for path in PLAN_PATHS:
        content = artifacts.read_text(workspace_path, path)
        if content is None:
            continue
        meta = artifacts.metadata(workspace_path, path)
        rows.append(
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "exists": True,
                "content": content,
                "etag": meta.etag,
                "size": meta.size,
                "updated_at": meta.updated_at,
            }
        )
    return {"version": 1, "artifacts": rows}


def _contract_etag(plan: dict) -> str:
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def ensure_approved_snapshot(
    db: Session, task: AgentTeamTask, *, approved_by: str | None
) -> AgentTeamGoalRun:
    """Create the immutable snapshot for the task's current approved contract.

    Repeated approve/approve-and-run calls for the same planning session and
    fingerprint reuse the row. A changed contract creates a new numbered row
    and preserves the prior row as ``superseded``.
    """

    meta = task.planning_meta()
    plan = _plan_snapshot(task.workspace_path)
    fingerprint = _contract_etag(plan)
    session_id = str(meta.get("planning_session_id") or "legacy")
    current = None
    current_id = str(meta.get("current_goal_run_id") or "")
    if current_id:
        current = db.get(AgentTeamGoalRun, current_id)
    if (
        current is not None
        and current.task_id == task.id
        and current.contract_etag == fingerprint
        and current.planning_meta().get("planning_session_id") == session_id
    ):
        return current

    if current is not None and current.status in _ACTIVE_STATUSES:
        current.status = "superseded"
        current.outcome = "superseded"
        current.completed_at = datetime.now(UTC)

    last_no = (
        db.query(func.max(AgentTeamGoalRun.run_no))
        .filter(AgentTeamGoalRun.task_id == task.id)
        .scalar()
    )
    approved_at = _parse_datetime(meta.get("approved_at")) or datetime.now(UTC)
    snapshot_meta = {
        **meta,
        "planning_session_id": session_id,
        "artifact_etags": meta.get("artifact_etags") or {},
    }
    row = AgentTeamGoalRun(
        task_id=task.id,
        run_no=int(last_no or 0) + 1,
        objective=task.objective or "",
        contract_etag=fingerprint,
        status="approved",
        plan_snapshot_json=json.dumps(plan, ensure_ascii=False),
        planning_meta_json=json.dumps(snapshot_meta, ensure_ascii=False),
        approved_by=approved_by,
        approved_at=approved_at,
    )
    db.add(row)
    db.flush()
    meta["current_goal_run_id"] = row.id
    task.planning_meta_json = json.dumps(meta, ensure_ascii=False)
    return row


def current_goal_run(db: Session, task: AgentTeamTask) -> AgentTeamGoalRun | None:
    current_id = str(task.planning_meta().get("current_goal_run_id") or "")
    row = db.get(AgentTeamGoalRun, current_id) if current_id else None
    if row is not None and row.task_id == task.id:
        return row
    return (
        db.query(AgentTeamGoalRun)
        .filter(AgentTeamGoalRun.task_id == task.id)
        .order_by(AgentTeamGoalRun.run_no.desc())
        .first()
    )


def mark_current_started(db: Session, task: AgentTeamTask) -> AgentTeamGoalRun | None:
    row = current_goal_run(db, task)
    if row is None:
        return None
    row.status = "running"
    row.outcome = None
    row.started_at = row.started_at or datetime.now(UTC)
    row.completed_at = None
    db.flush()
    return row


def mark_current_started_for_task(task_id: str) -> None:
    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        task = db.get(AgentTeamTask, task_id)
        if task is None:
            return
        mark_current_started(db, task)
        db.commit()
    finally:
        db.close()


def _execution_snapshot(db: Session, task: AgentTeamTask, row: AgentTeamGoalRun) -> dict:
    since = row.started_at or row.approved_at or row.created_at
    attempts = (
        db.query(AgentTeamAttempt)
        .filter(
            AgentTeamAttempt.task_id == task.id,
            AgentTeamAttempt.created_at >= since,
        )
        .order_by(AgentTeamAttempt.attempt_no.asc())
        .all()
    )
    attempt_ids = [a.id for a in attempts]
    evaluations = (
        db.query(AgentTeamEvaluation)
        .filter(AgentTeamEvaluation.attempt_id.in_(attempt_ids))
        .order_by(AgentTeamEvaluation.created_at.asc())
        .all()
        if attempt_ids
        else []
    )
    by_attempt: dict[str, list[dict]] = {}
    for evaluation in evaluations:
        by_attempt.setdefault(evaluation.attempt_id, []).append(
            {
                "id": evaluation.id,
                "run_id": evaluation.run_id,
                "verdict": evaluation.verdict,
                "score": evaluation.score,
                "missing": evaluation.missing,
                "evidence": evaluation.evidence(),
                "created_at": _iso(evaluation.created_at),
            }
        )
    receipts = (
        db.query(AgentTeamVerificationReceipt)
        .filter(
            AgentTeamVerificationReceipt.task_id == task.id,
            AgentTeamVerificationReceipt.created_at >= since,
        )
        .order_by(AgentTeamVerificationReceipt.created_at.asc())
        .all()
    )
    runs = (
        db.query(AgentTeamRun)
        .filter(AgentTeamRun.task_id == task.id, AgentTeamRun.created_at >= since)
        .order_by(AgentTeamRun.created_at.asc())
        .all()
    )
    # Planning normally finishes before approval, so it falls just outside the
    # execution time window. Preserve the latest planner transcript explicitly
    # so a historical goal still has its complete Plan → Build → Critic story.
    planner_run = (
        db.query(AgentTeamRun)
        .filter(
            AgentTeamRun.task_id == task.id,
            AgentTeamRun.role == "planner",
            AgentTeamRun.created_at <= (row.approved_at or datetime.now(UTC)),
        )
        .order_by(AgentTeamRun.created_at.desc())
        .first()
    )
    if planner_run is not None and all(run.id != planner_run.id for run in runs):
        runs.insert(0, planner_run)
    evidence = artifacts.read_json(task.workspace_path, artifacts.EVIDENCE_PATH) or {}
    final_tasks = artifacts.read_json(task.workspace_path, artifacts.TASKS_PATH) or {}
    return {
        "version": 1,
        "attempts": [
            {
                "id": attempt.id,
                "attempt_no": attempt.attempt_no,
                "status": attempt.status,
                "outcome": attempt.outcome,
                "created_at": _iso(attempt.created_at),
                "ended_at": _iso(attempt.ended_at),
                "evaluations": by_attempt.get(attempt.id, []),
            }
            for attempt in attempts
        ],
        "receipts": [
            {
                "id": receipt.id,
                "batch_id": receipt.batch_id,
                "command_id": receipt.command_id,
                "repo": receipt.repo_slug,
                "working_directory": receipt.working_directory,
                "command": receipt.command,
                "exit_code": receipt.exit_code,
                "duration_ms": receipt.duration_ms,
                "timed_out": receipt.timed_out,
                "stdout_path": receipt.stdout_path,
                "stderr_path": receipt.stderr_path,
                "created_at": _iso(receipt.created_at),
            }
            for receipt in receipts
        ],
        "roles": [
            {
                "run_id": run.id,
                "conversation_id": run.conversation_id,
                "attempt_id": run.attempt_id,
                "role": run.role,
                "agent_id": run.agent_alias,
                "status": run.status,
                "tokens": run.total_tokens,
                "cost_usd": run.cost_usd or 0,
            }
            for run in runs
            if run.role != "chat"
        ],
        "total_tokens": sum(run.total_tokens or 0 for run in runs),
        "total_cost_usd": sum(run.cost_usd or 0 for run in runs),
        "final_tasks": final_tasks,
        "evidence": evidence,
    }


def _trim_text(value: str, remaining: int) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    cap = max(0, min(remaining, _MAX_SNAPSHOT_FILE_BYTES))
    if len(raw) <= cap:
        return value, False
    return raw[:cap].decode("utf-8", "ignore"), True


def _workspace_snapshot(db: Session, task: AgentTeamTask) -> dict:
    from agent_team.features.repos import diff_service
    from agent_team.features.repos.task_copy import task_branch_name

    specs = diff_service.repo_specs(db, task)
    changes = diff_service.compute_changes(
        task.workspace_path, task_branch_name(task), specs
    )
    diffs: dict[str, dict] = {}
    remaining = _MAX_SNAPSHOT_DIFF_BYTES
    specs_by_slug = {spec["slug"]: spec for spec in specs}
    for entry in changes.get("files") or []:
        if remaining <= 0 or entry.get("binary"):
            continue
        spec = specs_by_slug.get(str(entry.get("repo") or ""))
        if spec is None:
            continue
        try:
            diff = diff_service.compute_file_diff(
                task.workspace_path, spec, str(entry.get("path") or "")
            )
        except (OSError, ValueError):
            continue
        original, o_truncated = _trim_text(str(diff.get("original") or ""), remaining)
        remaining -= len(original.encode("utf-8"))
        modified, m_truncated = _trim_text(str(diff.get("modified") or ""), remaining)
        remaining -= len(modified.encode("utf-8"))
        key = f"{entry.get('repo')}:{entry.get('path')}"
        diffs[key] = {
            **diff,
            "original": original,
            "modified": modified,
            "truncated": bool(diff.get("truncated") or o_truncated or m_truncated),
        }
    return {"version": 1, "changes": changes, "diffs": diffs}


def refresh_current_goal_run(task_id: str, *, outcome: str | None = None) -> None:
    """Refresh proof snapshots for the task's current goal run.

    Safe to call on pauses and terminal outcomes. A resumable pause remains the
    same goal run; genuinely terminal outcomes stamp ``completed_at``.
    """

    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        task = db.get(AgentTeamTask, task_id)
        if task is None:
            return
        row = current_goal_run(db, task)
        if row is None:
            return
        row.execution_snapshot_json = json.dumps(
            _execution_snapshot(db, task, row), ensure_ascii=False
        )
        row.workspace_snapshot_json = json.dumps(
            _workspace_snapshot(db, task), ensure_ascii=False
        )
        if outcome:
            row.outcome = outcome
            row.status = outcome
            if outcome in _TERMINAL_OUTCOMES:
                row.completed_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


def list_goal_runs(db: Session, task_id: str) -> list[AgentTeamGoalRun]:
    return (
        db.query(AgentTeamGoalRun)
        .filter(AgentTeamGoalRun.task_id == task_id)
        .order_by(AgentTeamGoalRun.run_no.desc())
        .all()
    )


def serialize_summary(row: AgentTeamGoalRun) -> dict:
    plan = row.plan_snapshot()
    workspace = row.workspace_snapshot()
    execution = row.execution_snapshot()
    changes = workspace.get("changes") if isinstance(workspace.get("changes"), dict) else {}
    return {
        "id": row.id,
        "task_id": row.task_id,
        "run_no": row.run_no,
        "objective": row.objective,
        "contract_etag": row.contract_etag,
        "status": row.status,
        "outcome": row.outcome,
        "approved_by": row.approved_by,
        "approved_at": _iso(row.approved_at),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "created_at": _iso(row.created_at),
        "artifact_count": len(plan.get("artifacts") or []),
        "changed_file_count": len(changes.get("files") or []),
        "receipt_count": len(execution.get("receipts") or []),
        "verdict": _latest_verdict(execution),
    }


def _latest_verdict(execution: dict) -> str | None:
    attempts = execution.get("attempts") if isinstance(execution, dict) else []
    for attempt in reversed(attempts or []):
        evaluations = attempt.get("evaluations") if isinstance(attempt, dict) else []
        if evaluations:
            return str(evaluations[-1].get("verdict") or "") or None
    return None


def serialize_detail(row: AgentTeamGoalRun) -> dict:
    return {
        **serialize_summary(row),
        "plan": row.plan_snapshot(),
        "planning_meta": row.planning_meta(),
        "execution": row.execution_snapshot(),
        "workspace": row.workspace_snapshot(),
    }
