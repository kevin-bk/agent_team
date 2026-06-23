"""Git **credential helper** for per-task repo working copies (token auth).

This is the "Cách B" of the board-wiki push design: a task working copy's push
remote points at the **real git host**, but the credential is **never written to
disk**. Instead each copy is configured with::

    git config credential.helper '!<python> <this file> --cred-file <.git/at_cred.json>'

so when ``git push`` needs a password git runs this script. The script reads the
tiny, non-secret cred file (``{task_id, repo_id}``), opens the app DB, re-checks
the **same push policy the ``git_push`` tool enforces** (the repo's admin
``allow_push`` master gate AND the board's per-assignment opt-in), and only then
returns the stored token to git over stdout. The token lives in the DB, not in
the workspace — a plain ``git push`` (from a direct-CLI agent or an LLM) goes
straight to the host, gated by policy.

Security note (honest): the helper runs inside the task workspace, so a
determined agent could invoke it to read the token. Cách B keeps the token out of
plaintext config, keeps the ``allow_push`` gate, and pairs with a pre-push hook
that blocks the default branch — it raises the bar, it does not sandbox.

SSH-auth repos do not use this helper (ssh authenticates with a key); see
``task_copy`` for how their push is wired.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _bootstrap_sys_path() -> None:
    """Make ``core`` and ``agent_team`` importable when run as a bare script.

    The helper is spawned by git as its own process and does not inherit the
    app's runtime ``sys.path``. Derive the roots from this file's location:
    ``.../<root>/community_plugins/agent_team/features/repos/git_cred_helper.py``.
    """
    here = Path(__file__).resolve()
    community_plugins = here.parents[3]
    app_root = here.parents[4]
    for p in (str(community_plugins), str(app_root / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)


def resolve_credentials(db, *, task_id: str, repo_id: str) -> tuple[str, str] | None:
    """Return ``(username, token)`` if this task may push this repo, else ``None``.

    Mirrors the ``git_push`` tool's gate exactly: effective push requires the
    repo's admin ``allow_push`` master gate **and** the board's per-assignment
    opt-in, and only token auth yields a credential here.
    """
    from agent_team.features.board.models import AgentTeamTask
    from agent_team.features.repos.models import AUTH_TOKEN
    from agent_team.features.repos.repositories import repos_for_board

    task = db.query(AgentTeamTask).filter(AgentTeamTask.id == task_id).first()
    if task is None:
        return None
    match = next(
        (
            (repo, board_allow)
            for repo, _branch, board_allow, _is_wiki in repos_for_board(db, task.board_id)
            if repo.id == repo_id
        ),
        None,
    )
    if match is None:
        return None
    repo, board_allow_push = match
    # Effective push = admin master gate AND this board's opt-in (same as git_push).
    if not (repo.allow_push and board_allow_push):
        return None
    if repo.auth_type != AUTH_TOKEN:
        return None
    secret = (repo.auth_secret or "").strip()
    if not secret:
        return None
    user = (repo.auth_username or "").strip() or "x-access-token"
    return user, secret


def main(argv: list[str]) -> int:
    # Git invokes the helper as ``<helper> [--cred-file X] <operation>`` where
    # operation is get/store/erase. We only answer ``get``; the request key=value
    # lines arrive on stdin but the cred file already identifies the repo, so we
    # don't need to parse them.
    cred_file: str | None = None
    operation: str | None = None
    i = 0
    while i < len(argv):
        if argv[i] == "--cred-file" and i + 1 < len(argv):
            cred_file = argv[i + 1]
            i += 2
            continue
        operation = argv[i]
        i += 1
    if operation != "get" or not cred_file:
        return 0

    try:
        data = json.loads(Path(cred_file).read_text(encoding="utf-8"))
        task_id = str(data["task_id"])
        repo_id = str(data["repo_id"])
    except (OSError, ValueError, KeyError):
        return 0

    _bootstrap_sys_path()
    try:
        from core.database.base import SessionLocal
    except Exception:
        return 0

    db = SessionLocal()
    try:
        creds = resolve_credentials(db, task_id=task_id, repo_id=repo_id)
    except Exception:
        return 0
    finally:
        db.close()

    if not creds:
        return 0
    user, token = creds
    # Credential-helper protocol: emit username/password on stdout for ``get``.
    sys.stdout.write(f"username={user}\npassword={token}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
