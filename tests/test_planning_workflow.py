"""Unit tests for the strict planning workflow (pure parts, no DB/subprocess).

Covers the planning artifact helpers (path safety, etag, TASKS.json validation,
change-request archiving), the strict planner/evaluator prompts, the loop
controller's strict preamble, and the evidence→verdict mapping.
"""

from __future__ import annotations

import json

import pytest
from agent_team.features.board.runtime.loop import planning_artifacts as A
from agent_team.features.board.runtime.loop import planning_prompts as P
from agent_team.features.board.runtime.loop.controller import LoopController
from agent_team.features.board.runtime.loop.verdict import LoopVerdict


# ── artifact path safety ─────────────────────────────────────────────────────
def test_safe_abs_rejects_traversal_and_absolute(tmp_path):
    ws = str(tmp_path)
    with pytest.raises(A.ArtifactError):
        A.read_text(ws, "../escape.txt")
    with pytest.raises(A.ArtifactError):
        A.read_text(ws, "/etc/passwd")
    with pytest.raises(A.ArtifactError):
        A.read_text(ws, ".agent-team/../../x")


def test_write_read_etag_roundtrip(tmp_path):
    ws = str(tmp_path)
    etag = A.write_text(ws, A.SPEC_PATH, "# SPEC\n")
    assert etag.startswith("sha256:")
    assert A.read_text(ws, A.SPEC_PATH) == "# SPEC\n"
    assert A.etag("# SPEC\n") == etag
    assert A.exists(ws, A.SPEC_PATH)
    assert not A.exists(ws, A.PLAN_PATH)


def test_missing_required_tracks_spec_and_plan(tmp_path):
    ws = str(tmp_path)
    assert set(A.missing_required(ws)) == {A.SPEC_PATH, A.PLAN_PATH}
    A.write_text(ws, A.SPEC_PATH, "spec")
    assert A.missing_required(ws) == [A.PLAN_PATH]
    A.write_text(ws, A.PLAN_PATH, "plan")
    assert A.missing_required(ws) == []


def test_approved_etags_only_existing(tmp_path):
    ws = str(tmp_path)
    A.write_text(ws, A.SPEC_PATH, "spec")
    A.write_text(ws, A.PLAN_PATH, "plan")
    etags = A.approved_etags(ws)
    assert set(etags) == {"SPEC.md", "PLAN.md"}
    assert etags["SPEC.md"] == A.etag("spec")


# ── TASKS.json validation ────────────────────────────────────────────────────
def test_validate_tasks_accepts_well_formed():
    data = {
        "version": 1,
        "tasks": [
            {"id": "T1", "status": "pending", "depends_on": []},
            {"id": "T2", "status": "pending", "depends_on": ["T1"]},
        ],
    }
    assert A.validate_tasks(data) == []


def test_validate_tasks_flags_problems():
    dup = A.validate_tasks(
        {"version": 1, "tasks": [{"id": "T1"}, {"id": "T1"}]}
    )
    assert any("duplicate" in e for e in dup)

    unknown = A.validate_tasks(
        {"version": 1, "tasks": [{"id": "T1", "depends_on": ["TX"]}]}
    )
    assert any("unknown id" in e for e in unknown)

    cycle = A.validate_tasks(
        {
            "version": 1,
            "tasks": [
                {"id": "T1", "depends_on": ["T2"]},
                {"id": "T2", "depends_on": ["T1"]},
            ],
        }
    )
    assert any("cycle" in e for e in cycle)

    bad_status = A.validate_tasks(
        {"version": 1, "tasks": [{"id": "T1", "status": "weird"}]}
    )
    assert any("unknown status" in e for e in bad_status)

    bad_version = A.validate_tasks({"version": 2, "tasks": []})
    assert any("version" in e for e in bad_version)


# ── change-request archiving ─────────────────────────────────────────────────
def test_archive_change_request_clears_active_marker(tmp_path):
    ws = str(tmp_path)
    assert A.archive_change_request(ws) is None  # nothing to archive
    A.write_text(ws, A.PLAN_CHANGE_REQUEST_PATH, "# change\nbad assumption")
    dest = A.archive_change_request(ws)
    assert dest is not None and dest.startswith(A.ARCHIVE_DIR)
    # active marker is gone, the archive copy remains
    assert not A.exists(ws, A.PLAN_CHANGE_REQUEST_PATH)
    assert A.read_text(ws, dest)


# ── prompts ──────────────────────────────────────────────────────────────────
def test_planning_prompt_requires_artifacts_and_forbids_edits():
    prompt = P.build_planning_prompt("Add X", task_id="t1", workspace_path="/ws")
    assert A.SPEC_PATH in prompt
    assert A.PLAN_PATH in prompt
    assert A.TASKS_PATH in prompt
    assert "Do not implement" in prompt


def test_strict_evaluator_prompt_references_approved_contract():
    prompt = P.build_strict_evaluator_prompt(
        objective="obj", generator_summary="did stuff", verdict_path=A.EVIDENCE_PATH
    )
    assert A.SPEC_PATH in prompt
    assert A.PLAN_PATH in prompt
    assert A.EVIDENCE_PATH in prompt
    assert "git diff" in prompt


def test_generator_strict_preamble_mentions_change_request():
    assert A.PLAN_CHANGE_REQUEST_PATH in P.GENERATOR_STRICT_PREAMBLE
    assert A.SPEC_PATH in P.GENERATOR_STRICT_PREAMBLE


