-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board planning_skill

-- Skill pack that owns the SPEC/PLAN structure guidance ("harness") for this
-- board's strict planning. Empty = the bundled default (project-harness). The
-- chosen pack is materialised into task workspaces alongside the board's other
-- skills, and the planner prompt points at it instead of the hardcoded default,
-- so a team can ship planning best-practices as their own skill pack.
ALTER TABLE plugin_agent_team_board ADD COLUMN planning_skill VARCHAR(128) NOT NULL DEFAULT '';
