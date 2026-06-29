"""Unit tests for the Communication Gateway inbound slice (v2.0 foundation).

Covers, without needing a live Slack/Mattermost connection:

* the verified user-mapping resolver (the inbound authorization gate);
* inbound repositories (external threads, action requests, inbound messages);
* the provider-agnostic action executor (allowed-action gating, request status,
  board-role authorization, request resolution, inbound-message bookkeeping);
* the free-text → answers expansion; and
* the extracted ``human_actions`` service (approve / ack / answer), with the
  loop/artifacts/journal side effects monkeypatched.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_team.features.board.models import (
    AgentTeamBoard,
    AgentTeamBoardMember,
    AgentTeamTask,
)
from agent_team.features.board.runtime.loop import human_actions
from agent_team.features.comm import inbound
from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm.models import (
    ACTION_ANSWER_QUESTIONS,
    ACTION_APPROVE_PLAN,
    ACTION_CANCELLED,
    LINK_AUTO,
    LINK_MANUAL,
    AgentTeamBoardChannel,
    AgentTeamCommConnection,
    AgentTeamCommDelivery,
    AgentTeamCommUserLink,
    AgentTeamExternalThread,
    AgentTeamHumanActionRequest,
    AgentTeamInboundMessage,
)
from agent_team.features.comm.schemas import ConnectionCreate
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
        AgentTeamTask,
        AgentTeamCommConnection,
        AgentTeamBoardChannel,
        AgentTeamCommDelivery,
        AgentTeamCommUserLink,
        AgentTeamExternalThread,
        AgentTeamHumanActionRequest,
        AgentTeamInboundMessage,
    ):
        model.__table__.create(bind=engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _user(db, *, uid="u1", role=UserRole.MEMBER, email=None) -> User:
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


def _connection(db, owner_id="u1") -> AgentTeamCommConnection:
    return comm_repo.create_connection(
        db,
        owner_id=owner_id,
        payload=ConnectionCreate(name="MM", server_url="https://mm.test", bot_token="t"),
    )


def _board(db, *, bid="B1", owner_id=None) -> AgentTeamBoard:
    board = AgentTeamBoard(id=bid, slug=bid.lower(), name=bid, owner_id=owner_id)
    db.add(board)
    db.commit()
    return board


def _task(db, *, tid="T1", board_id="B1") -> AgentTeamTask:
    task = AgentTeamTask(
        id=tid,
        human_key=f"{board_id}-{tid}",
        board_id=board_id,
        title="Dark mode",
        workspace_path="/tmp/ws",
    )
    db.add(task)
    db.commit()
    return task


def _request(db, *, task, conn, thread="thr1", actions=None) -> AgentTeamHumanActionRequest:
    return comm_repo.create_action_request(
        db,
        task_id=task.id,
        board_id=task.board_id,
        connection_id=conn.id,
        channel_id="C1",
        provider_thread_id=thread,
        provider="mattermost",
        event_type="plan_approval_required",
        allowed_actions=actions or [ACTION_APPROVE_PLAN],
    )


# ── verified mapping resolver ────────────────────────────────────────────────


def test_resolve_internal_user_requires_verified(db):
    user = _user(db, uid="huy")
    conn = _connection(db, owner_id="huy")
    comm_repo.upsert_user_link(
        db,
        connection_id=conn.id,
        user_id=user.id,
        mm_user_id="MM123",
        mm_username="huy",
        source=LINK_MANUAL,
        verified=False,
    )
    assert (
        inbound.resolve_internal_user(db, connection_id=conn.id, provider_user_id="MM123")
        is None
    )

    comm_repo.upsert_user_link(
        db,
        connection_id=conn.id,
        user_id=user.id,
        mm_user_id="MM123",
        mm_username="huy",
        source=LINK_MANUAL,
        verified=True,
    )
    resolved = inbound.resolve_internal_user(
        db, connection_id=conn.id, provider_user_id="MM123"
    )
    assert resolved is not None and resolved.id == "huy"


def test_auto_links_are_verified_manual_are_not(db):
    user = _user(db, uid="huy")
    conn = _connection(db, owner_id="huy")
    auto = comm_repo.upsert_user_link(
        db,
        connection_id=conn.id,
        user_id=user.id,
        mm_user_id="A",
        mm_username="a",
        source=LINK_AUTO,
    )
    assert auto.verified is True
    manual = comm_repo.upsert_user_link(
        db,
        connection_id=conn.id,
        user_id=user.id,
        mm_user_id="A",
        mm_username="a",
        source=LINK_MANUAL,
    )
    assert manual.verified is False


# ── inbound repositories ─────────────────────────────────────────────────────


def test_external_thread_upsert_and_lookup(db):
    conn = _connection(db)
    row = comm_repo.upsert_external_thread(
        db,
        connection_id=conn.id,
        provider="mattermost",
        channel_id="C1",
        provider_thread_id="root-9",
        task_id=None,
        board_id="B1",
    )
    assert row.id
    got = comm_repo.get_external_thread(
        db, connection_id=conn.id, provider_thread_id="root-9"
    )
    assert got is not None and got.board_id == "B1"
    # Upsert is idempotent on (connection, thread).
    again = comm_repo.upsert_external_thread(
        db,
        connection_id=conn.id,
        provider="mattermost",
        channel_id="C1",
        provider_thread_id="root-9",
        task_id="T1",
        board_id="B1",
    )
    assert again.id == row.id and again.task_id == "T1"


def test_open_action_request_lookup_and_resolve(db):
    conn = _connection(db)
    task = _task(db, board_id=_board(db).id)
    req = _request(db, task=task, conn=conn, thread="thr1")
    found = comm_repo.get_open_action_request_for_thread(
        db, connection_id=conn.id, provider_thread_id="thr1"
    )
    assert found is not None and found.id == req.id

    comm_repo.resolve_action_request(db, req, user_id="huy", action=ACTION_APPROVE_PLAN)
    assert req.status == "resolved" and req.resolved_by == "huy"
    # Resolved requests are no longer returned as open.
    assert (
        comm_repo.get_open_action_request_for_thread(
            db, connection_id=conn.id, provider_thread_id="thr1"
        )
        is None
    )


# ── executor ─────────────────────────────────────────────────────────────────


def _patch_human_actions(monkeypatch, calls):
    monkeypatch.setattr(
        human_actions, "approve_plan", lambda db, task, user: calls.append(("approve", task.id))
    )
    monkeypatch.setattr(
        human_actions, "ack_loop", lambda db, task, user: calls.append(("ack", task.id))
    )

    def fake_answer(db, task, user, *, answers, note):
        calls.append(("answer", answers, note))
        return "planning"

    monkeypatch.setattr(human_actions, "answer_questions", fake_answer)


def test_execute_approve_plan_happy(db, monkeypatch):
    user = _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    req = _request(db, task=task, conn=conn)
    calls: list = []
    _patch_human_actions(monkeypatch, calls)

    result = inbound.execute_action(
        db, action_request=req, user=user, action=ACTION_APPROVE_PLAN
    )
    assert result.ok is True
    assert ("approve", task.id) in calls
    assert req.status == "resolved" and req.resolved_action == ACTION_APPROVE_PLAN


def test_execute_rejects_disallowed_action(db, monkeypatch):
    user = _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    req = _request(db, task=task, conn=conn, actions=[ACTION_APPROVE_PLAN])
    _patch_human_actions(monkeypatch, [])

    result = inbound.execute_action(
        db, action_request=req, user=user, action=ACTION_ANSWER_QUESTIONS
    )
    assert result.ok is False and "isn't allowed" in result.error
    assert req.status == "open"  # unchanged


def test_execute_rejects_closed_request(db, monkeypatch):
    user = _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    req = _request(db, task=task, conn=conn)
    req.status = ACTION_CANCELLED
    db.commit()
    _patch_human_actions(monkeypatch, [])

    result = inbound.execute_action(
        db, action_request=req, user=user, action=ACTION_APPROVE_PLAN
    )
    assert result.ok is False and "no longer open" in result.error


def test_execute_requires_editor_role(db, monkeypatch):
    # Board owned by someone else; acting user is only a viewer.
    _user(db, uid="owner")
    viewer = _user(db, uid="viewer")
    board = _board(db, owner_id="owner")
    db.add(AgentTeamBoardMember(board_id=board.id, user_id=viewer.id, role="viewer"))
    db.commit()
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="owner")
    req = _request(db, task=task, conn=conn)
    _patch_human_actions(monkeypatch, [])

    result = inbound.execute_action(
        db, action_request=req, user=viewer, action=ACTION_APPROVE_PLAN
    )
    assert result.ok is False and "permission" in result.error
    assert req.status == "open"


def test_answer_action_requires_text(db, monkeypatch):
    user = _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    req = _request(db, task=task, conn=conn, actions=[ACTION_ANSWER_QUESTIONS])
    _patch_human_actions(monkeypatch, [])

    result = inbound.execute_action(
        db, action_request=req, user=user, action=ACTION_ANSWER_QUESTIONS, text="   "
    )
    assert result.ok is False and "Reply with your answer" in result.error


def test_answer_freetext_maps_every_unanswered_question(db, monkeypatch):
    user = _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    req = _request(db, task=task, conn=conn, actions=[ACTION_ANSWER_QUESTIONS])

    captured: list = []

    def fake_answer(db, task, user, *, answers, note):
        captured.append((answers, note))
        return "execution"

    monkeypatch.setattr(human_actions, "answer_questions", fake_answer)
    # Two unanswered, one already answered → only the unanswered get the reply.
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    monkeypatch.setattr(
        artifacts,
        "read_questions",
        lambda ws: [
            {"id": "q1", "answer": ""},
            {"id": "q2", "answer": None},
            {"id": "q3", "answer": "already"},
        ],
    )

    result = inbound.execute_action(
        db,
        action_request=req,
        user=user,
        action=ACTION_ANSWER_QUESTIONS,
        text="light theme, header toggle, localStorage",
    )
    assert result.ok is True
    answers, note = captured[0]
    assert answers == {
        "q1": "light theme, header toggle, localStorage",
        "q2": "light theme, header toggle, localStorage",
    }
    assert note == "light theme, header toggle, localStorage"


# ── handle_thread_reply (end-to-end inbound entry point) ─────────────────────


def test_handle_reply_unverified_user_is_ignored(db, monkeypatch):
    _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    _request(db, task=task, conn=conn, thread="thr1")
    # No verified link for MM999.
    _patch_human_actions(monkeypatch, [])

    result = inbound.handle_thread_reply(
        db,
        connection_id=conn.id,
        provider="mattermost",
        provider_user_id="MM999",
        provider_thread_id="thr1",
        text="approve",
    )
    assert result.ok is False and "not linked" in result.error
    msg = db.query(AgentTeamInboundMessage).one()
    assert msg.status == "ignored"


def test_handle_reply_no_open_request_is_ignored(db, monkeypatch):
    conn = _connection(db)
    result = inbound.handle_thread_reply(
        db,
        connection_id=conn.id,
        provider="mattermost",
        provider_user_id="MM1",
        provider_thread_id="ghost",
        text="hi",
    )
    assert result.ok is False and "Nothing is pending" in result.error
    assert db.query(AgentTeamInboundMessage).one().status == "ignored"


def test_handle_reply_infers_single_action_and_resolves(db, monkeypatch):
    user = _user(db, uid="huy")
    board = _board(db, owner_id="huy")
    task = _task(db, board_id=board.id)
    conn = _connection(db, owner_id="huy")
    req = _request(db, task=task, conn=conn, thread="thr1", actions=[ACTION_APPROVE_PLAN])
    comm_repo.upsert_user_link(
        db, connection_id=conn.id, user_id=user.id, mm_user_id="MM1", mm_username="huy",
        source=LINK_AUTO,
    )
    calls: list = []
    _patch_human_actions(monkeypatch, calls)

    result = inbound.handle_thread_reply(
        db,
        connection_id=conn.id,
        provider="mattermost",
        provider_user_id="MM1",
        provider_thread_id="thr1",
        text="ok",
        provider_message_id="m9",
    )
    assert result.ok is True and result.action == ACTION_APPROVE_PLAN
    assert ("approve", task.id) in calls
    msg = db.query(AgentTeamInboundMessage).one()
    assert msg.status == "processed" and msg.action_request_id == req.id
    db.refresh(req)
    assert req.status == "resolved"


# ── extracted human_actions service (loop/artifacts/journal mocked) ──────────


def _stub_artifacts(monkeypatch, **overrides):
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    defaults = {
        "missing_required": lambda ws: [],
        "read_json": lambda ws, p: None,
        "validate_tasks": lambda data: [],
        "archive_change_request": lambda ws: None,
        "approved_etags": lambda ws: {},
        "answer_questions": lambda ws, answers: None,
        "open_questions": lambda ws: [],
        "read_questions": lambda ws: [{"id": "q1", "question": "Q1?", "answer": "yes"}],
        "archive_questions": lambda ws: None,
        "append_clarifications": lambda ws, answered, note: None,
        "SPEC_PATH": "SPEC.md",
        "PLAN_PATH": "PLAN.md",
        "TASKS_PATH": "TASKS.json",
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(artifacts, name, value)


def _stub_loop(monkeypatch, *, running=False):
    from agent_team.features.board.runtime import task_journal
    from agent_team.features.board.runtime.loop import planning as planning_mod
    from agent_team.features.board.runtime.loop import service as loop_service

    monkeypatch.setattr(loop_service, "is_loop_running", lambda task_id: running)
    monkeypatch.setattr(planning_mod, "start_planning_job", lambda **kw: None)
    monkeypatch.setattr(
        "agent_team.features.board.runtime.dispatch.capture_main_loop", lambda: None
    )
    journal_calls: list = []
    monkeypatch.setattr(
        task_journal, "record_with", lambda db, **kw: journal_calls.append(kw)
    )
    return journal_calls


def test_human_actions_approve_plan_parks_state(db, monkeypatch):
    from agent_team.features.board.runtime.loop.status import LoopState

    task = _task(db, board_id=_board(db).id)
    _stub_artifacts(monkeypatch)
    journal = _stub_loop(monkeypatch)
    user = SimpleNamespace(id="huy")

    human_actions.approve_plan(db, task, user)
    assert task.loop_state == LoopState.PLAN_APPROVED.value
    assert task.planning_meta().get("approved") is True
    assert any(c.get("type") == "approval" for c in journal)


def test_human_actions_approve_plan_rejects_missing_artifacts(db, monkeypatch):
    task = _task(db, board_id=_board(db).id)
    _stub_artifacts(monkeypatch, missing_required=lambda ws: ["SPEC.md"])
    _stub_loop(monkeypatch)
    with pytest.raises(human_actions.ActionError, match="missing artifacts"):
        human_actions.approve_plan(db, task, SimpleNamespace(id="huy"))


def test_human_actions_ack_loop_refuses_while_running(db, monkeypatch):
    task = _task(db, board_id=_board(db).id)
    task.loop_state = "complete"
    db.commit()
    _stub_loop(monkeypatch, running=True)
    with pytest.raises(human_actions.ActionError, match="still running"):
        human_actions.ack_loop(db, task, SimpleNamespace(id="huy"))
    # Not running → clears state.
    _stub_loop(monkeypatch, running=False)
    human_actions.ack_loop(db, task, SimpleNamespace(id="huy"))
    assert task.loop_state is None


def test_human_actions_answer_resumes_planning(db, monkeypatch):
    from agent_team.features.board.runtime.loop.status import LoopState

    task = _task(db, board_id=_board(db).id)
    task.loop_state = LoopState.WAITING_ANSWERS.value
    import json

    task.planning_meta_json = json.dumps({"planner_id": "planner-1"})
    db.commit()
    _stub_artifacts(monkeypatch)
    _stub_loop(monkeypatch)
    monkeypatch.setattr(
        "agent_team.features.board.runtime.loop.planning_prompts.build_answers_addendum",
        lambda answered, note: "ADDENDUM",
    )

    resumed = human_actions.answer_questions(
        db, task, SimpleNamespace(id="huy"), answers={"q1": "yes"}, note=None
    )
    assert resumed == "planning"
