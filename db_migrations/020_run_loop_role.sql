-- migrate: skip_if_table_missing plugin_agent_team_run
-- migrate: skip_if_column_exists plugin_agent_team_run role

-- Loop stage + attempt link on runs. ``attempt_id`` references the brand-new
-- attempt table created on startup. The column is plain (no FK enforced here)
-- to keep the migration portable across SQLite and PostgreSQL.
ALTER TABLE plugin_agent_team_run ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'chat';
ALTER TABLE plugin_agent_team_run ADD COLUMN attempt_id VARCHAR(32);
