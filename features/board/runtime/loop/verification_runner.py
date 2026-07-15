"""Trusted execution of human-approved verification commands.

The evaluator is allowed to interpret results, but it is not trusted to claim
that a command ran.  This module resolves commands exclusively from the
approved ``TASKS.json`` contract, executes them through the task runtime, and
persists backend-owned receipts.  A workspace manifest is only a projection for
the evaluator to read; the completion gate reloads authoritative rows from the
database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import posixpath
import uuid
from dataclasses import dataclass
from typing import Any

from agent_team.features.board.repositories import verification_receipts as receipt_repo
from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
from agent_team.features.board.runtime.sandbox.service import (
    pause_task_sandbox,
    prepare_task_sandbox,
    resolve_profile,
)
from agent_team.features.board.runtime.turn_recovery import capture_workspace
from agent_team.features.repos.paths import task_copy_path
from agent_team.features.repos.repositories import repos_for_board
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

RECEIPT_MANIFEST_PATH = artifacts.VERIFICATION_RECEIPTS_PATH
RECEIPT_OUTPUT_DIR = f"{artifacts.ARTIFACT_DIR}/verification"
_DEFAULT_TIMEOUT_SECONDS = 30 * 60
_MAX_OUTPUT_FILE_CHARS = 2_000_000


class VerificationContractError(ValueError):
    """The command allow-list is not the exact human-approved TASKS artifact."""


@dataclass(frozen=True)
class ApprovedCommand:
    """One command selected from an approved task verification contract."""

    command_id: str
    task_ref: str
    kind: str
    repo: str | None
    working_directory: str
    command: str


@dataclass(frozen=True)
class ReceiptBatch:
    """The authoritative receipt batch minted for one evaluator invocation."""

    batch_id: str
    receipts: list[dict[str, Any]]
    source_sha256: str


@dataclass
class _ObservedCommand:
    approved: ApprovedCommand
    exit_code: int
    duration_ms: int
    timed_out: bool
    stdout: str
    stderr: str
    source_before: dict[str, Any]
    source_before_sha256: str
    source_after: dict[str, Any]
    source_after_sha256: str
    runtime: dict[str, Any]


def approved_commands(tasks: list[dict]) -> list[ApprovedCommand]:
    """Resolve the de-duplicated allow-list from approved task artifacts.

    Command strings supplied later by an evaluator are never accepted.  Stable
    ids let evidence cite a command without restating or modifying it.
    """
    rows: list[ApprovedCommand] = []
    seen: set[tuple[str, str]] = set()
    for task in tasks:
        task_ref = str(task.get("id") or "task").strip() or "task"
        verification = artifacts.normalize_verification(task.get("verification"))
        for kind, key in (
            ("feature", "feature_commands"),
            ("regression", "regression_commands"),
        ):
            for index, raw in enumerate(verification.get(key) or [], start=1):
                normalized = artifacts.normalize_verification_command(raw)
                if isinstance(normalized, dict):
                    repo = normalized["repo"]
                    command = normalized["command"]
                    working_directory = repo
                elif isinstance(normalized, str):
                    repo = None
                    command = normalized
                    working_directory = "."
                else:
                    continue
                identity = artifacts.verification_command_key(normalized)
                if identity == ("", "") or identity in seen:
                    continue
                seen.add(identity)
                rows.append(
                    ApprovedCommand(
                        command_id=f"{task_ref}:{kind}:{index}",
                        task_ref=task_ref,
                        kind=kind,
                        repo=repo,
                        working_directory=working_directory,
                        command=command,
                    )
                )
    return rows


def _assigned_repo_slugs(board_id: str) -> set[str]:
    if not board_id:
        return set()
    with SessionLocal() as db:
        return {repo.slug for repo, _branch, _push, _wiki in repos_for_board(db, board_id)}


def _validate_command_workdirs(
    commands: list[ApprovedCommand], *, board_id: str, workspace_path: str
) -> None:
    """Ensure every structured cwd is an assigned, prepared task repo."""
    structured = [command for command in commands if command.repo is not None]
    if not structured:
        return
    assigned = _assigned_repo_slugs(board_id)
    for command in structured:
        repo = command.repo or ""
        if repo not in assigned:
            raise VerificationContractError(
                f"verification command references repo {repo!r}, which is not "
                "assigned to this board"
            )
        try:
            target = task_copy_path(workspace_path, repo)
        except ValueError as exc:
            raise VerificationContractError(str(exc)) from exc
        if not target.is_dir() or not (target / ".git").exists():
            raise VerificationContractError(
                f"assigned repo {repo!r} is not prepared in the task workspace"
            )


def _execution_cwd(
    approved: ApprovedCommand, *, profile, sandbox, workspace_path: str
) -> str:
    if approved.repo is None:
        return (
            profile.workspace_mount_path
            if getattr(sandbox, "is_remote", False)
            else workspace_path
        )
    if getattr(sandbox, "is_remote", False):
        return posixpath.join(profile.workspace_mount_path, approved.repo)
    return str(task_copy_path(workspace_path, approved.repo))


def capture_source_state(workspace_path: str) -> tuple[dict[str, Any], str]:
    """Fingerprint every Git repo's HEAD plus dirty/untracked source state."""
    snapshot = capture_workspace(workspace_path)
    source = {
        "version": 1,
        "repos": snapshot.get("repos") if isinstance(snapshot, dict) else [],
    }
    payload = json.dumps(source, sort_keys=True, separators=(",", ":"))
    return source, hashlib.sha256(payload.encode()).hexdigest()


