"""In-process ticker that drives the board autopilot (no PM2).

A single daemon thread wakes every ``tick_interval`` seconds and processes any
board whose autopilot is enabled and due (see ``runtime.autopilot.run_tick``).
The thread lives and dies with the app process; on restart, due boards are
simply picked up on the next tick. An ``fcntl`` file lock ensures only one
worker process runs the ticker even if several are spawned.

This mirrors ``features/repos/scheduler.py`` (the repo-pull ticker) so the two
schedulers behave and fail the same way.
"""

from __future__ import annotations

import logging
import threading

from agent_team.features.board.workspace import workspace_root

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

#: How often the ticker scans for due autopilots.
_TICK_INTERVAL_SECONDS = 30.0


def _lock_path() -> str:
    return str(workspace_root() / ".autopilot.lock")


class BoardAutopilotTicker:
    def __init__(self, tick_interval: float = _TICK_INTERVAL_SECONDS) -> None:
        self._interval = max(5.0, tick_interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_fd: int | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._acquire_lock():
            logger.info(
                "agent_team autopilot: ticker lock held elsewhere; not starting here"
            )
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="agent_team-autopilot", daemon=True
        )
        self._thread.start()
        logger.info(
            "agent_team autopilot: ticker started (interval=%.0fs)", self._interval
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._release_lock()

    # ── loop ───────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        from agent_team.features.board.runtime.autopilot import run_tick

        while not self._stop.is_set():
            try:
                started = run_tick()
                if started:
                    logger.info("agent_team autopilot: started %d run(s)", started)
            except Exception:  # noqa: BLE001
                logger.exception("agent_team autopilot: tick crashed (continuing)")
            self._stop.wait(self._interval)

    # ── cross-process lock ───────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        if fcntl is None:
            return True  # best-effort on platforms without fcntl
        import os

        try:
            workspace_root().mkdir(parents=True, exist_ok=True)
            fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._lock_fd = fd
        return True

    def _release_lock(self) -> None:
        if fcntl is None or self._lock_fd is None:
            return
        import os

        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
        except OSError:
            pass
        self._lock_fd = None


_ticker: BoardAutopilotTicker | None = None


def start_ticker() -> None:
    global _ticker
    if _ticker is None:
        _ticker = BoardAutopilotTicker()
    _ticker.start()


def stop_ticker() -> None:
    global _ticker
    if _ticker is not None:
        _ticker.stop()
        _ticker = None
