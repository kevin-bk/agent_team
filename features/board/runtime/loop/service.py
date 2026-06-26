"""Wire the loop layer to real runs: generator + evaluator backed by the backend.

This is the integration glue between the pure loop engine
(:mod:`...loop.driver`) and the existing run machinery: each generator/evaluator
turn is a real ``AgentTeamRun`` driven by the run backend, so it streams into the
event store and shows in the cockpit exactly like a chat run — only tagged with
its loop ``role`` and ``attempt_id``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import (
    RUN_ROLE_EVALUATOR,
    RUN_ROLE_GENERATOR,
    AgentTeamTask,
)
from agent_team.features.board.repositories import conversations as conversations_repo
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.repositories.tasks import get_task
from agent_team.features.board.runtime import registry
from agent_team.features.board.runtime.backend import get_run_backend
from agent_team.features.board.runtime.events import RUN_CANCELLED, RUN_DONE
from agent_team.features.board.runtime.loop.budget import LoopBudget
from agent_team.features.board.runtime.loop.driver import (
    GeneratorTurn,
    LoopOutcome,
    run_loop,
)
from agent_team.features.board.runtime.loop.evaluator import (
    Verdict,
    build_evaluator_prompt,
)
from agent_team.features.board.runtime.loop.status import LoopStatus
from agent_team.features.board.runtime.loop.verdict import parse_verdict
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


def _create_loop_run(
    *, task_id: str, agent_alias: str, prompt: str, role: str, attempt_id: str | None
) -> str:
    """Create a queued run tagged with its loop role + attempt (own session)."""
    db = SessionLocal()
    try:
        conv = conversations_repo.get_or_create_active_conversation(
            db, task_id=task_id, agent_alias=agent_alias
        )
        run = runs_repo.create_run(
            db,
            task_id=task_id,
            conversation=conv,
            agent_alias=agent_alias,
            trigger="loop",
            actor_id=None,
            prompt=prompt,
            role=role,
            attempt_id=attempt_id,
        )
        run_id = run.id
        db.commit()
        return run_id
    finally:
        db.close()


@dataclass
class _RunResult:
    status: str
    final_answer: str
    tokens: int
    cost_usd: float


def _read_run_result(run_id: str) -> _RunResult:
    """Return the terminal status, answer and resource use of a finished run."""
    db = SessionLocal()
    try:
        run = runs_repo.get_run(db, run_id)
        if run is None:
            return _RunResult(RUN_CANCELLED, "", 0, 0.0)
        return _RunResult(
            status=run.status,
            final_answer=run.final_answer or "",
            tokens=int(run.total_tokens or 0),
            cost_usd=float(run.cost_usd or 0.0),
        )
    finally:
        db.close()


async def _drive_to_completion(run_id: str) -> _RunResult:
    """Start a run on the backend and wait for it to reach a terminal state."""
    await get_run_backend().start(run_id)
    handle = registry.get(run_id)
    if handle is not None and handle.task is not None:
        try:
            await handle.task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — the backend already persisted the error
            logger.warning("loop run %s raised while driving", run_id, exc_info=True)
    return await asyncio.to_thread(_read_run_result, run_id)


class BackendGenerator:
    """Runs one generator turn as a real run on the task's agent thread."""

    def __init__(self, *, task_id: str, agent_alias: str) -> None:
        self._task_id = task_id
        self._agent_alias = agent_alias

    async def __call__(self, attempt_id: str, prompt: str) -> GeneratorTurn:
        run_id = await asyncio.to_thread(
            _create_loop_run,
            task_id=self._task_id,
            agent_alias=self._agent_alias,
            prompt=prompt,
            role=RUN_ROLE_GENERATOR,
            attempt_id=attempt_id,
        )
        result = await _drive_to_completion(run_id)
        return GeneratorTurn(
            run_id=run_id,
            final_text=result.final_answer,
            cancelled=result.status == RUN_CANCELLED,
            tokens=result.tokens,
            cost_usd=result.cost_usd,
        )


