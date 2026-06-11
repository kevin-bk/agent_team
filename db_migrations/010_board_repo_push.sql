-- migrate: skip_if_table_missing plugin_agent_team_board_repo
-- migrate: skip_if_column_exists plugin_agent_team_board_repo allow_push

ALTER TABLE plugin_agent_team_board_repo ADD COLUMN allow_push BOOLEAN NOT NULL DEFAULT FALSE;
