"""Tests for the agent_team plugin (board feature)."""

from __future__ import annotations

import json

import pytest
from agent_team.features.board.keys import next_human_key, slugify
from agent_team.features.board.models import (
    AgentTeamActivity,
    AgentTeamAttempt,
    AgentTeamAutopilot,
    AgentTeamTaskSchedule,
    AgentTeamBoard,
    AgentTeamBoardMember,
    AgentTeamComment,
    AgentTeamConversation,
    AgentTeamEvaluation,
    AgentTeamJournalEntry,
    AgentTeamKeySeq,
    AgentTeamRun,
    AgentTeamRunEvent,
    AgentTeamTask,
    AgentTeamToolOutput,
)
from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import tasks as tasks_repo
from agent_team.features.board.workspace import workspace_path_for
from agent_team.features.repos.models import AgentTeamBoardRepo, AgentTeamRepo
from agent_team.plugin import SPA_MOUNT_PATH, AgentTeamPlugin
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_PLUGIN_MODELS = (
    AgentTeamKeySeq,
    AgentTeamTaskSchedule,
    AgentTeamBoard,
    AgentTeamBoardMember,
    AgentTeamTask,
    AgentTeamConversation,
    AgentTeamRun,
    AgentTeamRunEvent,
    AgentTeamComment,
    AgentTeamActivity,
    AgentTeamRepo,
    AgentTeamBoardRepo,
    AgentTeamToolOutput,
    AgentTeamAutopilot,
    AgentTeamAttempt,
    AgentTeamEvaluation,
    AgentTeamJournalEntry,
)


@pytest.fixture()
def db(monkeypatch):
    """In-memory SQLite session with the plugin tables created.

    The event store opens its own ``SessionLocal``; point it at this engine so
    store helpers and the test share one database.
    """
    # Import core models so the ``users`` FK target is registered in the shared
    # metadata before the plugin tables (which reference it) are created.
    from core.database import models as core_models

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    core_models.User.__table__.create(bind=engine, checkfirst=True)
    for model in _PLUGIN_MODELS:
        model.__table__.create(bind=engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from agent_team.features.board.repositories import activity as activity_repo
    from agent_team.features.board.runtime import event_store, local_backend

    monkeypatch.setattr(event_store, "SessionLocal", factory)
    monkeypatch.setattr(local_backend, "SessionLocal", factory)
    monkeypatch.setattr(activity_repo, "SessionLocal", factory)

    # ``git_service``/``scheduler`` open their own session via this attribute.
    import core.database.base as core_db

    monkeypatch.setattr(core_db, "SessionLocal", factory)

    session = factory()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Plugin wiring
# ---------------------------------------------------------------------------


def test_plugin_meta_models_and_menu():
    plugin = AgentTeamPlugin()
    assert plugin.meta().name == "agent_team"
    assert [m.__tablename__ for m in plugin.models()] == [
        "plugin_agent_team_key_seq",
        "plugin_agent_team_board",
        "plugin_agent_team_board_member",
        "plugin_agent_team_task",
        "plugin_agent_team_conversation",
        "plugin_agent_team_run",
        "plugin_agent_team_run_event",
        "plugin_agent_team_comment",
        "plugin_agent_team_activity",
        "plugin_agent_team_repo",
        "plugin_agent_team_board_repo",
        "plugin_agent_team_tool_output",
        "plugin_agent_team_autopilot",
        "plugin_agent_team_task_schedule",
        "plugin_agent_team_attempt",
        "plugin_agent_team_evaluation",
        "plugin_agent_team_journal_entry",
    ]
    menu = plugin.menu_items()
    assert len(menu) == 1
    assert menu[0].url == f"{SPA_MOUNT_PATH}/"


# ---------------------------------------------------------------------------
# Keys + workspace
# ---------------------------------------------------------------------------


def test_human_keys_increment_per_prefix(db):
    assert next_human_key(db, "T") == "T-1"
    assert next_human_key(db, "T") == "T-2"
    assert next_human_key(db, "R") == "R-1"


def test_slugify():
    assert slugify("My Board!") == "my-board"
    assert slugify("   ") == "board"


def test_workspace_path_rejects_traversal():
    assert workspace_path_for("team", "T-1").endswith("/team/T-1")
    for bad in ["../etc", "a/b", "..", ""]:
        with pytest.raises(ValueError):
            workspace_path_for("team", bad)
        with pytest.raises(ValueError):
            workspace_path_for(bad, "T-1")


def test_resolve_in_workspace_accepts_relative_and_inside_absolute(tmp_path):
    from agent_team.features.board.workspace import resolve_in_workspace

    base = tmp_path / "team" / "T-1"
    base.mkdir(parents=True)
    target = base / "out.txt"
    target.write_text("x")

    # Relative path (file-tree style) resolves under the workspace.
    assert resolve_in_workspace(str(base), "out.txt") == target.resolve()
    # Absolute path inside the workspace (agent-tool style) is accepted as-is.
    assert resolve_in_workspace(str(base), str(target)) == target.resolve()
    # Absolute path outside the workspace is still rejected as an escape.
    with pytest.raises(ValueError):
        resolve_in_workspace(str(base), str(tmp_path / "elsewhere.txt"))


# ---------------------------------------------------------------------------
# Board + task CRUD
# ---------------------------------------------------------------------------


def test_create_board_uses_default_columns_and_unique_slug(db):
    first = boards_repo.create_board(
        db, name="Team", description="d", columns=None, owner_id=None
    )
    second = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.commit()
    assert first.slug == "team"
    assert second.slug == "team-2"
    assert [c["key"] for c in first.columns()] == [
        "pending",
        "todo",
        "in_progress",
        "review",
        "done",
    ]


def test_create_task_sets_key_position_and_workspace(db):
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    t1 = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="First",
        description=None,
        status="todo",
        assignee_id=None,
        labels=["x", "y"],
        priority="high",
        created_by=None,
    )
    t2 = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="Second",
        description=None,
        status="todo",
        assignee_id=None,
        labels=None,
        priority=None,
        created_by=None,
    )
    db.commit()

    assert (t1.human_key, t2.human_key) == ("T-1", "T-2")
    assert t2.position > t1.position
    assert t1.workspace_path.endswith("/team/T-1")

    dto = tasks_repo.serialize_task(t1)
    assert dto.labels == ["x", "y"]
    assert dto.priority == "high"
    assert dto.task_type == "task"  # default when not specified

    counts = boards_repo.task_counts_by_board(db, [board.id])
    assert counts[board.id] == 2


def test_task_type_create_and_serialize(db):
    """task_type: defaults to "task", round-trips through create + serialize."""
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    bug = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="Crash",
        description=None,
        status="todo",
        assignee_id=None,
        labels=None,
        priority=None,
        created_by=None,
        task_type="bug",
    )
    db.commit()

    assert bug.task_type == "bug"
    assert tasks_repo.serialize_task(bug).task_type == "bug"


# ---------------------------------------------------------------------------
# Jira sync (Phase 1)
# ---------------------------------------------------------------------------


def test_board_jira_config_serialize_never_leaks_token(db):
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    board.jira_enabled = True
    board.jira_base_url = "https://acme.atlassian.net"
    board.jira_email = "svc@acme.io"
    board.jira_project_key = "ACME"
    board.jira_api_token = "tok-123"
    db.commit()

    dto = boards_repo.serialize_board(board)
    assert dto.jira_enabled is True
    assert dto.jira_base_url == "https://acme.atlassian.net"
    assert dto.jira_project_key == "ACME"
    assert dto.jira_has_token is True
    # The token must never appear in the wire payload — only its presence.
    dumped = dto.model_dump()
    assert "jira_api_token" not in dumped
    assert "tok-123" not in json.dumps(dumped)


def test_jira_build_task_changes_maps_fields(db):
    from agent_team.features.board.jira.sync import build_task_changes

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    issue = {
        "fields": {
            "summary": "Fix login",
            "description": "Repro steps",
            "status": {"name": "In Progress"},
            "priority": {"name": "High"},
            "issuetype": {"name": "Bug"},
            "labels": ["backend", "urgent"],
        }
    }

    changes = build_task_changes(issue, board=board)
    assert changes["title"] == "Fix login"
    assert changes["description"] == "Repro steps"
    # "In Progress" status matches the default column of the same name.
    assert changes["status"] == "in_progress"
    assert changes["priority"] == "high"
    assert changes["task_type"] == "bug"
    assert changes["labels"] == ["backend", "urgent"]


def test_jira_build_task_changes_extracts_people_emails(db):
    """Assignee/reporter account emails surface for later user mapping."""
    from agent_team.features.board.jira.sync import build_task_changes

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    issue = {
        "fields": {
            "summary": "t",
            "assignee": {"emailAddress": "Alice@Example.com", "displayName": "A"},
            "reporter": {"emailAddress": "bob@example.com", "displayName": "B"},
        }
    }
    changes = build_task_changes(issue, board=board)
    assert changes["assignee_email"] == "Alice@Example.com"
    assert changes["reporter_email"] == "bob@example.com"

    # Hidden email (GDPR) → no key, so nothing to map.
    hidden = {"fields": {"summary": "t", "assignee": {"displayName": "A"}}}
    assert "assignee_email" not in build_task_changes(hidden, board=board)


def test_jira_apply_maps_people_and_status_toggle(db, monkeypatch):
    """Sync maps assignee/reporter by email and honours jira_sync_status."""
    from agent_team.features.board.jira import service as jira_service

    alice = _make_user(db, username="alice")
    bob = _make_user(db, username="bob")
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    board.jira_base_url = "https://acme.atlassian.net"
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="t", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()

    class FakeClient:
        def get_issue(self, key):
            return {
                "fields": {
                    "summary": "Synced",
                    "status": {"name": "In Progress"},
                    "assignee": {"emailAddress": "alice@example.com"},
                    "reporter": {"emailAddress": "BOB@example.com"},
                }
            }

        def get_comments(self, key, *, max_results=200):
            return []

        def browse_url(self, key):
            return f"https://acme.atlassian.net/browse/{key}"

    client = FakeClient()

    # Default (jira_sync_status True): status + people all applied.
    applied = jira_service.apply_issue_to_task(
        db, board=board, task=task, client=client, key="ACME-1", actor_id=None
    )
    db.flush()
    assert task.status == "in_progress"
    assert task.assignee_id == alice.id
    assert task.reporter_id == bob.id  # case-insensitive email match
    assert "assignee" in applied and "reporter" in applied and "status" in applied

    # Turn off status overwrite: a later sync keeps the local status.
    board.jira_sync_status = False
    task.status = "done"
    db.flush()
    applied2 = jira_service.apply_issue_to_task(
        db, board=board, task=task, client=client, key="ACME-1", actor_id=None
    )
    db.flush()
    assert task.status == "done"
    assert "status" not in applied2


def test_jira_task_matches_filter():
    from types import SimpleNamespace

    from agent_team.features.board.jira.service import task_matches_filter

    task = SimpleNamespace(status="todo", task_type="bug", assignee_id="u1")
    assert task_matches_filter(task, {}) is True
    assert task_matches_filter(task, {"statuses": ["todo", "done"]}) is True
    assert task_matches_filter(task, {"statuses": ["done"]}) is False
    assert task_matches_filter(task, {"task_types": ["bug"]}) is True
    assert task_matches_filter(task, {"task_types": ["story"]}) is False
    assert task_matches_filter(task, {"assignee_ids": ["u1"]}) is True
    assert task_matches_filter(task, {"assignee_ids": ["u2"]}) is False
    # Clauses are AND-ed.
    assert (
        task_matches_filter(task, {"statuses": ["todo"], "task_types": ["story"]})
        is False
    )


def test_jira_sync_board_batch(db, monkeypatch):
    from agent_team.features.board.jira import service as jira_service

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    board.jira_enabled = True
    board.jira_base_url = "https://acme.atlassian.net"
    board.jira_email = "svc@acme.io"
    board.jira_api_token = "tok"
    board.jira_sync_filter_json = json.dumps({"statuses": ["todo"]})
    db.flush()

    linked = tasks_repo.create_task(
        db, board_id=board.id, title="A", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    linked.jira_key = "ACME-1"
    # Has a key but is filtered out by status → skipped.
    other_status = tasks_repo.create_task(
        db, board_id=board.id, title="B", description=None, status="done",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    other_status.jira_key = "ACME-2"
    # No key → skipped.
    tasks_repo.create_task(
        db, board_id=board.id, title="C", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()

    class FakeClient:
        def get_issue(self, key):
            return {"fields": {"summary": f"Synced {key}", "description": "x"}}

        def get_comments(self, key, *, max_results=200):
            return []

        def browse_url(self, key):
            return f"https://acme.atlassian.net/browse/{key}"

    monkeypatch.setattr(jira_service, "build_client", lambda board: FakeClient())

    tasks = tasks_repo.list_tasks(db, board_id=board.id)
    result = jira_service.sync_board(db, board=board, tasks=tasks, actor_id=None)
    db.commit()

    assert result.synced == 1
    assert result.skipped == 2
    assert result.failed == 0
    db.refresh(linked)
    assert linked.title == "Synced ACME-1"
    assert linked.jira_url.endswith("/browse/ACME-1")


def test_jira_priority_aliases(db):
    """Common Jira priority schemes map onto the local 5-level scale."""
    from agent_team.features.board.jira.sync import build_task_changes

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()

    def prio(name):
        issue = {"fields": {"summary": "x", "priority": {"name": name}}}
        return build_task_changes(issue, board=board).get("priority")

    assert prio("High") == "high"
    assert prio("Critical") == "high"
    assert prio("Blocker") == "highest"
    assert prio("Major") == "medium"
    assert prio("Minor") == "low"
    assert prio("Trivial") == "lowest"
    assert prio("P1") == "highest"
    # Numeric scheme (as used by e.g. the live CHIZY project).
    assert prio("1") == "highest"
    assert prio("2") == "high"
    assert prio("3") == "medium"
    assert prio("Totally Unknown") is None


def test_jira_type_and_status_mapping(db):
    """Issue-type synonyms and status-category fallback map onto the board."""
    from agent_team.features.board.jira.sync import build_task_changes

    board = boards_repo.create_board(  # default columns: pending/todo/in_progress/review/done
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()

    def chg(issuetype=None, status=None, category=None):
        fields = {"summary": "x"}
        if issuetype:
            fields["issuetype"] = {"name": issuetype}
        if status:
            fields["status"] = {
                "name": status,
                "statusCategory": {"name": category} if category else {},
            }
        return build_task_changes({"fields": fields}, board=board)

    # "Feature" (and friends) → story; Bug/Task pass through.
    assert chg(issuetype="Feature").get("task_type") == "story"
    assert chg(issuetype="Bug").get("task_type") == "bug"
    assert chg(issuetype="Sub-task").get("task_type") == "subtask"
    assert "task_type" not in chg(issuetype="Spaceship")  # unknown → untouched

    # Exact column-name match.
    assert chg(status="Review", category="In Progress").get("status") == "review"
    # No column named "Testing" → fall back to its category (→ in_progress).
    assert chg(status="Testing", category="In Progress").get("status") == "in_progress"
    # "To Do" category normalizes to the "Todo" column.
    assert chg(status="Backlog", category="To Do").get("status") == "todo"


def test_jira_build_search_jql():
    from agent_team.features.board.jira.service import build_search_jql

    # No filter → just the project, newest first.
    assert (
        build_search_jql("CHIZY", {})
        == 'project = "CHIZY" ORDER BY updated DESC'
    )
    # All clauses are AND-ed in order.
    jql = build_search_jql(
        "CHIZY",
        {
            "issue_types": ["Story", "Bug"],
            "status_categories": ["To Do", "In Progress"],
            "updated_within_days": 30,
        },
    )
    assert 'project = "CHIZY"' in jql
    assert 'issuetype in ("Story", "Bug")' in jql
    assert 'statusCategory in ("To Do", "In Progress")' in jql
    assert "updated >= -30d" in jql
    assert jql.endswith("ORDER BY updated DESC")
    # Zero/None days adds no recency clause.
    assert "updated >=" not in build_search_jql("X", {"updated_within_days": 0})


def test_jira_import_comments_dedup(db):
    """Jira comments import once, keep their author name, and don't duplicate."""
    from agent_team.features.board.jira import service as jira_service
    from agent_team.features.board.repositories import comments as comments_repo

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    task.jira_key = "ACME-1"
    db.flush()

    class FakeClient:
        payload = [
            {"id": "10", "author": {"displayName": "Alice"}, "body": "first"},
            {"id": "11", "author": {"displayName": "Bob"}, "body": "second"},
            {"id": "12", "author": {}, "body": "   "},  # blank → skipped
        ]

        def get_comments(self, key, *, max_results=200):
            return FakeClient.payload

    created, updated = jira_service.import_comments(
        db, task=task, client=FakeClient(), key="ACME-1"
    )
    assert (created, updated) == (2, 0)
    rows = comments_repo.list_comments(db, task.id)
    assert [c.body for c in rows] == ["first", "second"]
    assert rows[0].external_author == "Alice"
    assert rows[0].author_id is None
    assert rows[0].visible_to_agents is True

    # Re-import unchanged → nothing created or updated.
    assert jira_service.import_comments(
        db, task=task, client=FakeClient(), key="ACME-1"
    ) == (0, 0)
    assert len(comments_repo.list_comments(db, task.id)) == 2

    # Edit on the Jira side → existing comment is updated in place, not duplicated.
    FakeClient.payload = [
        {"id": "10", "author": {"displayName": "Alice"}, "body": "first (edited)"},
        {"id": "11", "author": {"displayName": "Bob"}, "body": "second"},
    ]
    assert jira_service.import_comments(
        db, task=task, client=FakeClient(), key="ACME-1"
    ) == (0, 1)
    rows = comments_repo.list_comments(db, task.id)
    assert [c.body for c in rows] == ["first (edited)", "second"]
    assert len(rows) == 2


def test_jira_import_attachments(db, tmp_path, monkeypatch):
    """Issue attachments download into the workspace and refresh on re-import."""
    import os

    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path))
    from agent_team.features.board.jira import service as jira_service
    from agent_team.features.board.repositories import comments as comments_repo

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()

    class FakeClient:
        def download(self, url):
            return b"bytes-of-" + url.encode()

    issue = {
        "fields": {
            "attachment": [
                {"id": "a1", "filename": "spec.pdf", "content": "https://j/a1",
                 "mimeType": "application/pdf"},
                {"id": "a2", "filename": "img.png", "content": "https://j/a2",
                 "mimeType": "image/png"},
            ]
        }
    }
    saved, name_to_path = jira_service.download_issue_attachments(
        db, task=task, client=FakeClient(), issue=issue
    )
    n = jira_service.write_attachments_note(
        db, task=task, saved=saved, referenced=set()
    )
    assert n == 2
    # Stable jira_<id> folders + a filename→path map for inline rewriting.
    assert set(name_to_path) == {"spec.pdf", "img.png"}
    assert all(p.startswith("_notes/jira_") for p in name_to_path.values())
    rows = comments_repo.list_comments(db, task.id)
    assert len(rows) == 1
    files = rows[0].attachments()
    assert {f["filename"] for f in files} == {"spec.pdf", "img.png"}
    # Paths are workspace-relative and the bytes were physically written.
    for f in files:
        assert os.path.exists(os.path.join(task.workspace_path, f["path"]))

    # Re-import with a different set → old note + files wiped, new ones written.
    issue2 = {
        "fields": {
            "attachment": [
                {"id": "a3", "filename": "new.txt", "content": "https://j/a3",
                 "mimeType": "text/plain"},
            ]
        }
    }
    saved2, _ = jira_service.download_issue_attachments(
        db, task=task, client=FakeClient(), issue=issue2
    )
    n2 = jira_service.write_attachments_note(
        db, task=task, saved=saved2, referenced=set()
    )
    assert n2 == 1
    rows = comments_repo.list_comments(db, task.id)
    assert len(rows) == 1
    assert [f["filename"] for f in rows[0].attachments()] == ["new.txt"]
    # The old jira_a1/jira_a2 folders are gone, only the new file remains.
    notes_dir = os.path.join(task.workspace_path, "_notes")
    jira_dirs = [d for d in os.listdir(notes_dir) if d.startswith("jira_")]
    assert jira_dirs == ["jira_a3"]


def test_view_image_tool(tmp_path):
    """The agent_team view_image tool returns workspace images as image blocks."""
    from agent_team.features.board.runtime.image_tools import get_image_tools

    from plugins.standard_tools.tools.workspace_override import (
        reset_workspace_override,
        set_workspace_override,
    )

    img_dir = tmp_path / "_notes" / "jira_a1"
    img_dir.mkdir(parents=True)
    (img_dir / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / "secret.png").write_bytes(b"x")  # exists but referenced via ..

    # Root is bound at tool-creation time via the same override the file tools use.
    token = set_workspace_override(str(tmp_path))
    try:
        tools = get_image_tools("alice", {})
    finally:
        reset_workspace_override(token)
    assert len(tools) == 1
    view_image = tools[0]
    assert view_image.name == "view_image"

    out = view_image.invoke({"path": "_notes/jira_a1/shot.png"})
    assert isinstance(out, list)
    img_block = next(b for b in out if b.get("type") == "image_url")
    assert img_block["image_url"]["url"].startswith("data:image/png;base64,")

    # A non-image file is rejected with a text-only explanation.
    txt = view_image.invoke({"path": "notes.txt"})
    assert all(b.get("type") == "text" for b in txt)
    assert "not a viewable image" in txt[0]["text"].lower()

    # Missing file and path traversal are handled, not raised.
    assert "not found" in view_image.invoke({"path": "nope.png"})[0]["text"].lower()
    assert "outside" in view_image.invoke({"path": "../secret.png"})[0]["text"].lower()


def test_view_image_tool_registered_on_plugin():
    """The plugin exposes view_image as a default-enabled tool factory."""
    from agent_team.plugin import AgentTeamPlugin

    factories = {f.key: f for f in AgentTeamPlugin().tool_factories()}
    assert "enable_agent_team_view_image" in factories
    factory = factories["enable_agent_team_view_image"]
    assert factory.default_enabled is True
    # The factory builds the tool on demand.
    tools = factory.create_tools("alice", {})
    assert [t.name for t in tools] == ["view_image"]


def test_jira_rewrite_inline_media():
    """Inline ``!file!`` / ``[^file]`` markup is rewritten to local Markdown."""
    from agent_team.features.board.jira import service as jira_service

    name_to_path = {
        "img.png": "_notes/jira_a2/img.png",
        "spec.pdf": "_notes/jira_a1/spec.pdf",
    }
    text = (
        "See !img.png|width=635,alt=\"img.png\"! and the doc [^spec.pdf]. "
        "Exciting news! No match here!"
    )
    out, referenced = jira_service.rewrite_jira_media(text, name_to_path)
    assert "![img.png](<_notes/jira_a2/img.png>)" in out
    assert "[spec.pdf](<_notes/jira_a1/spec.pdf>)" in out
    # Plain exclamation text isn't a known attachment → left untouched.
    assert "Exciting news! No match here!" in out
    assert referenced == {"img.png", "spec.pdf"}


def test_jira_inline_attachments_excluded_from_note(db, tmp_path, monkeypatch):
    """Attachments embedded in the description don't duplicate into the note."""
    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path))
    from agent_team.features.board.jira import service as jira_service
    from agent_team.features.board.repositories import comments as comments_repo

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()

    class FakeClient:
        def get_issue(self, key):
            return self.issue

        def get_comments(self, key, *, max_results=200):
            return []

        def download(self, url):
            return b"bytes"

        def browse_url(self, key):
            return f"https://acme.atlassian.net/browse/{key}"

    fake = FakeClient()
    fake.issue = {
        "fields": {
            "summary": "Has inline image",
            "description": "Look: !inline.png! and a loose file.",
            "attachment": [
                {"id": "a1", "filename": "inline.png", "content": "https://j/a1",
                 "mimeType": "image/png"},
                {"id": "a2", "filename": "loose.pdf", "content": "https://j/a2",
                 "mimeType": "application/pdf"},
            ],
        }
    }
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()

    jira_service.apply_issue_to_task(
        db, board=board, task=task, client=fake, key="ACME-9", actor_id=None,
    )
    # Description now embeds the inline image via a local Markdown path.
    assert "![inline.png](<_notes/jira_a1/inline.png>)" in (task.description or "")
    # Only the non-inlined attachment lands in the catalog note.
    notes = [c for c in comments_repo.list_comments(db, task.id)
             if c.jira_comment_id == jira_service._ATTACH_COMMENT_ID]
    assert len(notes) == 1
    assert [f["filename"] for f in notes[0].attachments()] == ["loose.pdf"]


