"""Phase 8 PostgreSQL Live Integration Test Suite.

Verifies:
1. Migration 006 (006_research_and_rag.sql) applies cleanly in an isolated schema.
2. Enum types exist within current schema namespace (typnamespace = to_regnamespace(current_schema())).
3. Real PostgreSQL research_sessions, research_queries, research_sources, research_evidence, and research_citations persistence.
4. Real PostgreSQL rag_documents and rag_chunks persistence with cascade deletion.
5. Strict PostgreSQL tenant isolation: cross-tenant research sessions and document queries return empty.
6. User CASCADE deletion cleanly removes all research records and documents.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from dotenv import load_dotenv

load_dotenv(r"n:\Documents\GitHub\TrustTrip_TravelPlanner\.env")

DB_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def pg_schema():
    """Sets up an isolated schema on PostgreSQL with Phase 1-7 tables and Phase 8 Migration 006."""
    if not DB_URL:
        pytest.skip("DATABASE_URL not set; skipping live PostgreSQL integration tests")

    schema_name = f"p8_integ_{uuid4().hex[:8]}"
    conn = psycopg.connect(DB_URL, autocommit=True)

    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {schema_name};")
            cur.execute(f"SET search_path TO {schema_name}, public;")
            cur.execute("""
                CREATE TABLE users (
                    id UUID PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                );

                CREATE TABLE trips (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL DEFAULT '',
                    destination TEXT NOT NULL DEFAULT '',
                    origin TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planning',
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)

            # Load migration 004
            m004_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\004_travel_intelligence.sql"
            with open(m004_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            # Load migration 005
            m005_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\005_evidence_revision_lifecycle.sql"
            with open(m005_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            # Load migration 006
            m006_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\006_research_and_rag.sql"
            with open(m006_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

        yield schema_name

    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")
        except Exception:
            pass
        conn.close()


def test_postgres_migration_006_schema_objects(pg_schema):
    """Verify that all Phase 8 tables and enums exist in the isolated schema."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            # Verify tables exist
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
            """, (pg_schema,))
            tables = {r["table_name"] for r in cur.fetchall()}

            expected_tables = {
                "research_sessions",
                "research_queries",
                "research_sources",
                "research_evidence",
                "research_citations",
                "rag_documents",
                "rag_chunks",
            }
            assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

            # Verify enum types exist in isolated schema
            cur.execute("""
                SELECT typname
                FROM pg_type
                WHERE typnamespace = to_regnamespace(%s)
            """, (pg_schema,))
            enums = {r["typname"] for r in cur.fetchall()}

            expected_enums = {
                "research_intent_enum",
                "research_source_type_enum",
                "source_trust_class_enum",
                "research_evidence_status_enum",
            }
            assert expected_enums.issubset(enums), f"Missing enums: {expected_enums - enums}"
    finally:
        conn.close()


def test_postgres_rag_documents_and_chunks_cascade(pg_schema):
    """Verify RAG document and chunks creation, and ON DELETE CASCADE on chunks."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_id = uuid4()
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                        (user_id, f"test_{user_id.hex[:6]}@example.com", "hash"))

            doc_id = uuid4()
            now = datetime.now(timezone.utc)
            cur.execute("""
                INSERT INTO rag_documents (
                    id, user_id, title, filename, content_type,
                    file_size_bytes, content_hash, chunk_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                doc_id, user_id, "Visa Requirements", "visa.txt", "text/plain",
                1024, "hash123", 2, now
            ))

            chunk1_id = uuid4()
            chunk2_id = uuid4()
            cur.execute("""
                INSERT INTO rag_chunks (
                    id, document_id, user_id, chunk_index, text_content,
                    token_count, content_hash, created_at
                ) VALUES
                (%s, %s, %s, 0, 'Chunk 0 text content for visa rules.', 10, 'chash0', %s),
                (%s, %s, %s, 1, 'Chunk 1 text content for passport rules.', 10, 'chash1', %s)
            """, (chunk1_id, doc_id, user_id, now, chunk2_id, doc_id, user_id, now))

            # Verify chunks exist
            cur.execute("SELECT COUNT(*) AS c FROM rag_chunks WHERE document_id = %s", (doc_id,))
            assert cur.fetchone()["c"] == 2

            # Delete parent document
            cur.execute("DELETE FROM rag_documents WHERE id = %s", (doc_id,))

            # Verify cascade deletion of chunks
            cur.execute("SELECT COUNT(*) AS c FROM rag_chunks WHERE document_id = %s", (doc_id,))
            assert cur.fetchone()["c"] == 0, "rag_chunks must be cascade-deleted with parent document"
    finally:
        conn.close()


def test_postgres_research_tenant_isolation(pg_schema):
    """Verify strict tenant isolation on research sessions and documents."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_a = uuid4()
            user_b = uuid4()
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s), (%s, %s, %s)",
                        (user_a, f"a_{user_a.hex[:6]}@example.com", "hash",
                         user_b, f"b_{user_b.hex[:6]}@example.com", "hash"))

            session_a = uuid4()
            now = datetime.now(timezone.utc)
            from datetime import timedelta
            expires_at = now + timedelta(hours=2)
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, 'web', 'active', %s, %s)
            """, (session_a, user_a, now, expires_at))

            doc_a = uuid4()
            cur.execute("""
                INSERT INTO rag_documents (
                    id, user_id, title, filename, content_type,
                    file_size_bytes, content_hash, chunk_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (doc_a, user_a, "Private Doc A", "doc.pdf", "application/pdf", 500, "hash_a", 1, now))

            # Query under user_b -> MUST return 0 records
            cur.execute("SELECT * FROM research_sessions WHERE id = %s AND user_id = %s", (session_a, user_b))
            assert cur.fetchone() is None, "Cross-tenant research session access must be impossible"

            cur.execute("SELECT * FROM rag_documents WHERE id = %s AND user_id = %s", (doc_a, user_b))
            assert cur.fetchone() is None, "Cross-tenant RAG document access must be impossible"

            cur.execute("SELECT * FROM rag_documents WHERE user_id = %s", (user_b,))
            assert len(cur.fetchall()) == 0
    finally:
        conn.close()


def test_postgres_user_cascade_deletion(pg_schema):
    """Verify that deleting a user cascades and deletes all research sessions and documents."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_id = uuid4()
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                        (user_id, f"del_{user_id.hex[:6]}@example.com", "hash"))

            session_id = uuid4()
            now = datetime.now(timezone.utc)
            from datetime import timedelta
            expires_at = now + timedelta(hours=2)
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, 'hybrid', 'active', %s, %s)
            """, (session_id, user_id, now, expires_at))

            doc_id = uuid4()
            cur.execute("""
                INSERT INTO rag_documents (
                    id, user_id, title, filename, content_type,
                    file_size_bytes, content_hash, chunk_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (doc_id, user_id, "User Doc", "doc.txt", "text/plain", 100, "hash_u", 1, now))

            # Delete user
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))

            # Verify research sessions and rag documents are cleanly gone
            cur.execute("SELECT COUNT(*) AS c FROM research_sessions WHERE user_id = %s", (user_id,))
            assert cur.fetchone()["c"] == 0

            cur.execute("SELECT COUNT(*) AS c FROM rag_documents WHERE user_id = %s", (user_id,))
            assert cur.fetchone()["c"] == 0
    finally:
        conn.close()


