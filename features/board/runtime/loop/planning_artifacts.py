"""Durable planning artifacts: the contract between human, planner and loop.

Strict planning persists a small set of files inside the task workspace under
``.agent-team/``. These are the source of truth the generator and evaluator both
read, and the surface a human reviews/edits/approves before any execution runs.

This module owns all artifact file I/O so the router, planning service and loop
never hand-roll paths. Every path is resolved *inside* the workspace and any
attempt to escape it (``..`` or absolute) is rejected, because some artifact
contents (e.g. ``TASKS.json``) are written by an agent and must not be trusted to
point outside the workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

#: Workspace-relative directory all planning artifacts live under.
ARTIFACT_DIR = ".agent-team"

#: The source-of-truth artifact paths (workspace-relative).
SPEC_PATH = f"{ARTIFACT_DIR}/SPEC.md"
PLAN_PATH = f"{ARTIFACT_DIR}/PLAN.md"
TASKS_PATH = f"{ARTIFACT_DIR}/TASKS.json"
PLAN_REVIEW_PATH = f"{ARTIFACT_DIR}/PLAN_REVIEW.json"
EVIDENCE_PATH = f"{ARTIFACT_DIR}/EVIDENCE.json"
PLAN_CHANGE_REQUEST_PATH = f"{ARTIFACT_DIR}/PLAN_CHANGE_REQUEST.md"
#: Structured questions an agent (planner or generator) raises for a human. A
#: blocking, unanswered question gates the planning/execution phase until the
#: human answers it via the cockpit.
QUESTIONS_PATH = f"{ARTIFACT_DIR}/QUESTIONS.json"
#: Append-only inbox where an agent suggests journal entries (one JSON object
#: per line, JSONL). The backend ingests these into the durable task journal
#: after each turn and then archives the file, so a write here is a *suggestion*
#: that survives the agent's own context compaction.
JOURNAL_NOTES_PATH = f"{ARTIFACT_DIR}/JOURNAL_NOTES.jsonl"
#: Backend-rendered, read-only mirror of the durable journal (full history).
#: Regenerated each turn so an agent can read the complete decision timeline on
#: demand instead of carrying it inline in every prompt.
JOURNAL_FILE_PATH = f"{ARTIFACT_DIR}/JOURNAL.md"
ARCHIVE_DIR = f"{ARTIFACT_DIR}/archive"

#: Artifacts that gate a strict approval. SPEC and PLAN are required; TASKS is
#: advisory in v1 (validated when present, but not required to approve).
APPROVAL_ARTIFACTS: tuple[str, ...] = (SPEC_PATH, PLAN_PATH, TASKS_PATH)
REQUIRED_FOR_APPROVAL: tuple[str, ...] = (SPEC_PATH, PLAN_PATH)

#: Editable artifacts addressable by short name through the edit endpoint.
EDITABLE_ARTIFACTS: dict[str, str] = {
    "SPEC.md": SPEC_PATH,
    "PLAN.md": PLAN_PATH,
    "TASKS.json": TASKS_PATH,
}


class ArtifactError(ValueError):
    """Raised when an artifact path is unsafe or content is invalid."""


@dataclass(frozen=True)
class ArtifactMeta:
    """Metadata for one planning artifact on disk."""

    path: str
    exists: bool
    etag: str | None
    size: int
    updated_at: str | None


def _safe_abs(workspace_path: str, rel_path: str) -> str:
    """Resolve ``rel_path`` strictly inside ``workspace_path``.

    Rejects absolute paths and any path that escapes the workspace root, so an
    agent-written reference can never read or clobber files outside the task
    workspace.
    """
    if not workspace_path:
        raise ArtifactError("workspace path is not set")
    rel = (rel_path or "").strip()
    if not rel or os.path.isabs(rel) or rel.startswith("~"):
        raise ArtifactError(f"unsafe artifact path: {rel_path!r}")
    root = os.path.realpath(workspace_path)
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ArtifactError(f"artifact path escapes workspace: {rel_path!r}")
    return target


def etag(text: str) -> str:
    """Content etag (sha256) used for optimistic-concurrency edits."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def read_text(workspace_path: str, rel_path: str) -> str | None:
    """Read an artifact's text, or ``None`` if it does not exist."""
    abs_path = _safe_abs(workspace_path, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def write_text(workspace_path: str, rel_path: str, content: str) -> str:
    """Write an artifact (creating parent dirs) and return its new etag."""
    abs_path = _safe_abs(workspace_path, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return etag(content)


def exists(workspace_path: str, rel_path: str) -> bool:
    """Whether an artifact file exists and is non-empty."""
    text = read_text(workspace_path, rel_path)
    return bool(text and text.strip())


def read_json(workspace_path: str, rel_path: str) -> object | None:
    """Parse a JSON artifact, or ``None`` when missing/blank/invalid."""
    text = read_text(workspace_path, rel_path)
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def metadata(workspace_path: str, rel_path: str) -> ArtifactMeta:
    """Return on-disk metadata for one artifact."""
    try:
        abs_path = _safe_abs(workspace_path, rel_path)
    except ArtifactError:
        return ArtifactMeta(path=rel_path, exists=False, etag=None, size=0, updated_at=None)
    text = read_text(workspace_path, rel_path)
    if text is None:
        return ArtifactMeta(path=rel_path, exists=False, etag=None, size=0, updated_at=None)
    try:
        mtime = os.path.getmtime(abs_path)
        updated = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    except OSError:
        updated = None
    return ArtifactMeta(
        path=rel_path,
        exists=True,
        etag=etag(text),
        size=len(text.encode("utf-8")),
        updated_at=updated,
    )


def all_metadata(workspace_path: str) -> list[ArtifactMeta]:
    """Metadata for every known planning artifact (existing or not)."""
    paths = (
        SPEC_PATH,
        PLAN_PATH,
        TASKS_PATH,
        PLAN_REVIEW_PATH,
        EVIDENCE_PATH,
        PLAN_CHANGE_REQUEST_PATH,
        QUESTIONS_PATH,
    )
    return [metadata(workspace_path, p) for p in paths]


def approved_etags(workspace_path: str) -> dict[str, str]:
    """Etags of the approval artifacts that currently exist on disk.

    Keyed by short name (``SPEC.md``…) to match the approval metadata snapshot.
    """
    out: dict[str, str] = {}
    for name, rel in EDITABLE_ARTIFACTS.items():
        text = read_text(workspace_path, rel)
        if text is not None and text.strip():
            out[name] = etag(text)
    return out


def missing_required(workspace_path: str) -> list[str]:
    """Required-for-approval artifacts that are absent or blank."""
    return [p for p in REQUIRED_FOR_APPROVAL if not exists(workspace_path, p)]


#: Valid lifecycle values for a task in ``TASKS.json``.
TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "in_progress", "complete", "blocked", "skipped"}
)
#: Statuses that satisfy a dependency (a dependant may start once its deps reach
#: one of these). ``skipped`` counts as satisfied so a manually skipped task does
#: not wedge the graph.
_DEP_SATISFIED: frozenset[str] = frozenset({"complete", "skipped"})


