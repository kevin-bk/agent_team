-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board jira_api_token

-- Repairs environments where the interim 004 migration created the column as
-- ``jira_api_token_enc`` (before the token storage was simplified to plaintext,
-- matching the core LLM-provider convention). Fresh installs already have
-- ``jira_api_token`` from the model, so the directive above skips this there.
ALTER TABLE plugin_agent_team_board RENAME COLUMN jira_api_token_enc TO jira_api_token;
