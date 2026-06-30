-- migrate: skip_if_table_missing plugin_agent_team_board_member
-- migrate: skip_if_column_exists plugin_agent_team_board_member jira_account_id

-- Jira accountId for this member, resolved once via Jira user-search on the
-- member's email and cached here. Lets sync map an issue's assignee/reporter by
-- stable accountId even when Jira hides the account's email (privacy/GDPR) on
-- issue payloads — which previously meant only the owner (whose own email is
-- always visible to the syncing credential) could be mapped.
ALTER TABLE plugin_agent_team_board_member ADD COLUMN jira_account_id VARCHAR(128);
