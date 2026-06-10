-- migrate: skip_if_table_missing plugin_agent_team_comment
-- migrate: skip_if_column_exists plugin_agent_team_comment jira_comment_id

ALTER TABLE plugin_agent_team_comment ADD COLUMN external_author VARCHAR(255);
ALTER TABLE plugin_agent_team_comment ADD COLUMN jira_comment_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_plugin_agent_team_comment_jira_comment_id
    ON plugin_agent_team_comment (jira_comment_id);
