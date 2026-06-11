"""Schedule math for repo auto-pull (interval / cron).

Kept dependency-light: ``croniter`` is already vendored by sibling plugins, so
no new package is required. ``compute_next_pull_at`` is used both when a repo's
schedule changes (to seed ``next_pull_at``) and by the ticker (to advance it).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_team.features.repos.models import (
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    SCHEDULE_CRON,
    SCHEDULE_INTERVAL,
)


def clamp_interval(seconds: int | None) -> int:
    try:
        n = int(seconds if seconds is not None else 3600)
    except (TypeError, ValueError):
        n = 3600
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, n))


def is_valid_cron(expr: str | None) -> bool:
    expr = (expr or "").strip()
    if not expr:
        return False
    try:
        from croniter import croniter
    except ImportError:
        return False
    return bool(croniter.is_valid(expr))


def compute_next_pull_at(
    *,
    mode: str,
    interval_seconds: int | None,
    cron: str | None,
    base: datetime | None = None,
) -> datetime | None:
    """Return the next due time for a schedule, or ``None`` when not scheduled."""
    now = base or datetime.now(UTC)
    if mode == SCHEDULE_INTERVAL:
        return now + timedelta(seconds=clamp_interval(interval_seconds))
    if mode == SCHEDULE_CRON:
        expr = (cron or "").strip()
        if not is_valid_cron(expr):
            return None
        from croniter import croniter

        return croniter(expr, now).get_next(datetime)
    return None
