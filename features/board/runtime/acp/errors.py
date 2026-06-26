"""Classify ACP failures into a short, stable reason string.

The session manager maps any exception raised while creating or prompting a
session into a human-readable run outcome. Keeping the classification here (pure,
duck-typed) means the manager stays focused on lifecycle.
"""

from __future__ import annotations

_AUTH_HINTS = (
    "unauthorized",
    "authentication",
    "auth required",
    "forbidden",
    "401",
    "403",
    "api key",
    "login",
    "credentials",
)


def error_text(exc: BaseException) -> str:
    """Best-effort human text for an ACP error (handles wrapped ``data`` payloads)."""
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        detail = data.get("details") or data.get("message")
        if detail:
            return str(detail)
    return str(exc) or exc.__class__.__name__


def indicates_auth(exc: BaseException) -> bool:
    """Whether the error looks like an authentication/authorization failure."""
    text = error_text(exc).lower()
    return any(hint in text for hint in _AUTH_HINTS)


def classify_turn_error(exc: BaseException, *, label: str = "ACP") -> str:
    """Map a prompt-turn failure to a friendly one-line reason."""
    if isinstance(exc, TimeoutError):
        return f"{label} agent timed out."
    if isinstance(exc, FileNotFoundError):
        return f"{label} command was not found in PATH."
    if indicates_auth(exc):
        return f"{label} agent could not authenticate: {error_text(exc)}"
    return f"{label} agent failed: {error_text(exc)}"


def classify_init_error(exc: BaseException, *, command: str, label: str = "ACP") -> str:
    """Map a session-create failure to a friendly one-line reason."""
    if isinstance(exc, FileNotFoundError):
        return (
            f"Command '{command}' was not found in PATH. Install Node.js/npx and "
            f"the ACP package for {label}."
        )
    if isinstance(exc, TimeoutError):
        return f"{label} session did not start in time."
    if indicates_auth(exc):
        return f"{label} agent could not authenticate: {error_text(exc)}"
    return f"{label} session failed to start: {error_text(exc)}"


def install_hint() -> str:
    return (
        "agent-client-protocol is not installed. Add it via the ai_code plugin "
        "requirements.txt and run `uv run setup`."
    )
