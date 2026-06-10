"""SQLAlchemy models for the Agent Team board feature.

All models use the shared ``core.database.base.Base`` and follow the
``plugin_agent_team_*`` table-naming convention. Column types stay portable
across SQLite (default) and PostgreSQL: structured fields (board columns, task
labels) are stored as JSON text rather than a dialect-specific JSON type.

The registry creates these tables on startup via ``Base.metadata`` +
``create(checkfirst=True)``; later schema changes go through
``db_migrations/*.sql``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


#: Default Kanban columns for a fresh board. Stored per-board as JSON text so a
#: board owner can rename/reorder them without a schema change.
DEFAULT_BOARD_COLUMNS: list[dict[str, str]] = [
    {"key": "pending", "name": "Pending"},
    {"key": "todo", "name": "Todo"},
    {"key": "in_progress", "name": "In Progress"},
    {"key": "review", "name": "Review"},
    {"key": "done", "name": "Done"},
]


class AgentTeamKeySeq(Base):
    """Monotonic counter for human-facing keys (e.g. ``T-142``).

    One row per prefix; the value is incremented inside the caller's
    transaction (see ``keys.next_human_key``).
    """

    __tablename__ = "plugin_agent_team_key_seq"

    prefix: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AgentTeamBoard(Base):
    """A Kanban board: one workflow with its own set of columns."""

    __tablename__ = "plugin_agent_team_board"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: JSON-encoded list of ``{"key", "name"}`` column definitions.
    columns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON-encoded list of agent aliases staffing this board — tasks only
    #: show these agents. Empty (the default) = none until configured.
    agents_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # ── Jira sync (per-board, Phase 1: one-way pull) ──────────────────────
    #: Master switch — when False the board ignores all Jira config.
    jira_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Jira site base URL, e.g. ``https://acme.atlassian.net``.
    jira_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Service-account email used with the API token (Basic auth).
    jira_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    #: API token for Basic auth. Stored as-is (same convention as LLM provider
    #: credentials) and never returned to the client — only its presence is.
    jira_api_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Project key (e.g. ``CHZ``) — scopes the board to one Jira project.
    jira_project_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: JSON object of optional value mappings (status/priority/issuetype).
    jira_mappings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: JSON object describing which tasks a batch ("sync all") run targets.
    jira_sync_filter_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    tasks: Mapped[list[AgentTeamTask]] = relationship(
        back_populates="board",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def columns(self) -> list[dict]:
        """Return decoded column definitions, falling back to the defaults."""
        try:
            value = json.loads(self.columns_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return list(DEFAULT_BOARD_COLUMNS)
        return value if isinstance(value, list) and value else list(DEFAULT_BOARD_COLUMNS)

    def agent_ids(self) -> list[str]:
        """Return the decoded staffing list (empty = board has no agents)."""
        try:
            value = json.loads(self.agents_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    def jira_mappings(self) -> dict:
        """Return the decoded Jira value-mapping object (empty = match by name)."""
        try:
            value = json.loads(self.jira_mappings_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def jira_has_token(self) -> bool:
        """Whether an API token is stored (without exposing the token itself)."""
        return bool(self.jira_api_token)

    def jira_sync_filter(self) -> dict:
        """Return the decoded batch-sync filter (empty = sync every linked task)."""
        try:
            value = json.loads(self.jira_sync_filter_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


class AgentTeamBoardMember(Base):
    """Membership of a user on a board with a role (owner/editor/viewer)."""

    __tablename__ = "plugin_agent_team_board_member"
    __table_args__ = (UniqueConstraint("board_id", "user_id", name="uq_board_member"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    board_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_board.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: One of ``owner``, ``editor``, ``viewer``.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="editor")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AgentTeamTask(Base):
    """A unit of work on a board, with its own shared workspace folder."""

    __tablename__ = "plugin_agent_team_task"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    human_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    board_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_board.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Jira-style issue type (task/story/bug/epic/subtask/agent); UI-driven.
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="task")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="todo", index=True)
    #: Fractional rank within a column so cards can be reordered without
    #: renumbering siblings.
    position: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assignee_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: JSON-encoded list of label strings.
    labels_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Linked Jira issue key (e.g. ``CHZ-123``) and its browse URL, set when the
    #: task is synced from Jira.
    jira_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    jira_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Absolute path of this task's shared workspace folder on the host.
    workspace_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    board: Mapped[AgentTeamBoard] = relationship(back_populates="tasks")

    __table_args__ = (
        UniqueConstraint("board_id", "human_key", name="uq_agent_team_task_board_key"),
    )

    def labels(self) -> list[str]:
        """Return decoded label list."""
        try:
            value = json.loads(self.labels_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []


class AgentTeamConversation(Base):
    """One ``(task, agent)`` thread of work.

    Maps to a checkpointer ``thread_id``. "Reset" archives the current row
    (``is_active=False``) and opens a new ``attempt`` with a fresh thread while
    the task's shared workspace stays in place.
    """

    __tablename__ = "plugin_agent_team_conversation"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "agent_alias", "attempt", name="uq_agent_team_conv_task_agent_attempt"
        ),
        UniqueConstraint("thread_id", name="uq_agent_team_conv_thread"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class AgentTeamRun(Base):
    """One execution of an agent against a task (one turn of a conversation)."""

    __tablename__ = "plugin_agent_team_run"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    human_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_conversation.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    #: How the run was started, e.g. ``mention`` or ``manual``.
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="mention")
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Highest event ``seq`` persisted so far (the SSE resume cursor).
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTeamComment(Base):
    """A human note on a task (soft-deletable)."""

    __tablename__ = "plugin_agent_team_comment"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Display name for non-user authors (e.g. imported Jira commenters).
    external_author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Source Jira comment id — set on imported comments so re-syncs don't
    #: duplicate them. Null for native comments.
    jira_comment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: JSON-encoded list of attachment descriptors.
    attachments_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: When False the note is people-only: shown in the cockpit but excluded
    #: from agent context builds.
    visible_to_agents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def attachments(self) -> list:
        """Return decoded attachment list."""
        try:
            value = json.loads(self.attachments_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return value if isinstance(value, list) else []


class AgentTeamActivity(Base):
    """Changelog entry for a task (Jira-style), human or agent driven."""

    __tablename__ = "plugin_agent_team_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Event kind, e.g. ``task_created``, ``task_moved``, ``comment_added``.
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: JSON-encoded, kind-specific detail (e.g. ``{"field", "from", "to"}``).
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def data(self) -> dict:
        """Return decoded detail payload."""
        try:
            value = json.loads(self.data_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


class AgentTeamRunEvent(Base):
    """Append-only stream frame for a run; ``seq`` is monotonic within the run."""

    __tablename__ = "plugin_agent_team_run_event"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_agent_team_run_event_run_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
