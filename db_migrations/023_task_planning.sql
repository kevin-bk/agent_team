-- migrate: skip_if_table_missing plugin_agent_team_task
-- migrate: skip_if_column_exists plugin_agent_team_task planning_mode

-- Strict planning columns on a task. planning_mode selects the lightweight
-- legacy planner or the contract-driven strict planner that requires human
-- approval. planning_meta_json holds backend-owned approval metadata (approved
-- flag/by/at, approved artifact etags, last reviewer verdict, last error).
ALTER TABLE plugin_agent_team_task
    ADD COLUMN planning_mode VARCHAR(16) NOT NULL DEFAULT 'legacy_plan';

ALTER TABLE plugin_agent_team_task
    ADD COLUMN planning_meta_json TEXT NOT NULL DEFAULT '{}';
