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
from agent_team.features.board.repositories import tool_outputs as tool_outputs_repo
from agent_team.features.board.repositories.comments import list_comments
from agent_team.features.board.repositories.runs import (
    get_run,
    list_runs_for_conversation,
)
from agent_team.features.board.repositories.tasks import get_task
from agent_team.features.board.runtime import cli_context, event_store, registry
from agent_team.features.board.runtime import events as ev
from agent_team.features.board.runtime.context import (
    build_task_context,
    prepare_workspace,
)
from agent_team.features.board.runtime.direct_acp import (
    DirectCliRun,
    engine_for_alias,
    is_direct_cli_alias,
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
        # Record the app's main loop so the autopilot ticker (a thread) can
        # dispatch runs onto it via ``run_coroutine_threadsafe``.
        from agent_team.features.board.runtime.dispatch import capture_main_loop

        capture_main_loop()
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

        await asyncio.to_thread(event_store.mark_running, run_id)
        await asyncio.to_thread(
            event_store.append_event, run_id, *ev.run_start(agent_alias=agent_alias)
        )
        try:
            # A ``cli:<engine>`` alias talks straight to a coding CLI over ACP;
            # any other alias is a regular agent driven through its graph.
            if is_direct_cli_alias(agent_alias):
                final_text, cancelled = await self._run_direct_cli(
                    run_id,
                    handle,
                    agent_alias=agent_alias,
                    prompt=input_text,
                    workspace_path=workspace_path,
                    thread_id=thread_id,
                )
            else:
                final_text, cancelled = await self._run_graph(
                    run_id,
                    handle,
                    agent_alias=agent_alias,
                    input_text=input_text,
                    workspace_path=workspace_path,
                    thread_id=thread_id,
                    usage=usage,
                )

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

    async def _run_graph(
        self,
        run_id: str,
        handle: RunHandle,
        *,
        agent_alias: str,
        input_text: str,
        workspace_path: str,
        thread_id: str,
        usage: dict,
    ) -> tuple[str, bool]:
        """Drive a regular agent through its graph; returns ``(final_text, cancelled)``.

        Builds the agent's full capability set, streams it token-by-token, and
        persists each translated frame. ``usage`` is accumulated in place. The
        checkpointer context is owned here so it is always released when the
        graph finishes, even on cancellation.
        """
        cancelled = False
        cp_ctx = None
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
                # ``messages`` streams the model output token-by-token (text +
                # thinking); ``updates`` carries the structured tool frames and
                # the final-answer snapshot; ``custom`` carries AI-coding
                # sub-agent live progress.
                stream_mode=["messages", "updates", "custom"],
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
                        data = await _persist_tool_output(run_id, event_type, data)
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
            return translator.final_text, cancelled
        finally:
            if cp_ctx is not None:
                try:
                    cp_ctx.__exit__(None, None, None)
                except Exception:
                    pass

    async def _run_direct_cli(
        self,
        run_id: str,
        handle: RunHandle,
        *,
        agent_alias: str,
        prompt: str,
        workspace_path: str,
        thread_id: str,
    ) -> tuple[str, bool]:
        """Drive a direct CLI conversation over ACP; returns ``(final_text, cancelled)``.

        Streams the coding agent's progress as ``AgentEvent`` frames and persists
        each one exactly like a graph run. Token usage stays zero — there is no
        LLM in this path. A cross-process cancel request flips the in-memory
        cancel event, which the ACP stream observes and acts on.
        """
        run = DirectCliRun(
            engine=engine_for_alias(agent_alias),
            prompt=prompt,
            cwd=workspace_path,
            thread_id=thread_id,
        )
        last_cancel_poll = 0.0
        async for event_type, data in run.stream_frames(handle.cancel_event):
            now = time.monotonic()
            if now - last_cancel_poll >= _CANCEL_POLL_SECONDS:
                last_cancel_poll = now
                if await asyncio.to_thread(event_store.is_cancel_requested, run_id):
                    handle.cancel_event.set()
            data = await _persist_tool_output(run_id, event_type, data)
            await asyncio.to_thread(event_store.append_event, run_id, event_type, data)
        cancelled = run.cancelled or handle.cancel_event.is_set()
        return run.final_text, cancelled

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


def _load_task_notes(db, task_id: str, *, since=None) -> list[dict]:
    """Return the task's notes as ``{author, body, attachments}`` dicts.

    Author display names are resolved in a single query (id → name) so the agent
    sees which user left each note. Soft-deleted notes are already excluded by
    ``list_comments``; people-only notes (``visible_to_agents=False``) are
    filtered here so they never reach the agent's context.

    When ``since`` is given, only notes created strictly after that time are
    returned — used on follow-up turns to send just the new notes (the earlier
    ones are already in the thread history).
    """
    from core.database.models import User

    comments = [c for c in list_comments(db, task_id) if c.visible_to_agents]
    if since is not None:
        comments = [c for c in comments if c.created_at and c.created_at > since]
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


def _previous_run(db, run: AgentTeamRun) -> AgentTeamRun | None:
    """The run immediately before ``run`` in the same conversation, if any.

    A conversation maps 1:1 to a checkpointer thread, so "no previous run" means
    this is the thread's first turn (or the thread was just reset). That is the
    boundary we use to decide full vs. delta context.
    """
    if not run.conversation_id:
        return None
    prev: AgentTeamRun | None = None
    for r in list_runs_for_conversation(db, run.conversation_id):
        if r.id == run.id:
            break
        prev = r
    return prev


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
        # Lazily materialise per-task working copies of the board's repos so the
        # agent can code in them. Failures here must not abort the run.
        repos: list[dict] = []
        try:
            from agent_team.features.repos.task_copy import prepare_task_repos

            repos = prepare_task_repos(db, task)
        except Exception:
            logger.exception("agent_team: failed to prepare task repos for %s", task.id)
        # First turn of the thread → full context; otherwise send only the delta
        # (new notes / changed description) so the prompt cache reuses the prior
        # prefix instead of re-billing the whole task context every turn.
        prior = _previous_run(db, run)
        full = prior is None
        since = prior.created_at if prior is not None else None
        notes = _load_task_notes(db, run.task_id, since=since)
        include_description = full or (
            since is not None
            and task.updated_at is not None
            and task.updated_at > since
        )
        # Materialise the board's skill packs into the workspace for EVERY run
        # (direct CLI, LLM agent, autopilot). Claude/Cursor discover the copied
        # ``.claude`` / ``.cursor`` skill dirs natively whether the engine runs
        # directly or is spawned by an LLM's ``claude_acp`` / ``cursor`` tool in
        # this workspace.
        from agent_team.features.board.runtime import skills as skills_rt

        skills_manifest: list[dict] = []
        try:
            from agent_team.features.board.repositories import boards as boards_repo

            board = boards_repo.get_board(db, task.board_id)
            skill_ids = board.skill_ids() if board is not None else []
            skills_manifest = skills_rt.materialize_skills(task.workspace_path, skill_ids)
        except Exception:
            logger.exception("agent_team: failed to materialise board skills for %s", task.id)
        # Board Wiki: when a checked-out repo is marked as the board's wiki,
        # advertise the bundled ``board-wiki`` skill pack so both LLM and direct
        # CLI agents know how to read it and contribute pages on their task
        # branch. Runs after skill materialisation (which clears the skill dirs)
        # so it is additive; the repos context block names which repo is the wiki.
        if any(r.get("is_wiki") for r in repos):
            try:
                from agent_team.features.board.wiki import service as wiki_rt

                wiki_row = wiki_rt.materialize_wiki_skill(task.workspace_path)
                if wiki_row:
                    skills_manifest = [*skills_manifest, wiki_row]
            except Exception:
                logger.exception(
                    "agent_team: failed to materialise board wiki skill for %s", task.id
                )
        if is_direct_cli_alias(run.agent_alias):
            # The CLI reads its context from files in the workspace
            # (``.agent-team/TASK.md`` via the CLAUDE.md / AGENTS.md / cursor-rule
            # pointers), refreshed with the full note history every turn. The
            # prompt gets a light nudge to read the brief on the first turn, or to
            # re-read it on a later turn when new notes arrived since last time
            # (``notes`` already holds just that delta).
            all_notes = _load_task_notes(db, run.task_id, since=None)
            cli_context.write_context_files(
                task.workspace_path, task, all_notes, repos, skills_manifest
            )
            input_text = cli_context.build_prompt(
                run.prompt or "", first_turn=full, has_new_notes=bool(notes)
            )
        else:
            # LLM run: the agent gets context via the prompt, but a coding
            # sub-agent it spawns over ACP runs in this workspace — Codex reads
            # ``AGENTS.md``, so advertise the materialised skills there too.
            try:
                skills_rt.write_codex_manifest(task.workspace_path, skills_manifest)
            except Exception:
                logger.exception("agent_team: failed to write Codex manifest for %s", task.id)
            input_text = build_task_context(
                task,
                run.prompt,
                notes=notes,
                full=full,
                include_description=include_description,
                repos=repos,
            )
        return {
            "agent_alias": run.agent_alias,
            "thread_id": run.thread_id,
            "input_text": input_text,
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
    # Move an autopilot-triggered task on completion (no-op for other runs).
    from agent_team.features.board.runtime import autopilot as autopilot_rt

    await asyncio.to_thread(autopilot_rt.on_run_finished, run_id, status)
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


async def _persist_tool_output(run_id: str, event_type: str, data: dict) -> dict:
    """Offload a tool's full output out of the streamed frame.

    For ``tool_use_end`` frames the full result (``output_full``) is saved to
    the tool-output store keyed by ``(run_id, tool_id)`` and dropped from the
    frame, so the event store / SSE keep only the light preview. ``run_id`` is
    stamped onto tool frames so the UI can fetch the full output on demand.
    """
    if event_type not in (ev.EVENT_TOOL_USE_START, ev.EVENT_TOOL_USE_END):
        return data
    data = {**data, "run_id": run_id}
    if event_type == ev.EVENT_TOOL_USE_END:
        full = data.pop("output_full", None)
        if full:
            await asyncio.to_thread(
                tool_outputs_repo.save_tool_output,
                run_id,
                str(data.get("tool_id") or ""),
                str(full),
                is_error=bool(data.get("is_error")),
            )
    return data


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
