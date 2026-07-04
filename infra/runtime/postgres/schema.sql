-- First-boot init for the app Postgres (pgvector/pgvector:pg16).
--
-- Runs from /docker-entrypoint-initdb.d ONLY when the data volume is EMPTY.
-- When you reuse an existing volume (the migration case: keeping the old
-- deep-agent data), Postgres skips this file entirely — extensions/roles/
-- databases already live in the volume. It exists so a fresh volume comes up
-- correctly too. App TABLES are owned by the app's own migrations, not this file.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";

-- UTC on whatever database this init runs against (name from POSTGRES_DB).
DO $$
BEGIN
  EXECUTE format('ALTER DATABASE %I SET timezone TO %L', current_database(), 'UTC');
END
$$;

SELECT 'Agent Manager PostgreSQL database initialized successfully!' AS message;
