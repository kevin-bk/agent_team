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
    #: Resource use of the *evaluator turn* that produced this verdict. The
    #: evaluator is a full agent run (it runs the project's tests/build), so its
    #: spend must count against the loop budget just like the generator's. The
    #: driver folds these into the ledger; only the backend evaluator sets them,
    #: so internally- or test-constructed verdicts default to 0.
    eval_tokens: int = 0
    eval_cost_usd: float = 0.0


#: Evidence keys that constitute *concrete proof of verification*: free-text the
#: evaluator wrote about what it ran (``checks``) or the strict EVIDENCE schema's
#: executed commands. Keys like ``verdict``/``score`` echoed back are not proof.
_VERIFICATION_EVIDENCE_KEYS = ("checks", "commands", "checks_run", "tests")


def has_verification_evidence(verdict: Verdict) -> bool:
    """Whether a verdict carries concrete proof that verification happened.

    A ``pass`` is only trustworthy if the evaluator actually ran or observed
    something. We accept any non-empty value under the keys the evaluator
    prompts ask for — free-text ``checks`` (lightweight contract) or a non-empty
    ``commands`` list (strict ``EVIDENCE.json`` schema). A bare ``{}`` or a
    document that only echoes ``verdict``/``score`` counts as *no* evidence, so
    the loop must not treat such a ``pass`` as a verified completion.
    """
    evidence = verdict.evidence
    if not isinstance(evidence, dict) or not evidence:
        return False
    return any(evidence.get(key) for key in _VERIFICATION_EVIDENCE_KEYS)


#: Soft cap on the rendered evidence digest so the retry prompt stays small and
#: the cacheable prior prefix is preserved.
_EVIDENCE_DIGEST_MAX_CHARS = 1500


def _evidence_lines(value: object) -> list[str]:
    """Coerce a str / list of strings into trimmed, non-empty lines."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [s for s in (str(item).strip() for item in value) if s]
    return []


def _command_exit_code(command: dict) -> int:
    try:
        return int(command.get("exit_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def format_evidence_digest(
    evidence: dict, *, max_chars: int = _EVIDENCE_DIGEST_MAX_CHARS
) -> str:
    """Render an evaluator's evidence into a compact digest for the next attempt.

    Surfaces the concrete signal a generator can act on — the commands the
    evaluator ran (with exit codes; failures first), any free-text ``checks``,
    and noted ``risks`` — capped so the retry prompt stays small and
    cache-friendly. Returns ``""`` when there is nothing actionable to relay.
    """
    if not isinstance(evidence, dict) or not evidence:
        return ""
    sections: list[str] = []

    commands = evidence.get("commands")
    if isinstance(commands, list) and commands:
        # Failed commands first — they are what the next attempt must address.
        ordered = sorted(
            (c for c in commands if isinstance(c, dict)),
            key=lambda c: 0 if _command_exit_code(c) != 0 else 1,
        )
        lines: list[str] = []
        for command in ordered:
            cmd = str(command.get("cmd") or command.get("command") or "").strip()
            if not cmd:
                continue
            code = _command_exit_code(command)
            summary = str(command.get("summary") or "").strip()
            line = f"- `{cmd}` → exit {code} ({'FAILED' if code != 0 else 'ok'})"
            if summary:
                line += f": {summary}"
            lines.append(line)
        if lines:
            sections.append("Commands the evaluator ran:\n" + "\n".join(lines))

    check_lines = _evidence_lines(evidence.get("checks"))
    if check_lines:
        sections.append(
            "Checks observed:\n" + "\n".join(f"- {line}" for line in check_lines)
        )

    risk_lines = _evidence_lines(evidence.get("risks"))
    if risk_lines:
        sections.append(
            "Risks noted:\n" + "\n".join(f"- {line}" for line in risk_lines)
        )

    if not sections:
        return ""
    digest = "\n\n".join(sections)
    if len(digest) > max_chars:
        digest = digest[: max_chars - 1].rstrip() + "…"
    return digest


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
