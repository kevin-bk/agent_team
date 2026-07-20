-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board planning_review_max_redrafts

-- Bounded, reviewer-driven plan correction. Zero preserves the historical
-- behaviour: a failed review parks the plan for a human instead of re-drafting.
ALTER TABLE plugin_agent_team_board ADD COLUMN planning_review_max_redrafts INTEGER NOT NULL DEFAULT 0;
