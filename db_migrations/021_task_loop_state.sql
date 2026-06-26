-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task loop_state

-- Persisted lifecycle state of a task's autonomous loop (running / complete /
-- waiting_for_human / failed / cancelled). Null for plain chat tasks.
ALTER TABLE plugin_agent_team_task ADD COLUMN loop_state VARCHAR(24);
