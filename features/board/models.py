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

#: How a task is executed. ``chat`` = interactive single turns (the default);
#: ``autonomous`` = the loop layer drives generator turns + independent
#: evaluation until the objective is met or a guardrail stops it.
TASK_EXEC_MODE_CHAT = "chat"
TASK_EXEC_MODE_AUTONOMOUS = "autonomous"
TASK_EXEC_MODES = frozenset({TASK_EXEC_MODE_CHAT, TASK_EXEC_MODE_AUTONOMOUS})

#: How a task's plan is produced before autonomous execution.
#: ``legacy_plan`` = the lightweight best-effort planner (writes PLAN.md inside
#: the loop, fails open to the raw objective). ``strict_plan`` = a contract-
#: driven phase that drafts durable artifacts and requires human approval before
#: any execution starts.
PLANNING_MODE_LEGACY = "legacy_plan"
PLANNING_MODE_STRICT = "strict_plan"
PLANNING_MODES = frozenset({PLANNING_MODE_LEGACY, PLANNING_MODE_STRICT})

#: Why a run was executed (its stage in the autonomous loop). A plain chat/mention
#: run is ``chat``; the loop tags its runs ``planner`` / ``generator`` /
#: ``evaluator``. Stored in a plain VARCHAR(16) (no CHECK), so new roles need no
#: migration.
RUN_ROLE_CHAT = "chat"
RUN_ROLE_PLANNER = "planner"
RUN_ROLE_GENERATOR = "generator"
RUN_ROLE_EVALUATOR = "evaluator"

#: Lifecycle of one loop attempt.
ATTEMPT_RUNNING = "running"
ATTEMPT_DONE = "done"

#: Independent evaluator verdicts for an attempt.
EVAL_PASS = "pass"
EVAL_FAIL = "fail"
EVAL_NEEDS_HUMAN = "needs_human"

#: Allowed values for ``AgentTeamAutopilot.schedule_mode``.
AUTOPILOT_SCHEDULE_OFF = "off"
AUTOPILOT_SCHEDULE_INTERVAL = "interval"
AUTOPILOT_SCHEDULE_CRON = "cron"

#: Clamp for autopilot interval schedules (60s .. 7 days).
AUTOPILOT_MIN_INTERVAL_SECONDS = 60
AUTOPILOT_MAX_INTERVAL_SECONDS = 604800

#: Task Journal — who authored a journal entry.
JOURNAL_ACTOR_HUMAN = "human"
JOURNAL_ACTOR_AGENT = "agent"
JOURNAL_ACTOR_SYSTEM = "system"
JOURNAL_ACTORS = frozenset({JOURNAL_ACTOR_HUMAN, JOURNAL_ACTOR_AGENT, JOURNAL_ACTOR_SYSTEM})

#: Severity of a journal entry (blocking entries should stand out in the UI).
JOURNAL_SEVERITY_INFO = "info"
JOURNAL_SEVERITY_WARNING = "warning"
JOURNAL_SEVERITY_BLOCKING = "blocking"
JOURNAL_SEVERITIES = frozenset(
    {JOURNAL_SEVERITY_INFO, JOURNAL_SEVERITY_WARNING, JOURNAL_SEVERITY_BLOCKING}
)

#: Controlled entry types. Stored as plain VARCHAR (no CHECK) so new types need
#: no migration; validation lives in the repository/helper layer.
JOURNAL_TYPES = frozenset(
    {
        "decision",
        "assumption",
        "question",
        "answer",
        "approval",
        "plan_review",
        "plan_change",
        "verdict",
        "state_change",
        "risk",
        "friction",
        "note",
        "artifact_update",
        "task_progress",
        "summary",
        "correction",
    }
)