def test_jira_import_create_and_update(db):
    """The import path creates a task for a new key and updates an existing one."""
    from agent_team.features.board.jira import service as jira_service

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    first_col = board.columns()[0]["key"]

    class FakeClient:
        def get_comments(self, key, *, max_results=200):
            return []

        def browse_url(self, key):
            return f"https://acme.atlassian.net/browse/{key}"

    issue = {"fields": {"summary": "Imported", "description": "body"}}

    # New key → no linked task yet, caller creates one then applies the issue.
    assert (
        tasks_repo.get_task_by_jira_key(db, board_id=board.id, jira_key="ACME-9")
        is None
    )
    created = tasks_repo.create_task(
        db, board_id=board.id, title="ACME-9", description=None, status=first_col,
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    jira_service.apply_issue_to_task(
        db, board=board, task=created, client=FakeClient(),
        key="ACME-9", actor_id=None, issue=issue,
    )
    db.flush()
    assert created.title == "Imported"
    assert created.jira_key == "ACME-9"
    assert created.jira_url.endswith("/browse/ACME-9")

    # Same key again → resolves to the existing task (update, not duplicate).
    found = tasks_repo.get_task_by_jira_key(db, board_id=board.id, jira_key="ACME-9")
    assert found is not None and found.id == created.id


def test_jira_build_task_changes_honours_mappings_and_unknowns(db):
    from agent_team.features.board.jira.sync import build_task_changes

    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    board.jira_mappings_json = json.dumps({"status": {"QA": "review"}})
    db.flush()
    issue = {
        "fields": {
            "summary": "Task",
            "description": None,
            "status": {"name": "QA"},
            "priority": {"name": "Wishlist"},  # not a known priority alias
            "issuetype": {"name": "Spaceship"},  # not a known local task type
            "labels": [],
        }
    }

    changes = build_task_changes(issue, board=board)
    # Configured mapping wins over name matching.
    assert changes["status"] == "review"
    # Null description clears; unknown priority/type are left untouched.
    assert changes["description"] is None
    assert "priority" not in changes
    assert "task_type" not in changes
    assert changes["labels"] == []


def test_task_counts_ignore_archived(db):
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="T",
        description=None,
        status="todo",
        assignee_id=None,
        labels=None,
        priority=None,
        created_by=None,
    )
    task.archived = True
    db.commit()
    assert boards_repo.task_counts_by_board(db, [board.id]) == {}


# ---------------------------------------------------------------------------
# Event store (runtime)
# ---------------------------------------------------------------------------


def _make_run(db, *, status="queued", human_key="R-1") -> AgentTeamRun:
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="T",
        description=None,
        status="todo",
        assignee_id=None,
        labels=None,
        priority=None,
        created_by=None,
    )
    db.flush()
    run = AgentTeamRun(
        human_key=human_key,
        task_id=task.id,
        agent_alias="alice",
        thread_id="agentteam:t:alice:1",
        trigger="mention",
        status=status,
        prompt="do it",
    )
    db.add(run)
    db.commit()
    return run


def test_event_store_append_assigns_monotonic_seq_and_replays(db):
    from agent_team.features.board.runtime import event_store
    from agent_team.features.board.runtime import events as ev

    run = _make_run(db)

    assert event_store.append_event(run.id, *ev.run_start(agent_alias="alice")) == 1
    assert event_store.append_event(run.id, *ev.text_delta("hello ")) == 2
    assert event_store.append_event(run.id, *ev.text_delta("world")) == 3

    db.refresh(run)
    assert run.last_seq == 3

    all_frames = event_store.list_events(run.id)
    assert [f["seq"] for f in all_frames] == [1, 2, 3]
    assert all_frames[0]["type"] == ev.EVENT_RUN_START
    assert all_frames[1]["data"]["text"] == "hello "

    # Resume from a cursor returns only newer frames.
    tail = event_store.list_events(run.id, after_seq=1)
    assert [f["seq"] for f in tail] == [2, 3]


def test_event_store_status_transitions_and_finalize(db):
    from agent_team.features.board.runtime import event_store
    from agent_team.features.board.runtime.events import RUN_DONE, RUN_RUNNING

    run = _make_run(db)

    event_store.mark_running(run.id)
    assert event_store.get_run_status(run.id) == RUN_RUNNING
    db.refresh(run)
    assert run.started_at is not None

    event_store.finalize_run(
        run.id,
        status=RUN_DONE,
        final_answer="done",
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        cli_usage_text="45,000/200,000 tokens",
    )
    db.refresh(run)
    assert run.status == RUN_DONE
    assert run.final_answer == "done"
    assert run.total_tokens == 15
    assert run.cli_usage_text == "45,000/200,000 tokens"
    assert run.ended_at is not None


def test_event_store_cancel_queued_vs_running(db):
    from agent_team.features.board.runtime import event_store

    queued = _make_run(db, status="queued")
    assert event_store.request_cancel(queued.id) == "cancelled"
    assert event_store.get_run_status(queued.id) == "cancelled"

    running = _make_run(db, status="queued", human_key="R-2")
    event_store.mark_running(running.id)
    assert event_store.request_cancel(running.id) == "requested"
    assert event_store.is_cancel_requested(running.id) is True
    assert event_store.get_run_status(running.id) == "running"


# ---------------------------------------------------------------------------
# Stream translator
# ---------------------------------------------------------------------------


def test_translator_pairs_tools_and_tracks_final_text():
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage, ToolMessage

    translator = StreamTranslator()

    call = {
        "agent": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "shell", "args": {"cmd": "ls"}, "id": "x"}],
                )
            ]
        }
    }
    result = {"tools": {"messages": [ToolMessage(content="a b", name="shell", tool_call_id="x")]}}
    answer = {"agent": {"messages": [AIMessage(content="Done: listed files")]}}

    start_frames = translator.translate(call)
    end_frames = translator.translate(result)
    answer_frames = translator.translate(answer)

    assert [t for t, _ in start_frames] == [ev.EVENT_TOOL_USE_START]
    assert [t for t, _ in end_frames] == [ev.EVENT_TOOL_USE_END]
    # start and end refer to the same tool_id so the UI can pair them.
    assert start_frames[0][1]["tool_id"] == end_frames[0][1]["tool_id"]
    assert end_frames[0][1]["is_error"] is False

    # ``updates`` snapshots no longer re-stream text (it streamed live via the
    # ``messages`` mode); the snapshot only captures the final answer.
    assert answer_frames == []
    assert translator.final_text == "Done: listed files"


def test_normalize_tool_input_maps_standard_arg_names():
    from agent_team.features.board.runtime.translator import normalize_tool_input

    # LangChain WriteFileTool stores the file body under ``text``; the cockpit
    # renders writes from ``content`` so the adapter mirrors it across.
    write = normalize_tool_input("write_file", {"file_path": "a.txt", "text": "hello"})
    assert write["content"] == "hello"
    assert write["file_path"] == "a.txt"

    # The shell tool takes ``commands`` (str | list); surface a single
    # ``command`` string for the UI's inline summary.
    shell = normalize_tool_input("shell", {"commands": ["ls", "pwd"]})
    assert shell["command"] == "ls\npwd"

    # Idempotent and non-destructive when the UI keys already exist.
    already = normalize_tool_input("write_file", {"content": "x", "text": "y"})
    assert already["content"] == "x"
    # Non-dict inputs degrade to an empty mapping rather than crashing.
    assert normalize_tool_input("read_file", None) == {}


def test_translator_emits_normalized_tool_input():
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage

    translator = StreamTranslator()
    chunk = {
        "agent": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "n.txt", "text": "body"},
                            "id": "w1",
                        }
                    ],
                )
            ]
        }
    }
    frames = translator.translate(chunk)
    assert [t for t, _ in frames] == [ev.EVENT_TOOL_USE_START]
    assert frames[0][1]["input"]["content"] == "body"


def test_translator_extract_usage_sums_tokens():
    from agent_team.features.board.runtime.translator import extract_usage
    from langchain_core.messages import AIMessage

    chunk = {
        "agent": {
            "messages": [
                AIMessage(
                    content="hi",
                    usage_metadata={
                        "input_tokens": 7,
                        "output_tokens": 3,
                        "total_tokens": 10,
                        "input_token_details": {"cache_read": 2},
                    },
                )
            ]
        }
    }
    usage = extract_usage(chunk)
    assert usage == {"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 2}


# ---------------------------------------------------------------------------
# LocalRunBackend (end-to-end with a fake graph)
# ---------------------------------------------------------------------------


class _DummyCtx:
    def __exit__(self, *args):
        return False


class _FakeAgent:
    def __init__(self, chunks):
        self._chunks = chunks

    def astream(self, *args, **kwargs):
        chunks = self._chunks

        async def gen():
            for chunk in chunks:
                yield chunk

        return gen()


async def test_local_backend_drives_run_and_persists_events(db, monkeypatch, tmp_path):
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import event_store, local_backend, registry
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.workers import llm_graph
    from langchain_core.messages import AIMessage, ToolMessage

    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path))

    from langchain_core.messages import AIMessageChunk

    ns = ("agent:1",)

    async def fake_build_graph(agent_alias, checkpointer, session=None, **kwargs):
        return _FakeAgent(
            [
                {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[{"name": "shell", "args": {}, "id": "x"}],
                                usage_metadata={
                                    "input_tokens": 4,
                                    "output_tokens": 1,
                                    "total_tokens": 5,
                                },
                            )
                        ]
                    }
                },
                {
                    "tools": {
                        "messages": [ToolMessage(content="ok", name="shell", tool_call_id="x")]
                    }
                },
                # The final answer streams token-by-token via ``messages`` mode,
                # then the ``updates`` snapshot captures the full final answer.
                (ns, "messages", (AIMessageChunk(content="All "), {})),
                (ns, "messages", (AIMessageChunk(content="done"), {})),
                {"agent": {"messages": [AIMessage(content="All done")]}},
            ]
        )

    monkeypatch.setattr(llm_graph, "build_graph", fake_build_graph)
    monkeypatch.setattr(llm_graph, "make_checkpointer", lambda alias: (object(), _DummyCtx()))

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="Investigate",
        description="please",
        status="todo",
        assignee_id=None,
        labels=None,
        priority=None,
        created_by=None,
    )
    conversation = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="alice"
    )
    run = runs_repo.create_run(
        db,
        task_id=task.id,
        conversation=conversation,
        agent_alias="alice",
        trigger="mention",
        actor_id=None,
        prompt="do it",
    )
    db.commit()

    handle = registry.register(run.id)
    backend = local_backend.LocalRunBackend()
    await backend._drive(run.id, handle)

    frames = event_store.list_events(run.id)
    types = [f["type"] for f in frames]
    assert types[0] == ev.EVENT_RUN_START
    assert ev.EVENT_TOOL_USE_START in types
    assert ev.EVENT_TOOL_USE_END in types
    # The final answer streamed as text_delta tokens before the run ended.
    assert ev.EVENT_TEXT_DELTA in types
    text = "".join(
        f["data"].get("text", "") for f in frames if f["type"] == ev.EVENT_TEXT_DELTA
    )
    assert text == "All done"
    assert ev.EVENT_FINAL_ANSWER in types
    assert types[-1] == ev.EVENT_RUN_END

    db.expire_all()
    refreshed = runs_repo.get_run(db, run.id)
    assert refreshed.status == ev.RUN_DONE
    assert refreshed.final_answer == "All done"
    assert refreshed.total_tokens == 5


# ---------------------------------------------------------------------------
# Worker resolution + CLI run-control policies
# ---------------------------------------------------------------------------