def read_tasks(workspace_path: str) -> dict | None:
    """Parse ``TASKS.json`` as a dict, or ``None`` when missing/blank/invalid."""
    data = read_json(workspace_path, TASKS_PATH)
    return data if isinstance(data, dict) else None


def task_list(workspace_path: str) -> list[dict]:
    """Normalised task rows from ``TASKS.json`` for scheduling and the cockpit.

    Returns ``[]`` when the file is absent or malformed; each row carries the
    fields the scheduler and generator need (id/title/status/depends_on plus the
    per-task contract).
    """
    data = read_tasks(workspace_path)
    if data is None:
        return []
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return []
    rows: list[dict] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.strip():
            continue
        status = t.get("status", "pending")
        rows.append(
            {
                "id": tid,
                "title": str(t.get("title") or tid),
                "status": status if status in TASK_STATUSES else "pending",
                "depends_on": [str(d) for d in (t.get("depends_on") or [])],
                "objective": str(t.get("objective") or ""),
                "files": [str(f) for f in (t.get("files") or [])],
                "acceptance": [str(a) for a in (t.get("acceptance") or [])],
                "validation": [str(v) for v in (t.get("validation") or [])],
                "risk": str(t.get("risk") or ""),
            }
        )
    return rows


def next_runnable_task(rows: list[dict]) -> dict | None:
    """First ``pending`` task whose dependencies are all satisfied, else ``None``.

    Preserves document order, so the planner's ordering is the tie-breaker.
    """
    done = {r["id"] for r in rows if r["status"] in _DEP_SATISFIED}
    for r in rows:
        if r["status"] != "pending":
            continue
        if all(dep in done for dep in r["depends_on"]):
            return r
    return None


