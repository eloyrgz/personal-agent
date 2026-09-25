-- Schema for personal-agent's own persisted memory in Supabase.
--
-- This repo does not run migrations automatically (same as
-- training-coach-agent): run this script once against the Supabase
-- Postgres project referenced by SUPABASE_DB_URI / SUPABASE_POOLER_DB_URI,
-- e.g. via the Supabase SQL editor or `psql "$SUPABASE_DB_URI" -f sql/personal_agent_schema.sql`.

CREATE EXTENSION IF NOT EXISTS vector;

-- Turn-by-turn conversation log, across all routes (general/training/home),
-- keyed by the router's conversation_id. Used to reconstruct recent history
-- for the general (OpenAI) route and as a durable audit trail.
CREATE TABLE IF NOT EXISTS conversation_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    route           TEXT NOT NULL DEFAULT 'general',
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversation_messages_conversation_id_created_at_idx
    ON conversation_messages (conversation_id, created_at);

-- Long-term semantic memory: durable facts about the user, extracted from
-- general-route turns, embedded with the same all-MiniLM-L6-v2 model used
-- by training-coach-agent (384 dimensions) so they can be recalled via
-- cosine-similarity search (PgVectorMemory.semantic_search).
CREATE TABLE IF NOT EXISTS agent_memories (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT,
    content         TEXT NOT NULL,
    embedding       vector(384),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Approximate nearest-neighbor index; fine to add once there's enough data.
-- Skipped by default since agent_memories is expected to stay small.
-- CREATE INDEX IF NOT EXISTS agent_memories_embedding_idx
--     ON agent_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
