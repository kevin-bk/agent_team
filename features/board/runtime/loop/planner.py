"""The in-loop planning hook contract.

Before the generator/evaluator loop begins, an optional *planning* step can
produce a plan file the generator then works from (handed off **by reference**:
the opening prompt points at the file rather than inlining the plan). The loop
driver depends only on this :class:`Planner` protocol, so any implementation —
or none — can be injected. Strict planning instead drafts and approves its
artifacts before the loop starts and passes the plan path directly, so it does
not use this in-loop hook.
"""

from __future__ import annotations

from typing import Protocol


class Planner(Protocol):
    """Produces a plan file for the objective before the loop starts.

    Returns the workspace-relative path of the plan it wrote, or ``None`` when
    no plan could be produced (the loop then proceeds from the raw objective).
    """

    async def plan(
        self, *, objective: str, workspace_path: str
    ) -> str | None: ...
