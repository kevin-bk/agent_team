"""Guardrails for an autonomous loop: bound attempts, tokens, cost and runtime.

A :class:`LoopBudget` is the static policy; a :class:`LoopLedger` accumulates
what the loop has spent and reports when a cap is hit. The attempt cap lives on
the controller (it shapes the continue decision); the ledger covers the resource
caps the controller cannot see (tokens/cost/wall-clock). Hitting any cap is a
**hard stop** that routes the task to human review rather than a silent finish.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoopBudget:
    """Resource caps for one autonomous loop run (``None``/``0`` = unbounded)."""

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_seconds: int | None = None


@dataclass
class LoopLedger:
    """Running totals for a loop, checked against a :class:`LoopBudget`."""

    budget: LoopBudget = field(default_factory=LoopBudget)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    _started_at: float = field(default_factory=time.monotonic)

    def add(self, *, tokens: int = 0, cost_usd: float = 0.0) -> None:
        """Fold one attempt's resource use into the totals."""
        self.total_tokens += max(0, int(tokens or 0))
        self.total_cost_usd += max(0.0, float(cost_usd or 0.0))

    def exceeded(self) -> str | None:
        """Return the name of the first cap hit, or ``None`` if within budget."""
        b = self.budget
        if b.max_tokens and self.total_tokens >= b.max_tokens:
            return "tokens"
        if b.max_cost_usd and self.total_cost_usd >= b.max_cost_usd:
            return "cost"
        if b.max_wall_seconds and (
            time.monotonic() - self._started_at >= b.max_wall_seconds
        ):
            return "runtime"
        return None
