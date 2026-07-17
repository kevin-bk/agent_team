"""Human-gated publication of verified goal trees to merge/pull requests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    AgentTeamGoalPublication,
    AgentTeamGoalRun,
    AgentTeamTask,
)
from agent_team.features.board.runtime.loop.verification_runner import (
    capture_source_state,
)
from agent_team.features.repos import git_service, review_service
from agent_team.features.repos.models import AgentTeamRepo
from agent_team.features.repos.paths import task_copy_path
from agent_team.features.repos.repositories import repos_for_board
from agent_team.features.repos.task_copy import _run_git, task_branch_name


class PublicationError(RuntimeError):
    """The current workspace is not safe or ready to publish."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_publication(row: AgentTeamGoalPublication) -> dict:
    return {
        "id": row.id,
        "goal_run_id": row.goal_run_id,
        "task_id": row.task_id,
        "repo_slug": row.repo_slug,
        "source_branch": row.source_branch,
        "target_branch": row.target_branch,
        "tree_sha": row.tree_sha,
        "commit_sha": row.commit_sha,
        "remote_commit_sha": row.remote_commit_sha,
        "provider": row.provider,
        "request_number": row.request_number,
        "request_url": row.request_url,
        "request_title": row.request_title,
        "status": row.status,
        "pushed": row.pushed,
        "error": row.error,
        "published_by": row.published_by,
        "published_at": _iso(row.published_at),
        "created_at": _iso(row.created_at),
    }


def list_publications(
    db: Session, goal_run_id: str
) -> list[AgentTeamGoalPublication]:
    return (
        db.query(AgentTeamGoalPublication)
        .filter(AgentTeamGoalPublication.goal_run_id == goal_run_id)
        .order_by(AgentTeamGoalPublication.repo_slug.asc())
        .all()
    )


def _latest_verdict(execution: dict) -> str | None:
    attempts = execution.get("attempts") if isinstance(execution, dict) else []
    for attempt in reversed(attempts or []):
        evaluations = attempt.get("evaluations") if isinstance(attempt, dict) else []
        if evaluations:
            return str(evaluations[-1].get("verdict") or "") or None
    return None


def _changed_repo_slugs(goal: AgentTeamGoalRun) -> list[str]:
    workspace = goal.workspace_snapshot()
    changes = workspace.get("changes") if isinstance(workspace, dict) else {}
    files = changes.get("files") if isinstance(changes, dict) else []
    return sorted(
        {
            str(row.get("repo") or "")
            for row in files or []
            if isinstance(row, dict) and row.get("repo")
        }
    )


def _target_branch(dest: Path, configured: str | None) -> str:
    if configured and configured.strip():
        return configured.strip()
    code, out, _err = _run_git(
        "-C", str(dest), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
    )
    if code == 0 and out.strip():
        return out.strip().removeprefix("origin/")
    return "main"


def _stage_tree(dest: Path) -> str:
    code, _out, err = _run_git("-C", str(dest), "add", "-A")
    if code != 0:
        raise PublicationError(f"Could not stage {dest.name}: {err[:500]}")
    code, tree, err = _run_git("-C", str(dest), "write-tree")
    if code != 0 or not tree.strip():
        raise PublicationError(f"Could not fingerprint {dest.name}: {err[:500]}")
    return tree.strip()


def _commit_tree(dest: Path, message: str) -> tuple[str, str]:
    staged, _out, _err = _run_git("-C", str(dest), "diff", "--cached", "--quiet")
    if staged == 1:
        code, _out, err = _run_git("-C", str(dest), "commit", "-m", message)
        if code != 0:
            raise PublicationError(f"Commit failed in {dest.name}: {err[:500]}")
    elif staged != 0:
        raise PublicationError(f"Could not inspect staged changes in {dest.name}.")
    code, commit, err = _run_git("-C", str(dest), "rev-parse", "HEAD")
    if code != 0 or not commit.strip():
        raise PublicationError(f"Could not resolve commit in {dest.name}: {err[:500]}")
    code, tree, err = _run_git("-C", str(dest), "show", "-s", "--format=%T", "HEAD")
    if code != 0 or not tree.strip():
        raise PublicationError(f"Could not resolve commit tree in {dest.name}: {err[:500]}")
    return commit.strip(), tree.strip()


def _mr_description(goal: AgentTeamGoalRun, repo_slug: str) -> str:
    execution = goal.execution_snapshot()
    receipts = execution.get("receipts") if isinstance(execution, dict) else []
    return (
        "## Goal\n\n"
        f"{goal.objective or 'Agent Team goal'}\n\n"
        "## Verification\n\n"
        f"- Outcome: `{goal.outcome or goal.status}`\n"
        f"- Critic verdict: `{_latest_verdict(execution) or 'unknown'}`\n"
        f"- Trusted command receipts: {len(receipts or [])}\n"
        f"- Repository: `{repo_slug}`\n\n"
        "Created by Agent Team after explicit human approval of the current workspace tree."
    )


def _publish_event(task: AgentTeamTask) -> None:
    get_board_bus().publish(
        task.board_id,
        {
            "type": "goal.publication",
            "board_id": task.board_id,
            "task_id": task.id,
        },
    )


