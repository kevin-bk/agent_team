"""Unit tests for the Communication Gateway (v1: outbound notifications).

Cover the pure units that carry the logic risk: the state→event mapping and
dedupe key, message rendering + deep links, the Mattermost provider's text
rendering and HTTP send (mocked), the repositories (write-only token, one
channel per board, delivery dedupe uniqueness), and mention resolution from a
cached user link (no network).
"""

from __future__ import annotations

import pytest

# Importing the board models registers their tables (board/task/users targets)
# in the shared metadata so the comm tables' foreign keys resolve at create-time.
from agent_team.features.board import models as _board_models  # noqa: F401
from agent_team.features.comm import events, render, tagging
from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm.models import (
    TAG_ASSIGNEE,
    TAG_CREATOR,
    TAG_NONE,
    AgentTeamBoardChannel,
    AgentTeamCommConnection,
    AgentTeamCommDelivery,
    AgentTeamCommUserLink,
)
from agent_team.features.comm.providers import registry
from agent_team.features.comm.providers.base import CommMessage, Mention, ProviderTarget
from agent_team.features.comm.providers.mattermost import MattermostProvider
from agent_team.features.comm.providers.slack import SlackProvider
from agent_team.features.comm.refs import TaskRef
from agent_team.features.comm.schemas import ConnectionCreate, ConnectionUpdate
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database.models import User


