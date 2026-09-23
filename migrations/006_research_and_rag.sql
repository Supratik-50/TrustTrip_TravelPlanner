-- Migration: 006_research_and_rag.sql
-- Description: Phase 8 Global Flight Research, RAG Ingestion, Sources, Evidence, and Citations.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'research_intent_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE research_intent_enum AS ENUM (
            'GLOBAL_FLIGHT_RESEARCH', 'DESTINATION_RESEARCH', 'AIRPORT_RESEARCH',
            'AIRLINE_RESEARCH', 'ROUTE_RESEARCH', 'POLICY_RESEARCH',
            'TRAVEL_ADVISORY_RESEARCH', 'DOCUMENT_RAG_RESEARCH',
            'COMBINED_WEB_RAG_RESEARCH', 'LIVE_FLIGHT_SEARCH', 'AMBIGUOUS_RESEARCH'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'research_source_type_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE research_source_type_enum AS ENUM (
            'WEB_PAGE', 'OFFICIAL_PORTAL', 'AIRLINE_POLICY',
            'GOVERNMENT_ADVISORY', 'UPLOADED_DOCUMENT', 'LOCAL_RAG'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'source_trust_class_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE source_trust_class_enum AS ENUM (
            'OFFICIAL', 'TRUSTED_SECONDARY', 'USER_PROVIDED', 'UNKNOWN'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'research_evidence_status_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE research_evidence_status_enum AS ENUM (
            'complete', 'partial', 'conflicted', 'not_found'
        );
    END IF;
END$$;

-- Ensure trips table has unique constraint on (id, user_id) for composite foreign key references
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_trips_id_user'
    ) THEN
        ALTER TABLE trips ADD CONSTRAINT uq_trips_id_user UNIQUE (id, user_id);
    END IF;
END$$;

-- 1. Research Sessions
CREATE TABLE IF NOT EXISTS research_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trip_id UUID,
    mode TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_research_sessions_id_user UNIQUE (id, user_id),
    CONSTRAINT fk_research_sessions_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_research_sessions_user ON research_sessions (user_id, created_at DESC);

