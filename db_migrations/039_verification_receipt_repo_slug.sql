-- migrate: skip_if_table_missing plugin_agent_team_verification_receipt
-- migrate: skip_if_column_exists plugin_agent_team_verification_receipt repo_slug

ALTER TABLE plugin_agent_team_verification_receipt ADD COLUMN repo_slug VARCHAR(64);
