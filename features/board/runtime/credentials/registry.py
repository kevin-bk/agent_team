"""Provider → credential requirements (the only provider-specific declaration).

Each coding agent (and, later, GitHub/GitLab/API integrations) lists what
credentials it needs and how they are shaped. Backends consume these; adding a
provider or credential here does not touch any backend or worker code.
"""

from __future__ import annotations

from agent_team.features.board.runtime.credentials.spec import CredentialRequirement

#: Provider identifier → declarative credential requirements.
#:
#: Both coding agents authenticate via a **subscription login folder** (the way
#: the AI Code Factory Environment Pool stores accounts): Claude in
#: ``CLAUDE_CONFIG_DIR`` and Codex in ``$CODEX_HOME``. Both are ``config_dir`` ⇒
#: the ``mount`` backend mounts the (writable) host folder into the sandbox so
#: the CLI reads its login and can refresh/rewrite auth state in place.
#:
#: The ``header_token`` shape (env/vault backends) is intentionally kept in the
#: codebase for future API-key providers (e.g. GitHub), but is not the default
#: path — nobody here uses raw API keys for the coding agents.
PROVIDER_REQUIREMENTS: dict[str, list[CredentialRequirement]] = {
    # Claude Code subscription: `claude /login` writes auth into CLAUDE_CONFIG_DIR.
    # IS_SANDBOX=1 lets bypassPermissions run as root in the container.
    "claude": [
        CredentialRequirement(
            name="claude-config",
            kind="config_dir",
            hosts=["api.anthropic.com"],
            target_dir_env="CLAUDE_CONFIG_DIR",
            mount_path="/root/.claude",
            static_env={"IS_SANDBOX": "1"},
        )
    ],
    # Codex subscription: ChatGPT-account auth stored in $CODEX_HOME/auth.json,
    # refreshed and rewritten during use ⇒ needs a writable mounted dir.
    "codex": [
        CredentialRequirement(
            name="codex-home",
            kind="config_dir",
            hosts=["chatgpt.com", "api.openai.com", "auth.openai.com"],
            target_dir_env="CODEX_HOME",
            mount_path="/root/.codex",
        )
    ],
}

#: Backend used when an account leaves ``backend`` blank. Both coding agents use
#: a login folder ⇒ ``mount``. (API-key providers would default to ``env`` here.)
DEFAULT_BACKEND_BY_PROVIDER: dict[str, str] = {
    "claude": "mount",
    "codex": "mount",
}


def requirements_for(provider: str) -> list[CredentialRequirement] | None:
    """Requirements for ``provider`` or ``None`` if the provider is unknown."""
    return PROVIDER_REQUIREMENTS.get(provider)


def default_backend_for(provider: str) -> str:
    """Backend name to use when an account does not pin one explicitly."""
    return DEFAULT_BACKEND_BY_PROVIDER.get(provider, "env")