class WorkerEvaluator:
    """Grades an attempt by running a separate evaluator agent over the task."""

    def __init__(self, *, task_id: str, evaluator_alias: str) -> None:
        self._task_id = task_id
        self._evaluator_alias = evaluator_alias

    async def evaluate(
        self, *, objective: str, generator_summary: str, workspace_path: str
    ) -> Verdict | None:
        prompt = build_evaluator_prompt(objective, generator_summary)
        run_id = await asyncio.to_thread(
            _create_loop_run,
            task_id=self._task_id,
            agent_alias=self._evaluator_alias,
            prompt=prompt,
            role=RUN_ROLE_EVALUATOR,
            attempt_id=None,  # evaluator runs are graded out-of-band, not an attempt
        )
        result = await _drive_to_completion(run_id)
        if result.status != RUN_DONE:
            return None  # could not evaluate → fail-open
        return parse_verdict(result.final_answer)


def _task_workspace_and_board(task_id: str) -> tuple[str, str | None]:
    db = SessionLocal()
    try:
        task = get_task(db, task_id)
        if task is None:
            return "", None
        return task.workspace_path, task.board_id
    finally:
        db.close()


def _persist_loop_state(task_id: str, state: str) -> None:
    db = SessionLocal()
    try:
        db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).update(
            {"loop_state": state}
        )
        db.commit()
    finally:
        db.close()


def _make_status_sink(board_id: str | None):
    """Build an ``on_status`` callback: persist loop state + notify the board."""

    def _on_status(status: LoopStatus) -> None:
        _persist_loop_state(status.task_id, status.state.value)
        if board_id:
            get_board_bus().publish(
                board_id,
                {
                    "type": "loop.status",
                    "board_id": board_id,
                    "task_id": status.task_id,
                    "state": status.state.value,
                    "attempt": status.attempt,
                    "max_attempts": status.max_attempts,
                    "outcome": status.outcome,
                    "total_tokens": status.total_tokens,
                },
            )

    return _on_status


async def run_autonomous_loop(
    *,
    task_id: str,
    agent_alias: str,
    evaluator_alias: str,
    objective: str,
    max_attempts: int = 10,
    budget: LoopBudget | None = None,
    cancel: asyncio.Event | None = None,
) -> LoopOutcome:
    """Drive a task to a verified result using real generator + evaluator runs."""
    workspace_path, board_id = await asyncio.to_thread(
        _task_workspace_and_board, task_id
    )
    return await run_loop(
        task_id=task_id,
        objective=objective,
        workspace_path=workspace_path,
        run_generator=BackendGenerator(task_id=task_id, agent_alias=agent_alias),
        evaluator=WorkerEvaluator(task_id=task_id, evaluator_alias=evaluator_alias),
        max_attempts=max_attempts,
        budget=budget,
        cancel=cancel,
        on_status=_make_status_sink(board_id),
    )


def start_autonomous_loop(
    *,
    task_id: str,
    agent_alias: str,
    evaluator_alias: str,
    objective: str,
    max_attempts: int = 10,
    budget: LoopBudget | None = None,
) -> asyncio.Task:
    """Launch the loop as a background task on the running event loop."""

    async def _go() -> None:
        try:
            outcome = await run_autonomous_loop(
                task_id=task_id,
                agent_alias=agent_alias,
                evaluator_alias=evaluator_alias,
                objective=objective,
                max_attempts=max_attempts,
                budget=budget,
            )
            logger.info(
                "autonomous loop finished task=%s outcome=%s attempts=%s",
                task_id, outcome.outcome, outcome.attempts,
            )
        except Exception:  # noqa: BLE001 — never let the loop crash the event loop
            logger.exception("autonomous loop failed for task %s", task_id)

    return asyncio.create_task(_go())
