"""REST API for the Communication Gateway (v1: outbound notifications).

Managing connections (server + bot token + user mapping) is **admin-only** and
**owner-scoped**, mirroring the repositories API. Configuring a board's channel
(pick connection, channel, events, tag mode) requires board **owner**.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from agent_team.features.board import authz
from agent_team.features.board.repositories import members as members_repo
from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm import service as comm_service
from agent_team.features.comm.events import V1_EVENT_TYPES
from agent_team.features.comm.models import LINK_AUTO, LINK_MANUAL
from agent_team.features.comm.providers import registry
from agent_team.features.comm.schemas import (
    BoardChannelUpsert,
    ConnectionCreate,
    ConnectionUpdate,
    ProviderDescriptorDTO,
    ProviderFieldDTO,
    TestSendResult,
    UserLinkDTO,
    UserLinkUpsert,
)
from agent_team.web import API_PREFIX, auth_or_401, not_found
from core.database.base import get_db
from core.database.models import User

router = APIRouter(prefix=API_PREFIX, tags=["agent-team-comm"])


def _is_admin(user) -> bool:
    role = getattr(user.role, "value", user.role)
    return str(role).lower() in {"admin", "super_admin"}


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


@router.get("/comm/event-types")
async def list_event_types(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    return {"event_types": list(V1_EVENT_TYPES)}


@router.get("/comm/providers")
async def list_providers(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    out: list[ProviderDescriptorDTO] = []
    for desc in registry.descriptors():
        out.append(
            ProviderDescriptorDTO(
                id=desc.id,
                label=desc.label,
                fields=[
                    ProviderFieldDTO(
                        key=f.key,
                        label=f.label,
                        type=f.type,
                        required=f.required,
                        placeholder=f.placeholder,
                        help=f.help,
                    )
                    for f in desc.fields
                ],
                channel_id_label=desc.channel_id_label,
                channel_id_placeholder=desc.channel_id_placeholder,
                channel_id_help=desc.channel_id_help,
            )
        )
    return out


# ── connections (admin, owner-scoped) ───────────────────────────────────────


@router.get("/comm/connections")
async def list_connections(request: Request, db: Session = Depends(get_db)):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    rows = comm_repo.list_connections(db, owner_id=user.id)
    return [comm_repo.serialize_connection(db, c) for c in rows]


@router.post("/comm/connections")
async def create_connection(
    payload: ConnectionCreate, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    if not registry.is_supported(payload.provider):
        return JSONResponse(
            status_code=422, content={"detail": f"Unsupported provider: {payload.provider}"}
        )
    conn = comm_repo.create_connection(db, owner_id=user.id, payload=payload)
    return comm_repo.serialize_connection(db, conn)


def _owned_connection_or_error(db: Session, connection_id: str, user):
    conn = comm_repo.get_connection(db, connection_id)
    if conn is None:
        return None, not_found("Connection not found")
    if conn.owner_id != user.id:
        return None, _forbidden("Not your connection")
    return conn, None


@router.patch("/comm/connections/{connection_id}")
async def update_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    conn, cerr = _owned_connection_or_error(db, connection_id, user)
    if cerr:
        return cerr
    conn = comm_repo.update_connection(db, conn, payload)
    return comm_repo.serialize_connection(db, conn)


@router.delete("/comm/connections/{connection_id}")
async def delete_connection(
    connection_id: str, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    conn, cerr = _owned_connection_or_error(db, connection_id, user)
    if cerr:
        return cerr
    board_ids = comm_repo.boards_for_connection(db, connection_id)
    if board_ids:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Connection is linked to boards; unlink it first.",
                "board_ids": board_ids,
            },
        )
    comm_repo.delete_connection(db, conn)
    return {"ok": True}


# ── connection user mapping (admin) ─────────────────────────────────────────


def _connection_user_universe(db: Session, connection_id: str) -> list[tuple[User, str]]:
    """Members of every board linked to this connection (deduped), with role."""
    board_ids = comm_repo.boards_for_connection(db, connection_id)
    seen: dict[str, tuple[User, str]] = {}
    for board_id in board_ids:
        for member, user in members_repo.list_members(db, board_id):
            if user.id not in seen:
                seen[user.id] = (user, member.role)
    return list(seen.values())


@router.get("/comm/connections/{connection_id}/user-links")
async def list_user_links(
    connection_id: str, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    conn, cerr = _owned_connection_or_error(db, connection_id, user)
    if cerr:
        return cerr
    links = {
        link.user_id: link for link in comm_repo.list_user_links(db, connection_id)
    }
    out: list[UserLinkDTO] = []
    for member_user, role in _connection_user_universe(db, connection_id):
        link = links.get(member_user.id)
        out.append(
            UserLinkDTO(
                user_id=member_user.id,
                email=member_user.email,
                display_name=member_user.full_name or member_user.username,
                role=role,
                mm_user_id=link.mm_user_id if link else None,
                mm_username=link.mm_username if link else None,
                source=link.source if link else None,
            )
        )
    return out


@router.put("/comm/connections/{connection_id}/user-links")
async def upsert_user_link(
    connection_id: str,
    payload: UserLinkUpsert,
    request: Request,
    db: Session = Depends(get_db),
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    conn, cerr = _owned_connection_or_error(db, connection_id, user)
    if cerr:
        return cerr
    link = comm_repo.upsert_user_link(
        db,
        connection_id=connection_id,
        user_id=payload.user_id,
        mm_user_id=payload.mm_user_id,
        mm_username=payload.mm_username,
        source=LINK_MANUAL,
    )
    return {"ok": True, "mm_username": link.mm_username}


@router.post("/comm/connections/{connection_id}/user-links/auto-match")
async def auto_match_user_links(
    connection_id: str, request: Request, db: Session = Depends(get_db)
):
    user, err = auth_or_401(db, request)
    if err:
        return err
    if not _is_admin(user):
        return _forbidden("Admin only")
    conn, cerr = _owned_connection_or_error(db, connection_id, user)
    if cerr:
        return cerr
    if not conn.has_token():
        return JSONResponse(
            status_code=422, content={"detail": "Connection has no bot token"}
        )

    def _run() -> int:
        provider = registry.get_provider(conn.provider)
        matched = 0
        for member_user, _role in _connection_user_universe(db, connection_id):
            existing = comm_repo.get_user_link(
                db, connection_id=connection_id, user_id=member_user.id
            )
            if existing and existing.source == LINK_MANUAL:
                continue  # never overwrite a manual override
            if not member_user.email:
                continue
            mm_user_id, mm_username = provider.resolve_username(
                server_url=conn.server_url,
                bot_token=conn.bot_token or "",
                email=member_user.email,
            )
            if mm_username:
                comm_repo.upsert_user_link(
                    db,
                    connection_id=connection_id,
                    user_id=member_user.id,
                    mm_user_id=mm_user_id,
                    mm_username=mm_username,
                    source=LINK_AUTO,
                )
                matched += 1
        return matched

    matched = await asyncio.to_thread(_run)
    return {"ok": True, "matched": matched}


# ── board channel (board owner) ─────────────────────────────────────────────


@router.get("/boards/{board_id}/channel")
async def get_board_channel(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    ctx, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
    row = comm_repo.get_board_channel(db, board_id)
    channel = comm_repo.serialize_board_channel(db, row) if row else None
    # Connections available to link are the caller's own (admins) — but a board
    # owner who is not the connection owner still needs to pick one, so we expose
    # the connections owned by the board's owner plus the caller's own.
    available = [
        comm_repo.serialize_connection(db, c)
        for c in comm_repo.list_connections(db, owner_id=ctx.user.id)
    ]
    return {"channel": channel, "available_connections": available}


@router.put("/boards/{board_id}/channel")
async def put_board_channel(
    board_id: str,
    payload: BoardChannelUpsert,
    request: Request,
    db: Session = Depends(get_db),
):
    _, err = authz.guard_board(db, request, board_id, min_role="owner")
    if err:
        return err
    conn = comm_repo.get_connection(db, payload.connection_id)
    if conn is None:
        return not_found("Connection not found")
    row = comm_repo.upsert_board_channel(
        db,
        board_id=board_id,
        connection_id=payload.connection_id,
        channel_id=payload.channel_id,
        channel_name=payload.channel_name,
        use_threads=payload.use_threads,
        event_allowlist=[e for e in payload.event_allowlist if e in V1_EVENT_TYPES],
        tag_mode=payload.tag_mode,
        enabled=payload.enabled,
    )
    return comm_repo.serialize_board_channel(db, row)


@router.delete("/boards/{board_id}/channel")
async def delete_board_channel(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = authz.guard_board(db, request, board_id, min_role="owner")
    if err:
        return err
    comm_repo.delete_board_channel(db, board_id)
    return {"ok": True}


@router.post("/boards/{board_id}/channel/test")
async def test_board_channel(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    _, err = authz.guard_board(db, request, board_id, min_role="owner")
    if err:
        return err
    row = comm_repo.get_board_channel(db, board_id)
    if row is None:
        return JSONResponse(status_code=422, content={"detail": "No channel configured"})
    conn = comm_repo.get_connection(db, row.connection_id)
    if conn is None or not conn.has_token():
        return JSONResponse(
            status_code=422, content={"detail": "Connection is not fully configured"}
        )
    ok, message_id, error = await asyncio.to_thread(
        comm_service.test_send,
        server_url=conn.server_url,
        bot_token=conn.bot_token or "",
        channel_id=row.channel_id,
        provider=conn.provider,
    )
    return TestSendResult(ok=ok, provider_message_id=message_id, error=error)


@router.get("/boards/{board_id}/channel/deliveries")
async def list_board_deliveries(
    board_id: str, request: Request, db: Session = Depends(get_db)
):
    ctx, err = authz.guard_board(db, request, board_id, min_role="viewer")
    if err:
        return err
    rows = comm_repo.list_deliveries(db, board_id=board_id, limit=50)
    return [comm_repo.serialize_delivery(r) for r in rows]
