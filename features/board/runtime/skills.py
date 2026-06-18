"""Per-board skill packs for direct-CLI agents.

A direct CLI agent (Claude / Cursor / Codex) has no system prompt and no tool
registry, so the core ``skill_packs`` plugin (which injects packs into an LLM
system prompt and exposes a ``skill_pack_view`` tool) never reaches it. Instead
we materialise the board's selected packs as files in the task workspace, in
each engine's native skill layout:

* ``.claude/skills/<name>/`` and ``.cursor/skills/<name>/`` — full pack folders
  (``SKILL.md`` + ``scripts/`` / ``references/`` / ``assets/``) that Claude Code
  and Cursor discover natively.
* A short ``## Available skills`` manifest is added to ``.agent-team/TASK.md`` by
  the caller (so Codex — which has no skills dir — and any engine that did not
  auto-load can still find them on demand).

The catalog reuses the ``skill_packs`` stores (shared dir + git sources), so a
board only stores the chosen pack *names*. Everything here is best-effort: a
missing ``skill_packs`` plugin or a bad pack is logged and skipped, never
aborting a run.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

#: Native skill directories materialised at the workspace root, one per engine.
_NATIVE_SKILL_DIRS = (".claude/skills", ".cursor/skills")

#: Fallback manifest read by Codex (which has no native skills dir) when a coding
#: sub-agent is spawned by an LLM's ``codex_acp`` tool in the task workspace.
_CODEX_MANIFEST_FILE = "AGENTS.md"


def _safe_dir_name(name: str) -> str:
    """Turn a pack name (may contain ``/``) into a single safe folder name."""
    return name.replace("/", "-").replace("\\", "-").strip("-") or "skill"


def _catalog() -> dict[str, tuple[object, object]]:
    """Return ``{name: (meta, store)}`` across all skill_packs stores.

    First store wins on a name clash (mirrors the core runtime). Returns ``{}``
    when the ``skill_packs`` plugin is unavailable.
    """
    try:
        from plugins.skill_packs.store import resolve_stores
    except Exception:
        logger.debug("skill_packs plugin not available", exc_info=True)
        return {}
    try:
        stores = resolve_stores("", {})
    except Exception:
        logger.debug("could not resolve skill_packs stores", exc_info=True)
        return {}
    out: dict[str, tuple[object, object]] = {}
    for store in stores:
        try:
            metas = store.list()
        except Exception:
            logger.debug("skill_packs store %r failed to list", store, exc_info=True)
            continue
        for meta in metas:
            out.setdefault(meta.name, (meta, store))
    return out


def list_available_packs() -> list[dict]:
    """Return the skill catalog as ``[{name, description, version, source}]``."""
    rows = [
        {
            "name": meta.name,  # type: ignore[attr-defined]
            "description": meta.description,  # type: ignore[attr-defined]
            "version": meta.version,  # type: ignore[attr-defined]
            "source": store.name,  # type: ignore[attr-defined]
        }
        for meta, store in _catalog().values()
    ]
    return sorted(rows, key=lambda p: p["name"])


def _clear_managed_dirs(ws: Path) -> None:
    """Remove previously-materialised skill dirs so the set stays idempotent."""
    for rel in _NATIVE_SKILL_DIRS:
        root = ws / rel
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)


def materialize_skills(workspace_path: str, names: Sequence[str]) -> list[dict]:
    """Copy the board's selected packs into the workspace's native skill dirs.

    Returns manifest rows ``[{name, description, path}]`` (``path`` points at the
    copied ``SKILL.md``) for the caller to list in the task brief. Always clears
    the managed skill dirs first, so de-selecting a pack removes it from the
    workspace on the next turn.
    """
    ws = Path(workspace_path)
    _clear_managed_dirs(ws)
    wanted = [n for n in (names or []) if n]
    if not wanted:
        return []

    catalog = _catalog()
    manifest: list[dict] = []
    for name in wanted:
        entry = catalog.get(name)
        if entry is None:
            logger.info("agent_team: skill pack %r not found in catalog", name)
            continue
        meta, store = entry
        try:
            src = store.find_dir(name)  # type: ignore[attr-defined]
        except Exception:
            src = None
        if not src or not Path(src).is_dir():
            continue
        folder = _safe_dir_name(name)
        copied_any = False
        for rel in _NATIVE_SKILL_DIRS:
            dest = ws / rel / folder
            try:
                shutil.copytree(src, dest, dirs_exist_ok=True)
                copied_any = True
            except OSError:
                logger.warning(
                    "agent_team: failed to copy skill %r into %s", name, dest, exc_info=True
                )
        if copied_any:
            manifest.append(
                {
                    "name": name,
                    "description": getattr(meta, "description", "") or "",
                    "path": f".claude/skills/{folder}/SKILL.md",
                }
            )
    return manifest


def render_manifest_md(manifest: Sequence[dict] | None) -> str:
    """Render the materialised skills as a markdown manifest, or "" when empty."""
    items = [s for s in (manifest or []) if s.get("name")]
    if not items:
        return ""
    out = [
        "# Available skills",
        "",
        "Reusable skill packs are available in this workspace. When a task matches "
        "one, read its `SKILL.md` and follow it:",
        "",
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
    return "\n".join(out) + "\n"


def write_codex_manifest(workspace_path: str, manifest: Sequence[dict] | None) -> None:
    """Advertise materialised skills to a Codex sub-agent via ``AGENTS.md``.

    Used on the LLM run path: Claude/Cursor discover the copied ``.claude`` /
    ``.cursor`` skill dirs natively, but Codex only reads ``AGENTS.md`` at the
    cwd. The direct-CLI path already lists skills inside ``.agent-team/TASK.md``
    (its ``AGENTS.md`` points there), so this is only for non-CLI runs.

    Best-effort: when there are no skills the file is left untouched so we never
    clobber a manifest written by another run on the same workspace.
    """
    body = render_manifest_md(manifest)
    if not body:
        return
    try:
        with open(os.path.join(workspace_path, _CODEX_MANIFEST_FILE), "w", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        logger.warning(
            "agent_team: failed to write Codex skills manifest in %s",
            workspace_path,
            exc_info=True,
        )
