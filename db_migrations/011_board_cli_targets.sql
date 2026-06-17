-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board cli_targets_json

ALTER TABLE plugin_agent_team_board ADD COLUMN cli_targets_json TEXT NOT NULL DEFAULT '[]';
