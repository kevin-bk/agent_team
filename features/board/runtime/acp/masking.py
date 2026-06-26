"""Secret masking for streamed ACP output.

An ACP subprocess can echo an injected credential (an env value, a task secret)
into its assistant text or a tool call's params/output. Those frames are
persisted and relayed to the UI, so a secret must never reach them in cleartext.

:class:`SecretMasker` is a plain ``str.replace`` over a set of known secret
values; :func:`mask_json_value` walks an arbitrary JSON-like value and masks
every string leaf so tool-call ``raw_input`` / ``raw_output`` / ``content``
(which may be a bare string, a dict, or a list of blocks) are covered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Shortest secret worth masking — masking 1–3 char values would mangle ordinary
#: text without protecting anything meaningful.
_MIN_SECRET_LEN = 4


class SecretMasker:
    """Replaces each known secret value with a fixed placeholder in any string."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        # Longest first so an overlapping shorter secret cannot pre-empt a longer
        # one and leave a tail of the longer value exposed.
        self._secrets = sorted(
            {s for s in (secrets or []) if s and len(s) >= _MIN_SECRET_LEN},
            key=len,
            reverse=True,
        )

    @property
    def active(self) -> bool:
        return bool(self._secrets)

    def __call__(self, text: str) -> str:
        if not text or not self._secrets:
            return text
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "***")
        return text


def mask_json_value(value: Any, mask: Callable[[str], str]) -> Any:
    """Recursively apply ``mask`` to every string leaf of a JSON-like value.

    Non-string leaves (ints, bools, ``None``) pass through unchanged. A pydantic
    model is converted to its dict form first so nested params are reached.
    """
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: mask_json_value(v, mask) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_json_value(item, mask) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return mask_json_value(dump(by_alias=True), mask)
        except Exception:
            return value
    return value
