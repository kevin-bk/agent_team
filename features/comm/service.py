"""Outbound notification service: the single dispatch chokepoint.

The loop status sink (and a couple of explicit planning call sites) call
:func:`notify_loop_state`. Work is handed to a background thread so a slow or
failing provider never blocks or breaks the loop (best-effort, plan §19).

Flow per send:

1. Resolve the board's channel link + connection.
2. Skip if the event is not in the channel allowlist or the channel is disabled.
3. Compute a ``dedupe_key``; skip if a delivery already exists for it.
4. Render the message, resolve ``@mention`` usernames, send via the provider.
5. Persist the delivery row and append a Task Journal entry.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from agent_team.features.comm import render, tagging
from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm.events import (
    dedupe_key as build_dedupe_key,
)
from agent_team.features.comm.events import (
    event_for_state,
)
from agent_team.features.comm.models import (
    DELIVERY_FAILED,
    DELIVERY_SENT,
)
from agent_team.features.comm.providers.base import CommMessage, ProviderTarget
from agent_team.features.comm.providers.registry import get_provider
from agent_team.features.comm.refs import TaskRef

logger = logging.getLogger(__name__)


def notify_loop_state(
    *, task_id: str, board_id: str | None, state: str, attempt: int = 0
) -> None:
    """Best-effort: fire a notification for a loop-state transition, off-thread.

    Returns immediately. Any error inside the worker is logged and swallowed.
    """
    event_type = event_for_state(state)
    if not event_type or not board_id:
        return
    thread = threading.Thread(
        target=_dispatch_safe,
        kwargs={
            "task_id": task_id,
            "board_id": board_id,
            "event_type": event_type,
            "state": state,
            "attempt": attempt,
        },
        daemon=True,
    )
    thread.start()


def _dispatch_safe(**kwargs) -> None:
    try:
        _dispatch(**kwargs)
    except Exception:  # pragma: no cover - notifications are best-effort
        logger.warning("comm: dispatch failed for task %s", kwargs.get("task_id"), exc_info=True)


def _load_task_ref(db, task_id: str) -> TaskRef | None:
    from agent_team.features.board.repositories.boards import get_board
    from agent_team.features.board.repositories.tasks import get_task

    task = get_task(db, task_id)
    if task is None:
        return None
    board = get_board(db, task.board_id)
    return TaskRef(
        id=task.id,
        board_id=task.board_id,
        key=task.human_key,
        title=task.title or "",
        assignee_id=task.assignee_id,
        reporter_id=task.reporter_id,
        created_by=task.created_by,
        board_slug=board.slug if board else "",
    )


def _dispatch(
    *, task_id: str, board_id: str, event_type: str, state: str, attempt: int
) -> None:
    from core.database.base import SessionLocal

    db = SessionLocal()
    try:
        channel = comm_repo.get_board_channel(db, board_id)
        if channel is None or not channel.enabled:
            return
        if event_type not in comm_repo.board_channel_allowlist(channel):
            return
        connection = comm_repo.get_connection(db, channel.connection_id)
        if connection is None or connection.archived or not connection.has_token():
            return

        key = build_dedupe_key(
            task_id=task_id, event_type=event_type, state=state, attempt=attempt
        )
        if comm_repo.get_delivery_by_dedupe(db, key) is not None:
            return  # already delivered this exact transition

        task = _load_task_ref(db, task_id)
        if task is None:
            return

        title, body, severity = render.render_event(
            event_type=event_type, task_key=task.key, task_title=task.title
        )
        url = render.task_deep_link(
            deep_link_base=connection.deep_link_base,
            board_slug=task.board_slug,
            task_key=task.key,
        )
        mentions = tagging.resolve_mentions(
            db,
            event_type=event_type,
            channel=channel,
            connection=connection,
            task=task,
        )

        mention_labels = [m.handle or m.user_id for m in mentions if (m.handle or m.user_id)]
        delivery = comm_repo.create_delivery(
            db,
            task_id=task_id,
            board_id=board_id,
            channel_id=channel.id,
            event_type=event_type,
            provider=connection.provider,
            dedupe_key=key,
            payload={"title": title, "severity": severity, "mentions": mention_labels},
        )

        root_id = (
            comm_repo.latest_thread_id(db, task_id=task_id, channel_id=channel.id)
            if channel.use_threads
            else None
        )
        message = CommMessage(
            title=title,
            body=body,
            url=url,
            severity=severity,
            mentions=mentions,
            thread_key=task_id,
        )
        target = ProviderTarget(
            server_url=connection.server_url,
            bot_token=connection.bot_token or "",
            channel_id=channel.channel_id,
            use_threads=channel.use_threads,
            root_id=root_id,
        )
        result = get_provider(connection.provider).send(target, message)

        delivery.status = DELIVERY_SENT if result.ok else DELIVERY_FAILED
        delivery.provider_message_id = result.provider_message_id
        delivery.provider_thread_id = result.provider_thread_id
        delivery.error = result.error
        if result.ok:
            delivery.sent_at = datetime.now(UTC)
        db.commit()

        _journal(
            task_id, event_type, channel.channel_name, result, delivery.id, connection.provider
        )
    finally:
        db.close()


def _journal(task_id, event_type, channel_name, result, delivery_id, provider) -> None:
    from agent_team.features.board.runtime import task_journal

    if result.ok:
        title = f"Notification sent: {event_type}"
        severity = "info"
        body = f"Posted to channel {channel_name}." if channel_name else "Posted to channel."
    else:
        title = f"Notification failed: {event_type}"
        severity = "warning"
        body = result.error or "Provider send failed."
    task_journal.record(
        task_id=task_id,
        title=title,
        type="note",
        phase="system",
        body=body,
        severity=severity,
        refs={"delivery_id": delivery_id, "provider": provider},
        metadata={"source": "communication_gateway"},
    )


def test_send(
    *,
    server_url: str,
    bot_token: str,
    channel_id: str,
    provider: str = "mattermost",
    text: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Send a one-off test message. Returns ``(ok, provider_message_id, error)``."""
    message = CommMessage(
        title="Agent Team test notification",
        body=text or "This channel is wired up correctly.",
        severity="success",
    )
    target = ProviderTarget(
        server_url=server_url, bot_token=bot_token, channel_id=channel_id, use_threads=False
    )
    result = get_provider(provider).send(target, message)
    return result.ok, result.provider_message_id, result.error


def serialize_payload(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
