-- Task Journal: an append-only semantic timeline of important decisions,
-- assumptions, questions, approvals, plan changes, and verification outcomes
-- for a task. Distinct from raw run events/transcripts (replay), EVIDENCE.json
-- (verification record) and task.loop_state (lifecycle): the journal is a
-- curated, durable record of *why* a task went the way it did. Written mostly
-- by the backend at lifecycle points so it stays complete regardless of any
-- agent's context compaction.
--
-- Created with IF NOT EXISTS so it is a no-op when the ORM has already
-- auto-created the table via Base.metadata on startup.
CREATE TABLE IF NOT EXISTS plugin_agent_team_journal_entry (
    id VARCHAR(32) PRIMARY KEY,
    task_id VARCHAR(32) NOT NULL REFERENCES plugin_agent_team_task(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 0,
    actor_type VARCHAR(16) NOT NULL DEFAULT 'system',
    actor_id VARCHAR(64),
    phase VARCHAR(24) NOT NULL DEFAULT 'system',
    type VARCHAR(24) NOT NULL DEFAULT 'note',
    title VARCHAR(200) NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    severity VARCHAR(16) NOT NULL DEFAULT 'info',
    refs_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    supersedes_id VARCHAR(32),
    created_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_agent_team_journal_task_seq
    ON plugin_agent_team_journal_entry (task_id, seq);