def set_task_status(workspace_path: str, task_id: str, status: str) -> bool:
    """Persist a new ``status`` for one task in ``TASKS.json``.

    Returns ``True`` when the task was found and written. The on-disk file stays
    the single source of truth for graph progress, so the cockpit and a resumed
    run both see the same state.
    """
    if status not in TASK_STATUSES:
        raise ArtifactError(f"invalid task status: {status!r}")
    data = read_tasks(workspace_path)
    if data is None:
        return False
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return False
    found = False
    for t in tasks:
        if isinstance(t, dict) and t.get("id") == task_id:
            t["status"] = status
            found = True
            break
    if not found:
        return False
    write_text(workspace_path, TASKS_PATH, json.dumps(data, ensure_ascii=False, indent=2))
    return True


def validate_tasks(data: object) -> list[str]:
    """Validate a ``TASKS.json`` document; return a list of error strings.

    Advisory in v1 (the loop does not schedule from it yet), but validated when
    present so a malformed task graph surfaces early instead of silently in v2.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["TASKS.json must be a JSON object"]
    if data.get("version") != 1:
        errors.append("TASKS.json: unsupported or missing 'version' (expected 1)")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return errors + ["TASKS.json: 'tasks' must be a list"]

    ids: set[str] = set()
    valid_status = TASK_STATUSES
    deps: dict[str, list[str]] = {}
    for i, task in enumerate(tasks):
        where = f"TASKS.json: task[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{where} must be an object")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not tid.strip():
            errors.append(f"{where} missing string 'id'")
            continue
        if tid in ids:
            errors.append(f"{where} duplicate id {tid!r}")
        ids.add(tid)
        status = task.get("status", "pending")
        if status not in valid_status:
            errors.append(f"{where} unknown status {status!r}")
        depends = task.get("depends_on") or []
        if not isinstance(depends, list):
            errors.append(f"{where} 'depends_on' must be a list")
            depends = []
        deps[tid] = [str(d) for d in depends]

    for tid, dep_ids in deps.items():
        for dep in dep_ids:
            if dep not in ids:
                errors.append(f"TASKS.json: task {tid!r} depends on unknown id {dep!r}")

    if _has_cycle(deps):
        errors.append("TASKS.json: dependency graph has a cycle")
    return errors


def _has_cycle(deps: dict[str, list[str]]) -> bool:
    """Whether the dependency graph contains a cycle (DFS, three-colour)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in deps}

    def visit(node: str) -> bool:
        colour[node] = GRAY
        for nxt in deps.get(node, []):
            if nxt not in colour:
                continue
            if colour[nxt] == GRAY:
                return True
            if colour[nxt] == WHITE and visit(nxt):
                return True
        colour[node] = BLACK
        return False

    return any(colour[node] == WHITE and visit(node) for node in deps)


