-- Communication Gateway (v1: outbound notifications only).
--
-- Repo-style N-N model (mirrors features/repos): an owner-scoped CONNECTION holds
-- the provider credential (bot token) and is reused across boards; a board↔
-- connection LINK (board_channel) picks the destination channel + per-board
-- routing; each send is a DELIVERY; user↔provider mapping (for @mentions) lives
-- in a connection-scoped USER_LINK.
--
-- All tables use IF NOT EXISTS so they are a no-op when the ORM has already
-- auto-created them via Base.metadata on startup. The bot token is stored plain
-- (same convention as plugin_agent_team_board.jira_api_token) and is never
-- returned by the API — only its presence is exposed.

CREATE TABLE IF NOT EXISTS plugin_agent_team_comm_connection (
    id VARCHAR(32) PRIMARY KEY,
    owner_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    provider VARCHAR(24) NOT NULL DEFAULT 'mattermost',
    name VARCHAR(255) NOT NULL,
    server_url VARCHAR(1024) NOT NULL DEFAULT '',
    bot_token TEXT,
    default_team_id VARCHAR(64),
    -- Optional public base URL of this platform, used to build task deep links
    -- in outbound messages (e.g. https://agent.example.com).
    deep_link_base VARCHAR(1024),
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_comm_connection_owner
    ON plugin_agent_team_comm_connection (owner_id);

CREATE TABLE IF NOT EXISTS plugin_agent_team_board_channel (
    id VARCHAR(32) PRIMARY KEY,
    board_id VARCHAR(32) NOT NULL
        REFERENCES plugin_agent_team_board(id) ON DELETE CASCADE,
    connection_id VARCHAR(32) NOT NULL
        REFERENCES plugin_agent_team_comm_connection(id) ON DELETE CASCADE,
    channel_id VARCHAR(64) NOT NULL DEFAULT '',
    channel_name VARCHAR(255) NOT NULL DEFAULT '',
    use_threads BOOLEAN NOT NULL DEFAULT TRUE,
    -- JSON array of event types this channel should receive (empty = none).
    event_allowlist_json TEXT NOT NULL DEFAULT '[]',
    -- none | assignee | creator — who to @mention on notifications.
    tag_mode VARCHAR(16) NOT NULL DEFAULT 'assignee',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_board_channel_board
    ON plugin_agent_team_board_channel (board_id);
CREATE INDEX IF NOT EXISTS ix_agent_team_board_channel_connection
    ON plugin_agent_team_board_channel (connection_id);

CREATE TABLE IF NOT EXISTS plugin_agent_team_comm_delivery (
    id VARCHAR(32) PRIMARY KEY,
    task_id VARCHAR(32) REFERENCES plugin_agent_team_task(id) ON DELETE CASCADE,
    board_id VARCHAR(32),
    channel_id VARCHAR(32)
        REFERENCES plugin_agent_team_board_channel(id) ON DELETE SET NULL,
    event_type VARCHAR(32) NOT NULL DEFAULT '',
    provider VARCHAR(24) NOT NULL DEFAULT 'mattermost',
    provider_message_id VARCHAR(64),
    provider_thread_id VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    dedupe_key VARCHAR(255),
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE,
    sent_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_comm_delivery_task
    ON plugin_agent_team_comm_delivery (task_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_team_comm_delivery_dedupe
    ON plugin_agent_team_comm_delivery (dedupe_key);

CREATE TABLE IF NOT EXISTS plugin_agent_team_comm_user_link (
    id VARCHAR(32) PRIMARY KEY,
    connection_id VARCHAR(32) NOT NULL
        REFERENCES plugin_agent_team_comm_connection(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mm_user_id VARCHAR(64),
    mm_username VARCHAR(255),
    -- auto (matched by email) | manual (admin override).
    source VARCHAR(16) NOT NULL DEFAULT 'auto',
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_team_comm_user_link
    ON plugin_agent_team_comm_user_link (connection_id, user_id);
