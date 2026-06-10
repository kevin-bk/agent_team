"""Reusable Jira sync operations shared by the single-task and batch endpoints.

Keeping the apply/filter logic here (rather than in the router) lets the future
scheduler reuse the exact same batch behaviour.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_team.features.board import attachments as attachments_store
from agent_team.features.board.jira.client import JiraClient, JiraError
from agent_team.features.board.jira.sync import build_task_changes
from agent_team.features.board.repositories import activity as activity_repo
from agent_team.features.board.repositories import comments as comments_repo
from agent_team.features.board.workspace import ensure_task_workspace

#: Sentinel jira_comment_id for the managed comment that holds an issue's
#: downloaded attachments (issue-level in Jira; surfaced here as one note so the
#: files reach both the cockpit and the agent context).
_ATTACH_COMMENT_ID = "__jira_attachments__"

#: Jira wiki-markup image embed: ``!name!`` or ``!name|width=…,alt=…!``.
_JIRA_IMG_RE = re.compile(r"!([^!|\n]+?)(?:\|[^!\n]*)?!")
#: Jira wiki-markup attachment link: ``[^name]``.
_JIRA_LINK_RE = re.compile(r"\[\^([^\]\n]+)\]")

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from agent_team.features.board.models import AgentTeamBoard, AgentTeamTask


def build_client(board: AgentTeamBoard) -> JiraClient:
    """Construct a client from the board's stored config (raises if incomplete)."""
    return JiraClient(
        base_url=board.jira_base_url or "",
        email=board.jira_email or "",
        api_token=board.jira_api_token or "",
    )


def rewrite_jira_media(text: str, name_to_path: dict[str, str]) -> tuple[str, set[str]]:
    """Turn Jira inline attachment markup into Markdown pointing at local files.

    Jira embeds attachments by filename (``!img.png!``, ``!img.png|width=…!`` or
    ``[^doc.pdf]``). We rewrite those to Markdown (``![img.png](<path>)`` /
    ``[doc.pdf](<path>)``) so the image renders inline in the cockpit and the
    agent sees the workspace path. A reference is only rewritten when its filename
    matches a downloaded attachment, so ordinary ``!`` text is left untouched.

    Returns the rewritten text and the set of filenames that were referenced.
    """
    if not text or not name_to_path:
        return text, set()
    referenced: set[str] = set()

    def _img(m: re.Match) -> str:
        name = m.group(1).strip()
        path = name_to_path.get(name)
        if not path:
            return m.group(0)
        referenced.add(name)
        return f"![{name}](<{path}>)"

    def _link(m: re.Match) -> str:
        name = m.group(1).strip()
        path = name_to_path.get(name)
        if not path:
            return m.group(0)
        referenced.add(name)
        return f"[{name}](<{path}>)"

    text = _JIRA_IMG_RE.sub(_img, text)
    text = _JIRA_LINK_RE.sub(_link, text)
    return text, referenced


def apply_issue_to_task(
    db: Session,
    *,
    board: AgentTeamBoard,
    task: AgentTeamTask,
    client: JiraClient,
    key: str,
    actor_id: str | None,
    issue: dict | None = None,
) -> list[str]:
    """Pull ``key`` via ``client`` and write its fields onto ``task``.

    Pass ``issue`` to reuse an already-fetched payload (e.g. from import). Returns
    the list of task fields that changed. Records a ``jira_synced`` activity.
    Caller is responsible for committing the session.
    """
    if issue is None:
        issue = client.get_issue(key)

    # Download attachments first so inline references in the description and
    # comments can be rewritten to point at the freshly-saved workspace files.
    saved, name_to_path = download_issue_attachments(
        db, task=task, client=client, issue=issue
    )
    referenced: set[str] = set()

    changes = build_task_changes(issue, board=board)
    valid_columns = {c["key"] for c in board.columns()}
    applied: list[str] = []

    if "title" in changes:
        task.title = changes["title"]
        applied.append("title")
    if "description" in changes:
        desc = changes["description"]
        if desc:
            desc, ref = rewrite_jira_media(desc, name_to_path)
            referenced |= ref
        task.description = desc
        applied.append("description")
    if changes.get("status") in valid_columns:
        task.status = changes["status"]
        applied.append("status")
    if "priority" in changes:
        task.priority = changes["priority"]
        applied.append("priority")
    if "task_type" in changes:
        task.task_type = changes["task_type"]
        applied.append("task_type")
    if "labels" in changes:
        task.labels_json = json.dumps(changes["labels"])
        applied.append("labels")

    task.jira_key = key
    task.jira_url = client.browse_url(key)

    created, updated = import_comments(
        db, task=task, client=client, key=key,
        name_to_path=name_to_path, referenced=referenced,
    )
    if created or updated:
        applied.append(f"comments(+{created}/~{updated})")

    # Files the description/comments embed inline are surfaced there; only the
    # leftovers go into the catalog note so nothing is shown twice.
    note_files = write_attachments_note(
        db, task=task, saved=saved, referenced=referenced
    )
    if saved:
        applied.append(f"attachments({len(saved)})")

    activity_repo.record(
        db,
        task_id=task.id,
        actor_id=actor_id,
        kind=activity_repo.JIRA_SYNCED,
        data={"jira_key": key, "fields": applied, "attachments_note": note_files},
    )
    return applied


