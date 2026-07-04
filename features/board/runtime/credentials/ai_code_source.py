"""Resolve a provider's sandbox credential from the AI Code Factory pool.

Rather than maintain a second account registry, the isolated runtime reuses the
existing **AI Code Factory Environment Pool** (``plugins.ai_code``): each Claude /
Codex environment already stores a ``config_dir`` — the subscription login folder
— which is exactly what the ``mount`` backend needs.

Everything here is best-effort and lazy: the ``ai_code`` plugin (and the app DB)
are imported inside the function so this module stays out of light import
closures (e.g. the sidecar image) and never hard-fails a board that simply has
no pool configured.
"""

from __future__ import annotations

import logging

from agent_team.features.board.runtime.credentials.registry import (
    requirements_for,
)
from agent_team.features.board.runtime.credentials.spec import ResolvedAccount

logger = logging.getLogger(__name__)

#: provider → the AI Code Factory environment model class name to read.
#:
#: Only the providers wired in :mod:`registry` belong here. Cursor has a pool
#: (``AICursorEnvironment``) too, but no credential descriptor yet (its egress
#: host set is unverified), so it is intentionally omitted — add it here **and**
#: to ``registry.PROVIDER_REQUIREMENTS`` together when wiring Cursor.
_PROVIDER_MODEL = {
    "claude": "AIClaudeCodeEnvironment",
    "codex": "AICodexEnvironment",
}


def resolve_account_for_provider(provider: str) -> ResolvedAccount | None:
    """Pick an enabled AI Code environment for ``provider`` as a mount account.

    Returns ``None`` (never raises) when the provider is unknown, the ``ai_code``
    plugin is unavailable, or no enabled environment with a ``config_dir`` exists
    — the caller then leaves that provider without injected credentials (it falls
    back to whatever ambient login the image carries, subject to strict-isolation).
    """
    if requirements_for(provider) is None:
        return None
    model_name = _PROVIDER_MODEL.get(provider)
    if model_name is None:
        return None

    try:
        from plugins.ai_code import models as ai_models
    except Exception:  # pragma: no cover — ai_code not installed/enabled
        logger.debug("ai_code plugin unavailable; no pool credential for %s", provider)
        return None

    model = getattr(ai_models, model_name, None)
    if model is None:
        return None

    try:
        from core.database.base import SessionLocal

        with SessionLocal() as db:
            rows = (
                db.query(model)
                .filter(model.enabled.is_(True))
                .order_by(model.weight.desc(), model.name.asc())
                .all()
            )
            for row in rows:
                config_dir = (getattr(row, "config_dir", "") or "").strip()
                if config_dir:
                    return ResolvedAccount(
                        name=str(row.name),
                        provider=provider,
                        backend="mount",
                        material={"host_path": config_dir},
                    )
    except Exception:  # pragma: no cover — DB hiccup must not break prepare
        logger.warning(
            "ai_code pool lookup failed for provider %s", provider, exc_info=True
        )
    return None
