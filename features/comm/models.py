"""SQLAlchemy models for the Communication Gateway (v1: outbound only).

Repo-style N-N split (see ``features/repos/models.py`` for the same pattern):

* :class:`AgentTeamCommConnection` — owner-scoped, holds the provider credential
  (bot token, write-only) and is reused across boards.
* :class:`AgentTeamBoardChannel` — the board↔connection link with per-board
  routing (destination channel, event allowlist, tag mode).
* :class:`AgentTeamCommDelivery` — one send attempt for one event on one channel.
* :class:`AgentTeamCommUserLink` — connection-scoped user↔Mattermost mapping used
  to ``@mention`` people in notifications (auto-matched by email, admin override).

Tables follow the ``plugin_agent_team_*`` convention and use only portable
column types; the registry creates them on startup via
``Base.metadata.create(checkfirst=True)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base

#: Provider identifiers.
PROVIDER_MATTERMOST = "mattermost"
PROVIDER_SLACK = "slack"

#: Tag modes for a board channel — who gets ``@mention``-ed on a notification.
TAG_NONE = "none"
TAG_ASSIGNEE = "assignee"
TAG_CREATOR = "creator"
TAG_MODES = (TAG_NONE, TAG_ASSIGNEE, TAG_CREATOR)

#: Delivery lifecycle.
DELIVERY_QUEUED = "queued"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"
DELIVERY_SKIPPED = "skipped"

#: User-link provenance.
LINK_AUTO = "auto"
LINK_MANUAL = "manual"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class AgentTeamCommConnection(Base):
    """An owner-scoped external messaging connection (e.g. a Mattermost bot)."""

    __tablename__ = "plugin_agent_team_comm_connection"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    owner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PROVIDER_MATTERMOST
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    server_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    #: Bot token, stored as-is (same convention as the Jira token); never echoed
    #: back to the client — only its presence is exposed via ``has_token``.
    bot_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Public base URL of this platform, used to build task deep links.
    deep_link_base: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def has_token(self) -> bool:
        """Whether a bot token is stored (without exposing it)."""
        return bool(self.bot_token)


class AgentTeamBoardChannel(Base):
    """Assignment of a connection's channel to a board (many-to-many link)."""

    __tablename__ = "plugin_agent_team_board_channel"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    board_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_board.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_comm_connection.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    use_threads: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: JSON array of event types this channel should receive (empty = none).
    event_allowlist_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: ``none`` / ``assignee`` / ``creator``.
    tag_mode: Mapped[str] = mapped_column(String(16), nullable=False, default=TAG_ASSIGNEE)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class AgentTeamCommDelivery(Base):
    """One attempt to send one notification event through one board channel."""

    __tablename__ = "plugin_agent_team_comm_delivery"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    board_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_board_channel.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    provider: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PROVIDER_MATTERMOST
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DELIVERY_QUEUED)
    #: Unique idempotency key; a repeat event with the same key is skipped.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTeamCommUserLink(Base):
    """Maps an internal user to a provider user, for ``@mention`` resolution.

    Scoped to a connection because the same person has a different provider user
    id on each server. v1 uses this for outbound mentions only; a future
    ``verified`` flag will gate inbound authorization.
    """

    __tablename__ = "plugin_agent_team_comm_user_link"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "user_id", name="uq_agent_team_comm_user_link"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    connection_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_comm_connection.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mm_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mm_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=LINK_AUTO)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
