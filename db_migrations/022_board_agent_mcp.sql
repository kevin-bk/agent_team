-- migrate: skip_if_table_missing plugin_agent_team_board
-- migrate: skip_if_column_exists plugin_agent_team_board agent_mcp_json

-- Per-agent MCP config for direct-CLI agents on a board. JSON object mapping a
-- cli:<engine> alias to its own {"mcpServers": {...}} config, so each CLI agent
-- can connect to a different set of MCP servers. Stored as text for portability
-- across SQLite and PostgreSQL.
ALTER TABLE plugin_agent_team_board ADD COLUMN agent_mcp_json TEXT NOT NULL DEFAULT '{}';
