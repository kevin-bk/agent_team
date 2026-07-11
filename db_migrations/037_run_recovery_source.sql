-- migrate: skip_if_table_missing plugin_agent_team_run
-- migrate: skip_if_column_exists plugin_agent_team_run recovery_source_run_id

-- The interrupted planner/generator run recovered by this successor.
ALTER TABLE plugin_agent_team_run ADD COLUMN recovery_source_run_id VARCHAR(32);
