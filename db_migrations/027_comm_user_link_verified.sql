-- migrate: skip_if_table_missing plugin_agent_team_comm_user_link
-- migrate: skip_if_column_exists plugin_agent_team_comm_user_link verified
-- Inbound authorization gate (v2): only a verified user/provider mapping may act
-- on a task from chat. Existing auto-matched-by-email links are backfilled to
-- verified=TRUE because an email match already proves identity on most servers.
-- Manual admin overrides are left untrusted until explicitly confirmed.
ALTER TABLE plugin_agent_team_comm_user_link
    ADD COLUMN verified BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE plugin_agent_team_comm_user_link SET verified = TRUE WHERE source = 'auto';
