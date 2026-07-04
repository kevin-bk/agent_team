-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task sandbox_id

-- Persisted OpenSandbox id of the task's isolated sandbox. Written when the
-- sandbox opens, cleared on kill/close. Lets the app REATTACH to the same
-- (usually paused) sandbox after a process restart via resume_existing()
-- instead of orphaning it and spawning a fresh one. Stale ids self-heal: if
-- the server no longer knows the sandbox, reattach fails and a new sandbox
-- overwrites the column.
ALTER TABLE plugin_agent_team_task ADD COLUMN sandbox_id VARCHAR(64);
