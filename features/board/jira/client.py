"""Thin Jira Cloud REST client (read-only, Phase 1).

Uses Basic auth (service-account email + API token) and the v2 REST API so
issue descriptions come back as plain text/wiki rather than ADF JSON, which
keeps the Phase-1 mapping simple. One client instance is scoped to one board's
configured Jira site + credentials.
"""

from __future__ import annotations

import base64

import httpx


class JiraError(Exception):
    """A Jira call failed; ``message`` is safe to surface to the user."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


#: Only the fields Phase 1 maps onto a task — keeps payloads small.
_ISSUE_FIELDS = "summary,description,status,priority,assignee,labels,issuetype"


class JiraClient:
    def __init__(self, *, base_url: str, email: str, api_token: str, timeout: float = 15.0):
        if not base_url or not email or not api_token:
            raise JiraError("Jira is not fully configured for this board")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        raw = f"{email}:{api_token}".encode()
        self._auth_header = "Basic " + base64.b64encode(raw).decode("ascii")

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self._base_url}{path}"
        try:
            resp = httpx.get(
                url,
                params=params,
                headers={
                    "Authorization": self._auth_header,
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise JiraError(f"Could not reach Jira: {exc}") from exc

        if resp.status_code == 401:
            raise JiraError("Jira rejected the credentials (401)", status_code=401)
        if resp.status_code == 403:
            raise JiraError("The Jira account lacks permission (403)", status_code=403)
        if resp.status_code == 404:
            raise JiraError("Jira resource was not found (404)", status_code=404)
        if resp.status_code >= 400:
            raise JiraError(
                f"Jira returned an error ({resp.status_code})",
                status_code=resp.status_code,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise JiraError("Jira returned an unreadable response") from exc

    def get_issue(self, key: str) -> dict:
        """Fetch one issue's mapped fields (+ attachments). Raises on failure."""
        try:
            return self._get(
                f"/rest/api/2/issue/{key}",
                {"fields": _ISSUE_FIELDS + ",attachment"},
            )
        except JiraError as exc:
            if exc.status_code == 404:
                raise JiraError(f"Issue {key} was not found in Jira", status_code=404) from exc
            raise

    def download(self, url: str) -> bytes:
        """Download an attachment's binary content by its Jira content URL."""
        try:
            resp = httpx.get(
                url,
                headers={"Authorization": self._auth_header},
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.RequestError as exc:
            raise JiraError(f"Could not download attachment: {exc}") from exc
        if resp.status_code >= 400:
            raise JiraError(
                f"Attachment download failed ({resp.status_code})",
                status_code=resp.status_code,
            )
        return resp.content

    def search_issues(self, jql: str, *, max_results: int = 100) -> list[dict]:
        """Run a JQL search and return up to ``max_results`` issues.

        Uses the current ``/search/jql`` endpoint (the legacy ``/search`` was
        removed and now returns 410 Gone) with its ``nextPageToken`` cursor
        pagination — there is no ``total``/``startAt`` anymore.
        """
        issues: list[dict] = []
        next_token: str | None = None
        while len(issues) < max_results:
            params: dict = {
                "jql": jql,
                # Repeated ``fields`` params = the array form the new API expects.
                "fields": _ISSUE_FIELDS.split(","),
                "maxResults": min(100, max_results - len(issues)),
            }
            if next_token:
                params["nextPageToken"] = next_token
            data = self._get("/rest/api/2/search/jql", params)
            batch = data.get("issues") or []
            issues.extend(batch)
            next_token = data.get("nextPageToken")
            if not batch or not next_token:
                break
        return issues[:max_results]

    def get_comments(self, key: str, *, max_results: int = 200) -> list[dict]:
        """Fetch an issue's comments (oldest first), paginated via startAt/total."""
        comments: list[dict] = []
        start_at = 0
        while len(comments) < max_results:
            data = self._get(
                f"/rest/api/2/issue/{key}/comment",
                {
                    "startAt": start_at,
                    "maxResults": min(100, max_results - len(comments)),
                    "orderBy": "created",
                },
            )
            batch = data.get("comments") or []
            comments.extend(batch)
            total = data.get("total", 0)
            start_at += len(batch)
            if not batch or start_at >= total:
                break
        return comments[:max_results]

    def browse_url(self, key: str) -> str:
        """Human-facing URL to open the issue in the Jira UI."""
        return f"{self._base_url}/browse/{key}"
