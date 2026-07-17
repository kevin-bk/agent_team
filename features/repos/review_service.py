"""Create GitLab merge requests or GitHub pull requests with stored tokens."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from agent_team.features.repos.git_service import _effective_url, _to_https
from agent_team.features.repos.models import AUTH_TOKEN, AgentTeamRepo


class ReviewRequestError(RuntimeError):
    """The repository host or credential could not create a review request."""


@dataclass(frozen=True)
class ReviewRequestResult:
    provider: str
    number: str
    url: str
    title: str
    existing: bool = False


@dataclass(frozen=True)
class RemoteProject:
    provider: str
    host: str
    path: str
    api_base: str


def parse_remote_project(repo: AgentTeamRepo) -> RemoteProject:
    """Resolve provider/project coordinates from HTTPS or SSH Git remotes."""
    raw = _to_https(_effective_url(repo))
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or "/" not in path:
        raise ReviewRequestError("Repository remote is not a supported hosted project URL.")
    if host == "github.com" or "github" in host:
        api_base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        provider = "github"
    elif host == "gitlab.com" or "gitlab" in host:
        api_base = f"https://{host}/api/v4"
        provider = "gitlab"
    else:
        raise ReviewRequestError(
            f"Automatic merge requests are not supported for host '{host}'."
        )
    return RemoteProject(provider=provider, host=host, path=path, api_base=api_base)


def _api_token(repo: AgentTeamRepo) -> str:
    token = (repo.auth_secret or "").strip()
    if repo.auth_type != AUTH_TOKEN or not token:
        raise ReviewRequestError(
            "Merge request creation needs token authentication with API scope "
            "on this repository. SSH-only credentials can push but cannot call "
            "the Git provider API."
        )
    return token


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None
    request_headers = {"Accept": "application/json", **headers}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 — trusted repo host
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        raise ReviewRequestError(
            f"Provider API returned HTTP {exc.code}: {str(detail)[:500]}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ReviewRequestError(f"Provider API request failed: {exc}") from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewRequestError("Provider API returned invalid JSON.") from exc


def _gitlab_request(
    repo: AgentTeamRepo,
    project: RemoteProject,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    draft: bool,
) -> ReviewRequestResult:
    headers = {"PRIVATE-TOKEN": _api_token(repo)}
    project_id = quote(project.path, safe="")
    query = urlencode(
        {
            "state": "opened",
            "source_branch": source_branch,
            "target_branch": target_branch,
        }
    )
    existing = _request_json(
        "GET",
        f"{project.api_base}/projects/{project_id}/merge_requests?{query}",
        headers=headers,
    )
    if isinstance(existing, list) and existing:
        row = existing[0]
        return ReviewRequestResult(
            provider="gitlab",
            number=str(row.get("iid") or row.get("id") or ""),
            url=str(row.get("web_url") or ""),
            title=str(row.get("title") or title),
            existing=True,
        )
    rendered_title = (
        f"Draft: {title}"
        if draft and not title.lower().startswith("draft:")
        else title
    )
    row = _request_json(
        "POST",
        f"{project.api_base}/projects/{project_id}/merge_requests",
        headers=headers,
        payload={
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": rendered_title,
            "description": description,
            "remove_source_branch": False,
        },
    )
    if not isinstance(row, dict) or not row.get("web_url"):
        raise ReviewRequestError("GitLab did not return a merge request URL.")
    return ReviewRequestResult(
        provider="gitlab",
        number=str(row.get("iid") or row.get("id") or ""),
        url=str(row["web_url"]),
        title=str(row.get("title") or rendered_title),
    )


def _github_request(
    repo: AgentTeamRepo,
    project: RemoteProject,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    draft: bool,
) -> ReviewRequestResult:
    headers = {
        "Authorization": f"Bearer {_api_token(repo)}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-team",
    }
    owner = project.path.split("/", 1)[0]
    query = urlencode(
        {"state": "open", "head": f"{owner}:{source_branch}", "base": target_branch}
    )
    existing = _request_json(
        "GET",
        f"{project.api_base}/repos/{project.path}/pulls?{query}",
        headers=headers,
    )
    if isinstance(existing, list) and existing:
        row = existing[0]
        return ReviewRequestResult(
            provider="github",
            number=str(row.get("number") or ""),
            url=str(row.get("html_url") or ""),
            title=str(row.get("title") or title),
            existing=True,
        )
    row = _request_json(
        "POST",
        f"{project.api_base}/repos/{project.path}/pulls",
        headers=headers,
        payload={
            "head": source_branch,
            "base": target_branch,
            "title": title,
            "body": description,
            "draft": draft,
        },
    )
    if not isinstance(row, dict) or not row.get("html_url"):
        raise ReviewRequestError("GitHub did not return a pull request URL.")
    return ReviewRequestResult(
        provider="github",
        number=str(row.get("number") or ""),
        url=str(row["html_url"]),
        title=str(row.get("title") or title),
    )


def create_review_request(
    repo: AgentTeamRepo,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    draft: bool,
) -> ReviewRequestResult:
    """Return the existing open review request or create it idempotently."""
    project = parse_remote_project(repo)
    kwargs = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title,
        "description": description,
        "draft": draft,
    }
    if project.provider == "gitlab":
        return _gitlab_request(repo, project, **kwargs)
    return _github_request(repo, project, **kwargs)
