-- migrate: skip_if_table_missing plugin_agent_team_board_repo
-- migrate: skip_if_column_exists plugin_agent_team_board_repo is_wiki

ALTER TABLE plugin_agent_team_board_repo ADD COLUMN is_wiki BOOLEAN NOT NULL DEFAULT FALSE;
