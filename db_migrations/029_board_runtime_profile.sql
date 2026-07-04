-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board runtime_profile_json

-- Per-board runtime profile override for the isolated OpenSandbox runtime.
-- JSON object shaped like RuntimeProfile (provider / image / cpu / memory /
-- idle_timeout_minutes / strict_isolation / workspace_mode / ...). Empty object
-- means "use the process env defaults" (AGENT_TEAM_RUNTIME_* / OPEN_SANDBOX_*).
-- Stored as text for portability across SQLite and PostgreSQL.
ALTER TABLE plugin_agent_team_board ADD COLUMN runtime_profile_json TEXT NOT NULL DEFAULT '{}';
