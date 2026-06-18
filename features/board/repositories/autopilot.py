"""Autopilot config queries and serialization.

One :class:`AgentTeamAutopilot` row per board holds the auto-pilot schedule,
status mapping and concurrency caps. ``get_or_create`` lazily materializes a
disabled default row so the API and ticker always have a config to read.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from agent_team.features.board.models import AgentTeamAutopilot
from agent_team.features.board.schemas import AutopilotDTO, RoutingRule


def get(db: Session, board_id: str) -> AgentTeamAutopilot | None:
    return (
        db.query(AgentTeamAutopilot)
        .filter(AgentTeamAutopilot.board_id == board_id)
        .first()
    )


def get_or_create(db: Session, board_id: str) -> AgentTeamAutopilot:
    """Return the board's autopilot config, creating a disabled default if absent."""
    row = get(db, board_id)
    if row is not None:
        return row
    row = AgentTeamAutopilot(board_id=board_id)
    db.add(row)
    db.flush()
    return row


def serialize(row: AgentTeamAutopilot) -> AutopilotDTO:
    return AutopilotDTO(
        board_id=row.board_id,
        enabled=row.enabled,
        schedule_mode=row.schedule_mode,
        interval_seconds=row.interval_seconds,
        cron=row.cron,
        timezone=row.timezone,
        source_status=row.source_status,
        working_status=row.working_status,
        done_status=row.done_status,
        error_status=row.error_status,
        board_concurrency=row.board_concurrency,
        default_agent_concurrency=row.default_agent_concurrency,
        agent_concurrency=row.agent_concurrency(),
        error_cooldown_seconds=row.error_cooldown_seconds,
        max_attempts=row.max_attempts,
        prompt_template=row.prompt_template,
        routing_rules=[RoutingRule(**r) for r in row.routing_rules()],
        next_run_at=row.next_run_at.isoformat() if row.next_run_at else None,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def set_agent_concurrency(row: AgentTeamAutopilot, mapping: dict[str, int]) -> None:
    """Store the per-agent concurrency overrides as JSON (drops invalid entries)."""
    clean: dict[str, int] = {}
    for alias, cap in (mapping or {}).items():
        try:
            clean[str(alias)] = max(0, int(cap))
        except (TypeError, ValueError):
            continue
    row.agent_concurrency_json = json.dumps(clean)


def set_routing_rules(row: AgentTeamAutopilot, rules: list) -> None:
    """Store the ordered routing rules as JSON and reset stale round-robin cursors."""
    clean: list[dict] = []
    for rule in rules or []:
        data = rule.model_dump() if hasattr(rule, "model_dump") else dict(rule)
        clean.append(
            {
                "labels": [str(x) for x in (data.get("labels") or [])],
                "priorities": [str(x) for x in (data.get("priorities") or [])],
                "agents": [str(x) for x in (data.get("agents") or [])],
            }
        )
    row.routing_rules_json = json.dumps(clean)
    # Rule indices changed → drop the per-rule cursors so rotation restarts clean.
    row.routing_rr_json = "{}"
