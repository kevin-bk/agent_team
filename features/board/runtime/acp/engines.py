"""Direct CLI engine catalogue: specs, aliases, and per-host runtime resolution.

A *direct CLI* conversation talks to Claude / Cursor / Codex over ACP without an
LLM orchestrator in between. Each engine is addressed by a synthetic alias
``cli:<engine>`` that flows through the normal conversation/run/thread machinery;
only the run driver branches on it.

This module is self-contained (it owns its config keys and defaults) so the
agent-team ACP engine does not depend on the shared ai_code ACP base.
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass

#: Synthetic-alias namespace marking a direct CLI conversation.
CLI_ALIAS_PREFIX = "cli:"

#: Session-create stays short — spawning ``npx`` is fast; a slow create means a
#: missing binary or a wedged install, not legitimate work.
_DEFAULT_CREATE_TIMEOUT_SECONDS = 120

#: Hard-pinned turn ceiling. Long-form jobs routinely run well past a default
#: few-minute cap, so direct-CLI turns get a generous 3-hour absolute backstop;
#: the per-run idle timeout stops a genuinely wedged turn far sooner.
TURN_TIMEOUT_SECONDS = 3 * 60 * 60

# Codex runs inside the task's OpenSandbox container in acp_sidecar mode. The
# container is the isolation boundary, so Codex CLI must not create a nested
# bubblewrap sandbox; many container hosts disable the required user namespace.
CODEX_ACP_DEFAULT_ARGS = (
    "-y @agentclientprotocol/codex-acp "
    "-c 'sandbox_mode=\"danger-full-access\"' "
    "-c 'approval_policy=\"never\"'"
)


@dataclass(frozen=True)
class EngineSpec:
    """Static defaults for one ACP engine (command + args before env override)."""

    engine: str
    label: str
    command: str
    args: str


#: The catalogue. Config is read from the process environment with the same
#: ``AI_CODE_<ENGINE>_ACP_*`` keys the per-agent ACP tools use, so a direct agent
#: needs no Agent row — it is deliberately not one.
ENGINES: dict[str, EngineSpec] = {
    "claude": EngineSpec(
        "claude", "Claude", "npx", "-y @agentclientprotocol/claude-agent-acp"
    ),
    "cursor": EngineSpec("cursor", "Cursor", "cursor-agent", "acp"),
    "codex": EngineSpec("codex", "Codex", "npx", CODEX_ACP_DEFAULT_ARGS),
}


def is_direct_cli_alias(alias: str | None) -> bool:
    """True when ``alias`` addresses a direct CLI engine (``cli:<engine>``)."""
    return bool(alias) and alias.startswith(CLI_ALIAS_PREFIX)


def engine_for_alias(alias: str | None) -> str:
    """Return the engine name encoded in a ``cli:<engine>`` alias, or ``""``."""
    if not is_direct_cli_alias(alias):
        return ""
    return (alias or "")[len(CLI_ALIAS_PREFIX):].strip().lower()


def alias_for_engine(engine: str) -> str:
    """Return the synthetic alias for an engine (inverse of :func:`engine_for_alias`)."""
    return f"{CLI_ALIAS_PREFIX}{engine}"


def known_cli_aliases() -> set[str]:
    """All valid direct-CLI aliases, regardless of whether the binary is installed."""
    return {alias_for_engine(engine) for engine in ENGINES}


def display_name_for_alias(alias: str | None) -> str:
    """Human label for a direct CLI alias (e.g. ``cli:claude`` → ``Claude (direct)``)."""
    spec = ENGINES.get(engine_for_alias(alias))
    return f"{spec.label} (direct)" if spec else (alias or "")


@dataclass(frozen=True)
class EngineRuntime:
    """A resolved, host-specific launch recipe for one engine."""

    engine: str
    label: str
    command: str
    args: list[str]
    timeout_seconds: int
    create_timeout_seconds: int


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def engine_runtime(engine: str) -> EngineRuntime:
    """Resolve an engine's command/args from env; the turn timeout is hard-pinned."""
    spec = ENGINES[engine]
    up = engine.upper()
    args_raw = _env(f"AI_CODE_{up}_ACP_ARGS", spec.args)
    return EngineRuntime(
        engine=engine,
        label=f"{spec.label} ACP",
        command=_env(f"AI_CODE_{up}_ACP_COMMAND", spec.command),
        args=shlex.split(args_raw) if args_raw else [],
        timeout_seconds=TURN_TIMEOUT_SECONDS,
        create_timeout_seconds=_DEFAULT_CREATE_TIMEOUT_SECONDS,
    )


def available_targets() -> list[dict]:
    """List engines, flagging which look runnable on this host (binary on PATH)."""
    targets: list[dict] = []
    for engine, spec in ENGINES.items():
        command = _env(f"AI_CODE_{engine.upper()}_ACP_COMMAND", spec.command)
        targets.append(
            {
                "id": alias_for_engine(engine),
                "engine": engine,
                "label": spec.label,
                "available": shutil.which(command) is not None,
            }
        )
    return targets