def test_postgres_single_migration_authority():
    """Verify that migrations/006_research_and_rag.sql is the sole authoritative Phase 8 migration file."""
    m1 = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\006_research_and_rag.sql"
    m2 = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\versions\006_research_and_rag.sql"
    assert os.path.exists(m1), "Authoritative migration 006 must exist in migrations/"
    assert not os.path.exists(m2), "Duplicate migration 006 must NOT exist in migrations/versions/"


def test_postgres_vector_store_persistence_across_restart(pg_schema):
    """Verify that PostgresVectorStore persists embeddings, supports cosine similarity query,
    survives process restart (simulated by new instance), enforces tenant isolation, and validates 128 dimension."""
    from services.research.rag.vector_store import PostgresVectorStore
    from domain.research import RagChunk

    user_a = uuid4()
    user_b = uuid4()
    doc_id = uuid4()

    # Create user and document in DB
    conn = psycopg.connect(DB_URL, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s), (%s, %s, %s)",
                        (user_a, f"va_{user_a.hex[:6]}@example.com", "hash",
                         user_b, f"vb_{user_b.hex[:6]}@example.com", "hash"))
            now = datetime.now(timezone.utc)
            cur.execute("""
                INSERT INTO rag_documents (
                    id, user_id, title, filename, content_type,
                    file_size_bytes, content_hash, chunk_count, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (doc_id, user_a, "Passport Guide", "guide.txt", "text/plain", 500, "chash_vec", 1, now))
    finally:
        conn.close()

    # Step 1: Upsert using first instance
    store_1 = PostgresVectorStore(db_url=DB_URL, schema=pg_schema)

    # Validate 128 dimension enforcement
    invalid_emb = [0.1] * 64
    chunk = RagChunk(
        chunk_id=uuid4(),
        document_id=doc_id,
        user_id=user_a,
        chunk_index=0,
        text_content="Passports must have at least 6 months validity for international travel.",
        token_count=12,
        content_hash="hash_validity",
        metadata={"category": "passport"},
    )
    with pytest.raises(ValueError, match="Embedding dimension must be exactly 128"):
        store_1.upsert_chunks([chunk], [invalid_emb])

    # Valid 128-dimensional embedding
    valid_emb = [0.0] * 128
    valid_emb[0] = 1.0  # Unit vector on first axis
    count = store_1.upsert_chunks([chunk], [valid_emb])
    assert count == 1

    # Step 2: Simulate complete worker restart / separate process by instantiating store_2
    store_2 = PostgresVectorStore(db_url=DB_URL, schema=pg_schema)

    # Query with exact matching vector
    query_vec = [0.0] * 128
    query_vec[0] = 1.0
    results = store_2.query_similar(query_vec, user_id=user_a, top_k=5)
    assert len(results) == 1
    retrieved_chunk, score = results[0]
    assert retrieved_chunk.chunk_id == chunk.chunk_id
    assert "Passports must have at least 6 months" in retrieved_chunk.text_content
    assert score > 0.99  # Cosine similarity of identical vectors is ~1.0

    # Step 3: Tenant isolation verification (user_b must see nothing)
    results_b = store_2.query_similar(query_vec, user_id=user_b, top_k=5)
    assert len(results_b) == 0, "Cross-tenant vector query must return 0 results"


def test_postgres_foreign_key_constraints(pg_schema):
    """Verify foreign key cascades on research_sources -> research_evidence and research_evidence -> research_citations."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_id = uuid4()
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                        (user_id, f"fk_{user_id.hex[:6]}@example.com", "hash"))

            now = datetime.now(timezone.utc)
            from datetime import timedelta
            session_id = uuid4()
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, 'web', 'active', %s, %s)
            """, (session_id, user_id, now, now + timedelta(hours=2)))

            query_id = uuid4()
            cur.execute("""
                INSERT INTO research_queries (
                    id, session_id, user_id, query_text, normalized_query, intent, mode, created_at
                ) VALUES (%s, %s, %s, 'visa rules', 'visa rules', 'policy', 'web', %s)
            """, (query_id, session_id, user_id, now))

            source_id = uuid4()
            cur.execute("""
                INSERT INTO research_sources (
                    id, user_id, url, canonical_url, title, domain, source_type,
                    trust_class, content_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'web', 'trusted_travel_site', %s, %s)
            """, (source_id, user_id, "https://example.com/visa", "https://example.com/visa", "Visa Rules", "example.com", "hash_visa", now))

            evidence_id = uuid4()
            cur.execute("""
                INSERT INTO research_evidence (
                    id, query_id, user_id, source_id, canonical_url, title, domain,
                    trust_class, snippet, immutable_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'official', 'Visa required for stay > 30 days', %s, %s)
            """, (evidence_id, query_id, user_id, source_id, "https://example.com/visa", "Visa Rules", "example.com", "hash_ev", now))

            citation_id = uuid4()
            cur.execute("""
                INSERT INTO research_citations (
                    id, query_id, evidence_id, user_id, citation_token, title, domain,
                    canonical_url, retrieved_at, created_at
                ) VALUES (%s, %s, %s, %s, '[cite:1]', 'Visa Rules', 'example.com', %s, %s, %s)
            """, (citation_id, query_id, evidence_id, user_id, "https://example.com/visa", now, now))

            # Verify rows exist
            cur.execute("SELECT COUNT(*) AS c FROM research_citations WHERE id = %s", (citation_id,))
            assert cur.fetchone()["c"] == 1

            # Foreign key constraint enforcement: inserting evidence with non-existent source_id must fail
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute("""
                    INSERT INTO research_evidence (
                        id, query_id, user_id, source_id, canonical_url, title, domain,
                        trust_class, snippet, immutable_hash, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'official', 'Snippet', %s, %s)
                """, (uuid4(), query_id, user_id, uuid4(), "https://example.com/bad", "Bad", "example.com", "hbad", now))

            # Foreign key constraint enforcement: inserting citation with non-existent evidence_id must fail
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute("""
                    INSERT INTO research_citations (
                        id, query_id, evidence_id, user_id, citation_token, title, domain,
                        canonical_url, retrieved_at, created_at
                    ) VALUES (%s, %s, %s, %s, '[cite:2]', 'Bad', 'example.com', %s, %s, %s)
                """, (uuid4(), query_id, uuid4(), user_id, "https://example.com/bad", now, now))

            # Delete source -> blocked because child research_evidence is immutable and append-only
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute("DELETE FROM research_sources WHERE id = %s", (source_id,))
    finally:
        conn.close()


def test_postgres_immutability_trigger(pg_schema):
    """Verify that update trigger prevents modification of research_evidence (immutable append-only)."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_id = uuid4()
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                        (user_id, f"imm_{user_id.hex[:6]}@example.com", "hash"))

            now = datetime.now(timezone.utc)
            from datetime import timedelta
            session_id = uuid4()
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, 'web', 'active', %s, %s)
            """, (session_id, user_id, now, now + timedelta(hours=2)))

            query_id = uuid4()
            cur.execute("""
                INSERT INTO research_queries (
                    id, session_id, user_id, query_text, normalized_query, intent, mode, created_at
                ) VALUES (%s, %s, %s, 'rules query', 'rules query', 'general', 'web', %s)
            """, (query_id, session_id, user_id, now))

            source_id = uuid4()
            cur.execute("""
                INSERT INTO research_sources (
                    id, user_id, url, canonical_url, title, domain, source_type,
                    trust_class, content_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'web', 'curated_authority', %s, %s)
            """, (source_id, user_id, "https://example.com/imm", "https://example.com/imm", "Rules", "example.com", "hash_imm", now))

            evidence_id = uuid4()
            cur.execute("""
                INSERT INTO research_evidence (
                    id, query_id, user_id, source_id, canonical_url, title, domain,
                    trust_class, snippet, immutable_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'official', 'Original excerpt text', %s, %s)
            """, (evidence_id, query_id, user_id, source_id, "https://example.com/imm", "Rules", "example.com", "hash_orig", now))

            # Attempt to UPDATE research_evidence -> must fail with immutability error
            with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
                cur.execute("UPDATE research_evidence SET snippet = 'Tampered excerpt' WHERE id = %s", (evidence_id,))

            # Attempt to DELETE research_evidence directly -> must fail with append-only error
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                cur.execute("DELETE FROM research_evidence WHERE id = %s", (evidence_id,))
    finally:
        conn.close()


def test_postgres_mandatory_revision_linkage_check(pg_schema):
    """Verify CHECK constraint enforces revision_id NOT NULL when trip_id is present."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_id = uuid4()
            trip_id = uuid4()
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                        (user_id, f"chk_{user_id.hex[:6]}@example.com", "hash"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, 'Test Trip')",
                        (trip_id, user_id))

            now = datetime.now(timezone.utc)
            from datetime import timedelta
            session_id = uuid4()
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, 'web', 'active', %s, %s)
            """, (session_id, user_id, now, now + timedelta(hours=2)))

            query_id = uuid4()
            cur.execute("""
                INSERT INTO research_queries (
                    id, session_id, user_id, query_text, normalized_query, intent, mode, created_at
                ) VALUES (%s, %s, %s, 'visa query', 'visa query', 'policy', 'web', %s)
            """, (query_id, session_id, user_id, now))

            source_id = uuid4()
            cur.execute("""
                INSERT INTO research_sources (
                    id, user_id, url, canonical_url, title, domain, source_type,
                    trust_class, content_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'web', 'trusted_travel_site', %s, %s)
            """, (source_id, user_id, "https://example.com/rule", "https://example.com/rule", "Rules", "example.com", "hash_chk", now))

            # Inserting with trip_id but revision_id=NULL must fail with check constraint violation
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute("""
                    INSERT INTO research_evidence (
                        id, query_id, user_id, source_id, trip_id, revision_id,
                        canonical_url, title, domain, trust_class, snippet, immutable_hash, created_at
                    ) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, 'official', 'Snippet', %s, %s)
                """, (uuid4(), query_id, user_id, source_id, trip_id, "https://example.com/rule", "Rules", "example.com", "h1", now))
    finally:
        conn.close()


def test_postgres_exact_revision_id_persistence_in_research_evidence(pg_schema):
    """Verify that trip-linked research_evidence persists the exact authoritative revision_id."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_id = uuid4()
            trip_id = uuid4()
            rev_id = uuid4()
            now = datetime.now(timezone.utc)
            from datetime import timedelta

            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                        (user_id, f"rev_{user_id.hex[:6]}@example.com", "hash"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, 'Paris Trip')",
                        (trip_id, user_id))
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb)
            """, (rev_id, trip_id, user_id))

            session_id = uuid4()
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, trip_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, %s, 'web', 'active', %s, %s)
            """, (session_id, user_id, trip_id, now, now + timedelta(hours=2)))

            query_id = uuid4()
            cur.execute("""
                INSERT INTO research_queries (
                    id, session_id, user_id, trip_id, query_text, normalized_query, intent, mode, created_at
                ) VALUES (%s, %s, %s, %s, 'Paris flights', 'Paris flights', 'route', 'web', %s)
            """, (query_id, session_id, user_id, trip_id, now))

            source_id = uuid4()
            cur.execute("""
                INSERT INTO research_sources (
                    id, user_id, url, canonical_url, title, domain, source_type,
                    trust_class, content_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'web', 'official', %s, %s)
            """, (source_id, user_id, "https://example.com/paris", "https://example.com/paris", "Paris", "example.com", "h_paris", now))

            # Persist research_evidence with trip_id and exact revision_id
            ev_id = uuid4()
            cur.execute("""
                INSERT INTO research_evidence (
                    id, query_id, user_id, source_id, trip_id, revision_id,
                    canonical_url, title, domain, trust_class, snippet, immutable_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'official', 'Paris guide', %s, %s)
            """, (ev_id, query_id, user_id, source_id, trip_id, rev_id, "https://example.com/paris", "Paris", "example.com", "h_ev_paris", now))

            # Assert exact revision_id exists in research_evidence row
            cur.execute("SELECT id, user_id, trip_id, revision_id FROM research_evidence WHERE id = %s", (ev_id,))
            row = cur.fetchone()
            assert row is not None
            assert row["id"] == ev_id
            assert row["user_id"] == user_id
            assert row["trip_id"] == trip_id
            assert row["revision_id"] == rev_id, f"Expected revision_id {rev_id}, got {row['revision_id']}"
    finally:
        conn.close()


