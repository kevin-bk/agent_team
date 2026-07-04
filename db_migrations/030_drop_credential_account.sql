-- Drop the short-lived credential-account registry.
-- The isolated runtime now reuses the AI Code Factory Environment Pool for
-- coding-agent logins (see runtime/credentials/ai_code_source.py), so this
-- dedicated table is no longer created or read. IF EXISTS keeps it idempotent
-- whether or not the earlier 030 table-create ever ran on this deployment.
DROP TABLE IF EXISTS plugin_agent_team_credential_account;