# ── controller strict preamble ───────────────────────────────────────────────
def test_controller_preamble_replaces_plan_ref_in_opening():
    c = LoopController("obj", plan_path=A.PLAN_PATH, preamble=P.GENERATOR_STRICT_PREAMBLE)
    opening = c.start()
    assert "approved plan" in opening
    assert A.SPEC_PATH in opening
    # The redundant "A detailed implementation plan has been written" line is
    # suppressed when a preamble is supplied.
    assert "has been written to" not in opening


def test_controller_followup_still_cites_plan():
    from agent_team.features.board.runtime.loop.verdict import Verdict

    c = LoopController("obj", plan_path=A.PLAN_PATH, preamble="x")
    c.start()
    step = c.on_attempt_finished(
        Verdict(verdict=LoopVerdict.FAIL, missing="finish Y")
    )
    assert A.PLAN_PATH in step.followup


# ── evidence → verdict mapping ───────────────────────────────────────────────
def test_verdict_from_evidence_maps_fields(tmp_path):
    from agent_team.features.board.runtime.loop.service import _verdict_from_evidence

    ws = str(tmp_path)
    A.write_text(
        ws,
        A.EVIDENCE_PATH,
        json.dumps(
            {
                "version": 1,
                "verdict": "fail",
                "score": 0.4,
                "missing": ["run tests", "fix lint"],
                "commands": [{"cmd": "pytest", "exit_code": 1}],
            }
        ),
    )
    v = _verdict_from_evidence(ws)
    assert v is not None
    assert v.verdict == LoopVerdict.FAIL
    assert v.score == 0.4
    assert "run tests" in v.missing and "fix lint" in v.missing
    assert v.evidence["commands"][0]["exit_code"] == 1


def test_verdict_from_evidence_none_when_absent(tmp_path):
    from agent_team.features.board.runtime.loop.service import _verdict_from_evidence

    assert _verdict_from_evidence(str(tmp_path)) is None


# ── task-graph scheduling helpers ────────────────────────────────────────────
def _write_tasks(ws, tasks):
    A.write_text(ws, A.TASKS_PATH, json.dumps({"version": 1, "tasks": tasks}))


def test_task_list_parses_and_normalises(tmp_path):
    ws = str(tmp_path)
    _write_tasks(
        ws,
        [
            {"id": "T1", "title": "First", "status": "pending", "acceptance": ["a"]},
            {"id": "T2", "depends_on": ["T1"]},  # missing title/status default
        ],
    )
    rows = A.task_list(ws)
    assert [r["id"] for r in rows] == ["T1", "T2"]
    assert rows[1]["title"] == "T2"  # falls back to id
    assert rows[1]["status"] == "pending"
    assert rows[1]["depends_on"] == ["T1"]
    assert rows[0]["acceptance"] == ["a"]
    assert A.task_list(str(tmp_path / "empty")) == []


def test_next_runnable_respects_dependencies(tmp_path):
    ws = str(tmp_path)
    _write_tasks(
        ws,
        [
            {"id": "T1", "status": "pending", "depends_on": []},
            {"id": "T2", "status": "pending", "depends_on": ["T1"]},
        ],
    )
    rows = A.task_list(ws)
    assert A.next_runnable_task(rows)["id"] == "T1"  # T2 blocked on T1

    A.set_task_status(ws, "T1", "complete")
    rows = A.task_list(ws)
    assert A.next_runnable_task(rows)["id"] == "T2"  # now unblocked

    A.set_task_status(ws, "T2", "complete")
    assert A.next_runnable_task(A.task_list(ws)) is None  # nothing left


def test_set_task_status_roundtrip_and_validation(tmp_path):
    ws = str(tmp_path)
    _write_tasks(ws, [{"id": "T1", "status": "pending"}])
    assert A.set_task_status(ws, "T1", "in_progress") is True
    assert A.task_list(ws)[0]["status"] == "in_progress"
    assert A.set_task_status(ws, "TX", "complete") is False  # unknown id
    with pytest.raises(A.ArtifactError):
        A.set_task_status(ws, "T1", "weird")


def test_skipped_dependency_does_not_wedge_graph(tmp_path):
    ws = str(tmp_path)
    _write_tasks(
        ws,
        [
            {"id": "T1", "status": "skipped", "depends_on": []},
            {"id": "T2", "status": "pending", "depends_on": ["T1"]},
        ],
    )
    assert A.next_runnable_task(A.task_list(ws))["id"] == "T2"


def test_build_task_prompts_scope_to_single_task():
    task = {
        "id": "T3",
        "title": "Wire the API",
        "objective": "Add endpoint",
        "files": ["api.py"],
        "acceptance": ["returns 200"],
        "validation": ["pytest test_api.py"],
    }
    obj = P.build_task_objective(task)
    assert "T3" in obj and "Wire the API" in obj
    assert "returns 200" in obj and "pytest test_api.py" in obj

    ev = P.build_task_evaluator_prompt(
        task=task, generator_summary="done", verdict_path=A.EVIDENCE_PATH
    )
    assert "T3" in ev and "returns 200" in ev
    assert A.EVIDENCE_PATH in ev
    assert A.PLAN_CHANGE_REQUEST_PATH in P.TASK_GRAPH_PREAMBLE


# ── plan-change-request outcome mapping ──────────────────────────────────────
def test_plan_change_outcome_maps_to_change_requested_state():
    from agent_team.features.board.runtime.loop.driver import OUTCOME_PLAN_CHANGE
    from agent_team.features.board.runtime.loop.status import (
        LoopState,
        outcome_to_state,
    )

    assert outcome_to_state(OUTCOME_PLAN_CHANGE) == LoopState.PLAN_CHANGE_REQUESTED
