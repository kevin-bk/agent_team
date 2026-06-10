"""Attachment storage for task chat and comments, backed by the workspace.

Uploaded files live under the task's own workspace so the agent can open them
with its file tools and the web file routes can serve previews/downloads:

* chat attachments (agent mentions) → ``_attachments/<id>/<filename>``
* comment attachments               → ``_notes/<id>/<filename>``

Each ``<id>`` is an opaque token (one folder per upload) so a filename never
collides and the id stays slash-free for use in REST paths.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from agent_team.features.board.workspace import resolve_in_workspace

CHAT_DIR = "_attachments"
COMMENT_DIR = "_notes"

#: Folder-id prefix for files downloaded from a Jira issue. Lets a re-sync wipe
#: every Jira attachment (referenced inline or not) without touching the user's
#: own comment uploads in the same ``_notes`` dir.
JIRA_ATT_PREFIX = "jira_"


def _kind_for(media_type: str) -> str:
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("text/") or media_type in {
        "application/json",
        "application/xml",
    }:
        return "text"
    return "binary"


def save_attachment(
    workspace_path: str,
    *,
    subdir: str,
    filename: str,
    content: bytes,
    media_type: str,
    att_id: str | None = None,
) -> dict:
    """Persist one upload and return its metadata dict.

    The returned ``path`` is workspace-relative so the file routes (and the
    agent) can address it; ``id`` identifies the upload folder for deletion.

    Pass ``att_id`` to pin the folder name (e.g. a stable ``jira_<id>`` so a
    re-sync overwrites the same path); it must be slash-free. Otherwise a random
    id is generated so a filename never collides across uploads.
    """
    safe_id = (att_id or "").strip()
    if safe_id and ("/" in safe_id or "\\" in safe_id or ".." in safe_id):
        safe_id = ""
    att_id = safe_id or uuid.uuid4().hex
    safe_name = Path(filename or "file").name or "file"
    rel = f"{subdir}/{att_id}/{safe_name}"
    target = resolve_in_workspace(workspace_path, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {
        "id": att_id,
        "kind": _kind_for(media_type),
        "media_type": media_type or "application/octet-stream",
        "filename": safe_name,
        "size_bytes": len(content),
        "path": rel,
    }


def delete_attachment(workspace_path: str, *, subdir: str, att_id: str) -> bool:
    """Remove an upload folder; returns whether anything was deleted."""
    safe_id = (att_id or "").strip()
    if not safe_id or "/" in safe_id or "\\" in safe_id or ".." in safe_id:
        return False
    folder = resolve_in_workspace(workspace_path, f"{subdir}/{safe_id}")
    if not folder.is_dir():
        return False
    for child in folder.iterdir():
        if child.is_file():
            child.unlink()
    folder.rmdir()
    return True


def delete_jira_attachments(workspace_path: str) -> int:
    """Remove every Jira-downloaded attachment folder; returns how many were removed.

    Used on re-sync so inline-referenced files (which aren't tracked by any
    comment record) are cleaned up alongside the catalog note's files.
    """
    base = resolve_in_workspace(workspace_path, COMMENT_DIR)
    if not base.is_dir():
        return 0
    removed = 0
    for folder in base.iterdir():
        if not (folder.is_dir() and folder.name.startswith(JIRA_ATT_PREFIX)):
            continue
        for child in folder.iterdir():
            if child.is_file():
                child.unlink()
        folder.rmdir()
        removed += 1
    return removed


def resolve_chat_attachments(workspace_path: str, ids: list[str]) -> list[dict]:
    """Resolve chat attachment ids to ``{filename, path}`` (skips missing)."""
    found: list[dict] = []
    for att_id in ids or []:
        safe_id = (att_id or "").strip()
        if not safe_id or "/" in safe_id or "\\" in safe_id or ".." in safe_id:
            continue
        folder = resolve_in_workspace(workspace_path, f"{CHAT_DIR}/{safe_id}")
        if not folder.is_dir():
            continue
        for child in sorted(folder.iterdir()):
            if child.is_file():
                found.append(
                    {
                        "filename": child.name,
                        "path": f"{CHAT_DIR}/{safe_id}/{child.name}",
                    }
                )
    return found
