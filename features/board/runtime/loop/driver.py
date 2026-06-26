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
from agent_team.features.board.runtime.loop.budget import LoopBudget, LoopLedger
from agent_team.features.board.runtime.loop.controller import Done, LoopController
from agent_team.features.board.runtime.loop.evaluator import Evaluator
from agent_team.features.board.runtime.loop.status import (
    LoopState,
    LoopStatus,
    outcome_to_state,
)
from agent_team.features.board.runtime.loop.verdict import Verdict
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

#: Outcome recorded when the loop is stopped by a cancel signal.
OUTCOME_CANCELLED = "cancelled"
#: Outcome recorded when a resource guardrail (tokens/cost/runtime) hard-stops it.
OUTCOME_BUDGET = "budget"


@dataclass
class GeneratorTurn:
    """Result of running one generator turn."""

    run_id: str | None
    final_text: str
    cancelled: bool
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
    max_attempts: int = 10,
    budget: LoopBudget | None = None,
    cancel: asyncio.Event | None = None,
    on_status: OnStatusFn | None = None,
) -> LoopOutcome:
    """Drive a task to a verified result; returns the terminal outcome.

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
    controller = LoopController(objective, max_attempts=max_attempts)
    ledger = LoopLedger(budget=budget or LoopBudget())
    prompt = controller.start()

    def _emit(state: LoopState, outcome: str | None = None) -> None:
        if on_status is None:
            return
        try:
            on_status(
                LoopStatus(
                    task_id=task_id,
                    state=state,
                    attempt=controller.attempts,
                    max_attempts=max_attempts,
                    objective=objective,
                    outcome=outcome,
                    total_tokens=ledger.total_tokens,
                )
            )
        except Exception:  # noqa: BLE001 — status is best-effort, never fatal
            logger.debug("loop status emit failed", exc_info=True)

    def _finish(attempt_id: str, outcome: str) -> LoopOutcome:
        _close_attempt(attempt_id, outcome)
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

        verdict: Verdict | None = None
        try:
            verdict = await evaluator.evaluate(
                objective=objective,
                generator_summary=turn.final_text,
                workspace_path=workspace_path,
            )
        except Exception:  # noqa: BLE001 — fail-open: a broken judge must not wedge
            logger.warning("loop evaluation failed for task %s", task_id, exc_info=True)
            verdict = None

        if verdict is not None:
            await asyncio.to_thread(
                _record_evaluation, task_id, attempt_id, turn.run_id, verdict
            )

        step = controller.on_attempt_finished(verdict)
        if isinstance(step, Done):
            return await asyncio.to_thread(_finish, attempt_id, step.outcome)

        # The objective is not met yet — but a resource guardrail can still stop
        # continuation, routing the task to human review rather than burning more.
        if ledger.exceeded() is not None:
            return await asyncio.to_thread(_finish, attempt_id, OUTCOME_BUDGET)

        await asyncio.to_thread(_close_attempt, attempt_id, None)
        _emit(LoopState.RUNNING)
        prompt = step.followup
