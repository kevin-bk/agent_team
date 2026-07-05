"""Admin overview of every sandbox: manage + analytics for the Sandboxes page.

Merges three sources into one list keyed by sandbox id:

* **manager registry** (RAM) — sandboxes this process actively tracks
  (state, idle time, live metrics for open ones);
* **task rows** (DB) — persisted ``task.sandbox_id`` links a sandbox to its
  task/board even when the process no longer tracks it;
* **OpenSandbox server** — the ground truth. Anything the server holds that
  neither RAM nor DB references is an **orphan** (safe to kill early instead of
  waiting out its TTL).

Everything is best-effort: a missing SDK, dead server, or DB hiccup degrades to
whatever sources still answer (with an ``errors`` note), never raises out.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_team.features.board.runtime.sandbox import service as sandbox_service
from agent_team.features.board.runtime.sandbox.config import RuntimeProfile

logger = logging.getLogger(__name__)

#: Server states that mean "gone" — listed for completeness but not actionable.
_DEAD_SERVER_STATES = {"TERMINATED", "FAILED", "DELETED"}


def _tracked_rows() -> dict[str, dict[str, Any]]:
    """Manager registry keyed by sandbox id (skips sandboxes not yet open)."""
    manager = sandbox_service._manager  # noqa: SLF001 — same package, read-only view
    if manager is None:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for rec in manager.snapshot():
        sid = rec.get("sandbox_id")
        if sid:
            rows[str(sid)] = rec
    return rows


def _task_rows() -> dict[str, dict[str, Any]]:
    """Persisted ``sandbox_id`` → task/board info from the DB (best-effort)."""
    try:
        from agent_team.features.board.models import AgentTeamBoard, AgentTeamTask
        from core.database.base import SessionLocal

        with SessionLocal() as db:
            pairs = (
                db.query(AgentTeamTask, AgentTeamBoard)
                .join(AgentTeamBoard, AgentTeamTask.board_id == AgentTeamBoard.id)
                .filter(AgentTeamTask.sandbox_id.isnot(None))
                .all()
            )
            return {
                str(task.sandbox_id): {
                    "task_id": task.id,
                    "task_key": task.human_key,
                    "task_title": task.title,
                    "board_id": board.id,
                    "board_slug": board.slug,
                    "board_name": getattr(board, "name", None)
                    or getattr(board, "title", None),
                }
                for task, board in pairs
            }
    except Exception:  # noqa: BLE001 — DB down ⇒ no task labels, page still works
        logger.debug("sandbox admin: task rows load failed", exc_info=True)
        return {}


async def _server_rows(profile: RuntimeProfile) -> tuple[list[dict[str, Any]], str | None]:
    """All sandboxes the OpenSandbox server knows; ``(rows, error_message)``."""
    if profile.provider != "opensandbox":
        return [], None
    try:
        import os

        from agent_team.features.board.runtime.sandbox.opensandbox import (
            list_server_sandboxes,
        )

        api_key = (
            os.environ.get(profile.api_key_env) if profile.api_key_env else None
        )
        rows = await list_server_sandboxes(
            server_url=profile.server_url or "http://localhost:8090",
            api_key=api_key,
        )
        return rows, None
    except Exception as e:  # noqa: BLE001 — server down ⇒ tracked view only
        logger.warning("sandbox admin: server list failed: %s", e)
        return [], str(e)


async def list_sandboxes_overview() -> dict[str, Any]:
    """The admin Sandboxes page payload: merged rows + aggregate cards."""
    profile = sandbox_service.resolve_profile()
    tracked = _tracked_rows()
    tasks = _task_rows()
    server_rows, server_error = await _server_rows(profile)

    merged: dict[str, dict[str, Any]] = {}

    for row in server_rows:
        sid = row["sandbox_id"]
        merged[sid] = {**row, "source": "server"}

    # Overlay manager bookkeeping (state per this process, idle time, metrics).
    for sid, rec in tracked.items():
        base = merged.setdefault(sid, {"sandbox_id": sid, "source": "tracked"})
        base["source"] = "tracked"
        base["state"] = rec["state"]
        base["idle_seconds"] = round(rec["idle_seconds"])
        base["name"] = rec["name"]
        base.setdefault("image", rec["image"])
        base["task_id"] = rec["task_id"]
        sb = rec["sandbox"]
        get_metrics = getattr(sb, "get_metrics", None)
        if get_metrics is not None and rec["state"] == "open":
            try:
                base["metrics"] = await get_metrics()
            except Exception:  # noqa: BLE001
                base["metrics"] = None

    # Overlay task/board labels from the persisted link.
    for sid, info in tasks.items():
        base = merged.get(sid)
        if base is None:
            # Persisted id the server doesn't know: stale link (will self-heal
            # on the task's next run); still show it so admins aren't surprised.
            base = merged[sid] = {"sandbox_id": sid, "source": "stale_link"}
        elif base["source"] == "server":
            base["source"] = "persisted"
        base.update(info)

    rows = []
    counts = {"running": 0, "paused": 0, "orphan": 0, "other": 0}
    for base in merged.values():
        server_state = str(base.get("server_state") or "").upper()
        state = base.get("state")
        local_ui = (
            {"open": "running", "opening": "running", "paused": "paused"}.get(str(state))
            if state
            else None
        )
        server_ui = {
            "RUNNING": "running",
            "READY": "running",
            "PAUSED": "paused",
            "PENDING": "running",
        }.get(server_state)
        # The server is the ground truth: when this process's record disagrees
        # (e.g. a stale "paused" record while the container actually runs), show
        # the server's view — the local record self-heals on the task's next
        # prepare. The local view only fills in when the server didn't answer.
        ui_state = server_ui or local_ui
        if local_ui is not None and server_ui is not None and local_ui != server_ui:
            base["state_mismatch"] = True
        is_dead = server_state in _DEAD_SERVER_STATES and base.get("source") == "server"
        orphan = base.get("source") == "server" and not is_dead
        if orphan:
            base["source"] = "orphan"
            counts["orphan"] += 1
        elif ui_state == "running":
            counts["running"] += 1
        elif ui_state == "paused":
            counts["paused"] += 1
        else:
            counts["other"] += 1
        base["ui_state"] = ui_state
        rows.append(base)

    rows.sort(key=lambda r: (r.get("created_at") or "", r["sandbox_id"]), reverse=True)

    manager = sandbox_service._manager  # noqa: SLF001
    return {
        "provider": profile.provider,
        "server_url": profile.server_url if profile.provider == "opensandbox" else None,
        "counts": counts,
        "total": len(rows),
        "tracked": len(tracked),
        "max_concurrent": manager.max_concurrent if manager else 0,
        "idle_ttl_seconds": manager.idle_ttl_seconds if manager else 0,
        "server_error": server_error,
        "sandboxes": rows,
    }


async def sandbox_admin_action(sandbox_id: str, action: str) -> dict[str, Any]:
    """Pause/kill one sandbox by id — routed to the task path when tracked.

    Tracked sandboxes go through ``pause_task_sandbox``/``kill_task_sandbox`` so
    the manager bookkeeping and the persisted id stay consistent; anything else
    (orphans) is hit directly on the server.
    """
    if action not in ("pause", "kill"):
        return {"ok": False, "error": f"unknown action {action!r}"}

    tracked = _tracked_rows()
    rec = tracked.get(sandbox_id)
    if rec is not None:
        task_id = str(rec["task_id"])
        if action == "pause":
            await sandbox_service.pause_task_sandbox(task_id)
        else:
            await sandbox_service.kill_task_sandbox(task_id)
        return {"ok": True, "routed": "task", "task_id": task_id}

    profile = sandbox_service.resolve_profile()
    if profile.provider != "opensandbox":
        return {"ok": False, "error": "sandbox is not tracked and provider is local"}

    server_error: str | None = None
    try:
        import os

        from agent_team.features.board.runtime.sandbox.opensandbox import (
            server_sandbox_action,
        )

        api_key = (
            os.environ.get(profile.api_key_env) if profile.api_key_env else None
        )
        await server_sandbox_action(
            sandbox_id,
            action,
            server_url=profile.server_url or "http://localhost:8090",
            api_key=api_key,
        )
    except Exception as e:  # noqa: BLE001
        server_error = str(e)

    # If a task row still points at this sandbox, clear the link so the task's
    # next run doesn't try to reattach a corpse. Done regardless of whether the
    # server still had the sandbox — killing a **stale link** (server already
    # deleted it, e.g. after the idle TTL) is exactly this cleanup.
    cleared_link = False
    if action == "kill":
        for sid, info in _task_rows().items():
            if sid == sandbox_id:
                sandbox_service._store_task_sandbox_id(str(info["task_id"]), None)  # noqa: SLF001
                cleared_link = True

    if server_error is None:
        return {"ok": True, "routed": "server"}
    if cleared_link:
        return {"ok": True, "routed": "stale_link"}
    return {"ok": False, "error": server_error}
