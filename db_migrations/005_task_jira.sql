-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task jira_key

ALTER TABLE plugin_agent_team_task ADD COLUMN jira_key VARCHAR(64);
ALTER TABLE plugin_agent_team_task ADD COLUMN jira_url VARCHAR(1024);
