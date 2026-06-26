"""The evaluator's verdict and a tolerant parser for an agent-produced one.

An evaluator turn ends with a structured verdict embedded in its text output as a
JSON object. The parser is deliberately forgiving — it scans for the last JSON
object in the text and coerces its fields — so an evaluator that wraps the JSON
in prose (or emits several) still yields a usable verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum


class LoopVerdict(StrEnum):
    """Whether an attempt met the objective."""

    PASS = "pass"
    FAIL = "fail"
    NEEDS_HUMAN = "needs_human"


@dataclass
class Verdict:
    """An independent grade of one attempt."""

    verdict: LoopVerdict
    score: float = 0.0
    #: What still has to happen for the objective to be met (fed back to the
    #: generator on the next attempt).
    missing: str = ""
    evidence: dict = field(default_factory=dict)


def _coerce_verdict(value: object) -> LoopVerdict | None:
    text = str(value or "").strip().lower()
    for member in LoopVerdict:
        if text == member.value:
            return member
    if text in ("complete", "done", "passed", "ok", "success"):
        return LoopVerdict.PASS
    if text in ("incomplete", "failed", "not_done", "fail"):
        return LoopVerdict.FAIL
    if text in ("human", "needs-human", "review", "escalate"):
        return LoopVerdict.NEEDS_HUMAN
    return None


def _iter_json_objects(text: str):
    """Yield top-level ``{...}`` substrings of ``text``, brace-balanced."""
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : i + 1]


def parse_verdict(text: str) -> Verdict | None:
    """Extract a :class:`Verdict` from an evaluator's text output, or ``None``.

    Scans for JSON objects in the text and uses the **last** one that carries a
    recognisable ``verdict`` field, so trailing summaries win over any example
    JSON quoted earlier. Returns ``None`` when nothing parseable is found — the
    caller treats that as "could not evaluate" (fail-open).
    """
    if not text:
        return None
    found: Verdict | None = None
    for blob in _iter_json_objects(text):
        try:
            obj = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        verdict = _coerce_verdict(obj.get("verdict") or obj.get("status"))
        if verdict is None:
            continue
        try:
            score = float(obj.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        score = min(1.0, max(0.0, score))
        missing = str(obj.get("missing") or obj.get("remaining") or "").strip()
        evidence = obj.get("evidence")
        found = Verdict(
            verdict=verdict,
            score=score,
            missing=missing,
            evidence=evidence if isinstance(evidence, dict) else {},
        )
    return found
