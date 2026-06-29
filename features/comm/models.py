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

#: Inbound action kinds a human can take from chat (v2). ``approve_plan`` parks
#: at ``plan_approved`` only — it never starts execution (``approve_and_run``
#: stays web-only). ``answer_questions`` resumes a paused phase; ``ack_complete``
#: clears a finished loop's state; ``note`` appends a journal note.
ACTION_APPROVE_PLAN = "approve_plan"
ACTION_ANSWER_QUESTIONS = "answer_questions"
ACTION_ACK_COMPLETE = "ack_complete"
ACTION_NOTE = "note"
INBOUND_ACTIONS = (
    ACTION_APPROVE_PLAN,
    ACTION_ANSWER_QUESTIONS,
    ACTION_ACK_COMPLETE,
    ACTION_NOTE,
)

#: Lifecycle of a human action request (the actionable side of a notification).
ACTION_OPEN = "open"
ACTION_RESOLVED = "resolved"
ACTION_EXPIRED = "expired"
ACTION_CANCELLED = "cancelled"

#: Lifecycle of a raw inbound message (stored before interpretation, for debug).
INBOUND_RECEIVED = "received"
INBOUND_PROCESSED = "processed"
INBOUND_IGNORED = "ignored"
INBOUND_ERROR = "error"


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
    #: Gate for inbound authorization (v2): only a verified mapping may act on a
    #: task from chat. Auto-matched-by-email links are considered verified; a
    #: future flow can require explicit admin/self confirmation before trusting.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class AgentTeamExternalThread(Base):
    """Maps a provider conversation thread to the task it is about.

    When an actionable notification is posted, the gateway remembers the thread
    root (Mattermost ``root_id`` / Slack ``thread_ts``) so a later inbound reply
    in that thread can be resolved back to the originating task — purely via the
    DB, so it works across worker processes.
    """

    __tablename__ = "plugin_agent_team_comm_external_thread"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "provider_thread_id", name="uq_agent_team_comm_ext_thread"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    connection_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_comm_connection.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PROVIDER_MATTERMOST
    )
    #: Provider channel id (not the board_channel row) this thread lives in.
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Thread root id used to match replies (Mattermost root_id / Slack thread_ts).
    provider_thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    board_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AgentTeamHumanActionRequest(Base):
    """An outstanding "the human needs to act" request behind a notification.

    Every actionable notification (plan approval needed, blocking questions,
    completion to acknowledge) creates one of these. An inbound reply/button
    resolves it after validation: the gateway looks up the open request for the
    thread, checks the mapped user's authorization, then performs one of the
    allowed actions by calling the same service the cockpit uses.
    """

    __tablename__ = "plugin_agent_team_comm_action_request"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    board_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_comm_connection.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Provider channel id the prompt was posted to.
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Thread root the reply is expected in (links to an external_thread row).
    provider_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PROVIDER_MATTERMOST
    )
    #: The event that triggered this request (e.g. ``plan_approval_required``).
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    #: JSON array of action kinds this request permits (subset of INBOUND_ACTIONS).
    allowed_actions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON object of extra context (e.g. open question ids) for interpretation.
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ACTION_OPEN)
    #: Who resolved it (internal user id), with which action and when.
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolved_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTeamInboundMessage(Base):
    """A raw inbound provider event, stored before interpretation (for debug).

    Persisting the raw message first means a failed interpretation can be
    inspected and replayed, and gives an audit trail of who said what.
    """

    __tablename__ = "plugin_agent_team_comm_inbound_message"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    connection_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_comm_connection.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PROVIDER_MATTERMOST
    )
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: The action request this message resolved, if any.
    action_request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=INBOUND_RECEIVED)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