def test_postgres_cross_tenant_composite_fk_rejection(pg_schema):
    """Verify that composite foreign keys reject cross-tenant reference tampering at DB level."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            user_a = uuid4()
            user_b = uuid4()
            now = datetime.now(timezone.utc)
            from datetime import timedelta

            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s), (%s, %s, %s)",
                        (user_a, f"ua_{user_a.hex[:6]}@example.com", "hash",
                         user_b, f"ub_{user_b.hex[:6]}@example.com", "hash"))

            session_a = uuid4()
            cur.execute("""
                INSERT INTO research_sessions (id, user_id, mode, status, created_at, expires_at)
                VALUES (%s, %s, 'web', 'active', %s, %s)
            """, (session_a, user_a, now, now + timedelta(hours=2)))

            # Attempt to create research_query under User B referencing User A's session -> must fail
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute("""
                    INSERT INTO research_queries (
                        id, session_id, user_id, query_text, normalized_query, intent, mode, created_at
                    ) VALUES (%s, %s, %s, 'hijack query', 'hijack query', 'general', 'web', %s)
                """, (uuid4(), session_a, user_b, now))

            # Attempt to insert rag_chunks under User B referencing User A's document -> must fail
            doc_a = uuid4()
            cur.execute("""
                INSERT INTO rag_documents (
                    id, user_id, title, filename, content_type, file_size_bytes, content_hash, created_at
                ) VALUES (%s, %s, 'Doc A', 'doc.txt', 'text/plain', 100, 'h100', %s)
            """, (doc_a, user_a, now))

            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute("""
                    INSERT INTO rag_chunks (
                        id, document_id, user_id, chunk_index, text_content, token_count, content_hash, created_at
                    ) VALUES (%s, %s, %s, 0, 'Cross-tenant text', 5, 'cth', %s)
                """, (uuid4(), doc_a, user_b, now))
    finally:
        conn.close()
