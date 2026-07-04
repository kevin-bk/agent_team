"""SandboxManager — one sandbox per task, with a capacity cap and an idle GC.

Each task that runs in isolated mode gets a **dedicated** sandbox so sibling
tasks never collide on workspace files. This module owns:

* **Opening** a fresh sandbox for a task (from a :class:`RuntimeProfile` + the
  task's workspace mount + any extra mounts/env).
* **Tracking** which sandbox belongs to which task so the loop can look it up
  between runs (pause/resume).
* **Reaping** idle sandboxes so an idle task costs no resources
  (``idle_ttl_seconds`` GC + ``pin_until`` to keep one warm while awaiting human
  review).

Ported and slimmed from ``deep_agent.sandbox.manager`` (dropped the
codebase-mirror translation — agent_team passes the workspace mount directly).

Threading model: single-process asyncio; the registry is a plain ``dict``
guarded by an ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from agent_team.features.board.runtime.sandbox.base import Sandbox
from agent_team.features.board.runtime.sandbox.config import RuntimeProfile
from agent_team.features.board.runtime.sandbox.factory import build_sandbox

logger = logging.getLogger(__name__)


class SandboxAcquireTimeout(Exception):
    """Raised when waiting for a free capacity slot exceeds the configured timeout."""


#: OpenSandbox metadata names must be DNS-friendly; slug anything else.
_NAME_ALLOWED = re.compile(r"[^a-z0-9-]")


@dataclass
class _SandboxRecord:
    """In-memory bookkeeping for one open per-task sandbox."""

    task_id: str
    sandbox: Sandbox
    name: str
    image: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    #: Monotonic timestamp of the last touch (open or ``mark_used``) — drives GC.
    last_used_at: float = field(default_factory=time.monotonic)
    #: Optional UNIX-epoch the GC must not reap before (keeps a sandbox warm while
    #: a task waits for human review). ``None`` ⇒ normal idle-TTL applies.
    pin_until_epoch: float | None = None


class SandboxManager:
    """Allocate, track, and tear down per-task sandboxes."""

    def __init__(
        self,
        *,
        profile: RuntimeProfile,
        max_concurrent: int = 0,
        acquire_timeout_seconds: float = 0.0,
        idle_ttl_seconds: float = 0.0,
        gc_interval_seconds: float = 0.0,
    ) -> None:
        """Args:

        profile: template runtime profile for every per-task sandbox.
        max_concurrent: cap on parallel sandboxes (``0`` ⇒ unlimited).
        acquire_timeout_seconds: how long ``open_for_task`` waits for a free slot
            (``0`` ⇒ wait forever).
        idle_ttl_seconds: reap a sandbox untouched for longer than this (``0`` ⇒
            never reap; rely on the runtime's own idle-close).
        gc_interval_seconds: GC sweep cadence (``0`` ⇒ no background loop).
        """
        self._profile = profile
        self._max_concurrent = max_concurrent
        self._acquire_timeout = acquire_timeout_seconds
        self._idle_ttl = idle_ttl_seconds
        self._gc_interval = gc_interval_seconds
        self._records: dict[str, _SandboxRecord] = {}
        self._lock = asyncio.Lock()
        sem_capacity = max_concurrent or 999_999
        self._slots = asyncio.Semaphore(sem_capacity)
        self._slot_held: dict[str, bool] = {}
        self._gc_task: asyncio.Task[None] | None = None

    # ── public API ─────────────────────────────────────────────────

    @property
    def open_count(self) -> int:
        return len(self._records)

    def has_task(self, task_id: str) -> bool:
        return task_id in self._records

    def get(self, task_id: str) -> Sandbox | None:
        rec = self._records.get(task_id)
        return rec.sandbox if rec else None

    def snapshot(self) -> list[dict[str, Any]]:
        """Read-only bookkeeping view of every tracked sandbox (admin page)."""
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        for rec in list(self._records.values()):
            out.append(
                {
                    "task_id": rec.task_id,
                    "name": rec.name,
                    "sandbox_id": getattr(rec.sandbox, "sandbox_id", None),
                    "state": rec.sandbox.state,
                    "image": rec.image,
                    "idle_seconds": max(0.0, now - rec.last_used_at),
                    "pinned_until_epoch": rec.pin_until_epoch,
                    "sandbox": rec.sandbox,
                }
            )
        return out

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def idle_ttl_seconds(self) -> float:
        return self._idle_ttl

    async def open_for_task(
        self,
        task_id: str,
        *,
        name: str | None = None,
        workspace_root: str | None = None,
        extra_env: dict[str, str] | None = None,
        extra_mounts: list[Any] | None = None,
        network_policy: dict[str, Any] | None = None,
        credential_proxy: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> Sandbox:
        """Spawn a fresh sandbox dedicated to ``task_id`` and remember it.

        Raises :class:`ValueError` if ``task_id`` already has an open sandbox,
        :class:`SandboxAcquireTimeout` if no slot frees up in time, or propagates
        :class:`SandboxError` from the runtime if open fails.
        """
        if not task_id:
            raise ValueError("task_id must be a non-empty string")

        if task_id in self._records:
            raise ValueError(
                f"SandboxManager: task_id {task_id!r} already has an open sandbox; "
                "close it first or pick a fresh id."
            )

        timeout = self._acquire_timeout
        try:
            if timeout > 0:
                await asyncio.wait_for(self._slots.acquire(), timeout=timeout)
            else:
                await self._slots.acquire()
        except TimeoutError as exc:
            raise SandboxAcquireTimeout(
                f"SandboxManager: timed out after {timeout}s waiting for a free slot "
                f"(max_concurrent={self._max_concurrent})."
            ) from exc
        slot_acquired = True

        try:
            async with self._lock:
                if task_id in self._records:
                    self._slots.release()
                    slot_acquired = False
                    raise ValueError(
                        f"SandboxManager: task_id {task_id!r} already has an open "
                        "sandbox; close it first or pick a fresh id."
                    )

                display = name or self._make_display_name(task_id)
                sandbox = build_sandbox(
                    self._profile,
                    workspace_root=workspace_root,
                    name=display,
                    extra_env=extra_env,
                    extra_mounts=extra_mounts,
                    network_policy=network_policy,
                    credential_proxy=credential_proxy,
                )

                logger.info(
                    "SandboxManager: opening sandbox task=%s name=%s image=%s mounts=%d",
                    task_id,
                    display,
                    self._profile.image or "<default>",
                    len(extra_mounts or []),
                )

                try:
                    await sandbox.open()
                except Exception:
                    logger.exception(
                        "SandboxManager: failed to open sandbox for task=%s", task_id
                    )
                    try:
                        await sandbox.close()
                    except Exception:  # noqa: BLE001
                        pass
                    raise

                self._records[task_id] = _SandboxRecord(
                    task_id=task_id,
                    sandbox=sandbox,
                    name=display,
                    image=self._profile.image,
                    extra=dict(extra or {}),
                )
                self._slot_held[task_id] = True
                return sandbox
        except Exception:
            if slot_acquired:
                self._slots.release()
            raise

    async def adopt(
        self,
        task_id: str,
        sandbox: Sandbox,
        *,
        name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Register an already-open sandbox (e.g. reattached after a restart).

        Takes a capacity slot exactly like :meth:`open_for_task` so the
        concurrency cap stays honest. Raises :class:`ValueError` if the task
        already has a tracked sandbox, :class:`SandboxAcquireTimeout` when no
        slot frees up in time.
        """
        if not task_id:
            raise ValueError("task_id must be a non-empty string")

        timeout = self._acquire_timeout
        try:
            if timeout > 0:
                await asyncio.wait_for(self._slots.acquire(), timeout=timeout)
            else:
                await self._slots.acquire()
        except TimeoutError as exc:
            raise SandboxAcquireTimeout(
                f"SandboxManager: timed out after {timeout}s waiting for a free slot "
                f"(max_concurrent={self._max_concurrent})."
            ) from exc

        async with self._lock:
            if task_id in self._records:
                self._slots.release()
                raise ValueError(
                    f"SandboxManager: task_id {task_id!r} already has an open "
                    "sandbox; close it first."
                )
            display = name or self._make_display_name(task_id)
            self._records[task_id] = _SandboxRecord(
                task_id=task_id,
                sandbox=sandbox,
                name=display,
                image=self._profile.image,
                extra=dict(extra or {}),
            )
            self._slot_held[task_id] = True
        logger.info(
            "SandboxManager: adopted existing sandbox task=%s name=%s id=%s",
            task_id,
            display,
            getattr(sandbox, "sandbox_id", None),
        )

    async def close(self, task_id: str) -> bool:
        """Close ``task_id``'s sandbox and drop it. Idempotent."""
        async with self._lock:
            rec = self._records.pop(task_id, None)
            had_slot = self._slot_held.pop(task_id, False)

        if had_slot:
            self._slots.release()

        if rec is None:
            return False

        logger.info("SandboxManager: closing sandbox task=%s name=%s", task_id, rec.name)
        try:
            await rec.sandbox.close()
        except Exception:  # noqa: BLE001
            logger.exception(
                "SandboxManager: error closing sandbox for task=%s (continuing)", task_id
            )
        return True

    async def aclose(self) -> None:
        """Close ALL outstanding sandboxes (process shutdown hook)."""
        await self.stop_gc()
        async with self._lock:
            task_ids = list(self._records.keys())
        for tid in task_ids:
            await self.close(tid)

    # ── idle bookkeeping + GC ───────────────────────────────────────

    def mark_used(self, task_id: str) -> None:
        """Refresh ``last_used_at`` so the GC doesn't reap a still-active task."""
        rec = self._records.get(task_id)
        if rec is not None:
            rec.last_used_at = time.monotonic()

    def pin_until(self, task_id: str, until_epoch: float | None) -> None:
        """Pin a sandbox so the GC won't reap it until ``until_epoch`` (None clears)."""
        rec = self._records.get(task_id)
        if rec is not None:
            rec.pin_until_epoch = until_epoch

    async def start_gc(self) -> None:
        """Spawn the idle-sweep task. Idempotent; ``gc_interval<=0`` disables it."""
        if self._gc_task is not None and not self._gc_task.done():
            return
        if self._gc_interval <= 0:
            logger.debug("SandboxManager.start_gc: gc_interval<=0 → disabled")
            return
        self._gc_task = asyncio.create_task(self._gc_loop(), name="sandbox-manager-gc")

    async def stop_gc(self) -> None:
        task = self._gc_task
        self._gc_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def sweep_idle(self) -> list[str]:
        """Run one immediate sweep. Returns reaped task_ids (tests + manual ops)."""
        return await self._sweep_once()

    async def _gc_loop(self) -> None:
        interval = max(1.0, self._gc_interval)
        while True:
            try:
                await asyncio.sleep(interval)
                reaped = await self._sweep_once()
                if reaped:
                    logger.info(
                        "SandboxManager.gc: reaped %d idle sandbox(es): %s",
                        len(reaped),
                        reaped,
                    )
            except asyncio.CancelledError:
                logger.debug("SandboxManager.gc: cancelled, exiting loop")
                raise
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.exception("SandboxManager.gc: sweep failed (continuing)")

    async def _sweep_once(self) -> list[str]:
        if self._idle_ttl <= 0:
            return []
        now_mono = time.monotonic()
        now_epoch = time.time()
        async with self._lock:
            stale_ids = [
                tid
                for tid, rec in self._records.items()
                if (now_mono - rec.last_used_at) > self._idle_ttl
                and (rec.pin_until_epoch is None or rec.pin_until_epoch <= now_epoch)
            ]
        for tid in stale_ids:
            await self.close(tid)
        return stale_ids

    # ── internals ──────────────────────────────────────────────────

    @staticmethod
    def _make_display_name(task_id: str) -> str:
        """Slug ``task_id`` into a DNS-safe display name (``at-<slug>``)."""
        slug = _NAME_ALLOWED.sub("-", task_id.lower()).strip("-")
        if not slug:
            slug = "task"
        slug = slug[:50]
        return f"at-{slug}"