def _runtime_details(
    profile, sandbox: object | None, *, contract_etag: str
) -> dict[str, Any]:
    details = {
        "provider": profile.provider,
        "isolated": bool(profile.is_sandboxed),
        "strategy": profile.runtime_strategy if profile.is_sandboxed else None,
        "image": profile.image if profile.is_sandboxed else None,
        "snapshot_id": profile.snapshot_id,
        "sandbox_kind": getattr(sandbox, "kind", None),
        "sandbox_id": getattr(sandbox, "sandbox_id", None),
        "workspace_mode": profile.workspace_mode,
        "host_platform": platform.platform(),
        "runner_version": 1,
        "approved_tasks_contract_etag": contract_etag,
    }
    canonical = json.dumps(details, sort_keys=True, separators=(",", ":"))
    details["fingerprint_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return details


def _timeout_seconds(profile) -> int:
    raw = (os.environ.get("AGENT_TEAM_VERIFICATION_TIMEOUT_SECONDS") or "").strip()
    try:
        configured = int(raw) if raw else _DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        configured = _DEFAULT_TIMEOUT_SECONDS
    runtime_ceiling = max(1, int(profile.timeout_minutes or 180) * 60)
    return max(1, min(configured, runtime_ceiling))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _output_text(value: str) -> tuple[str, bool]:
    if len(value) <= _MAX_OUTPUT_FILE_CHARS:
        return value, False
    return value[-_MAX_OUTPUT_FILE_CHARS:], True


def _safe_command_segment(command_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in command_id)


def _write_output(
    workspace_path: str,
    *,
    batch_id: str,
    command_id: str,
    stream: str,
    content: str,
) -> tuple[str, bool]:
    # An empty stream is valid (and common for successful type-checks), but it
    # is not a useful workspace artifact.  Keep its SHA on the receipt while
    # omitting a path so evaluators do not cite a zero-byte log as evidence.
    if not content.strip():
        return "", False
    rel_path = (
        f"{RECEIPT_OUTPUT_DIR}/{batch_id}/"
        f"{_safe_command_segment(command_id)}.{stream}.log"
    )
    rendered, truncated = _output_text(content)
    try:
        artifacts.write_text(workspace_path, rel_path, rendered)
    except OSError:
        logger.warning("could not persist verification output %s", rel_path, exc_info=True)
        return "", truncated
    return rel_path, truncated


def _persist_observed(
    *,
    task_id: str,
    attempt_id: str | None,
    workspace_path: str,
    batch_id: str,
    observations: list[_ObservedCommand],
) -> list[dict[str, Any]]:
    manifest_rows: list[dict[str, Any]] = []
    with SessionLocal() as db:
        for observed in observations:
            stdout_path, stdout_truncated = _write_output(
                workspace_path,
                batch_id=batch_id,
                command_id=observed.approved.command_id,
                stream="stdout",
                content=observed.stdout,
            )
            stderr_path, stderr_truncated = _write_output(
                workspace_path,
                batch_id=batch_id,
                command_id=observed.approved.command_id,
                stream="stderr",
                content=observed.stderr,
            )
            runtime = {
                **observed.runtime,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "stdout_bytes": len(observed.stdout.encode("utf-8", errors="replace")),
                "stderr_bytes": len(observed.stderr.encode("utf-8", errors="replace")),
            }
            row = receipt_repo.create_receipt(
                db,
                task_id=task_id,
                attempt_id=attempt_id,
                batch_id=batch_id,
                command_id=observed.approved.command_id,
                repo_slug=observed.approved.repo,
                working_directory=observed.approved.working_directory,
                command=observed.approved.command,
                exit_code=observed.exit_code,
                duration_ms=observed.duration_ms,
                timed_out=observed.timed_out,
                stdout_sha256=_sha256_text(observed.stdout),
                stderr_sha256=_sha256_text(observed.stderr),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                source_before=observed.source_before,
                source_after=observed.source_after,
                source_before_sha256=observed.source_before_sha256,
                source_after_sha256=observed.source_after_sha256,
                runtime=runtime,
            )
            manifest_rows.append(receipt_to_dict(row))
        db.commit()
    return manifest_rows


def receipt_to_dict(row) -> dict[str, Any]:
    """Return the safe evaluator/gate view of a persisted receipt."""
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "command_id": row.command_id,
        "repo": row.repo_slug,
        "working_directory": row.working_directory,
        "command": row.command,
        "exit_code": row.exit_code,
        "status": "pass" if row.exit_code == 0 and not row.timed_out else "fail",
        "duration_ms": row.duration_ms,
        "timed_out": row.timed_out,
        "stdout_sha256": row.stdout_sha256,
        "stderr_sha256": row.stderr_sha256,
        "stdout_path": row.stdout_path,
        "stderr_path": row.stderr_path,
        "source_before_sha256": row.source_before_sha256,
        "source_after_sha256": row.source_after_sha256,
        "runtime": row.runtime(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def load_receipt_batch(task_id: str, batch_id: str) -> list[dict[str, Any]]:
    """Reload authoritative receipts; never trust the workspace projection."""
    with SessionLocal() as db:
        rows = receipt_repo.list_batch(db, task_id=task_id, batch_id=batch_id)
        return [receipt_to_dict(row) for row in rows]


def _write_manifest(
    workspace_path: str,
    *,
    batch_id: str,
    receipts: list[dict[str, Any]],
) -> None:
    payload = {
        "version": 1,
        "batch_id": batch_id,
        "authoritative": False,
        "note": "Evaluator-readable projection; completion uses database receipts.",
        "receipts": receipts,
    }
    artifacts.write_text(
        workspace_path,
        RECEIPT_MANIFEST_PATH,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


async def run_approved_commands(
    *,
    task_id: str,
    board_id: str,
    attempt_id: str | None,
    workspace_path: str,
    tasks: list[dict],
    approved_tasks_contract_etag: str,
) -> ReceiptBatch | None:
    """Execute the approved allow-list and mint one authoritative receipt each."""
    current_contract_etag = artifacts.tasks_contract_etag(workspace_path)
    if (
        not approved_tasks_contract_etag
        or current_contract_etag != approved_tasks_contract_etag
    ):
        raise VerificationContractError(
            "TASKS.json content does not match the human-approved task contract; "
            "re-approval is required"
        )
    commands = approved_commands(tasks)
    if not commands:
        return None
    _validate_command_workdirs(
        commands, board_id=board_id, workspace_path=workspace_path
    )

    batch_id = uuid.uuid4().hex
    profile = resolve_profile(task_id, board_id)
    sandbox = None
    observations: list[_ObservedCommand] = []
    prepare_error = ""
    try:
        sandbox = await prepare_task_sandbox(
            task_id=task_id,
            host_workspace_path=workspace_path,
            profile=profile,
            board_id=board_id,
        )
    except Exception as exc:  # noqa: BLE001 — mint failed receipts for every command
        prepare_error = f"Verification runtime unavailable: {exc}"
        logger.warning(
            "verification runtime unavailable for task=%s", task_id, exc_info=True
        )

    runtime = _runtime_details(
        profile, sandbox, contract_etag=approved_tasks_contract_etag
    )
    try:
        for approved in commands:
            before, before_sha = capture_source_state(workspace_path)
            if sandbox is None:
                exit_code, duration_ms, timed_out = -1, 0, False
                stdout, stderr = "", prepare_error or "Verification runtime unavailable"
            else:
                cwd = _execution_cwd(
                    approved,
                    profile=profile,
                    sandbox=sandbox,
                    workspace_path=workspace_path,
                )
                try:
                    result = await sandbox.exec_shell(
                        approved.command,
                        cwd=cwd,
                        timeout_seconds=_timeout_seconds(profile),
                    )
                    exit_code = int(result.exit_code)
                    duration_ms = int(result.duration_ms)
                    timed_out = bool(result.timed_out)
                    stdout = result.stdout or ""
                    stderr = result.stderr or ""
                except Exception as exc:  # noqa: BLE001 — mint a failed receipt
                    logger.warning(
                        "approved verification command failed to execute: %s",
                        approved.command_id,
                        exc_info=True,
                    )
                    exit_code, duration_ms, timed_out = -1, 0, False
                    stdout, stderr = "", f"Runner error: {type(exc).__name__}: {exc}"
            after, after_sha = capture_source_state(workspace_path)
            observations.append(
                _ObservedCommand(
                    approved=approved,
                    exit_code=exit_code,
                    duration_ms=duration_ms,
                    timed_out=timed_out,
                    stdout=stdout,
                    stderr=stderr,
                    source_before=before,
                    source_before_sha256=before_sha,
                    source_after=after,
                    source_after_sha256=after_sha,
                    runtime={
                        **runtime,
                        "repo": approved.repo,
                        "working_directory": approved.working_directory,
                        "execution_cwd": cwd if sandbox is not None else None,
                    },
                )
            )
    finally:
        if sandbox is not None:
            await pause_task_sandbox(
                task_id, workspace_mount_path=profile.workspace_mount_path
            )

    receipts = _persist_observed(
        task_id=task_id,
        attempt_id=attempt_id,
        workspace_path=workspace_path,
        batch_id=batch_id,
        observations=observations,
    )
    _write_manifest(workspace_path, batch_id=batch_id, receipts=receipts)
    _source, current_sha = capture_source_state(workspace_path)
    return ReceiptBatch(batch_id=batch_id, receipts=receipts, source_sha256=current_sha)
