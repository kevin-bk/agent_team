-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task agent_assignee

-- Agent ownership + autopilot back-off bookkeeping on tasks. The autopilot
-- config table (plugin_agent_team_autopilot) is a brand-new table created by
-- Base.metadata.create(checkfirst=True) on startup, so it needs no migration.
ALTER TABLE plugin_agent_team_task ADD COLUMN agent_assignee VARCHAR(255);
ALTER TABLE plugin_agent_team_task ADD COLUMN autopilot_resume_after TIMESTAMP;
ALTER TABLE plugin_agent_team_task ADD COLUMN autopilot_attempts INTEGER NOT NULL DEFAULT 0;
