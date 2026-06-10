"""Jira sync for the agent_team board (Phase 1: one-way pull, per-board config).

Each board can point at its own Jira site/project with its own service-account
credentials. The API token is stored as-is (same convention as the core LLM
provider credentials) and never returned to the client. Syncing pulls an
issue's fields onto the linked task; see :mod:`.client` and :mod:`.sync`.
"""