def test_resolve_worker_picks_path_by_alias():
    from agent_team.features.board.runtime.workers import resolve_worker
    from agent_team.features.board.runtime.workers.acp_cli import AcpCliWorker
    from agent_team.features.board.runtime.workers.llm_graph import LlmGraphWorker

    cli_worker = resolve_worker("cli:claude")
    assert isinstance(cli_worker, AcpCliWorker)
    assert cli_worker.engine == "claude"

    llm_worker = resolve_worker("alice")
    assert isinstance(llm_worker, LlmGraphWorker)


async def test_acp_worker_threads_permission_and_idle(monkeypatch):
    import asyncio

    from agent_team.features.board.runtime.workers import acp_cli
    from agent_team.features.board.runtime.workers.base import (
        PermissionMode,
        TurnContext,
    )

    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.final_text = "hi"
            self.cancelled = False
            self.cli_usage_text = None
            self.usage = {"total_tokens": 3}

        async def stream_frames(self, cancel):
            if False:  # pragma: no cover - generator with no frames
                yield ("", {})

    monkeypatch.setattr(acp_cli, "DirectCliRun", _FakeRun)

    worker = acp_cli.AcpCliWorker(engine="claude", idle_timeout_seconds=42)
    ctx = TurnContext(
        run_id="r1",
        agent_alias="cli:claude",
        prompt="p",
        workspace_path="/tmp/ws",
        thread_id="t1",
        permission_mode=PermissionMode.READ_ONLY,
    )

    async def emit(event_type, data):  # pragma: no cover - no frames emitted
        pass

    result = await worker.run_turn(ctx, emit, asyncio.Event())

    assert captured["auto_approve"] is False
    assert captured["idle_timeout_seconds"] == 42
    assert result.final_text == "hi"
    assert result.cancelled is False


# ---------------------------------------------------------------------------
# Autonomous loop layer (verdict / controller / driver)
# ---------------------------------------------------------------------------


def test_parse_verdict_reads_trailing_json():
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, parse_verdict

    text = (
        'Here is an example: {"verdict": "fail"}.\n'
        "After verifying tests pass, my verdict:\n"
        '{"verdict": "pass", "score": 0.9, "missing": "", '
        '"evidence": {"checks": "pytest green"}}'
    )
    v = parse_verdict(text)
    assert v is not None
    # The last recognisable object wins over the earlier example.
    assert v.verdict == LoopVerdict.PASS
    assert v.score == 0.9
    assert v.evidence == {"checks": "pytest green"}

    assert parse_verdict("no json here") is None
    assert parse_verdict("") is None


def test_loop_controller_decision_table():
    from agent_team.features.board.runtime.loop.controller import (
        OUTCOME_CAPPED,
        OUTCOME_COMPLETE,
        OUTCOME_NEEDS_HUMAN,
        Continue,
        Done,
        LoopController,
    )
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    # pass -> complete
    c = LoopController("obj", max_attempts=5)
    assert "obj" in c.start()
    step = c.on_attempt_finished(Verdict(LoopVerdict.PASS, score=1.0))
    assert isinstance(step, Done) and step.outcome == OUTCOME_COMPLETE

    # needs_human -> needs_human
    c = LoopController("obj", max_attempts=5)
    step = c.on_attempt_finished(Verdict(LoopVerdict.NEEDS_HUMAN))
    assert isinstance(step, Done) and step.outcome == OUTCOME_NEEDS_HUMAN

    # fail with budget left -> continue, carrying the missing text
    c = LoopController("obj", max_attempts=2)
    step = c.on_attempt_finished(Verdict(LoopVerdict.FAIL, missing="add tests"))
    assert isinstance(step, Continue) and "add tests" in step.followup
    # second fail hits the cap
    step = c.on_attempt_finished(Verdict(LoopVerdict.FAIL))
    assert isinstance(step, Done) and step.outcome == OUTCOME_CAPPED

    # missing verdict (evaluator could not grade) -> fail-open continue
    c = LoopController("obj", max_attempts=3)
    assert isinstance(c.on_attempt_finished(None), Continue)


def test_loop_controller_references_plan_when_present():
    from agent_team.features.board.runtime.loop.controller import LoopController
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    # No plan: the opening prompt carries the objective, no file reference.
    plain = LoopController("obj", max_attempts=5).start()
    assert "obj" in plain and "PLAN.md" not in plain

    # With a plan: both the opening prompt and the follow-up point at the file.
    c = LoopController("obj", max_attempts=5, plan_path=".agent-team/PLAN.md")
    start = c.start()
    assert ".agent-team/PLAN.md" in start
    step = c.on_attempt_finished(Verdict(LoopVerdict.FAIL, missing="x"))
    assert ".agent-team/PLAN.md" in step.followup


