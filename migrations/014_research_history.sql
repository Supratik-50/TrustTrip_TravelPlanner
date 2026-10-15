-- Migration: 014_research_history.sql
-- Description: Persistent Research History for TrustTrip TravelPlanner Notebook

CREATE TABLE IF NOT EXISTS research_history (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    query_id UUID NOT NULL,
    trip_id UUID,
    query_text TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto',
    intent TEXT,
    answer TEXT NOT NULL,
    evidence_status TEXT NOT NULL DEFAULT 'complete',
    evidence_count INT NOT NULL DEFAULT 0,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_research_history_id_user UNIQUE (id, user_id),
    CONSTRAINT uq_research_history_query_user UNIQUE (query_id, user_id),
    CONSTRAINT fk_research_history_trip FOREIGN KEY (trip_id)
        REFERENCES trips(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_research_history_user ON research_history (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_history_session ON research_history (session_id);
CREATE INDEX IF NOT EXISTS idx_research_history_query ON research_history (query_id);
CREATE INDEX IF NOT EXISTS idx_research_history_trip ON research_history (trip_id);