def _comment_body(comment: dict) -> str:
    """Best-effort plain text from a v2 comment body (str) or ADF dict."""
    body = comment.get("body")
    if isinstance(body, str):
        return body
    # Defensive: if a v3-style ADF doc slips through, flatten its text nodes.
    if isinstance(body, dict):
        out: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text" and isinstance(node.get("text"), str):
                    out.append(node["text"])
                for child in node.get("content") or []:
                    walk(child)

        walk(body)
        return " ".join(out)
    return ""


def import_comments(
    db: Session,
    *,
    task: AgentTeamTask,
    client: JiraClient,
    key: str,
    name_to_path: dict[str, str] | None = None,
    referenced: set[str] | None = None,
) -> tuple[int, int]:
    """Sync Jira comments onto the task. Returns ``(created, updated)``.

    Dedup is by Jira comment id so re-syncing never duplicates a thread; an
    already-imported comment whose body changed in Jira is updated in place.
    Inline attachment markup is rewritten to local Markdown via ``name_to_path``;
    every referenced filename is added to ``referenced`` (so the caller can keep
    it out of the catalog note).
    """
    try:
        jira_comments = client.get_comments(key)
    except JiraError:
        # Comments are best-effort: a failure here shouldn't abort the field sync.
        return (0, 0)

    name_to_path = name_to_path or {}
    existing = comments_repo.jira_comments_map(db, task.id)
    created = 0
    updated = 0
    for c in jira_comments:
        cid = str(c.get("id") or "").strip()
        if not cid:
            continue
        body = _comment_body(c).strip()
        if not body:
            continue
        body, ref = rewrite_jira_media(body, name_to_path)
        if referenced is not None:
            referenced |= ref
        author = (c.get("author") or {}).get("displayName") or "Jira"
        prior = existing.get(cid)
        if prior is not None:
            # Reflect edits made on the Jira side.
            if prior.body != body or prior.external_author != author:
                prior.body = body
                prior.external_author = author
                db.flush()
                updated += 1
            continue
        existing[cid] = comments_repo.create_comment(
            db,
            task_id=task.id,
            author_id=None,
            body=body,
            attachments=None,
            visible_to_agents=True,
            external_author=author,
            jira_comment_id=cid,
        )
        created += 1
    return (created, updated)


def download_issue_attachments(
    db: Session, *, task: AgentTeamTask, client: JiraClient, issue: dict
) -> tuple[list[dict], dict[str, str]]:
    """Download an issue's attachments into the workspace (delete & refresh).

    Jira attachments are issue-level. Each file lands under a stable
    ``_notes/jira_<id>/<filename>`` folder so a re-sync overwrites the same path,
    and every prior Jira file is wiped first. Returns ``(saved, name_to_path)``
    where ``name_to_path`` maps each filename to its workspace-relative path so
    inline references in the description/comments can be rewritten.
    """
    fields = issue.get("fields") or {}
    atts = fields.get("attachment")
    atts = atts if isinstance(atts, list) else []

    # Drop the previous catalog note (its files are removed by the prefix sweep
    # below; older imports may still use random ids, so delete those explicitly).
    prior = comments_repo.jira_comments_map(db, task.id).get(_ATTACH_COMMENT_ID)
    if prior is not None:
        for a in prior.attachments():
            aid = a.get("id")
            if aid:
                attachments_store.delete_attachment(
                    task.workspace_path,
                    subdir=attachments_store.COMMENT_DIR,
                    att_id=aid,
                )
        db.delete(prior)
        db.flush()
    attachments_store.delete_jira_attachments(task.workspace_path)

    if not atts:
        return [], {}

    ensure_task_workspace(task.workspace_path)
    saved: list[dict] = []
    name_to_path: dict[str, str] = {}
    for a in atts:
        url = a.get("content")
        if not url:
            continue
        try:
            content = client.download(url)
        except JiraError:
            continue  # best-effort: skip a file that won't download
        jid = str(a.get("id") or "").strip()
        meta = attachments_store.save_attachment(
            task.workspace_path,
            subdir=attachments_store.COMMENT_DIR,
            filename=a.get("filename") or "file",
            content=content,
            media_type=a.get("mimeType") or "application/octet-stream",
            att_id=(attachments_store.JIRA_ATT_PREFIX + jid) if jid else None,
        )
        saved.append(meta)
        name_to_path[meta["filename"]] = meta["path"]
    return saved, name_to_path


