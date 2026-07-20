-- migrate: skip_if_column_exists plugin_agent_team_board planning_max_tasks
ALTER TABLE plugin_agent_team_board ADD COLUMN planning_max_tasks INTEGER NOT NULL DEFAULT 25;
