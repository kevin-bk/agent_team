-- Communication Gateway (v2: inbound actions).
--
-- Adds the tables the inbound slice needs so a human can act on a task from
-- chat (approve a plan, answer questions, acknowledge completion, leave a note)
-- without opening the web cockpit. All resolution happens purely via the DB so
-- it works across worker processes:
--
--   * external_thread  — maps a provider thread root (Mattermost root_id /
--                        Slack thread_ts) back to the originating task.
--   * action_request   — the outstanding "human must act" request behind an
--                        actionable notification; an inbound reply resolves it.
--   * inbound_message  — raw inbound provider events, stored before
--                        interpretation for debugging/replay.
--
-- All tables use IF NOT EXISTS so they are a no-op when the ORM has already
-- auto-created them via Base.metadata on startup.

CREATE TABLE IF NOT EXISTS plugin_agent_team_comm_external_thread (
    id VARCHAR(32) PRIMARY KEY,
    connection_id VARCHAR(32) NOT NULL
        REFERENCES plugin_agent_team_comm_connection(id) ON DELETE CASCADE,
    provider VARCHAR(24) NOT NULL DEFAULT 'mattermost',
    channel_id VARCHAR(64) NOT NULL DEFAULT '',
    provider_thread_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(32) REFERENCES plugin_agent_team_task(id) ON DELETE CASCADE,
    board_id VARCHAR(32),
    created_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_comm_ext_thread_connection
    ON plugin_agent_team_comm_external_thread (connection_id);
CREATE INDEX IF NOT EXISTS ix_agent_team_comm_ext_thread_thread
    ON plugin_agent_team_comm_external_thread (provider_thread_id);
CREATE INDEX IF NOT EXISTS ix_agent_team_comm_ext_thread_task
    ON plugin_agent_team_comm_external_thread (task_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_team_comm_ext_thread
    ON plugin_agent_team_comm_external_thread (connection_id, provider_thread_id);

CREATE TABLE IF NOT EXISTS plugin_agent_team_comm_action_request (
    id VARCHAR(32) PRIMARY KEY,
    task_id VARCHAR(32) NOT NULL
        REFERENCES plugin_agent_team_task(id) ON DELETE CASCADE,
    board_id VARCHAR(32),
    connection_id VARCHAR(32)
        REFERENCES plugin_agent_team_comm_connection(id) ON DELETE SET NULL,
    channel_id VARCHAR(64),
    provider_thread_id VARCHAR(64),
    provider VARCHAR(24) NOT NULL DEFAULT 'mattermost',
    event_type VARCHAR(32) NOT NULL DEFAULT '',
    -- JSON array of action kinds this request permits (subset of INBOUND_ACTIONS).
    allowed_actions_json TEXT NOT NULL DEFAULT '[]',
    -- JSON object of extra context (e.g. open question ids) for interpretation.
    payload_json TEXT NOT NULL DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'open',
    resolved_by VARCHAR(36),
    resolved_action VARCHAR(32),
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_comm_action_request_task
    ON plugin_agent_team_comm_action_request (task_id);
CREATE INDEX IF NOT EXISTS ix_agent_team_comm_action_request_thread
    ON plugin_agent_team_comm_action_request (provider_thread_id);

CREATE TABLE IF NOT EXISTS plugin_agent_team_comm_inbound_message (
    id VARCHAR(32) PRIMARY KEY,
    connection_id VARCHAR(32)
        REFERENCES plugin_agent_team_comm_connection(id) ON DELETE SET NULL,
    provider VARCHAR(24) NOT NULL DEFAULT 'mattermost',
    channel_id VARCHAR(64),
    provider_user_id VARCHAR(64),
    provider_message_id VARCHAR(64),
    provider_thread_id VARCHAR(64),
    text TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    action_request_id VARCHAR(32),
    status VARCHAR(16) NOT NULL DEFAULT 'received',
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE,
    processed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_comm_inbound_connection
    ON plugin_agent_team_comm_inbound_message (connection_id);
CREATE INDEX IF NOT EXISTS ix_agent_team_comm_inbound_thread
    ON plugin_agent_team_comm_inbound_message (provider_thread_id);
