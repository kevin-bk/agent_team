"""Pydantic request/response models for the Communication Gateway REST API.

The bot token is **write-only**: accepted on create/update but never returned.
DTOs expose only ``has_token`` so the UI can render a "configured" state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["mattermost", "slack"]
TagMode = Literal["none", "assignee", "creator"]


# ── connections (owner-scoped registry) ─────────────────────────────────────


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: Provider = "mattermost"
    #: Optional: Mattermost needs it, Slack ignores it (uses the public API).
    server_url: str = Field(default="", max_length=1024)
    #: Bot token. Stored as-is, never echoed back.
    bot_token: str | None = Field(default=None, max_length=4096)
    default_team_id: str | None = Field(default=None, max_length=64)
    deep_link_base: str | None = Field(default=None, max_length=1024)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    server_url: str | None = Field(default=None, max_length=1024)
    #: Omit to keep the current token; send "" to clear it.
    bot_token: str | None = Field(default=None, max_length=4096)
    default_team_id: str | None = Field(default=None, max_length=64)
    deep_link_base: str | None = Field(default=None, max_length=1024)
    archived: bool | None = None


class ConnectionDTO(BaseModel):
    id: str
    owner_id: str | None
    provider: str
    name: str
    server_url: str
    #: True when a bot token is stored (the token itself is never exposed).
    has_token: bool = False
    default_team_id: str | None = None
    deep_link_base: str | None = None
    archived: bool = False
    #: How many boards link to this connection.
    used_by_boards: int = 0
    created_at: str | None = None
    updated_at: str | None = None


# ── board channel (board↔connection link) ───────────────────────────────────


class BoardChannelUpsert(BaseModel):
    connection_id: str = Field(min_length=1, max_length=32)
    channel_id: str = Field(min_length=1, max_length=64)
    channel_name: str | None = Field(default=None, max_length=255)
    use_threads: bool = True
    event_allowlist: list[str] = Field(default_factory=list)
    tag_mode: TagMode = "assignee"
    enabled: bool = True


class BoardChannelDTO(BaseModel):
    id: str
    board_id: str
    connection_id: str
    #: Connection name, for display without a second fetch.
    connection_name: str | None = None
    provider: str = "mattermost"
    channel_id: str
    channel_name: str
    use_threads: bool = True
    event_allowlist: list[str] = Field(default_factory=list)
    tag_mode: str = "assignee"
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None


# ── user mapping (connection-scoped) ─────────────────────────────────────────


class UserLinkDTO(BaseModel):
    user_id: str
    email: str | None = None
    display_name: str | None = None
    role: str | None = None
    mm_user_id: str | None = None
    mm_username: str | None = None
    source: str | None = None


class UserLinkUpsert(BaseModel):
    user_id: str = Field(min_length=1, max_length=36)
    mm_username: str | None = Field(default=None, max_length=255)
    mm_user_id: str | None = Field(default=None, max_length=64)


# ── deliveries ───────────────────────────────────────────────────────────────


class DeliveryDTO(BaseModel):
    id: str
    task_id: str | None = None
    board_id: str | None = None
    channel_id: str | None = None
    event_type: str
    provider: str
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    status: str
    error: str | None = None
    created_at: str | None = None
    sent_at: str | None = None


class TestSendResult(BaseModel):
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None


# ── provider descriptors (drive the connection/channel forms in the UI) ──────


class ProviderFieldDTO(BaseModel):
    key: str
    label: str
    type: str = "text"
    required: bool = False
    placeholder: str = ""
    help: str = ""


class ProviderDescriptorDTO(BaseModel):
    id: str
    label: str
    fields: list[ProviderFieldDTO] = Field(default_factory=list)
    channel_id_label: str = "Channel ID"
    channel_id_placeholder: str = ""
    channel_id_help: str = ""
