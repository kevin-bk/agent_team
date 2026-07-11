-- migrate: skip_if_table_missing plugin_agent_team_run

-- A failed turn must be handed to at most one successor. Both SQLite and
-- PostgreSQL allow multiple NULLs in a unique index, so normal runs are free.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_team_run_recovery_source
    ON plugin_agent_team_run (recovery_source_run_id);
