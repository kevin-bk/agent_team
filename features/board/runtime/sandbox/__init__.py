"""Task-scoped isolated execution runtime for agent_team.

This package gives each task an OpenSandbox-backed container so coding agents
run *inside an isolated environment* instead of on the host process. It is a
near-verbatim port of the proven ``deep_agent.sandbox`` layer, adapted to
agent_team (no pydantic profile dependency, no deep-agent-specific paths).

Public surface:

* :class:`~.base.Sandbox` — the runtime boundary (ABC).
* :class:`~.local.LocalSandbox` — host, no isolation (tests / dev parity).
* :class:`~.opensandbox.OpenSandboxRuntime` — OpenSandbox-backed, with keepalive
  TTL renewal, idle-close, and pause/resume so an idle task costs no resources.
* :class:`~.manager.SandboxManager` — one sandbox per task + idle GC reaper.
* :class:`~.config.RuntimeProfile` / :class:`~.config.VolumeMount` — config DTOs.
* :func:`~.factory.build_sandbox` — build a sandbox from a profile.

See ``docs/plans/opensandbox-runtime-implementation-plan.md``.
"""

from __future__ import annotations

from agent_team.features.board.runtime.sandbox.base import (
    ExecResult,
    Sandbox,
    SandboxAuthError,
    SandboxBrokenError,
    SandboxError,
    SandboxNotFoundError,
    SandboxRateLimitError,
    SandboxState,
    SandboxTimeoutError,
    StreamCallback,
)
from agent_team.features.board.runtime.sandbox.config import (
    RuntimeProfile,
    VolumeMount,
)

__all__ = [
    "ExecResult",
    "RuntimeProfile",
    "Sandbox",
    "SandboxAuthError",
    "SandboxBrokenError",
    "SandboxError",
    "SandboxNotFoundError",
    "SandboxRateLimitError",
    "SandboxState",
    "SandboxTimeoutError",
    "StreamCallback",
    "VolumeMount",
]
