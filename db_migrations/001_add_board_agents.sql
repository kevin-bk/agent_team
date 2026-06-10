-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board agents_json

ALTER TABLE plugin_agent_team_board ADD COLUMN agents_json TEXT NOT NULL DEFAULT '[]';
