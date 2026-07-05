-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board planning_auto_approve_quick

-- Lane-aware planning: when the planner's risk intake (.agent-team/INTAKE.json)
-- classifies a task into the "quick" lane (0-1 risk flags, no hard gates) and
-- this board opts in, the backend auto-approves the drafted plan instead of
-- parking it for a human. Only ever applies to the quick lane on the FIRST
-- draft (never after a human requested changes or answered questions); normal
-- and risk lanes always require human approval. Default OFF - human approval
-- stays the rule unless a board explicitly opts in.
ALTER TABLE plugin_agent_team_board ADD COLUMN planning_auto_approve_quick BOOLEAN NOT NULL DEFAULT FALSE;
