"""Immutable project-policy validation and strict-run gates.

This module is deliberately project agnostic. A deployment compiles YAML or
another authoring format into the three parsed documents accepted here; the
backend persists and enforces only the immutable canonical bundle.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from agent_team.features.board.models import (
    AgentTeamBoard,
    AgentTeamProjectPolicyBundle,
    AgentTeamTask,
)

POLICY_FILES = ("project.yaml", "evidence.yaml", "paths.yaml")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
#: Template (argv-item) check — tolerates ``${VAR}`` placeholders authored in
#: the policy, but rejects obvious shell operators inside a single arg.
_SHELL_META = re.compile(r"\|\||&&|[;|<>`]|\$\(")
#: Resolved-runtime-command check. The runner joins the command into one string
#: and executes it through a shell, so ANY shell-control character is unsafe:
#: separators (``;`` ``&`` ``|`` and a bare newline/CR), redirects (``<`` ``>``),
#: subshell (``(`` ``)``), substitution (``$`` `` ` ``). Placeholders are already
#: resolved to concrete values, so a legitimate verification command never needs
#: these — reviewer showed ``settings&id`` and ``settings\nid`` slipping past the
#: old ``&&``/``||``-only pattern.
_SHELL_META_RESOLVED = re.compile(r"[;&|<>()$`\n\r]")


class PolicyError(ValueError):
    """A policy is absent, invalid, stale, or violated."""


@dataclass(frozen=True)
class PolicyIdentity:
    id: str
    project_key: str
    schema_version: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_key": self.project_key,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
        }


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def bundle_digest(
    *,
    project_key: str,
    schema_version: int,
    source_ref: str,
    file_hashes: dict,
    documents: dict,
) -> str:
    payload = {
        "project_key": project_key,
        "schema_version": schema_version,
        "source_ref": source_ref,
        "file_hashes": file_hashes,
        "documents_sha256": hashlib.sha256(_canonical(documents).encode()).hexdigest(),
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def validate_documents(documents: object, file_hashes: object) -> tuple[str, int]:
    """Validate fail-closed policy semantics and return key/version."""
    if not isinstance(documents, dict) or set(documents) != set(POLICY_FILES):
        raise PolicyError(f"documents must contain exactly {', '.join(POLICY_FILES)}")
    if not isinstance(file_hashes, dict) or set(file_hashes) != set(POLICY_FILES):
        raise PolicyError(f"file_hashes must contain exactly {', '.join(POLICY_FILES)}")
    for name, digest in file_hashes.items():
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise PolicyError(f"{name} SHA-256 must be 64 lowercase hex characters")

    parsed = [documents[name] for name in POLICY_FILES]
    if not all(isinstance(item, dict) for item in parsed):
        raise PolicyError("every policy document must be an object")
    keys = {item.get("project_key") for item in parsed}
    versions = {item.get("schema_version") for item in parsed}
    if len(keys) != 1 or not isinstance(next(iter(keys)), str) or not next(iter(keys)):
        raise PolicyError("project_key must be the same non-empty string in all files")
    if versions != {1}:
        raise PolicyError("enforced policy schema_version must be 1")

    project, evidence, paths = parsed
    source = project.get("source")
    if not isinstance(source, dict) or not source.get("repo_logical_id"):
        raise PolicyError("project.yaml source.repo_logical_id is required")
    if any(str(value).startswith("/") for value in source.values() if isinstance(value, str)):
        raise PolicyError("project source values must be logical ids/refs, not absolute paths")

    commands = evidence.get("commands")
    if evidence.get("enforcement") != "enforced" or not isinstance(commands, list) or not commands:
        raise PolicyError("evidence.yaml must be enforced and contain commands")
    seen: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            raise PolicyError("evidence command must be an object")
        command_id = command.get("id")
        argv = command.get("argv")
        cwd = command.get("cwd")
        if not isinstance(command_id, str) or not command_id or command_id in seen:
            raise PolicyError("evidence command ids must be unique non-empty strings")
        seen.add(command_id)
        if not isinstance(argv, list) or not argv or not all(
            isinstance(arg, str) and arg for arg in argv
        ):
            raise PolicyError(f"command {command_id}: argv must be non-empty strings")
        if any(_SHELL_META.search(arg) for arg in argv):
            raise PolicyError(f"command {command_id}: shell metacharacters are forbidden")
        if not isinstance(cwd, str) or not cwd or cwd.startswith("/") or ".." in cwd.split("/"):
            raise PolicyError(f"command {command_id}: cwd must be logical and relative")
        if not isinstance(command.get("timeout_s"), int) or command["timeout_s"] <= 0:
            raise PolicyError(f"command {command_id}: timeout_s must be positive")

    if paths.get("enforcement") != "enforced":
        raise PolicyError("paths.yaml enforcement must be enforced")
    for key in ("deny_read", "protected_write", "allowed_write"):
        values = paths.get(key)
        if not isinstance(values, list) or not values or not all(
            isinstance(value, str) and value and not value.startswith("/") for value in values
        ):
            raise PolicyError(f"paths.yaml {key} must contain relative glob strings")
    for optional_key in ("append_only", "risk_triggers"):
        values = paths.get(optional_key, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value or value.startswith("/")
            for value in values
        ):
            raise PolicyError(
                f"paths.yaml {optional_key} must contain relative glob strings"
            )
    return next(iter(keys)), 1


def create_bundle(
    db: Session,
    *,
    documents: dict,
    file_hashes: dict,
    source_ref: str,
    created_by: str | None,
) -> AgentTeamProjectPolicyBundle:
    project_key, schema_version = validate_documents(documents, file_hashes)
    if not source_ref or source_ref.startswith("/"):
        raise PolicyError("source_ref must be an immutable logical release/commit")
    digest = bundle_digest(
        project_key=project_key,
        schema_version=schema_version,
        source_ref=source_ref,
        file_hashes=file_hashes,
        documents=documents,
    )
    existing = (
        db.query(AgentTeamProjectPolicyBundle)
        .filter(
            AgentTeamProjectPolicyBundle.project_key == project_key,
            AgentTeamProjectPolicyBundle.bundle_sha256 == digest,
        )
        .first()
    )
    if existing is not None:
        if existing.documents() != documents or existing.file_hashes() != file_hashes:
            raise PolicyError("bundle digest collision or inconsistent immutable payload")
        return existing
    row = AgentTeamProjectPolicyBundle(
        project_key=project_key,
        schema_version=schema_version,
        source_ref=source_ref,
        documents_json=_canonical(documents),
        file_hashes_json=_canonical(file_hashes),
        bundle_sha256=digest,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def identity(row: AgentTeamProjectPolicyBundle) -> PolicyIdentity:
    return PolicyIdentity(row.id, row.project_key, row.schema_version, row.bundle_sha256)


def bound_bundle(db: Session, board_id: str, *, required: bool = False):
    board = db.get(AgentTeamBoard, board_id)
    row = (
        db.get(AgentTeamProjectPolicyBundle, board.policy_bundle_id)
        if board and board.policy_bundle_id
        else None
    )
    if required and row is None:
        raise PolicyError("strict/enforced run requires a board policy bundle")
    if row is not None:
        validate_documents(row.documents(), row.file_hashes())
        expected = bundle_digest(
            project_key=row.project_key,
            schema_version=row.schema_version,
            source_ref=row.source_ref,
            file_hashes=row.file_hashes(),
            documents=row.documents(),
        )
        if expected != row.bundle_sha256:
            raise PolicyError("stored policy bundle digest is invalid")
    return row


def assert_approval_current(task: AgentTeamTask, bundle: AgentTeamProjectPolicyBundle) -> None:
    approved = task.planning_meta().get("policy_bundle") or {}
    current = identity(bundle).as_dict()
    if approved != current:
        raise PolicyError("policy bundle changed after approval; human re-approval required")


def assert_contract_current(task: AgentTeamTask, bundle: AgentTeamProjectPolicyBundle) -> None:
    """Rehash all immutable planning content while ignoring task status fields."""
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    assert_approval_current(task, bundle)
    meta = task.planning_meta()
    approved = meta.get("artifact_etags") or {}
    current = artifacts.approved_etags(task.workspace_path)
    for name in ("SPEC.md", "PLAN.md"):
        if not approved.get(name) or current.get(name) != approved.get(name):
            raise PolicyError(f"{name} changed after approval; human re-approval required")
    expected_tasks = str(meta.get("tasks_contract_etag") or "")
    if not expected_tasks or artifacts.tasks_contract_etag(task.workspace_path) != expected_tasks:
        raise PolicyError("TASKS.json contract changed after approval; re-approval required")
    assert_denied_paths_absent(bundle, task.workspace_path)


def assert_denied_paths_absent(
    bundle: AgentTeamProjectPolicyBundle, workspace_path: str
) -> None:
    """Fail closed when a secret/deny-read path entered an enforced workspace."""
    patterns = bundle.documents()["paths.yaml"]["deny_read"]
    roots: list[tuple[str, str]] = []
    workspace = os.path.realpath(workspace_path)
    if os.path.exists(os.path.join(workspace, ".git")):
        roots.append((".", workspace))
    try:
        for entry in os.scandir(workspace):
            if entry.is_dir(follow_symlinks=False) and os.path.exists(
                os.path.join(entry.path, ".git")
            ):
                roots.append((entry.name, entry.path))
    except OSError as exc:
        raise PolicyError(f"cannot inspect enforced workspace: {exc}") from exc
    if not roots:
        raise PolicyError("enforced workspace has no prepared Git repository")
    denied: list[str] = []
    for label, root in roots:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name != ".git"]
            for name in [*dirs, *files]:
                absolute = os.path.join(current, name)
                relative = os.path.relpath(absolute, root).replace(os.sep, "/")
                if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                    denied.append(relative if label == "." else f"{label}/{relative}")
                    if len(denied) >= 20:
                        break
            if len(denied) >= 20:
                break
    if denied:
        raise PolicyError(
            "deny-read paths are present in enforced workspace; sanitize the repo: "
            + ", ".join(sorted(denied))
        )


def enforced_context(
    db: Session, task_id: str
) -> tuple[AgentTeamTask, AgentTeamProjectPolicyBundle] | None:
    task = db.get(AgentTeamTask, task_id)
    if task is None:
        raise PolicyError("task not found")
    bundle = bound_bundle(db, task.board_id, required=False)
    if bundle is None:
        return None
    assert_contract_current(task, bundle)
    return task, bundle


def _argv_matches(template: list[str], actual: list[str]) -> bool:
    if len(template) != len(actual):
        return False
    return all(
        wanted == got
        or (re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", wanted) is not None and bool(got))
        for wanted, got in zip(template, actual, strict=True)
    )


def assert_commands_allowed(bundle: AgentTeamProjectPolicyBundle, tasks: list[dict]) -> None:
    """Require every planned verification command to match the bundle allowlist."""
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    for task in tasks:
        verification = artifacts.normalize_verification(task.get("verification"))
        for key in ("feature_commands", "regression_commands"):
            for raw in verification.get(key) or []:
                normalized = artifacts.normalize_verification_command(raw)
                command = normalized.get("command") if isinstance(normalized, dict) else normalized
                cwd = normalized.get("cwd", ".") if isinstance(normalized, dict) else "."
                repo = normalized.get("repo") if isinstance(normalized, dict) else None
                if command_policy(bundle, command or "", cwd=cwd, repo=repo) is None:
                    raise PolicyError(f"verification command is not allowlisted: {command!r}")


def command_policy(
    bundle: AgentTeamProjectPolicyBundle,
    command: str,
    *,
    cwd: str,
    repo: str | None,
) -> dict | None:
    """Resolve one exact allowlist row, including timeout/expected-exit policy."""
    # The runner executes the resolved command as a single string through a
    # shell, but validation tokenises with shlex.split — which does NOT split on
    # `;` `|` `&&` `$(` `<` `>` `` ` ``. Without this guard a value like
    # `settings;id` tokenises to one argument that matches a `${MODULE}`
    # placeholder, passes the allowlist, and then runs `id` as a second shell
    # command. Reject shell metacharacters on the resolved command so the
    # validation model matches the execution model (the allowlist is a security
    # boundary, not a hint).
    if _SHELL_META_RESOLVED.search(command):
        raise PolicyError(
            f"verification command contains shell metacharacters: {command!r}"
        )
    try:
        actual = shlex.split(command)
    except ValueError as exc:
        raise PolicyError(f"invalid verification command quoting: {command!r}") from exc
    documents = bundle.documents()
    repo_logical_id = documents["project.yaml"]["source"]["repo_logical_id"]
    if repo is not None and repo != repo_logical_id:
        raise PolicyError(
            f"verification command repo {repo!r} does not match policy repo "
            f"{repo_logical_id!r}"
        )
    for row in documents["evidence.yaml"]["commands"]:
        if (
            _argv_matches(row["argv"], actual)
            and posixpath.normpath(cwd) == posixpath.normpath(row["cwd"])
        ):
            return row
    return None


def normalize_changed_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized in ("", ".") or normalized.startswith("../") or normalized.startswith("/"):
        raise PolicyError(f"unsafe changed path: {path!r}")
    return normalized


def path_violations(
    bundle: AgentTeamProjectPolicyBundle, changed_paths: list[str | dict[str, str]]
) -> list[dict[str, str]]:
    paths = bundle.documents()["paths.yaml"]
    protected = paths["protected_write"]
    allowed = paths["allowed_write"]
    append_only = paths.get("append_only") or []
    violations: list[dict[str, str]] = []
    for item in changed_paths:
        raw = item.get("path", "") if isinstance(item, dict) else item
        git_state = item.get("git_state") if isinstance(item, dict) else None
        path = normalize_changed_path(raw)
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in protected):
            violations.append({"path": path, "reason": "protected_write"})
        elif not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed):
            violations.append({"path": path, "reason": "outside_allowed_write"})
        elif any(fnmatch.fnmatchcase(path, pattern) for pattern in append_only) and (
            git_state != "untracked"
        ):
            violations.append({"path": path, "reason": "append_only_modified"})
    return violations


def risk_triggered_paths(
    bundle: AgentTeamProjectPolicyBundle, changed_paths: list[str | dict[str, str]]
) -> list[str]:
    """Changed paths that enter the policy's explicit-risk lane (e.g. migrations)."""
    patterns = bundle.documents()["paths.yaml"].get("risk_triggers") or []
    if not patterns:
        return []
    hits: list[str] = []
    for item in changed_paths:
        raw = item.get("path", "") if isinstance(item, dict) else item
        path = normalize_changed_path(raw)
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            hits.append(path)
    return sorted(set(hits))


