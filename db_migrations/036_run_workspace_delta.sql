-- migrate: skip_if_table_missing plugin_agent_team_run
-- migrate: skip_if_column_exists plugin_agent_team_run workspace_delta_json

-- Frozen file/tool delta used to hand an interrupted turn to its successor.
ALTER TABLE plugin_agent_team_run ADD COLUMN workspace_delta_json TEXT;