def write_attachments_note(
    db: Session,
    *,
    task: AgentTeamTask,
    saved: list[dict],
    referenced: set[str],
) -> int:
    """Surface attachments not embedded inline as one catalog note.

    Files already referenced in the description/comments render there, so only
    the leftovers are listed here (avoiding a duplicate display). The note's
    workspace-relative paths keep those files reachable by the agent. Returns the
    number of files placed in the note.
    """
    leftover = [s for s in saved if s.get("filename") not in referenced]
    if not leftover:
        return 0
    comments_repo.create_comment(
        db,
        task_id=task.id,
        author_id=None,
        body="Attachments from Jira",
        attachments=leftover,
        visible_to_agents=True,
        external_author="Jira",
        jira_comment_id=_ATTACH_COMMENT_ID,
    )
    return len(leftover)


def _jql_in(values: list) -> str:
    """Render a JQL ``in (...)`` value list, quoting/escaping each entry."""
    return ", ".join('"' + str(v).replace('"', '\\"') + '"' for v in values)


def build_search_jql(project_key: str, flt: dict) -> str:
    """Build the import JQL for a project, narrowed by the board's Jira filter.

    The filter is expressed in Jira-native, project-agnostic terms so no project
    metadata lookup is needed:
      * ``issue_types``        → ``issuetype in (...)`` (Jira type names)
      * ``status_categories``  → ``statusCategory in (...)`` (To Do/In Progress/Done)
      * ``updated_within_days``→ ``updated >= -Nd``
    """
    clauses = [f'project = "{project_key}"']

    types = flt.get("issue_types")
    if types:
        clauses.append(f"issuetype in ({_jql_in(types)})")

    cats = flt.get("status_categories")
    if cats:
        clauses.append(f"statusCategory in ({_jql_in(cats)})")

    days = flt.get("updated_within_days")
    if isinstance(days, int) and days > 0:
        clauses.append(f"updated >= -{days}d")

    return " AND ".join(clauses) + " ORDER BY updated DESC"


def task_matches_filter(task: AgentTeamTask, flt: dict) -> bool:
    """Whether a task satisfies the board's batch-sync filter (AND of clauses)."""
    statuses = flt.get("statuses")
    if statuses and task.status not in statuses:
        return False
    task_types = flt.get("task_types")
    if task_types and task.task_type not in task_types:
        return False
    assignees = flt.get("assignee_ids")
    if assignees and task.assignee_id not in assignees:
        return False
    return True


@dataclass
class BatchResult:
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "synced": self.synced,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors or [],
        }


def sync_board(
    db: Session,
    *,
    board: AgentTeamBoard,
    tasks: list[AgentTeamTask],
    actor_id: str | None,
) -> BatchResult:
    """Sync every task that has a linked key and matches the board filter.

    Builds one client for the whole run. Per-task Jira failures are counted
    rather than aborting the batch.
    """
    flt = board.jira_sync_filter()
    result = BatchResult(errors=[])
    client = build_client(board)

    for task in tasks:
        if not task.jira_key or not task_matches_filter(task, flt):
            result.skipped += 1
            continue
        try:
            apply_issue_to_task(
                db,
                board=board,
                task=task,
                client=client,
                key=task.jira_key,
                actor_id=actor_id,
            )
            result.synced += 1
        except JiraError as exc:
            result.failed += 1
            assert result.errors is not None
            result.errors.append(f"{task.human_key} ({task.jira_key}): {exc.message}")

    return result
