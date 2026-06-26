"""Loop attempt + evaluation persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.board.models import (
    ATTEMPT_DONE,
    ATTEMPT_RUNNING,
    AgentTeamAttempt,
    AgentTeamEvaluation,
)


def open_attempt(db: Session, *, task_id: str) -> AgentTeamAttempt:
    """Open the next attempt for a task (``attempt_no`` is per-task monotonic)."""
    last = (
        db.query(func.max(AgentTeamAttempt.attempt_no))
        .filter(AgentTeamAttempt.task_id == task_id)
        .scalar()
    )
    attempt = AgentTeamAttempt(
        task_id=task_id,
        attempt_no=int(last or 0) + 1,
        status=ATTEMPT_RUNNING,
    )
    db.add(attempt)
    db.flush()
    return attempt


def close_attempt(
    db: Session, attempt_id: str, *, outcome: str | None = None
) -> None:
    """Mark an attempt finished, optionally stamping the loop outcome on it."""
    values: dict = {"status": ATTEMPT_DONE, "ended_at": datetime.now(UTC)}
    if outcome is not None:
        values["outcome"] = outcome
    db.query(AgentTeamAttempt).filter(AgentTeamAttempt.id == attempt_id).update(values)
    db.flush()


def record_evaluation(
    db: Session,
    *,
    task_id: str,
    attempt_id: str,
    run_id: str | None,
    verdict: str,
    score: float,
    missing: str,
    evidence_json: str = "{}",
) -> AgentTeamEvaluation:
    """Persist one evaluator verdict for an attempt."""
    evaluation = AgentTeamEvaluation(
        task_id=task_id,
        attempt_id=attempt_id,
        run_id=run_id,
        verdict=verdict,
        score=score,
        missing=missing,
        evidence_json=evidence_json,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def list_attempts_for_task(db: Session, task_id: str) -> list[AgentTeamAttempt]:
    """Return a task's attempts, oldest first."""
    return (
        db.query(AgentTeamAttempt)
        .filter(AgentTeamAttempt.task_id == task_id)
        .order_by(AgentTeamAttempt.attempt_no.asc())
        .all()
    )
