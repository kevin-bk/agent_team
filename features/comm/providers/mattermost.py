"""Mattermost provider via the bot account + REST API.

Outbound + threading patterns mirror the existing adapter in
``hermes-agent/gateway/platforms/mattermost.py`` (bearer bot token, create posts
with ``root_id`` for threads). The difference here: the token is read per
connection from the DB, not from a process-wide env var.

Bot API is used (not an incoming webhook) so we get the post id + thread root id
back, which a later inbound slice needs for reply mapping.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from agent_team.features.comm.providers.base import (
    CommMessage,
    DeliveryResult,
    ProviderTarget,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0

#: Severity → a small leading marker so a busy channel scans quickly.
_SEVERITY_MARK = {
    "info": "",
    "success": ":white_check_mark: ",
    "warning": ":warning: ",
    "error": ":rotating_light: ",
}


class MattermostProvider:
    provider = "mattermost"

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def render_text(self, message: CommMessage) -> str:
        """Build the Markdown post body (title + body + mentions + link)."""
        mark = _SEVERITY_MARK.get(message.severity, "")
        lines = [f"{mark}**{message.title}**"]
        if message.body:
            lines += ["", message.body]
        handles = [m.handle for m in message.mentions if m.handle]
        if handles:
            lines += ["", " ".join(f"@{h}" for h in handles)]
        if message.url:
            lines += ["", f"[Open task]({message.url})"]
        return "\n".join(lines)

    def send(self, target: ProviderTarget, message: CommMessage) -> DeliveryResult:
        if not target.server_url or not target.bot_token or not target.channel_id:
            return DeliveryResult(ok=False, error="Connection is not fully configured")
        url = target.server_url.rstrip("/") + "/api/v4/posts"
        payload: dict = {
            "channel_id": target.channel_id,
            "message": self.render_text(message),
        }
        if target.use_threads and target.root_id:
            payload["root_id"] = target.root_id
        try:
            resp = httpx.post(
                url, json=payload, headers=self._headers(target.bot_token), timeout=_TIMEOUT
            )
        except httpx.RequestError as exc:
            return DeliveryResult(ok=False, error=f"Could not reach Mattermost: {exc}")
        if resp.status_code == 401:
            return DeliveryResult(ok=False, error="Mattermost rejected the bot token (401)")
        if resp.status_code == 403:
            return DeliveryResult(ok=False, error="The bot lacks permission for this channel (403)")
        if resp.status_code >= 400:
            return DeliveryResult(
                ok=False, error=f"Mattermost returned an error ({resp.status_code})"
            )
        try:
            data = resp.json()
        except ValueError:
            return DeliveryResult(ok=False, error="Mattermost returned an unreadable response")
        post_id = data.get("id")
        root_id = data.get("root_id") or post_id
        return DeliveryResult(ok=True, provider_message_id=post_id, provider_thread_id=root_id)

    def resolve_username(
        self, *, server_url: str, bot_token: str, email: str
    ) -> tuple[str | None, str | None]:
        if not server_url or not bot_token or not email:
            return None, None
        url = server_url.rstrip("/") + f"/api/v4/users/email/{quote(email, safe='')}"
        try:
            resp = httpx.get(url, headers=self._headers(bot_token), timeout=_TIMEOUT)
        except httpx.RequestError:
            return None, None
        if resp.status_code >= 400:
            return None, None
        try:
            data = resp.json()
        except ValueError:
            return None, None
        return data.get("id"), data.get("username")
