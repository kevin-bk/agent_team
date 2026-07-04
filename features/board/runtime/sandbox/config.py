"""Runtime profile & volume-mount config DTOs (dependency-free dataclasses).

These replace deep-agent's pydantic profile schema so the sandbox layer stays
importable without web/pydantic deps. :class:`VolumeMount` is intentionally
**duck-type compatible** with the fields ``OpenSandboxRuntime._build_volumes``
reads (``kind`` / ``name`` / ``mount_path`` / ``read_only`` / ``sub_path`` /
``host_path`` / ``pvc_claim`` / ``ossfs_uri`` / ``pvc_*``).

Resolution order for a runtime profile is: run request → task → board →
env defaults (see :func:`profile_from_env`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

Provider = Literal["local", "opensandbox"]
WorkspaceMode = Literal["mount", "sync"]
VolumeMountKind = Literal["host", "pvc", "ossfs"]
#: How a ``cli:`` engine is driven inside an isolated sandbox.
#: * ``oneshot``     — Phase 1: non-interactive print-mode command, parse stream.
#: * ``acp_sidecar`` — Phase 2: an in-sandbox server owns the ACP subprocess and
#:                     bridges the full ACP frame stream to the host over WS.
RuntimeStrategy = Literal["oneshot", "acp_sidecar"]


@dataclass
class VolumeMount:
    """One volume mounted into a sandbox. See :class:`OpenSandboxRuntime`."""

    name: str
    mount_path: str
    kind: VolumeMountKind = "host"
    read_only: bool = False
    sub_path: str | None = None

    # host bind mount
    host_path: str | None = None
    # PVC / Docker named volume
    pvc_claim: str | None = None
    pvc_create_if_not_exists: bool = True
    pvc_delete_on_sandbox_termination: bool = False
    pvc_storage_class: str | None = None
    pvc_storage: str | None = None
    pvc_access_modes: list[str] | None = None
    # OSSFS
    ossfs_uri: str | None = None


@dataclass
class RuntimeProfile:
    """Resolved runtime configuration for a task's execution environment."""

    provider: Provider = "local"
    image: str = "agent-team/runtime-full:latest"
    snapshot_id: str | None = None

    # OpenSandbox server connection (falls back to env / ~/.sandbox.toml in the
    # runtime itself when left empty).
    server_url: str = ""
    api_key_env: str = "OPEN_SANDBOX_API_KEY"

    # resources
    cpu: float = 2.0
    memory_mb: int = 4096
    timeout_minutes: int = 180
    idle_timeout_minutes: int = 30
    ready_timeout_seconds: int = 60
    request_timeout_seconds: int = 600

    # workspace strategy
    workspace_mode: WorkspaceMode = "mount"
    workspace_mount_path: str = "/workspace"

    # how the CLI is driven inside the sandbox (see RuntimeStrategy)
    runtime_strategy: RuntimeStrategy = "oneshot"
    #: TCP port the in-sandbox ACP sidecar server listens on (acp_sidecar only).
    sidecar_port: int = 8871

    # network / secrets
    network_policy: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    env_blocklist: list[str] = field(default_factory=list)
    #: Name/id of a CredentialAccount whose secret is injected into the sandbox
    #: (see runtime/credentials/). Empty = no credential injection (opt-in).
    credential_account: str | None = None

    # behaviour
    strict_isolation: bool = False
    allow_fallback: bool = False
    use_server_proxy: bool = True

    # pooling (designed now, wired later)
    pool_enabled: bool = False
    pool_max_idle: int = 1

    # extra volumes beyond the task workspace
    mounts: list[VolumeMount] = field(default_factory=list)

    @property
    def is_sandboxed(self) -> bool:
        return self.provider == "opensandbox"

    @property
    def is_acp_sidecar(self) -> bool:
        """True when an isolated run should use the Phase-2 ACP sidecar bridge."""
        return self.is_sandboxed and self.runtime_strategy == "acp_sidecar"


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


#: Board-editable subset of the runtime profile (via ``runtime_profile_json``).
#: Server connection + secret-ish fields (server_url/api_key_env/env/…) stay
#: env-only; a board only tunes provider/strategy/image/resources/isolation.
OVERLAY_FIELDS: frozenset[str] = frozenset({
    "provider",
    "runtime_strategy",
    "image",
    "snapshot_id",
    "cpu",
    "memory_mb",
    "timeout_minutes",
    "idle_timeout_minutes",
    "ready_timeout_seconds",
    "workspace_mode",
    "workspace_mount_path",
    "sidecar_port",
    "strict_isolation",
    "allow_fallback",
    "use_server_proxy",
    "credential_account",
})

