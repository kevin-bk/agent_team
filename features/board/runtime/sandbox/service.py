"""Process-wide sandbox service: the runtime provider seam for agent_team.

Owns a single :class:`SandboxManager` per process (so per-task sandboxes are
reused across a task's runs, the capacity cap is global, and one idle-GC loop
runs), resolves the effective :class:`RuntimeProfile`, and prepares a task's
sandbox (open on first run, **resume** a paused one on later runs).

Profile resolution overlays a board's ``runtime_profile_json`` on top of the
env defaults (``profile_from_env``); a per-task override is a planned follow-up.
Keeping the resolution behind :func:`resolve_profile` means later layers touch
only this function.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agent_team.features.board.runtime.sandbox.base import Sandbox, SandboxError
from agent_team.features.board.runtime.sandbox.config import (
    RuntimeProfile,
    VolumeMount,
    profile_from_env,
)
from agent_team.features.board.runtime.sandbox.manager import SandboxManager

logger = logging.getLogger(__name__)

_manager: SandboxManager | None = None
_manager_profile: RuntimeProfile | None = None


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_profile(task_id: str = "", board_id: str = "") -> RuntimeProfile:
    """Resolve the runtime profile for a task.

    Order (highest wins): task (TBD) → board override → env default. The env
    default is the base; a board's ``runtime_profile_json`` overlays any fields it
    sets. ``task_id`` is accepted now so a per-task override can be added later
    without changing callers.
    """
    profile = profile_from_env()
    if board_id:
        overlay = _board_overlay(board_id)
        if overlay:
            _apply_overlay(profile, overlay)
    return profile


def _board_overlay(board_id: str) -> dict:
    """Load a board's ``runtime_profile`` override (best-effort; empty on error)."""
    try:
        from agent_team.features.board.repositories import boards as boards_repo
        from core.database.base import SessionLocal
    except Exception:  # noqa: BLE001
        return {}
    db = None
    try:
        db = SessionLocal()
        board = boards_repo.get_board(db, board_id)
        return board.runtime_profile() if board is not None else {}
    except Exception:  # noqa: BLE001
        logger.debug("agent_team runtime: board overlay load failed", exc_info=True)
        return {}
    finally:
        if db is not None:
            db.close()


def _apply_overlay(profile: RuntimeProfile, overlay: dict) -> None:
    """Overlay a dict of RuntimeProfile field values in place (unknown keys ignored)."""
    import dataclasses

    valid = {f.name for f in dataclasses.fields(RuntimeProfile)}
    for key, value in overlay.items():
        if key in valid and value is not None:
            setattr(profile, key, value)


def get_manager(profile: RuntimeProfile | None = None) -> SandboxManager:
    """Return the process-wide :class:`SandboxManager`, building it on first use.

    The manager is keyed to the profile it was built with; if a caller passes a
    materially different provider/image we rebuild it (the old one is left for GC
    to drain — callers should not mix profiles within a process in practice).
    """
    global _manager, _manager_profile
    prof = profile or resolve_profile()
    if _manager is not None and _manager_profile is not None:
        if (
            _manager_profile.provider == prof.provider
            and _manager_profile.image == prof.image
        ):
            return _manager
    _manager = SandboxManager(
        profile=prof,
        max_concurrent=_env_int("AGENT_TEAM_RUNTIME_MAX_CONCURRENT", 0),
        acquire_timeout_seconds=_env_int("AGENT_TEAM_RUNTIME_ACQUIRE_TIMEOUT", 0),
        # Reap a task sandbox untouched for longer than idle_minutes; the runtime
        # itself also idle-closes, this is the manager-level backstop.
        idle_ttl_seconds=max(0, prof.idle_timeout_minutes) * 60,
        gc_interval_seconds=_env_int("AGENT_TEAM_RUNTIME_GC_INTERVAL", 60),
    )
    _manager_profile = prof
    return _manager


def _resolve_network_policy(
    profile: RuntimeProfile,
    plan: Any | None,
) -> dict[str, Any] | None:
    """Build the effective egress policy for a task sandbox.

    Merges the board-configured ``profile.network_policy`` (operator base) with
    the credential plan's provider-host allow rules. ``default_action`` resolves
    to: the board's explicit setting, else ``deny`` when ``strict_isolation`` is
    on *and* a credential is being injected (so an in-sandbox secret can't be
    exfiltrated), else ``allow`` (non-breaking — pip/npm/git keep working).

    Returns ``None`` when there is nothing to enforce (allow-all, no rules).
    """
    base = dict(profile.network_policy or {})
    rules: list[dict[str, str]] = [
        dict(r) for r in (base.get("egress") or []) if r.get("target")
    ]
    if plan is not None:
        rules.extend(r for r in plan.network_rules if r.get("target"))

    default_action = base.get("default_action")
    if default_action not in ("allow", "deny"):
        default_action = (
            "deny" if (profile.strict_isolation and plan is not None) else "allow"
        )

    if not rules and default_action == "allow":
        return None

    # Dedupe by target, first rule wins (matches the sidecar's merge semantics).
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for r in rules:
        target = r["target"]
        if target in seen:
            continue
        seen.add(target)
        deduped.append({"action": r.get("action", "allow"), "target": target})

    return {"default_action": default_action, "egress": deduped}


