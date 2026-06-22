"""Board Wiki runtime: materialise the bundled ``board-wiki`` skill pack.

The wiki content lives in a board repo (designated via the board↔repo
assignment's ``is_wiki`` flag) and is checked out per task by the repos feature.
This module only ensures the skill pack — the workflow the agent follows — is
present in the task workspace for both LLM and direct-CLI engines. Best-effort:
failures log and are swallowed so they never abort a run.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from agent_team.features.board.runtime.skills import _NATIVE_SKILL_DIRS

logger = logging.getLogger(__name__)

#: Bundled ``board-wiki`` skill pack shipped with the plugin.
_BUNDLED_SKILL_DIR = Path(__file__).resolve().parent / "skill_pack" / "board-wiki"
_SKILL_NAME = "board-wiki"
_SKILL_DESCRIPTION = (
    "How to read and contribute to this board's knowledge base (Board Wiki)."
)


def materialize_wiki_skill(workspace_path: str) -> dict | None:
    """Copy the bundled ``board-wiki`` skill pack into the workspace skill dirs.

    Returns a manifest row ``{name, description, path}`` for the caller to append
    to the task's skills manifest, or ``None`` if nothing was copied. Must run
    *after* ``skills.materialize_skills`` (which clears the skill dirs) so it adds
    to, rather than is wiped by, that step.
    """
    if not _BUNDLED_SKILL_DIR.is_dir():
        logger.warning("agent_team: bundled board-wiki skill pack is missing")
        return None
    ws = Path(workspace_path)
    copied = False
    for rel in _NATIVE_SKILL_DIRS:
        dest = ws / rel / _SKILL_NAME
        try:
            shutil.copytree(_BUNDLED_SKILL_DIR, dest, dirs_exist_ok=True)
            copied = True
        except OSError:
            logger.warning(
                "agent_team: failed to copy board-wiki skill into %s", dest, exc_info=True
            )
    if not copied:
        return None
    return {
        "name": _SKILL_NAME,
        "description": _SKILL_DESCRIPTION,
        "path": f".claude/skills/{_SKILL_NAME}/SKILL.md",
    }
