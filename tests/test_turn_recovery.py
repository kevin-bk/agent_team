"""Focused tests for turn-aware workspace checkpointing."""

from __future__ import annotations

import subprocess

from agent_team.features.board.runtime import turn_recovery as recovery


def _git(repo, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(workspace):
    repo = workspace / "app"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "clean.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "dirty.py").write_text("before = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_snapshot_delta_excludes_unchanged_preexisting_dirty_files(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _repo(workspace)

    # State that predates the turn must become its baseline, not recovery work.
    (repo / "dirty.py").write_text("before = False\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("already here\n", encoding="utf-8")
    agent_dir = workspace / ".agent-team"
    agent_dir.mkdir()
    (agent_dir / "SPEC.md").write_text("old spec\n", encoding="utf-8")

    before = recovery.capture_workspace(str(workspace), repo_paths=[str(repo)])

    # The turn commits one clean file, further edits one pre-dirty file and edits
    # a planning artifact. The untouched untracked file must not appear.
    (repo / "clean.py").write_text("value = 2\n", encoding="utf-8")
    _git(repo, "add", "clean.py")
    _git(repo, "commit", "-m", "turn commit")
    (repo / "dirty.py").write_text("before = False\nafter = True\n", encoding="utf-8")
    (agent_dir / "SPEC.md").write_text("new spec\n", encoding="utf-8")

    current = recovery.capture_workspace(str(workspace), repo_paths=[str(repo)])
    delta = recovery.compare_snapshots(str(workspace), before, current)
    by_path = {row["path"]: row["change"] for row in delta["changed_files"]}

    assert by_path["app/clean.py"] == "modified"
    assert by_path["app/dirty.py"] == "modified"
    assert by_path[".agent-team/SPEC.md"] == "modified"
    assert "app/scratch.txt" not in by_path


def test_snapshot_detects_added_deleted_and_restored_state(tmp_path):
    workspace = tmp_path / "workspace"
    repo = _repo(workspace)
    (repo / "dirty.py").write_text("preexisting dirty\n", encoding="utf-8")
    (repo / "old.tmp").write_text("preexisting untracked\n", encoding="utf-8")
    before = recovery.capture_workspace(str(workspace), repo_paths=["app"])

    _git(repo, "checkout", "--", "dirty.py")
    (repo / "old.tmp").unlink()
    (repo / "new.py").write_text("new = True\n", encoding="utf-8")

    current = recovery.capture_workspace(str(workspace), repo_paths=["app"])
    delta = recovery.compare_snapshots(str(workspace), before, current)
    by_path = {row["path"]: row["change"] for row in delta["changed_files"]}

    assert by_path == {
        "app/dirty.py": "restored",
        "app/new.py": "added",
        "app/old.tmp": "deleted",
    }


def test_root_repo_is_not_duplicated_as_loose_files(tmp_path):
    repo = _repo(tmp_path)
    # _repo created tmp_path/app; use that directory itself as the workspace root.
    snapshot = recovery.capture_workspace(str(repo), repo_paths=[str(repo)])
    assert snapshot["repos"][0]["path"] == "."
    assert snapshot["loose"] == {}
