"""Persistence helpers for backend-owned verification receipts."""

from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamVerificationReceipt


def create_receipt(
    db: Session,
    *,
    task_id: str,
    attempt_id: str | None,
    batch_id: str,
    command_id: str,
    repo_slug: str | None,
    working_directory: str,
    command: str,
    exit_code: int,
    duration_ms: int,
    timed_out: bool,
    stdout_sha256: str,
    stderr_sha256: str,
    stdout_path: str,
    stderr_path: str,
    source_before: dict,
    source_after: dict,
    source_before_sha256: str,
    source_after_sha256: str,
    runtime: dict,
    policy_bundle_id: str | None = None,
    policy_bundle_sha256: str | None = None,
) -> AgentTeamVerificationReceipt:
    row = AgentTeamVerificationReceipt(
        task_id=task_id,
        attempt_id=attempt_id,
        batch_id=batch_id,
        command_id=command_id,
        repo_slug=repo_slug,
        working_directory=working_directory,
        command=command,
        exit_code=exit_code,
        duration_ms=duration_ms,
        timed_out=timed_out,
        stdout_sha256=stdout_sha256,
        stderr_sha256=stderr_sha256,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        source_before_json=json.dumps(source_before, sort_keys=True),
        source_after_json=json.dumps(source_after, sort_keys=True),
        source_before_sha256=source_before_sha256,
        source_after_sha256=source_after_sha256,
        runtime_json=json.dumps(runtime, sort_keys=True),
        policy_bundle_id=policy_bundle_id,
        policy_bundle_sha256=policy_bundle_sha256,
    )
    db.add(row)
    db.flush()
    return row


def list_batch(
    db: Session, *, task_id: str, batch_id: str
) -> list[AgentTeamVerificationReceipt]:
    return list(
        db.scalars(
            select(AgentTeamVerificationReceipt)
            .where(
                AgentTeamVerificationReceipt.task_id == task_id,
                AgentTeamVerificationReceipt.batch_id == batch_id,
            )
            .order_by(AgentTeamVerificationReceipt.created_at.asc())
        )
    )


def receipt_ids(rows: Iterable[AgentTeamVerificationReceipt]) -> set[str]:
    return {row.id for row in rows}
