"""Generic credential injection for the isolated OpenSandbox runtime.

A provider-agnostic layer that gets coding-agent (and future) credentials into a
task sandbox through pluggable backends:

* ``env``   — forward a real token env var into the sandbox (Claude OAuth token).
* ``mount`` — mount a writable config dir into the sandbox (Codex ``auth.json``).
* ``vault`` — inject the secret at egress via OpenSandbox Credential Vault so the
  real secret never enters the sandbox (Đợt 3).

The only provider-specific piece is a declarative descriptor in
:mod:`registry`; adding a new credential (GitHub token, private API) is one entry
there — no coding-agent code changes. See
``docs/plans/opensandbox-phase3-credentials.md``.
"""