async def test_run_loop_runs_planning_phase_first(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop
    from agent_team.features.board.runtime.loop.status import LoopState
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    states: list[LoopState] = []
    prompts: list[str] = []

    class FakePlanner:
        def __init__(self):
            self.called = False

        async def plan(self, *, objective, workspace_path):
            self.called = True
            return ".agent-team/PLAN.md"

    async def fake_generator(attempt_id, prompt):
        prompts.append(prompt)
        return GeneratorTurn(run_id=None, final_text="done", cancelled=False)

    class PassEvaluator:
        async def evaluate(
            self, *, objective, generator_summary, workspace_path, attempt_id=None
        ):
            return Verdict(LoopVerdict.PASS, score=1.0)

    planner = FakePlanner()
    outcome = await run_loop(
        task_id=task.id,
        objective="build it",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=PassEvaluator(),
        planner=planner,
        max_attempts=5,
        on_status=lambda s: states.append(s.state),
    )

    assert outcome.outcome == "complete"
    assert planner.called
    # Planning is published before the first run, and the generator is pointed
    # at the plan file the planner wrote.
    assert LoopState.PLANNING in states
    assert ".agent-team/PLAN.md" in prompts[0]


async def test_run_loop_planning_failopen(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    prompts: list[str] = []

    class BrokenPlanner:
        async def plan(self, *, objective, workspace_path):
            raise RuntimeError("planner exploded")

    async def fake_generator(attempt_id, prompt):
        prompts.append(prompt)
        return GeneratorTurn(run_id=None, final_text="done", cancelled=False)

    class PassEvaluator:
        async def evaluate(
            self, *, objective, generator_summary, workspace_path, attempt_id=None
        ):
            return Verdict(LoopVerdict.PASS, score=1.0)

    outcome = await run_loop(
        task_id=task.id,
        objective="build it",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=PassEvaluator(),
        planner=BrokenPlanner(),
        max_attempts=5,
    )

    # A broken planner must not wedge the loop: it proceeds from the raw
    # objective with no plan reference.
    assert outcome.outcome == "complete"
    assert "build it" in prompts[0]
    assert "PLAN.md" not in prompts[0]


async def test_run_loop_drives_attempts_until_pass(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.repositories import attempts as attempts_repo
    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    prompts: list[str] = []

    async def fake_generator(attempt_id, prompt):
        prompts.append(prompt)
        return GeneratorTurn(run_id=None, final_text="did work", cancelled=False)

    class FakeEvaluator:
        def __init__(self):
            self.calls = 0

        async def evaluate(
            self, *, objective, generator_summary, workspace_path, attempt_id=None
        ):
            self.calls += 1
            # Fail the first attempt, pass the second.
            if self.calls == 1:
                return Verdict(LoopVerdict.FAIL, missing="write the function")
            return Verdict(LoopVerdict.PASS, score=1.0)

    outcome = await run_loop(
        task_id=task.id,
        objective="build the thing",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=FakeEvaluator(),
        max_attempts=5,
    )

    assert outcome.outcome == "complete"
    assert outcome.attempts == 2
    # First prompt seeds the objective; the follow-up carries the missing work.
    assert "build the thing" in prompts[0]
    assert "write the function" in prompts[1]

    db.expire_all()
    attempts = attempts_repo.list_attempts_for_task(db, task.id)
    assert [a.attempt_no for a in attempts] == [1, 2]
    assert attempts[-1].outcome == "complete"


async def test_run_loop_pauses_on_plan_change_request(db, monkeypatch):
    """A generator that flags the plan pauses the loop before grading."""
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop
    from agent_team.features.board.runtime.loop.status import (
        LoopState,
        outcome_to_state,
    )
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    async def fake_generator(attempt_id, prompt):
        return GeneratorTurn(run_id=None, final_text="plan is wrong", cancelled=False)

    class NeverEvaluator:
        def __init__(self):
            self.calls = 0

        async def evaluate(self, **_kwargs):
            self.calls += 1
            return Verdict(LoopVerdict.PASS, score=1.0)

    evaluator = NeverEvaluator()
    outcome = await run_loop(
        task_id=task.id,
        objective="build the thing",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=evaluator,
        max_attempts=5,
        replan_requested=lambda: True,
    )

    assert outcome.outcome == "plan_change"
    # The loop pauses before grading (like a cancel), so the controller has not
    # counted the attempt and the evaluator never runs.
    assert outcome.attempts == 0
    assert evaluator.calls == 0
    assert outcome_to_state(outcome.outcome) == LoopState.PLAN_CHANGE_REQUESTED


async def test_run_loop_pauses_on_questions(db, monkeypatch):
    """A generator that raises blocking questions pauses the loop before grading."""
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop
    from agent_team.features.board.runtime.loop.status import (
        LoopState,
        outcome_to_state,
    )
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    async def fake_generator(attempt_id, prompt):
        return GeneratorTurn(run_id=None, final_text="I have a question", cancelled=False)

    class NeverEvaluator:
        def __init__(self):
            self.calls = 0

        async def evaluate(self, **_kwargs):
            self.calls += 1
            return Verdict(LoopVerdict.PASS, score=1.0)

    evaluator = NeverEvaluator()
    outcome = await run_loop(
        task_id=task.id,
        objective="build the thing",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=evaluator,
        max_attempts=5,
        questions_pending=lambda: True,
    )

    assert outcome.outcome == "needs_answers"
    # Pauses before grading: no attempt counted, evaluator never runs.
    assert outcome.attempts == 0
    assert evaluator.calls == 0
    assert outcome_to_state(outcome.outcome) == LoopState.WAITING_ANSWERS


async def test_run_task_graph_executes_tasks_in_dependency_order(db, monkeypatch, tmp_path):
    """The orchestrator schedules by dependency and marks each task complete."""
    import json as _json

    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop import planning_artifacts as A
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn
    from agent_team.features.board.runtime.loop.task_graph import run_task_graph
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    ws = str(tmp_path)
    A.write_text(
        ws,
        A.TASKS_PATH,
        _json.dumps(
            {
                "version": 1,
                "tasks": [
                    {"id": "T2", "status": "pending", "depends_on": ["T1"]},
                    {"id": "T1", "status": "pending", "depends_on": []},
                ],
            }
        ),
    )

    seen: list[str] = []

    async def fake_generator(attempt_id, prompt):
        # The per-task objective names the task id; record execution order.
        for tid in ("T1", "T2"):
            if f"task: {tid}" in prompt:
                seen.append(tid)
        return GeneratorTurn(run_id=None, final_text="did it", cancelled=False)

    class PassEvaluator:
        async def evaluate(self, **_kwargs):
            return Verdict(LoopVerdict.PASS, score=1.0)

    outcome = await run_task_graph(
        task_id=task.id,
        objective="build everything",
        workspace_path=ws,
        run_generator=fake_generator,
        make_evaluator=lambda _t: PassEvaluator(),
        max_attempts_per_task=2,
        final_verify=True,
    )

    assert outcome.outcome == "complete"
    # T1 runs before T2 even though T2 is first in the document.
    assert seen == ["T1", "T2"]
    statuses = {r["id"]: r["status"] for r in A.task_list(ws)}
    assert statuses == {"T1": "complete", "T2": "complete"}


async def test_run_task_graph_blocks_task_that_never_passes(db, monkeypatch, tmp_path):
    """A task that exhausts its attempt cap is marked blocked and escalates."""
    import json as _json

    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop import planning_artifacts as A
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn
    from agent_team.features.board.runtime.loop.task_graph import run_task_graph
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    ws = str(tmp_path)
    A.write_text(
        ws,
        A.TASKS_PATH,
        _json.dumps({"version": 1, "tasks": [{"id": "T1", "status": "pending"}]}),
    )

    async def fake_generator(attempt_id, prompt):
        return GeneratorTurn(run_id=None, final_text="...", cancelled=False)

    class FailEvaluator:
        async def evaluate(self, **_kwargs):
            return Verdict(LoopVerdict.FAIL, missing="not done")

    outcome = await run_task_graph(
        task_id=task.id,
        objective="build",
        workspace_path=ws,
        run_generator=fake_generator,
        make_evaluator=lambda _t: FailEvaluator(),
        max_attempts_per_task=2,
        final_verify=True,
    )

    assert outcome.outcome == "needs_human"
    assert A.task_list(ws)[0]["status"] == "blocked"


async def test_run_loop_caps_when_never_passing(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    async def fake_generator(attempt_id, prompt):
        return GeneratorTurn(run_id=None, final_text="...", cancelled=False)

    class BrokenEvaluator:
        async def evaluate(
            self, *, objective, generator_summary, workspace_path, attempt_id=None
        ):
            raise RuntimeError("judge exploded")

    outcome = await run_loop(
        task_id=task.id,
        objective="obj",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=BrokenEvaluator(),
        max_attempts=3,
    )

    # Fail-open: a broken judge never wedges the loop; the budget caps it.
    assert outcome.outcome == "capped"
    assert outcome.attempts == 3


def test_loop_budget_ledger_caps():
    from agent_team.features.board.runtime.loop.budget import LoopBudget, LoopLedger

    ledger = LoopLedger(budget=LoopBudget(max_tokens=100))
    assert ledger.exceeded() is None
    ledger.add(tokens=60)
    assert ledger.exceeded() is None
    ledger.add(tokens=60)
    assert ledger.exceeded() == "tokens"

    # Unbounded budget never trips.
    free = LoopLedger(budget=LoopBudget())
    free.add(tokens=10_000_000, cost_usd=999.0)
    assert free.exceeded() is None


def test_outcome_to_state_routes_caps_to_human():
    from agent_team.features.board.runtime.loop.status import LoopState, outcome_to_state

    assert outcome_to_state("complete") == LoopState.COMPLETE
    assert outcome_to_state("cancelled") == LoopState.CANCELLED
    assert outcome_to_state("capped") == LoopState.WAITING_FOR_HUMAN
    assert outcome_to_state("budget") == LoopState.WAITING_FOR_HUMAN
    assert outcome_to_state("needs_human") == LoopState.WAITING_FOR_HUMAN
    assert outcome_to_state("weird") == LoopState.FAILED


async def test_run_loop_stops_on_token_budget(db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime.loop import driver
    from agent_team.features.board.runtime.loop.budget import LoopBudget
    from agent_team.features.board.runtime.loop.driver import GeneratorTurn, run_loop
    from agent_team.features.board.runtime.loop.status import LoopState
    from agent_team.features.board.runtime.loop.verdict import LoopVerdict, Verdict

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr(driver, "SessionLocal", factory)

    board = boards_repo.create_board(db, name="B", description=None, columns=None, owner_id=None)
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    async def fake_generator(attempt_id, prompt):
        # Each turn burns 80 tokens; the 50-token budget trips after one turn.
        return GeneratorTurn(run_id=None, final_text="...", cancelled=False, tokens=80)

    class AlwaysFail:
        async def evaluate(
            self, *, objective, generator_summary, workspace_path, attempt_id=None
        ):
            return Verdict(LoopVerdict.FAIL, missing="more")

    statuses: list = []

    outcome = await run_loop(
        task_id=task.id,
        objective="obj",
        workspace_path="/tmp/ws",
        run_generator=fake_generator,
        evaluator=AlwaysFail(),
        max_attempts=99,
        budget=LoopBudget(max_tokens=50),
        on_status=statuses.append,
    )

    # The attempt cap is high; the token budget is what stops it (after 1 turn).
    assert outcome.outcome == "budget"
    assert outcome.attempts == 1
    # A terminal status routing to human review was published.
    assert statuses[-1].state == LoopState.WAITING_FOR_HUMAN
    assert statuses[-1].outcome == "budget"
    assert statuses[0].state == LoopState.RUNNING


# ---------------------------------------------------------------------------
# Comments + activity changelog
# ---------------------------------------------------------------------------


def _make_task(db) -> AgentTeamTask:
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db,
        board_id=board.id,
        title="T",
        description=None,
        status="todo",
        assignee_id=None,
        labels=None,
        priority=None,
        created_by=None,
    )
    db.commit()
    return task


def test_comments_create_list_and_soft_delete(db):
    from agent_team.features.board.repositories import comments as comments_repo

    task = _make_task(db)

    first = comments_repo.create_comment(
        db, task_id=task.id, author_id=None, body="  hello  ", attachments=[{"name": "a"}]
    )
    comments_repo.create_comment(
        db, task_id=task.id, author_id=None, body="second", attachments=None
    )
    db.commit()

    listed = comments_repo.list_comments(db, task.id)
    assert [c.body for c in listed] == ["hello", "second"]
    assert comments_repo.serialize_comment(first).attachments == [{"name": "a"}]

    comments_repo.soft_delete_comment(db, first)
    db.commit()
    assert [c.body for c in comments_repo.list_comments(db, task.id)] == ["second"]


def test_comments_attachment_only_allowed_by_schema():
    """A note may carry only attachments — the body is optional on create."""
    from agent_team.features.board.schemas import CommentCreate

    payload = CommentCreate(attachments=[{"id": "a1", "filename": "f.png"}])
    assert payload.body == ""
    assert payload.attachments == [{"id": "a1", "filename": "f.png"}]


def test_comments_update_body(db):
    from agent_team.features.board.repositories import comments as comments_repo

    task = _make_task(db)
    comment = comments_repo.create_comment(
        db, task_id=task.id, author_id=None, body="draft", attachments=None
    )
    db.commit()

    comments_repo.update_comment(db, comment, body="  edited  ")
    db.commit()
    db.refresh(comment)

    assert comment.body == "edited"
    # Attachments and soft-delete state are untouched by a body edit.
    assert comment.attachments() == []
    assert comment.deleted_at is None
    assert [c.body for c in comments_repo.list_comments(db, task.id)] == ["edited"]


def test_comments_agent_visibility(db):
    """visible_to_agents: defaults True, toggles independently of the body,
    and hidden notes are dropped from the agent context build."""
    from agent_team.features.board.repositories import comments as comments_repo
    from agent_team.features.board.runtime.local_backend import _load_task_notes

    task = _make_task(db)
    shown = comments_repo.create_comment(
        db, task_id=task.id, author_id=None, body="for everyone", attachments=None
    )
    hidden = comments_repo.create_comment(
        db,
        task_id=task.id,
        author_id=None,
        body="people only",
        attachments=None,
        visible_to_agents=False,
    )
    db.commit()

    assert shown.visible_to_agents is True
    assert comments_repo.serialize_comment(shown).visible_to_agents is True
    assert comments_repo.serialize_comment(hidden).visible_to_agents is False

    # Both stay listed for humans, but agents only see the visible one.
    assert len(comments_repo.list_comments(db, task.id)) == 2
    assert [n["body"] for n in _load_task_notes(db, task.id)] == ["for everyone"]

    # A visibility-only update leaves the body untouched, and vice versa.
    comments_repo.update_comment(db, hidden, visible_to_agents=True)
    db.commit()
    assert hidden.body == "people only"
    assert [n["body"] for n in _load_task_notes(db, task.id)] == [
        "for everyone",
        "people only",
    ]


def test_run_context_first_turn_full_then_delta(db):
    """First run gets full context; a later run only sees notes added since."""
    from datetime import UTC, datetime, timedelta

    from agent_team.features.board.repositories import comments as comments_repo
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime.local_backend import (
        _load_task_notes,
        _previous_run,
    )

    task = _make_task(db)
    conv = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="alice"
    )

    def _run(prompt: str):
        return runs_repo.create_run(
            db, task_id=task.id, conversation=conv, agent_alias="alice",
            trigger="mention", actor_id=None, prompt=prompt,
        )

    pre_note = comments_repo.create_comment(
        db, task_id=task.id, author_id=None, body="pre", attachments=None
    )
    run1 = _run("first")
    mid_note = comments_repo.create_comment(
        db, task_id=task.id, author_id=None, body="mid", attachments=None
    )
    run2 = _run("second")

    base = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    pre_note.created_at = base - timedelta(minutes=5)
    run1.created_at = base
    mid_note.created_at = base + timedelta(minutes=5)
    run2.created_at = base + timedelta(minutes=10)
    db.commit()
    db.expire_all()

    run1 = runs_repo.get_run(db, run1.id)
    run2 = runs_repo.get_run(db, run2.id)

    # First turn of the thread has no predecessor → full context is used.
    assert _previous_run(db, run1) is None
    # The second turn's predecessor is run1; its created_at is the delta boundary.
    assert _previous_run(db, run2).id == run1.id

    # Delta for run2 = only notes added after run1's turn (pre-note excluded).
    since = run1.created_at
    assert [n["body"] for n in _load_task_notes(db, task.id, since=since)] == ["mid"]
    # No boundary (first turn) → every visible note is included.
    assert [n["body"] for n in _load_task_notes(db, task.id)] == ["pre", "mid"]


def test_activity_record_and_list_newest_first(db):
    from agent_team.features.board.repositories import activity as activity_repo

    task = _make_task(db)

    activity_repo.record(
        db, task_id=task.id, actor_id=None, kind=activity_repo.TASK_CREATED, data={"a": 1}
    )
    activity_repo.record(
        db,
        task_id=task.id,
        actor_id=None,
        kind=activity_repo.TASK_MOVED,
        data={"from": "todo", "to": "done"},
    )
    db.commit()

    entries = activity_repo.list_activity(db, task.id)
    assert [e.kind for e in entries] == [activity_repo.TASK_MOVED, activity_repo.TASK_CREATED]
    assert activity_repo.serialize_activity(entries[0]).data == {"from": "todo", "to": "done"}


def test_record_standalone_is_best_effort(db):
    from agent_team.features.board.repositories import activity as activity_repo

    task = _make_task(db)
    activity_repo.record_standalone(
        task_id=task.id, actor_id=None, kind=activity_repo.RUN_FINISHED, data={"status": "done"}
    )
    db.expire_all()
    entries = activity_repo.list_activity(db, task.id)
    assert [e.kind for e in entries] == [activity_repo.RUN_FINISHED]


def _make_user(db, *, username="alice"):
    from core.database import models as core_models

    user = core_models.User(
        id=username,
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        password_hash="x",
    )
    db.add(user)
    db.flush()
    return user


def test_board_agent_staffing_round_trip(db):
    """agent_ids: empty by default, persisted as JSON, surfaced in the DTO."""
    board = boards_repo.create_board(
        db, name="Staffed", description=None, columns=None, owner_id=None
    )
    db.commit()
    assert board.agent_ids() == []
    assert boards_repo.serialize_board(board).agent_ids == []

    board.agents_json = json.dumps(["dev-agent", "qa-agent"])
    db.commit()
    db.refresh(board)
    assert board.agent_ids() == ["dev-agent", "qa-agent"]
    assert boards_repo.serialize_board(board).agent_ids == ["dev-agent", "qa-agent"]

    # Corrupt JSON degrades to an empty staffing list instead of crashing.
    board.agents_json = "{not json"
    assert board.agent_ids() == []


def test_board_cli_targets_round_trip(db):
    """cli_target_ids: empty by default, persisted as JSON, surfaced in the DTO."""
    board = boards_repo.create_board(
        db, name="CLI board", description=None, columns=None, owner_id=None
    )
    db.commit()
    assert board.cli_target_ids() == []
    assert boards_repo.serialize_board(board).cli_target_ids == []

    board.cli_targets_json = json.dumps(["cli:claude", "cli:codex"])
    db.commit()
    db.refresh(board)
    assert board.cli_target_ids() == ["cli:claude", "cli:codex"]
    assert boards_repo.serialize_board(board).cli_target_ids == [
        "cli:claude",
        "cli:codex",
    ]

    # Corrupt JSON degrades to an empty list instead of crashing.
    board.cli_targets_json = "{not json"
    assert board.cli_target_ids() == []


def test_known_cli_aliases_covers_engines():
    from agent_team.features.board.runtime.direct_acp import known_cli_aliases

    assert known_cli_aliases() == {"cli:claude", "cli:cursor", "cli:codex"}


def _seed_skill_pack(root, name: str, description: str = "A demo skill") -> None:
    """Create a minimal valid skill pack folder under *root*/shared/<name>."""
    pack = root / "shared" / name
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    (pack / "scripts").mkdir(exist_ok=True)
    (pack / "scripts" / "run.sh").write_text("echo hi\n", encoding="utf-8")


def test_board_skill_ids_round_trip(db):
    from agent_team.features.board.repositories import boards as boards_repo

    board = boards_repo.create_board(
        db, name="Skills", description=None, columns=None, owner_id=None
    )
    assert board.skill_ids() == []
    board.skills_json = json.dumps(["pdf-tools", "git-helper"])
    db.commit()
    dto = boards_repo.serialize_board(board, my_role="owner")
    assert dto.skill_ids == ["pdf-tools", "git-helper"]


def test_list_available_packs_reads_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_PACKS_ROOT", str(tmp_path))
    _seed_skill_pack(tmp_path, "pdf-tools", "Work with PDFs")

    from agent_team.features.board.runtime import skills as skills_rt

    packs = skills_rt.list_available_packs()
    names = {p["name"] for p in packs}
    assert "pdf-tools" in names
    pdf = next(p for p in packs if p["name"] == "pdf-tools")
    assert pdf["description"] == "Work with PDFs"


def test_materialize_skills_copies_into_native_dirs_and_is_idempotent(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SKILL_PACKS_ROOT", str(tmp_path / "packs"))
    _seed_skill_pack(tmp_path / "packs", "pdf-tools")

    from agent_team.features.board.runtime import skills as skills_rt

    ws = tmp_path / "ws"
    ws.mkdir()
    manifest = skills_rt.materialize_skills(str(ws), ["pdf-tools", "missing-pack"])

    # Only the existing pack is materialised; the unknown name is skipped.
    assert [m["name"] for m in manifest] == ["pdf-tools"]
    assert (ws / ".claude" / "skills" / "pdf-tools" / "SKILL.md").is_file()
    assert (ws / ".cursor" / "skills" / "pdf-tools" / "SKILL.md").is_file()
    assert (ws / ".claude" / "skills" / "pdf-tools" / "scripts" / "run.sh").is_file()
    assert manifest[0]["path"] == ".claude/skills/pdf-tools/SKILL.md"

    # De-selecting a skill removes it from the workspace on the next pass.
    manifest2 = skills_rt.materialize_skills(str(ws), [])
    assert manifest2 == []
    assert not (ws / ".claude" / "skills").exists()
    assert not (ws / ".cursor" / "skills").exists()


def test_write_codex_manifest_writes_agents_md(tmp_path):
    from agent_team.features.board.runtime import skills as skills_rt

    ws = tmp_path / "ws"
    ws.mkdir()
    manifest = [
        {"name": "pdf-tools", "description": "Work with PDFs",
         "path": ".claude/skills/pdf-tools/SKILL.md"}
    ]
    skills_rt.write_codex_manifest(str(ws), manifest)
    agents = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "Available skills" in agents
    assert "pdf-tools" in agents

    # No skills → leave any existing manifest untouched (no clobber).
    (ws / "AGENTS.md").write_text("keep me\n", encoding="utf-8")
    skills_rt.write_codex_manifest(str(ws), [])
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == "keep me\n"


def test_render_brief_lists_available_skills():
    from types import SimpleNamespace

    from agent_team.features.board.runtime import cli_context

    task = SimpleNamespace(
        human_key="T-1", title="Demo", description="x", workspace_path="/ws"
    )
    brief = cli_context.render_brief(
        task,
        notes=None,
        repos=None,
        skills=[{"name": "pdf-tools", "description": "Work with PDFs",
                 "path": ".claude/skills/pdf-tools/SKILL.md"}],
    )
    assert "## Available skills" in brief
    assert "pdf-tools" in brief
    assert ".claude/skills/pdf-tools/SKILL.md" in brief


def test_csv_export_then_reimport_round_trips(db):
    """Exported rows carry human_key, so re-importing updates (not duplicates)."""
    from agent_team.features.board import csv_tasks
    from agent_team.features.board.repositories import tasks as tasks_repo

    board = boards_repo.create_board(
        db, name="CSV RT", description=None, columns=None, owner_id=None
    )
    db.commit()
    tasks_repo.create_task(
        db, board_id=board.id, title="First", description="d1", status="todo",
        assignee_id=None, labels=["a", "b"], priority="high", created_by=None,
    )
    tasks_repo.create_task(
        db, board_id=board.id, title="Second", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    text = csv_tasks.export_tasks_csv(db, board, include_archived=False)
    assert "human_key,title,description" in text.splitlines()[0]
    assert "First" in text and "a;b" in text

    # Re-importing the export updates both rows (matched by human_key).
    plans = csv_tasks.plan_import(db, board, text.encode("utf-8"))
    assert [p.action for p in plans] == ["update", "update"]
    result, changed = csv_tasks.apply_import(db, board, plans, actor_id=None)
    db.commit()
    assert (result.created, result.updated) == (0, 2)
    assert changed is True
    # No new tasks were created.
    assert len(tasks_repo.list_tasks(db, board_id=board.id)) == 2


def test_csv_import_creates_with_defaults_and_flags_errors(db):
    """title is required; optional fields fall back to defaults with warnings."""
    from agent_team.features.board import csv_tasks
    from agent_team.features.board.repositories import tasks as tasks_repo

    board = boards_repo.create_board(
        db, name="CSV Import", description=None, columns=None, owner_id=None
    )
    db.commit()
    first_col = board.columns()[0]["key"]

    csv_text = (
        "title,status,priority,labels,task_type\n"
        "Valid task,in_progress,High,x;y,bug\n"
        "Bad status,nonsense,,,,\n"
        ",todo,,,\n"  # missing title → error
    )
    plans = csv_tasks.plan_import(db, board, csv_text.encode("utf-8"))
    assert [p.action for p in plans] == ["create", "create", "error"]
    # Unknown status degrades to the first column, with a warning.
    bad = plans[1]
    assert bad.values["status"] == first_col
    assert "unknown status" in bad.message

    result, _ = csv_tasks.apply_import(db, board, plans, actor_id=None)
    db.commit()
    assert (result.created, result.skipped) == (2, 1)
    assert any("row 4" in e for e in result.errors)

    titles = {t.title for t in tasks_repo.list_tasks(db, board_id=board.id)}
    assert {"Valid task", "Bad status"} <= titles


def test_csv_import_requires_title_column(db):
    from agent_team.features.board import csv_tasks

    board = boards_repo.create_board(
        db, name="No title col", description=None, columns=None, owner_id=None
    )
    db.commit()
    with pytest.raises(csv_tasks.CsvImportError):
        csv_tasks.plan_import(db, board, b"name,status\nfoo,todo\n")


def test_board_members_add_list_remove(db):
    from agent_team.features.board.repositories import members as members_repo

    user = _make_user(db, username="owner1")
    board = boards_repo.create_board(
        db, name="Team", description=None, columns=None, owner_id=user.id
    )
    db.flush()
    members_repo.add_member(db, board_id=board.id, user_id=user.id, role="owner")
    db.commit()

    listed = members_repo.list_members(db, board.id)
    assert [(m.user_id, m.role) for m, _ in listed] == [(user.id, "owner")]
    assert members_repo.get_role(db, board.id, user.id) == "owner"

    # Owner gets "owner" via board.owner_id even without admin.
    assert (
        members_repo.effective_role(db, board, user_id=user.id, is_admin=False) == "owner"
    )
    # A stranger defaults to viewer (no membership, not admin).
    assert (
        members_repo.effective_role(db, board, user_id="ghost", is_admin=False) == "viewer"
    )

    assert members_repo.remove_member(db, board_id=board.id, user_id=user.id) is True
    db.commit()
    assert members_repo.list_members(db, board.id) == []


def test_attempts_list_and_reset(db):
    from agent_team.features.board.repositories import conversations as conv_repo

    task = _make_task(db)
    conv_repo.get_or_create_active_conversation(db, task_id=task.id, agent_alias="alice")
    db.commit()

    reset = conv_repo.reset_conversation(db, task_id=task.id, agent_alias="alice")
    db.commit()
    assert reset.attempt == 2 and reset.is_active is True

    attempts = conv_repo.list_attempts(db, task_id=task.id, agent_alias="alice")
    assert [a.attempt for a in attempts] == [2, 1]
    assert conv_repo.serialize_attempt(attempts[0]).agent_id == "alice"


def test_resolve_in_workspace_rejects_escape(tmp_path):
    from agent_team.features.board.workspace import resolve_in_workspace

    root = str(tmp_path)
    assert str(resolve_in_workspace(root, "notes/a.txt")).endswith("/notes/a.txt")
    for bad in ["../secret", "../../etc/passwd"]:
        with pytest.raises(ValueError):
            resolve_in_workspace(root, bad)


def test_board_bus_fans_out_typed_events_to_subscribers():
    import asyncio

    from agent_team.features.board.board_events import BoardEventBus

    async def scenario():
        bus = BoardEventBus()
        q1 = bus.subscribe("b1")
        q2 = bus.subscribe("b1")
        other = bus.subscribe("b2")

        event = {"type": "task.created", "board_id": "b1", "task_id": "t1"}
        bus.publish("b1", event)

        assert q1.get_nowait() == event
        assert q2.get_nowait() == event
        assert other.empty()

        # Unsubscribed queues stop receiving; an empty board is cleaned up.
        bus.unsubscribe("b1", q1)
        bus.publish("b1", {"type": "task.deleted", "board_id": "b1", "task_id": "t1"})
        assert q1.empty()
        assert not q2.empty()

    asyncio.run(scenario())


def test_translator_streams_text_tokens_from_messages_mode():
    """``messages`` mode streams assistant text token-by-token as text_delta."""
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessageChunk

    translator = StreamTranslator()
    ns = ("agent:1",)

    first = translator.translate((ns, "messages", (AIMessageChunk(content="Hello"), {})))
    second = translator.translate((ns, "messages", (AIMessageChunk(content=" world"), {})))

    assert [(t, d["text"]) for t, d in first] == [(ev.EVENT_TEXT_DELTA, "Hello")]
    assert [(t, d["text"]) for t, d in second] == [(ev.EVENT_TEXT_DELTA, " world")]


def test_translator_streams_thinking_tokens_from_messages_mode():
    """Reasoning/thinking blocks stream as ``thinking`` frames (not text)."""
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessageChunk

    translator = StreamTranslator()
    ns = ("agent:1",)

    chunk = AIMessageChunk(
        content=[
            {"type": "thinking", "thinking": "Let me reason"},
            {"type": "text", "text": "Here is the answer"},
        ]
    )
    frames = translator.translate((ns, "messages", (chunk, {})))

    assert [t for t, _ in frames] == [ev.EVENT_THINKING, ev.EVENT_TEXT_DELTA]
    assert frames[0][1]["thinking"] == "Let me reason"
    assert frames[1][1]["text"] == "Here is the answer"


def test_translator_messages_mode_ignores_tool_use_blocks():
    """Tool-use blocks in a message chunk are not streamed as visible text."""
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessageChunk

    translator = StreamTranslator()
    ns = ("agent:1",)
    chunk = AIMessageChunk(
        content=[
            {"type": "text", "text": "Reading file"},
            {
                "type": "tool_use",
                "id": "toolu_123",
                "name": "read_file",
                "input": {"file_path": "/ws/T-1"},
            },
        ]
    )
    frames = translator.translate((ns, "messages", (chunk, {})))
    joined = "".join(d.get("text", "") for _, d in frames)
    assert joined == "Reading file"
    assert "tool_use" not in joined and "toolu_123" not in joined


def test_translator_updates_capture_final_text_without_streaming_it():
    """``updates`` snapshots set the final answer but emit no text frame.

    Leaked Anthropic tool-use JSON in the snapshot text is stripped, so the
    persisted final answer is clean prose.
    """
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage

    translator = StreamTranslator()
    message = AIMessage(
        content=[
            {"type": "text", "text": "Let me read the file:"},
            {
                "type": "tool_use",
                "id": "toolu_123",
                "name": "read_file",
                "input": {"file_path": "/ws/T-1"},
            },
        ],
        tool_calls=[
            {
                "name": "read_file",
                "args": {"file_path": "/ws/T-1"},
                "id": "toolu_123",
                "type": "tool_call",
            }
        ],
    )

    frames = translator.translate({"agent": {"messages": [message]}})
    types = [t for t, _ in frames]
    # The tool call surfaces as a proper tool frame; no text frame is emitted.
    assert "tool_use_start" in types
    assert "text_delta" not in types
    # The captured final text carries only the prose, never the JSON block.
    assert translator.final_text == "Let me read the file:"
    assert "tool_use" not in translator.final_text
    assert "toolu_123" not in translator.final_text


def test_translator_custom_mode_surfaces_acp_progress_on_running_tool():
    """ACP sub-agent custom progress streams as tool_use_progress on the tool."""
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage

    translator = StreamTranslator()
    ns = ("agent:1",)

    # The model calls the claude_acp tool → opens a tool card.
    call = {
        "agent": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "claude_acp", "args": {}, "id": "c1"}],
                )
            ]
        }
    }
    start = translator.translate(call)
    tool_id = start[0][1]["tool_id"]

    # While it runs, the sub-agent emits live progress on the custom channel.
    progress = translator.translate((ns, "custom", {"claude_acp_progress": "working..."}))
    thought = translator.translate((ns, "custom", {"claude_acp_thought": "hmm"}))

    assert [t for t, _ in progress] == [ev.EVENT_TOOL_USE_PROGRESS]
    assert progress[0][1]["tool_id"] == tool_id
    assert progress[0][1]["chunk"] == "working..."
    assert [t for t, _ in thought] == [ev.EVENT_TOOL_USE_PROGRESS]
    assert thought[0][1]["chunk"] == "hmm"