def _workspace_mount(profile: RuntimeProfile, host_workspace_path: str) -> VolumeMount:
    """Build the task-workspace mount for mount-mode (host path → /workspace)."""
    return VolumeMount(
        name="task-workspace",
        kind="host",
        host_path=host_workspace_path,
        mount_path=profile.workspace_mount_path,
        read_only=False,
    )


async def prepare_task_sandbox(
    *,
    task_id: str,
    host_workspace_path: str,
    profile: RuntimeProfile,
    board_id: str = "",
    extra_env: dict[str, str] | None = None,
) -> Sandbox:
    """Get-or-open-or-resume the sandbox for ``task_id`` and return it ready to use.

    * First run of the task → open a fresh sandbox (mount the workspace).
    * Later run while the sandbox is paused → resume it (workspace preserved).
    * Later run while still open → reuse as-is.

    In mount mode the host task workspace is bind-mounted at
    ``profile.workspace_mount_path`` so host-side git diff / file browser see
    changes immediately. Sync mode is a planned follow-up.
    """
    manager = get_manager(profile)
    # Idempotent: starts the idle-GC backstop on first prepare (needs a running
    # loop, which we have here). A paused sandbox left untouched past idle_ttl is
    # then fully closed to free the slot.
    await manager.start_gc()
    sb = manager.get(task_id)
    if sb is not None:
        if sb.state == "open":
            manager.mark_used(task_id)
            return sb
        if sb.state == "paused":
            logger.info("agent_team runtime: resuming paused sandbox for task=%s", task_id)
            try:
                await sb.resume()
                manager.mark_used(task_id)
                return sb
            except SandboxError:
                logger.warning(
                    "agent_team runtime: resume failed for task=%s; reopening fresh",
                    task_id,
                    exc_info=True,
                )
        else:
            # closed / broken (e.g. runtime idle-closed or lost on restart) —
            # drop the dead record and open a fresh sandbox below.
            logger.info(
                "agent_team runtime: tracked sandbox for task=%s is %s; reopening",
                task_id,
                sb.state,
            )
        await manager.close(task_id)

    # Credential injection, driven by the board's staffed coding agents +
    # remote MCP hosts. Only for the isolated provider — the local runtime uses
    # the host's own credentials. Each staffed agent's login folder (from the AI
    # Code Factory pool) is mounted into the same task sandbox.
    plan = None
    merged_env = extra_env
    cred_mounts: list[Any] = []
    if profile.is_sandboxed and board_id:
        from agent_team.features.board.runtime.credentials.service import (
            build_injection_for_board,
        )

        plan = build_injection_for_board(board_id)
        if plan is not None:
            if plan.env:
                merged_env = {**(extra_env or {}), **plan.env}
            cred_mounts = list(plan.mounts)

    extra_mounts: list[Any] = []
    if profile.workspace_mode == "mount":
        extra_mounts.append(_workspace_mount(profile, host_workspace_path))
    extra_mounts.extend(cred_mounts)

    network_policy = _resolve_network_policy(profile, plan)
    credential_proxy = bool(plan is not None and plan.needs_credential_proxy)

    sb = await manager.open_for_task(
        task_id,
        workspace_root=host_workspace_path,  # used by the local provider
        extra_env=merged_env,
        extra_mounts=extra_mounts or None,
        network_policy=network_policy,
        credential_proxy=credential_proxy,
    )

    # Provision the Credential Vault after open (secrets stay at the egress proxy,
    # never in the sandbox). Only on this fresh-open path; a reused/resumed
    # sandbox already has its vault from its first open.
    if plan is not None and plan.vault_bindings:
        try:
            await sb.write_vault(plan.vault_credentials, plan.vault_bindings)
        except SandboxError:
            logger.exception(
                "agent_team runtime: credential vault write failed for task=%s; "
                "closing the sandbox to avoid an unauthenticated run",
                task_id,
            )
            await manager.close(task_id)
            raise
    return sb


