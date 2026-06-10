"""Pydantic request/response models for the board REST API.

These are the wire contract consumed by the web frontend. Response builders
live in ``repositories`` so serialization stays close to the queries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BoardColumn(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)


class BoardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    columns: list[BoardColumn] | None = None


class BoardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    columns: list[BoardColumn] | None = None
    #: Agents staffing this board — tasks only show these agents.
    agent_ids: list[str] | None = None
    archived: bool | None = None
    # ── Jira sync config (write side) ─────────────────────────────────────
    jira_enabled: bool | None = None
    jira_base_url: str | None = Field(default=None, max_length=512)
    jira_email: str | None = Field(default=None, max_length=320)
    #: Token from the form; stored as-is and never echoed back. Omit to keep
    #: the current token; send "" to clear it.
    jira_api_token: str | None = Field(default=None, max_length=512)
    jira_project_key: str | None = Field(default=None, max_length=64)
    jira_mappings: dict | None = None
    #: Which tasks a "sync all" run targets (statuses/task_types/assignee_ids/
    #: exclude_archived). Empty = every linked task.
    jira_sync_filter: dict | None = None


class BoardDTO(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    owner_id: str | None
    columns: list[BoardColumn]
    #: Agents staffing this board — tasks only show these agents.
    agent_ids: list[str] = Field(default_factory=list)
    archived: bool
    task_count: int = 0
    #: The requesting user's role on this board (owner/editor/viewer).
    my_role: str | None = None
    # ── Jira sync config (read side — token never exposed) ─────────────────
    jira_enabled: bool = False
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_project_key: str | None = None
    jira_mappings: dict = Field(default_factory=dict)
    jira_sync_filter: dict = Field(default_factory=dict)
    #: True when an API token is stored (so the UI can show "configured").
    jira_has_token: bool = False
    created_at: str | None
    updated_at: str | None


class BoardMemberDTO(BaseModel):
    board_id: str
    user_id: str
    role: str
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


class AddMemberBody(BaseModel):
    user_id: str | None = None
    email: str | None = None
    role: str = "editor"


class AttemptDTO(BaseModel):
    id: str
    task_id: str
    agent_id: str
    conv_id: str
    attempt: int
    is_active: bool
    created_at: str | None
    title: str | None = None


class TaskCreate(BaseModel):
    #: Optional here so the nested ``/boards/{id}/tasks`` route can omit it; the
    #: flat ``/tasks`` route (used by the web UI) requires it in the body.
    board_id: str | None = Field(default=None, max_length=32)
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, max_length=64)
    task_type: str | None = Field(default=None, max_length=32)
    assignee_id: str | None = Field(default=None, max_length=36)
    labels: list[str] | None = None
    priority: str | None = Field(default=None, max_length=16)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, max_length=64)
    task_type: str | None = Field(default=None, max_length=32)
    assignee_id: str | None = Field(default=None, max_length=36)
    labels: list[str] | None = None
    priority: str | None = Field(default=None, max_length=16)
    archived: bool | None = None


class TaskMove(BaseModel):
    status: str = Field(min_length=1, max_length=64)
    position: float


class TaskDTO(BaseModel):
    id: str
    human_key: str
    board_id: str
    title: str
    description: str | None
    status: str
    position: float
    assignee_id: str | None
    labels: list[str]
    priority: str | None
    task_type: str = "task"
    jira_key: str | None = None
    jira_url: str | None = None
    workspace_path: str
    created_by: str | None
    archived: bool
    created_at: str | None
    updated_at: str | None


class MentionCreate(BaseModel):
    #: Agent identifier; the alias is used as the public id (see ``AgentDTO``).
    agent_id: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20000)
    attachment_ids: list[str] | None = None


class CommentCreate(BaseModel):
    #: May be empty when ``attachments`` carries the whole note (validated in
    #: the route: a note needs text or at least one attachment).
    body: str = Field(default="", max_length=20000)
    attachments: list[dict] | None = None
    #: False = people-only note, excluded from agent context builds.
    visible_to_agents: bool = True


class CommentUpdate(BaseModel):
    """Edit a note (attachments are immutable after posting).

    Both fields are optional so the client can edit the body, toggle agent
    visibility, or do both in one call. An empty body is allowed: an
    attachment-only note keeps a blank body.
    """

    body: str | None = Field(default=None, max_length=20000)
    visible_to_agents: bool | None = None


class TypingBody(BaseModel):
    """Typing-presence ping; ``stop`` clears the indicator for other viewers."""

    state: Literal["start", "stop"] = "start"


class JiraSyncBody(BaseModel):
    """Optional override of the issue key to pull (defaults to the task's)."""

    jira_key: str | None = Field(default=None, max_length=64)


class JiraImportBody(BaseModel):
    """One issue to import (create-or-update a task) during a batch import."""

    jira_key: str = Field(min_length=1, max_length=64)


class JiraPreviewItem(BaseModel):
    """A project issue offered for import, with a peek at its Jira-side fields."""

    jira_key: str
    title: str
    #: Raw Jira names (shown as a fallback when no local mapping exists).
    jira_type: str | None = None
    jira_priority: str | None = None
    #: Mapped to local values so the UI can reuse its own type/priority glyphs.
    task_type: str | None = None
    priority: str | None = None
    #: Display label for the (mapped) status — board column name, else Jira status.
    status: str | None = None
    #: True if a task on this board is already linked to this key (→ "Update").
    exists: bool = False
    #: The linked task's human key (e.g. ``T-12``) when ``exists`` is true.
    human_key: str | None = None


class JiraPreviewResponse(BaseModel):
    items: list[JiraPreviewItem]


class CommentDTO(BaseModel):
    id: str
    task_id: str
    author_id: str | None
    author_name: str | None = None
    author_avatar: str | None = None
    body: str
    attachments: list[dict]
    #: False = people-only note, excluded from agent context builds.
    visible_to_agents: bool = True
    created_at: str | None
    updated_at: str | None


class ActivityDTO(BaseModel):
    id: int
    task_id: str
    actor_id: str | None
    kind: str
    data: dict
    created_at: str | None


class MessageDTO(BaseModel):
    """One transcript turn (user or assistant) rebuilt from run events.

    ``content`` is a list of typed blocks (``text``/``thinking``/``tool_use``/
    ``tool_result``) so the cockpit renders the same timeline as the live SSE
    stream; ``sender_*`` attributes each turn to a board user or the agent.
    """

    seq: int
    role: str
    content: list[dict]
    text: str
    created_at_ms: int
    run_id: str | None = None
    sender_type: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    sender_avatar: str | None = None


class RunDTO(BaseModel):
    id: str
    human_key: str
    task_id: str
    conversation_id: str | None
    #: Public agent identifier (the agent alias).
    agent_id: str
    trigger: str
    actor_id: str | None
    status: str
    prompt: str
    final_answer: str | None
    error: str | None
    #: Aggregate token usage (alias of ``total_tokens`` for the FE contract).
    tokens: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    last_seq: int
    created_at: str | None
    started_at: str | None
    ended_at: str | None


class MentionResponse(BaseModel):
    run: RunDTO
    conversation_id: str
    #: SSE endpoint the client opens to stream this run's trajectory.
    stream_url: str
