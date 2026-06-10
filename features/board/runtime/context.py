"""Build the per-run agent input from task context.

The input is a task header (key, title, description, shared workspace path)
followed by any human notes left on the task, then the user's prompt. Notes give
the agent the context users captured on the task; each note is attributed to its
author so the agent knows who said what, and notes that carry attachments are
surfaced as workspace-relative file pointers so the agent can open and study
those files itself with its file tools.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_team.features.board.models import AgentTeamTask
from agent_team.features.board.workspace import ensure_task_workspace


def _format_notes(notes: Sequence[dict] | None) -> str:
    """Render task notes as a context block, or "" when there is nothing to add.

    Notes are expected oldest-first. Each note may carry an ``author`` display
    name, a ``created_at`` timestamp string, a text ``body`` and a list of
    ``attachments`` (each a dict with a workspace-relative ``path`` and optional
    ``filename``). Each note is shown as a small block headed by its author and
    time; an attached file is listed as ``Attached file: <path>`` so the agent
    knows it can open that file to research the user's input.
    """
    if not notes:
        return ""
    out: list[str] = [
        "User notes on this task (oldest first; attached file paths are inside "
        "the task workspace):"
    ]
    for note in notes:
        author = (note.get("author") or "").strip() or "a user"
        when = (note.get("created_at") or "").strip()
        body = (note.get("body") or "").strip()
        files = [att for att in (note.get("attachments") or []) if att.get("path")]
        if not body and not files:
            continue
        out.append(f"- {author}" + (f" at {when}" if when else "") + ":")
        if body:
            out.append(f"  {body}")
        for att in files:
            name = att.get("filename")
            label = f"`{att['path']}`" + (f" ({name})" if name else "")
            out.append(f"  Attached file: {label}")
    # Only the header was added (notes had neither body nor usable attachments).
    return "\n".join(out) if len(out) > 1 else ""


def build_task_context(
    task: AgentTeamTask, prompt: str, notes: Sequence[dict] | None = None
) -> str:
    """Return the user-message text: task header, notes, then the prompt."""
    lines = [f"Task {task.human_key}: {task.title}"]
    if task.description:
        lines.append("")
        lines.append(task.description.strip())
    lines.append("")
    lines.append(f"Shared workspace folder: {task.workspace_path}")
    note_block = _format_notes(notes)
    if note_block:
        # Wrap notes in a machine-readable <task_notes> block so the agent has a
        # hard boundary between background notes and the user's actual message
        # (rather than relying on a text separator alone).
        lines.append("")
        lines.append("<task_notes>")
        lines.append(note_block)
        lines.append("</task_notes>")
    # Everything above is task context; clearly delimit the user's actual request
    # that follows so the agent does not confuse background notes with the ask.
    lines.append("")
    lines.append("--- User's current message ---")
    lines.append("")
    lines.append(prompt.strip())
    return "\n".join(lines)


def prepare_workspace(task: AgentTeamTask) -> str:
    """Ensure the task's workspace folder exists and return its path."""
    return ensure_task_workspace(task.workspace_path)