@pytest.fixture()
def session(monkeypatch):
    """In-memory SQLite with the comm tables + users table created."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    for model in (
        AgentTeamCommConnection,
        AgentTeamBoardChannel,
        AgentTeamCommDelivery,
        AgentTeamCommUserLink,
        User,
    ):
        model.__table__.create(bind=engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    import core.database.base as core_db

    monkeypatch.setattr(core_db, "SessionLocal", factory)
    db = factory()
    try:
        yield db
    finally:
        db.close()


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


# ── events ───────────────────────────────────────────────────────────────────


def test_event_for_state_maps_only_relevant_states():
    assert events.event_for_state("waiting_plan_approval") == events.EVENT_PLAN_APPROVAL_REQUIRED
    assert events.event_for_state("waiting_answers") == events.EVENT_ANSWERS_REQUIRED
    assert events.event_for_state("complete") == events.EVENT_GOAL_COMPLETE
    # Silent states + unknown values map to nothing.
    assert events.event_for_state("running") is None
    assert events.event_for_state("plan_approved") is None
    assert events.event_for_state("bogus") is None


def test_dedupe_key_is_stable_and_attempt_scoped():
    a = events.dedupe_key(task_id="T1", event_type="goal_complete", state="complete", attempt=0)
    b = events.dedupe_key(task_id="T1", event_type="goal_complete", state="complete", attempt=0)
    c = events.dedupe_key(
        task_id="T1", event_type="answers_required", state="waiting_answers", attempt=1
    )
    assert a == b
    assert a != c


# ── render ─────────────────────────────────────────────────────────────────


def test_render_event_headline_and_severity():
    title, body, severity = render.render_event(
        event_type=events.EVENT_ANSWERS_REQUIRED, task_key="T-42", task_title="Add export"
    )
    assert title == "T-42 needs your answer"
    assert "Add export" in body
    assert severity == "warning"


def test_task_deep_link_requires_base():
    assert render.task_deep_link(deep_link_base=None, board_slug="b", task_key="T-1") is None
    link = render.task_deep_link(
        deep_link_base="https://x.test/", board_slug="ops", task_key="T-1"
    )
    assert link == "https://x.test/agent-team/boards/ops/tasks/T-1"


# ── provider: rendering + send ───────────────────────────────────────────────


def test_mattermost_render_text_includes_mentions_and_link():
    text = MattermostProvider().render_text(
        CommMessage(
            title="T-1 needs your answer",
            body="reason here",
            url="https://x.test/t/1",
            severity="warning",
            mentions=[Mention(handle="alice"), Mention(handle="bob")],
        )
    )
    assert "**T-1 needs your answer**" in text
    assert "reason here" in text
    assert "@alice @bob" in text
    assert "[Open task](https://x.test/t/1)" in text
    assert text.startswith(":warning:")


def test_mattermost_send_success(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp(201, {"id": "post123", "root_id": ""})

    monkeypatch.setattr("agent_team.features.comm.providers.mattermost.httpx.post", fake_post)
    result = MattermostProvider().send(
        ProviderTarget(server_url="https://mm.test", bot_token="tok", channel_id="c1"),
        CommMessage(title="hi"),
    )
    assert result.ok is True
    assert result.provider_message_id == "post123"
    # root_id falls back to the post id when the server returns an empty root.
    assert result.provider_thread_id == "post123"
    assert captured["url"] == "https://mm.test/api/v4/posts"
    assert captured["json"]["channel_id"] == "c1"


def test_mattermost_send_threads_under_root(monkeypatch):
    def fake_post(url, json, headers, timeout):
        assert json["root_id"] == "rootX"
        return _FakeResp(201, {"id": "p2", "root_id": "rootX"})

    monkeypatch.setattr("agent_team.features.comm.providers.mattermost.httpx.post", fake_post)
    result = MattermostProvider().send(
        ProviderTarget(
            server_url="https://mm.test",
            bot_token="tok",
            channel_id="c1",
            use_threads=True,
            root_id="rootX",
        ),
        CommMessage(title="reply"),
    )
    assert result.provider_thread_id == "rootX"


def test_mattermost_send_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        "agent_team.features.comm.providers.mattermost.httpx.post",
        lambda *a, **k: _FakeResp(401, {}),
    )
    result = MattermostProvider().send(
        ProviderTarget(server_url="https://mm.test", bot_token="bad", channel_id="c1"),
        CommMessage(title="hi"),
    )
    assert result.ok is False
    assert "401" in (result.error or "")


def test_mattermost_send_requires_full_config():
    result = MattermostProvider().send(
        ProviderTarget(server_url="", bot_token="", channel_id=""),
        CommMessage(title="hi"),
    )
    assert result.ok is False


# ── repositories ─────────────────────────────────────────────────────────────


def test_connection_token_is_write_only(session):
    conn = comm_repo.create_connection(
        session,
        owner_id="u1",
        payload=ConnectionCreate(name="MM", server_url="https://mm.test", bot_token="secret"),
    )
    dto = comm_repo.serialize_connection(session, conn)
    assert dto.has_token is True
    assert not hasattr(dto, "bot_token")
    # Omitting bot_token on update keeps it; "" clears it.
    comm_repo.update_connection(session, conn, ConnectionUpdate(name="MM2"))
    assert conn.bot_token == "secret"
    comm_repo.update_connection(session, conn, ConnectionUpdate(bot_token=""))
    assert conn.bot_token is None


def test_board_channel_is_one_per_board(session):
    comm_repo.upsert_board_channel(
        session,
        board_id="B1",
        connection_id="C1",
        channel_id="ch1",
        channel_name="agent-team",
        use_threads=True,
        event_allowlist=["goal_complete", "goal_complete", "answers_required"],
        tag_mode="assignee",
        enabled=True,
    )
    # A second upsert updates the same row, not a new one.
    row = comm_repo.upsert_board_channel(
        session,
        board_id="B1",
        connection_id="C2",
        channel_id="ch2",
        channel_name="urgent",
        use_threads=False,
        event_allowlist=["goal_failed"],
        tag_mode="creator",
        enabled=False,
    )
    all_rows = session.query(AgentTeamBoardChannel).filter_by(board_id="B1").all()
    assert len(all_rows) == 1
    assert row.connection_id == "C2"
    assert row.channel_id == "ch2"
    # Allowlist dedupes on write.
    comm_repo.upsert_board_channel(
        session,
        board_id="B1",
        connection_id="C2",
        channel_id="ch2",
        channel_name="urgent",
        use_threads=False,
        event_allowlist=["goal_failed", "goal_failed"],
        tag_mode="creator",
        enabled=True,
    )
    fresh = comm_repo.get_board_channel(session, "B1")
    assert comm_repo.board_channel_allowlist(fresh) == ["goal_failed"]


def test_delivery_dedupe_lookup(session):
    comm_repo.create_delivery(
        session,
        task_id="T1",
        board_id="B1",
        channel_id="ch1",
        event_type="goal_complete",
        provider="mattermost",
        dedupe_key="T1:goal_complete:complete:0",
        payload={"title": "done"},
    )
    found = comm_repo.get_delivery_by_dedupe(session, "T1:goal_complete:complete:0")
    assert found is not None
    assert comm_repo.get_delivery_by_dedupe(session, "nope") is None


def test_user_link_upsert_is_idempotent(session):
    a = comm_repo.upsert_user_link(
        session, connection_id="C1", user_id="u1", mm_user_id="m1", mm_username="alice"
    )
    b = comm_repo.upsert_user_link(
        session, connection_id="C1", user_id="u1", mm_user_id="m1", mm_username="alice2"
    )
    assert a.id == b.id
    assert b.mm_username == "alice2"
    rows = comm_repo.list_user_links(session, "C1")
    assert len(rows) == 1


# ── tagging ───────────────────────────────────────────────────────────────────


def _task() -> TaskRef:
    return TaskRef(
        id="T1",
        board_id="B1",
        key="T-1",
        title="Title",
        assignee_id="ua",
        reporter_id="ur",
        created_by="uc",
        board_slug="b",
    )


def test_target_user_ids_complete_tags_assignee_and_reporter():
    ids = tagging._target_user_ids(
        event_type=events.EVENT_GOAL_COMPLETE, tag_mode=TAG_ASSIGNEE, task=_task()
    )
    assert ids == ["ua", "ur"]


def test_target_user_ids_modes():
    assignee = tagging._target_user_ids(
        event_type=events.EVENT_ANSWERS_REQUIRED, tag_mode=TAG_ASSIGNEE, task=_task()
    )
    creator = tagging._target_user_ids(
        event_type=events.EVENT_ANSWERS_REQUIRED, tag_mode=TAG_CREATOR, task=_task()
    )
    none = tagging._target_user_ids(
        event_type=events.EVENT_ANSWERS_REQUIRED, tag_mode=TAG_NONE, task=_task()
    )
    assert assignee == ["ua"]
    assert creator == ["ur"]
    assert none == []


def test_resolve_mentions_uses_cached_link_without_network(session, monkeypatch):
    # A network call here would be a bug — the link is cached.
    def _boom(*a, **k):
        raise AssertionError("provider lookup should not run when link is cached")

    monkeypatch.setattr(MattermostProvider, "resolve_username", _boom)

    session.add(User(id="ua", email="a@x.test", username="alice", password_hash="x"))
    conn = comm_repo.create_connection(
        session,
        owner_id="u1",
        payload=ConnectionCreate(name="MM", server_url="https://mm.test", bot_token="tok"),
    )
    comm_repo.upsert_user_link(
        session, connection_id=conn.id, user_id="ua", mm_user_id="m1", mm_username="alice"
    )
    channel = comm_repo.upsert_board_channel(
        session,
        board_id="B1",
        connection_id=conn.id,
        channel_id="ch1",
        channel_name="agent-team",
        use_threads=True,
        event_allowlist=["answers_required"],
        tag_mode="assignee",
        enabled=True,
    )
    mentions = tagging.resolve_mentions(
        session,
        event_type=events.EVENT_ANSWERS_REQUIRED,
        channel=channel,
        connection=conn,
        task=_task(),
    )
    assert [m.handle for m in mentions] == ["alice"]


# ── provider registry + Slack ────────────────────────────────────────────────


def test_registry_resolves_known_providers_and_falls_back():
    assert isinstance(registry.get_provider("mattermost"), MattermostProvider)
    assert isinstance(registry.get_provider("slack"), SlackProvider)
    # Unknown provider falls back to Mattermost rather than crashing the loop.
    assert isinstance(registry.get_provider("bogus"), MattermostProvider)
    ids = registry.provider_ids()
    assert "mattermost" in ids and "slack" in ids
    assert registry.get_descriptor("slack").label == "Slack"


def test_slack_render_text_uses_slack_markup():
    text = SlackProvider().render_text(
        CommMessage(
            title="T-1 needs your answer",
            body="reason here",
            url="https://x.test/t/1",
            severity="warning",
            mentions=[Mention(user_id="U123", handle="alice"), Mention(handle="no-id")],
        )
    )
    assert "*T-1 needs your answer*" in text  # single-asterisk bold
    assert "<@U123>" in text  # mention by id
    assert "no-id" not in text  # no user id → not mentioned
    assert "<https://x.test/t/1|Open task>" in text
    assert text.startswith(":warning:")


def test_slack_send_success(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp(200, {"ok": True, "ts": "169.1", "channel": "C1"})

    monkeypatch.setattr("agent_team.features.comm.providers.slack.httpx.post", fake_post)
    result = SlackProvider().send(
        ProviderTarget(server_url="", bot_token="xoxb-1", channel_id="C1"),
        CommMessage(title="hi"),
    )
    assert result.ok is True
    assert result.provider_message_id == "169.1"
    assert result.provider_thread_id == "169.1"  # starts a new thread
    assert captured["url"] == "https://slack.com/api/chat.postMessage"
    assert captured["json"]["channel"] == "C1"


def test_slack_send_threads_under_root(monkeypatch):
    def fake_post(url, json, headers, timeout):
        assert json["thread_ts"] == "root.1"
        return _FakeResp(200, {"ok": True, "ts": "reply.2"})

    monkeypatch.setattr("agent_team.features.comm.providers.slack.httpx.post", fake_post)
    result = SlackProvider().send(
        ProviderTarget(
            server_url="",
            bot_token="xoxb-1",
            channel_id="C1",
            use_threads=True,
            root_id="root.1",
        ),
        CommMessage(title="reply"),
    )
    # Thread root stays the original ts, not the reply's own ts.
    assert result.provider_thread_id == "root.1"


def test_slack_send_failure_reads_ok_field(monkeypatch):
    monkeypatch.setattr(
        "agent_team.features.comm.providers.slack.httpx.post",
        lambda *a, **k: _FakeResp(200, {"ok": False, "error": "channel_not_found"}),
    )
    result = SlackProvider().send(
        ProviderTarget(server_url="", bot_token="xoxb-1", channel_id="bad"),
        CommMessage(title="hi"),
    )
    assert result.ok is False
    assert "channel_not_found" in (result.error or "")


def test_slack_resolve_username_by_email(monkeypatch):
    def fake_get(url, params, headers, timeout):
        assert params["email"] == "a@x.test"
        return _FakeResp(200, {"ok": True, "user": {"id": "U9", "name": "alice"}})

    monkeypatch.setattr("agent_team.features.comm.providers.slack.httpx.get", fake_get)
    user_id, username = SlackProvider().resolve_username(
        server_url="", bot_token="xoxb-1", email="a@x.test"
    )
    assert user_id == "U9"
    assert username == "alice"