-- 2. Research Queries
CREATE TABLE IF NOT EXISTS research_queries (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trip_id UUID,
    revision_number INT,
    query_text TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    intent TEXT NOT NULL,
    mode TEXT NOT NULL,
    locale TEXT NOT NULL DEFAULT 'en-US',
    max_sources INT NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_research_queries_id_user UNIQUE (id, user_id),
    CONSTRAINT fk_research_queries_session_user FOREIGN KEY (session_id, user_id)
        REFERENCES research_sessions(id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_research_queries_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE,
    CONSTRAINT chk_research_queries_max_sources CHECK (max_sources >= 1 AND max_sources <= 50)
);
CREATE INDEX IF NOT EXISTS idx_research_queries_user ON research_queries (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_queries_session ON research_queries (session_id);

-- 3. Research Sources
CREATE TABLE IF NOT EXISTS research_sources (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_type TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_research_sources_id_user UNIQUE (id, user_id),
    CONSTRAINT uq_research_sources_user_url_hash UNIQUE (user_id, canonical_url, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_research_sources_user ON research_sources (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_sources_canonical ON research_sources (canonical_url);

-- 4. Research Evidence
CREATE TABLE IF NOT EXISTS research_evidence (
    id UUID PRIMARY KEY,
    query_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_id UUID NOT NULL,
    trip_id UUID,
    revision_id UUID,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    claims JSONB NOT NULL DEFAULT '[]'::jsonb,
    snippet TEXT NOT NULL,
    locator TEXT NOT NULL DEFAULT '',
    relevance_score NUMERIC(5, 4) NOT NULL DEFAULT 0.0,
    freshness_state TEXT NOT NULL DEFAULT 'FRESH',
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    immutable_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_research_evidence_id_user UNIQUE (id, user_id),
    CONSTRAINT chk_research_evidence_trip_revision CHECK (trip_id IS NULL OR revision_id IS NOT NULL),
    CONSTRAINT chk_research_evidence_relevance CHECK (relevance_score >= 0.0 AND relevance_score <= 1.0),
    CONSTRAINT fk_research_evidence_query_user FOREIGN KEY (query_id, user_id)
        REFERENCES research_queries(id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_research_evidence_source_user FOREIGN KEY (source_id, user_id)
        REFERENCES research_sources(id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_research_evidence_revision_composite FOREIGN KEY (revision_id, trip_id, user_id)
        REFERENCES plan_revisions(revision_id, trip_id, user_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_research_evidence_query ON research_evidence (query_id);
CREATE INDEX IF NOT EXISTS idx_research_evidence_user ON research_evidence (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_evidence_source ON research_evidence (source_id);

-- Enforce cross-table trip/revision/tenant consistency
CREATE OR REPLACE FUNCTION fn_validate_research_evidence_trip_revision_tenant()
RETURNS TRIGGER AS $$
DECLARE
    v_trip_user_id UUID;
    v_rev_trip_id UUID;
    v_rev_user_id UUID;
BEGIN
    -- If trip_id is present, revision_id is mandatory
    IF NEW.trip_id IS NOT NULL AND NEW.revision_id IS NULL THEN
        RAISE EXCEPTION 'Trip-linked research_evidence requires a valid revision_id'
            USING ERRCODE = 'check_violation';
    END IF;

    -- If revision_id is present, verify it belongs to trip_id and user_id
    IF NEW.revision_id IS NOT NULL THEN
        IF NEW.trip_id IS NULL THEN
            RAISE EXCEPTION 'Revision-linked research_evidence requires trip_id'
                USING ERRCODE = 'check_violation';
        END IF;

        SELECT trip_id, user_id INTO v_rev_trip_id, v_rev_user_id
        FROM plan_revisions
        WHERE revision_id = NEW.revision_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Referenced revision_id % does not exist in plan_revisions', NEW.revision_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;

        IF v_rev_trip_id <> NEW.trip_id THEN
            RAISE EXCEPTION 'research_evidence revision_id % belongs to trip % not trip %',
                NEW.revision_id, v_rev_trip_id, NEW.trip_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF v_rev_user_id <> NEW.user_id THEN
            RAISE EXCEPTION 'research_evidence tenant user_id % does not match revision user_id %',
                NEW.user_id, v_rev_user_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    -- If trip_id is present, check trip ownership
    IF NEW.trip_id IS NOT NULL THEN
        SELECT user_id INTO v_trip_user_id
        FROM trips
        WHERE id = NEW.trip_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Referenced trip_id % does not exist in trips', NEW.trip_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;

        IF v_trip_user_id <> NEW.user_id THEN
            RAISE EXCEPTION 'research_evidence tenant user_id % does not match trip user_id %',
                NEW.user_id, v_trip_user_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_validate_research_evidence_trip_revision ON research_evidence;
CREATE TRIGGER trg_validate_research_evidence_trip_revision
    BEFORE INSERT ON research_evidence
    FOR EACH ROW EXECUTE FUNCTION fn_validate_research_evidence_trip_revision_tenant();

-- 5. Research Citations
CREATE TABLE IF NOT EXISTS research_citations (
    id UUID PRIMARY KEY,
    query_id UUID NOT NULL,
    evidence_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    citation_token TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    locator TEXT NOT NULL DEFAULT '',
    cited_claims JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_research_citations_id_user UNIQUE (id, user_id),
    CONSTRAINT fk_research_citations_query_user FOREIGN KEY (query_id, user_id)
        REFERENCES research_queries(id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_research_citations_evidence_user FOREIGN KEY (evidence_id, user_id)
        REFERENCES research_evidence(id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_research_citations_query ON research_citations (query_id);
CREATE INDEX IF NOT EXISTS idx_research_citations_evidence ON research_citations (evidence_id);

-- 6. RAG Documents
CREATE TABLE IF NOT EXISTS rag_documents (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trip_id UUID,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_size_bytes INT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rag_documents_id_user UNIQUE (id, user_id),
    CONSTRAINT fk_rag_documents_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE,
    CONSTRAINT chk_rag_docs_size CHECK (file_size_bytes >= 0 AND file_size_bytes <= 10485760),
    CONSTRAINT chk_rag_docs_chunks CHECK (chunk_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_rag_documents_user ON rag_documents (user_id, created_at DESC);

-- 7. RAG Chunks (with pgvector embedding storage)
CREATE TABLE IF NOT EXISTS rag_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text_content TEXT NOT NULL,
    token_count INT NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_rag_chunks_document_user FOREIGN KEY (document_id, user_id)
        REFERENCES rag_documents(id, user_id) ON DELETE CASCADE,
    CONSTRAINT chk_rag_chunks_token_count CHECK (token_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks (document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_user ON rag_chunks (user_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- 8. Complete Immutability protections (Blocker 8)
CREATE OR REPLACE FUNCTION prevent_research_evidence_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'research_evidence records are immutable and cannot be updated';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'research_evidence records are append-only and cannot be manually deleted';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_research_evidence_immutable ON research_evidence;
CREATE TRIGGER trg_research_evidence_immutable
    BEFORE UPDATE OR DELETE ON research_evidence
    FOR EACH ROW EXECUTE FUNCTION prevent_research_evidence_mutation();

CREATE OR REPLACE FUNCTION prevent_research_citations_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'research_citations records are immutable and cannot be updated';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'research_citations records are append-only and cannot be manually deleted';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_research_citations_immutable ON research_citations;
CREATE TRIGGER trg_research_citations_immutable
    BEFORE UPDATE OR DELETE ON research_citations
    FOR EACH ROW EXECUTE FUNCTION prevent_research_citations_mutation();
