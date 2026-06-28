"""Data access + serialization for the Communication Gateway."""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from agent_team.features.comm.models import (
    LINK_AUTO,
    AgentTeamBoardChannel,
    AgentTeamCommConnection,
    AgentTeamCommDelivery,
    AgentTeamCommUserLink,
)
from agent_team.features.comm.schemas import (
    BoardChannelDTO,
    ConnectionCreate,
    ConnectionDTO,
    ConnectionUpdate,
    DeliveryDTO,
)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _loads_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


# ── connections (owner-scoped registry) ─────────────────────────────────────


def list_connections(
    db: Session, *, owner_id: str | None = None, include_archived: bool = False
) -> list[AgentTeamCommConnection]:
    q = db.query(AgentTeamCommConnection)
    if owner_id is not None:
        q = q.filter(AgentTeamCommConnection.owner_id == owner_id)
    if not include_archived:
        q = q.filter(AgentTeamCommConnection.archived.is_(False))
    return q.order_by(AgentTeamCommConnection.updated_at.desc()).all()


def get_connection(db: Session, connection_id: str) -> AgentTeamCommConnection | None:
    return (
        db.query(AgentTeamCommConnection)
        .filter(AgentTeamCommConnection.id == connection_id)
        .first()
    )


def create_connection(
    db: Session, *, owner_id: str | None, payload: ConnectionCreate
) -> AgentTeamCommConnection:
    conn = AgentTeamCommConnection(
        owner_id=owner_id,
        provider=payload.provider,
        name=payload.name.strip(),
        server_url=payload.server_url.strip(),
        bot_token=(payload.bot_token or None),
        default_team_id=(payload.default_team_id or None),
        deep_link_base=(payload.deep_link_base or None),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def update_connection(
    db: Session, conn: AgentTeamCommConnection, payload: ConnectionUpdate
) -> AgentTeamCommConnection:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        conn.name = data["name"].strip()
    if "server_url" in data and data["server_url"]:
        conn.server_url = data["server_url"].strip()
    # Write-only token: omitted = keep; "" or null = clear.
    if "bot_token" in data:
        conn.bot_token = data["bot_token"] or None
    if "default_team_id" in data:
        conn.default_team_id = data["default_team_id"] or None
    if "deep_link_base" in data:
        conn.deep_link_base = data["deep_link_base"] or None
    if "archived" in data and data["archived"] is not None:
        conn.archived = bool(data["archived"])
    db.commit()
    db.refresh(conn)
    return conn


def delete_connection(db: Session, conn: AgentTeamCommConnection) -> None:
    db.delete(conn)
    db.commit()


def count_boards_for_connection(db: Session, connection_id: str) -> int:
    return (
        db.query(func.count(AgentTeamBoardChannel.id))
        .filter(AgentTeamBoardChannel.connection_id == connection_id)
        .scalar()
        or 0
    )


def serialize_connection(db: Session, conn: AgentTeamCommConnection) -> ConnectionDTO:
    return ConnectionDTO(
        id=conn.id,
        owner_id=conn.owner_id,
        provider=conn.provider,
        name=conn.name,
        server_url=conn.server_url,
        has_token=conn.has_token(),
        default_team_id=conn.default_team_id,
        deep_link_base=conn.deep_link_base,
        archived=conn.archived,
        used_by_boards=count_boards_for_connection(db, conn.id),
        created_at=_iso(conn.created_at),
        updated_at=_iso(conn.updated_at),
    )


# ── board channel (board↔connection link) ───────────────────────────────────


def get_board_channel(db: Session, board_id: str) -> AgentTeamBoardChannel | None:
    """Return the board's single active channel link (v1 enforces one per board)."""
    return (
        db.query(AgentTeamBoardChannel)
        .filter(AgentTeamBoardChannel.board_id == board_id)
        .order_by(AgentTeamBoardChannel.created_at.asc())
        .first()
    )


def upsert_board_channel(
    db: Session,
    *,
    board_id: str,
    connection_id: str,
    channel_id: str,
    channel_name: str | None,
    use_threads: bool,
    event_allowlist: list[str],
    tag_mode: str,
    enabled: bool,
) -> AgentTeamBoardChannel:
    row = get_board_channel(db, board_id)
    allowlist_json = json.dumps(list(dict.fromkeys(event_allowlist)), ensure_ascii=False)
    if row is None:
        row = AgentTeamBoardChannel(board_id=board_id, connection_id=connection_id)
        db.add(row)
    row.connection_id = connection_id
    row.channel_id = channel_id.strip()
    row.channel_name = (channel_name or "").strip()
    row.use_threads = bool(use_threads)
    row.event_allowlist_json = allowlist_json
    row.tag_mode = tag_mode
    row.enabled = bool(enabled)
    db.commit()
    db.refresh(row)
    return row


def delete_board_channel(db: Session, board_id: str) -> bool:
    row = get_board_channel(db, board_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def boards_for_connection(db: Session, connection_id: str) -> list[str]:
    rows = (
        db.query(AgentTeamBoardChannel.board_id)
        .filter(AgentTeamBoardChannel.connection_id == connection_id)
        .all()
    )
    return [r[0] for r in rows]


def serialize_board_channel(
    db: Session, row: AgentTeamBoardChannel
) -> BoardChannelDTO:
    conn = get_connection(db, row.connection_id)
    return BoardChannelDTO(
        id=row.id,
        board_id=row.board_id,
        connection_id=row.connection_id,
        connection_name=conn.name if conn else None,
        provider=conn.provider if conn else "mattermost",
        channel_id=row.channel_id,
        channel_name=row.channel_name,
        use_threads=row.use_threads,
        event_allowlist=_loads_list(row.event_allowlist_json),
        tag_mode=row.tag_mode,
        enabled=row.enabled,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def board_channel_allowlist(row: AgentTeamBoardChannel) -> list[str]:
    return _loads_list(row.event_allowlist_json)


# ── user links (connection-scoped) ───────────────────────────────────────────


def list_user_links(db: Session, connection_id: str) -> list[AgentTeamCommUserLink]:
    return (
        db.query(AgentTeamCommUserLink)
        .filter(AgentTeamCommUserLink.connection_id == connection_id)
        .all()
    )


def get_user_link(
    db: Session, *, connection_id: str, user_id: str
) -> AgentTeamCommUserLink | None:
    return (
        db.query(AgentTeamCommUserLink)
        .filter(
            AgentTeamCommUserLink.connection_id == connection_id,
            AgentTeamCommUserLink.user_id == user_id,
        )
        .first()
    )


def upsert_user_link(
    db: Session,
    *,
    connection_id: str,
    user_id: str,
    mm_user_id: str | None,
    mm_username: str | None,
    source: str = LINK_AUTO,
) -> AgentTeamCommUserLink:
    row = get_user_link(db, connection_id=connection_id, user_id=user_id)
    if row is None:
        row = AgentTeamCommUserLink(connection_id=connection_id, user_id=user_id)
        db.add(row)
    row.mm_user_id = mm_user_id or None
    row.mm_username = mm_username or None
    row.source = source
    db.commit()
    db.refresh(row)
    return row


# ── deliveries ───────────────────────────────────────────────────────────────


def get_delivery_by_dedupe(db: Session, dedupe_key: str) -> AgentTeamCommDelivery | None:
    return (
        db.query(AgentTeamCommDelivery)
        .filter(AgentTeamCommDelivery.dedupe_key == dedupe_key)
        .first()
    )


def latest_thread_id(db: Session, *, task_id: str, channel_id: str) -> str | None:
    """Return the most recent provider thread id for a task on a channel."""
    row = (
        db.query(AgentTeamCommDelivery.provider_thread_id)
        .filter(
            AgentTeamCommDelivery.task_id == task_id,
            AgentTeamCommDelivery.channel_id == channel_id,
            AgentTeamCommDelivery.provider_thread_id.isnot(None),
        )
        .order_by(AgentTeamCommDelivery.created_at.desc())
        .first()
    )
    return row[0] if row else None


def create_delivery(
    db: Session,
    *,
    task_id: str | None,
    board_id: str | None,
    channel_id: str | None,
    event_type: str,
    provider: str,
    dedupe_key: str | None,
    payload: dict,
) -> AgentTeamCommDelivery:
    row = AgentTeamCommDelivery(
        task_id=task_id,
        board_id=board_id,
        channel_id=channel_id,
        event_type=event_type,
        provider=provider,
        dedupe_key=dedupe_key,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_deliveries(
    db: Session, *, task_id: str | None = None, board_id: str | None = None, limit: int = 50
) -> list[AgentTeamCommDelivery]:
    q = db.query(AgentTeamCommDelivery)
    if task_id is not None:
        q = q.filter(AgentTeamCommDelivery.task_id == task_id)
    if board_id is not None:
        q = q.filter(AgentTeamCommDelivery.board_id == board_id)
    return q.order_by(AgentTeamCommDelivery.created_at.desc()).limit(limit).all()


def serialize_delivery(row: AgentTeamCommDelivery) -> DeliveryDTO:
    return DeliveryDTO(
        id=row.id,
        task_id=row.task_id,
        board_id=row.board_id,
        channel_id=row.channel_id,
        event_type=row.event_type,
        provider=row.provider,
        provider_message_id=row.provider_message_id,
        provider_thread_id=row.provider_thread_id,
        status=row.status,
        error=row.error,
        created_at=_iso(row.created_at),
        sent_at=_iso(row.sent_at),
    )
