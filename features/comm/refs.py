"""Lightweight value objects passed through the gateway, decoupled from the ORM."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskRef:
    """The minimal task facts the gateway needs to render + route a notification."""

    id: str
    board_id: str
    key: str
    title: str
    assignee_id: str | None = None
    reporter_id: str | None = None
    created_by: str | None = None
    board_slug: str = ""
