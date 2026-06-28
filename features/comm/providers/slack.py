"""Slack provider via a bot token + Web API.

Outbound posts use ``chat.postMessage``; threading replies pass ``thread_ts`` of
the root message. Mentions use Slack's ``<@USERID>`` syntax (a plain ``@handle``
does not notify), so we resolve user ids via ``users.lookupByEmail``.

Slack's Web API always responds ``200`` with an ``{"ok": bool, "error": ...}``
body, so success is read from the payload, not the HTTP status.
"""

from __future__ import annotations

import logging

import httpx

from agent_team.features.comm.providers.base import (
    CommMessage,
    DeliveryResult,
    ProviderTarget,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_TIMEOUT = 15.0

#: Severity → a small leading marker so a busy channel scans quickly.
_SEVERITY_MARK = {
    "info": "",
    "success": ":white_check_mark: ",
    "warning": ":warning: ",
    "error": ":rotating_light: ",
}


class SlackProvider:
    provider = "slack"

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def render_text(self, message: CommMessage) -> str:
        """Build the Slack ``mrkdwn`` body (bold = ``*x*``, link = ``<url|text>``)."""
        mark = _SEVERITY_MARK.get(message.severity, "")
        lines = [f"{mark}*{message.title}*"]
        if message.body:
            lines += ["", message.body]
        ids = [m.user_id for m in message.mentions if m.user_id]
        if ids:
            lines += ["", " ".join(f"<@{uid}>" for uid in ids)]
        if message.url:
            lines += ["", f"<{message.url}|Open task>"]
        return "\n".join(lines)

    def send(self, target: ProviderTarget, message: CommMessage) -> DeliveryResult:
        if not target.bot_token or not target.channel_id:
            return DeliveryResult(ok=False, error="Connection is not fully configured")
        payload: dict = {
            "channel": target.channel_id,
            "text": self.render_text(message),
        }
        thread_ts = target.root_id if (target.use_threads and target.root_id) else None
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            resp = httpx.post(
                f"{_API_BASE}/chat.postMessage",
                json=payload,
                headers=self._headers(target.bot_token),
                timeout=_TIMEOUT,
            )
        except httpx.RequestError as exc:
            return DeliveryResult(ok=False, error=f"Could not reach Slack: {exc}")
        if resp.status_code >= 400:
            return DeliveryResult(
                ok=False, error=f"Slack returned an error ({resp.status_code})"
            )
        try:
            data = resp.json()
        except ValueError:
            return DeliveryResult(ok=False, error="Slack returned an unreadable response")
        if not data.get("ok"):
            return DeliveryResult(ok=False, error=f"Slack error: {data.get('error', 'unknown')}")
        ts = data.get("ts")
        # The stable thread root is the ts we replied under (or this message's ts
        # when it starts a new thread) — never the reply's own ts.
        thread_root = thread_ts or ts
        return DeliveryResult(ok=True, provider_message_id=ts, provider_thread_id=thread_root)

    def resolve_username(
        self, *, server_url: str, bot_token: str, email: str
    ) -> tuple[str | None, str | None]:
        if not bot_token or not email:
            return None, None
        try:
            resp = httpx.get(
                f"{_API_BASE}/users.lookupByEmail",
                params={"email": email},
                headers={"Authorization": f"Bearer {bot_token}"},
                timeout=_TIMEOUT,
            )
        except httpx.RequestError:
            return None, None
        if resp.status_code >= 400:
            return None, None
        try:
            data = resp.json()
        except ValueError:
            return None, None
        if not data.get("ok"):
            return None, None
        user = data.get("user") or {}
        return user.get("id"), user.get("name")
