"""Agent tool: let an agent *see* an image file from its workspace.

The standard file tools only read text, and an agent_team task run feeds the
model a plain-text message — so images downloaded into the task workspace (e.g.
Jira attachments referenced in a description or comment) are otherwise invisible
to the agent. ``view_image`` returns the file as a multimodal content block so a
vision-capable model can actually look at it.

It is contributed by the agent_team plugin's ``tool_factories()``, so the tool is
only available to agents while that plugin is enabled. The working directory is
bound at graph-build time and, for a task run, resolves to the shared task folder
via the same context-local override the standard file/shell tools honor.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Inline image types the supported providers (OpenAI/Anthropic/Gemini) accept.
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
#: Cap the raw file size so one huge image can't blow up the context window.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _resolve_root(agent_alias: str, settings: dict[str, str]) -> str | None:
    """Resolve the tool's workspace root, matching the standard file tools.

    Using the same resolver means ``view_image`` and ``read_file`` share a root,
    so a path that works for one works for the other. For a task run this is the
    shared task workspace (via the context-local override set during build).
    """
    try:
        from plugins.standard_tools.tools.file_tools import _resolve_work_dir

        return (_resolve_work_dir(agent_alias, settings) or "").strip() or None
    except ImportError:
        try:
            from plugins.standard_tools.tools.workspace_override import (
                get_workspace_override,
            )

            return (get_workspace_override() or "").strip() or None
        except ImportError:
            return None


def get_image_tools(agent_alias: str, settings: dict[str, str]) -> list[Any]:
    """Create the ``view_image`` tool for an agent (empty if langchain absent)."""
    try:
        from langchain_core.tools import tool
    except ImportError:
        return []

    root = _resolve_root(agent_alias, settings)

    @tool(parse_docstring=True, response_format="content_and_artifact")
    def view_image(path: str) -> tuple[list[dict], dict]:
        """View an image file from the workspace so you can see its contents.

        Use this for images (PNG/JPG/GIF/WebP) referenced by the task, such as
        Jira attachments embedded in the description or notes. The ordinary file
        reading tools only return text and cannot show you an image.

        Args:
            path: Workspace-relative path to the image, e.g.
                ``_notes/jira_123/screenshot.png``.
        """
        if not root:
            return ([_text("No workspace is configured for this agent.")], {})
        rel = (path or "").strip().lstrip("/")
        if not rel:
            return ([_text("A file path is required.")], {})
        base = Path(root).resolve()
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            return ([_text(f"Path is outside the workspace: {path}")], {})
        if not target.is_file():
            return ([_text(f"File not found: {path}")], {})
        mime = _IMAGE_MIME.get(target.suffix.lower())
        if not mime:
            return (
                [_text(f"'{target.suffix}' is not a viewable image. "
                       "Supported: PNG, JPG, GIF, WebP.")],
                {},
            )
        data = target.read_bytes()
        if len(data) > _MAX_IMAGE_BYTES:
            return (
                [_text(f"Image is too large to view ({len(data)} bytes; "
                       f"limit {_MAX_IMAGE_BYTES}).")],
                {},
            )
        data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        content = [
            _text(f"Image at {rel}:"),
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        return content, {"path": rel, "mime_type": mime, "bytes": len(data)}

    return [view_image]


def _text(value: str) -> dict:
    return {"type": "text", "text": value}
