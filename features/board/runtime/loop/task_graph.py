"""Task-graph execution: run an approved plan one task at a time.

Whole-objective execution (``run_loop``) drives a single generator against the
whole plan and grades the whole SPEC. Task-graph execution instead treats
``TASKS.json`` as an executable contract: it schedules tasks in dependency
order, runs a scoped generator+critic sub-loop for each, and marks the task
``complete`` on disk before moving on. The on-disk ``TASKS.json`` status is the
single source of truth for progress, so the cockpit (and a resumed run) always
see the same state.

The orchestrator reuses :func:`run_loop` for each task's sub-loop (sharing one
resource :class:`LoopLedger` so the budget spans the whole graph) and owns the
overall lifecycle state itself — the sub-loops publish no status. Dependencies
are injected (``run_generator`` / ``make_evaluator``) so the scheduling is
testable without live agents.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from agent_team.features.board.runtime import task_journal
from agent_team.features.board.runtime.loop import planning_artifacts as artifacts
from agent_team.features.board.runtime.loop import planning_prompts
from agent_team.features.board.runtime.loop.budget import LoopBudget, LoopLedger
from agent_team.features.board.runtime.loop.driver import (
    LoopOutcome,
    OnStatusFn,
    RunGeneratorFn,
    _close_attempt,
    _open_attempt,
    _record_evaluation,
    run_loop,
)
from agent_team.features.board.runtime.loop.evaluator import Evaluator
from agent_team.features.board.runtime.loop.status import LoopState, LoopStatus
from agent_team.features.board.runtime.loop.verdict import LoopVerdict

logger = logging.getLogger(__name__)

#: Builds the evaluator for one plan task (``None`` = grade the whole SPEC, used
#: for the final verification pass).
MakeEvaluatorFn = Callable[[dict | None], Evaluator]

#: Outcome when every planned task verified and (optionally) the final
#: whole-SPEC verification passed.
OUTCOME_COMPLETE = "complete"
#: Outcome when a task could not be verified within its attempt cap, a
#: dependency is blocked, or final verification failed — routes to a human.
OUTCOME_NEEDS_HUMAN = "needs_human"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_BUDGET = "budget"
OUTCOME_PLAN_CHANGE = "plan_change"
#: Outcome when the per-task generator raised blocking questions and paused for
#: a human to answer them.
OUTCOME_NEEDS_ANSWERS = "needs_answers"


async def run_task_graph(
    *,
    task_id: str,
    objective: str,
    workspace_path: str,
    run_generator: RunGeneratorFn,
    make_evaluator: MakeEvaluatorFn,
    max_attempts_per_task: int = 3,
    budget: LoopBudget | None = None,
    cancel: asyncio.Event | None = None,
    on_status: OnStatusFn | None = None,
    final_verify: bool = True,
    replan_requested: Callable[[], bool] | None = None,
    questions_pending: Callable[[], bool] | None = None,
    extra_preamble: str | None = None,
) -> LoopOutcome:
    """Execute ``TASKS.json`` task-by-task; return the terminal outcome.

    Scheduling is greedy over the document order: the next ``pending`` task whose
    dependencies are all satisfied runs first. Each task gets its own scoped
    sub-loop (capped at ``max_attempts_per_task``); a verified task is marked
    ``complete`` on disk, a task that exhausts its cap is marked ``blocked`` and
    the graph escalates to a human. The resource ``budget`` and ``cancel`` signal
    span the whole graph. When all tasks are complete an optional final
    verification grades the whole SPEC once.
    """
    ledger = LoopLedger(budget=budget or LoopBudget())

    def _completed() -> int:
        return sum(
            1
            for r in artifacts.task_list(workspace_path)
            if r["status"] in ("complete", "skipped")
        )

    def _publish(state: LoopState, *, outcome: str | None = None) -> None:
        if on_status is None:
            return
        try:
            on_status(
                LoopStatus(
                    task_id=task_id,
                    state=state,
                    attempt=_completed(),
                    max_attempts=len(artifacts.task_list(workspace_path)),
                    objective=objective,
                    outcome=outcome,
                    total_tokens=ledger.total_tokens,
                )
            )
        except Exception:  # noqa: BLE001 — status is best-effort, never fatal
            logger.debug("task-graph status emit failed", exc_info=True)

    # Graph-level terminal lines. plan_change/needs_answers are already journaled
    # by the per-task sub-loop, so they are omitted here to avoid duplicates.
    _graph_terminal = {
        OUTCOME_COMPLETE: ("state_change", "info", "Plan complete — all tasks verified"),
        OUTCOME_NEEDS_HUMAN: ("state_change", "warning", "Plan stopped — needs human"),
        OUTCOME_BUDGET: ("risk", "warning", "Plan stopped — resource budget exceeded"),
        OUTCOME_CANCELLED: ("state_change", "warning", "Plan execution cancelled"),
    }

    def _terminal(state: LoopState, outcome: str) -> LoopOutcome:
        entry = _graph_terminal.get(outcome)
        if entry is not None:
            jtype, severity, title = entry
            task_journal.record(
                task_id=task_id,
                phase="result",
                type=jtype,
                title=title,
                actor_type="system",
                severity=severity,
                metadata={"outcome": outcome, "completed": _completed()},
            )
        _publish(state, outcome=outcome)
        return LoopOutcome(outcome, _completed())

    # A prior run may have crashed mid-task, leaving an ``in_progress`` marker on
    # disk. Reset those to ``pending`` so the scheduler can pick them up again
    # instead of wedging behind a task nothing is driving.
    for r in artifacts.task_list(workspace_path):
        if r["status"] == "in_progress":
            await asyncio.to_thread(
                artifacts.set_task_status, workspace_path, r["id"], "pending"
            )

    _publish(LoopState.RUNNING)

    # On resume after a question pause, the human's answers ride along in the
    # per-task preamble so the shared generator thread proceeds with them.
    task_preamble = planning_prompts.TASK_GRAPH_PREAMBLE
    if extra_preamble and extra_preamble.strip():
        task_preamble = f"{task_preamble}\n\n{extra_preamble.strip()}"

    # Schedule and execute tasks until none remain runnable.
    while True:
        if cancel is not None and cancel.is_set():
            return _terminal(LoopState.CANCELLED, OUTCOME_CANCELLED)
        if ledger.exceeded() is not None:
            return _terminal(LoopState.WAITING_FOR_HUMAN, OUTCOME_BUDGET)

        rows = artifacts.task_list(workspace_path)
        nxt = artifacts.next_runnable_task(rows)
        if nxt is None:
            # Nothing runnable: either all done (→ final verification) or the
            # remaining pending tasks are wedged behind a blocked dependency.
            if any(r["status"] == "pending" for r in rows):
                logger.info("task-graph %s wedged: pending tasks have unmet deps", task_id)
                return _terminal(LoopState.WAITING_FOR_HUMAN, OUTCOME_NEEDS_HUMAN)
            break

        await asyncio.to_thread(
            artifacts.set_task_status, workspace_path, nxt["id"], "in_progress"
        )
        await asyncio.to_thread(
            task_journal.record,
            task_id=task_id,
            phase="execution",
            type="task_progress",
            title=f"Task {nxt['id']} started",
            body=str(nxt.get("title") or nxt.get("objective") or ""),
            actor_type="system",
            metadata={"task_key": nxt["id"]},
        )
        _publish(LoopState.RUNNING)

        outcome = await run_loop(
            task_id=task_id,
            objective=planning_prompts.build_task_objective(nxt),
            workspace_path=workspace_path,
            run_generator=run_generator,
            evaluator=make_evaluator(nxt),
            max_attempts=max_attempts_per_task,
            cancel=cancel,
            on_status=None,  # the orchestrator owns the overall lifecycle state
            plan_path=artifacts.PLAN_PATH,
            preamble=task_preamble,
            replan_requested=replan_requested,
            questions_pending=questions_pending,
            ledger=ledger,  # share the budget across every task
            journal_terminal=False,  # the graph journals task-level lines instead
        )

        if outcome.outcome == "complete":
            await asyncio.to_thread(
                artifacts.set_task_status, workspace_path, nxt["id"], "complete"
            )
            await asyncio.to_thread(
                task_journal.record,
                task_id=task_id,
                phase="execution",
                type="task_progress",
                title=f"Task {nxt['id']} complete",
                actor_type="system",
                metadata={"task_key": nxt["id"]},
            )
            _publish(LoopState.RUNNING)
            continue
        if outcome.outcome == "cancelled":
            return _terminal(LoopState.CANCELLED, OUTCOME_CANCELLED)
        if outcome.outcome == "plan_change":
            return _terminal(LoopState.PLAN_CHANGE_REQUESTED, OUTCOME_PLAN_CHANGE)
        if outcome.outcome == "needs_answers":
            # The task is left ``in_progress``: once the human answers, resuming
            # the graph re-runs this same task (now with the answers in context).
            return _terminal(LoopState.WAITING_ANSWERS, OUTCOME_NEEDS_ANSWERS)
        # capped / budget / needs_human — this task could not be verified.
        await asyncio.to_thread(
            artifacts.set_task_status, workspace_path, nxt["id"], "blocked"
        )
        await asyncio.to_thread(
            task_journal.record,
            task_id=task_id,
            phase="execution",
            type="task_progress",
            title=f"Task {nxt['id']} blocked — could not be verified",
            actor_type="system",
            severity="warning",
            metadata={"task_key": nxt["id"], "outcome": outcome.outcome},
        )
        return _terminal(LoopState.WAITING_FOR_HUMAN, OUTCOME_NEEDS_HUMAN)

    # Every task is complete. Optionally grade the whole SPEC once as a backstop
    # against tasks that each pass but do not add up to the objective.
    if final_verify:
        attempt_id = await asyncio.to_thread(_open_attempt, task_id)
        verdict = None
        try:
            verdict = await make_evaluator(None).evaluate(
                objective=objective,
                generator_summary="All planned tasks were completed and verified.",
                workspace_path=workspace_path,
                attempt_id=attempt_id,
            )
        except Exception:  # noqa: BLE001 — fail-open: a broken judge must not wedge
            logger.warning("task-graph final verify failed for %s", task_id, exc_info=True)
        if verdict is not None:
            await asyncio.to_thread(_record_evaluation, task_id, attempt_id, None, verdict)
        passed = verdict is not None and verdict.verdict == LoopVerdict.PASS
        await asyncio.to_thread(
            _close_attempt, attempt_id, "complete" if passed else "needs_human"
        )
        await asyncio.to_thread(
            task_journal.record,
            task_id=task_id,
            phase="verification",
            type="verdict",
            title=f"Final whole-SPEC verification: {'pass' if passed else 'fail'}",
            body=(verdict.missing if verdict is not None else ""),
            actor_type="agent",
            severity="info" if passed else "warning",
            refs=task_journal.refs(attempt_id=attempt_id),
        )
        if not passed:
            return _terminal(LoopState.WAITING_FOR_HUMAN, OUTCOME_NEEDS_HUMAN)

    return _terminal(LoopState.COMPLETE, OUTCOME_COMPLETE)
