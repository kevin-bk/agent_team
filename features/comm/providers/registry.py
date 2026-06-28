"""Provider registry: the single place that knows the set of providers.

Adding a provider (Slack, generic webhook, email, …) means implementing the
:class:`~agent_team.features.comm.providers.base.CommunicationProvider` protocol
and registering it here with a :class:`ProviderDescriptor`. The dispatch flow
(``service``/``tagging``/``router``) and the UI both drive off this registry, so
no other code needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_team.features.comm.models import PROVIDER_MATTERMOST, PROVIDER_SLACK
from agent_team.features.comm.providers.base import CommunicationProvider
from agent_team.features.comm.providers.mattermost import MattermostProvider
from agent_team.features.comm.providers.slack import SlackProvider


@dataclass
class ProviderField:
    """One connection-level config field, mapped to a connection column.

    ``key`` must be one of the connection columns the UI can write:
    ``server_url`` / ``bot_token`` / ``default_team_id`` / ``deep_link_base``.
    """

    key: str
    label: str
    type: str = "text"  # "text" | "url" | "secret"
    required: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass
class ProviderDescriptor:
    """Everything the UI needs to render a provider's connection + channel form."""

    id: str
    label: str
    fields: list[ProviderField] = field(default_factory=list)
    channel_id_label: str = "Channel ID"
    channel_id_placeholder: str = ""
    channel_id_help: str = ""


_FIELD_DEEP_LINK = ProviderField(
    key="deep_link_base",
    label="Deep-link base URL (optional)",
    type="url",
    required=False,
    placeholder="https://agent.example.com",
    help="Public URL of this platform, used to build “Open task” links in messages.",
)


_DESCRIPTORS: dict[str, ProviderDescriptor] = {
    PROVIDER_MATTERMOST: ProviderDescriptor(
        id=PROVIDER_MATTERMOST,
        label="Mattermost",
        fields=[
            ProviderField(
                key="server_url",
                label="Server URL",
                type="url",
                required=True,
                placeholder="https://mattermost.example.com",
            ),
            ProviderField(
                key="bot_token",
                label="Bot token",
                type="secret",
                required=True,
                placeholder="Bot access token",
                help="Create a bot account in Mattermost (Integrations → Bot Accounts) "
                "and paste its token. Stored server-side and never shown again.",
            ),
            _FIELD_DEEP_LINK,
        ],
        channel_id_label="Channel ID",
        channel_id_placeholder="Mattermost channel ID (e.g. 8f3k…)",
    ),
    PROVIDER_SLACK: ProviderDescriptor(
        id=PROVIDER_SLACK,
        label="Slack",
        fields=[
            ProviderField(
                key="bot_token",
                label="Bot token",
                type="secret",
                required=True,
                placeholder="xoxb-…",
                help="Create a Slack app with a bot user, add the chat:write, "
                "channels:read and users:read.email scopes, install it, and paste the "
                "Bot User OAuth Token. Stored server-side and never shown again.",
            ),
            _FIELD_DEEP_LINK,
        ],
        channel_id_label="Channel ID",
        channel_id_placeholder="Slack channel ID (e.g. C0123ABCD)",
        channel_id_help="Invite the bot to the channel, then copy the channel ID "
        "from the channel details.",
    ),
}

_PROVIDERS: dict[str, type] = {
    PROVIDER_MATTERMOST: MattermostProvider,
    PROVIDER_SLACK: SlackProvider,
}


def get_provider(name: str | None) -> CommunicationProvider:
    """Return a provider instance for ``name`` (falls back to Mattermost)."""
    cls = _PROVIDERS.get(name or "", MattermostProvider)
    return cls()


def is_supported(name: str | None) -> bool:
    return name in _PROVIDERS


def provider_ids() -> list[str]:
    return list(_PROVIDERS)


def descriptors() -> list[ProviderDescriptor]:
    return list(_DESCRIPTORS.values())


def get_descriptor(name: str | None) -> ProviderDescriptor | None:
    return _DESCRIPTORS.get(name or "")
