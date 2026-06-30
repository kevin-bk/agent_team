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
#: Jira wiki-markup external link: ``[text|url]`` / ``[url]`` (optionally with a
#: trailing ``|smart-link``/``|smart-card`` render hint). Excludes attachment
#: links (``[^name]``) and user mentions (``[~accountid]``).
_JIRA_WIKI_LINK_RE = re.compile(r"\[(?![\^~])([^\[\]\n]+?)\]")
#: Trailing render hints Jira appends to smart links — dropped on conversion.
_JIRA_LINK_HINTS = {"smart-link", "smart-card", "smartlink", "smartcard"}
#: Jira wiki-markup code macro: ``{code}…{code}`` / ``{code:lang}…{code}`` /
#: ``{code:language=js|title=…}…{code}``. DOTALL so it spans multiple lines.
_JIRA_CODE_RE = re.compile(r"\{code(?::([^}]*))?\}(.*?)\{code\}", re.DOTALL)
#: Jira wiki-markup preformatted block: ``{noformat}…{noformat}``.
_JIRA_NOFORMAT_RE = re.compile(r"\{noformat\}(.*?)\{noformat\}", re.DOTALL)
#: Jira wiki-markup inline monospace: ``{{text}}``.
_JIRA_MONO_RE = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)
#: A Markdown fenced code block, used to avoid rewriting markup *inside* code.
_MD_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
#: Jira block macros that wrap content: ``{quote}``, ``{panel[:…]}``, ``{color[:…]}``.
_JIRA_QUOTE_RE = re.compile(r"\{quote\}(.*?)\{quote\}", re.DOTALL)
_JIRA_PANEL_RE = re.compile(r"\{panel(?::[^}]*)?\}(.*?)\{panel\}", re.DOTALL)
_JIRA_COLOR_RE = re.compile(r"\{color(?::[^}]*)?\}(.*?)\{color\}", re.DOTALL)
#: Line-level Jira markup. ``h1.``–``h6.`` headings, ``bq.`` blockquote, and
#: ``*``/``#`` (possibly nested) list items — the latter matters because a Jira
#: numbered item ``# x`` would otherwise render as a Markdown H1.
_JIRA_HEADING_RE = re.compile(r"^h([1-6])\.\s+(.*)$")
_JIRA_BQ_RE = re.compile(r"^bq\.\s+(.*)$")
_JIRA_LIST_RE = re.compile(r"^([*#]+)\s+(.*)$")
#: Inline Jira markup: ``*bold*`` (single-star bold) and ``[~user]`` mentions.
_JIRA_BOLD_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_JIRA_MENTION_RE = re.compile(r"\[~([^\]\n]+)\]")

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


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "mailto:"))


