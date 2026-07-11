-- migrate: skip_if_table_missing plugin_agent_team_run
-- migrate: skip_if_column_exists plugin_agent_team_run workspace_snapshot_json

-- Non-invasive pre-turn checkpoint: Git heads plus dirty/untracked signatures,
-- not file blobs or hidden commits.
ALTER TABLE plugin_agent_team_run ADD COLUMN workspace_snapshot_json TEXT;
