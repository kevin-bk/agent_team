-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board jira_sync_filter_json

ALTER TABLE plugin_agent_team_board ADD COLUMN jira_sync_filter_json TEXT NOT NULL DEFAULT '{}';
