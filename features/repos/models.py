"""SQLAlchemy models for board code repositories.

A *repository* is a first-class, owner-scoped entity (not board-scoped): a board
can use many repos and a repo can be assigned to many boards (many-to-many via
``AgentTeamBoardRepo``).

The plugin keeps one **canonical clone** per repo under the owner's folder and
pulls it on a schedule; each task gets its own cheap working copy
(``git clone --local`` from the canonical). Credentials are stored write-only
(never returned to the client) and are only ever used for the canonical
clone/pull — task copies are local, so the secret never reaches an agent
workspace.

Tables follow the ``plugin_agent_team_*`` naming convention and use only
portable column types; the registry creates them on startup via
``Base.metadata.create(checkfirst=True)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


#: Allowed values for ``AgentTeamRepo.auth_type``.
AUTH_NONE = "none"
AUTH_TOKEN = "token"
AUTH_SSH = "ssh"

#: Allowed values for ``AgentTeamRepo.schedule_mode``.
SCHEDULE_OFF = "off"
SCHEDULE_INTERVAL = "interval"
SCHEDULE_CRON = "cron"

#: Clamp for interval schedules (60s .. 7 days), mirroring the pull scheduler.
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 604800


class AgentTeamRepo(Base):
    """A git repository registered by an owner, cloned + pulled on a schedule."""

    __tablename__ = "plugin_agent_team_repo"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    #: Owner scope — the canonical clone lives under this owner's folder.
    owner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Safe path segment used for the canonical/working-copy folder name.
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    git_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    #: Branch to track; empty/null = the remote's default branch (HEAD).
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── credentials (write-only; never returned to the client) ────────────
    #: One of ``none`` / ``token`` / ``ssh``.
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False, default=AUTH_NONE)
    #: Optional username for HTTPS token auth.
    auth_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: PAT (token) or SSH private key. Stored as-is, same convention as the Jira
    #: token; injected at git-time only (never written into ``.git/config``).
    auth_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── push policy (admin-controlled write access for agents) ─────────────
    #: When false, the ``git_push`` agent tool refuses to push this repo even if
    #: the stored credential has write scope. Admin opts in per repo.
    allow_push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Commit identity applied to a task's working copy (``git config``). Empty
    #: falls back to a generic bot identity. Email matters because hosts map
    #: commits to accounts by email.
    committer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    committer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # ── schedule (per-repo) ───────────────────────────────────────────────
    #: One of ``off`` / ``interval`` / ``cron``.
    schedule_mode: Mapped[str] = mapped_column(String(16), nullable=False, default=SCHEDULE_OFF)
    schedule_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600
    )
    schedule_cron: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── sync state ────────────────────────────────────────────────────────
    #: One of ``absent`` / ``cloning`` / ``cloned`` / ``error``.
    clone_status: Mapped[str] = mapped_column(String(16), nullable=False, default="absent")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: ``ok`` / ``failed`` for the last pull (or clone) attempt.
    last_sync_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Scheduler cursor: the next time a pull is due (advance-first semantics).
    next_pull_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def has_secret(self) -> bool:
        """Whether a credential is stored (without exposing it)."""
        return bool(self.auth_secret)


class AgentTeamBoardRepo(Base):
    """Assignment of a repo to a board (many-to-many)."""

    __tablename__ = "plugin_agent_team_board_repo"
    __table_args__ = (
        UniqueConstraint("board_id", "repo_id", name="uq_agent_team_board_repo"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    board_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_board.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repo_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_repo.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Optional per-board branch override (else the repo's default branch).
    branch_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Per-board push opt-in (set by a board owner/editor). Effective push also
    #: requires the repo's own ``allow_push`` master gate (admin-controlled).
    allow_push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