def _apply_outside_fences(text: str, fn) -> str:
    """Run ``fn`` on the parts of ``text`` that aren't inside a ```` ``` ```` block."""
    out: list[str] = []
    last = 0
    for m in _MD_FENCE_RE.finditer(text):
        out.append(fn(text[last : m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def _code_lang(params: str | None) -> str:
    """Pull the language out of a Jira ``{code:…}`` parameter string."""
    if not params:
        return ""
    for part in params.split("|"):
        part = part.strip()
        if "=" in part:
            key, val = part.split("=", 1)
            if key.strip().lower() in ("language", "lang"):
                return val.strip()
        elif part:  # bare token like ``{code:javascript}``
            return part
    return ""


def rewrite_jira_code(text: str) -> str:
    """Convert Jira code/preformat/monospace markup to Markdown.

    Jira's v2 API returns ``{code:lang}…{code}`` and ``{noformat}…{noformat}``
    blocks plus inline ``{{monospace}}``. Untouched they render as literal
    ``{code}`` noise in the Markdown viewer (and the agent sees the same), so we
    turn blocks into fenced code (carrying the language when given) and inline
    spans into backticks. Inline conversion skips inside fenced blocks so code
    bodies stay verbatim.
    """
    if not text:
        return text

    if "{code" in text:
        text = _JIRA_CODE_RE.sub(
            lambda m: f"\n```{_code_lang(m.group(1))}\n{m.group(2).strip(chr(10))}\n```\n",
            text,
        )
    if "{noformat}" in text:
        text = _JIRA_NOFORMAT_RE.sub(
            lambda m: f"\n```\n{m.group(1).strip(chr(10))}\n```\n", text
        )
    if "{{" in text:
        text = _apply_outside_fences(
            text, lambda s: _JIRA_MONO_RE.sub(lambda m: f"`{m.group(1)}`", s)
        )
    return text


def rewrite_jira_links(text: str) -> str:
    """Convert Jira wiki-markup external links to Markdown.

    Jira's v2 API returns descriptions/comments as wiki markup, where links look
    like ``[label|url]`` or ``[url]`` — and "smart links" tack on a render hint
    (``[url|url|smart-link]``). Left untouched these render as literal ``[…|…]``
    brackets in the Markdown viewer (and the agent sees the same noise), so we
    rewrite them to ``[label](url)`` (or a bare ``<url>`` autolink when there is
    no distinct label). Non-link brackets, attachment links (``[^name]``) and
    user mentions (``[~id]``) are left as-is.
    """
    if not text or "[" not in text:
        return text

    def _sub(m: re.Match) -> str:
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) >= 2 and parts[-1].lower() in _JIRA_LINK_HINTS:
            parts = parts[:-1]
        if len(parts) == 1:
            target = parts[0]
            return f"<{target}>" if _looks_like_url(target) else m.group(0)
        label, target = parts[0], parts[1]
        if not _looks_like_url(target):
            return m.group(0)
        if not label or label == target:
            return f"<{target}>"
        return f"[{label}]({target})"

    # Skip fenced code so e.g. JS array/bracket syntax isn't mistaken for a link.
    return _apply_outside_fences(text, lambda s: _JIRA_WIKI_LINK_RE.sub(_sub, s))


def _blockquote(inner: str) -> str:
    """Wrap ``inner`` lines as a Markdown blockquote."""
    lines = inner.strip("\n").split("\n")
    body = "\n".join(("> " + ln) if ln.strip() else ">" for ln in lines)
    return f"\n{body}\n"


def _rewrite_jira_block_macros(text: str) -> str:
    """Convert ``{quote}``/``{panel}``/``{color}`` wrappers (outside code)."""
    if "{quote}" not in text and "{panel" not in text and "{color" not in text:
        return text

    def fn(s: str) -> str:
        s = _JIRA_QUOTE_RE.sub(lambda m: _blockquote(m.group(1)), s)
        s = _JIRA_PANEL_RE.sub(lambda m: _blockquote(m.group(1)), s)
        s = _JIRA_COLOR_RE.sub(lambda m: m.group(1), s)  # drop color, keep text
        return s

    return _apply_outside_fences(text, fn)


def _list_line(m: "re.Match") -> str:
    """Render a Jira ``*``/``#`` list item as a Markdown list item."""
    markers = m.group(1)
    depth = len(markers)
    bullet = "1." if markers[-1] == "#" else "-"
    return "  " * (depth - 1) + bullet + " " + m.group(2)


def _parse_table_row(line: str) -> list[str]:
    """Split a Jira table row (``|a|b|`` or ``||h||h||``) into cell strings."""
    s = line.strip().replace("||", "|")
    cells = [c.strip() for c in s.split("|")]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _convert_table(lines: list[str], start: int) -> tuple[list[str], int]:
    """Turn a run of Jira table rows into a GFM table; returns (rows, consumed)."""
    rows: list[list[str]] = []
    j = start
    while j < len(lines) and lines[j].strip().startswith("|"):
        rows.append(_parse_table_row(lines[j]))
        j += 1
    ncols = max((len(r) for r in rows), default=0)

    def fmt(cells: list[str]) -> str:
        padded = cells + [""] * (ncols - len(cells))
        return "| " + " | ".join(padded) + " |"

    out = [fmt(rows[0]), "| " + " | ".join(["---"] * ncols) + " |"]
    out.extend(fmt(r) for r in rows[1:])
    return out, (j - start)


def _rewrite_jira_lines(text: str) -> str:
    """Apply line-level Jira markup (headings, lists, blockquotes, tables).

    Walks line by line, skipping fenced code, so block constructs are converted
    without touching code bodies.
    """
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
        if line.strip().startswith("|"):
            block, consumed = _convert_table(lines, i)
            if out and out[-1].strip() != "":
                out.append("")  # GFM tables want a preceding blank line
            out.extend(block)
            i += consumed
            if i < n and lines[i].strip() != "":
                out.append("")
            continue
        heading = _JIRA_HEADING_RE.match(line)
        if heading:
            out.append("#" * int(heading.group(1)) + " " + heading.group(2))
            i += 1
            continue
        bq = _JIRA_BQ_RE.match(line)
        if bq:
            out.append("> " + bq.group(1))
            i += 1
            continue
        listed = _JIRA_LIST_RE.match(line)
        if listed:
            out.append(_list_line(listed))
            i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _mention(m: "re.Match") -> str:
    """Render a Jira ``[~user]`` mention as ``@user`` (opaque ids → ``@user``)."""
    value = m.group(1).strip()
    if value.lower().startswith("accountid:"):
        return "@user"
    return "@" + value.lstrip("@")


def _rewrite_jira_inline(s: str) -> str:
    """Inline Jira markup: single-star bold and ``[~user]`` mentions."""
    s = _JIRA_BOLD_RE.sub(lambda m: f"**{m.group(1)}**", s)
    s = _JIRA_MENTION_RE.sub(_mention, s)
    return s


def jira_to_markdown(text: str, name_to_path: dict[str, str] | None = None) -> tuple[str, set]:
    """Convert a full Jira wiki-markup body to Markdown.

    Runs the whole pipeline in a fence-safe order: attachments → code blocks (so
    later steps can skip them) → block macros → line-level constructs → inline →
    links. Returns the Markdown plus the set of attachment filenames referenced
    inline (so the caller can avoid duplicating them in the attachments note).
    """
    if not text:
        return text, set()
    referenced: set = set()
    if name_to_path:
        text, referenced = rewrite_jira_media(text, name_to_path)
    text = rewrite_jira_code(text)
    text = _rewrite_jira_block_macros(text)
    text = _rewrite_jira_lines(text)
    text = _apply_outside_fences(text, _rewrite_jira_inline)
    text = rewrite_jira_links(text)
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
            desc, ref = jira_to_markdown(desc, name_to_path)
            referenced |= ref
        task.description = desc
        applied.append("description")
    if board.jira_sync_status and changes.get("status") in valid_columns:
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

    # Map Jira people to local users. Only set when a confident match exists;
    # never wipe an existing assignment when Jira gives us nothing to match on.
    assignee_uid = _resolve_person(db, board=board, account=changes.get("assignee"), client=client)
    if assignee_uid and assignee_uid != task.assignee_id:
        task.assignee_id = assignee_uid
        applied.append("assignee")
    reporter_uid = _resolve_person(db, board=board, account=changes.get("reporter"), client=client)
    if reporter_uid and reporter_uid != task.reporter_id:
        task.reporter_id = reporter_uid
        applied.append("reporter")

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


def _user_id_for_email(db: Session, email: str | None) -> str | None:
    """Resolve a local user id from a Jira account email (case-insensitive)."""
    if not email:
        return None
    from sqlalchemy import func

    from core.database.models import User

    row = (
        db.query(User.id)
        .filter(func.lower(User.email) == email.strip().lower())
        .first()
    )
    return row[0] if row else None


def _resolve_person(
    db: Session,
    *,
    board: AgentTeamBoard,
    account: dict | None,
    client: object | None,
) -> str | None:
    """Map a Jira person ``{account_id, email, display_name}`` to a local user id.

    Resolution order, stopping at the first confident match:

    1. A board member already bound to this Jira ``accountId`` (the warm cache).
    2. A user whose email matches (works for the syncing account itself and any
       account whose email Jira exposes).
    3. Email hidden? Warm the member→accountId cache via Jira user-search and
       retry (1) — this is what lets non-owner assignees map at all.
    4. Last resort: a board member whose name matches the Jira ``displayName``.

    Whenever (2)/(4) lands on a board member and we know the ``accountId``, we
    cache it on the membership so later syncs are a direct lookup.
    """
    if not account:
        return None
    account_id = account.get("account_id")
    email = account.get("email")
    display = account.get("display_name")

    if account_id:
        uid = _member_user_by_account(db, board.id, account_id)
        if uid:
            return uid

    if email:
        uid = _user_id_for_email(db, email)
        if uid:
            if account_id:
                _bind_member_account(db, board.id, uid, account_id)
            return uid

    if account_id and client is not None and hasattr(client, "search_users"):
        _warm_member_accounts(db, board, client)
        uid = _member_user_by_account(db, board.id, account_id)
        if uid:
            return uid

    if display:
        uid = _member_user_by_name(db, board.id, display)
        if uid:
            if account_id:
                _bind_member_account(db, board.id, uid, account_id)
            return uid

    return None


def _member_user_by_account(db: Session, board_id: str, account_id: str) -> str | None:
    """User id of the board member bound to ``account_id`` (or None)."""
    from agent_team.features.board.models import AgentTeamBoardMember

    row = (
        db.query(AgentTeamBoardMember.user_id)
        .filter(
            AgentTeamBoardMember.board_id == board_id,
            AgentTeamBoardMember.jira_account_id == account_id,
        )
        .first()
    )
    return row[0] if row else None


def _member_user_by_name(db: Session, board_id: str, display: str) -> str | None:
    """User id of the board member whose name matches ``display`` (unique only)."""
    from sqlalchemy import func

    from agent_team.features.board.models import AgentTeamBoardMember
    from core.database.models import User

    name = display.strip().lower()
    if not name:
        return None
    rows = (
        db.query(AgentTeamBoardMember.user_id)
        .join(User, User.id == AgentTeamBoardMember.user_id)
        .filter(
            AgentTeamBoardMember.board_id == board_id,
            (func.lower(User.full_name) == name) | (func.lower(User.username) == name),
        )
        .all()
    )
    # Only trust a unique match — names are not guaranteed unique.
    return rows[0][0] if len(rows) == 1 else None


def _bind_member_account(db: Session, board_id: str, user_id: str, account_id: str) -> None:
    """Cache ``account_id`` on a board member (no-op if already set or no row)."""
    from agent_team.features.board.models import AgentTeamBoardMember

    member = (
        db.query(AgentTeamBoardMember)
        .filter(
            AgentTeamBoardMember.board_id == board_id,
            AgentTeamBoardMember.user_id == user_id,
        )
        .first()
    )
    if member is not None and not member.jira_account_id:
        member.jira_account_id = account_id
        db.flush()


def _warm_member_accounts(db: Session, board: AgentTeamBoard, client: object) -> None:
    """Resolve & cache the Jira accountId for members that don't have one yet.

    For each unbound member we search Jira by their email; Jira matches it
    server-side and returns the accountId even when the email is hidden on issue
    payloads. We only bind when the match is unambiguous (a result whose email
    matches, or a single result) so we never mis-attribute an assignee.
    """
    from agent_team.features.board.repositories import members as members_repo

    search = getattr(client, "search_users", None)
    if search is None:
        return
    for member, user in members_repo.list_members(db, board.id):
        if member.jira_account_id or not (user.email or "").strip():
            continue
        try:
            results = search(user.email) or []
        except Exception:  # never let a lookup break the sync
            continue
        account_id = _pick_account_id(results, user.email)
        if account_id:
            member.jira_account_id = account_id
    db.flush()


def _pick_account_id(results: list, email: str) -> str | None:
    """Choose an unambiguous accountId from Jira user-search results."""
    target = (email or "").strip().lower()
    candidates = [r for r in results if isinstance(r, dict) and r.get("accountId")]
    if not candidates:
        return None
    # Prefer an exact email match when Jira exposes it.
    for r in candidates:
        if (r.get("emailAddress") or "").strip().lower() == target and target:
            return str(r["accountId"])
    # Otherwise only trust a single result (avoid mis-binding on ambiguity).
    if len(candidates) == 1:
        return str(candidates[0]["accountId"])
    return None


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
        body, ref = jira_to_markdown(body, name_to_path)
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


def build_keys_jql(keys: list[str]) -> str:
    """Build JQL that fetches a specific set of issue keys (any project)."""
    return f"key in ({_jql_in(keys)}) ORDER BY updated DESC"


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