def archive_change_request(workspace_path: str) -> str | None:
    """Move an active ``PLAN_CHANGE_REQUEST.md`` into the archive.

    Only the active marker path gates execution; archiving it (rather than
    deleting) keeps the history while clearing the gate. Returns the archive
    path, or ``None`` when there was no active marker.
    """
    text = read_text(workspace_path, PLAN_CHANGE_REQUEST_PATH)
    if not text or not text.strip():
        return None
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    rel_dest = f"{ARCHIVE_DIR}/plan-change-requests/{stamp}.md"
    write_text(workspace_path, rel_dest, text)
    try:
        os.remove(_safe_abs(workspace_path, PLAN_CHANGE_REQUEST_PATH))
    except OSError:
        pass
    return rel_dest


# ---------------------------------------------------------------------------
# Structured Q&A (``QUESTIONS.json``)
# ---------------------------------------------------------------------------
#
# An agent raises blocking questions instead of guessing a materially-impacting
# decision; a blocking, unanswered question parks the phase at
# ``waiting_answers`` until a human answers via the cockpit. The same artifact
# serves both the planning phase (planner asks) and execution (generator asks).


def read_questions(workspace_path: str) -> list[dict]:
    """Normalised question rows from ``QUESTIONS.json`` (``[]`` when absent).

    Each row carries: ``id``, ``question``, ``reason``, ``blocking`` (bool),
    ``options`` (list[str], may be empty), and ``answer`` (str, ``""`` when
    unanswered). The cockpit always renders an extra "Other" free-text choice on
    top of ``options`` so a human can answer outside the offered set.
    """
    data = read_json(workspace_path, QUESTIONS_PATH)
    if not isinstance(data, dict):
        return []
    raw = data.get("questions")
    if not isinstance(raw, list):
        return []
    rows: list[dict] = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        qid = q.get("id")
        question = q.get("question")
        if not isinstance(qid, str) or not qid.strip():
            continue
        if not isinstance(question, str) or not question.strip():
            continue
        answer = q.get("answer")
        rows.append(
            {
                "id": qid.strip(),
                "question": question.strip(),
                "reason": str(q.get("reason") or "").strip(),
                # Default to blocking: an agent that bothered to ask should pause
                # unless it explicitly marked the question non-blocking.
                "blocking": q.get("blocking", True) is not False,
                "options": [str(o) for o in (q.get("options") or []) if str(o).strip()],
                "answer": str(answer).strip() if isinstance(answer, str) else "",
            }
        )
    return rows


def open_questions(workspace_path: str) -> list[dict]:
    """Blocking questions that still have no answer."""
    return [q for q in read_questions(workspace_path) if q["blocking"] and not q["answer"]]


def questions_pending(workspace_path: str) -> bool:
    """Whether at least one blocking question is still unanswered."""
    return bool(open_questions(workspace_path))


def answer_questions(workspace_path: str, answers: dict[str, str]) -> int:
    """Persist human ``answers`` (keyed by question id) into ``QUESTIONS.json``.

    Returns the number of questions updated. Unknown ids and blank answers are
    ignored, so a partial submission only fills the questions it addresses.
    """
    data = read_json(workspace_path, QUESTIONS_PATH)
    if not isinstance(data, dict):
        return 0
    raw = data.get("questions")
    if not isinstance(raw, list):
        return 0
    updated = 0
    for q in raw:
        if not isinstance(q, dict):
            continue
        qid = q.get("id")
        if not isinstance(qid, str):
            continue
        ans = answers.get(qid)
        if ans is None or not str(ans).strip():
            continue
        q["answer"] = str(ans).strip()
        updated += 1
    if updated:
        write_text(
            workspace_path, QUESTIONS_PATH, json.dumps(data, ensure_ascii=False, indent=2)
        )
    return updated


#: Heading the durable human clarifications live under in ``SPEC.md``.
CLARIFICATIONS_HEADING = "## Approved Clarifications"


