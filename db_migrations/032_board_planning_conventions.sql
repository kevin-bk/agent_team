-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board planning_conventions

-- Per-board planning conventions: free-text house rules a team writes once and
-- the backend injects into every strict-planning prompt (PLAN / REVIEW /
-- IMPLEMENT / VERIFY). Lets each board shape HOW SPEC.md / PLAN.md / code are
-- written (their own best practices, section styles, review bars) without
-- forking the backend prompts. Governs artifact CONTENT/STRUCTURE only — the
-- artifact paths, JSON schemas and lifecycle stay backend-owned.
ALTER TABLE plugin_agent_team_board ADD COLUMN planning_conventions TEXT NOT NULL DEFAULT '';
