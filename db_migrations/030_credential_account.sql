-- Credential-account registry for the isolated OpenSandbox runtime.
-- Each row is one provider identity (Claude subscription, Codex ChatGPT account,
-- future GitHub/GitLab/API secret). It stores only a REFERENCE to the secret
-- material (host env-var name or host path) in material_ref_json — never the
-- secret itself. The ORM auto-creates this table on startup
-- (Base.metadata.create(checkfirst=True)); this migration is the idempotent
-- safety net (and creates it on Postgres deployments).
CREATE TABLE IF NOT EXISTS plugin_agent_team_credential_account (
    id                VARCHAR(32)  PRIMARY KEY,
    name              VARCHAR(100) NOT NULL UNIQUE,
    description       TEXT         NOT NULL DEFAULT '',
    provider          VARCHAR(40)  NOT NULL,
    backend           VARCHAR(20)  NOT NULL DEFAULT '',
    material_ref_json TEXT         NOT NULL DEFAULT '{}',
    enabled           BOOLEAN      NOT NULL DEFAULT TRUE,
    weight            INTEGER      NOT NULL DEFAULT 1,
    max_concurrency   INTEGER      NOT NULL DEFAULT 1,
    created_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
