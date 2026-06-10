-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board jira_enabled

ALTER TABLE plugin_agent_team_board ADD COLUMN jira_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE plugin_agent_team_board ADD COLUMN jira_base_url VARCHAR(512);
ALTER TABLE plugin_agent_team_board ADD COLUMN jira_email VARCHAR(320);
ALTER TABLE plugin_agent_team_board ADD COLUMN jira_api_token_enc TEXT;
ALTER TABLE plugin_agent_team_board ADD COLUMN jira_project_key VARCHAR(64);
ALTER TABLE plugin_agent_team_board ADD COLUMN jira_mappings_json TEXT NOT NULL DEFAULT '{}';
