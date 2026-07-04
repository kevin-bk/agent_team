"""ORM model for the credential-account registry.

A ``CredentialAccount`` is one authenticated identity for a provider (a Claude
subscription, a Codex ChatGPT account, later a GitHub token). It stores only a
**reference** to the secret material — a host env-var name or a host path — never
the secret itself, so the DB never holds a plaintext credential.

Table name follows the ``plugin_agent_team_*`` convention. New table ⇒ the ORM
auto-creates it on startup (``Base.metadata.create(checkfirst=True)``);
``db_migrations/030_credential_account.sql`` is the idempotent safety net.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class AgentTeamCredentialAccount(Base):
    """One provider account/environment usable by isolated task sandboxes."""

    __tablename__ = "plugin_agent_team_credential_account"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: Provider key mapping to ``registry.PROVIDER_REQUIREMENTS`` (claude/codex/…).
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Injection backend: ``env`` | ``mount`` | ``vault``. Empty = provider default.
    backend: Mapped[str] = mapped_column(String(20), nullable=False, default="")

    #: Reference to the secret material (NOT the secret): e.g.
    #: ``{"secret_env": "CLAUDE_CODE_OAUTH_TOKEN"}`` or ``{"host_path": "/…"}``.
    material_ref_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Load-balancing weight + concurrency cap for the future multi-account pool.
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_concurrency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def material_ref(self) -> dict:
        """Parsed ``material_ref_json`` (``{}`` on empty/invalid)."""
        if not self.material_ref_json:
            return {}
        try:
            data = json.loads(self.material_ref_json)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
