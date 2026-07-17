"""Focused tests for human-gated verified-tree publication."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_team.features.board.runtime import goal_publication
from agent_team.features.repos import review_service
from agent_team.features.repos.models import AUTH_TOKEN, AgentTeamRepo


def _repo(url: str) -> AgentTeamRepo:
    return AgentTeamRepo(
        name="Service",
        slug="service",
        git_url=url,
        auth_type=AUTH_TOKEN,
        auth_secret="test-token",
    )


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_review_project_detection_supports_gitlab_and_github():
    gitlab = review_service.parse_remote_project(
        _repo("git@gitlab.example.com:group/service.git")
    )
    assert gitlab.provider == "gitlab"
    assert gitlab.path == "group/service"
    assert gitlab.api_base == "https://gitlab.example.com/api/v4"

    github = review_service.parse_remote_project(
        _repo("https://github.com/acme/service.git")
    )
    assert github.provider == "github"
    assert github.path == "acme/service"
    assert github.api_base == "https://api.github.com"


def test_gitlab_publication_reuses_open_merge_request(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, url, *, headers, payload=None):
        calls.append((method, url, payload))
        return [
            {
                "iid": 42,
                "web_url": "https://gitlab.com/acme/service/-/merge_requests/42",
                "title": "Existing request",
            }
        ]

    monkeypatch.setattr(review_service, "_request_json", fake_request)
    result = review_service.create_review_request(
        _repo("https://gitlab.com/acme/service.git"),
        source_branch="agent-team/t-19",
        target_branch="main",
        title="T-19: feature",
        description="Verified",
        draft=False,
    )

    assert result.existing is True
    assert result.number == "42"
    assert result.url.endswith("/merge_requests/42")
    assert [call[0] for call in calls] == ["GET"]
    assert "source_branch=agent-team%2Ft-19" in calls[0][1]


def test_github_publication_creates_pull_request_after_lookup(monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, url, *, headers, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return []
        return {
            "number": 7,
            "html_url": "https://github.com/acme/service/pull/7",
            "title": payload["title"],
        }

    monkeypatch.setattr(review_service, "_request_json", fake_request)
    result = review_service.create_review_request(
        _repo("git@github.com:acme/service.git"),
        source_branch="agent-team/t-19",
        target_branch="main",
        title="T-19: feature",
        description="Verified",
        draft=True,
    )

    assert result.existing is False
    assert result.number == "7"
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][2] == {
        "head": "agent-team/t-19",
        "base": "main",
        "title": "T-19: feature",
        "body": "Verified",
        "draft": True,
    }


def test_approved_tree_remains_identical_after_backend_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Agent Team")
    _git(repo, "config", "user.email", "agent-team@example.com")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")

    (repo / "feature.txt").write_text("verified change\n")
    approved_tree = goal_publication._stage_tree(repo)
    commit_sha, committed_tree = goal_publication._commit_tree(repo, "T-19: feature")

    assert committed_tree == approved_tree
    assert _git(repo, "rev-parse", "HEAD") == commit_sha
    assert _git(repo, "status", "--porcelain") == ""
