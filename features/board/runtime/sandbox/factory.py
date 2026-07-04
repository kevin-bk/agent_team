"""Build a :class:`Sandbox` from a :class:`RuntimeProfile`.

Centralises dispatch from a resolved runtime profile (``provider`` =
``local`` | ``opensandbox``) to a concrete runtime instance, so callers stay
free of SDK-specific imports.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_team.features.board.runtime.sandbox.base import Sandbox, SandboxError
from agent_team.features.board.runtime.sandbox.config import RuntimeProfile


def build_sandbox(
    profile: RuntimeProfile,
    *,
    workspace_root: Path | str | None = None,
    name: str | None = None,
    extra_env: dict[str, str] | None = None,
    extra_mounts: list[Any] | None = None,
) -> Sandbox:
    """Instantiate a sandbox from a :class:`RuntimeProfile`.

    :param workspace_root: used for ``provider="local"`` — the host directory the
        LocalSandbox roots its shell in (usually the task workspace).
    :param name: human-readable id surfaced into OpenSandbox metadata (the
        per-task :class:`SandboxManager` uses ``at-<task-slug>``).
    :param extra_env: merged on top of ``profile.env`` (extras win); the
        ``env_blocklist`` is applied afterwards inside the runtime.
    :param extra_mounts: appended to ``profile.mounts`` (e.g. the task workspace
        mount added by the manager).
    """
    if profile.provider == "local":
        root = workspace_root
        if root is None:
            raise SandboxError(
                "LocalSandbox needs a workspace_root (pass workspace_root= to build_sandbox)"
            )
        from agent_team.features.board.runtime.sandbox.local import LocalSandbox

        return LocalSandbox(workspace_root=root)

    if profile.provider == "opensandbox":
        from agent_team.features.board.runtime.sandbox.opensandbox import (
            OpenSandboxRuntime,
        )

        api_key = os.environ.get(profile.api_key_env) if profile.api_key_env else None

        merged_env: dict[str, str] = dict(profile.env)
        if extra_env:
            merged_env.update(extra_env)

        merged_mounts: list[Any] = list(profile.mounts)
        if extra_mounts:
            merged_mounts.extend(extra_mounts)

        idle_timeout = (
            timedelta(minutes=profile.idle_timeout_minutes)
            if profile.idle_timeout_minutes > 0
            else None
        )
        return OpenSandboxRuntime(
            server_url=profile.server_url or "http://localhost:8090",
            api_key=api_key,
            image=profile.image,
            timeout=timedelta(minutes=profile.timeout_minutes),
            idle_timeout=idle_timeout,
            env=merged_env,
            env_blocklist=list(profile.env_blocklist),
            mounts=merged_mounts,
            cpu=profile.cpu,
            memory_mb=profile.memory_mb,
            ready_timeout_seconds=profile.ready_timeout_seconds,
            request_timeout_seconds=profile.request_timeout_seconds,
            use_server_proxy=profile.use_server_proxy,
            workspace_mount_path=profile.workspace_mount_path,
            name=name,
        )

    raise SandboxError(f"Unknown runtime provider: {profile.provider!r}")
