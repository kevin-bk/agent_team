"""Pydantic request/response models for the credential-account REST API.

No secret ever transits this API: ``material_ref`` holds only *references*
(a host env-var name or a host path). The DTO adds a computed ``ready`` flag so
the UI can show whether the referenced material actually resolves on the host.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CredentialAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    provider: str = Field(min_length=1, max_length=40)
    #: "" (provider default) | "env" | "mount" | "vault".
    backend: str = Field(default="", max_length=20)
    #: Reference only, e.g. {"secret_env": "..."} or {"host_path": "..."}.
    material_ref: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    weight: int = Field(default=1, ge=1, le=1000)
    max_concurrency: int = Field(default=1, ge=1, le=1000)


class CredentialAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    provider: str | None = Field(default=None, min_length=1, max_length=40)
    backend: str | None = Field(default=None, max_length=20)
    material_ref: dict[str, str] | None = None
    enabled: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=1000)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)


class CredentialAccountDTO(BaseModel):
    id: str
    name: str
    description: str
    provider: str
    #: The backend as stored (may be "").
    backend: str
    #: The backend that will actually run (provider default when ``backend`` is "").
    effective_backend: str
    material_ref: dict[str, str]
    enabled: bool
    weight: int
    max_concurrency: int
    #: True when the referenced material resolves on the host right now
    #: (env var set, or host path exists). Lets the UI flag misconfiguration.
    ready: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class CredentialProviderInfo(BaseModel):
    """Static metadata to drive the create/edit form (providers + backends)."""

    provider: str
    label: str
    #: Backends valid for this provider, first = default.
    backends: list[str]
    #: Which material_ref keys this provider expects, e.g. ["secret_env"].
    material_keys: list[str]
