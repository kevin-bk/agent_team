"""Dependency-free DTOs for credential injection.

Deliberately imports **only** :class:`VolumeMount` from the sandbox config so the
whole credential layer stays importable without the DB or the OpenSandbox SDK
(keeps it out of the sidecar image's import closure). ORM/DB access lives in
:mod:`service`; SDK translation (network rules, vault bindings) happens later at
the runtime seam, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent_team.features.board.runtime.sandbox.config import VolumeMount

#: How a single credential is shaped.
#: * ``header_token`` — a bearer/apiKey secret sent as an HTTP header (Claude
#:   OAuth, API keys). Injectable via ``env`` or ``vault``.
#: * ``config_dir``   — a directory of on-disk auth state that the CLI reads and
#:   rewrites (Codex ``$CODEX_HOME``). Injectable via ``mount`` only.
CredentialKind = Literal["header_token", "config_dir"]

#: Auth header rendering for the vault backend (mirrors OpenSandbox auth types).
AuthType = Literal["bearer", "apiKey", "basic"]


@dataclass(frozen=True)
class CredentialRequirement:
    """One credential a provider needs, described declaratively.

    A provider (see :mod:`registry`) is a list of these. Backends read the fields
    relevant to them and ignore the rest, so the same descriptor drives every
    backend without per-provider branching.
    """

    name: str
    kind: CredentialKind
    #: Outbound hosts this credential authenticates to. Feeds the egress
    #: allowlist (Đợt 2) and the vault binding match (Đợt 3).
    hosts: list[str] = field(default_factory=list)
    #: Path globs for the vault binding match (e.g. ``/v1/*``).
    paths: list[str] = field(default_factory=lambda: ["/*"])

    # ── header_token ──────────────────────────────────────────────────
    #: Env var the CLI reads inside the sandbox (e.g. ``CLAUDE_CODE_OAUTH_TOKEN``).
    #: ``env`` backend sets it to the real secret; ``vault`` sets it to a
    #: placeholder and injects the real value at egress.
    secret_sandbox_env: str | None = None
    auth_type: AuthType | None = None
    #: Header name for ``apiKey`` auth (e.g. ``x-api-key``); unused for bearer.
    header_name: str | None = None

    # ── config_dir ────────────────────────────────────────────────────
    #: Env var pointing the CLI at the mounted dir (e.g. ``CODEX_HOME``).
    target_dir_env: str | None = None
    #: In-sandbox mount path for the config dir.
    mount_path: str | None = None

    # ── always ────────────────────────────────────────────────────────
    #: Non-secret env vars every backend sets (e.g. ``IS_SANDBOX=1``).
    static_env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedAccount:
    """A provider identity resolved to concrete material, ready for a backend.

    ORM-free on purpose: the account may come from the AI Code Factory
    Environment Pool (``config_dir`` → ``material={"host_path": ...}``) or any
    other source. Backends only read :attr:`name` and :meth:`material_ref`.
    """

    name: str
    provider: str
    #: "" = the provider's default backend (see :mod:`registry`).
    backend: str = ""
    #: Reference material, e.g. ``{"host_path": "/…"}`` or ``{"secret_env": "…"}``.
    material: dict[str, str] = field(default_factory=dict)

    def material_ref(self) -> dict[str, str]:
        return dict(self.material)


@dataclass
class InjectionPlan:
    """Everything a backend contributes to a sandbox for one account.

    Accumulated across a provider's requirements, then applied at the runtime
    seam: ``env`` + ``mounts`` at create time; ``network_rules`` (Đợt 2) and
    ``vault_*`` / ``upload_files`` (Đợt 3) around/after open.
    """

    env: dict[str, str] = field(default_factory=dict)
    mounts: list[VolumeMount] = field(default_factory=list)
    #: ``{"action": "allow", "target": "api.anthropic.com"}`` rules for the
    #: egress network policy (applied in Đợt 2).
    network_rules: list[dict] = field(default_factory=list)
    #: OpenSandbox Credential Vault payloads (applied in Đợt 3).
    vault_credentials: list[dict] = field(default_factory=list)
    vault_bindings: list[dict] = field(default_factory=list)
    #: ``(remote_path, data)`` files to upload after open (Đợt 3 ephemeral mode).
    upload_files: list[tuple[str, bytes]] = field(default_factory=list)
    #: True when the sandbox must be created with the credential proxy enabled.
    needs_credential_proxy: bool = False

    def merge(self, other: InjectionPlan) -> None:
        """Fold another plan into this one (env: later wins)."""
        self.env.update(other.env)
        self.mounts.extend(other.mounts)
        self.network_rules.extend(other.network_rules)
        self.vault_credentials.extend(other.vault_credentials)
        self.vault_bindings.extend(other.vault_bindings)
        self.upload_files.extend(other.upload_files)
        self.needs_credential_proxy = (
            self.needs_credential_proxy or other.needs_credential_proxy
        )
