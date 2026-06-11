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


def _format_notes(notes: Sequence[dict] | None, *, new_only: bool = False) -> str:
    """Render task notes as a context block, or "" when there is nothing to add.

    Notes are expected oldest-first. Each note may carry an ``author`` display
    name, a ``created_at`` timestamp string, a text ``body`` and a list of
    ``attachments`` (each a dict with a workspace-relative ``path`` and optional
    ``filename``). Each note is shown as a small block headed by its author and
    time; an attached file is listed as ``Attached file: <path>`` so the agent
    knows it can open that file to research the user's input.

    ``new_only`` only changes the header wording: on a follow-up turn the caller
    passes just the notes added since the previous turn, so the agent is told
    these are *new* notes rather than the full list.
    """
    if not notes:
        return ""
    header = (
        "New notes added since the last message (attached file paths are inside "
        "the task workspace):"
        if new_only
        else "User notes on this task (oldest first; attached file paths are "
        "inside the task workspace):"
    )
    out: list[str] = [header]
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


def _format_repos(repos: Sequence[dict] | None) -> str:
    """Render the task's checked-out code repos as a context block, or ""."""
    items = [r for r in (repos or []) if r.get("path")]
    if not items:
        return ""
    out = [
        "Code repositories checked out in this workspace (each is an independent "
        "git clone you can read, edit, build, and commit in — paths are relative "
        "to the shared workspace folder). Each is already on its own task branch; "
        "commit your work there, do not switch to the default branch:"
    ]
    pushable = False
    for repo in items:
        branch = (repo.get("branch") or "").strip()
        suffix = f" (branch {branch})" if branch else ""
        if repo.get("can_push"):
            suffix += " — push enabled"
            pushable = True
        out.append(f"- `{repo['path']}/`{suffix}")
    if pushable:
        out.append(
            "To publish commits to a repo's remote, use the `git_push` tool "
            "(it pushes with managed credentials — you don't need a token). "
            "Plain `git push` will not reach the remote."
        )
    return "\n".join(out)


def build_task_context(
    task: AgentTeamTask,
    prompt: str,
    notes: Sequence[dict] | None = None,
    *,
    full: bool = True,
    include_description: bool = True,
    repos: Sequence[dict] | None = None,
) -> str:
    """Return the user-message text for one run (turn).

    On the **first turn** of a thread (``full=True``) the message carries the
    whole task context — header, description, workspace path and all notes — so
    the agent has everything up front.

    On a **follow-up turn** (``full=False``) the prior context already lives in
    the thread history (same checkpointer thread), so re-sending it only wastes
    tokens and cannot be prompt-cached on the turn it is sent. We therefore send
    only the *delta*: the updated description (when ``include_description``) and
    the notes added since the previous turn (``notes`` is pre-filtered by the
    caller), then the prompt. When nothing changed, just the prompt is returned —
    keeping the cacheable prefix (tools + system + prior history) untouched.
    """
    sections: list[str] = []

    if full:
        header = [f"Task {task.human_key}: {task.title}"]
        if task.description:
            header.append("")
            header.append(task.description.strip())
        header.append("")
        header.append(f"Shared workspace folder: {task.workspace_path}")
        repo_block = _format_repos(repos)
        if repo_block:
            header.append("")
            header.append(repo_block)
        sections.append("\n".join(header))
    elif include_description and task.description:
        sections.append("The task description was updated:\n\n" + task.description.strip())

    note_block = _format_notes(notes, new_only=not full)
    if note_block:
        # Wrap notes in a machine-readable <task_notes> block so the agent has a
        # hard boundary between background notes and the user's actual message
        # (rather than relying on a text separator alone).
        sections.append("<task_notes>\n" + note_block + "\n</task_notes>")

    user_message = prompt.strip()
    if not sections:
        # Pure follow-up with no new context: send only the prompt so the prompt
        # cache reuses the entire prior prefix.
        return user_message
    # Everything above is task context; clearly delimit the user's actual request
    # that follows so the agent does not confuse background notes with the ask.
    sections.append("--- User's current message ---\n\n" + user_message)
    return "\n\n".join(sections)


def prepare_workspace(task: AgentTeamTask) -> str:
    """Ensure the task's workspace folder exists and return its path."""
    return ensure_task_workspace(task.workspace_path)
