-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board skills_json

ALTER TABLE plugin_agent_team_board ADD COLUMN skills_json TEXT NOT NULL DEFAULT '[]';
