"""Tests for the agent_team plugin (board feature)."""

from __future__ import annotations

import json

import pytest
from agent_team.features.board.keys import next_human_key, slugify
from agent_team.features.board.models import (
    AgentTeamActivity,
    AgentTeamBoard,
    AgentTeamBoardMember,
    AgentTeamComment,
    AgentTeamConversation,
    AgentTeamKeySeq,
    AgentTeamRun,
    AgentTeamRunEvent,
    AgentTeamTask,
)
from agent_team.features.board.repositories import boards as boards_repo
from agent_team.features.board.repositories import tasks as tasks_repo
from agent_team.features.board.workspace import workspace_path_for
from agent_team.plugin import SPA_MOUNT_PATH, AgentTeamPlugin
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_PLUGIN_MODELS = (
    AgentTeamKeySeq,
    AgentTeamBoard,
    AgentTeamBoardMember,
    AgentTeamTask,
    AgentTeamConversation,
    AgentTeamRun,
    AgentTeamRunEvent,
    AgentTeamComment,
    AgentTeamActivity,
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
    )
    db.refresh(run)
    assert run.status == RUN_DONE
    assert run.final_answer == "done"
    assert run.total_tokens == 15
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

    assert [t for t, _ in answer_frames] == [ev.EVENT_TEXT_DELTA]
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
    from langchain_core.messages import AIMessage, ToolMessage

    monkeypatch.setenv("AGENT_TEAM_WORKSPACE_ROOT", str(tmp_path))

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
                {"agent": {"messages": [AIMessage(content="All done")]}},
            ]
        )

    monkeypatch.setattr(local_backend, "build_graph", fake_build_graph)
    monkeypatch.setattr(local_backend, "make_checkpointer", lambda alias: (object(), _DummyCtx()))

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
    assert ev.EVENT_FINAL_ANSWER in types
    assert types[-1] == ev.EVENT_RUN_END

    db.expire_all()
    refreshed = runs_repo.get_run(db, run.id)
    assert refreshed.status == ev.RUN_DONE
    assert refreshed.final_answer == "All done"
    assert refreshed.total_tokens == 5


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


def test_translator_dedupes_snapshot_and_emits_suffix():
    from agent_team.features.board.runtime import events as ev
    from agent_team.features.board.runtime.translator import StreamTranslator
    from langchain_core.messages import AIMessage

    translator = StreamTranslator()

    first = translator.translate({"agent": {"messages": [AIMessage(content="Hello")]}})
    # Same snapshot surfacing again (subgraph + parent) must not be re-emitted.
    dup = translator.translate({"agent": {"messages": [AIMessage(content="Hello")]}})
    # A growing snapshot emits only the new suffix.
    more = translator.translate(
        {"agent": {"messages": [AIMessage(content="Hello world")]}}
    )

    assert [(t, d["text"]) for t, d in first] == [(ev.EVENT_TEXT_DELTA, "Hello")]
    assert dup == []
    assert [(t, d["text"]) for t, d in more] == [(ev.EVENT_TEXT_DELTA, " world")]


def test_translator_strips_leaked_tool_use_blocks_from_text():
    """Anthropic tool_use blocks must surface as tool frames, never as text."""
    from agent_team.features.board.runtime import events as ev
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
    by_type: dict[str, list[dict]] = {}
    for ftype, data in frames:
        by_type.setdefault(ftype, []).append(data)

    # The tool call is surfaced as a proper tool frame ...
    assert by_type.get(ev.EVENT_TOOL_USE_START)
    # ... and the visible text carries only the prose, never the JSON block.
    text_frames = by_type.get(ev.EVENT_TEXT_DELTA, [])
    assert text_frames, "expected the prose to still stream"
    joined = "".join(d["text"] for d in text_frames)
    assert joined == "Let me read the file:"
    assert "tool_use" not in joined
    assert "toolu_123" not in joined
    # The model speaks before it calls the tool, so text must lead.
    order = [t for t, _ in frames]
    assert order.index(ev.EVENT_TEXT_DELTA) < order.index(ev.EVENT_TOOL_USE_START)


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
