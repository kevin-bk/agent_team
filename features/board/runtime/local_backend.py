"""In-process run backend: drives an agent and persists its stream as events.

The per-engine drive is delegated to an ``AgentWorker`` (a LangGraph agent or a
direct coding CLI), resolved from the agent alias. The backend only resolves the
worker, persists each frame it emits to the event store (the source of truth),
and applies the run's terminal status — so the SSE endpoint can replay and tail a
run regardless of which worker produced it or which process started it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime

from agent_team.features.board.board_events import get_board_bus
from agent_team.features.board.models import AgentTeamRun, AgentTeamTask
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
from agent_team.features.board.runtime.direct_acp import is_direct_cli_alias
from agent_team.features.board.runtime.events import (
    RUN_CANCELLED,
    RUN_DONE,
    RUN_ERROR,
    TERMINAL_RUN_STATUSES,
)
from agent_team.features.board.runtime.registry import RunHandle
from agent_team.features.board.runtime.workers import (
    TurnContext,
    WorkerRole,
    resolve_worker,
)
from core.database.base import SessionLocal

logger = logging.getLogger(__name__)


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
        #: Direct-CLI context-window gauge text (e.g. "45,000/200,000 tokens"),
        #: persisted so the cockpit can show it after the run ends.
        cli_usage_text: str | None = None

        await asyncio.to_thread(event_store.mark_running, run_id)
        await asyncio.to_thread(
            event_store.append_event, run_id, *ev.run_start(agent_alias=agent_alias)
        )

        async def emit(event_type: str, data: dict) -> None:
            """Persist one streamed frame (offloading large tool output)."""
            data = await _persist_tool_output(run_id, event_type, data)
            await asyncio.to_thread(
                event_store.append_event, run_id, event_type, data
            )

        # The worker owns the per-engine drive (graph vs direct CLI); the backend
        # only persists frames and applies the run's terminal status. ``usage`` is
        # accumulated in place on the context so partial totals survive a cancel.
        ctx = TurnContext(
            run_id=run_id,
            agent_alias=agent_alias,
            prompt=input_text,
            workspace_path=workspace_path,
            thread_id=thread_id,
            role=WorkerRole.CHAT,
            task_id=task_id,
            board_id=board_id,
            usage=usage,
            mcp_config=context.get("mcp_config"),
            secrets=context.get("secrets") or [],
            ephemeral_workspace=bool(context.get("ephemeral_workspace")),
        )
        try:
            worker = resolve_worker(agent_alias, WorkerRole.CHAT, board_id=board_id)
            result = await worker.run_turn(ctx, emit, handle.cancel_event)
            final_text = result.final_text
            cancelled = result.cancelled
            cli_usage_text = result.cli_usage_text
            result_error = getattr(result, "error", None)

            if result_error and not cancelled:
                await self._finish_error(run_id, result_error, usage, cli_usage_text)
                await asyncio.to_thread(
                    _finalize_turn_recovery_sync, run_id, workspace_path
                )
                await _log_run_finished(
                    task_id, actor_id, run_id, RUN_ERROR,
                    board_id=board_id, agent_alias=agent_alias,
                )
            elif cancelled:
                await self._finish_cancelled(
                    run_id, thread_id, final_text, usage, cli_usage_text
                )
                await asyncio.to_thread(
                    _finalize_turn_recovery_sync, run_id, workspace_path
                )
                await _log_run_finished(
                    task_id, actor_id, run_id, RUN_CANCELLED,
                    board_id=board_id, agent_alias=agent_alias,
                )
            else:
                await self._finish_done(run_id, final_text, usage, cli_usage_text)
                await _log_run_finished(
                    task_id, actor_id, run_id, RUN_DONE,
                    board_id=board_id, agent_alias=agent_alias,
                )

        except asyncio.CancelledError:
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
            await self._finish_cancelled(
                run_id, thread_id, final_text, usage, cli_usage_text
            )
            await asyncio.to_thread(
                _finalize_turn_recovery_sync, run_id, workspace_path
            )
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
            await asyncio.to_thread(
                _finalize_turn_recovery_sync, run_id, workspace_path
            )
            await _log_run_finished(
                task_id, actor_id, run_id, RUN_ERROR,
                board_id=board_id, agent_alias=agent_alias,
            )
        finally:
            registry.unregister(run_id)

    async def _finish_done(
        self,
        run_id: str,
        final_text: str,
        usage: dict,
        cli_usage_text: str | None = None,
    ) -> None:
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
            cli_usage_text=cli_usage_text,
        )

    async def _finish_cancelled(
        self,
        run_id: str,
        thread_id: str,
        final_text: str,
        usage: dict,
        cli_usage_text: str | None = None,
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
            cli_usage_text=cli_usage_text,
        )
        await _cancel_ai_coding(thread_id)

    async def _finish_error(
        self,
        run_id: str,
        error: str,
        usage: dict,
        cli_usage_text: str | None = None,
    ) -> None:
        """Finalize a worker-reported failure whose live error frame was emitted."""
        await asyncio.to_thread(
            event_store.append_event, run_id, *ev.run_end(status=RUN_ERROR)
        )
        await asyncio.to_thread(
            event_store.finalize_run,
            run_id,
            status=RUN_ERROR,
            error=error,
            usage=usage,
            cli_usage_text=cli_usage_text,
        )


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


def _agent_visible_workspace_path(task, agent_alias: str) -> str:
    """Return the workspace path that should be shown to the agent.

    The backend still uses ``task.workspace_path`` for host-side operations
    (mounting, diffing, writing artifacts). Direct CLI agents running inside
    OpenSandbox see that same workspace mounted at ``profile.workspace_mount_path``
    instead, so task-facing docs should speak in the agent's filesystem dialect.
    """
    if not is_direct_cli_alias(agent_alias):
        return task.workspace_path
    try:
        from agent_team.features.board.runtime.sandbox.service import resolve_profile

        profile = resolve_profile(task.id, task.board_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "agent_team: failed to resolve agent-visible workspace path",
            exc_info=True,
        )
        return task.workspace_path
    if profile.is_sandboxed:
        return profile.workspace_mount_path
    return task.workspace_path


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
        board = None
        try:
            from agent_team.features.board.repositories import boards as boards_repo

            board = boards_repo.get_board(db, task.board_id)
            skill_ids = board.skill_ids() if board is not None else []
            # The board's planning-harness skill must always be present in the
            # workspace (the strict planner prompt points at it), even when the
            # owner forgot to also tick it in the board's skill list.
            planning_skill = (
                (getattr(board, "planning_skill", "") or "").strip()
                if board is not None
                else ""
            ) or "project-harness"
            if planning_skill and planning_skill not in skill_ids:
                skill_ids = [*skill_ids, planning_skill]
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
        mcp_config: dict | None = None
        secrets: list[str] = []
        visible_workspace = _agent_visible_workspace_path(task, run.agent_alias)
        workspace_display_path = (
            visible_workspace if visible_workspace != task.workspace_path else None
        )
        direct_cli = is_direct_cli_alias(run.agent_alias)
        all_notes: list[dict] = []
        if direct_cli:
            # The CLI reads its context from files in the workspace
            # (``.agent-team/TASK.md`` via the CLAUDE.md / AGENTS.md / cursor-rule
            # pointers), refreshed with the full note history every turn. The
            # prompt gets a light nudge to read the brief on the first turn, or to
            # re-read it on a later turn when new notes arrived since last time
            # (``notes`` already holds just that delta).
            all_notes = _load_task_notes(db, run.task_id, since=None)
            cli_context.write_context_files(
                task.workspace_path,
                task,
                all_notes,
                repos,
                skills_manifest,
                workspace_display_path=workspace_display_path,
            )
            # Per-agent MCP config: this CLI alias may have its own MCP servers
            # configured on the board. The owned ACP engine forwards them to the
            # CLI; any auth/header/env values become secrets to mask in output.
            # Reviewer/evaluator get the same servers — verdicts are still
            # gated by backend trusted receipts, not by tool output.
            if board is not None:
                cfg = board.agent_mcp_for(run.agent_alias)
                if cfg.get("mcpServers"):
                    mcp_config = cfg
                    secrets = _collect_mcp_secrets(cfg)
        else:
            # LLM run: the agent gets context via the prompt, but a coding
            # sub-agent it spawns over ACP runs in this workspace — Codex reads
            # ``AGENTS.md``, so advertise the materialised skills there too.
            try:
                skills_rt.write_codex_manifest(task.workspace_path, skills_manifest)
            except Exception:
                logger.exception("agent_team: failed to write Codex manifest for %s", task.id)

        # Planner/generator runs are recoverable turns. Capture their baseline only
        # after repos, skills and generated context files are ready, so a later
        # delta contains agent work rather than setup performed by this backend.
        try:
            from agent_team.features.board.runtime import turn_recovery

            turn_recovery.prepare_run(
                db,
                run,
                workspace_path=task.workspace_path,
                repo_paths=[str(r["path"]) for r in repos if r.get("path")],
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "agent_team: failed to prepare turn checkpoint for run %s", run.id
            )
            # Recovery metadata is an availability aid, not permission to block
            # normal work. Reload after rollback and continue with the original
            # prompt if checkpoint preparation failed.
            run = get_run(db, run_id)
            if run is None:
                return None

        if direct_cli:
            input_text = cli_context.build_prompt(
                run.prompt or "",
                first_turn=full,
                has_new_notes=bool(notes),
                workspace_display_path=workspace_display_path,
            )
        else:
            input_text = build_task_context(
                task,
                run.prompt,
                notes=notes,
                full=full,
                include_description=include_description,
                repos=repos,
                workspace_display_path=workspace_display_path,
            )
        run_workspace = task.workspace_path
        if run.workspace_override_path:
            source = os.path.realpath(task.workspace_path)
            override = os.path.realpath(run.workspace_override_path)
            expected_prefix = f".agent-team-review-{task.id}-"
            if (
                os.path.dirname(override) != os.path.dirname(source)
                or not os.path.basename(override).startswith(expected_prefix)
            ):
                raise ValueError("invalid backend review workspace override")
            # symlinks=True: copy links as links. Dependency-seed links (e.g.
            # node_modules -> /opt/agent-team/project-deps/...) resolve only
            # inside the runtime image; following them on the host would fail.
            shutil.copytree(source, override, symlinks=True, dirs_exist_ok=True)
            # Reviewer truth arrives in its prompt/packet. Remove mutable
            # narrative surfaces copied from the builder workspace.
            for rel in (
                ".agent-team/JOURNAL.md",
                ".agent-team/JOURNAL_NOTES.jsonl",
            ):
                try:
                    os.remove(os.path.join(override, rel))
                except FileNotFoundError:
                    pass
            run_workspace = override
        return {
            "agent_alias": run.agent_alias,
            "thread_id": run.thread_id,
            "input_text": input_text,
            "task_id": run.task_id,
            "board_id": task.board_id,
            "workspace_path": run_workspace,
            # Task sandboxes are keyed by task_id and their mounts are fixed at
            # creation, so a per-run workspace override must run in its own
            # disposable sandbox instead of reusing (or poisoning) the task one.
            "ephemeral_workspace": bool(run.workspace_override_path),
            "actor_id": run.actor_id,
            "mcp_config": mcp_config,
            "secrets": secrets,
        }
    finally:
        db.close()


def _collect_mcp_secrets(mcp_config: dict) -> list[str]:
    """Gather sensitive string values from an MCP config to mask in output.

    Conservatively treats every ``headers``/``env`` value and any string
    ``auth`` (e.g. a bearer token) as a secret. Short values are skipped to
    avoid masking incidental substrings of normal output.
    """
    secrets: set[str] = set()
    servers = mcp_config.get("mcpServers")
    if not isinstance(servers, dict):
        return []

    def _add(value: object) -> None:
        if isinstance(value, str) and len(value.strip()) >= 6:
            secrets.add(value.strip())

    for server in servers.values():
        if not isinstance(server, dict):
            continue
        _add(server.get("auth"))
        for bucket in ("headers", "env"):
            values = server.get(bucket)
            if isinstance(values, dict):
                for v in values.values():
                    _add(v)
    return sorted(secrets)


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

        # Freeze deltas before any resumed phase resets task markers or refreshes
        # generated context files. Also catch a graceful process shutdown that
        # finalized a run as cancelled without a durable human cancel request.
        graceful_candidates = (
            db.query(AgentTeamRun)
            .filter(
                AgentTeamRun.status == RUN_CANCELLED,
                AgentTeamRun.cancel_requested.is_(False),
                AgentTeamRun.workspace_snapshot_json.isnot(None),
            )
            .all()
        )
        graceful_interruptions = [
            run
            for run in graceful_candidates
            if (
                db.query(AgentTeamRun.id)
                .filter(AgentTeamRun.recovery_source_run_id == run.id)
                .first()
            )
            is None
        ]
        recovery_rows = [
            *rows,
            *(run for run in graceful_interruptions if run.workspace_delta_json is None),
        ]
        if recovery_rows:
            from agent_team.features.board.runtime import turn_recovery

            for run in recovery_rows:
                task = db.get(AgentTeamTask, run.task_id)
                if task is not None:
                    turn_recovery.finalize_run(
                        db, run, workspace_path=task.workspace_path
                    )

        # The in-memory planning/loop driver disappeared with the process too.
        # Move only actively-running task states to FAILED so the existing human
        # resume/re-plan actions become available; never regress a task that had
        # already reached a parked or terminal state in a concurrent final write.
        (
            db.query(AgentTeamTask)
            .filter(AgentTeamTask.loop_state.in_(("planning", "running")))
            .update({"loop_state": "failed"}, synchronize_session=False)
        )
        db.commit()
        return len(rows)
    finally:
        db.close()


def _finalize_turn_recovery_sync(run_id: str, workspace_path: str) -> None:
    """Best-effort terminal checkpoint using the backend's configured DB factory."""
    db = SessionLocal()
    try:
        run = get_run(db, run_id)
        if run is None:
            return
        from agent_team.features.board.runtime import turn_recovery

        if turn_recovery.finalize_run(
            db, run, workspace_path=workspace_path
        ):
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("agent_team: failed to finalize turn checkpoint for run %s", run_id)
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