_ENUMS: dict[str, frozenset[str]] = {
    "provider": frozenset({"local", "opensandbox"}),
    "runtime_strategy": frozenset({"oneshot", "acp_sidecar"}),
    "workspace_mode": frozenset({"mount", "sync"}),
}
_INT_FIELDS = frozenset({
    "memory_mb", "timeout_minutes", "idle_timeout_minutes",
    "ready_timeout_seconds", "sidecar_port",
})
_BOOL_FIELDS = frozenset({"strict_isolation", "allow_fallback", "use_server_proxy"})


def validate_overlay(overlay: dict) -> tuple[dict, str | None]:
    """Validate a board ``runtime_profile`` override → ``(cleaned, error)``.

    Rejects unknown keys, bad enum values, and non-numeric/non-bool types so a
    malformed board setting can never silently mis-provision a sandbox. ``cleaned``
    is safe to persist as ``runtime_profile_json``; ``error`` is a 422 message.
    """
    if not isinstance(overlay, dict):
        return {}, "runtime_profile must be an object"
    cleaned: dict = {}
    for key, value in overlay.items():
        if key not in OVERLAY_FIELDS:
            return {}, f"unknown runtime_profile field: {key!r}"
        if value is None:
            continue
        if key in _ENUMS:
            if value not in _ENUMS[key]:
                allowed = ", ".join(sorted(_ENUMS[key]))
                return {}, f"{key} must be one of: {allowed}"
        elif key == "cpu":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                return {}, "cpu must be a positive number"
        elif key in _INT_FIELDS:
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return {}, f"{key} must be a non-negative integer"
        elif key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                return {}, f"{key} must be a boolean"
        elif not isinstance(value, str):
            return {}, f"{key} must be a string"
        cleaned[key] = value
    return cleaned, None


def profile_from_env() -> RuntimeProfile:
    """Build the default runtime profile from environment variables.

    ``AGENT_TEAM_RUNTIME_PROVIDER`` gates everything: unless it is
    ``opensandbox``, the returned profile is ``local`` (current behaviour).
    """
    provider = _env("AGENT_TEAM_RUNTIME_PROVIDER", "local").lower()
    if provider not in ("local", "opensandbox"):
        provider = "local"

    blocklist_raw = _env("AGENT_TEAM_RUNTIME_ENV_BLOCKLIST")
    blocklist = [x.strip() for x in blocklist_raw.split(",") if x.strip()]

    return RuntimeProfile(
        provider=provider,  # type: ignore[arg-type]
        image=_env("AGENT_TEAM_RUNTIME_IMAGE", "agent-team/runtime-full:latest"),
        snapshot_id=_env("AGENT_TEAM_RUNTIME_SNAPSHOT") or None,
        server_url=_env("OPEN_SANDBOX_DOMAIN"),
        api_key_env="OPEN_SANDBOX_API_KEY",
        cpu=_env_float("AGENT_TEAM_RUNTIME_CPU", 2.0),
        memory_mb=_env_int("AGENT_TEAM_RUNTIME_MEMORY_MB", 4096),
        timeout_minutes=_env_int("AGENT_TEAM_RUNTIME_TIMEOUT_MINUTES", 180),
        idle_timeout_minutes=_env_int("AGENT_TEAM_RUNTIME_IDLE_MINUTES", 30),
        ready_timeout_seconds=_env_int("AGENT_TEAM_RUNTIME_READY_TIMEOUT_SECONDS", 60),
        workspace_mode=_env("AGENT_TEAM_RUNTIME_WORKSPACE_MODE", "mount"),  # type: ignore[arg-type]
        workspace_mount_path=_env("AGENT_TEAM_RUNTIME_WORKSPACE_PATH", "/workspace"),
        runtime_strategy=_env("AGENT_TEAM_RUNTIME_STRATEGY", "oneshot"),  # type: ignore[arg-type]
        sidecar_port=_env_int("AGENT_TEAM_RUNTIME_SIDECAR_PORT", 8871),
        env_blocklist=blocklist,
        credential_account=_env("AGENT_TEAM_RUNTIME_CREDENTIAL_ACCOUNT") or None,
        strict_isolation=_env_bool("AGENT_TEAM_RUNTIME_STRICT", False),
        allow_fallback=_env_bool("AGENT_TEAM_RUNTIME_ALLOW_FALLBACK", False),
        use_server_proxy=_env_bool("AGENT_TEAM_RUNTIME_SERVER_PROXY", True),
        pool_enabled=_env_bool("AGENT_TEAM_RUNTIME_POOL_ENABLED", False),
        pool_max_idle=_env_int("AGENT_TEAM_RUNTIME_POOL_MAX_IDLE", 1),
    )
