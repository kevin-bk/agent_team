"""Integration tests for the Communication Gateway REST router.

These exercise the endpoint functions directly (driving the async handlers with
``asyncio.run``) against an in-memory SQLite database, with the auth helpers
(``auth_or_401`` / ``authz.guard_board``) monkeypatched to a known caller. The
focus is the router's own logic: the admin gate, owner-scoping, the
deletion guard, provider validation, board-channel upsert/allowlist filtering,
the test-send path, and the user-mapping universe + auto-match.
"""

from __future__ import annotations

import asyncio

import pytest
from agent_team.features.board import authz
from agent_team.features.board.authz import BoardCtx
from agent_team.features.board.models import AgentTeamBoard, AgentTeamBoardMember
from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm import router as comm_router
from agent_team.features.comm import service as comm_service
from agent_team.features.comm.models import (
    AgentTeamBoardChannel,
    AgentTeamCommConnection,
    AgentTeamCommDelivery,
    AgentTeamCommUserLink,
)
from agent_team.features.comm.providers.mattermost import MattermostProvider
from agent_team.features.comm.schemas import (
    BoardChannelUpsert,
    ConnectionCreate,
    ConnectionUpdate,
    UserLinkUpsert,
)
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database.models import User, UserRole


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    for model in (
        User,
        AgentTeamBoard,
        AgentTeamBoardMember,
        AgentTeamCommConnection,
        AgentTeamBoardChannel,
        AgentTeamCommDelivery,
        AgentTeamCommUserLink,
    ):
        model.__table__.create(bind=engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def run(coro):
    return asyncio.run(coro)


def _user(db, *, uid: str, role: UserRole = UserRole.ADMIN, email: str | None = None) -> User:
    user = User(
        id=uid,
        email=email or f"{uid}@x.test",
        username=uid,
        full_name=uid.title(),
        password_hash="x",
        role=role,
    )
    db.add(user)
    db.commit()
    return user


def _as_user(monkeypatch, user: User) -> None:
    monkeypatch.setattr(comm_router, "auth_or_401", lambda db, request: (user, None))


def _as_board_owner(monkeypatch, user: User, role: str = "owner") -> None:
    def fake_guard(db, request, board_id, min_role="viewer"):
        return BoardCtx(user=user, board=None, role=role), None

    monkeypatch.setattr(authz, "guard_board", fake_guard)


def _make_connection(db, owner_id: str, *, token: str | None = "tok") -> AgentTeamCommConnection:
    return comm_repo.create_connection(
        db,
        owner_id=owner_id,
        payload=ConnectionCreate(name="MM", server_url="https://mm.test", bot_token=token),
    )


# ── public-ish endpoints (auth only) ─────────────────────────────────────────


def test_list_providers_includes_mattermost_and_slack(db, monkeypatch):
    _as_user(monkeypatch, _user(db, uid="adm"))
    result = run(comm_router.list_providers(None, db))
    ids = {d.id for d in result}
    assert {"mattermost", "slack"} <= ids
    slack = next(d for d in result if d.id == "slack")
    # Slack descriptor omits server_url (it uses the public API).
    assert "server_url" not in {f.key for f in slack.fields}
    assert any(f.type == "secret" for f in slack.fields)


def test_event_types_endpoint(db, monkeypatch):
    _as_user(monkeypatch, _user(db, uid="adm"))
    result = run(comm_router.list_event_types(None, db))
    assert "goal_complete" in result["event_types"]


# ── connections: admin gate + owner scope ────────────────────────────────────


def test_connections_require_admin(db, monkeypatch):
    _as_user(monkeypatch, _user(db, uid="mem", role=UserRole.MEMBER))
    result = run(comm_router.list_connections(None, db))
    assert isinstance(result, JSONResponse)
    assert result.status_code == 403


def test_create_and_list_connection_is_owner_scoped(db, monkeypatch):
    admin1 = _user(db, uid="a1")
    admin2 = _user(db, uid="a2")

    _as_user(monkeypatch, admin1)
    created = run(
        comm_router.create_connection(
            ConnectionCreate(name="MM", server_url="https://mm.test", bot_token="t"),
            None,
            db,
        )
    )
    assert created.has_token is True
    assert not hasattr(created, "bot_token")

    listed = run(comm_router.list_connections(None, db))
    assert [c.id for c in listed] == [created.id]

    # A different admin sees none of admin1's connections.
    _as_user(monkeypatch, admin2)
    assert run(comm_router.list_connections(None, db)) == []


def test_update_delete_rejects_non_owner(db, monkeypatch):
    admin1 = _user(db, uid="a1")
    admin2 = _user(db, uid="a2")
    conn = _make_connection(db, admin1.id)

    _as_user(monkeypatch, admin2)
    upd = run(
        comm_router.update_connection(conn.id, ConnectionUpdate(name="x"), None, db)
    )
    assert isinstance(upd, JSONResponse) and upd.status_code == 403
    dele = run(comm_router.delete_connection(conn.id, None, db))
    assert isinstance(dele, JSONResponse) and dele.status_code == 403


def test_delete_connection_guarded_when_linked(db, monkeypatch):
    admin = _user(db, uid="adm")
    conn = _make_connection(db, admin.id)
    comm_repo.upsert_board_channel(
        db,
        board_id="B1",
        connection_id=conn.id,
        channel_id="C1",
        channel_name="ch",
        use_threads=True,
        event_allowlist=[],
        tag_mode="assignee",
        enabled=True,
    )

    _as_user(monkeypatch, admin)
    blocked = run(comm_router.delete_connection(conn.id, None, db))
    assert isinstance(blocked, JSONResponse) and blocked.status_code == 409

    # Unlink the board, then deletion succeeds.
    comm_repo.delete_board_channel(db, "B1")
    ok = run(comm_router.delete_connection(conn.id, None, db))
    assert ok == {"ok": True}


# ── board channel (owner) ────────────────────────────────────────────────────


def test_board_channel_put_filters_allowlist_and_roundtrips(db, monkeypatch):
    admin = _user(db, uid="adm")
    conn = _make_connection(db, admin.id)
    _as_user(monkeypatch, admin)
    _as_board_owner(monkeypatch, admin)

    dto = run(
        comm_router.put_board_channel(
            "B1",
            BoardChannelUpsert(
                connection_id=conn.id,
                channel_id="C1",
                channel_name="dev",
                event_allowlist=["goal_complete", "bogus_event", "answers_required"],
                tag_mode="assignee",
            ),
            None,
            db,
        )
    )
    # Unknown events are dropped; known ones kept.
    assert set(dto.event_allowlist) == {"goal_complete", "answers_required"}
    assert dto.connection_name == "MM"

    got = run(comm_router.get_board_channel("B1", None, db))
    assert got["channel"].channel_id == "C1"
    assert [c.id for c in got["available_connections"]] == [conn.id]

    assert run(comm_router.delete_board_channel("B1", None, db)) == {"ok": True}
    assert run(comm_router.get_board_channel("B1", None, db))["channel"] is None


def test_board_channel_put_missing_connection_is_404(db, monkeypatch):
    admin = _user(db, uid="adm")
    _as_user(monkeypatch, admin)
    _as_board_owner(monkeypatch, admin)
    res = run(
        comm_router.put_board_channel(
            "B1",
            BoardChannelUpsert(connection_id="nope", channel_id="C1"),
            None,
            db,
        )
    )
    assert isinstance(res, JSONResponse) and res.status_code == 404


def test_test_send_requires_channel_and_token(db, monkeypatch):
    admin = _user(db, uid="adm")
    _as_user(monkeypatch, admin)
    _as_board_owner(monkeypatch, admin)

    # No channel configured yet → 422.
    res = run(comm_router.test_board_channel("B1", None, db))
    assert isinstance(res, JSONResponse) and res.status_code == 422

    # Channel on a tokenless connection → 422.
    conn = _make_connection(db, admin.id, token=None)
    comm_repo.upsert_board_channel(
        db,
        board_id="B1",
        connection_id=conn.id,
        channel_id="C1",
        channel_name="ch",
        use_threads=True,
        event_allowlist=[],
        tag_mode="assignee",
        enabled=True,
    )
    res2 = run(comm_router.test_board_channel("B1", None, db))
    assert isinstance(res2, JSONResponse) and res2.status_code == 422


def test_test_send_invokes_service(db, monkeypatch):
    admin = _user(db, uid="adm")
    conn = _make_connection(db, admin.id)
    comm_repo.upsert_board_channel(
        db,
        board_id="B1",
        connection_id=conn.id,
        channel_id="C1",
        channel_name="ch",
        use_threads=True,
        event_allowlist=[],
        tag_mode="assignee",
        enabled=True,
    )
    captured = {}

    def fake_test_send(*, server_url, bot_token, channel_id, provider="mattermost", text=None):
        captured.update(server_url=server_url, channel_id=channel_id, provider=provider)
        return True, "msg-1", None

    monkeypatch.setattr(comm_service, "test_send", fake_test_send)
    _as_user(monkeypatch, admin)
    _as_board_owner(monkeypatch, admin)

    res = run(comm_router.test_board_channel("B1", None, db))
    assert res.ok is True
    assert res.provider_message_id == "msg-1"
    assert captured == {
        "server_url": "https://mm.test",
        "channel_id": "C1",
        "provider": "mattermost",
    }


# ── user mapping universe + auto-match ───────────────────────────────────────


def test_user_links_universe_and_auto_match(db, monkeypatch):
    admin = _user(db, uid="adm")
    member = _user(db, uid="mem", role=UserRole.MEMBER, email="mem@x.test")
    conn = _make_connection(db, admin.id)

    # Link a board (with one member) to the connection.
    board = AgentTeamBoard(id="B1", slug="b1", name="Board 1")
    db.add(board)
    db.add(AgentTeamBoardMember(board_id="B1", user_id=member.id, role="editor"))
    db.commit()
    comm_repo.upsert_board_channel(
        db,
        board_id="B1",
        connection_id=conn.id,
        channel_id="C1",
        channel_name="ch",
        use_threads=True,
        event_allowlist=[],
        tag_mode="assignee",
        enabled=True,
    )

    _as_user(monkeypatch, admin)
    links = run(comm_router.list_user_links(conn.id, None, db))
    assert [link.user_id for link in links] == [member.id]
    assert links[0].mm_username is None  # not mapped yet

    # Manual upsert.
    res = run(
        comm_router.upsert_user_link(
            conn.id, UserLinkUpsert(user_id=member.id, mm_username="bob"), None, db
        )
    )
    assert res == {"ok": True, "mm_username": "bob"}

    # Auto-match resolves remaining members by email via the provider.
    monkeypatch.setattr(
        MattermostProvider,
        "resolve_username",
        lambda self, *, server_url, bot_token, email: ("U9", "auto-" + email.split("@")[0]),
    )
    # The member already has a MANUAL link → auto-match must not overwrite it.
    matched = run(comm_router.auto_match_user_links(conn.id, None, db))
    assert matched == {"ok": True, "matched": 0}
    after = run(comm_router.list_user_links(conn.id, None, db))
    assert after[0].mm_username == "bob"


def test_auto_match_requires_token(db, monkeypatch):
    admin = _user(db, uid="adm")
    conn = _make_connection(db, admin.id, token=None)
    _as_user(monkeypatch, admin)
    res = run(comm_router.auto_match_user_links(conn.id, None, db))
    assert isinstance(res, JSONResponse) and res.status_code == 422