def publish_goal(
    goal_run_id: str, *, actor_id: str | None, draft: bool = False
) -> dict:
    """Commit/push human-approved workspace trees, then create review requests.

    A trusted PASS remains the prerequisite for showing the human delivery
    action. The confirmation approves the workspace as it exists at click time,
    including any human edits made after verification. Each call binds the
    current Git tree before performing external side effects.
    """
    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        goal = db.get(AgentTeamGoalRun, goal_run_id)
        if goal is None:
            raise PublicationError("Goal run not found.")
        task = db.get(AgentTeamTask, goal.task_id)
        if task is None:
            raise PublicationError("Task not found.")
        if str(task.planning_meta().get("current_goal_run_id") or "") != goal.id:
            raise PublicationError("Only the task's current goal can be published.")
        if goal.outcome != "complete" or _latest_verdict(goal.execution_snapshot()) != "pass":
            raise PublicationError(
                "The goal must have a trusted PASS verdict before publication."
            )

        slugs = _changed_repo_slugs(goal)
        if not slugs:
            raise PublicationError("The verified goal has no changed repositories.")
        assigned = {
            repo.slug: (repo, branch_override, board_allow_push)
            for repo, branch_override, board_allow_push, _is_wiki in repos_for_board(
                db, task.board_id
            )
            if repo.slug in slugs
        }
        missing = [slug for slug in slugs if slug not in assigned]
        if missing:
            raise PublicationError(
                f"Changed repositories are no longer assigned: {', '.join(missing)}."
            )

        existing = {row.repo_slug: row for row in list_publications(db, goal.id)}
        _source, approved_source_sha = capture_source_state(task.workspace_path)

        prepared: list[tuple[AgentTeamRepo, Path, AgentTeamGoalPublication]] = []
        source_branch = task_branch_name(task)
        for slug in slugs:
            repo, branch_override, board_allow_push = assigned[slug]
            if not (repo.allow_push and board_allow_push):
                raise PublicationError(
                    f"Push is not enabled for repository '{slug}' on this board."
                )
            dest = task_copy_path(task.workspace_path, slug)
            if not (dest / ".git").is_dir():
                raise PublicationError(f"Task repository '{slug}' is not prepared.")
            code, branch, _err = _run_git(
                "-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"
            )
            if code != 0 or branch.strip() != source_branch:
                raise PublicationError(
                    f"Repository '{slug}' must be on task branch '{source_branch}'."
                )
            tree_sha = _stage_tree(dest)
            target_branch = _target_branch(
                dest, branch_override or repo.default_branch
            )
            row = existing.get(slug)
            if row is None:
                row = AgentTeamGoalPublication(
                    goal_run_id=goal.id,
                    task_id=task.id,
                    repo_id=repo.id,
                    repo_slug=slug,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    tree_sha=tree_sha,
                    approved_source_sha256=approved_source_sha,
                    status="pending",
                    published_by=actor_id,
                )
                db.add(row)
                db.flush()
            elif row.status != "published" and row.tree_sha != tree_sha:
                # A fresh click is a fresh human approval of the current tree.
                # Reset partial delivery metadata so a retry commits and pushes
                # the newly approved content before creating the review request.
                row.tree_sha = tree_sha
                row.approved_source_sha256 = approved_source_sha
                row.commit_sha = None
                row.remote_commit_sha = None
                row.provider = None
                row.request_number = None
                row.request_url = None
                row.request_title = None
                row.status = "pending"
                row.pushed = False
                row.error = None
                row.published_by = actor_id
            prepared.append((repo, dest, row))
        # Persist the approved trees before the first irreversible external call.
        db.commit()

        title = f"{task.human_key}: {task.title}"[:500]
        commit_message = title[:250]
        for repo, dest, row in prepared:
            if row.status == "published" and row.request_url:
                continue
            try:
                commit_sha, committed_tree = _commit_tree(dest, commit_message)
                if committed_tree != row.tree_sha:
                    raise PublicationError(
                        f"Committed tree for '{repo.slug}' differs from the approved tree."
                    )
                row.commit_sha = commit_sha
                remote_sha = git_service.remote_branch_sha(repo, row.source_branch)
                if remote_sha != commit_sha:
                    pushed = git_service.push_branch(repo, str(dest), row.source_branch)
                    if not pushed.ok:
                        raise PublicationError(
                            f"Push failed for '{repo.slug}': {pushed.message}"
                        )
                    row.pushed = True
                    remote_sha = git_service.remote_branch_sha(repo, row.source_branch)
                else:
                    row.pushed = True
                row.remote_commit_sha = remote_sha or commit_sha
                review = review_service.create_review_request(
                    repo,
                    source_branch=row.source_branch,
                    target_branch=row.target_branch,
                    title=title,
                    description=_mr_description(goal, repo.slug),
                    draft=draft,
                )
                row.provider = review.provider
                row.request_number = review.number
                row.request_url = review.url
                row.request_title = review.title
                row.status = "published"
                row.error = None
                row.published_by = actor_id
                row.published_at = datetime.now(UTC)
            except (PublicationError, review_service.ReviewRequestError) as exc:
                row.status = "error"
                row.error = str(exc)[:2000]
            db.commit()

        rows = list_publications(db, goal.id)
        ok = bool(rows) and all(row.status == "published" for row in rows)
        _publish_event(task)
        return {
            "ok": ok,
            "publications": [serialize_publication(row) for row in rows],
            "detail": None if ok else "One or more repositories could not be published.",
        }
    finally:
        db.close()
