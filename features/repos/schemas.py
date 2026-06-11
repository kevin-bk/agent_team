"""Pydantic request/response models for the repositories REST API.

The credential (``auth_secret``) is **write-only**: it is accepted on
create/update but never returned. DTOs expose only ``has_secret`` so the UI can
render a "configured" state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AuthType = Literal["none", "token", "ssh"]
ScheduleMode = Literal["off", "interval", "cron"]


class RepoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    git_url: str = Field(min_length=1, max_length=1024)
    default_branch: str | None = Field(default=None, max_length=255)
    auth_type: AuthType = "none"
    auth_username: str | None = Field(default=None, max_length=255)
    #: PAT or SSH private key. Stored as-is, never echoed back.
    auth_secret: str | None = Field(default=None, max_length=20000)
    schedule_mode: ScheduleMode = "off"
    schedule_interval_seconds: int = Field(default=3600, ge=60, le=604800)
    schedule_cron: str | None = Field(default=None, max_length=128)
    allow_push: bool = False
    committer_name: str | None = Field(default=None, max_length=255)
    committer_email: str | None = Field(default=None, max_length=320)


class RepoUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    git_url: str | None = Field(default=None, min_length=1, max_length=1024)
    default_branch: str | None = Field(default=None, max_length=255)
    auth_type: AuthType | None = None
    auth_username: str | None = Field(default=None, max_length=255)
    #: Omit to keep the current secret; send "" to clear it.
    auth_secret: str | None = Field(default=None, max_length=20000)
    schedule_mode: ScheduleMode | None = None
    schedule_interval_seconds: int | None = Field(default=None, ge=60, le=604800)
    schedule_cron: str | None = Field(default=None, max_length=128)
    allow_push: bool | None = None
    committer_name: str | None = Field(default=None, max_length=255)
    committer_email: str | None = Field(default=None, max_length=320)
    archived: bool | None = None


class RepoDTO(BaseModel):
    id: str
    owner_id: str | None
    name: str
    slug: str
    git_url: str
    default_branch: str | None
    auth_type: str
    auth_username: str | None
    #: True when a credential is stored (the secret itself is never exposed).
    has_secret: bool = False
    schedule_mode: str
    schedule_interval_seconds: int
    schedule_cron: str | None
    allow_push: bool = False
    committer_name: str | None = None
    committer_email: str | None = None
    clone_status: str
    last_synced_at: str | None
    last_sync_status: str | None
    last_sync_error: str | None
    next_pull_at: str | None
    #: How many boards this repo is assigned to.
    used_by_boards: int = 0
    archived: bool
    created_at: str | None
    updated_at: str | None


class AssignRepoRequest(BaseModel):
    repo_id: str = Field(min_length=1, max_length=32)
    branch_override: str | None = Field(default=None, max_length=255)
    #: Per-board push opt-in. Effective push still needs the repo's master gate.
    allow_push: bool | None = None


class BoardRepoDTO(BaseModel):
    """A repo as seen from a board: the repo plus this board's overrides."""

    repo: RepoDTO
    branch_override: str | None = None
    #: This board's push opt-in for the repo (set by board owner/editor).
    allow_push: bool = False


class RepoStatusDTO(BaseModel):
    repo_id: str
    is_git: bool = False
    branch: str | None = None
    last_commit: str | None = None
    behind: int | None = None
    ahead: int | None = None
    error: str | None = None
