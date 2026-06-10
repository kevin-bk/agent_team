"""Human-facing key generation (e.g. ``T-142``).

Keys are short, stable, sequential identifiers shown in the UI alongside the
opaque primary keys. The counter lives in ``plugin_agent_team_key_seq`` and is
bumped inside the caller's transaction so concurrent requests cannot reuse a
value (the surrounding ``UPDATE`` is serialized by the database).
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamKeySeq

#: Prefix used for task keys (``T-1``, ``T-2``, ...).
TASK_KEY_PREFIX = "T"

#: Prefix used for run keys (``R-1``, ``R-2``, ...).
RUN_KEY_PREFIX = "R"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def next_human_key(db: Session, prefix: str) -> str:
    """Return the next ``{prefix}-{n}`` key, creating the counter row if needed.

    The caller owns the transaction; this only flushes so the new value is
    visible within the same session before commit.
    """
    row = (
        db.query(AgentTeamKeySeq)
        .filter(AgentTeamKeySeq.prefix == prefix)
        .with_for_update()
        .first()
    )
    if row is None:
        row = AgentTeamKeySeq(prefix=prefix, value=0)
        db.add(row)
        db.flush()
    row.value += 1
    db.flush()
    return f"{prefix}-{row.value}"


def slugify(value: str) -> str:
    """Turn a free-form name into a URL-safe board slug."""
    slug = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug or "board"
