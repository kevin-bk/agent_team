"""Phase-C hardening gates: policy identity, isolation and bounded graphs."""

from __future__ import annotations

import hashlib
import subprocess
from types import SimpleNamespace

import pytest
from agent_team.features.board.runtime import project_policy
from agent_team.features.board.runtime.loop import service as loop_service
from agent_team.features.board.runtime.loop import task_graph
from agent_team.features.board.runtime.status_tools import get_status_tools


def _bundle_documents(project_key: str = "project-a") -> dict:
    return {
        "project.yaml": {
            "schema_version": 1,
            "project_key": project_key,
            "source": {"repo_logical_id": project_key, "base_sha": "a" * 40},
        },
        "evidence.yaml": {
            "schema_version": 1,
            "project_key": project_key,
            "commands": [
                {
                    "id": "test_module",
                    "argv": ["yarn", "test", "${MODULE}"],
                    "cwd": ".",
                    "timeout_s": 60,
                    "expected_exit": 0,
                }
            ],
            "enforcement": "enforced",
        },
        "paths.yaml": {
            "schema_version": 1,
            "project_key": project_key,
            "deny_read": [".env"],
            "protected_write": ["src/main.py"],
            "allowed_write": ["src/**", "tests/**"],
            "enforcement": "enforced",
        },
    }


def _hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256(name.encode()).hexdigest()
        for name in project_policy.POLICY_FILES
    }


def _bundle(key: str = "project-a"):
    documents = _bundle_documents(key)
    return SimpleNamespace(documents=lambda: documents)


def test_policy_validation_rejects_cross_file_project_key():
    documents = _bundle_documents()
    documents["paths.yaml"]["project_key"] = "project-b"
    with pytest.raises(project_policy.PolicyError, match="project_key"):
        project_policy.validate_documents(documents, _hashes())


def test_project_a_command_is_rejected_by_project_b_policy():
    tasks = [
        {
            "id": "T1",
            "verification": {
                "feature_commands": ["yarn test inventory"],
                "regression_commands": [],
            },
        }
    ]
    project_policy.assert_commands_allowed(_bundle("project-a"), tasks)
    docs_b = _bundle_documents("project-b")
    docs_b["evidence.yaml"]["commands"][0]["argv"] = ["npm", "test"]
    bundle_b = SimpleNamespace(documents=lambda: docs_b)
    with pytest.raises(project_policy.PolicyError, match="not allowlisted"):
        project_policy.assert_commands_allowed(bundle_b, tasks)


def test_allowlisted_command_in_wrong_cwd_is_rejected():
    documents = _bundle_documents()
    documents["evidence.yaml"]["commands"][0]["cwd"] = "server"
    bundle = SimpleNamespace(documents=lambda: documents)
    tasks = [{
        "id": "T1",
        "verification": {"feature_commands": ["yarn test settings"]},
    }]
    with pytest.raises(project_policy.PolicyError, match="not allowlisted"):
        project_policy.assert_commands_allowed(bundle, tasks)


def test_path_gate_is_fail_closed_for_protected_and_outside_scope():
    violations = project_policy.path_violations(
        _bundle(), ["src/main.py", "docs/notes.md", "tests/ok.py"]
    )
    assert violations == [
        {"path": "src/main.py", "reason": "protected_write"},
        {"path": "docs/notes.md", "reason": "outside_allowed_write"},
    ]


def test_append_only_path_modification_is_rejected():
    documents = _bundle_documents()
    documents["paths.yaml"]["allowed_write"] = ["src/**", "tests/**", "docs/**"]
    documents["paths.yaml"]["append_only"] = ["docs/decisions/**"]
    bundle = SimpleNamespace(documents=lambda: documents)
    violations = project_policy.path_violations(
        bundle,
        [
            {"path": "docs/decisions/0001-adr.md", "git_state": "modified"},
            {"path": "docs/decisions/0002-adr.md", "git_state": "untracked"},
        ],
    )
    assert violations == [
        {"path": "docs/decisions/0001-adr.md", "reason": "append_only_modified"}
    ]


