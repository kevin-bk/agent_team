-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board starter_prompt

ALTER TABLE plugin_agent_team_board ADD COLUMN starter_prompt TEXT NOT NULL DEFAULT '';
