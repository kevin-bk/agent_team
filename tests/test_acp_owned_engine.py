"""Unit tests for the agent-team-owned ACP engine (pure parts, no subprocess).

Covers the translator frame mapping, secret masking, MCP config conversion,
usage encoding/decoding, engine helpers, and the worker's flag-based engine
selection. The live session manager (real ACP subprocess) is out of scope here.
"""

from __future__ import annotations

from agent_team.features.board.runtime.acp import engines
from agent_team.features.board.runtime.acp.masking import SecretMasker, mask_json_value
from agent_team.features.board.runtime.acp.mcp import mcp_config_to_acp_servers
from agent_team.features.board.runtime.acp.run import _DirectAcpTranslator
from agent_team.features.board.runtime.acp.usage import (
    extract_token_usage,
    parse_gauge_tokens,
    parse_totals,
)


def test_translator_text_thinking_and_usage():
    t = _DirectAcpTranslator()
    assert t.on_delta({"claude_acp_progress": "hello"})[0][0] == "text_delta"
    assert t.on_delta({"claude_acp_thought": "thinking"})[0][0] == "thinking"
    assert t.on_delta({"claude_acp_plan": "- step"})[0][0] == "thinking"
    usage_frames = t.on_delta({"claude_acp_usage": "10/200 tokens"})
    assert usage_frames[0][0] == "usage"
    assert t.cli_usage_text == "10/200 tokens"
    # usage_final is stored for finalize, emits no live frame
    assert t.on_delta({"claude_acp_usage_final": "10\x0020\x0030\x005"}) == []
    assert t.totals == {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "cache_read_tokens": 5,
    }


def test_translator_tool_card_open_and_close():
    t = _DirectAcpTranslator()
    start = t.on_delta({"claude_acp_tool_start": "shell\x00Run tests\x00id1\x00pytest -q"})
    assert start[0][0] == "tool_use_start"
    end = t.on_delta(
        {"claude_acp_tool_progress": "id1\x00completed\x00done\x00all green\x00pytest -q"}
    )
    assert end[0][0] == "tool_use_end"
    # The card is closed; a stray later progress for the same id yields nothing.
    assert t.on_delta({"claude_acp_tool_progress": "id1\x00completed\x00x\x00y\x00z"}) == []


def test_translator_finalize_closes_open_cards():
    t = _DirectAcpTranslator()
    t.on_delta({"claude_acp_tool_start": "shell\x00Build\x00id9\x00make"})
    closed = t.finalize()
    assert len(closed) == 1 and closed[0][0] == "tool_use_end"
    # Idempotent: nothing left to close.
    assert t.finalize() == []


def test_secret_masker_basic_and_inactive():
    m = SecretMasker(["supersecretvalue"])
    assert m.active is True
    assert m("k=supersecretvalue end") == "k=*** end"
    # Too-short secrets are ignored (would mangle ordinary text).
    assert SecretMasker(["ab"]).active is False
    assert SecretMasker([])("untouched") == "untouched"


def test_mask_json_value_recurses_into_structures():
    m = SecretMasker(["supersecretvalue"])
    masked = mask_json_value(
        {"a": "supersecretvalue", "b": [1, "x supersecretvalue"], "c": 5}, m
    )
    assert masked == {"a": "***", "b": [1, "x ***"], "c": 5}


def test_mcp_stdio_forwarded_and_remote_gated():
    stdio = mcp_config_to_acp_servers(
        {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "x"], "env": {"K": "V"}}}},
        None,
    )
    assert len(stdio) == 1 and type(stdio[0]).__name__ == "McpServerStdio"

    # Remote transport dropped when the engine advertises no capability.
    assert (
        mcp_config_to_acp_servers(
            {"mcpServers": {"web": {"url": "https://x", "transport": "http"}}}, None
        )
        == []
    )

    class _Caps:
        http = True
        sse = False

    http = mcp_config_to_acp_servers(
        {"mcpServers": {"web": {"url": "https://x"}}}, _Caps()
    )
    assert len(http) == 1 and type(http[0]).__name__ == "HttpMcpServer"


def test_usage_helpers():
    assert parse_totals("1\x002\x000\x000") == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "cache_read_tokens": 0,
    }
    assert parse_gauge_tokens("45,000/200,000 tokens") == (45000, 200000)
    assert parse_gauge_tokens("nonsense") == (0, 0)

    class _Usage:
        input_tokens = 7
        output_tokens = 11
        cached_read_tokens = 2
        cached_write_tokens = 0
        thought_tokens = 1

    class _Resp:
        usage = _Usage()

    assert extract_token_usage(_Resp()) == (7, 11, 2, 0, 1)


def test_engine_aliases_and_targets():
    assert engines.engine_for_alias("cli:claude") == "claude"
    assert engines.is_direct_cli_alias("cli:codex") is True
    assert engines.is_direct_cli_alias("agent-x") is False
    assert engines.alias_for_engine("cursor") == "cli:cursor"
    assert "cli:claude" in engines.known_cli_aliases()
    ids = {t["id"] for t in engines.available_targets()}
    assert {"cli:claude", "cli:cursor", "cli:codex"} <= ids


def test_flag_selects_engine(monkeypatch):
    # Exercise the selector directly (no module reload, so class identity used by
    # other tests is untouched).
    from agent_team.features.board.runtime.workers import acp_cli

    monkeypatch.setenv("AGENT_TEAM_ACP_ENGINE", "owned")
    owned_run, _ = acp_cli._load_direct_cli()
    assert owned_run.__module__ == "agent_team.features.board.runtime.acp.run"

    monkeypatch.setenv("AGENT_TEAM_ACP_ENGINE", "legacy")
    legacy_run, _ = acp_cli._load_direct_cli()
    assert legacy_run.__module__ == "agent_team.features.board.runtime.direct_acp"

    # Default (unset) is the owned engine — it supports structured plan updates
    # and per-agent MCP pass-through that the strict workflow relies on.
    monkeypatch.delenv("AGENT_TEAM_ACP_ENGINE", raising=False)
    default_run, _ = acp_cli._load_direct_cli()
    assert default_run.__module__ == "agent_team.features.board.runtime.acp.run"
