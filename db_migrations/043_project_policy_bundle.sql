CREATE TABLE IF NOT EXISTS plugin_agent_team_project_policy_bundle (
    id VARCHAR(32) PRIMARY KEY,
    project_key VARCHAR(128) NOT NULL,
    schema_version INTEGER NOT NULL,
    source_ref VARCHAR(255) NOT NULL,
    documents_json TEXT NOT NULL,
    file_hashes_json TEXT NOT NULL,
    bundle_sha256 VARCHAR(64) NOT NULL,
    created_by VARCHAR(36),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_at_policy_key_sha UNIQUE (project_key, bundle_sha256)
);
CREATE INDEX IF NOT EXISTS ix_at_policy_project_key
    ON plugin_agent_team_project_policy_bundle(project_key);
CREATE INDEX IF NOT EXISTS ix_at_policy_bundle_sha
    ON plugin_agent_team_project_policy_bundle(bundle_sha256);