def test_translator_custom_mode_without_running_tool_is_ignored():
    from agent_team.features.board.runtime.translator import StreamTranslator

    translator = StreamTranslator()
    frames = translator.translate((("agent:1",), "custom", {"claude_acp_progress": "x"}))
    assert frames == []


def test_acp_progress_text_formats_tool_lines_without_raw_leak():
    """Tool start/progress render as clean lines; raw ids/statuses never leak."""
    from agent_team.features.board.runtime.translator import _acp_progress_text

    # ``kind\x00title\x00tool_id`` -> a single ``→ title`` line; the id is hidden.
    start = _acp_progress_text(
        "claude_acp_tool_start", "execute\x00Terminal\x00toolu_014abc"
    )
    assert start == "\n\u2192 Terminal\n"
    assert "toolu_014abc" not in start

    # ``tool_id\x00status\x00title`` -> only terminal states render, with an icon.
    done = _acp_progress_text(
        "claude_acp_tool_progress", "toolu_014abc\x00completed\x00Terminal"
    )
    assert done == "  \u2713 Terminal\n"
    running = _acp_progress_text(
        "claude_acp_tool_progress", "toolu_014abc\x00in_progress\x00Terminal"
    )
    assert running == ""  # noisy in-flight pings are dropped


def test_acp_progress_text_surfaces_command_and_output():
    """The optional 4th field (command / output) is rendered when present."""
    from agent_team.features.board.runtime.translator import _acp_progress_text

    start = _acp_progress_text(
        "claude_acp_tool_start", "execute\x00Terminal\x00tid1\x00find . -maxdepth 3"
    )
    assert start == "\n\u2192 Terminal: find . -maxdepth 3\n"

    done = _acp_progress_text(
        "claude_acp_tool_progress", "tid1\x00completed\x00Terminal\x00line_a\nline_b"
    )
    assert "\u2713 Terminal" in done
    assert "    line_a" in done and "    line_b" in done


def test_translator_acp_trail_survives_in_final_tool_output():
    """The streamed sub-agent action trail is merged into the tool's output."""
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage, ToolMessage

    translator = StreamTranslator()
    ns = ("agent:1",)

    call = {
        "agent": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "claude_acp", "args": {}, "id": "c1"}],
                )
            ]
        }
    }
    translator.translate(call)

    # Live trail: prose + a terminal tool call + completion + closing prose.
    translator.translate((ns, "custom", {"claude_acp_progress": "Exploring repo."}))
    translator.translate(
        (ns, "custom", {"claude_acp_tool_start": "execute\x00Terminal\x00tid1"})
    )
    translator.translate(
        (ns, "custom", {"claude_acp_tool_progress": "tid1\x00completed\x00Terminal"})
    )
    translator.translate((ns, "custom", {"claude_acp_progress": "All done."}))

    # The result message is only the final prose (core's collector behaviour).
    result = {
        "tools": {
            "messages": [
                ToolMessage(content="All done.", name="claude_acp", tool_call_id="c1")
            ]
        }
    }
    end = translator.translate(result)
    assert [t for t, _ in end] == [ev.EVENT_TOOL_USE_END]
    preview = end[0][1]["output_preview"]
    # The action trail (prose + tool steps) is preserved, not just the prose.
    assert "Exploring repo." in preview
    assert "\u2192 Terminal" in preview
    assert "\u2713 Terminal" in preview
    assert "All done." in preview


def test_translator_non_acp_tool_output_unchanged():
    """Tools without a progress trail keep their plain result message."""
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage, ToolMessage

    translator = StreamTranslator()
    call = {
        "agent": {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "shell", "args": {"cmd": "ls"}, "id": "x"}],
                )
            ]
        }
    }
    translator.translate(call)
    result = {
        "tools": {"messages": [ToolMessage(content="a b", name="shell", tool_call_id="x")]}
    }
    end = translator.translate(result)
    assert end[0][1]["output_preview"] == "a b"


def test_thread_messages_reconstructs_user_and_assistant_turns(db):
    from agent_team.features.board.repositories import (
        conversations as conversations_repo,
    )
    from agent_team.features.board.repositories import messages as messages_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import event_store
    from agent_team.features.board.runtime import events as ev

    task = _make_task(db)
    conv = conversations_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="alice"
    )
    run = runs_repo.create_run(
        db,
        task_id=task.id,
        conversation=conv,
        agent_alias="alice",
        trigger="mention",
        actor_id=None,
        prompt="hi there",
    )
    db.commit()

    event_store.append_event(run.id, *ev.text_delta("Hello"))
    event_store.append_event(
        run.id, *ev.tool_use_start(tool_id="t1", tool_name="shell", tool_input={})
    )
    event_store.append_event(
        run.id,
        *ev.tool_use_end(
            tool_id="t1", tool_name="shell", success=True, is_error=False,
            output_preview="ok",
        ),
    )

    msgs = messages_repo.list_thread_messages(
        db, conversation=conv, agent_display="Alice"
    )

    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].text == "hi there"
    assert msgs[1].sender_type == "agent" and msgs[1].sender_name == "Alice"
    kinds = [b["type"] for b in msgs[1].content]
    assert kinds == ["text", "tool_use", "tool_result"]
    assert msgs[1].content[0]["text"] == "Hello"


def test_thread_messages_strips_leaked_tool_json_from_old_events(db):
    """History rebuilt from pre-fix events must drop leaked tool_use JSON."""
    from agent_team.features.board.repositories import (
        conversations as conversations_repo,
    )
    from agent_team.features.board.repositories import messages as messages_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import event_store
    from agent_team.features.board.runtime import events as ev

    task = _make_task(db)
    conv = conversations_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="alice"
    )
    run = runs_repo.create_run(
        db,
        task_id=task.id,
        conversation=conv,
        agent_alias="alice",
        trigger="mention",
        actor_id=None,
        prompt="read the file",
    )
    db.commit()

    leaked = (
        'Let me read the file:\n'
        '{"id": "toolu_9", "input": {"file_path": "/ws/T-1"}, '
        '"name": "read_file", "type": "tool_use"}'
    )
    event_store.append_event(run.id, *ev.text_delta(leaked))

    msgs = messages_repo.list_thread_messages(
        db, conversation=conv, agent_display="Alice"
    )
    assistant = msgs[1]
    assert assistant.content[0]["text"] == "Let me read the file:"
    assert "tool_use" not in assistant.text
    assert "toolu_9" not in assistant.text


def test_attachment_save_resolve_and_delete_roundtrip(tmp_path):
    from agent_team.features.board import attachments

    ws = str(tmp_path)
    dto = attachments.save_attachment(
        ws,
        subdir=attachments.CHAT_DIR,
        filename="notes.txt",
        content=b"data",
        media_type="text/plain",
    )
    assert dto["kind"] == "text" and dto["size_bytes"] == 4
    assert dto["path"] == f"{attachments.CHAT_DIR}/{dto['id']}/notes.txt"

    resolved = attachments.resolve_chat_attachments(ws, [dto["id"]])
    assert resolved == [{"filename": "notes.txt", "path": dto["path"]}]

    assert attachments.delete_attachment(
        ws, subdir=attachments.CHAT_DIR, att_id=dto["id"]
    )
    assert attachments.resolve_chat_attachments(ws, [dto["id"]]) == []


def test_build_graph_roots_tools_at_task_workspace(monkeypatch):
    import asyncio

    from agent_team.features.board.runtime import graph_builder

    from plugins.standard_tools.tools import file_tools
    from plugins.standard_tools.tools.workspace_override import get_workspace_override

    seen: dict[str, str | None] = {}

    async def fake_create_runtime_graph(agent_alias, checkpointer, session=None):
        # Inside the build, the file tools resolve to the task folder via the
        # context-local override (no global mutation).
        seen["override"] = get_workspace_override()
        seen["resolved"] = file_tools._resolve_work_dir("alice", {})
        return object()

    import core.agents.agent_api as agent_api

    monkeypatch.setattr(agent_api, "_create_runtime_graph", fake_create_runtime_graph)

    asyncio.run(
        graph_builder.build_graph("alice", object(), workspace_path="/tmp/agent_team/b/T-9")
    )

    assert seen == {
        "override": "/tmp/agent_team/b/T-9",
        "resolved": "/tmp/agent_team/b/T-9",
    }
    # The override is context-local and reset after the build: other flows see none.
    assert get_workspace_override() is None


def _fake_task():
    from types import SimpleNamespace

    return SimpleNamespace(
        human_key="T-7",
        title="Build the importer",
        description="Parse the CSV and load rows.",
        workspace_path="/ws/agent_team/board/T-7",
    )


def test_build_task_context_injects_notes_and_file_pointers():
    from agent_team.features.board.runtime.context import build_task_context

    notes = [
        {
            "author": "alice",
            "created_at": "2026-06-09 22:30 UTC",
            "body": "Use the staging credentials.",
            "attachments": [],
        },
        {
            "author": "bob",
            "created_at": "2026-06-09 22:35 UTC",
            "body": "Sample data is here:",
            "attachments": [
                {"path": "_notes/abc/data.csv", "filename": "data.csv"},
            ],
        },
        # A file-only note (no body, unknown author) still renders with its file.
        {
            "created_at": "2026-06-09 22:40 UTC",
            "body": "",
            "attachments": [{"path": "_notes/def/spec.pdf"}],
        },
    ]
    text = build_task_context(_fake_task(), "Start now.", notes=notes)

    assert "User notes on this task" in text
    assert "- alice at 2026-06-09 22:30 UTC:" in text
    assert "  Use the staging credentials." in text
    assert "- bob at 2026-06-09 22:35 UTC:" in text
    assert "  Attached file: `_notes/abc/data.csv` (data.csv)" in text
    assert "- a user at 2026-06-09 22:40 UTC:" in text
    assert "  Attached file: `_notes/def/spec.pdf`" in text
    # Notes are wrapped in a <task_notes> block that closes before the user's
    # current message, giving the agent a hard boundary between the two.
    assert "<task_notes>" in text
    assert (
        text.index("<task_notes>")
        < text.index("User notes on this task")
        < text.index("</task_notes>")
        < text.index("--- User's current message ---")
        < text.index("Start now.")
    )


def test_build_task_context_without_notes_has_no_notes_block():
    from agent_team.features.board.runtime.context import build_task_context

    assert "User notes" not in build_task_context(_fake_task(), "Go.", notes=None)
    assert "User notes" not in build_task_context(_fake_task(), "Go.", notes=[])
    # Notes with neither body nor usable attachments add nothing.
    empty = [{"body": "", "attachments": [{"filename": "x"}]}]
    assert "User notes" not in build_task_context(_fake_task(), "Go.", notes=empty)


def test_build_task_context_delta_turn_omits_repeated_header():
    """A follow-up turn sends only the delta (new notes / changed description)."""
    from agent_team.features.board.runtime.context import build_task_context

    new_notes = [
        {"author": "carol", "created_at": "2026-06-10 09:00 UTC",
         "body": "One more thing.", "attachments": []},
    ]
    text = build_task_context(
        _fake_task(), "Continue.", notes=new_notes, full=False, include_description=True
    )
    # The header/description/workspace are NOT re-sent (they're in history).
    assert "Task T-7: Build the importer" not in text
    assert "Shared workspace folder" not in text
    # Delta notes use the "new notes" framing and the prompt is present.
    assert "New notes added since the last message" in text
    assert "One more thing." in text
    assert text.rstrip().endswith("Continue.")
    # Description is only re-sent (with an "updated" marker) when flagged changed.
    assert "The task description was updated:" in text


def test_build_task_context_delta_pure_prompt_is_prompt_only():
    """When nothing changed, only the prompt is sent so the cache prefix is reused."""
    from agent_team.features.board.runtime.context import build_task_context

    text = build_task_context(
        _fake_task(), "Just this.", notes=[], full=False, include_description=False
    )
    assert text == "Just this."
    assert "Task T-7" not in text
    assert "--- User's current message ---" not in text


# ---------------------------------------------------------------------------
# Board code repositories
# ---------------------------------------------------------------------------


def _git(*args, cwd) -> None:
    import subprocess

    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _make_source_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "Tester", cwd=path)
    (path / "README.md").write_text("hello\n")
    _git("add", ".", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)
    return path


def test_repo_dto_never_leaks_secret(db):
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate

    repo = repos_repo.create_repo(
        db,
        owner_id=None,
        payload=RepoCreate(
            name="Backend",
            git_url="https://example.com/x.git",
            auth_type="token",
            auth_secret="supersecret",
        ),
    )
    dto = repos_repo.serialize_repo(db, repo)
    assert dto.has_secret is True
    dumped = dto.model_dump()
    assert "auth_secret" not in dumped
    assert "supersecret" not in str(dumped)


def test_repo_update_secret_write_only(db):
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate, RepoUpdate

    repo = repos_repo.create_repo(
        db,
        owner_id=None,
        payload=RepoCreate(
            name="Svc", git_url="https://example.com/x.git", auth_type="token",
            auth_secret="a",
        ),
    )
    # Omitting the secret keeps it; other fields still update.
    repo = repos_repo.update_repo(db, repo, RepoUpdate(name="Renamed"))
    assert repo.name == "Renamed"
    assert repo.has_secret() is True
    # Sending "" clears it.
    repo = repos_repo.update_repo(db, repo, RepoUpdate(auth_secret=""))
    assert repo.has_secret() is False


def test_repo_assign_and_repos_for_board(db):
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate

    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="owner1"
    )
    db.commit()
    repo = repos_repo.create_repo(
        db, owner_id="owner1", payload=RepoCreate(name="Svc", git_url="https://x/y.git")
    )
    repos_repo.assign_repo(
        db, board_id=board.id, repo_id=repo.id, branch_override="dev"
    )
    pairs = repos_repo.repos_for_board(db, board.id)
    assert len(pairs) == 1
    assert pairs[0][0].id == repo.id
    assert pairs[0][1] == "dev"
    assert pairs[0][2] is False  # per-board push defaults off
    # Toggling the board's push opt-in persists without re-creating the row.
    repos_repo.assign_repo(
        db, board_id=board.id, repo_id=repo.id, branch_override="dev", allow_push=True
    )
    updated = repos_repo.repos_for_board(db, board.id)[0]
    assert updated[1] == "dev" and updated[2] is True
    assert repos_repo.count_boards_for_repo(db, repo.id) == 1
    assert repos_repo.boards_using_repo(db, repo.id) == [board.id]
    assert repos_repo.unassign_repo(db, board_id=board.id, repo_id=repo.id) is True
    assert repos_repo.repos_for_board(db, board.id) == []


# ---------------------------------------------------------------------------
# Board Wiki (a board repo marked is_wiki)
# ---------------------------------------------------------------------------


def test_board_repo_is_wiki_round_trip(db):
    """is_wiki: off by default, persisted on assign, surfaced via tuple + DTO."""
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate

    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="owner1"
    )
    db.commit()
    repo = repos_repo.create_repo(
        db, owner_id="owner1", payload=RepoCreate(name="KB", git_url="https://x/kb.git")
    )

    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id)
    assert repos_repo.repos_for_board(db, board.id)[0][3] is False  # is_wiki

    # Toggling is_wiki persists without re-creating the assignment row.
    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id, is_wiki=True)
    repo_t, _branch, _allow, is_wiki = repos_repo.repos_for_board(db, board.id)[0]
    assert repo_t.id == repo.id and is_wiki is True

    dto = repos_repo.serialize_board_repo(db, repo, None, False, True)
    assert dto.is_wiki is True


def test_prepare_task_repos_marks_wiki(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path / "ws"))
    src = _make_source_repo(tmp_path / "src")

    from agent_team.features.repos import git_service
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate
    from agent_team.features.repos.task_copy import prepare_task_repos

    repo = repos_repo.create_repo(
        db, owner_id="owner1", payload=RepoCreate(name="KB", git_url=str(src))
    )
    assert git_service.sync_repo_by_id(repo.id).ok
    db.expire_all()

    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="owner1"
    )
    db.commit()
    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id, is_wiki=True)
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    prepared = prepare_task_repos(db, task)
    assert prepared and prepared[0]["is_wiki"] is True


