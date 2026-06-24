-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task reporter_id

-- Human reporter mapped from Jira's reporter (by account email) on sync.
ALTER TABLE plugin_agent_team_task ADD COLUMN reporter_id VARCHAR(36);
