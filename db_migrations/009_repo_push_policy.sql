-- migrate: skip_if_table_missing plugin_agent_team_repo
-- migrate: skip_if_column_exists plugin_agent_team_repo allow_push

ALTER TABLE plugin_agent_team_repo ADD COLUMN allow_push BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE plugin_agent_team_repo ADD COLUMN committer_name VARCHAR(255);
ALTER TABLE plugin_agent_team_repo ADD COLUMN committer_email VARCHAR(320);
