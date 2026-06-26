"""Token-usage extraction, cost estimation, and the streamed usage encoding.

ACP reports usage two ways:

* a live *context-window gauge* (``UsageUpdate``: used/size) shown during a turn, and
* the turn's authoritative cumulative totals on ``PromptResponse.usage`` (the
  ``Usage`` type), surfaced once the prompt returns.

The gauge is encoded as a human string; the totals as a NUL-delimited tuple that
the translator decodes back into a usage dict.
"""

from __future__ import annotations

from typing import Any


def format_gauge(used: int, size: int, cost: Any) -> str:
    """Render the live context-window gauge (e.g. ``"45,000/200,000 tokens"``)."""
    text = f"{used:,}/{size:,} tokens"
    if cost is not None:
        amount = getattr(cost, "amount", None)
        currency = str(getattr(cost, "currency", "USD") or "USD")
        if amount is not None:
            text += f" · ${float(amount):.4f} {currency}"
    return text


def format_totals(usage: Any) -> str:
    """Encode cumulative totals as ``input\\x00output\\x00total\\x00cache_read``.

    ``PromptResponse.usage`` counters are cumulative across the whole session,
    so a consumer should take the latest turn's value, not sum across turns.
    """
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0)
    cache = int(getattr(usage, "cached_read_tokens", 0) or 0)
    return f"{inp}\x00{out}\x00{total}\x00{cache}"


def parse_totals(value: str) -> dict[str, int] | None:
    """Decode the NUL-delimited totals string back into a usage dict."""
    parts = value.split("\x00")
    try:
        nums = [int(p) for p in parts[:4]]
    except (TypeError, ValueError):
        return None
    nums += [0] * (4 - len(nums))
    inp, out, total, cache = nums[0], nums[1], nums[2], nums[3]
    if total <= 0:
        total = inp + out
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": total,
        "cache_read_tokens": cache,
    }


def parse_gauge_tokens(text: str | None) -> tuple[int, int]:
    """Best-effort ``(used, size)`` from a gauge like ``"45,000/200,000 tokens"``.

    Used as a fallback when the engine does not report ``PromptResponse.usage``:
    ``used`` is the conversation's current context occupancy. Returns ``(0, 0)``
    when unparseable.
    """
    if not text:
        return 0, 0
    head = text.split("tokens", 1)[0].replace(",", "").strip()
    if "/" not in head:
        return 0, 0
    used_str, _, size_str = head.partition("/")
    try:
        return int(used_str.strip() or 0), int(size_str.strip() or 0)
    except ValueError:
        return 0, 0


def extract_token_usage(response: Any) -> tuple[int, int, int, int, int]:
    """Extract ``(input, output, cache_read, cache_write, reasoning)`` from a response.

    Reads the standard ``response.usage`` first; falls back to a non-standard
    ``response._meta.quota.token_count`` shape some engines use.
    """
    usage = getattr(response, "usage", None)
    if usage is not None:
        return (
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
            int(getattr(usage, "cached_read_tokens", 0) or 0),
            int(getattr(usage, "cached_write_tokens", 0) or 0),
            int(getattr(usage, "thought_tokens", 0) or 0),
        )
    meta = getattr(response, "field_meta", None) or getattr(response, "_meta", None)
    if isinstance(meta, dict):
        tc = (meta.get("quota") or {}).get("token_count") or {}
        return (
            int(tc.get("input_tokens", 0) or 0),
            int(tc.get("output_tokens", 0) or 0),
            0,
            0,
            0,
        )
    return (0, 0, 0, 0, 0)


def estimate_cost_from_tokens(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost from token counts via LiteLLM pricing; ``0.0`` if unavailable."""
    try:
        import litellm

        info = litellm.model_cost.get(model, {})
        input_cost = info.get("input_cost_per_token", 0) or 0
        output_cost = info.get("output_cost_per_token", 0) or 0
        return input_tokens * input_cost + output_tokens * output_cost
    except Exception:
        return 0.0
