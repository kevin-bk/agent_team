"""Resolve which Mattermost usernames to ``@mention`` for an event.

Tagging is best-effort and outbound-only (no authorization implication). A user
is matched to a Mattermost account by **email**: we first consult the cached
``AgentTeamCommUserLink``; on a miss we ask the provider once (by email) and
cache the result. No match → the user is simply not mentioned.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm.events import EVENT_GOAL_COMPLETE
from agent_team.features.comm.models import (
    LINK_AUTO,
    TAG_ASSIGNEE,
    TAG_CREATOR,
    TAG_NONE,
    AgentTeamBoardChannel,
    AgentTeamCommConnection,
)
from agent_team.features.comm.providers.base import Mention
from agent_team.features.comm.providers.registry import get_provider
from agent_team.features.comm.refs import TaskRef
from core.database.models import User

logger = logging.getLogger(__name__)


def _target_user_ids(
    *, event_type: str, tag_mode: str, task: TaskRef
) -> list[str]:
    """Pick which internal user ids to mention for this event + channel mode."""
    if tag_mode == TAG_NONE:
        return []
    ids: list[str] = []
    # ``complete`` always tags both the assignee and the reporter, per product
    # decision; other events follow the channel's tag_mode.
    if event_type == EVENT_GOAL_COMPLETE:
        ids = [task.assignee_id, task.reporter_id or task.created_by]
    elif tag_mode == TAG_CREATOR:
        ids = [task.reporter_id or task.created_by]
    elif tag_mode == TAG_ASSIGNEE:
        ids = [task.assignee_id]
    seen: list[str] = []
    for uid in ids:
        if uid and uid not in seen:
            seen.append(uid)
    return seen


def _resolve_mention(
    db: Session,
    conn: AgentTeamCommConnection,
    user: User,
) -> Mention | None:
    """Cached → provider lookup → cache. Returns a :class:`Mention` or ``None``.

    Carries both the provider user id and handle so each provider can format the
    mention its own way (Mattermost ``@handle`` vs Slack ``<@id>``).
    """
    link = comm_repo.get_user_link(db, connection_id=conn.id, user_id=user.id)
    if link and (link.mm_username or link.mm_user_id):
        return Mention(user_id=link.mm_user_id, handle=link.mm_username)
    if not user.email or not conn.has_token():
        return None
    provider_user_id, provider_username = get_provider(conn.provider).resolve_username(
        server_url=conn.server_url, bot_token=conn.bot_token or "", email=user.email
    )
    if not provider_username and not provider_user_id:
        return None
    try:
        comm_repo.upsert_user_link(
            db,
            connection_id=conn.id,
            user_id=user.id,
            mm_user_id=provider_user_id,
            mm_username=provider_username,
            source=LINK_AUTO,
        )
    except Exception:  # pragma: no cover - caching is best-effort
        logger.debug("comm: failed to cache user link", exc_info=True)
    return Mention(user_id=provider_user_id, handle=provider_username)


def resolve_mentions(
    db: Session,
    *,
    event_type: str,
    channel: AgentTeamBoardChannel,
    connection: AgentTeamCommConnection,
    task: TaskRef,
) -> list[Mention]:
    """Return the people to mention (may be empty), provider-formatted later."""
    user_ids = _target_user_ids(
        event_type=event_type, tag_mode=channel.tag_mode, task=task
    )
    if not user_ids:
        return []
    mentions: list[Mention] = []
    seen: set[str] = set()
    for uid in user_ids:
        user = db.query(User).filter(User.id == uid).first()
        if user is None:
            continue
        mention = _resolve_mention(db, connection, user)
        if mention is None:
            continue
        dedupe = mention.user_id or mention.handle or ""
        if dedupe and dedupe not in seen:
            seen.add(dedupe)
            mentions.append(mention)
    return mentions
