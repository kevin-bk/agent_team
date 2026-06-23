-- migrate: skip_if_table_missing plugin_agent_team_run
-- migrate: skip_if_column_exists plugin_agent_team_run cli_usage_text

ALTER TABLE plugin_agent_team_run ADD COLUMN cli_usage_text VARCHAR(255);
