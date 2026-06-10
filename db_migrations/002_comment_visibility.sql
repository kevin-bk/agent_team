-- migrate: skip_if_table_missing plugin_agent_team_comment
-- migrate: skip_if_column_exists plugin_agent_team_comment visible_to_agents

ALTER TABLE plugin_agent_team_comment ADD COLUMN visible_to_agents BOOLEAN NOT NULL DEFAULT TRUE;