def test_runtime_policy_gate_accepts_repo_root_cwd_commands():
    """Regression: a structured command at repo root (planner cwd ".").

    ``ApprovedCommand.working_directory`` equals the repo slug for the root
    case; the runtime allowlist lookup must resolve it back to cwd "." or the
    whole receipt batch aborts before executing anything (T-4 incident).
    """
    from agent_team.features.board.runtime.loop.verification_runner import (
        approved_commands,
        policy_cwd,
    )

    tasks = [
        {
            "id": "T2",
            "verification": {
                "feature_commands": [
                    {"repo": "project-a", "command": "yarn test settings"}
                ],
                "regression_commands": [
                    {"repo": "project-a", "cwd": "server", "command": "npx nest build"}
                ],
            },
        }
    ]
    commands = {c.command: c for c in approved_commands(tasks)}
    root_cmd = commands["yarn test settings"]
    sub_cmd = commands["npx nest build"]
    assert root_cmd.working_directory == "project-a"
    assert policy_cwd(root_cmd) == "."
    assert policy_cwd(sub_cmd) == "server"

    documents = _bundle_documents()
    documents["evidence.yaml"]["commands"].append(
        {
            "id": "typecheck",
            "argv": ["npx", "nest", "build"],
            "cwd": "server",
            "timeout_s": 120,
            "expected_exit": 0,
        }
    )
    bundle = SimpleNamespace(documents=lambda: documents)
    assert project_policy.command_policy(
        bundle, root_cmd.command, cwd=policy_cwd(root_cmd), repo=root_cmd.repo
    ) is not None
    assert project_policy.command_policy(
        bundle, sub_cmd.command, cwd=policy_cwd(sub_cmd), repo=sub_cmd.repo
    ) is not None


def test_command_policy_rejects_shell_metacharacters():
    """A resolved command that smuggles a shell metacharacter is rejected, even
    when its first tokens match an allowlisted argv template — the runner
    executes the string through a shell, so `;`/`|`/`&&`/`$(`/`` ` `` must not
    slip past validation (allowlist is a security boundary, not a hint)."""
    documents = _bundle_documents()  # argv: ["yarn", "test", "${MODULE}"], cwd "."
    bundle = SimpleNamespace(documents=lambda: documents)
    # Baseline: a clean allowlisted command resolves.
    assert project_policy.command_policy(
        bundle, "yarn test settings", cwd=".", repo=None
    ) is not None
    for payload in (
        "yarn test settings;id",
        "yarn test settings && id",
        "yarn test settings | id",
        "yarn test $(id)",
        "yarn test `id`",
    ):
        with pytest.raises(project_policy.PolicyError, match="shell metacharacter"):
            project_policy.command_policy(bundle, payload, cwd=".", repo=None)


def test_risk_triggers_must_be_relative_globs():
    documents = _bundle_documents()
    documents["paths.yaml"]["risk_triggers"] = ["/etc/passwd"]
    with pytest.raises(project_policy.PolicyError, match="risk_triggers"):
        project_policy.validate_documents(documents, _hashes())


def test_risk_lane_change_requires_explicit_approval():
    documents = _bundle_documents()
    documents["paths.yaml"]["allowed_write"] = ["src/**", "server/prisma/**"]
    documents["paths.yaml"]["risk_triggers"] = [
        "server/prisma/migrations/**",
        "server/prisma/schema.prisma",
    ]
    bundle = SimpleNamespace(documents=lambda: documents)
    changed = [
        {"path": "server/prisma/migrations/20260717_add_x/migration.sql",
         "git_state": "untracked"},
        {"path": "src/ok.py", "git_state": "modified"},
    ]

    issues = project_policy.risk_lane_issues(bundle, {}, changed)
    assert len(issues) == 1
    assert "risk-lane" in issues[0]
    assert "server/prisma/migrations/20260717_add_x/migration.sql" in issues[0]

    # Explicit human acceptance stamped at approval clears the gate; any other
    # value (missing/false/truthy-non-True) stays fail-closed.
    assert project_policy.risk_lane_issues(
        bundle, {"risk_lane_accepted": True}, changed
    ) == []
    assert project_policy.risk_lane_issues(
        bundle, {"risk_lane_accepted": "yes"}, changed
    ) != []

    # No risk-lane paths touched → no acceptance needed.
    assert project_policy.risk_lane_issues(
        bundle, {}, [{"path": "src/ok.py", "git_state": "modified"}]
    ) == []


def test_deny_read_file_blocks_enforced_workspace(tmp_path):
    repo = tmp_path / "project-a"
    (repo / ".git").mkdir(parents=True)
    (repo / ".env").write_text("do-not-read")
    with pytest.raises(project_policy.PolicyError, match="sanitize"):
        project_policy.assert_denied_paths_absent(_bundle(), str(tmp_path))


