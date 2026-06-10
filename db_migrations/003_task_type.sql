-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task task_type

ALTER TABLE plugin_agent_team_task ADD COLUMN task_type VARCHAR(32) NOT NULL DEFAULT 'task';
