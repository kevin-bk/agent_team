"""Generic credential injection for the isolated OpenSandbox runtime.

A provider-agnostic layer that gets coding-agent (and future) credentials into a
task sandbox through pluggable backends:

* ``mount`` — mount a writable login/config dir into the sandbox (used by both
  Claude ``CLAUDE_CONFIG_DIR`` and Codex ``$CODEX_HOME``).
* ``env``   — forward a real token env var into the sandbox (future API keys).
* ``vault`` — inject a header secret at egress via OpenSandbox Credential Vault
  so the real secret never enters the sandbox (future header-token providers).

Accounts are **not** stored here: :mod:`ai_code_source` resolves them from the
AI Code Factory Environment Pool, and :func:`service.build_injection_for_board`
merges one plan per coding agent staffed on a board (plus remote-MCP egress
rules). The only provider-specific piece is a declarative descriptor in
:mod:`registry`; adding a new credential (GitHub token, private API) is one entry
there — no coding-agent code changes. See
``docs/plans/opensandbox-phase3-credentials.md``.
"""
