-- migrate: skip_if_table_missing plugin_agent_team_verification_receipt
-- migrate: skip_if_column_exists plugin_agent_team_verification_receipt working_directory

ALTER TABLE plugin_agent_team_verification_receipt ADD COLUMN working_directory TEXT NOT NULL DEFAULT '.';
