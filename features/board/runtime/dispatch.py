"""Bridge for starting agent runs from non-async contexts (the autopilot ticker).

Board runs execute as asyncio tasks on the app's main event loop (see
``LocalRunBackend.start``). The autopilot ticker is a plain daemon thread, so it
cannot ``await`` the backend directly; it hands the coroutine to the captured
main loop via :func:`asyncio.run_coroutine_threadsafe`.

The loop is captured the first time an async board endpoint or a manual run
touches it — in practice well before the first scheduled tick fires. Until then
:func:`dispatch_start` reports "not ready" so the ticker defers claiming a task
(it stays in the source column and is retried on a later tick).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_main_loop: asyncio.AbstractEventLoop | None = None


def capture_main_loop() -> None:
    """Record the currently running loop as the app's main loop (idempotent)."""
    global _main_loop
    if _main_loop is None:
        try:
            _main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass


def main_loop_ready() -> bool:
    return _main_loop is not None and not _main_loop.is_closed()


def dispatch_start(run_id: str) -> bool:
    """Schedule ``backend.start(run_id)`` on the main loop from any thread.

    Returns ``False`` when the main loop has not been captured yet, so the
    caller can avoid claiming work it cannot start.
    """
    if not main_loop_ready():
        return False

    from agent_team.features.board.runtime.backend import get_run_backend

    async def _go() -> None:
        try:
            await get_run_backend().start(run_id)
        except Exception:  # noqa: BLE001 — never let a dispatch crash the loop
            logger.exception("autopilot: failed to start run %s", run_id)

    assert _main_loop is not None
    asyncio.run_coroutine_threadsafe(_go(), _main_loop)
    return True