def test_repos_context_blocks_label_the_wiki(db):
    from agent_team.features.board.runtime.cli_context import _render_repos_block
    from agent_team.features.board.runtime.context import build_task_context

    task = _make_task(db)
    repos = [
        {"slug": "kb", "path": "kb", "branch": "agent/t-1", "is_wiki": True},
        {"slug": "app", "path": "app", "branch": "agent/t-1"},
    ]
    llm = build_task_context(task, "Go.", repos=repos, full=True)
    assert "board wiki" in llm
    assert "board-wiki" in llm  # points the agent at the skill

    cli = _render_repos_block(repos)
    assert "board wiki" in cli
    assert "`kb/`" in cli


def test_materialize_wiki_skill_copies_into_native_dirs(tmp_path):
    from agent_team.features.board.wiki import service as wiki_rt

    ws = tmp_path / "ws"
    ws.mkdir()
    row = wiki_rt.materialize_wiki_skill(str(ws))

    assert row is not None
    assert row["name"] == "board-wiki"
    assert row["path"] == ".claude/skills/board-wiki/SKILL.md"
    assert (ws / ".claude" / "skills" / "board-wiki" / "SKILL.md").is_file()
    assert (ws / ".cursor" / "skills" / "board-wiki" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# CLI push straight to host (credential helper + pre-push hook)
# ---------------------------------------------------------------------------


def test_resolve_push_credentials_respects_allow_push(db):
    """The credential helper only yields a token when push is actually allowed."""
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.git_cred_helper import resolve_credentials
    from agent_team.features.repos.schemas import RepoCreate

    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="o"
    )
    db.commit()
    repo = repos_repo.create_repo(
        db,
        owner_id="o",
        payload=RepoCreate(
            name="KB", git_url="https://x/kb.git", auth_type="token",
            auth_username="u", auth_secret="tok", allow_push=True,
        ),
    )
    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id, allow_push=True)
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    assert resolve_credentials(db, task_id=task.id, repo_id=repo.id) == ("u", "tok")

    # Board opt-in off → no credential.
    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id, allow_push=False)
    assert resolve_credentials(db, task_id=task.id, repo_id=repo.id) is None

    # Board opt-in back on, but repo master gate off → still no credential.
    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id, allow_push=True)
    repo.allow_push = False
    db.commit()
    assert resolve_credentials(db, task_id=task.id, repo_id=repo.id) is None


def test_prepare_task_repos_wires_push_to_host(db, tmp_path, monkeypatch):
    """Task copy has a single origin remote pointed at the real host."""
    import os
    import subprocess
    from pathlib import Path

    from agent_team.features.repos.paths import task_copy_path
    from agent_team.features.repos.task_copy import prepare_task_repos

    src, repo, task = _prepare_pushable_task(db, tmp_path, monkeypatch, allow_push=True)
    prepare_task_repos(db, task)
    copy = task_copy_path(task.workspace_path, repo.slug)

    def _run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(copy), *args], capture_output=True, text=True
        ).stdout.strip()

    # One remote only: origin → the real host (effective URL; auth none = as-is).
    assert _run("remote") == "origin"
    assert _run("config", "remote.origin.url") == str(src)
    hook = Path(copy) / ".git" / "hooks" / "pre-push"
    assert hook.is_file() and os.access(hook, os.X_OK)
    assert "refs/heads/" in hook.read_text()


def test_configure_push_to_host_sets_credential_helper_for_token(db, tmp_path):
    """Token repos get a credential helper + a 0600 non-secret cred file."""
    import json
    import stat as _stat
    import subprocess

    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate
    from agent_team.features.repos.task_copy import _configure_push_to_host

    dest = tmp_path / "copy"
    dest.mkdir()
    subprocess.run(["git", "init", "-q", str(dest)], check=True)

    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="o"
    )
    db.commit()
    repo = repos_repo.create_repo(
        db,
        owner_id="o",
        payload=RepoCreate(
            name="KB", git_url="https://x/kb.git", auth_type="token", auth_secret="tok"
        ),
    )
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    _configure_push_to_host(dest, repo, task)

    helper = subprocess.run(
        ["git", "-C", str(dest), "config", "credential.helper"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert "git_cred_helper.py" in helper
    cred = dest / ".git" / "at_cred.json"
    assert cred.is_file()
    data = json.loads(cred.read_text())
    assert data["task_id"] == task.id and data["repo_id"] == repo.id
    # 0600: not readable/writable by group or other.
    assert _stat.S_IMODE(cred.stat().st_mode) & 0o077 == 0


def test_pre_push_hook_blocks_default_branch():
    from agent_team.features.repos.task_copy import _pre_push_hook_body

    body = _pre_push_hook_body(["develop"])
    assert "refs/heads/develop" in body
    assert "refs/heads/main" in body  # safety-net defaults always included
    assert "refs/heads/master" in body
    assert "refusing to push to protected branch" in body


def test_repo_schedule_sets_next_pull(db):
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate

    scheduled = repos_repo.create_repo(
        db,
        owner_id=None,
        payload=RepoCreate(
            name="A", git_url="https://x/a.git", schedule_mode="interval",
            schedule_interval_seconds=120,
        ),
    )
    assert scheduled.next_pull_at is not None
    off = repos_repo.create_repo(
        db, owner_id=None, payload=RepoCreate(name="B", git_url="https://x/b.git")
    )
    assert off.next_pull_at is None


def test_repo_auth_builds_token_header_and_ssh_key(tmp_path):
    import base64
    import os

    from agent_team.features.repos.git_service import _auth

    token_repo = AgentTeamRepo(
        name="x", slug="x", git_url="https://h/r.git",
        auth_type="token", auth_username="me", auth_secret="tok",
    )
    with _auth(token_repo) as (extra, env):
        assert extra[0] == "-c"
        assert extra[1].startswith("http.extraHeader=Authorization: Basic ")
        encoded = extra[1].split("Basic ", 1)[1]
        assert base64.b64decode(encoded).decode() == "me:tok"
        assert env == {}

    ssh_repo = AgentTeamRepo(
        name="y", slug="y", git_url="git@h:r.git",
        auth_type="ssh", auth_secret="KEYDATA",
    )
    keyfile_holder = {}
    with _auth(ssh_repo) as (extra, env):
        assert extra == []
        cmd = env["GIT_SSH_COMMAND"]
        keyfile = cmd.split("-i ", 1)[1].split(" ", 1)[0]
        keyfile_holder["path"] = keyfile
        assert os.path.exists(keyfile)
        assert open(keyfile).read().startswith("KEYDATA")
    # The temp key is cleaned up when the context exits.
    assert not os.path.exists(keyfile_holder["path"])


def test_repo_effective_url_matches_credential_transport():
    """Token auth over an SSH URL (and vice versa) is rewritten at git-time."""
    from agent_team.features.repos.git_service import _effective_url

    # Token credential + scp-like SSH URL -> rewritten to HTTPS so the
    # ``http.extraHeader`` Basic auth is actually used (the reported bug).
    token_ssh = AgentTeamRepo(
        name="a", slug="a", git_url="git@gitlab.com:chizy/chizy-chatbot.git",
        auth_type="token", auth_secret="tok",
    )
    assert _effective_url(token_ssh) == "https://gitlab.com/chizy/chizy-chatbot.git"

    # Token credential already on HTTPS -> unchanged.
    token_https = AgentTeamRepo(
        name="b", slug="b", git_url="https://gitlab.com/o/r.git",
        auth_type="token", auth_secret="tok",
    )
    assert _effective_url(token_https) == "https://gitlab.com/o/r.git"

    # SSH key credential + HTTPS URL -> rewritten to scp-like SSH.
    ssh_https = AgentTeamRepo(
        name="c", slug="c", git_url="https://gitlab.com/o/r.git",
        auth_type="ssh", auth_secret="KEY",
    )
    assert _effective_url(ssh_https) == "git@gitlab.com:o/r.git"

    # No credential -> never rewritten (e.g. local path clones).
    none_local = AgentTeamRepo(
        name="d", slug="d", git_url="/tmp/some/local/repo", auth_type="none",
    )
    assert _effective_url(none_local) == "/tmp/some/local/repo"


def test_repo_sync_clone_and_task_copy(db, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path / "ws"))
    src = _make_source_repo(tmp_path / "src")

    from agent_team.features.repos import git_service
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.paths import canonical_path
    from agent_team.features.repos.schemas import RepoCreate
    from agent_team.features.repos.task_copy import (
        cleanup_task_repos,
        prepare_task_repos,
    )

    repo = repos_repo.create_repo(
        db, owner_id="owner1", payload=RepoCreate(name="Svc", git_url=str(src))
    )
    result = git_service.sync_repo_by_id(repo.id)
    assert result.ok, result.message
    db.expire_all()
    repo = repos_repo.get_repo(db, repo.id)
    assert repo.clone_status == "cloned"
    assert repo.last_sync_status == "ok"
    canon = canonical_path("owner1", repo.slug)
    assert (canon / ".git").exists()

    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="owner1"
    )
    db.commit()
    repos_repo.assign_repo(db, board_id=board.id, repo_id=repo.id)
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    prepared = prepare_task_repos(db, task)
    assert prepared and prepared[0]["slug"] == repo.slug
    from pathlib import Path

    copy = Path(task.workspace_path) / repo.slug
    assert (copy / ".git").exists()
    assert (copy / "README.md").exists()

    # `git clone --local` hardlinks the object store: at least one loose object
    # shares an inode with the canonical clone (so history costs ~no extra disk).
    canon_objs = {
        p.stat().st_ino
        for p in (canon / ".git" / "objects").rglob("*")
        if p.is_file()
    }
    copy_objs = {
        p.stat().st_ino
        for p in (copy / ".git" / "objects").rglob("*")
        if p.is_file()
    }
    assert canon_objs & copy_objs

    # Re-running is idempotent (copy already present).
    again = prepare_task_repos(db, task)
    assert again and again[0]["slug"] == repo.slug

    assert cleanup_task_repos(db, task) == 1
    assert not copy.exists()


def test_build_task_context_includes_repos_only_on_first_turn(db):
    from agent_team.features.board.runtime.context import build_task_context

    task = _make_task(db)
    repos = [{"slug": "backend", "path": "backend", "branch": "main"}]
    full = build_task_context(task, "Do it.", repos=repos, full=True)
    assert "Code repositories checked out" in full
    assert "`backend/` (branch main)" in full
    # Follow-up turns don't repeat the repo list (it's already in history).
    delta = build_task_context(
        task, "More.", repos=repos, full=False, include_description=False
    )
    assert "backend/" not in delta


def test_repo_push_policy_roundtrip(db):
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate, RepoUpdate

    repo = repos_repo.create_repo(
        db,
        owner_id="owner1",
        payload=RepoCreate(
            name="Svc",
            git_url="https://example.com/x.git",
            allow_push=True,
            committer_name="Bot",
            committer_email="bot@org.com",
        ),
    )
    dto = repos_repo.serialize_repo(db, repo)
    assert dto.allow_push is True
    assert dto.committer_name == "Bot"
    assert dto.committer_email == "bot@org.com"

    # Turning push off and clearing identity persists.
    repos_repo.update_repo(
        db, repo, RepoUpdate(allow_push=False, committer_name="", committer_email="")
    )
    dto2 = repos_repo.serialize_repo(db, repo)
    assert dto2.allow_push is False
    assert dto2.committer_name is None
    assert dto2.committer_email is None


def _prepare_pushable_task(db, tmp_path, monkeypatch, *, allow_push: bool):
    """Clone a source repo, assign to a board, create a task, prepare copies."""
    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path / "ws"))
    src = _make_source_repo(tmp_path / "src")

    from agent_team.features.repos import git_service
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoCreate

    repo = repos_repo.create_repo(
        db,
        owner_id="owner1",
        payload=RepoCreate(
            name="Svc",
            git_url=str(src),
            allow_push=allow_push,
            committer_name="Bot",
            committer_email="bot@org.com",
        ),
    )
    assert git_service.sync_repo_by_id(repo.id).ok
    board = boards_repo.create_board(
        db, name="B", description=None, columns=None, owner_id="owner1"
    )
    db.commit()
    repos_repo.assign_repo(
        db, board_id=board.id, repo_id=repo.id, allow_push=True
    )
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()
    return src, repo, task


