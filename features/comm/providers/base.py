"""Provider-agnostic message types and the outbound provider protocol.

v1 only needs outbound ``send``. The protocol is intentionally narrow so Slack /
email / webhook providers can be added later without touching loop/planning code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Mention:
    """A resolved person to ``@mention``.

    Providers format mentions differently — Mattermost uses ``@handle`` text,
    Slack needs ``<@USERID>`` — so we carry both the provider user id and the
    handle and let each provider pick what it needs.
    """

    user_id: str | None = None
    handle: str | None = None


@dataclass
class CommMessage:
    """A provider-agnostic outbound message."""

    title: str
    body: str = ""
    url: str | None = None
    severity: str = "info"
    #: People to ``@mention`` (best-effort); each provider formats them.
    mentions: list[Mention] = field(default_factory=list)
    #: Stable key for threading subsequent messages of the same task together.
    thread_key: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class DeliveryResult:
    """Outcome of one send attempt."""

    ok: bool
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    error: str | None = None


@dataclass
class ProviderTarget:
    """Everything a provider needs to address one destination."""

    server_url: str
    bot_token: str
    channel_id: str
    use_threads: bool = True
    #: Provider message id of an existing root post to reply under (threading).
    root_id: str | None = None


class CommunicationProvider(Protocol):
    provider: str

    def send(self, target: ProviderTarget, message: CommMessage) -> DeliveryResult:
        """Send one message to one destination (synchronous, best-effort)."""
        ...

    def resolve_username(
        self, *, server_url: str, bot_token: str, email: str
    ) -> tuple[str | None, str | None]:
        """Look up a provider ``(user_id, username)`` by email, or ``(None, None)``."""
        ...
