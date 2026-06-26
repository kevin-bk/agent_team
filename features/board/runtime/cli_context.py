"""Context delivery for direct CLI conversations.

A direct CLI agent (Claude / Cursor / Codex) is driven straight over ACP without
the LLM orchestrator, so it has no system prompt and no checkpointer thread to
carry the task context. Instead we materialise the task brief as files in the
task workspace and let each CLI discover them through its own native mechanism:

* ``.agent-team/TASK.md`` — the single source of truth (task header, description,
  user notes, repo layout). Regenerated every turn so it always reflects the
  latest task state.
* ``CLAUDE.md`` / ``AGENTS.md`` / ``.cursor/rules/agent-team-task.mdc`` — thin
  pointer files at the workspace root that each engine auto-loads. They only tell
  the agent to read ``.agent-team/TASK.md``. Kept at the *workspace* root (not
  inside any repo clone) so they never clobber a repo's own ``CLAUDE.md`` and
  never dirty a repo's git status.

The per-turn prompt carries the user's message. On the *first* turn of a
conversation we also append a light parenthetical nudge pointing at
``.agent-team/TASK.md`` — auto-loading of the native pointer files is not
guaranteed for every engine (notably the Claude ACP adapter, which runs the
Agent SDK in "isolation by default"), so this one-line hint guarantees the
agent knows where the task brief lives. Follow-up turns send the raw message
only, since the brief is already in the session history.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

from agent_team.features.board.models import AgentTeamTask
from agent_team.features.board.runtime.context import _format_notes

logger = logging.getLogger(__name__)

#: Workspace-relative path of the generated task brief.
BRIEF_REL_PATH = os.path.join(".agent-team", "TASK.md")

_POINTER_BODY = (
    "Task context for this session — the task description, user notes, and the "
    "code repositories checked out here — lives in `.agent-team/TASK.md`. "
    "Read that file before working so you understand the task."
)

#: First-turn nudge appended to the user's message so the agent always knows
#: where the brief is, even if its native context files were not auto-loaded.
_FIRST_TURN_NUDGE = (
    "(Read `.agent-team/TASK.md` for the full context of this task.)"
)

#: Follow-up nudge used when the user added task notes since the previous turn.
#: The brief file is already refreshed with them; this just tells the agent to
#: re-read it (its session history does not include the new notes).
_NEW_NOTES_NUDGE = (
    "(New notes were added to this task — re-read `.agent-team/TASK.md`.)"
)


def build_prompt(
    user_text: str, *, first_turn: bool, has_new_notes: bool = False
) -> str:
    """Build the per-turn CLI prompt.

    On the first turn of a conversation the user's message gets a light
    parenthetical nudge pointing at the task brief. On a later turn the nudge is
    only added when new task notes arrived since the previous turn, telling the
    agent to re-read the (already refreshed) brief. Otherwise the message is sent
    verbatim — the brief is already in the session history.
    """
    text = (user_text or "").strip()
    if first_turn:
        nudge: str | None = _FIRST_TURN_NUDGE
    elif has_new_notes:
        nudge = _NEW_NOTES_NUDGE
    else:
        nudge = None
    if nudge is None:
        return text
    if not text:
        return nudge
    return f"{text}\n\n{nudge}"


def _render_repos_block(repos: Sequence[dict] | None) -> str:
    """Render the checked-out repos for a CLI agent, or "".

    A direct CLI has no ``git_push`` tool, but its working copy is wired so a
    plain ``git push`` reaches the **real remote** on the task branch (managed
    credentials, default branch blocked) for push-enabled repos; non-pushable
    repos can only be committed locally.
    """
    items = [r for r in (repos or []) if r.get("path")]
    if not items:
        return ""
    out = [
        "## Code repositories",
        "",
        "Each folder below is an independent git clone you can read, edit, build, "
        "and commit in (paths are relative to this workspace). Each is already on "
        "its own task branch — commit your work there and do not switch to the "
        "default branch:",
    ]
    has_wiki = False
    has_push = False
    for repo in items:
        branch = (repo.get("branch") or "").strip()
        suffix = f" (branch `{branch}`)" if branch else ""
        if repo.get("is_wiki"):
            suffix += " — **board wiki** (knowledge base)"
            has_wiki = True
        if repo.get("can_push"):
            suffix += " — push enabled"
            has_push = True
        out.append(f"- `{repo['path']}/`{suffix}")
    out.append("")
    if has_wiki:
        out.append(
            "A repo marked **board wiki** is this board's knowledge base. Read its "
            "`index.md` before working and follow the wiki's own conventions (and "
            "the `board-wiki` skill). Record new knowledge in the wiki's existing "
            "structure and commit it on your task branch."
        )
        out.append("")
    if has_push:
        out.append(
            "For repos marked **push enabled**, a plain `git push` publishes your "
            "task branch to the remote (credentials are managed for you; you can "
            "only push your task branch, never the default branch). A human reviews "
            "and merges. Other repos: commits stay in this local clone."
        )
    else:
        out.append("Commits stay in this local clone (no remote push for these repos).")
    return "\n".join(out)


def _render_skills_block(skills: Sequence[dict] | None) -> str:
    """Render the available skill packs as a context block, or "".

    Each entry is ``{name, description, path}`` where ``path`` points at the
    materialised ``SKILL.md`` inside the workspace. Claude and Cursor also load
    these natively from ``.claude/skills`` / ``.cursor/skills``; this manifest is
    the fallback so any engine (notably Codex) can find them on demand.
    """
    items = [s for s in (skills or []) if s.get("name")]
    if not items:
        return ""
    out = [
        "## Available skills",
        "",
        "Reusable skill packs are available in this workspace. When a task matches "
        "one, read its `SKILL.md` and follow it:",
    ]
    for skill in items:
        desc = (skill.get("description") or "").strip()
        path = (skill.get("path") or "").strip()
        line = f"- **{skill['name']}**"
        if desc:
            line += f": {desc}"
        if path:
            line += f" — read `{path}`"
        out.append(line)
    return "\n".join(out)


def render_brief(
    task: AgentTeamTask,
    notes: Sequence[dict] | None,
    repos: Sequence[dict] | None,
    skills: Sequence[dict] | None = None,
) -> str:
    """Render the full task brief written to ``.agent-team/TASK.md``."""
    sections: list[str] = [f"# Task {task.human_key}: {task.title}"]
    if task.description:
        sections.append(task.description.strip())
    sections.append(f"Shared workspace folder: `{task.workspace_path}`")

    repos_block = _render_repos_block(repos)
    if repos_block:
        sections.append(repos_block)

    skills_block = _render_skills_block(skills)
    if skills_block:
        sections.append(skills_block)

    notes_block = _format_notes(notes, new_only=False)
    if notes_block:
        sections.append("## User notes\n\n" + notes_block)

    return "\n\n".join(sections) + "\n"


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def write_context_files(
    workspace_path: str,
    task: AgentTeamTask,
    notes: Sequence[dict] | None,
    repos: Sequence[dict] | None,
    skills_manifest: Sequence[dict] | None = None,
) -> None:
    """Write the task brief + native pointer files into the workspace.

    ``skills_manifest`` (already materialised by the caller via
    :func:`skills.materialize_skills`) is listed in the brief as a fallback for
    engines without a native skills dir.

    Best-effort: a write failure is logged and swallowed so it never aborts the
    run (the CLI can still work from the prompt nudge alone).
    """
    try:
        _write_file(
            os.path.join(workspace_path, BRIEF_REL_PATH),
            render_brief(task, notes, repos, skills_manifest),
        )
        # Native discovery files, one per engine, at the workspace root. The
        # Cursor rule needs front matter so the agent always applies it.
        _write_file(os.path.join(workspace_path, "CLAUDE.md"), _POINTER_BODY + "\n")
        _write_file(os.path.join(workspace_path, "AGENTS.md"), _POINTER_BODY + "\n")
        _write_file(
            os.path.join(workspace_path, ".cursor", "rules", "agent-team-task.mdc"),
            "---\nalwaysApply: true\n---\n\n" + _POINTER_BODY + "\n",
        )
    except OSError:
        logger.warning(
            "agent_team: failed to write CLI context files in %s",
            workspace_path,
            exc_info=True,
        )
