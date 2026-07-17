"""The evaluator's verdict and a tolerant parser for an agent-produced one.

An evaluator turn ends with a structured verdict embedded in its text output as a
JSON object. The parser is deliberately forgiving — it scans for the last JSON
object in the text and coerces its fields — so an evaluator that wraps the JSON
in prose (or emits several) still yields a usable verdict.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import StrEnum

from agent_team.features.board.runtime.loop import planning_artifacts as artifacts


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


_EVIDENCE_SECTIONS = frozenset({"commands", "criteria", "scenarios", "artifacts"})
_UI_ARTIFACT_KINDS = frozenset(
    {"screenshot", "trace", "video", "snapshot", "report"}
)


def requirements_for_profiles(
    profiles: list[str], explicit: list[str] | None = None
) -> set[str]:
    """Derive evidence sections required by project-defined profiles.

    Every declared verification contract requires executed commands and a
    criterion-to-evidence mapping. UI/visual work additionally requires a
    scenario plus a persisted artifact; AI behaviour requires scenario results.
    Projects may add any of the standard sections explicitly.
    """
    required = {"commands", "criteria"}
    normalised = [str(profile).strip().lower() for profile in profiles]
    if any(
        profile == "ui" or profile.startswith("ui_") or profile == "visual"
        for profile in normalised
    ):
        required.update({"scenarios", "artifacts"})
    if any(profile == "ai" or profile.startswith("ai_") for profile in normalised):
        required.add("scenarios")
    required.update(
        item
        for item in (explicit or [])
        if isinstance(item, str) and item in _EVIDENCE_SECTIONS
    )
    return required


def _clean_id(value: object) -> str:
    return str(value or "").strip()


def _exit_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_command_evidence(
    evidence: dict, *, planned_commands: list[object] | None = None
) -> list[str]:
    """Validate that strict evidence records successful, concrete commands."""
    errors: list[str] = []
    commands = evidence.get("commands")
    if not isinstance(commands, list) or not commands:
        return ["verification evidence must contain at least one executed command"]

    observed: set[str] = set()
    for index, command in enumerate(commands):
        where = f"commands[{index}]"
        if not isinstance(command, dict):
            errors.append(f"{where} must be an object")
            continue
        cmd = str(command.get("cmd") or command.get("command") or "").strip()
        if not cmd:
            errors.append(f"{where}.cmd must be non-empty")
        else:
            observed.add(" ".join(cmd.split()))
        if "exit_code" not in command:
            errors.append(f"{where}.exit_code is required")
            continue
        code = _exit_code(command.get("exit_code"))
        if code is None:
            errors.append(f"{where}.exit_code must be an integer")
        elif code != 0:
            errors.append(f"{where} failed with exit code {code}")

    for planned in planned_commands or []:
        repo, normalised = artifacts.verification_command_key(planned)
        if normalised and (repo or normalised not in observed):
            label = artifacts.verification_command_label(planned)
            errors.append(f"planned verification command was not recorded: {label}")
    return errors


def validate_trusted_receipts(
    evidence: dict,
    *,
    trusted_receipts: list[dict],
    planned_commands: list[object],
    current_source_sha256: str,
) -> list[str]:
    """Validate evaluator references against backend-owned command receipts."""
    errors: list[str] = []
    if not trusted_receipts:
        return ["verification runner produced no trusted receipts"]

    trusted_ids: set[str] = set()
    observed_commands: set[tuple[str, str]] = set()
    for index, receipt in enumerate(trusted_receipts):
        where = f"trusted receipt[{index}]"
        receipt_id = _clean_id(receipt.get("id"))
        if not receipt_id:
            errors.append(f"{where} has no id")
        elif receipt_id in trusted_ids:
            errors.append(f"duplicate trusted receipt id: {receipt_id}")
        else:
            trusted_ids.add(receipt_id)
        command = str(receipt.get("command") or "").strip()
        if command:
            repo = str(receipt.get("repo") or "").strip()
            working_directory = str(receipt.get("working_directory") or repo).strip()
            if repo and working_directory not in {"", repo, "."}:
                relative = working_directory.removeprefix(f"{repo}/")
                repo = f"{repo}:{relative}"
            observed_commands.add((repo, " ".join(command.split())))
        code = _exit_code(receipt.get("exit_code"))
        passed = (
            receipt.get("status") == "pass"
            if receipt.get("status") is not None
            else code == 0 and receipt.get("timed_out") is not True
        )
        if code is None or not passed:
            errors.append(
                f"{where} did not pass (exit_code={receipt.get('exit_code')}, "
                f"timed_out={bool(receipt.get('timed_out'))})"
            )
        bound_source = str(receipt.get("source_after_sha256") or "").strip()
        if not bound_source or bound_source != current_source_sha256:
            errors.append(f"{where} is stale for the current source state")

    for planned in planned_commands:
        key = artifacts.verification_command_key(planned)
        if key != ("", "") and key not in observed_commands:
            label = artifacts.verification_command_label(planned)
            errors.append(f"approved command has no trusted receipt: {label}")

    cited_raw = evidence.get("receipt_ids")
    cited = (
        {_clean_id(item) for item in cited_raw if _clean_id(item)}
        if isinstance(cited_raw, list)
        else set()
    )
    if not cited:
        errors.append("EVIDENCE.json must cite backend receipt_ids")
    for receipt_id in sorted(trusted_ids - cited):
        errors.append(f"trusted receipt was not cited by evaluator: {receipt_id}")
    for receipt_id in sorted(cited - trusted_ids):
        errors.append(f"evaluator cited unknown receipt id: {receipt_id}")
    return errors


def _workspace_artifact_exists(workspace_path: str, rel_path: str) -> bool:
    """Whether a non-empty binary or text artifact exists inside the workspace."""
    if not workspace_path or not rel_path or os.path.isabs(rel_path):
        return False
    root = os.path.realpath(workspace_path)
    target = os.path.realpath(os.path.join(root, rel_path))
    if target == root or not target.startswith(root + os.sep):
        return False
    try:
        return os.path.isfile(target) and os.path.getsize(target) > 0
    except OSError:
        return False


def validate_verification_evidence(
    evidence: dict,
    *,
    profiles: list[str],
    expected_criteria: list[str],
    planned_commands: list[object],
    required_evidence: list[str] | None = None,
    workspace_path: str = "",
    trusted_receipts: list[dict] | None = None,
    current_source_sha256: str = "",
) -> list[str]:
    """Validate schema-v2 evidence against an approved verification contract."""
    errors: list[str] = []
    if evidence.get("version") != 2:
        errors.append("verification contract requires EVIDENCE.json schema version 2")

    required = requirements_for_profiles(profiles, required_evidence)
    if "commands" in required:
        if trusted_receipts is None:
            errors.extend(
                validate_command_evidence(evidence, planned_commands=planned_commands)
            )
        else:
            errors.extend(
                validate_trusted_receipts(
                    evidence,
                    trusted_receipts=trusted_receipts,
                    planned_commands=planned_commands,
                    current_source_sha256=current_source_sha256,
                )
            )

    artifact_ids: set[str] = set()
    artifact_kinds: dict[str, str] = {}
    artifacts = evidence.get("artifacts")
    if isinstance(artifacts, list):
        if "artifacts" in required and not artifacts:
            errors.append("verification evidence must contain artifacts")
        for index, artifact in enumerate(artifacts):
            where = f"artifacts[{index}]"
            if not isinstance(artifact, dict):
                errors.append(f"{where} must be an object")
                continue
            artifact_id = _clean_id(artifact.get("id"))
            kind = str(artifact.get("kind") or "").strip().lower()
            path = str(artifact.get("path") or "").strip()
            if not artifact_id:
                errors.append(f"{where}.id must be non-empty")
            elif artifact_id in artifact_ids:
                errors.append(f"duplicate evidence id: {artifact_id}")
            else:
                artifact_ids.add(artifact_id)
                artifact_kinds[artifact_id] = kind
            if not kind:
                errors.append(f"{where}.kind must be non-empty")
            if not _workspace_artifact_exists(workspace_path, path):
                errors.append(f"{where}.path is missing, empty, or unsafe: {path!r}")
    elif "artifacts" in required:
        errors.append("verification evidence must contain artifacts")

    scenario_ids: set[str] = set()
    scenario_refs: dict[str, list[str]] = {}
    scenario_profiles: set[str] = set()
    scenarios = evidence.get("scenarios")
    if isinstance(scenarios, list):
        if "scenarios" in required and not scenarios:
            errors.append("verification evidence must contain scenarios")
        for index, scenario in enumerate(scenarios):
            where = f"scenarios[{index}]"
            if not isinstance(scenario, dict):
                errors.append(f"{where} must be an object")
                continue
            scenario_id = _clean_id(scenario.get("id"))
            if not scenario_id:
                errors.append(f"{where}.id must be non-empty")
            elif scenario_id in scenario_ids:
                errors.append(f"duplicate evidence id: {scenario_id}")
            else:
                scenario_ids.add(scenario_id)
            if str(scenario.get("status") or "").strip().lower() != "pass":
                errors.append(f"{where}.status must be 'pass'")
            profile = str(scenario.get("profile") or "").strip().lower()
            if not profile:
                errors.append(f"{where}.profile must be non-empty")
            else:
                scenario_profiles.add(profile)
            refs = scenario.get("evidence_ids")
            clean_refs = (
                [_clean_id(ref) for ref in refs if _clean_id(ref)]
                if isinstance(refs, list)
                else []
            )
            scenario_refs[scenario_id] = clean_refs
            if not clean_refs:
                errors.append(f"{where}.evidence_ids must be non-empty")
    elif "scenarios" in required:
        errors.append("verification evidence must contain scenarios")

    command_ids = {
        _clean_id(command.get("id"))
        for command in evidence.get("commands", [])
        if isinstance(command, dict) and _clean_id(command.get("id"))
    }
    receipt_ids = {
        _clean_id(receipt.get("id"))
        for receipt in (trusted_receipts or [])
        if isinstance(receipt, dict) and _clean_id(receipt.get("id"))
    }
    known_ids = artifact_ids | scenario_ids | command_ids | receipt_ids
    for scenario_id, refs in scenario_refs.items():
        for ref in refs:
            if ref not in known_ids:
                errors.append(
                    f"scenario {scenario_id!r} references unknown evidence id {ref!r}"
                )

    criteria = evidence.get("criteria")
    criteria_by_id: dict[str, dict] = {}
    if isinstance(criteria, list):
        if "criteria" in required and not criteria:
            errors.append("verification evidence must map acceptance criteria")
        for index, criterion in enumerate(criteria):
            where = f"criteria[{index}]"
            if not isinstance(criterion, dict):
                errors.append(f"{where} must be an object")
                continue
            criterion_id = _clean_id(criterion.get("id"))
            if not criterion_id:
                errors.append(f"{where}.id must be non-empty")
                continue
            if criterion_id in criteria_by_id:
                errors.append(f"duplicate criterion id: {criterion_id}")
            criteria_by_id[criterion_id] = criterion
            if str(criterion.get("status") or "").strip().lower() != "pass":
                errors.append(f"{where}.status must be 'pass'")
            refs = criterion.get("evidence_ids")
            clean_refs = (
                [_clean_id(ref) for ref in refs if _clean_id(ref)]
                if isinstance(refs, list)
                else []
            )
            if not clean_refs:
                errors.append(f"{where}.evidence_ids must be non-empty")
            for ref in clean_refs:
                if ref not in known_ids:
                    errors.append(f"{where} references unknown evidence id {ref!r}")
    elif "criteria" in required:
        errors.append("verification evidence must map acceptance criteria")

    for criterion_id in expected_criteria:
        if criterion_id not in criteria_by_id:
            errors.append(f"acceptance criterion has no evidence: {criterion_id}")
    expected_criterion_ids = set(expected_criteria)
    for criterion_id in criteria_by_id:
        if criterion_id not in expected_criterion_ids:
            errors.append(
                "evidence contains criterion outside the approved contract: "
                f"{criterion_id}"
            )

    is_ui = any(
        profile.lower() == "ui"
        or profile.lower().startswith("ui_")
        or profile.lower() == "visual"
        for profile in profiles
    )
    if is_ui:
        for scenario_id, refs in scenario_refs.items():
            if not any(
                artifact_kinds.get(ref) in _UI_ARTIFACT_KINDS for ref in refs
            ):
                errors.append(
                    f"UI scenario {scenario_id!r} must reference a screenshot, "
                    "trace, video, snapshot, or report artifact"
                )
    for profile in (item.strip().lower() for item in profiles):
        needs_scenario = (
            profile == "ui"
            or profile.startswith("ui_")
            or profile == "visual"
            or profile == "ai"
            or profile.startswith("ai_")
        )
        if needs_scenario and profile not in scenario_profiles:
            errors.append(f"verification profile has no passing scenario: {profile}")
    return errors


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