#: Controlled lifecycle phases a journal entry can belong to.
JOURNAL_PHASES = frozenset(
    {
        "intake",
        "planning",
        "review",
        "approval",
        "execution",
        "verification",
        "change_request",
        "result",
        "system",
    }
)


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
    #: JSON-encoded list of direct-CLI aliases (``cli:<engine>``) enabled on this
    #: board — tasks only show these CLIs. Empty (the default) = none.
    cli_targets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON object mapping a direct-CLI alias (``cli:<engine>``) to its own MCP
    #: config (``{"mcpServers": {...}}``), so each CLI agent on the board can
    #: connect to a different set of MCP servers. Empty object = no per-agent MCP.
    agent_mcp_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: JSON-encoded list of skill pack names made available to direct-CLI agents
    #: on this board (materialised into each task workspace). Empty = no skills.
    skills_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON object overriding the isolated-runtime profile for this board (shaped
    #: like ``RuntimeProfile``: provider/image/cpu/memory/idle_timeout_minutes/
    #: strict_isolation/workspace_mode/...). Empty object = use env defaults.
    runtime_profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: Optional reusable "starter" chat message for this board. When set, a task's
    #: chat shows a one-click button to send it as the first message of a new
    #: conversation (handy for direct-CLI tasks that all start the same way).
    starter_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
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
    #: When True (default), a Jira sync overwrites the local task status with the
    #: mapped Jira status. Turn off to keep the board status under local control
    #: while still syncing the other fields.
    jira_sync_status: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
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

    def cli_target_ids(self) -> list[str]:
        """Return the enabled direct-CLI aliases (empty = no CLI on this board)."""
        try:
            value = json.loads(self.cli_targets_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    def skill_ids(self) -> list[str]:
        """Return the skill pack names enabled for direct-CLI agents (empty = none)."""
        try:
            value = json.loads(self.skills_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in value] if isinstance(value, list) else []

    def runtime_profile(self) -> dict:
        """Return the decoded runtime-profile override (empty = use env defaults)."""
        try:
            value = json.loads(self.runtime_profile_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def agent_mcp(self) -> dict[str, dict]:
        """Return the full ``alias -> mcp_config`` map (empty = no per-agent MCP)."""
        try:
            value = json.loads(self.agent_mcp_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(k): v for k, v in value.items() if isinstance(v, dict)}

    def agent_mcp_for(self, alias: str) -> dict:
        """Return the MCP config for one CLI alias (empty = none configured)."""
        cfg = self.agent_mcp().get(alias)
        return cfg if isinstance(cfg, dict) else {}

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
    #: Cached Jira accountId for this member (resolved by user-search on their
    #: email). Used to map an issue's assignee/reporter back to this user even
    #: when Jira hides the account email on issue payloads.
    jira_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    #: Acceptance criteria for autonomous execution — what "done" means. The loop
    #: layer's controller seeds the first prompt from this and the evaluator grades
    #: against it. Null for plain chat tasks.
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``chat`` (default, interactive single turns) or ``autonomous`` (loop layer).
    execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TASK_EXEC_MODE_CHAT
    )
    #: Lifecycle state of the autonomous loop (running / complete /
    #: waiting_for_human / failed / cancelled). Null for plain chat tasks.
    loop_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: How this task's plan is produced (``legacy_plan`` or ``strict_plan``).
    #: Defaults to legacy so existing tasks keep their current behaviour.
    planning_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PLANNING_MODE_LEGACY
    )
    #: Backend-owned planning metadata (approval flag/by/at, approved artifact
    #: etags, last reviewer verdict, last error). Authoritative and never written
    #: by an agent — distinct from the artifact files an agent produces on disk.
    planning_meta_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    #: Jira-style issue type (task/story/bug/epic/subtask/agent); UI-driven.
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="task")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="todo", index=True)
    #: Fractional rank within a column so cards can be reordered without
    #: renumbering siblings.
    position: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assignee_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Human reporter (who raised the issue), set from Jira's reporter on sync by
    #: matching the Jira account email to a local user. Distinct from
    #: ``created_by`` (whoever created the task row in our system).
    reporter_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Agent (or direct-CLI) alias this task is assigned to, e.g. an agent id or
    #: ``cli:claude``. Distinct from ``assignee_id`` (a human user): the board
    #: autopilot only auto-picks tasks whose ``agent_assignee`` is set, and routes
    #: each to that agent. Null = no agent owns it (autopilot ignores it).
    agent_assignee: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    #: Autopilot back-off cursor: when set and in the future, the autopilot skips
    #: this task (set after a failed auto-run so it isn't retried immediately).
    autopilot_resume_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Count of consecutive failed auto-runs; cleared on any human status change.
    #: Once it hits the autopilot's ``max_attempts`` the task is left alone.
    autopilot_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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

    def planning_meta(self) -> dict:
        """Return decoded planning metadata (approval, etags, verdict, error)."""
        try:
            value = json.loads(self.planning_meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


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
    #: Stage in the autonomous loop: ``chat`` (default), ``planner``,
    #: ``generator`` or ``evaluator``. Lets the cockpit label loop runs and the
    #: loop layer query them.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=RUN_ROLE_CHAT)
    #: The loop attempt this run belongs to (null for chat runs).
    attempt_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_attempt.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
    #: Direct-CLI context-window gauge text (e.g. "45,000/200,000 tokens"),
    #: captured at the end of the run so the cockpit can display it after the
    #: live stream ends. Null for non-CLI runs (or CLI runs with no gauge).
    cli_usage_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
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


class AgentTeamToolOutput(Base):
    """Full text of one tool result, stored out of the event/SSE stream.

    The streamed ``tool_use_end`` frame keeps only a short ``output_preview``
    (and a ``truncated`` flag) so the timeline and SSE replay stay light. The
    complete output lives here, keyed by ``(run_id, tool_id)``, and is fetched
    on demand when the user expands a tool card — so large outputs never bloat
    the live stream yet remain fully readable later.
    """

    __tablename__ = "plugin_agent_team_tool_output"

    run_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_run.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: Per-run tool id assigned by the stream translator (e.g. ``t3``).
    tool_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AgentTeamAttempt(Base):
    """One iteration of the autonomous loop: a generator turn and its evaluation.

    Attempts are numbered per task (``attempt_no``). The loop opens an attempt,
    runs the generator, evaluates it, and either continues (a new attempt) or
    finishes. Runs and evaluations reference their attempt.
    """

    __tablename__ = "plugin_agent_team_attempt"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_agent_team_attempt_task_no"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ATTEMPT_RUNNING
    )
    #: ``complete`` / ``capped`` / ``needs_human`` / ``failed`` once the loop ends
    #: (the attempt that ended the loop carries the outcome). Null while running.
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTeamEvaluation(Base):
    """An independent verdict on one attempt: did it meet the objective?

    Produced by an evaluator run separate from the generator (so an agent never
    grades its own work). ``evidence_json`` records what the evaluator checked.
    """

    __tablename__ = "plugin_agent_team_evaluation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_attempt.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The evaluator run that produced this verdict (null if graded inline).
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: One of ``pass`` / ``fail`` / ``needs_human``.
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, default=EVAL_FAIL)
    #: Confidence 0..1.
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: What remains to be done (fed back into the next generator turn).
    missing: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: JSON object of evidence (commands run, checks, outputs).
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def evidence(self) -> dict:
        """Return the decoded evidence payload (empty on error)."""
        try:
            value = json.loads(self.evidence_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


class AgentTeamJournalEntry(Base):
    """One append-only entry in a task's semantic journal ("sổ cái").

    The journal is a curated, durable timeline of meaningful moments — decisions,
    assumptions, questions, approvals, plan changes, verification verdicts and
    human/agent notes — that a future human or agent needs to understand why the
    task went the way it did. It is written mostly by the backend at lifecycle
    points (so it stays complete regardless of any agent's context compaction)
    and is intentionally distinct from raw run events (replay), ``EVIDENCE.json``
    (verification record) and ``task.loop_state`` (lifecycle); entries reference
    those sources through ``refs`` rather than replacing them.

    Entries are append-only: a wrong entry is corrected by appending a
    ``correction`` entry that points at the original via ``supersedes_id``.
    ``seq`` is a task-local monotonic order assigned by the repository.
    """

    __tablename__ = "plugin_agent_team_journal_entry"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Task-local monotonic sequence for stable ordering/pagination.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: ``human`` / ``agent`` / ``system``.
    actor_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JOURNAL_ACTOR_SYSTEM
    )
    #: User id (human note) or agent alias (agent note); null for system events.
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default="system")
    type: Mapped[str] = mapped_column(String(24), nullable=False, default="note")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JOURNAL_SEVERITY_INFO
    )
    #: JSON object of references (run_id, attempt_id, artifacts, files, etags).
    refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: JSON object of free-form metadata (score, counts, guardrail name, ...).
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    #: Id of the entry this one corrects/supersedes, if any.
    supersedes_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def refs(self) -> dict:
        """Return the decoded references payload (empty on error)."""
        try:
            value = json.loads(self.refs_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def meta(self) -> dict:
        """Return the decoded metadata payload (empty on error)."""
        try:
            value = json.loads(self.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


class AgentTeamAutopilot(Base):
    """Per-board auto-pilot: on a schedule, claim assigned tasks and run them.

    One row per board (``board_id`` is the primary key). When ``enabled`` and a
    schedule is set, a background ticker periodically scans the board's
    ``source_status`` column for tasks that carry an ``agent_assignee``, claims
    each by moving it to ``working_status``, and starts an agent run. On
    completion the run's task is moved to ``done_status`` (success) or
    ``error_status`` (failure). All status fields hold a board *column key*
    (stable across column renames), not a display label.
    """

    __tablename__ = "plugin_agent_team_autopilot"

    board_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_board.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── schedule ──────────────────────────────────────────────────────────
    #: One of ``off`` / ``interval`` / ``cron``.
    schedule_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AUTOPILOT_SCHEDULE_OFF
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    cron: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    # ── status mapping (board column *keys*) ──────────────────────────────
    source_status: Mapped[str] = mapped_column(String(64), nullable=False, default="todo")
    working_status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="in_progress"
    )
    done_status: Mapped[str] = mapped_column(String(64), nullable=False, default="review")
    error_status: Mapped[str] = mapped_column(String(64), nullable=False, default="todo")

    # ── concurrency (two layers) ──────────────────────────────────────────
    #: Max autopilot runs in flight across the whole board.
    board_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    #: Per-agent cap applied when no explicit override exists in
    #: ``agent_concurrency_json``.
    default_agent_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: JSON object ``{agent_alias: max_in_flight}`` overriding the default.
    agent_concurrency_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # ── failure handling ──────────────────────────────────────────────────
    #: Seconds to back off a task after a failed auto-run before retrying.
    error_cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    #: After this many consecutive failures the task is left alone (no retry).
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    #: Optional instruction seeded into each auto-run's first prompt. Empty falls
    #: back to a built-in default that points the agent at ``.agent-team/TASK.md``.
    prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── routing (manual "auto-assign" trigger only — never runs on the tick) ──
    #: Ordered rules ``[{labels, priorities, agents}]``. First rule whose
    #: conditions match an unassigned source-column task wins; its ``agents`` list
    #: is round-robined within the rule. Applied only when a user clicks
    #: "Auto-assign", never automatically by the scheduler.
    routing_rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: Per-rule round-robin cursor ``{rule_index: next_offset}``.
    routing_rr_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # ── scheduler cursor ──────────────────────────────────────────────────
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def agent_concurrency(self) -> dict[str, int]:
        """Return the decoded per-agent concurrency overrides (empty on error)."""
        try:
            value = json.loads(self.agent_concurrency_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        out: dict[str, int] = {}
        for alias, cap in value.items():
            try:
                out[str(alias)] = max(0, int(cap))
            except (TypeError, ValueError):
                continue
        return out

    def concurrency_for(self, agent_alias: str) -> int:
        """Effective in-flight cap for one agent (override else the default)."""
        return self.agent_concurrency().get(agent_alias, self.default_agent_concurrency)

    def routing_rules(self) -> list[dict]:
        """Return the decoded routing rules (each ``{labels, priorities, agents}``)."""
        try:
            value = json.loads(self.routing_rules_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(value, list):
            return []
        out: list[dict] = []
        for rule in value:
            if not isinstance(rule, dict):
                continue
            out.append(
                {
                    "labels": [str(x) for x in (rule.get("labels") or [])],
                    "priorities": [str(x) for x in (rule.get("priorities") or [])],
                    "agents": [str(x) for x in (rule.get("agents") or [])],
                }
            )
        return out

    def routing_rr(self) -> dict[str, int]:
        """Return the decoded per-rule round-robin cursors (empty on error)."""
        try:
            value = json.loads(self.routing_rr_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        out: dict[str, int] = {}
        for key, idx in value.items():
            try:
                out[str(key)] = int(idx)
            except (TypeError, ValueError):
                continue
        return out


#: How a scheduled run reuses the agent's thread.
TASK_SCHEDULE_MODE_NEW = "new"  # start a fresh conversation each time
TASK_SCHEDULE_MODE_CONTINUE = "continue"  # append to the existing conversation
TASK_SCHEDULE_MODES = frozenset({TASK_SCHEDULE_MODE_NEW, TASK_SCHEDULE_MODE_CONTINUE})


class AgentTeamTaskSchedule(Base):
    """Per-task cron schedule that fires a recurring agent run.

    One row per task (``task_id`` is the primary key). When ``enabled`` and a
    valid ``cron`` is set, a background ticker fires at each due time: it picks
    ``agent_alias`` and sends ``prompt`` as the opening message, either starting
    a fresh conversation (``conversation_mode == "new"``) or appending to the
    agent's existing thread (``"continue"``). Unlike the board autopilot, a
    scheduled run never moves the task between columns. If the previous scheduled
    run is still in flight when the next tick is due, the tick is skipped.
    """

    __tablename__ = "plugin_agent_team_task_schedule"

    task_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plugin_agent_team_task.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Five-field cron expression evaluated in ``timezone``.
    cron: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    #: Agent (or direct-CLI) alias that runs the task each time it fires.
    agent_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Opening message sent to the agent on each fire.
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``new`` (fresh conversation each time) or ``continue`` (append to thread).
    conversation_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TASK_SCHEDULE_MODE_CONTINUE
    )

    # ── scheduler cursor / last-fire bookkeeping ──────────────────────────
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Id of the run started by the most recent successful fire (for the UI).
    last_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
