"""In-process run backend: drives an agent and persists its stream as events.

The agent graph is built through ``graph_builder.build_graph`` so the run
inherits the agent's full capability set. Every frame the agent produces is
translated and appended to the event store (the source of truth), so the SSE
endpoint can replay and tail a run regardless of which process started it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import AgentTeamRun
from agent_team.features.board.repositories import activity as activity_repo
from agent_team.features.board.repositories.comments import list_comments
from agent_team.features.board.repositories.runs import get_run
from agent_team.features.board.repositories.tasks import get_task
from agent_team.features.board.runtime import event_store, registry
from agent_team.features.board.runtime import events as ev
from agent_team.features.board.runtime.context import (
    build_task_context,
    prepare_workspace,
)
from agent_team.features.board.runtime.events import (
    RUN_CANCELLED,
    RUN_DONE,
    RUN_ERROR,
    TERMINAL_RUN_STATUSES,
)
from agent_team.features.board.runtime.graph_builder import (
    build_graph,
    make_checkpointer,
)
from agent_team.features.board.runtime.registry import RunHandle
from agent_team.features.board.runtime.translator import (
    StreamTranslator,
    extract_usage,
)
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)

#: How often to poll the DB for a cross-process cancel while streaming.
_CANCEL_POLL_SECONDS = 2.0


class LocalRunBackend:
    """Runs agents as asyncio tasks in the current process."""

    async def start(self, run_id: str) -> None:
        handle = registry.register(run_id)
        handle.task = asyncio.create_task(self._drive(run_id, handle))

    async def cancel(self, run_id: str) -> bool:
        outcome = await asyncio.to_thread(event_store.request_cancel, run_id)
        handle = registry.get(run_id)
        if handle is not None:
            handle.cancel_event.set()
            if handle.task is not None:
                handle.task.cancel()
            return True
        return outcome != "noop"

    async def reconcile_orphans(self) -> int:
        return await asyncio.to_thread(reconcile_orphans_sync)

    async def _drive(self, run_id: str, handle: RunHandle) -> None:
        context = await asyncio.to_thread(_load_run_context, run_id)
        if context is None:
            logger.warning("agent_team run %s vanished before drive", run_id)
            registry.unregister(run_id)
            return

        agent_alias = context["agent_alias"]
        thread_id = context["thread_id"]
        input_text = context["input_text"]
        task_id = context["task_id"]
        board_id = context["board_id"]
        workspace_path = context["workspace_path"]
        actor_id = context["actor_id"]

        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        final_text = ""
        cancelled = False
        cp_ctx = None

        await asyncio.to_thread(event_store.mark_running, run_id)
        await asyncio.to_thread(
            event_store.append_event, run_id, *ev.run_start(agent_alias=agent_alias)
        )
        try:
            checkpointer, cp_ctx = await asyncio.to_thread(make_checkpointer, agent_alias)
            agent = await build_graph(
                agent_alias, checkpointer, workspace_path=workspace_path
            )
            translator = StreamTranslator()
            stream = agent.astream(
                {"messages": [{"role": "user", "content": input_text}]},
                {"configurable": {"thread_id": thread_id}},
                subgraphs=True,
                stream_mode=["updates", "custom"],
            )
            last_cancel_poll = 0.0
            try:
                async for raw_chunk in stream:
                    if handle.cancel_event.is_set():
                        cancelled = True
                        break
                    now = time.monotonic()
                    if now - last_cancel_poll >= _CANCEL_POLL_SECONDS:
                        last_cancel_poll = now
                        if await asyncio.to_thread(event_store.is_cancel_requested, run_id):
                            cancelled = True
                            break
                    for event_type, data in translator.translate(raw_chunk):
                        await asyncio.to_thread(
                            event_store.append_event, run_id, event_type, data
                        )
                    chunk_usage = extract_usage(raw_chunk)
                    usage["input_tokens"] += chunk_usage["input_tokens"]
                    usage["output_tokens"] += chunk_usage["output_tokens"]
                    usage["cache_read_tokens"] += chunk_usage["cache_read_tokens"]
            finally:
                try:
                    await stream.aclose()
                except Exception:
                    pass

            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            final_text = translator.final_text

            if cancelled:
                await self._finish_cancelled(run_id, thread_id, final_text, usage)
                await _log_run_finished(
                    task_id, actor_id, run_id, RUN_CANCELLED,
                    board_id=board_id, agent_alias=agent_alias,
                )
            else:
                await self._finish_done(run_id, final_text, usage)
                await _log_run_finished(
                    task_id, actor_id, run_id, RUN_DONE,
                    board_id=board_id, agent_alias=agent_alias,
                )

        except asyncio.CancelledError:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            await self._finish_cancelled(run_id, thread_id, final_text, usage)
            await _log_run_finished(
                task_id, actor_id, run_id, RUN_CANCELLED,
                board_id=board_id, agent_alias=agent_alias,
            )
        except Exception as exc:
            logger.error("agent_team run %s failed", run_id, exc_info=True)
            await asyncio.to_thread(
                event_store.append_event,
                run_id,
                *ev.error(error_class=type(exc).__name__, message=str(exc)),
            )
            await asyncio.to_thread(
                event_store.append_event, run_id, *ev.run_end(status=RUN_ERROR)
            )
            await asyncio.to_thread(
                event_store.finalize_run, run_id, status=RUN_ERROR, error=str(exc), usage=usage
            )
            await _log_run_finished(
                task_id, actor_id, run_id, RUN_ERROR,
                board_id=board_id, agent_alias=agent_alias,
            )
        finally:
            registry.unregister(run_id)
            if cp_ctx is not None:
                try:
                    cp_ctx.__exit__(None, None, None)
                except Exception:
                    pass

    async def _finish_done(self, run_id: str, final_text: str, usage: dict) -> None:
        if final_text:
            await asyncio.to_thread(
                event_store.append_event, run_id, *ev.final_answer(final_text)
            )
        await asyncio.to_thread(
            event_store.append_event,
            run_id,
            *ev.run_end(status=RUN_DONE, final_answer=final_text or None),
        )
        await asyncio.to_thread(
            event_store.finalize_run,
            run_id,
            status=RUN_DONE,
            final_answer=final_text or None,
            usage=usage,
        )

    async def _finish_cancelled(
        self, run_id: str, thread_id: str, final_text: str, usage: dict
    ) -> None:
        await asyncio.to_thread(
            event_store.append_event,
            run_id,
            *ev.run_end(status=RUN_CANCELLED, final_answer=final_text or None),
        )
        await asyncio.to_thread(
            event_store.finalize_run,
            run_id,
            status=RUN_CANCELLED,
            final_answer=final_text or None,
            usage=usage,
        )
        await _cancel_ai_coding(thread_id)


def _load_task_notes(db, task_id: str) -> list[dict]:
    """Return the task's notes as ``{author, body, attachments}`` dicts.

    Author display names are resolved in a single query (id → name) so the agent
    sees which user left each note. Soft-deleted notes are already excluded by
    ``list_comments``; people-only notes (``visible_to_agents=False``) are
    filtered here so they never reach the agent's context.
    """
    from core.database.models import User

    comments = [c for c in list_comments(db, task_id) if c.visible_to_agents]
    author_ids = {c.author_id for c in comments if c.author_id}
    names: dict[str, str] = {}
    if author_ids:
        for user in db.query(User).filter(User.id.in_(author_ids)).all():
            names[user.id] = user.full_name or user.username or user.email
    return [
        {
            # Fall back to the stored display name for non-user (e.g. Jira) authors.
            "author": (
                names.get(c.author_id) if c.author_id else c.external_author
            ),
            "created_at": (
                c.created_at.strftime("%Y-%m-%d %H:%M UTC") if c.created_at else None
            ),
            "body": c.body,
            "attachments": c.attachments(),
        }
        for c in comments
    ]


def _load_run_context(run_id: str) -> dict | None:
    """Load the run + task, ensure the workspace, and build the agent input."""
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return None
        task = get_task(db, run.task_id)
        if task is None:
            return None
        prepare_workspace(task)
        notes = _load_task_notes(db, run.task_id)
        return {
            "agent_alias": run.agent_alias,
            "thread_id": run.thread_id,
            "input_text": build_task_context(task, run.prompt, notes=notes),
            "task_id": run.task_id,
            "board_id": task.board_id,
            "workspace_path": task.workspace_path,
            "actor_id": run.actor_id,
        }
    finally:
        db.close()


def reconcile_orphans_sync() -> int:
    """Mark non-terminal runs (left over by a restart) as errored.

    The local backend keeps in-flight runs only in memory, so any run still
    ``queued``/``running`` after a restart can never make progress and is failed
    with a clear reason.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentTeamRun)
            .filter(AgentTeamRun.status.notin_(tuple(TERMINAL_RUN_STATUSES)))
            .all()
        )
        now = datetime.now(UTC)
        for run in rows:
            run.status = RUN_ERROR
            run.error = "Interrupted by restart"
            run.ended_at = now
        db.commit()
        return len(rows)
    finally:
        db.close()


async def _log_run_finished(
    task_id: str,
    actor_id: str | None,
    run_id: str,
    status: str,
    *,
    board_id: str | None = None,
    agent_alias: str | None = None,
) -> None:
    """Record a ``run_finished`` activity entry and notify the board (best-effort)."""
    await asyncio.to_thread(
        activity_repo.record_standalone,
        task_id=task_id,
        actor_id=actor_id,
        kind=activity_repo.RUN_FINISHED,
        data={"run_id": run_id, "status": status},
    )
    if board_id:
        get_board_bus().publish(
            board_id,
            {
                "type": "run.finished",
                "board_id": board_id,
                "task_id": task_id,
                "agent_id": agent_alias,
                "run_id": run_id,
                "status": status,
            },
        )


async def _cancel_ai_coding(thread_id: str) -> None:
    """Best-effort stop of any AI coding subprocess bound to this thread."""
    try:
        from plugins.ai_code.tools._acp_base import cancel_acp_sessions
    except ImportError:
        return
    try:
        await asyncio.to_thread(cancel_acp_sessions, thread_id)
    except Exception:
        logger.warning("ACP cancel failed thread_id=%s", thread_id, exc_info=True)
