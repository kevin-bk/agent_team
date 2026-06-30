"""The loop driver: orchestrates attempts, evaluation and persistence (I/O).

``run_loop`` ties the pure :class:`LoopController` to real work: it opens an
attempt, runs a generator turn, evaluates it, persists the verdict, and asks the
controller whether to continue. Generator execution and evaluation are injected
(``run_generator`` / ``evaluator``) so the orchestration is testable without a
live agent and so the same loop works for any worker type.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_team.features.board.repositories import attempts as attempts_repo
from agent_team.features.board.runtime import task_journal
from agent_team.features.board.runtime.loop.budget import LoopBudget, LoopLedger
from agent_team.features.board.runtime.loop.controller import (
    DEFAULT_MAX_ZERO_STREAK,
    OUTCOME_STALLED,
    Done,
    LoopController,
)
from agent_team.features.board.runtime.loop.evaluator import Evaluator
from agent_team.features.board.runtime.loop.planner import Planner
from agent_team.features.board.runtime.loop.status import (
    LoopState,
    LoopStatus,
    outcome_to_state,
)
from agent_team.features.board.runtime.loop.verdict import (
    Verdict,
    format_evidence_digest,
)
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

#: Outcome recorded when the loop is stopped by a cancel signal.
OUTCOME_CANCELLED = "cancelled"
#: Outcome recorded when a resource guardrail (tokens/cost/runtime) hard-stops it.
OUTCOME_BUDGET = "budget"
#: Outcome recorded when the generator flagged the approved plan as wrong/unsafe
#: (it wrote the change-request marker) and the loop paused for a human to revise.
OUTCOME_PLAN_CHANGE = "plan_change"
#: Outcome recorded when the generator raised blocking questions (it wrote the
#: questions marker) and the loop paused for a human to answer them.
OUTCOME_NEEDS_ANSWERS = "needs_answers"

#: How a terminal outcome reads in the journal. ``plan_change``/``needs_answers``
#: get their own richer entries before the loop finishes, so they are omitted
#: here to avoid a duplicate generic line.
_TERMINAL_JOURNAL: dict[str, tuple[str, str, str]] = {
    "complete": ("state_change", "info", "Loop complete — objective verified"),
    "capped": ("state_change", "warning", "Loop stopped — attempt cap reached (needs human)"),
    OUTCOME_STALLED: (
        "state_change",
        "warning",
        "Loop stopped — no progress (stuck at 0%, needs human)",
    ),
    OUTCOME_BUDGET: ("risk", "warning", "Loop stopped — resource budget exceeded"),
    OUTCOME_CANCELLED: ("state_change", "warning", "Loop cancelled"),
}

#: Terminal outcomes that signal *process friction* — the loop could not reach a
#: verified completion within its limits. These surface on the board Friction
#: page so a human can fix the underlying blocker (missing tests, env, scope).
_FRICTION_OUTCOMES = frozenset({"capped", OUTCOME_STALLED, OUTCOME_BUDGET})


@dataclass
class GeneratorTurn:
    """Result of running one generator turn."""

    run_id: str | None
    final_text: str
    cancelled: bool
    #: The generator run ended in an error (e.g. a provider rate/credit limit) so
    #: there is no work to grade. Counts as zero progress for the stall guard.
    errored: bool = False
    #: Resource use of the turn, folded into the budget ledger.
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LoopOutcome:
    """Terminal result of a loop run."""

    outcome: str
    attempts: int


#: Runs one generator turn for ``(attempt_id, prompt)`` and returns its result.
RunGeneratorFn = Callable[[str, str], Awaitable[GeneratorTurn]]

#: Optional sink for live status snapshots (publish to a bus, persist state, …).
OnStatusFn = Callable[[LoopStatus], None]


def _open_attempt(task_id: str) -> str:
    db = SessionLocal()
    try:
        attempt = attempts_repo.open_attempt(db, task_id=task_id)
        attempt_id = attempt.id
        db.commit()
        return attempt_id
    finally:
        db.close()


def _close_attempt(attempt_id: str, outcome: str | None) -> None:
    db = SessionLocal()
    try:
        attempts_repo.close_attempt(db, attempt_id, outcome=outcome)
        db.commit()
    finally:
        db.close()


def _record_evaluation(
    task_id: str, attempt_id: str, run_id: str | None, verdict: Verdict
) -> None:
    db = SessionLocal()
    try:
        attempts_repo.record_evaluation(
            db,
            task_id=task_id,
            attempt_id=attempt_id,
            run_id=run_id,
            verdict=verdict.verdict.value,
            score=verdict.score,
            missing=verdict.missing,
            evidence_json=json.dumps(verdict.evidence, ensure_ascii=False),
        )
        db.commit()
    finally:
        db.close()


async def run_loop(
    *,
    task_id: str,
    objective: str,
    workspace_path: str,
    run_generator: RunGeneratorFn,
    evaluator: Evaluator,
    planner: Planner | None = None,
    max_attempts: int = 10,
    budget: LoopBudget | None = None,
    cancel: asyncio.Event | None = None,
    on_status: OnStatusFn | None = None,
    plan_path: str | None = None,
    preamble: str | None = None,
    replan_requested: Callable[[], bool] | None = None,
    questions_pending: Callable[[], bool] | None = None,
    ledger: LoopLedger | None = None,
    journal_terminal: bool = True,
    max_zero_streak: int = DEFAULT_MAX_ZERO_STREAK,
) -> LoopOutcome:
    """Drive a task to a verified result; returns the terminal outcome.

    When a ``planner`` is given, an optional planning phase runs first: it writes
    a structured plan to the workspace and the generator is then pointed at that
    file. Planning is fail-open — if it produces no plan, the loop proceeds from
    the raw objective.

    Each iteration opens an attempt, runs the generator, evaluates it (fail-open:
    an evaluator error or unparseable verdict is treated as "keep going"), and
    consults the controller. Two backstops always terminate the loop: the attempt
    cap (on the controller) and the resource budget (tokens/cost/runtime). A
    completed objective wins over a budget cap; a cap that stops *continuation*
    routes the task to human review.

    ``on_status`` (when given) receives a snapshot at the start, after each
    attempt, and at the terminal state — so a caller can publish progress and
    persist the task's loop state.
    """
    # A caller (e.g. the task-graph orchestrator running one sub-loop per task)
    # may pass a shared ledger so the resource budget spans every sub-loop.
    ledger = ledger if ledger is not None else LoopLedger(budget=budget or LoopBudget())

    def _publish(
        state: LoopState, *, attempt: int, outcome: str | None = None
    ) -> None:
        if on_status is None:
            return
        try:
            on_status(
                LoopStatus(
                    task_id=task_id,
                    state=state,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    objective=objective,
                    outcome=outcome,
                    total_tokens=ledger.total_tokens,
                )
            )
        except Exception:  # noqa: BLE001 — status is best-effort, never fatal
            logger.debug("loop status emit failed", exc_info=True)

    # Optional planning phase. Hand the plan to the generator by reference (the
    # opening prompt points at the file) rather than inlining it. A caller may
    # also pass ``plan_path`` directly (strict planning, where the plan was
    # approved before this loop started); the in-loop planner overrides it.
    if planner is not None:
        if cancel is not None and cancel.is_set():
            _publish(LoopState.CANCELLED, attempt=0, outcome=OUTCOME_CANCELLED)
            return LoopOutcome(OUTCOME_CANCELLED, 0)
        _publish(LoopState.PLANNING, attempt=0)
        try:
            plan_path = await planner.plan(
                objective=objective, workspace_path=workspace_path
            )
        except Exception:  # noqa: BLE001 — fail-open: a broken planner must not wedge
            logger.warning("loop planning failed for task %s", task_id, exc_info=True)
            plan_path = None

    controller = LoopController(
        objective,
        max_attempts=max_attempts,
        plan_path=plan_path,
        preamble=preamble,
        max_zero_streak=max_zero_streak,
    )
    prompt = controller.start()

    def _emit(state: LoopState, outcome: str | None = None) -> None:
        _publish(state, attempt=controller.attempts, outcome=outcome)

    #: The most recent verdict, kept so a terminal friction entry can carry the
    #: evaluator's evidence digest (what the last attempt could/could not prove).
    last_verdict: Verdict | None = None

    def _finish(attempt_id: str, outcome: str) -> LoopOutcome:
        _close_attempt(attempt_id, outcome)
        entry = _TERMINAL_JOURNAL.get(outcome) if journal_terminal else None
        if entry is not None:
            jtype, severity, title = entry
            task_journal.record(
                task_id=task_id,
                phase="result",
                type=jtype,
                title=title,
                actor_type="system",
                severity=severity,
                refs=task_journal.refs(attempt_id=attempt_id),
                metadata={"outcome": outcome, "attempts": controller.attempts},
            )
        if journal_terminal and outcome in _FRICTION_OUTCOMES:
            reason = {
                "capped": "attempt cap reached",
                OUTCOME_STALLED: "no progress for several attempts (stuck at 0%)",
                OUTCOME_BUDGET: "resource budget exceeded",
            }.get(outcome, "could not verify completion")
            body = (
                f"The loop stopped without a verified pass ({reason}). A human "
                "should check what blocked verification before re-running."
            )
            digest = ""
            if last_verdict is not None:
                try:
                    digest = format_evidence_digest(last_verdict.evidence)
                except Exception:  # noqa: BLE001 — digest is best-effort context
                    digest = ""
            if digest:
                body += f"\n\nLast evaluator evidence:\n{digest}"
            task_journal.record(
                task_id=task_id,
                phase="result",
                type="friction",
                title=f"Loop blocked without verified completion ({outcome})",
                body=body,
                actor_type="system",
                severity="warning",
                refs=task_journal.refs(attempt_id=attempt_id),
                metadata={"outcome": outcome, "attempts": controller.attempts},
            )
        _emit(outcome_to_state(outcome), outcome)
        return LoopOutcome(outcome, controller.attempts)

    _emit(LoopState.RUNNING)
    while True:
        if cancel is not None and cancel.is_set():
            _emit(LoopState.CANCELLED, OUTCOME_CANCELLED)
            return LoopOutcome(OUTCOME_CANCELLED, controller.attempts)

        attempt_id = await asyncio.to_thread(_open_attempt, task_id)
        turn = await run_generator(attempt_id, prompt)
        ledger.add(tokens=turn.tokens, cost_usd=turn.cost_usd)

        if turn.cancelled:
            return await asyncio.to_thread(_finish, attempt_id, OUTCOME_CANCELLED)

        # The generator can flag the approved plan as wrong/unsafe by writing the
        # change-request marker. Honour it before grading: pause the loop and
        # route the task back to a human to revise the plan rather than burning
        # more attempts against a plan the agent itself distrusts.
        if replan_requested is not None and await asyncio.to_thread(replan_requested):
            await asyncio.to_thread(
                task_journal.record,
                task_id=task_id,
                phase="change_request",
                type="plan_change",
                title="Generator requested a plan change",
                body="The generator flagged the approved plan as wrong/unsafe "
                "and wrote the change-request marker; the loop paused for a human.",
                actor_type="agent",
                severity="blocking",
                refs=task_journal.refs(run_id=turn.run_id, attempt_id=attempt_id),
            )
            return await asyncio.to_thread(_finish, attempt_id, OUTCOME_PLAN_CHANGE)

        # The generator can also pause itself by raising blocking questions
        # (writing the questions marker). Honour it before grading: park the loop
        # for a human to answer rather than evaluating a deliberately-incomplete
        # turn.
        if questions_pending is not None and await asyncio.to_thread(questions_pending):
            await asyncio.to_thread(
                task_journal.record,
                task_id=task_id,
                phase="execution",
                type="question",
                title="Generator raised blocking question(s)",
                body="The generator wrote the questions marker and paused for a "
                "human to answer before continuing.",
                actor_type="agent",
                severity="blocking",
                refs=task_journal.refs(run_id=turn.run_id, attempt_id=attempt_id),
            )
            return await asyncio.to_thread(_finish, attempt_id, OUTCOME_NEEDS_ANSWERS)

        # A generator run that errored (e.g. a provider rate/credit limit) left no
        # work to grade — skip the evaluator entirely and count it as a zero-
        # progress attempt, so the stall guard can stop the loop instead of
        # grading empty output to 0% over and over.
        verdict: Verdict | None = None
        if turn.errored:
            await asyncio.to_thread(
                task_journal.record,
                task_id=task_id,
                phase="execution",
                type="friction",
                title=f"Generator run failed (attempt {controller.attempts + 1})",
                body=(
                    "The generator run did not complete — often a provider "
                    "rate/credit limit or an infrastructure error. No output to "
                    "grade; counted as a zero-progress attempt."
                ),
                actor_type="system",
                severity="warning",
                refs=task_journal.refs(run_id=turn.run_id, attempt_id=attempt_id),
            )
        else:
            try:
                verdict = await evaluator.evaluate(
                    objective=objective,
                    generator_summary=turn.final_text,
                    workspace_path=workspace_path,
                    attempt_id=attempt_id,
                )
            except Exception:  # noqa: BLE001 — fail-open: a broken judge must not wedge
                logger.warning(
                    "loop evaluation failed for task %s", task_id, exc_info=True
                )
                verdict = None

        if verdict is not None:
            last_verdict = verdict
            # The evaluator turn is a real agent run (it executes tests/build);
            # fold its spend into the budget so the cap reflects total cost, not
            # just the generator's half.
            ledger.add(tokens=verdict.eval_tokens, cost_usd=verdict.eval_cost_usd)
            await asyncio.to_thread(
                _record_evaluation, task_id, attempt_id, turn.run_id, verdict
            )
            _v = verdict.verdict.value
            await asyncio.to_thread(
                task_journal.record,
                task_id=task_id,
                phase="verification",
                type="verdict",
                title=f"Evaluator verdict: {_v} (attempt {controller.attempts})",
                body=verdict.missing,
                actor_type="agent",
                severity="info" if _v == "pass" else "warning",
                refs=task_journal.refs(attempt_id=attempt_id),
                metadata={"verdict": _v, "score": verdict.score},
            )

        step = controller.on_attempt_finished(verdict, errored=turn.errored)
        if isinstance(step, Done):
            return await asyncio.to_thread(_finish, attempt_id, step.outcome)

        # The objective is not met yet — but a resource guardrail can still stop
        # continuation, routing the task to human review rather than burning more.
        if ledger.exceeded() is not None:
            return await asyncio.to_thread(_finish, attempt_id, OUTCOME_BUDGET)

        await asyncio.to_thread(_close_attempt, attempt_id, None)
        _emit(LoopState.RUNNING)
        prompt = step.followup
