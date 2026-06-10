"""Reconstruct a (task, agent) transcript from durable run events.

The plugin's source of truth for a run's content is the event store, not a
separate message table. To render a persistent conversation — and to survive a
reload or the post-run refetch the cockpit does — we replay each run's frames
into the same typed content blocks (``text``/``thinking``/``tool_use``/
``tool_result``) the live SSE stream produces, then wrap them as ``MessageDTO``
turns (one user prompt + one assistant answer per run).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamConversation, AgentTeamRun
from agent_team.features.board.repositories import runs as runs_repo
from agent_team.features.board.runtime import event_store
from agent_team.features.board.runtime.events import (
    EVENT_TEXT_DELTA,
    EVENT_THINKING,
    EVENT_TOOL_USE_END,
    EVENT_TOOL_USE_START,
)
from agent_team.features.board.runtime.translator import (
    normalize_tool_input,
    strip_tool_blocks,
)
from agent_team.features.board.schemas import MessageDTO
from core.database.models import User


def _ms(value: datetime | None) -> int:
    return int(value.timestamp() * 1000) if value is not None else 0


def _assistant_blocks(events: list[dict]) -> tuple[list[dict], str]:
    """Replay run frames into content blocks; also return the joined text."""
    blocks: list[dict] = []
    for frame in events:
        ftype = frame.get("type")
        data = frame.get("data") or {}
        if ftype == EVENT_TEXT_DELTA:
            text = str(data.get("text") or "")
            if not text:
                continue
            if blocks and blocks[-1]["type"] == "text":
                blocks[-1]["text"] += text
            else:
                blocks.append({"type": "text", "text": text})
        elif ftype == EVENT_THINKING:
            text = str(data.get("text") or "")
            if not text:
                continue
            if blocks and blocks[-1]["type"] == "thinking":
                blocks[-1]["thinking"] += text
            else:
                blocks.append({"type": "thinking", "thinking": text})
        elif ftype == EVENT_TOOL_USE_START:
            tool_name = str(data.get("tool_name") or "tool")
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(data.get("tool_id") or ""),
                    "name": tool_name,
                    # Old runs were persisted with the raw tool arg names; map
                    # them to the cockpit's display schema on read so historic
                    # file writes show their body too.
                    "input": normalize_tool_input(tool_name, data.get("input") or {}),
                }
            )
        elif ftype == EVENT_TOOL_USE_END:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(data.get("tool_id") or ""),
                    "content": data.get("output_preview") or "",
                    "is_error": bool(data.get("is_error")),
                }
            )
    # Sanitize on read too: runs recorded before tool-use blocks were filtered
    # still carry leaked JSON in their stored text, so clean it when rebuilding
    # the transcript. Text blocks emptied by the filter are dropped.
    cleaned: list[dict] = []
    for block in blocks:
        if block["type"] == "text":
            text = strip_tool_blocks(block["text"])
            if not text:
                continue
            block = {"type": "text", "text": text}
        cleaned.append(block)
    blocks = cleaned
    text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
    return blocks, text


def list_thread_messages(
    db: Session, *, conversation: AgentTeamConversation, agent_display: str
) -> list[MessageDTO]:
    """Build the ordered transcript for one conversation attempt."""
    runs = runs_repo.list_runs_for_conversation(db, conversation.id)

    actor_ids = {r.actor_id for r in runs if r.actor_id}
    users = {
        u.id: u
        for u in (
            db.query(User).filter(User.id.in_(actor_ids)).all() if actor_ids else []
        )
    }

    messages: list[MessageDTO] = []
    seq = 0
    for run in runs:
        messages.append(_user_turn(run, seq, users))
        seq += 1
        messages.append(_assistant_turn(db, run, seq, conversation, agent_display))
        seq += 1
    return messages


def _user_turn(run: AgentTeamRun, seq: int, users: dict[str, User]) -> MessageDTO:
    author = users.get(run.actor_id) if run.actor_id else None
    return MessageDTO(
        seq=seq,
        role="user",
        content=[{"type": "text", "text": run.prompt or ""}],
        text=run.prompt or "",
        created_at_ms=_ms(run.created_at),
        run_id=run.id,
        sender_type="user" if run.actor_id else None,
        sender_id=run.actor_id,
        sender_name=(author.full_name or author.username) if author else None,
        sender_avatar=None,
    )


def _assistant_turn(
    db: Session,
    run: AgentTeamRun,
    seq: int,
    conversation: AgentTeamConversation,
    agent_display: str,
) -> MessageDTO:
    blocks, text = _assistant_blocks(event_store.list_events(run.id))
    final = strip_tool_blocks(run.final_answer or "") or text
    if not blocks and final:
        blocks = [{"type": "text", "text": final}]
    return MessageDTO(
        seq=seq,
        role="assistant",
        content=blocks,
        text=final,
        created_at_ms=_ms(run.ended_at or run.started_at or run.created_at),
        run_id=run.id,
        sender_type="agent",
        sender_id=conversation.agent_alias,
        sender_name=agent_display or conversation.agent_alias,
        sender_avatar=None,
    )
