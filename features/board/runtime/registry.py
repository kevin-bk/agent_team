"""In-process registry of running runs for the local backend.

This is only a fast-path for same-process cancellation; the durable source of
truth for run state is the database (event store). After a restart this map is
empty, so orphaned runs are reconciled from the database instead.
"""

from __future__ import annotations

import asyncio


class RunHandle:
    """Tracks the asyncio task and cancel signal for one in-flight run."""

    __slots__ = ("cancel_event", "task")

    def __init__(self) -> None:
        self.cancel_event = asyncio.Event()
        self.task: asyncio.Task | None = None


_handles: dict[str, RunHandle] = {}


def register(run_id: str) -> RunHandle:
    handle = RunHandle()
    _handles[run_id] = handle
    return handle


def get(run_id: str) -> RunHandle | None:
    return _handles.get(run_id)


def unregister(run_id: str) -> None:
    _handles.pop(run_id, None)


def active_run_ids() -> set[str]:
    return set(_handles)
