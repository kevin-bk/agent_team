-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board jira_sync_status

-- Per-board toggle: whether a Jira sync overwrites the local task status with
-- the mapped Jira status (default on, preserving prior behaviour).
ALTER TABLE plugin_agent_team_board ADD COLUMN jira_sync_status BOOLEAN NOT NULL DEFAULT TRUE;
