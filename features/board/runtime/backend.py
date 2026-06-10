"""Run backend abstraction.

Every "run an agent in the background" path goes through this single interface
so the implementation can later be swapped (e.g. to a durable job queue) without
touching routers or the frontend. Reading run state never goes through a
backend — routers always read the DB event store — so SSE/REST stay backend
agnostic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RunBackend(Protocol):
    async def start(self, run_id: str) -> None:
        """Begin executing a run that already exists in the DB as ``queued``."""

    async def cancel(self, run_id: str) -> bool:
        """Request cancellation; return ``True`` if the request was accepted."""

    async def reconcile_orphans(self) -> int:
        """Recover runs left non-terminal by a crash/restart. Return the count."""


_backend: RunBackend | None = None


def get_run_backend() -> RunBackend:
    """Return the process-wide run backend.

    Only the in-process ``LocalRunBackend`` exists today. A future durable
    backend (selected via an ``AGENT_TEAM_RUN_BACKEND`` setting) would implement
    this same interface, leaving routers and the frontend unchanged.
    """
    global _backend
    if _backend is None:
        from agent_team.features.board.runtime.local_backend import (
            LocalRunBackend,
        )

        _backend = LocalRunBackend()
    return _backend