def test_task_repo_sanitizer_fails_closed_on_tracked_denied_paths(tmp_path):
    """A tracked deny_read file is not made unreadable by removing the worktree
    file — its blob stays in .git (git show/cat-file). Preparation must fail
    closed rather than pretend the secret is protected."""
    from agent_team.features.repos.task_copy import _sanitize_deny_read_paths

    repo = tmp_path / "project-a"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".env.local").write_text("secret\n", encoding="utf-8")
    (repo / "server").mkdir()
    (repo / "server" / ".env.example").write_text("example\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=Test",
            "-c", "user.email=test@example.com", "commit", "-qm", "fixture",
        ],
        check=True,
    )

    with pytest.raises(RuntimeError, match="tracked file"):
        _sanitize_deny_read_paths(repo, [".env.*", "**/.env*"])


def test_task_repo_sanitizer_removes_untracked_denied_paths(tmp_path):
    """Untracked denied paths (the common .gitignore case) are removed cleanly;
    no blob ever entered .git so nothing is left to read."""
    from agent_team.features.repos.task_copy import _sanitize_deny_read_paths

    repo = tmp_path / "project-a"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "server").mkdir()
    (repo / "server" / "settings.ts").write_text("safe\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "-c", "user.name=Test",
            "-c", "user.email=test@example.com", "commit", "-qm", "fixture",
        ],
        check=True,
    )
    # Untracked secrets appear after the commit (e.g. a real .env on disk).
    (repo / ".env.local").write_text("secret\n", encoding="utf-8")

    removed = _sanitize_deny_read_paths(repo, [".env.*", "**/.env*"])

    assert removed == [".env.local"]
    assert not (repo / ".env.local").exists()
    assert (repo / "server" / "settings.ts").read_text() == "safe\n"
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    project_policy.assert_denied_paths_absent(_bundle(), str(tmp_path))


def test_approved_contract_mutation_requires_reapproval(tmp_path):
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    (tmp_path / ".git").mkdir()
    artifacts.write_text(str(tmp_path), artifacts.SPEC_PATH, "spec")
    artifacts.write_text(str(tmp_path), artifacts.PLAN_PATH, "plan")
    artifacts.write_text(
        str(tmp_path), artifacts.TASKS_PATH, '{"version": 1, "tasks": []}'
    )
    bundle = SimpleNamespace(
        id="bundle",
        project_key="project-a",
        schema_version=1,
        bundle_sha256="a" * 64,
        documents=lambda: _bundle_documents(),
    )
    meta = {
        "policy_bundle": project_policy.identity(bundle).as_dict(),
        "artifact_etags": artifacts.approved_etags(str(tmp_path)),
        "tasks_contract_etag": artifacts.tasks_contract_etag(str(tmp_path)),
    }
    task = SimpleNamespace(workspace_path=str(tmp_path), planning_meta=lambda: meta)
    project_policy.assert_contract_current(task, bundle)
    artifacts.write_text(str(tmp_path), artifacts.PLAN_PATH, "mutated")
    with pytest.raises(project_policy.PolicyError, match="PLAN.md changed"):
        project_policy.assert_contract_current(task, bundle)


def test_enforced_alias_can_disable_generic_status_tool():
    assert get_status_tools(
        "strict-builder", {"agent_team_disable_set_task_status": "true"}
    ) == []


@pytest.mark.asyncio
async def test_graph_total_attempt_cap_stops_before_generator(monkeypatch, tmp_path):
    monkeypatch.setattr(task_graph.artifacts, "task_list", lambda _ws: [
        {"id": "T1", "status": "pending", "depends_on": [], "title": "one"}
    ])
    monkeypatch.setattr(
        task_graph.artifacts,
        "next_runnable_task",
        lambda rows: rows[0],
    )
    monkeypatch.setattr(task_graph.task_journal, "record", lambda **_kwargs: None)
    called = False

    async def generator(_prompt: str):
        nonlocal called
        called = True
        raise AssertionError("generator must not run")

    result = await task_graph.run_task_graph(
        task_id="task",
        objective="bounded",
        workspace_path=str(tmp_path),
        run_generator=generator,
        make_evaluator=lambda _task: SimpleNamespace(),
        max_total_attempts=0,
        final_verify=False,
    )
    assert result.outcome == "capped"
    assert called is False


def test_strict_reviewer_prompt_has_no_builder_narrative_input():
    prompt = loop_service._build_strict_review_prompt(
        objective="objective",
        graph_task=None,
        conventions="",
        profiles=["code"],
        review_packet={"version": 1, "source_sha256": "abc"},
    )
    assert "Backend-owned review packet" in prompt
    assert '"source_sha256": "abc"' in prompt
    assert "FALSE_BUILDER_REPORT_CANARY" not in prompt