def append_clarifications(
    workspace_path: str, answered: list[dict], note: str | None = None
) -> bool:
    """Fold answered execution-phase questions into ``SPEC.md`` as approved scope.

    During execution the human's answers only reach the generator's prompt, so
    the independent evaluator — which grades against the SPEC — would never see
    them. Recording each answered question (including a free-text "Other" answer
    and any overall ``note``) under a durable ``## Approved Clarifications``
    section makes those decisions part of the contract the evaluator reads, so a
    human-approved choice is not graded as a deviation.

    Returns ``True`` when something was written.
    """
    lines: list[str] = []
    for q in answered:
        ans = str(q.get("answer") or "").strip()
        if not ans:
            continue
        lines.append(f"- Q ({q.get('id')}): {q.get('question')}")
        lines.append(f"  A: {ans}")
    note = (note or "").strip()
    if note:
        lines.append(f"- Note: {note}")
    if not lines:
        return False
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = f"### {stamp}\n" + "\n".join(lines)
    spec = read_text(workspace_path, SPEC_PATH) or "# SPEC"
    if CLARIFICATIONS_HEADING in spec:
        # The section is appended once and stays last, so new rounds append here.
        spec = f"{spec.rstrip()}\n\n{block}\n"
    else:
        spec = f"{spec.rstrip()}\n\n{CLARIFICATIONS_HEADING}\n\n{block}\n"
    write_text(workspace_path, SPEC_PATH, spec)
    return True


def archive_questions(workspace_path: str) -> str | None:
    """Move an active ``QUESTIONS.json`` into the archive, clearing the gate.

    Archiving (rather than deleting) keeps the answered questionnaire for
    history while removing the marker so a resumed phase does not re-pause.
    Returns the archive path, or ``None`` when there was no active file.
    """
    text = read_text(workspace_path, QUESTIONS_PATH)
    if not text or not text.strip():
        return None
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    rel_dest = f"{ARCHIVE_DIR}/questions/{stamp}.json"
    write_text(workspace_path, rel_dest, text)
    try:
        os.remove(_safe_abs(workspace_path, QUESTIONS_PATH))
    except OSError:
        pass
    return rel_dest


# ---------------------------------------------------------------------------
# Agent journal-note inbox (``JOURNAL_NOTES.jsonl``)
# ---------------------------------------------------------------------------
#
# Agents append suggested journal entries here, one JSON object per line. The
# backend ingests them into the durable DB journal and archives the file, so a
# note survives even if the agent's own context is later compacted.


def read_journal_notes(workspace_path: str) -> list[dict]:
    """Parse the agent's journal-note inbox (JSONL); skip malformed lines.

    Each line is one suggested entry: ``{"type", "title", "body", "severity",
    "phase"}``. Only ``title`` is required; the rest default. Malformed lines
    (bad JSON, non-objects, blank title) are skipped so one bad line never loses
    the others.
    """
    text = read_text(workspace_path, JOURNAL_NOTES_PATH)
    if not text:
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        title = str(obj.get("title") or "").strip()
        if not title:
            continue
        rows.append(
            {
                "type": str(obj.get("type") or "note").strip(),
                "title": title,
                "body": str(obj.get("body") or "").strip(),
                "severity": str(obj.get("severity") or "info").strip(),
                "phase": str(obj.get("phase") or "").strip(),
            }
        )
    return rows


def archive_journal_notes(workspace_path: str) -> str | None:
    """Move an active ``JOURNAL_NOTES.jsonl`` into the archive, clearing it.

    Called right after the backend ingests the notes so the same suggestions are
    never ingested twice. Returns the archive path, or ``None`` when there was
    no active inbox.
    """
    text = read_text(workspace_path, JOURNAL_NOTES_PATH)
    if not text or not text.strip():
        return None
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    rel_dest = f"{ARCHIVE_DIR}/journal-notes/{stamp}.jsonl"
    write_text(workspace_path, rel_dest, text)
    try:
        os.remove(_safe_abs(workspace_path, JOURNAL_NOTES_PATH))
    except OSError:
        pass
    return rel_dest
