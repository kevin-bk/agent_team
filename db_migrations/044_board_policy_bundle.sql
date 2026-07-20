-- migrate: skip_if_column_exists plugin_agent_team_board policy_bundle_id
ALTER TABLE plugin_agent_team_board ADD COLUMN policy_bundle_id VARCHAR(32);
CREATE INDEX IF NOT EXISTS ix_at_board_policy_bundle
    ON plugin_agent_team_board(policy_bundle_id);
