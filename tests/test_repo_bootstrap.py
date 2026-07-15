from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_team.features.board.runtime.sandbox.base import ExecResult, SandboxError
from agent_team.features.repos import bootstrap


class _FakeSandbox:
    is_remote = False

    def __init__(self, results: list[ExecResult] | None = None) -> None:
        self.results = list(results or [ExecResult()])
        self.calls: list[tuple[str, str, int]] = []

    async def exec_shell(self, command, *, cwd=None, timeout_seconds=0, **_kwargs):
        self.calls.append((command, cwd, timeout_seconds))
        return self.results.pop(0) if self.results else ExecResult()


def _task_repo(tmp_path, slug: str = "web"):
    workspace = tmp_path / "task"
    repo = workspace / slug
    (repo / ".git").mkdir(parents=True)
    return workspace, repo


async def test_repo_bootstrap_runs_once_per_task_clone(monkeypatch, tmp_path):
    workspace, repo = _task_repo(tmp_path)
    spec = bootstrap.RepoBootstrapSpec("web", "npm ci")
    monkeypatch.setattr(bootstrap, "_configured_specs", lambda _board_id: [spec])
    sandbox = _FakeSandbox()
    profile = SimpleNamespace(workspace_mount_path="/workspace")

    await bootstrap.run_repo_bootstraps(
        sandbox,
        task_id="T-1",
        board_id="board-1",
        profile=profile,
        host_workspace_path=str(workspace),
    )
    assert sandbox.calls == [("npm ci", str(repo), 600)]
    marker = repo / ".git" / "agent-team" / "bootstrap.json"
    assert marker.is_file()

    # A later turn reuses the success marker and does not spend time reinstalling.
    await bootstrap.run_repo_bootstraps(
        sandbox,
        task_id="T-1",
        board_id="board-1",
        profile=profile,
        host_workspace_path=str(workspace),
    )
    assert len(sandbox.calls) == 1


async def test_repo_bootstrap_reruns_when_command_changes(monkeypatch, tmp_path):
    workspace, _repo = _task_repo(tmp_path)
    current = [bootstrap.RepoBootstrapSpec("web", "npm ci")]
    monkeypatch.setattr(bootstrap, "_configured_specs", lambda _board_id: current)
    sandbox = _FakeSandbox()
    profile = SimpleNamespace(workspace_mount_path="/workspace")

    kwargs = {
        "task_id": "T-2",
        "board_id": "board-1",
        "profile": profile,
        "host_workspace_path": str(workspace),
    }
    await bootstrap.run_repo_bootstraps(sandbox, **kwargs)
    current[:] = [bootstrap.RepoBootstrapSpec("web", "npm ci --ignore-scripts")]
    await bootstrap.run_repo_bootstraps(sandbox, **kwargs)
    assert [call[0] for call in sandbox.calls] == [
        "npm ci",
        "npm ci --ignore-scripts",
    ]


async def test_repo_bootstrap_failure_is_actionable_and_not_marked(monkeypatch, tmp_path):
    workspace, repo = _task_repo(tmp_path)
    monkeypatch.setattr(
        bootstrap,
        "_configured_specs",
        lambda _board_id: [bootstrap.RepoBootstrapSpec("web", "npm ci")],
    )
    sandbox = _FakeSandbox(
        [ExecResult(exit_code=17, stderr="registry unavailable")]
    )

    with pytest.raises(SandboxError, match="registry unavailable"):
        await bootstrap.run_repo_bootstraps(
            sandbox,
            task_id="T-3",
            board_id="board-1",
            profile=SimpleNamespace(workspace_mount_path="/workspace"),
            host_workspace_path=str(workspace),
        )

    assert not (repo / ".git" / "agent-team" / "bootstrap.json").exists()


async def test_repo_bootstrap_uses_mounted_repo_cwd(monkeypatch, tmp_path):
    workspace, _repo = _task_repo(tmp_path, "frontend")
    monkeypatch.setattr(
        bootstrap,
        "_configured_specs",
        lambda _board_id: [bootstrap.RepoBootstrapSpec("frontend", "pnpm install")],
    )
    sandbox = _FakeSandbox()
    sandbox.is_remote = True

    await bootstrap.run_repo_bootstraps(
        sandbox,
        task_id="T-4",
        board_id="board-1",
        profile=SimpleNamespace(workspace_mount_path="/workspace"),
        host_workspace_path=str(workspace),
    )
    assert sandbox.calls[0][1] == "/workspace/frontend"
