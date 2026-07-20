-- migrate: skip_if_column_exists plugin_agent_team_run workspace_override_path
ALTER TABLE plugin_agent_team_run ADD COLUMN workspace_override_path TEXT;
