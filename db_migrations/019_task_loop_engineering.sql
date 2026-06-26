-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task objective

-- Autonomous loop layer: objective + execution mode on tasks, and loop bookkeeping
-- on runs. The attempt/evaluation tables are brand-new and created by
-- Base.metadata.create(checkfirst=True) on startup, so they need no migration.
ALTER TABLE plugin_agent_team_task ADD COLUMN objective TEXT;
ALTER TABLE plugin_agent_team_task ADD COLUMN execution_mode VARCHAR(16) NOT NULL DEFAULT 'chat';