#: Command that (idempotently) starts the ACP sidecar server inside the sandbox
#: and blocks until its health endpoint answers. ``exit 0`` if already running.
_SIDECAR_START = (
    "set -e; "
    'if curl -sf "http://127.0.0.1:{port}/healthz" >/dev/null 2>&1; then exit 0; fi; '
    "nohup agent-team-runtime-server --host 0.0.0.0 --port {port} "
    ">/tmp/agent-team-runtime-server.log 2>&1 & "
    "for i in $(seq 1 {tries}); do "
    '  if curl -sf "http://127.0.0.1:{port}/healthz" >/dev/null 2>&1; then exit 0; fi; '
    "  sleep 0.5; "
    "done; "
    "echo 'sidecar did not become healthy' >&2; "
    "tail -n 40 /tmp/agent-team-runtime-server.log >&2 || true; exit 1"
)


async def open_sidecar_channel(sandbox: Sandbox, profile: RuntimeProfile) -> str:
    """Ensure the in-sandbox ACP sidecar is up and return its WebSocket URL.

    Idempotent: starts the server on first turn, no-ops thereafter. The WS URL is
    resolved through the OpenSandbox proxy (:meth:`get_endpoint`) so the host can
    reach the in-sandbox port without direct network access.
    """
    port = profile.sidecar_port
    # First cold start imports the app + creates the local SQLite, so allow a
    # generous health window (tries × 0.5s) that stays under the exec timeout.
    res = await sandbox.exec_shell(
        _SIDECAR_START.format(port=port, tries=140),
        timeout_seconds=90,
    )
    if not res.success:
        raise SandboxError(
            f"ACP sidecar failed to start (exit={res.exit_code}): "
            f"{(res.stderr or res.stdout)[-500:]}"
        )
    endpoint = await sandbox.get_endpoint(port)
    return _endpoint_to_ws_url(endpoint, port)


def _endpoint_to_ws_url(endpoint: object, port: int) -> str:
    """Coerce an OpenSandbox endpoint (str / object) into a ``ws(s)://…/acp`` URL."""
    url: str | None = None
    if isinstance(endpoint, str):
        url = endpoint
    else:
        for attr in ("url", "endpoint", "public_url", "href"):
            val = getattr(endpoint, attr, None)
            if val:
                url = str(val)
                break
    if not url:
        raise SandboxError(f"could not resolve sidecar endpoint for port {port}")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://") :]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://") :]
    elif not url.startswith(("ws://", "wss://")):
        url = "ws://" + url
    return url.rstrip("/") + "/acp"


def describe_runtime(task_id: str = "", board_id: str = "") -> dict:
    """Return a read-only snapshot of a task's runtime for the cockpit panel.

    Reports the effective profile (provider / image / resources / isolation) plus
    the live sandbox id + state when one is currently tracked for the task.
    """
    profile = resolve_profile(task_id, board_id)
    info: dict = {
        "provider": profile.provider,
        "isolated": profile.is_sandboxed,
        "strategy": profile.runtime_strategy if profile.is_sandboxed else None,
        "strict_isolation": profile.strict_isolation,
        "image": profile.image if profile.is_sandboxed else None,
        "workspace_mode": profile.workspace_mode,
        "cpu": profile.cpu,
        "memory_mb": profile.memory_mb,
        "idle_timeout_minutes": profile.idle_timeout_minutes,
        "sandbox_id": None,
        "sandbox_state": None,
    }
    if profile.is_sandboxed and _manager is not None and task_id:
        sb = _manager.get(task_id)
        if sb is not None:
            info["sandbox_id"] = sb.sandbox_id
            info["sandbox_state"] = _UI_STATE.get(sb.state, sb.state)
    return info


#: Map internal sandbox lifecycle states to cockpit-facing labels.
_UI_STATE = {
    "open": "running",
    "opening": "running",
    "paused": "paused",
    "closed": None,
    "broken": None,
}


async def pause_task_sandbox(task_id: str) -> None:
    """Pause a task's sandbox after a turn so an idle task costs no resources.

    Best-effort: providers that cannot pause (e.g. local) are left running.
    """
    if _manager is None:
        return
    sb = _manager.get(task_id)
    if sb is None:
        return
    try:
        await sb.pause()
    except NotImplementedError:
        pass  # local sandbox has nothing to pause
    except Exception:  # noqa: BLE001
        logger.warning("agent_team runtime: pause failed for task=%s", task_id, exc_info=True)


async def kill_task_sandbox(task_id: str) -> None:
    """Tear a task's sandbox down entirely, freeing its resources.

    Unlike :func:`pause_task_sandbox` this discards the environment; the next turn
    reprovisions from scratch. No-op when nothing is tracked for the task.
    """
    if _manager is None:
        return
    if _manager.get(task_id) is None:
        return
    try:
        await _manager.close(task_id)
    except Exception:  # noqa: BLE001
        logger.warning("agent_team runtime: kill failed for task=%s", task_id, exc_info=True)