def risk_lane_issues(
    bundle: AgentTeamProjectPolicyBundle,
    planning_meta: dict,
    changed_paths: list[str | dict[str, str]],
) -> list[str]:
    """Fail closed when risk-lane paths changed without explicit approval.

    A path matching ``paths.yaml risk_triggers`` (schema/migration lane) may
    only be changed when the human approval carried explicit acceptance
    (``risk_lane_accepted``). Anything else routes back to human re-approval.
    """
    hits = risk_triggered_paths(bundle, changed_paths)
    if not hits or planning_meta.get("risk_lane_accepted") is True:
        return []
    return [
        "risk-lane paths changed without explicit human approval "
        "(re-approve with risk acceptance): " + ", ".join(hits[:10])
    ]


def changed_paths_from_source(source: dict) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    repos = [repo for repo in (source.get("repos") or []) if isinstance(repo, dict)]
    multi_repo = len(repos) > 1
    for repo in repos:
        prefix = str(repo.get("path") or ".")
        for path, signature in (repo.get("dirty") or {}).items():
            resolved = f"{prefix}/{path}" if multi_repo and prefix != "." else str(path)
            result.append(
                {
                    "path": resolved,
                    "git_state": str((signature or {}).get("git_state") or "unknown"),
                }
            )
    return result
