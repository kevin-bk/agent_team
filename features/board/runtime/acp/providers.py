"""Per-engine capability table.

Replaces an external provider registry: a small static description of what each
ACP engine supports, used to gate optional protocol calls (model switching, MCP
transports) so a call is only attempted where the engine can honour it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    """What an engine can do beyond the baseline prompt/cancel turn."""

    #: Apply an initial model right after session creation.
    supports_set_session_model: bool = False
    #: Switch the model mid-session without a subprocess restart.
    supports_runtime_model_switch: bool = False
    #: Whether the model is applied via ``set_config_option(model)`` (newer CLIs)
    #: rather than ``set_session_model``.
    model_via_config_option: bool = False


#: Conservative defaults: model features off until verified per engine. MCP
#: transport support is read live from the session's advertised capabilities at
#: create time, not hard-coded here.
_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "claude": ProviderCapabilities(
        supports_set_session_model=True,
        supports_runtime_model_switch=True,
        model_via_config_option=True,
    ),
    "codex": ProviderCapabilities(
        supports_set_session_model=True,
        supports_runtime_model_switch=True,
        model_via_config_option=True,
    ),
    "cursor": ProviderCapabilities(),
}


def capabilities_for(engine: str) -> ProviderCapabilities:
    """Return the capabilities for ``engine`` (safe defaults when unknown)."""
    return _CAPABILITIES.get(engine, ProviderCapabilities())
