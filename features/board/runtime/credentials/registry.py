"""Provider → credential requirements (the only provider-specific declaration).

Each coding agent (and, later, GitHub/GitLab/API integrations) lists what
credentials it needs and how they are shaped. Backends consume these; adding a
provider or credential here does not touch any backend or worker code.
"""

from __future__ import annotations

from agent_team.features.board.runtime.credentials.spec import CredentialRequirement

#: Provider identifier → declarative credential requirements.
PROVIDER_REQUIREMENTS: dict[str, list[CredentialRequirement]] = {
    # Claude Code subscription: a static ~1yr OAuth token sent as
    # ``Authorization: Bearer`` to api.anthropic.com. Static + no refresh ⇒
    # vault-friendly (Đợt 3); ``env`` backend works today (Đợt 1).
    # IS_SANDBOX=1 lets bypassPermissions run as root in the container.
    "claude": [
        CredentialRequirement(
            name="anthropic-oauth",
            kind="header_token",
            hosts=["api.anthropic.com"],
            paths=["/v1/*"],
            secret_sandbox_env="CLAUDE_CODE_OAUTH_TOKEN",
            auth_type="bearer",
            static_env={"IS_SANDBOX": "1"},
        )
    ],
    # Codex subscription: ChatGPT-account auth stored in $CODEX_HOME/auth.json,
    # refreshed (token endpoint, body param) and rewritten during use ⇒ needs a
    # writable mounted dir; not vault-coverable.
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

#: Backend used when an account leaves ``backend`` blank. Defaults are the
#: **infra-free** options so a fresh deploy works without egress ``dns+nft``:
#: Claude=``env`` (token in-sandbox), Codex=``mount`` (writable auth dir). Flip a
#: Claude account to ``backend="vault"`` (zero-secret) once the server has the
#: Credential Vault egress configured.
DEFAULT_BACKEND_BY_PROVIDER: dict[str, str] = {
    "claude": "env",
    "codex": "mount",
}


def requirements_for(provider: str) -> list[CredentialRequirement] | None:
    """Requirements for ``provider`` or ``None`` if the provider is unknown."""
    return PROVIDER_REQUIREMENTS.get(provider)


def default_backend_for(provider: str) -> str:
    """Backend name to use when an account does not pin one explicitly."""
    return DEFAULT_BACKEND_BY_PROVIDER.get(provider, "env")
