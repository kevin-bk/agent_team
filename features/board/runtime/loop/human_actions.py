"""Human actions on a task's loop/planning, decoupled from the HTTP layer.

These functions hold the *post-authorization* logic for the cockpit's planning
endpoints (approve a plan, answer blocking questions, acknowledge a finished
loop). They take an already-resolved ``task`` and ``user`` so the same logic can
be driven from two callers without duplicating loop-resumption code:

* the REST router (``features/board/router.py``), after ``authz.guard_task``;
* the communication gateway's inbound executor (``features/comm/inbound.py``),
  after resolving a verified provider user and checking board role.

Validation/precondition failures raise :class:`ActionError`; the caller maps it
to its own error shape (``bad_request`` for HTTP, an ``ActionResult`` for chat).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session


class ActionError(Exception):
    """A human action could not be performed (failed a validation/precondition)."""


def approve_plan(
    db: Session,
    task: Any,
    user: Any,
    *,
    actor_type: str = "human",
    title: str = "Plan approved",
) -> None:
    """Validate the drafted artifacts and stamp approval metadata.

    Parks the task at ``plan_approved`` — it does **not** start execution (that
    stays a web-only ``approve_and_run``). Raises :class:`ActionError` if the
    artifacts are missing or invalid.

    ``actor_type``/``title`` let the lane-aware planning phase stamp a *system*
    approval for quick-lane plans (board opt-in) without pretending a human
    clicked the button — the journal keeps the two distinguishable.
    """
    from agent_team.features.board.runtime import task_journal
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
    from agent_team.features.board.runtime.loop.status import LoopState

    missing = artifacts.missing_required(task.workspace_path)
    if missing:
        raise ActionError(f"Cannot approve — missing artifacts: {', '.join(missing)}")
    tasks_data = artifacts.read_json(task.workspace_path, artifacts.TASKS_PATH)
    if tasks_data is not None:
        errors = artifacts.validate_tasks(tasks_data)
        if errors:
            raise ActionError("; ".join(errors))

    # Clear any active plan-change-request marker: approving (re)settles the plan,
    # so the gate that would otherwise immediately re-pause execution is removed.
    artifacts.archive_change_request(task.workspace_path)

    meta = task.planning_meta()
    meta.update(
        {
            "approved": True,
            "approved_by": getattr(user, "id", None),
            "approved_at": datetime.now(UTC).isoformat(),
            "artifact_etags": artifacts.approved_etags(task.workspace_path),
            "tasks_contract_etag": artifacts.tasks_contract_etag(
                task.workspace_path
            ),
            "last_error": None,
        }
    )
    task.planning_meta_json = json.dumps(meta, ensure_ascii=False)
    task.loop_state = LoopState.PLAN_APPROVED.value

    # Snapshot the approved contract before a later re-plan/new goal can
    # overwrite the shared workspace artifacts. Re-approving the same contract
    # in the same planning session safely reuses the existing row.
    from agent_team.features.board.runtime import goal_runs

    goal_run = goal_runs.ensure_approved_snapshot(
        db, task, approved_by=getattr(user, "id", None)
    )

    task_journal.record_with(
        db,
        task_id=task.id,
        phase="approval",
        type="approval",
        title=title,
        actor_id=getattr(user, "id", None),
        actor_type=actor_type,
        refs=task_journal.refs(
            artifacts=[artifacts.SPEC_PATH, artifacts.PLAN_PATH, artifacts.TASKS_PATH]
        ),
        metadata={
            "artifact_etags": meta.get("artifact_etags", {}),
            "goal_run_id": goal_run.id,
            "goal_run_no": goal_run.run_no,
        },
    )
    db.commit()


def ack_loop(db: Session, task: Any, user: Any) -> None:
    """Acknowledge a finished loop, clearing its state from the cockpit.

    Refuses (raises :class:`ActionError`) while a loop is still running.
    """
    from agent_team.features.board.runtime.loop.service import is_loop_running

    if is_loop_running(task.id):
        raise ActionError("The loop is still running — cancel it first.")
    task.loop_state = None
    db.commit()


def answer_questions(
    db: Session, task: Any, user: Any, *, answers: dict[str, str], note: str | None
) -> str:
    """Persist answers to an agent's blocking questions, then resume the phase.

    Returns ``"execution"`` or ``"planning"`` depending on which paused phase was
    resumed. Raises :class:`ActionError` if the task is not waiting for answers,
    a loop is still running, or not every blocking question was answered.
    """
    from agent_team.features.board.runtime import task_journal
    from agent_team.features.board.runtime.dispatch import capture_main_loop
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
    from agent_team.features.board.runtime.loop import planning_prompts
    from agent_team.features.board.runtime.loop.service import is_loop_running
    from agent_team.features.board.runtime.loop.status import LoopState

    if task.loop_state != LoopState.WAITING_ANSWERS.value:
        raise ActionError("This task is not waiting for answers.")
    if is_loop_running(task.id):
        raise ActionError("A loop is still running for this task.")

    artifacts.answer_questions(task.workspace_path, answers)
    still_open = artifacts.open_questions(task.workspace_path)
    if still_open:
        ids = ", ".join(q["id"] for q in still_open)
        raise ActionError(f"Please answer all blocking questions first: {ids}")

    answered = [q for q in artifacts.read_questions(task.workspace_path) if q["answer"]]
    addendum = planning_prompts.build_answers_addendum(answered, note)

    _answers_body = "\n".join(
        f"- {q.get('question', q['id'])} → {q['answer']}" for q in answered
    )
    if note:
        _answers_body = f"{_answers_body}\n\nNote: {note}".strip()
    task_journal.record_with(
        db,
        task_id=task.id,
        phase="change_request",
        type="answer",
        title=f"Human answered {len(answered)} question(s)",
        body=_answers_body,
        actor_id=getattr(user, "id", None),
        actor_type="human",
        metadata={"answers": answers, "note": note},
    )
    db.commit()

    meta = task.planning_meta()
    # Execution-phase answers only reach the generator's prompt, so also fold
    # them into SPEC.md as approved scope — otherwise the evaluator (which grades
    # against the SPEC) would never see the human's decisions. Re-planning
    # (planning phase) regenerates the SPEC, so it does not need this.
    if meta.get("approved") and meta.get("run_params"):
        artifacts.append_clarifications(task.workspace_path, answered, note)

    # Archive the answered questionnaire so the resumed phase does not re-pause.
    artifacts.archive_questions(task.workspace_path)

    capture_main_loop()
    if meta.get("approved") and meta.get("run_params"):
        # Execution-phase pause: resume the loop with the remembered parameters.
        from agent_team.features.board.runtime.loop.budget import LoopBudget
        from agent_team.features.board.runtime.loop.service import start_autonomous_loop

        rp = meta["run_params"]
        start_autonomous_loop(
            task_id=task.id,
            agent_alias=rp["agent_id"],
            evaluator_alias=rp["evaluator_id"],
            objective=task.objective or "",
            max_attempts=int(rp.get("max_attempts", 10)),
            budget=LoopBudget(
                max_tokens=rp.get("max_tokens"),
                max_cost_usd=rp.get("max_cost_usd"),
                max_wall_seconds=rp.get("max_wall_seconds"),
            ),
            strict=True,
            task_graph=bool(rp.get("task_graph", True)),
            resume_note=addendum,
        )
        return "execution"

    # Planning-phase pause: re-plan with the answers folded into the objective.
    objective = f"{task.objective or ''}\n\n{addendum}".strip()
    planner_id = meta.get("planner_id")
    if not planner_id:
        raise ActionError("No planner is set for this task; start planning first.")

    from agent_team.features.board.runtime.loop.planning import start_planning_job

    start_planning_job(
        task_id=task.id,
        planner_alias=planner_id,
        objective=objective,
        reviewer_alias=meta.get("reviewer_id") or None,
        # The planner needed a human decision, so this task is not trivial —
        # the human reviews the re-draft even on a quick-lane auto-approve board.
        allow_auto_approve=False,
    )
    return "planning"
