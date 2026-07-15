-- migrate: skip_if_column_exists plugin_agent_team_repo bootstrap_command

ALTER TABLE plugin_agent_team_repo ADD COLUMN bootstrap_command TEXT;
