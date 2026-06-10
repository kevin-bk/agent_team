"""Board-scoped realtime event bus (process-local pub/sub).

Mirrors the deep-agent board feed: a tiny fan-out so everyone viewing a board
(Kanban + open cockpit) sees each other's changes without an F5. Events are
small ``{type, ...ids}`` hints; the client re-reads through the normal API, so a
dropped frame only costs a slightly stale view.

The frontend switches on ``type`` (``task.created``/``task.updated``/
``task.moved``/``task.deleted``, ``comment.created``/``comment.deleted``,
``run.started``/``run.finished``, ``agent.typing``) to invalidate the right
query or attach to a newly started run, so the type strings here are a wire
contract and must match the FE.

This is process-local. With a single app process it is sufficient; a
multi-process deployment would back the same ``publish``/``subscribe`` surface
with Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

#: Bounded per-subscriber queue; overflow drops frames for a slow consumer
#: (it re-syncs on the next event / refetch) rather than blocking producers.
_QUEUE_MAXSIZE = 256


class BoardEventBus:
    """Per-``board_id`` fan-out of small JSON event dicts to SSE streams."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, board_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subs.setdefault(board_id, set()).add(queue)
        return queue

    def unsubscribe(self, board_id: str, queue: asyncio.Queue) -> None:
        subs = self._subs.get(board_id)
        if subs is None:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(board_id, None)

    def publish(self, board_id: str, event: dict) -> None:
        """Push one event dict to every subscriber of ``board_id`` (best-effort)."""
        if not board_id:
            return
        for queue in list(self._subs.get(board_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "agent_team board event queue full for %s — dropping %s",
                    board_id,
                    event.get("type"),
                )


_bus = BoardEventBus()


def get_board_bus() -> BoardEventBus:
    """Return the process-wide board event bus singleton."""
    return _bus