def test_prepare_task_repos_sets_task_branch_and_identity(db, tmp_path, monkeypatch):
    from pathlib import Path

    from agent_team.features.repos.task_copy import prepare_task_repos, task_branch_name

    _src, repo, task = _prepare_pushable_task(
        db, tmp_path, monkeypatch, allow_push=True
    )
    prepared = prepare_task_repos(db, task)
    assert prepared[0]["can_push"] is True
    assert prepared[0]["branch"] == task_branch_name(task)

    copy = Path(task.workspace_path) / repo.slug
    import subprocess

    branch = subprocess.run(
        ["git", "-C", str(copy), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert branch == task_branch_name(task)
    email = subprocess.run(
        ["git", "-C", str(copy), "config", "user.email"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert email == "bot@org.com"


def test_prepare_task_repos_reattaches_task_branch_on_existing_copy(
    db, tmp_path, monkeypatch
):
    """A copy that drifted off the task branch is switched back on re-prepare."""
    import subprocess
    from pathlib import Path

    from agent_team.features.repos.task_copy import prepare_task_repos, task_branch_name

    _src, repo, task = _prepare_pushable_task(
        db, tmp_path, monkeypatch, allow_push=True
    )
    prepare_task_repos(db, task)
    copy = Path(task.workspace_path) / repo.slug

    # Simulate a pre-existing copy left on another branch (e.g. created before
    # this logic, stuck on the default branch).
    subprocess.run(
        ["git", "-C", str(copy), "checkout", "-B", "stray"],
        capture_output=True, text=True,
    )

    # A subsequent prepare must put it back on the task branch.
    prepare_task_repos(db, task)
    branch = subprocess.run(
        ["git", "-C", str(copy), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert branch == task_branch_name(task)


def test_git_push_tool_respects_allow_push(db, tmp_path, monkeypatch):
    from agent_team.features.board.runtime.git_tools import get_git_tools
    from agent_team.features.repos import repositories as repos_repo
    from agent_team.features.repos.schemas import RepoUpdate
    from agent_team.features.repos.task_copy import prepare_task_repos, task_branch_name

    from plugins.standard_tools.tools.workspace_override import (
        reset_workspace_override,
        set_workspace_override,
    )

    src, repo, task = _prepare_pushable_task(
        db, tmp_path, monkeypatch, allow_push=False
    )
    prepare_task_repos(db, task)

    token = set_workspace_override(task.workspace_path)
    try:
        tools = {t.name: t for t in get_git_tools("alice", {})}
        git_push = tools["git_push"]

        # Push disabled by policy → refused, nothing reaches the remote.
        out = git_push.invoke({"repo": repo.slug})
        assert "disabled" in out.lower()

        # Admin enables push; agent makes a change and pushes its task branch.
        repos_repo.update_repo(db, repo, RepoUpdate(allow_push=True))
        from pathlib import Path

        (Path(task.workspace_path) / repo.slug / "new.txt").write_text("hi\n")
        out2 = git_push.invoke({"repo": repo.slug, "message": "add file"})
        assert "pushed" in out2.lower(), out2
    finally:
        reset_workspace_override(token)

    import subprocess

    branch = task_branch_name(task)
    rc = subprocess.run(
        ["git", "-C", str(src), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, "task branch should exist on the remote after push"


def test_set_task_status_tool_moves_within_board(db, monkeypatch):
    """The agent tool moves its task by column key/name and rejects unknowns."""
    from agent_team.features.board.repositories import activity as activity_repo
    from agent_team.features.board.runtime.status_tools import get_status_tools

    from plugins.standard_tools.tools.workspace_override import (
        reset_workspace_override,
        set_workspace_override,
    )

    board = boards_repo.create_board(  # columns: pending/todo/in_progress/review/done
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="t", description=None, status="in_progress",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    token = set_workspace_override(task.workspace_path)
    try:
        tools = {t.name: t for t in get_status_tools("agent:a", {})}
        set_status = tools["set_task_status"]

        # By display name (case-insensitive).
        out = set_status.invoke({"status": "Review"})
        assert "moved" in out.lower()
        db.expire_all()
        assert tasks_repo.get_task(db, task.id).status == "review"

        # Already there → no-op message.
        assert "already" in set_status.invoke({"status": "review"}).lower()

        # Unknown column → rejected, status unchanged.
        out = set_status.invoke({"status": "nope"})
        assert "unknown column" in out.lower()
        db.expire_all()
        assert tasks_repo.get_task(db, task.id).status == "review"
    finally:
        reset_workspace_override(token)

    kinds = [a.kind for a in activity_repo.list_activity(db, task.id)]
    assert activity_repo.AGENT_STATUS_CHANGED in kinds


def test_set_task_status_tool_registered_on_plugin():
    from agent_team.plugin import AgentTeamPlugin

    factories = {f.key: f for f in AgentTeamPlugin().tool_factories()}
    factory = factories["enable_agent_team_set_task_status"]
    assert factory.default_enabled is True
    tools = factory.create_tools("agent:a", {})
    assert [t.name for t in tools] == ["set_task_status"]


# ---------------------------------------------------------------------------
# Direct CLI (chat straight with Claude/Cursor/Codex over ACP, no LLM)
# ---------------------------------------------------------------------------


def test_direct_acp_alias_helpers():
    from agent_team.features.board.runtime import direct_acp as dacp

    assert dacp.is_direct_cli_alias("cli:claude")
    assert not dacp.is_direct_cli_alias("deep_agent")
    assert not dacp.is_direct_cli_alias(None)
    assert dacp.engine_for_alias("cli:Cursor") == "cursor"
    assert dacp.engine_for_alias("deep_agent") == ""
    assert dacp.alias_for_engine("codex") == "cli:codex"
    assert dacp.display_name_for_alias("cli:claude") == "Claude (direct)"
    assert dacp.display_name_for_alias("deep_agent") == "deep_agent"


def test_available_targets_lists_known_engines():
    from agent_team.features.board.runtime.direct_acp import available_targets

    targets = {t["engine"]: t for t in available_targets()}
    assert set(targets) == {"claude", "cursor", "codex"}
    assert targets["claude"]["id"] == "cli:claude"
    assert targets["claude"]["label"] == "Claude"
    assert isinstance(targets["claude"]["available"], bool)


def test_engine_runtime_reads_env(monkeypatch):
    from agent_team.features.board.runtime import direct_acp as dacp

    monkeypatch.setenv("AI_CODE_CLAUDE_ACP_COMMAND", "/opt/claude")
    monkeypatch.setenv("AI_CODE_CLAUDE_ACP_ARGS", "acp --flag")
    rt = dacp._engine_runtime("claude")
    assert rt.command == "/opt/claude"
    assert rt.args == ["acp", "--flag"]
    # The per-turn timeout is intentionally hard-pinned (long-form CLI jobs run
    # well past any env-derived default), so it is not read from the environment.
    assert rt.timeout_seconds == dacp._DIRECT_ACP_TURN_TIMEOUT_SECONDS
    assert rt.label == "Claude ACP"


def test_direct_acp_translator_maps_progress_thinking_and_tools():
    """Assistant text → text_delta, reasoning → thinking, tool calls → cards."""
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.direct_acp import _DirectAcpTranslator

    tr = _DirectAcpTranslator()
    assert tr.on_delta({"claude_acp_progress": "Hello"}) == [ev.text_delta("Hello")]
    assert tr.on_delta({"claude_acp_thought": "thinking"}) == [ev.thinking("thinking")]
    assert tr.on_delta({"claude_acp_plan": "step 1"}) == [ev.thinking("step 1")]

    start = tr.on_delta({"claude_acp_tool_start": "execute\x00Terminal\x00tid1"})
    assert len(start) == 1
    etype, data = start[0]
    assert etype == ev.EVENT_TOOL_USE_START
    tool_id = data["tool_id"]
    assert data["tool_name"] == "Terminal"
    # The raw ACP tool id is never surfaced in the frame.
    assert "tid1" not in str(data)

    done = tr.on_delta({"claude_acp_tool_progress": "tid1\x00completed\x00Terminal"})
    assert len(done) == 1
    etype, data = done[0]
    assert etype == ev.EVENT_TOOL_USE_END
    assert data["tool_id"] == tool_id
    assert data["success"] is True and data["is_error"] is False

    # The CLI's context-window gauge surfaces as a live usage frame…
    usage = tr.on_delta({"claude_acp_usage": "45,000/200,000 tokens"})
    assert usage == [ev.usage({"text": "45,000/200,000 tokens"})]
    # …and is remembered so it can be persisted after the run ends.
    assert tr.cli_usage_text == "45,000/200,000 tokens"
    # …while unknown keys are ignored.
    assert tr.on_delta({"claude_acp_unknown": "x"}) == []


def test_direct_acp_translator_captures_usage_final_totals():
    """``claude_acp_usage_final`` parses cumulative totals; emits no live frame."""
    from agent_team.features.board.runtime.direct_acp import _DirectAcpTranslator

    tr = _DirectAcpTranslator()
    # Encoded ``input\x00output\x00total\x00cache_read``.
    assert tr.on_delta({"claude_acp_usage_final": "1200\x00800\x002000\x00300"}) == []
    assert tr.totals == {
        "input_tokens": 1200,
        "output_tokens": 800,
        "total_tokens": 2000,
        "cache_read_tokens": 300,
    }


def test_direct_acp_translator_usage_final_derives_total_when_missing():
    """When ``total`` is 0, it falls back to input+output."""
    from agent_team.features.board.runtime.direct_acp import _DirectAcpTranslator

    tr = _DirectAcpTranslator()
    tr.on_delta({"claude_acp_usage_final": "1000\x00500\x000\x000"})
    assert tr.totals is not None
    assert tr.totals["total_tokens"] == 1500


def test_parse_gauge_tokens_extracts_used_and_size():
    from agent_team.features.board.runtime.direct_acp import _parse_gauge_tokens

    assert _parse_gauge_tokens("45,000/200,000 tokens") == (45000, 200000)
    assert _parse_gauge_tokens("12,345/67,890 tokens · $0.0123 USD") == (12345, 67890)
    assert _parse_gauge_tokens(None) == (0, 0)
    assert _parse_gauge_tokens("garbage") == (0, 0)


def test_usage_totals_roundtrip_producer_to_consumer():
    """Engine-agnostic accurate-totals path: ACP ``Usage`` → encode → decode.

    Proves that whenever *any* ACP engine populates ``PromptResponse.usage``,
    the producer (``_acp_base._format_usage_totals``) and the direct-CLI consumer
    (``direct_acp._parse_usage_totals``) agree on the wire format, so accurate
    cumulative totals get persisted on the run. (Today Claude populates this;
    Cursor/Codex don't yet — see the gauge fallback test below.)
    """
    from acp.schema import Usage

    from agent_team.features.board.runtime.direct_acp import _parse_usage_totals
    from plugins.ai_code.tools._acp_base import _format_usage_totals

    usage = Usage.model_validate(
        {
            "inputTokens": 1200,
            "outputTokens": 800,
            "totalTokens": 2000,
            "cachedReadTokens": 300,
            "cachedWriteTokens": 0,
            "thoughtTokens": 50,
        }
    )
    encoded = _format_usage_totals(usage)
    assert _parse_usage_totals(encoded) == {
        "input_tokens": 1200,
        "output_tokens": 800,
        "total_tokens": 2000,
        "cache_read_tokens": 300,
    }


def test_direct_acp_translator_failed_tool_and_finalize():
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.direct_acp import _DirectAcpTranslator

    tr = _DirectAcpTranslator()
    tr.on_delta({"claude_acp_tool_start": "edit\x00Patch file\x00tid9"})
    failed = tr.on_delta({"claude_acp_tool_progress": "tid9\x00failed\x00Patch file"})
    assert failed[0][0] == ev.EVENT_TOOL_USE_END
    assert failed[0][1]["is_error"] is True

    # An unfinished tool is closed by finalize so no card is left hanging.
    tr.on_delta({"claude_acp_tool_start": "read\x00Read file\x00tid10"})
    closing = tr.finalize()
    assert [t for t, _ in closing] == [ev.EVENT_TOOL_USE_END]
    assert tr.finalize() == []  # idempotent


def test_direct_acp_translator_surfaces_command_and_output():
    """The 4th field feeds the card's command input and its output preview."""
    from agent_team.features.board.runtime.direct_acp import _DirectAcpTranslator

    tr = _DirectAcpTranslator()
    start = tr.on_delta(
        {"claude_acp_tool_start": "execute\x00Terminal\x00tid1\x00ls -la"}
    )
    _etype, data = start[0]
    assert data["input"] == {"kind": "execute", "command": "ls -la"}

    done = tr.on_delta(
        {"claude_acp_tool_progress": "tid1\x00completed\x00Terminal\x00total 0\nfile.py"}
    )
    _etype, data = done[0]
    assert data["tool_name"] == "Terminal"
    assert data["output_preview"] == "total 0\nfile.py"


def test_direct_acp_translator_command_revealed_after_start():
    """A command absent at start but revealed by a later update reaches the card."""
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.direct_acp import _DirectAcpTranslator

    tr = _DirectAcpTranslator()
    start = tr.on_delta({"claude_acp_tool_start": "execute\x00Terminal\x00tid1\x00"})
    assert "command" not in start[0][1]["input"]

    # An in-progress update reveals the command → a live input update.
    mid = tr.on_delta({"claude_acp_tool_progress": "tid1\x00in_progress\x00\x00\x00ls -la"})
    assert mid[0][0] == ev.EVENT_TOOL_USE_PROGRESS
    assert mid[0][1]["input"] == {"command": "ls -la"}

    # Completion (no command field) still carries the accumulated command.
    done = tr.on_delta({"claude_acp_tool_progress": "tid1\x00completed\x00Terminal\x00ok\x00"})
    assert done[0][0] == ev.EVENT_TOOL_USE_END
    assert done[0][1]["input"] == {"command": "ls -la"}
    assert done[0][1]["output_preview"] == "ok"


def test_cli_context_render_brief_and_repos_block():
    """The brief carries description + notes + repos, without the git_push tool."""
    from agent_team.features.board.runtime import cli_context

    repos = [{"path": "svc", "branch": "agent/T-7", "can_push": True}]
    notes = [
        {"author": "alice", "created_at": "2026-06-09 22:30 UTC",
         "body": "Use staging.", "attachments": []},
    ]
    brief = cli_context.render_brief(_fake_task(), notes, repos)
    assert "# Task T-7: Build the importer" in brief
    assert "Parse the CSV and load rows." in brief
    assert "`svc/` (branch `agent/T-7`)" in brief
    assert "Use staging." in brief
    # Direct CLI has no git_push tool — the block must not mention it.
    assert "git_push" not in brief


def test_cli_context_write_files_creates_brief_and_pointers(tmp_path):
    from agent_team.features.board.runtime import cli_context

    ws = tmp_path / "ws"
    ws.mkdir()
    task = _fake_task()
    task.workspace_path = str(ws)
    cli_context.write_context_files(str(ws), task, notes=None, repos=None)

    brief = ws / ".agent-team" / "TASK.md"
    assert brief.exists() and "# Task T-7" in brief.read_text()
    for pointer in ("CLAUDE.md", "AGENTS.md"):
        body = (ws / pointer).read_text()
        assert ".agent-team/TASK.md" in body
    rule = ws / ".cursor" / "rules" / "agent-team-task.mdc"
    assert "alwaysApply: true" in rule.read_text()
    assert ".agent-team/TASK.md" in rule.read_text()


def test_cli_context_build_prompt_nudges_first_turn_and_new_notes():
    """First turn always nudges; later turns nudge only when new notes arrived."""
    from agent_team.features.board.runtime import cli_context

    first = cli_context.build_prompt("Fix the bug", first_turn=True)
    assert "Fix the bug" in first
    assert ".agent-team/TASK.md" in first

    # Follow-up with no new notes → sent verbatim.
    later = cli_context.build_prompt("And now add tests", first_turn=False)
    assert later == "And now add tests"
    assert ".agent-team/TASK.md" not in later

    # Follow-up with new notes → re-read nudge appended.
    with_notes = cli_context.build_prompt(
        "Keep going", first_turn=False, has_new_notes=True
    )
    assert "Keep going" in with_notes
    assert "New notes" in with_notes
    assert ".agent-team/TASK.md" in with_notes

    # An empty first message still carries the nudge so the agent is grounded.
    assert ".agent-team/TASK.md" in cli_context.build_prompt("", first_turn=True)


# ---------------------------------------------------------------------------
# Board autopilot
# ---------------------------------------------------------------------------


def _make_autopilot(db, board_id, **overrides):
    from agent_team.features.board.repositories import autopilot as autopilot_repo

    row = autopilot_repo.get_or_create(db, board_id)
    for key, value in overrides.items():
        setattr(row, key, value)
    db.flush()
    return row


def test_autopilot_schedule_math():
    """Interval mode clamps to the allowed window; off mode/invalid cron → None."""
    from datetime import UTC, datetime

    from agent_team.features.board.models import (
        AUTOPILOT_MAX_INTERVAL_SECONDS,
        AUTOPILOT_MIN_INTERVAL_SECONDS,
        AUTOPILOT_SCHEDULE_CRON,
        AUTOPILOT_SCHEDULE_INTERVAL,
        AUTOPILOT_SCHEDULE_OFF,
        AgentTeamAutopilot,
    )
    from agent_team.features.board.runtime import autopilot as ap

    base = datetime(2026, 1, 1, tzinfo=UTC)

    # Below the floor clamps up; above the ceiling clamps down.
    tiny = AgentTeamAutopilot(
        board_id="b", schedule_mode=AUTOPILOT_SCHEDULE_INTERVAL, interval_seconds=1
    )
    nxt = ap.compute_next_run_at(tiny, base=base)
    assert (nxt - base).total_seconds() == AUTOPILOT_MIN_INTERVAL_SECONDS

    huge = AgentTeamAutopilot(
        board_id="b",
        schedule_mode=AUTOPILOT_SCHEDULE_INTERVAL,
        interval_seconds=AUTOPILOT_MAX_INTERVAL_SECONDS * 10,
    )
    nxt = ap.compute_next_run_at(huge, base=base)
    assert (nxt - base).total_seconds() == AUTOPILOT_MAX_INTERVAL_SECONDS

    off = AgentTeamAutopilot(board_id="b", schedule_mode=AUTOPILOT_SCHEDULE_OFF)
    assert ap.compute_next_run_at(off, base=base) is None

    # Cron: invalid → None, valid → a strictly future instant.
    bad = AgentTeamAutopilot(
        board_id="b", schedule_mode=AUTOPILOT_SCHEDULE_CRON, cron="not a cron"
    )
    assert ap.compute_next_run_at(bad, base=base) is None
    assert ap.is_valid_cron("*/5 * * * *") is True
    assert ap.is_valid_cron("nope") is False
    good = AgentTeamAutopilot(
        board_id="b", schedule_mode=AUTOPILOT_SCHEDULE_CRON, cron="*/5 * * * *"
    )
    assert ap.compute_next_run_at(good, base=base) > base


def test_autopilot_concurrency_for_overrides_default():
    """Per-agent override wins; unknown agents fall back to the default cap."""
    row = AgentTeamAutopilot(board_id="b", default_agent_concurrency=1)
    row.agent_concurrency_json = json.dumps({"cli:claude": 3})
    assert row.concurrency_for("cli:claude") == 3
    assert row.concurrency_for("agent:other") == 1


def test_autopilot_process_board_claims_by_status_and_concurrency(db, monkeypatch):
    """A due board claims only assigned, source-status tasks within both caps."""
    from datetime import UTC, datetime

    from agent_team.features.board.runtime import autopilot as ap
    from agent_team.features.board.runtime import dispatch

    started_ids: list[str] = []
    monkeypatch.setattr(
        dispatch, "dispatch_start", lambda run_id: (started_ids.append(run_id), True)[1]
    )

    board = boards_repo.create_board(
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    board.agents_json = json.dumps(["agent:a", "agent:b"])
    db.flush()

    def mk(agent, status="todo", **kw):
        t = tasks_repo.create_task(
            db, board_id=board.id, title="t", description=None, status=status,
            assignee_id=None, labels=None, priority=None, created_by=None,
            agent_assignee=agent, **kw,
        )
        db.flush()
        return t

    # Eligible: two for agent:a, one for agent:b.
    mk("agent:a")
    mk("agent:a")
    mk("agent:b")
    # Ignored: not assigned, wrong column, or assigned to a non-staffed agent.
    mk(None)
    mk("agent:a", status="in_progress")
    mk("agent:ghost")
    db.commit()

    row = _make_autopilot(
        db, board.id, enabled=True, board_concurrency=5, default_agent_concurrency=1
    )
    db.commit()

    started = ap._process_board(db, row, datetime.now(UTC))

    # Per-agent cap of 1 means only one task per agent is claimed this tick.
    assert started == 2
    assert len(started_ids) == 2
    working = [
        t for t in tasks_repo.list_tasks(db, board_id=board.id)
        if t.status == "in_progress"
    ]
    # One newly-claimed per agent + the pre-existing in_progress task = 3.
    assert len(working) == 3
    runs = db.query(AgentTeamRun).all()
    assert len(runs) == 2
    assert {r.trigger for r in runs} == {"autopilot"}


def test_autopilot_board_concurrency_caps_total(db, monkeypatch):
    """The board-wide cap limits total claims regardless of per-agent room."""
    from datetime import UTC, datetime

    from agent_team.features.board.runtime import autopilot as ap
    from agent_team.features.board.runtime import dispatch

    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board = boards_repo.create_board(
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    board.agents_json = json.dumps(["agent:a", "agent:b", "agent:c"])
    db.flush()
    for agent in ("agent:a", "agent:b", "agent:c"):
        tasks_repo.create_task(
            db, board_id=board.id, title="t", description=None, status="todo",
            assignee_id=None, labels=None, priority=None, created_by=None,
            agent_assignee=agent,
        )
        db.flush()
    db.commit()

    row = _make_autopilot(
        db, board.id, enabled=True, board_concurrency=1, default_agent_concurrency=5
    )
    db.commit()

    started = ap._process_board(db, row, datetime.now(UTC))
    assert started == 1


def test_autopilot_route_now_assigns_by_rule_and_round_robin(db):
    """Routing fills unassigned source tasks: first match wins, group rotates."""
    from agent_team.features.board.repositories import autopilot as autopilot_repo
    from agent_team.features.board.runtime import autopilot as ap

    board = boards_repo.create_board(
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    board.agents_json = json.dumps(["agent:a", "agent:b", "agent:fe"])
    db.flush()

    def mk(labels=None, priority=None, agent=None):
        t = tasks_repo.create_task(
            db, board_id=board.id, title="t", description=None, status="todo",
            assignee_id=None, labels=labels, priority=priority, created_by=None,
            agent_assignee=agent,
        )
        db.flush()
        return t

    # Rule 0: label "frontend" → agent:fe. Rule 1 (catch-all) → rotate a,b.
    row = _make_autopilot(db, board.id, source_status="todo")
    autopilot_repo.set_routing_rules(
        row,
        [
            {"labels": ["frontend"], "priorities": [], "agents": ["agent:fe"]},
            {"labels": [], "priorities": [], "agents": ["agent:a", "agent:b"]},
        ],
    )
    fe = mk(labels=["frontend"])
    t1 = mk()
    t2 = mk()
    t3 = mk()
    already = mk(agent="agent:a")  # has an agent → untouched
    db.commit()

    assigned = ap.route_now(db, board.id)
    db.commit()
    db.expire_all()

    assert assigned == 4
    assert tasks_repo.get_task(db, fe.id).agent_assignee == "agent:fe"
    # Catch-all rotates a, b, a across the three plain tasks (by position order).
    rotation = [
        tasks_repo.get_task(db, t.id).agent_assignee for t in (t1, t2, t3)
    ]
    assert rotation == ["agent:a", "agent:b", "agent:a"]
    assert tasks_repo.get_task(db, already.id).agent_assignee == "agent:a"

    # Re-running assigns nothing (no unassigned source tasks left).
    assert ap.route_now(db, board.id) == 0


def test_autopilot_route_now_skips_unstaffed_agents(db):
    """A matching rule whose agents aren't staffed is skipped (falls through)."""
    from agent_team.features.board.repositories import autopilot as autopilot_repo
    from agent_team.features.board.runtime import autopilot as ap

    board = boards_repo.create_board(
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    board.agents_json = json.dumps(["agent:b"])
    db.flush()
    row = _make_autopilot(db, board.id, source_status="todo")
    autopilot_repo.set_routing_rules(
        row,
        [
            {"labels": [], "priorities": [], "agents": ["agent:ghost"]},
            {"labels": [], "priorities": [], "agents": ["agent:b"]},
        ],
    )
    task = tasks_repo.create_task(
        db, board_id=board.id, title="t", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.commit()

    assert ap.route_now(db, board.id) == 1
    db.expire_all()
    assert tasks_repo.get_task(db, task.id).agent_assignee == "agent:b"


def _autopilot_session_local(db, monkeypatch):
    """Point ``autopilot.SessionLocal`` at the test engine (shared connection)."""
    from agent_team.features.board.runtime import autopilot as ap
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(
        bind=db.get_bind(), autoflush=False, autocommit=False, future=True
    )
    monkeypatch.setattr(ap, "SessionLocal", factory)


@pytest.mark.parametrize(
    "run_status,expected_status",
    [("done", "review"), ("error", "todo"), ("cancelled", "todo")],
)
def test_autopilot_on_run_finished_transitions(
    db, monkeypatch, run_status, expected_status
):
    """A finished autopilot run moves its task per the board's status mapping."""
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import autopilot as ap

    _autopilot_session_local(db, monkeypatch)

    board = boards_repo.create_board(
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="t", description=None, status="in_progress",
        assignee_id=None, labels=None, priority=None, created_by=None,
        agent_assignee="agent:a",
    )
    db.flush()
    conv = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="agent:a"
    )
    run = runs_repo.create_run(
        db, task_id=task.id, conversation=conv, agent_alias="agent:a",
        trigger="autopilot", actor_id=None, prompt="go",
    )
    _make_autopilot(
        db, board.id, source_status="todo", working_status="in_progress",
        done_status="review", error_status="todo", error_cooldown_seconds=60,
    )
    db.commit()

    ap.on_run_finished(run.id, run_status)

    db.expire_all()
    moved = tasks_repo.get_task(db, task.id)
    assert moved.status == expected_status
    if run_status == "error":
        assert moved.autopilot_attempts == 1
        assert moved.autopilot_resume_after is not None
    elif run_status == "done":
        assert moved.autopilot_attempts == 0


def test_autopilot_on_run_finished_ignores_non_autopilot(db, monkeypatch):
    """A manually-triggered run never moves the task via the autopilot hook."""
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import autopilot as ap

    _autopilot_session_local(db, monkeypatch)

    board = boards_repo.create_board(
        db, name="Auto", description=None, columns=None, owner_id=None
    )
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="t", description=None, status="in_progress",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()
    conv = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="agent:a"
    )
    run = runs_repo.create_run(
        db, task_id=task.id, conversation=conv, agent_alias="agent:a",
        trigger="mention", actor_id="u1", prompt="go",
    )
    _make_autopilot(db, board.id, working_status="in_progress", done_status="review")
    db.commit()

    ap.on_run_finished(run.id, "done")

    db.expire_all()
    assert tasks_repo.get_task(db, task.id).status == "in_progress"


# ---------------------------------------------------------------------------
# Task schedule (per-task recurring cron runs)
# ---------------------------------------------------------------------------


def _make_schedule(db, task_id, **overrides):
    from agent_team.features.board.repositories import task_schedule as schedule_repo

    row = schedule_repo.get_or_create(db, task_id)
    for key, value in overrides.items():
        setattr(row, key, value)
    db.flush()
    return row


def _scheduled_board_task(db, *, agent="agent:a", status="todo"):
    """A staffed board with one task; returns ``(board, task)``."""
    board = boards_repo.create_board(
        db, name="Sched", description=None, columns=None, owner_id=None
    )
    board.agents_json = json.dumps([agent])
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="recurring", description=None, status=status,
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()
    return board, task


def test_task_schedule_compute_next_run_at():
    """A valid cron yields a strictly future instant; invalid/empty → None."""
    from datetime import UTC, datetime

    from agent_team.features.board.models import AgentTeamTaskSchedule
    from agent_team.features.board.runtime import task_schedule as ts

    base = datetime(2026, 1, 1, tzinfo=UTC)
    good = AgentTeamTaskSchedule(task_id="t", cron="*/5 * * * *", timezone="UTC")
    assert ts.compute_next_run_at(good, base=base) > base

    bad = AgentTeamTaskSchedule(task_id="t", cron="not a cron")
    assert ts.compute_next_run_at(bad, base=base) is None

    empty = AgentTeamTaskSchedule(task_id="t", cron=None)
    assert ts.compute_next_run_at(empty, base=base) is None


def test_task_schedule_fire_starts_run(db, monkeypatch):
    """A due schedule starts one scheduled run with the configured prompt."""
    from agent_team.features.board.repositories import activity as activity_repo
    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    started_ids: list[str] = []
    monkeypatch.setattr(
        dispatch, "dispatch_start", lambda run_id: (started_ids.append(run_id), True)[1]
    )

    board, task = _scheduled_board_task(db)
    _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:a",
        prompt="daily standup", conversation_mode="continue",
    )
    db.commit()
    row = _make_schedule(db, task.id)

    from datetime import UTC, datetime

    assert ts._fire(db, row, datetime.now(UTC)) is True

    runs = db.query(AgentTeamRun).filter(AgentTeamRun.task_id == task.id).all()
    assert len(runs) == 1
    assert runs[0].trigger == "schedule"
    assert runs[0].prompt == "daily standup"
    assert runs[0].agent_alias == "agent:a"
    assert started_ids == [runs[0].id]
    # The task stays in its column (scheduled runs never move it).
    assert tasks_repo.get_task(db, task.id).status == "todo"
    # A fire is recorded in the activity log.
    kinds = [a.kind for a in activity_repo.list_activity(db, task.id)]
    assert activity_repo.SCHEDULE_FIRED in kinds


def test_task_schedule_fire_skips_when_run_in_flight(db, monkeypatch):
    """If a previous scheduled run is still running, the fire is skipped."""
    from agent_team.features.board.repositories import activity as activity_repo
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board, task = _scheduled_board_task(db)
    # An in-flight scheduled run already exists.
    conv = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="agent:a"
    )
    runs_repo.create_run(
        db, task_id=task.id, conversation=conv, agent_alias="agent:a",
        trigger="schedule", actor_id=None, prompt="prev",
    )
    row = _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:a",
    )
    db.commit()

    from datetime import UTC, datetime

    assert ts._fire(db, row, datetime.now(UTC)) is False
    # Still only the original run; a skip is recorded.
    runs = db.query(AgentTeamRun).filter(AgentTeamRun.task_id == task.id).all()
    assert len(runs) == 1
    kinds = [a.kind for a in activity_repo.list_activity(db, task.id)]
    assert activity_repo.SCHEDULE_SKIPPED in kinds


def test_task_schedule_fire_skips_when_autopilot_run_active(db, monkeypatch):
    """A scheduled fire is skipped if the same agent has an autopilot run going."""
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board, task = _scheduled_board_task(db, agent="agent:a")
    conv = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="agent:a"
    )
    # An autopilot (not schedule) run is already in flight for the same agent.
    runs_repo.create_run(
        db, task_id=task.id, conversation=conv, agent_alias="agent:a",
        trigger="autopilot", actor_id=None, prompt="auto",
    )
    row = _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:a",
    )
    db.commit()

    from datetime import UTC, datetime

    assert ts._fire(db, row, datetime.now(UTC)) is False
    # No new scheduled run was created.
    sched_runs = (
        db.query(AgentTeamRun)
        .filter(AgentTeamRun.task_id == task.id, AgentTeamRun.trigger == "schedule")
        .count()
    )
    assert sched_runs == 0


def test_task_schedule_fire_ignores_other_agent_run(db, monkeypatch):
    """An in-flight run for a *different* agent doesn't block the schedule."""
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.repositories import runs as runs_repo
    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board = boards_repo.create_board(
        db, name="Sched", description=None, columns=None, owner_id=None
    )
    board.agents_json = json.dumps(["agent:a", "agent:b"])
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="t", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()
    # agent:b is busy; the schedule runs agent:a → not blocked.
    conv = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="agent:b"
    )
    runs_repo.create_run(
        db, task_id=task.id, conversation=conv, agent_alias="agent:b",
        trigger="autopilot", actor_id=None, prompt="auto",
    )
    row = _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:a",
    )
    db.commit()

    from datetime import UTC, datetime

    assert ts._fire(db, row, datetime.now(UTC)) is True
    sched_runs = (
        db.query(AgentTeamRun)
        .filter(AgentTeamRun.task_id == task.id, AgentTeamRun.trigger == "schedule")
        .count()
    )
    assert sched_runs == 1


def test_task_schedule_fire_new_mode_resets_conversation(db, monkeypatch):
    """``new`` mode archives the active thread and opens a fresh attempt."""
    from agent_team.features.board.repositories import conversations as conv_repo
    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board, task = _scheduled_board_task(db)
    first = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias="agent:a"
    )
    assert first.attempt == 1
    row = _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:a",
        conversation_mode="new",
    )
    db.commit()

    from datetime import UTC, datetime

    assert ts._fire(db, row, datetime.now(UTC)) is True
    active = conv_repo.get_active_conversation(db, task_id=task.id, agent_alias="agent:a")
    assert active.attempt == 2


def test_task_schedule_fire_skips_unstaffed_agent(db, monkeypatch):
    """A schedule whose agent isn't staffed on the board is skipped."""
    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board, task = _scheduled_board_task(db, agent="agent:a")
    row = _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:ghost",
    )
    db.commit()

    from datetime import UTC, datetime

    assert ts._fire(db, row, datetime.now(UTC)) is False
    assert db.query(AgentTeamRun).filter(AgentTeamRun.task_id == task.id).count() == 0


def test_task_schedule_run_tick_advances_cursor(db, monkeypatch):
    """The tick fires due schedules and advances ``next_run_at`` (at-most-once)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import sessionmaker

    from agent_team.features.board.runtime import dispatch
    from agent_team.features.board.runtime import task_schedule as ts

    factory = sessionmaker(
        bind=db.get_bind(), autoflush=False, autocommit=False, future=True
    )
    monkeypatch.setattr(ts, "SessionLocal", factory)
    monkeypatch.setattr(dispatch, "main_loop_ready", lambda: True)
    monkeypatch.setattr(dispatch, "dispatch_start", lambda run_id: True)

    board, task = _scheduled_board_task(db)
    past = datetime.now(UTC) - timedelta(minutes=1)
    _make_schedule(
        db, task.id, enabled=True, cron="*/5 * * * *", agent_alias="agent:a",
        next_run_at=past,
    )
    db.commit()

    assert ts.run_tick() == 1

    db.expire_all()
    refreshed = _make_schedule(db, task.id)
    assert refreshed.next_run_at is not None
    # SQLite returns naive datetimes; compare both as naive.
    assert refreshed.next_run_at.replace(tzinfo=None) > past.replace(tzinfo=None)
    assert refreshed.last_run_id is not None


# ---------------------------------------------------------------------------
# Role-based access control (authz)
# ---------------------------------------------------------------------------


def test_authz_role_at_least_hierarchy():
    """owner > editor > viewer, and missing roles never satisfy a minimum."""
    from agent_team.features.board import authz

    assert authz.role_at_least("owner", "viewer")
    assert authz.role_at_least("owner", "editor")
    assert authz.role_at_least("owner", "owner")
    assert authz.role_at_least("editor", "viewer")
    assert authz.role_at_least("editor", "editor")
    assert not authz.role_at_least("editor", "owner")
    assert authz.role_at_least("viewer", "viewer")
    assert not authz.role_at_least("viewer", "editor")
    assert not authz.role_at_least("viewer", "owner")
    # No role at all (non-member) satisfies nothing.
    assert not authz.role_at_least(None, "viewer")
    assert not authz.role_at_least("", "viewer")


def test_access_role_owner_member_admin_and_nonmember(db):
    """`access_role` returns the real role, or None when the user has no access."""
    from agent_team.features.board.repositories import members as members_repo

    board = boards_repo.create_board(
        db, name="Perm", description=None, columns=None, owner_id="u-owner"
    )
    members_repo.add_member(db, board_id=board.id, user_id="u-editor", role="editor")
    members_repo.add_member(db, board_id=board.id, user_id="u-viewer", role="viewer")
    db.flush()

    role = lambda uid, admin=False: members_repo.access_role(
        db, board, user_id=uid, is_admin=admin
    )

    assert role("u-owner") == "owner"
    assert role("u-editor") == "editor"
    assert role("u-viewer") == "viewer"
    # A non-member gets no access (None), even though `effective_role` would
    # have defaulted them to "viewer".
    assert role("u-stranger") is None
    # Admins are treated as owner on any board, member or not.
    assert role("u-stranger", admin=True) == "owner"


# ---------------------------------------------------------------------------
# Per-CLI-agent MCP config
# ---------------------------------------------------------------------------


def test_board_agent_mcp_helpers(db):
    """`agent_mcp()` decodes the map; `agent_mcp_for` returns one alias' config."""
    board = boards_repo.create_board(
        db, name="MCP", description=None, columns=None, owner_id="u1"
    )
    board.agent_mcp_json = json.dumps(
        {"cli:claude": {"mcpServers": {"ctx7": {"url": "https://x/sse"}}}}
    )
    db.flush()

    assert board.agent_mcp() == {
        "cli:claude": {"mcpServers": {"ctx7": {"url": "https://x/sse"}}}
    }
    assert board.agent_mcp_for("cli:claude") == {
        "mcpServers": {"ctx7": {"url": "https://x/sse"}}
    }
    # Unknown alias or malformed JSON yields an empty config rather than raising.
    assert board.agent_mcp_for("cli:cursor") == {}
    board.agent_mcp_json = "not json"
    assert board.agent_mcp() == {}
    assert board.agent_mcp_for("cli:claude") == {}


def test_collect_mcp_secrets_picks_auth_headers_env():
    """Secret collection gathers auth/headers/env values, skipping short ones."""
    from agent_team.features.board.runtime.local_backend import _collect_mcp_secrets

    cfg = {
        "mcpServers": {
            "remote": {
                "url": "https://x/sse",
                "auth": "supersecrettoken",
                "headers": {"X-Api-Key": "headervalue123"},
            },
            "local": {"command": "node", "env": {"TOKEN": "envvalue123"}},
            "short": {"auth": "abc"},
        }
    }
    secrets = _collect_mcp_secrets(cfg)
    assert "supersecrettoken" in secrets
    assert "headervalue123" in secrets
    assert "envvalue123" in secrets
    # Values shorter than the threshold are not masked.
    assert "abc" not in secrets


def test_serialize_board_hides_agent_mcp_from_non_owner(db):
    """MCP config (may hold tokens) is returned to owners only."""
    board = boards_repo.create_board(
        db, name="MCP", description=None, columns=None, owner_id="u1"
    )
    board.agent_mcp_json = json.dumps(
        {"cli:claude": {"mcpServers": {"ctx7": {"url": "https://x/sse"}}}}
    )
    db.flush()

    owner_dto = boards_repo.serialize_board(board, my_role="owner")
    assert owner_dto.agent_mcp == {
        "cli:claude": {"mcpServers": {"ctx7": {"url": "https://x/sse"}}}
    }
    viewer_dto = boards_repo.serialize_board(board, my_role="viewer")
    assert viewer_dto.agent_mcp == {}


# ---------------------------------------------------------------------------
# Loop session isolation (planner / generator / evaluator)
# ---------------------------------------------------------------------------


def _loop_board_task(db):
    board = boards_repo.create_board(
        db, name="Loop", description=None, columns=None, owner_id="u1"
    )
    db.flush()
    task = tasks_repo.create_task(
        db, board_id=board.id, title="T", description=None, status="todo",
        assignee_id=None, labels=None, priority=None, created_by=None,
    )
    db.flush()
    return board, task


def test_loop_roles_get_isolated_sessions(db):
    """Planner/generator/evaluator never share the chat alias's thread."""
    from agent_team.features.board.repositories import conversations as conv_repo

    _, task = _loop_board_task(db)
    alias = "cli:claude"

    chat = conv_repo.get_or_create_active_conversation(
        db, task_id=task.id, agent_alias=alias
    )
    gen = conv_repo.get_or_create_loop_conversation(
        db, task_id=task.id, agent_alias=alias, role="generator"
    )
    plan = conv_repo.get_or_create_loop_conversation(
        db, task_id=task.id, agent_alias=alias, role="planner"
    )
    db.flush()

    # Each role's thread is distinct from the manual chat and from each other.
    threads = {chat.thread_id, gen.thread_id, plan.thread_id}
    assert len(threads) == 3
    # The real engine alias is recoverable (synthetic alias is a scoped prefix).
    assert gen.agent_alias.startswith(alias)
    assert plan.agent_alias != gen.agent_alias


def test_loop_generator_session_is_stable_evaluator_is_fresh(db):
    """Generator reuses one thread across attempts; evaluator gets a new one."""
    from agent_team.features.board.repositories import conversations as conv_repo

    _, task = _loop_board_task(db)
    alias = "cli:claude"

    gen1 = conv_repo.get_or_create_loop_conversation(
        db, task_id=task.id, agent_alias=alias, role="generator"
    )
    db.flush()
    gen2 = conv_repo.get_or_create_loop_conversation(
        db, task_id=task.id, agent_alias=alias, role="generator"
    )
    db.flush()
    # Generator: same persistent thread carried across attempts.
    assert gen1.thread_id == gen2.thread_id

    ev1 = conv_repo.get_or_create_loop_conversation(
        db, task_id=task.id, agent_alias=alias, role="evaluator", fresh=True
    )
    db.flush()
    ev2 = conv_repo.get_or_create_loop_conversation(
        db, task_id=task.id, agent_alias=alias, role="evaluator", fresh=True
    )
    db.flush()
    # Evaluator: a brand-new isolated thread every grading.
    assert ev1.thread_id != ev2.thread_id
    assert ev1.thread_id != gen1.thread_id
