"""Unit tests for the Task Journal (repository + best-effort runtime helper).

These exercise the append-only semantics (task-local ``seq``), value coercion,
filtering/pagination, DTO serialization, and that the runtime helper is truly
best-effort (a write failure never propagates).
"""

from __future__ import annotations

import pytest
from agent_team.features.board.models import AgentTeamJournalEntry
from agent_team.features.board.repositories import journal as journal_repo
from agent_team.features.board.runtime import task_journal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def session(monkeypatch):
    """In-memory SQLite session with only the journal table created.

    ``append_entry`` does not require the task row to exist (the FK is not
    enforced under SQLite's default pragma), so a bare table keeps these unit
    tests focused. ``core.database.base.SessionLocal`` is pointed at the same
    engine so :func:`task_journal.record` writes here too.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    AgentTeamJournalEntry.__table__.create(bind=engine, checkfirst=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    import core.database.base as core_db

    monkeypatch.setattr(core_db, "SessionLocal", factory)

    db = factory()
    try:
        yield db
    finally:
        db.close()


# ── repository: seq + persistence ────────────────────────────────────────────
def test_append_assigns_task_local_monotonic_seq(session):
    a = journal_repo.append_entry(session, task_id="T1", title="one")
    b = journal_repo.append_entry(session, task_id="T1", title="two")
    other = journal_repo.append_entry(session, task_id="T2", title="other")
    session.commit()
    assert (a.seq, b.seq) == (1, 2)
    # seq is per-task, not global.
    assert other.seq == 1


def test_append_persists_payload_and_parses_json(session):
    entry = journal_repo.append_entry(
        session,
        task_id="T1",
        actor_type="agent",
        actor_id="claude",
        phase="execution",
        type="verdict",
        title="Evaluator verdict: pass",
        body="all good",
        severity="info",
        refs={"attempt_id": "att1"},
        metadata={"score": 0.9},
    )
    session.commit()
    assert entry.refs() == {"attempt_id": "att1"}
    assert entry.meta() == {"score": 0.9}
    assert entry.actor_type == "agent"


# ── repository: value coercion ───────────────────────────────────────────────
def test_unknown_values_coerced_to_safe_defaults(session):
    entry = journal_repo.append_entry(
        session,
        task_id="T1",
        actor_type="martian",
        phase="nowhere",
        type="bogus",
        title="x",
        severity="catastrophic",
    )
    session.commit()
    assert entry.actor_type == "system"
    assert entry.phase == "system"
    assert entry.type == "note"
    assert entry.severity == "info"


def test_title_and_body_are_clamped(session):
    entry = journal_repo.append_entry(
        session, task_id="T1", title="a" * 500, body="b" * 20000
    )
    session.commit()
    assert len(entry.title) == 200
    assert len(entry.body) == 10000


# ── repository: listing + filters + pagination ───────────────────────────────
def test_list_orders_by_seq_and_filters(session):
    journal_repo.append_entry(session, task_id="T1", type="decision", phase="planning", title="d")
    journal_repo.append_entry(
        session, task_id="T1", type="verdict", phase="verification", title="v", severity="warning"
    )
    journal_repo.append_entry(session, task_id="T1", type="note", phase="system", title="n")
    session.commit()

    everything = journal_repo.list_entries(session, "T1")
    assert [e.seq for e in everything] == [1, 2, 3]

    only_verdict = journal_repo.list_entries(session, "T1", type="verdict")
    assert [e.title for e in only_verdict] == ["v"]

    warnings = journal_repo.list_entries(session, "T1", severity="warning")
    assert [e.seq for e in warnings] == [2]

    after_one = journal_repo.list_entries(session, "T1", after_seq=1)
    assert [e.seq for e in after_one] == [2, 3]

    before_three = journal_repo.list_entries(session, "T1", before_seq=3)
    assert [e.seq for e in before_three] == [1, 2]


def test_serialize_entry_shape(session):
    entry = journal_repo.append_entry(
        session, task_id="T1", title="hi", refs={"run_id": "r1"}, metadata={"k": 1}
    )
    session.commit()
    dto = journal_repo.serialize_entry(entry)
    assert dto.task_id == "T1"
    assert dto.title == "hi"
    assert dto.refs == {"run_id": "r1"}
    assert dto.metadata == {"k": 1}
    assert dto.created_at is not None


# ── runtime helper: best-effort ──────────────────────────────────────────────
def test_record_with_appends_on_given_session(session):
    task_journal.record_with(session, task_id="T1", title="planned", type="state_change")
    session.commit()
    rows = journal_repo.list_entries(session, "T1")
    assert [r.title for r in rows] == ["planned"]


def test_record_opens_its_own_session(session):
    # ``record`` uses ``core.database.base.SessionLocal`` (monkeypatched above).
    task_journal.record(task_id="T1", title="bg", type="note")
    rows = journal_repo.list_entries(session, "T1")
    assert [r.title for r in rows] == ["bg"]


def test_record_never_raises_on_failure(monkeypatch):
    # Force the lazy SessionLocal import to blow up; the helper must swallow it.
    import core.database.base as core_db

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(core_db, "SessionLocal", _boom)
    # Must not raise.
    task_journal.record(task_id="T1", title="should not crash")


def test_record_with_never_raises_on_failure():
    class _BrokenSession:
        def query(self, *a, **k):
            raise RuntimeError("broken")

    # Must not raise even though the session is unusable.
    task_journal.record_with(_BrokenSession(), task_id="T1", title="x")


# ── refs builder ─────────────────────────────────────────────────────────────
def test_refs_drops_empty_values():
    out = task_journal.refs(run_id="r1", attempt_id=None, artifacts=[], extra_key="v")
    assert out == {"run_id": "r1", "extra_key": "v"}


# ── agent note inbox (JSONL) ─────────────────────────────────────────────────
def _write_inbox(ws, lines):
    from agent_team.features.board.runtime.loop import planning_artifacts as A

    A.write_text(ws, A.JOURNAL_NOTES_PATH, "\n".join(lines))


def test_read_journal_notes_skips_malformed(tmp_path):
    from agent_team.features.board.runtime.loop import planning_artifacts as A

    ws = str(tmp_path)
    _write_inbox(
        ws,
        [
            '{"type": "decision", "title": "Chose Postgres", "body": "scales"}',
            "not json at all",
            '{"no_title": true}',
            '["array not object"]',
            '{"title": "  Bare note  "}',
            "",
        ],
    )
    rows = A.read_journal_notes(ws)
    assert [r["title"] for r in rows] == ["Chose Postgres", "Bare note"]
    assert rows[0]["type"] == "decision"
    assert rows[1]["type"] == "note"  # default


def test_archive_journal_notes_clears_inbox(tmp_path):
    from agent_team.features.board.runtime.loop import planning_artifacts as A

    ws = str(tmp_path)
    _write_inbox(ws, ['{"title": "x"}'])
    dest = A.archive_journal_notes(ws)
    assert dest is not None and dest.endswith(".jsonl")
    assert A.read_text(ws, A.JOURNAL_NOTES_PATH) is None  # inbox gone
    assert A.read_text(ws, dest)  # archived copy kept
    assert A.archive_journal_notes(ws) is None  # nothing left to archive


def test_ingest_agent_notes_appends_dedupes_and_archives(session, tmp_path, monkeypatch):
    # No board lookup in the bare test DB → keep secret resolution inert.
    monkeypatch.setattr(task_journal, "_collect_agent_secrets", lambda *a, **k: [])
    ws = str(tmp_path)
    _write_inbox(
        ws,
        [
            '{"type": "decision", "title": "Use SSE", "body": "stream tokens"}',
            '{"type": "decision", "title": "Use SSE", "body": "stream tokens"}',
            '{"type": "risk", "title": "Rate limit", "severity": "warning"}',
        ],
    )
    n = task_journal.ingest_agent_notes(
        task_id="T1", workspace_path=ws, actor_id="claude", phase="execution"
    )
    assert n == 2  # duplicate dropped
    rows = journal_repo.list_entries(session, "T1")
    assert [r.title for r in rows] == ["Use SSE", "Rate limit"]
    assert all(r.actor_type == "agent" and r.actor_id == "claude" for r in rows)
    assert rows[1].severity == "warning"
    # Inbox archived → a second ingest finds nothing.
    assert task_journal.ingest_agent_notes(task_id="T1", workspace_path=ws) == 0


def test_ingest_masks_agent_secrets(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        task_journal, "_collect_agent_secrets", lambda *a, **k: ["supersecrettoken"]
    )
    ws = str(tmp_path)
    _write_inbox(
        ws,
        ['{"title": "Configured API", "body": "used token supersecrettoken to auth"}'],
    )
    task_journal.ingest_agent_notes(task_id="T1", workspace_path=ws, actor_id="claude")
    row = journal_repo.list_entries(session, "T1")[0]
    assert "supersecrettoken" not in row.body


def test_ingest_empty_inbox_is_noop(session, tmp_path):
    assert task_journal.ingest_agent_notes(task_id="T1", workspace_path=str(tmp_path)) == 0


# ── journal file mirror (read-on-demand, full history) ───────────────────────
def test_write_journal_file_renders_full_timeline(session, tmp_path):
    from agent_team.features.board.runtime.loop import planning_artifacts as A

    journal_repo.append_entry(
        session, task_id="T1", type="decision", title="Use SSE", body="stream tokens"
    )
    journal_repo.append_entry(
        session, task_id="T1", type="state_change", title="Loop started"
    )
    session.commit()
    rel = task_journal.write_journal_file("T1", str(tmp_path))
    assert rel == A.JOURNAL_FILE_PATH
    md = A.read_text(str(tmp_path), A.JOURNAL_FILE_PATH)
    # The mirror keeps the FULL timeline (lifecycle included), newest last.
    assert "Use SSE" in md and "stream tokens" in md
    assert "Loop started" in md
    assert md.index("Use SSE") < md.index("Loop started")  # ascending seq


def test_write_journal_file_none_when_empty(session, tmp_path):
    from agent_team.features.board.runtime.loop import planning_artifacts as A

    assert task_journal.write_journal_file("T1", str(tmp_path)) is None
    assert A.read_text(str(tmp_path), A.JOURNAL_FILE_PATH) is None  # no file written


def test_write_journal_file_no_workspace_is_noop(session):
    assert task_journal.write_journal_file("T1", "") is None
