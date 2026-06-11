"""In-process scheduled-pull ticker for repos (no PM2).

A single daemon thread wakes every ``tick_interval`` seconds, finds repos whose
``next_pull_at`` is due, advances their cursor first (at-most-once), then runs a
fast-forward pull for each. The thread lives and dies with the app process —
exactly what we want for git pulls (there's nothing to pull while the app is
down). On restart, due repos are simply picked up on the next tick.

``on_startup`` runs synchronously during app construction (no event loop yet), so
this uses a plain thread rather than an asyncio task. An ``fcntl`` file lock makes
sure only one worker process runs the ticker even if several are spawned.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from agent_team.features.repos.git_service import sync_repo_by_id
from agent_team.features.repos.models import SCHEDULE_OFF, AgentTeamRepo
from agent_team.features.repos.paths import pull_lock_path, repos_root
from agent_team.features.repos.schedule import compute_next_pull_at

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

#: How often the ticker scans for due repos.
_TICK_INTERVAL_SECONDS = 60.0


class RepoPullTicker:
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
                "agent_team repos: pull ticker lock held elsewhere; not starting here"
            )
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="agent_team-repo-pull", daemon=True
        )
        self._thread.start()
        logger.info(
            "agent_team repos: pull ticker started (interval=%.0fs)", self._interval
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._release_lock()

    # ── loop ───────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_once()
            except Exception:  # noqa: BLE001
                logger.exception("agent_team repos: pull tick crashed (continuing)")
            self._stop.wait(self._interval)

    def _tick_once(self) -> int:
        due_ids = self._claim_due()
        for repo_id in due_ids:
            if self._stop.is_set():
                break
            try:
                result = sync_repo_by_id(repo_id)
                logger.info(
                    "agent_team repos: scheduled %s for %s -> %s",
                    result.action,
                    repo_id,
                    "ok" if result.ok else f"failed: {result.message[:120]}",
                )
            except Exception:  # noqa: BLE001
                logger.exception("agent_team repos: scheduled pull failed for %s", repo_id)
        return len(due_ids)

    @staticmethod
    def _claim_due() -> list[str]:
        """Load due repos and advance their cursor first (at-most-once)."""
        from core.database.base import SessionLocal

        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            rows = (
                db.query(AgentTeamRepo)
                .filter(
                    AgentTeamRepo.archived.is_(False),
                    AgentTeamRepo.schedule_mode != SCHEDULE_OFF,
                    AgentTeamRepo.next_pull_at.is_not(None),
                    AgentTeamRepo.next_pull_at <= now,
                )
                .all()
            )
            ids: list[str] = []
            for repo in rows:
                repo.next_pull_at = compute_next_pull_at(
                    mode=repo.schedule_mode,
                    interval_seconds=repo.schedule_interval_seconds,
                    cron=repo.schedule_cron,
                    base=now,
                )
                ids.append(repo.id)
            db.commit()
            return ids
        finally:
            db.close()

    # ── cross-process lock ───────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        if fcntl is None:
            return True  # best-effort on platforms without fcntl
        import os

        try:
            repos_root().mkdir(parents=True, exist_ok=True)
            fd = os.open(str(pull_lock_path()), os.O_CREAT | os.O_RDWR, 0o600)
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


_ticker: RepoPullTicker | None = None


def start_ticker() -> None:
    global _ticker
    if _ticker is None:
        _ticker = RepoPullTicker()
    _ticker.start()


def stop_ticker() -> None:
    global _ticker
    if _ticker is not None:
        _ticker.stop()
        _ticker = None
