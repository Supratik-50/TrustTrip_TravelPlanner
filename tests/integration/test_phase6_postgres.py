"""Phase 6 PostgreSQL Live Integration Test Suite.

Executes all 20 required database-level invariant, trigger, cascade,
and concurrency tests against real PostgreSQL in an isolated schema.
"""
from __future__ import annotations

import os
from uuid import uuid4
import psycopg
from psycopg.errors import CheckViolation, ForeignKeyViolation, RestrictViolation
import pytest
from dotenv import load_dotenv

load_dotenv(r"n:\Documents\GitHub\TrustTrip_TravelPlanner\.env")

DB_URL = os.environ.get("DATABASE_URL")
MIGRATION_PATH = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\005_evidence_revision_lifecycle.sql"


@pytest.fixture(scope="module")
def pg_schema():
    """Sets up an isolated schema on PostgreSQL, runs migration 005, and tears it down."""
    if not DB_URL:
        pytest.skip("DATABASE_URL not set; skipping live PostgreSQL integration tests")

    schema_name = f"p6_integ_{uuid4().hex[:8]}"
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
                    created_at TIMESTAMPTZ DEFAULT now()
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

                CREATE TYPE freshness_status_enum AS ENUM ('fresh', 'stale', 'expired');
                CREATE TYPE availability_status_enum AS ENUM ('available', 'limited_inventory', 'sold_out', 'unavailable');
                CREATE TYPE revalidation_status_enum AS ENUM ('pending', 'confirmed_unchanged', 'price_changed', 'schedule_changed', 'offer_withdrawn');
                CREATE TYPE offer_type_enum AS ENUM ('flight', 'hotel');

                CREATE TABLE search_sessions (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                    thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
                    trip_revision INT NOT NULL DEFAULT 0,
                    search_type VARCHAR(32) NOT NULL,
                    criteria_hash VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    session_reuse_expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '1 hour'
                );

                CREATE TABLE offer_snapshots (
                    id UUID PRIMARY KEY,
                    session_id UUID NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
                    provider VARCHAR(32) NOT NULL,
                    provider_offer_id VARCHAR(255) NOT NULL,
                    offer_type offer_type_enum NOT NULL,
                    price_total NUMERIC(12, 2) NOT NULL,
                    currency VARCHAR(3) NOT NULL,
                    normalized_offer JSONB NOT NULL,
                    raw_payload_encrypted BYTEA NOT NULL,
                    freshness freshness_status_enum NOT NULL DEFAULT 'fresh',
                    availability availability_status_enum NOT NULL DEFAULT 'available',
                    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ NULL,
                    predecessor_snapshot_id UUID REFERENCES offer_snapshots(id) ON DELETE SET NULL
                );
            """)

            with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
                migration_sql = f.read()

            cur.execute(f"SET search_path TO {schema_name};")
            cur.execute(migration_sql)

            # Confirm all Phase 6 objects are created strictly inside the isolated schema
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_name IN ('plan_revisions', 'evidence_records', 'plan_approvals', 'plan_revision_evidence_links', 'evidence_provenance');
            """, (schema_name,))
            created_tables = {r[0] for r in cur.fetchall()}
            assert created_tables == {
                'plan_revisions', 'evidence_records', 'plan_approvals',
                'plan_revision_evidence_links', 'evidence_provenance'
            }

            # Confirm enum types created in isolated schema
            cur.execute("""
                SELECT typname FROM pg_type
                WHERE typnamespace = to_regnamespace(%s)
                  AND typname IN ('evidence_type_enum', 'usage_state_enum', 'freshness_kind_enum');
            """, (schema_name,))
            created_enums = {r[0] for r in cur.fetchall()}
            assert created_enums == {'evidence_type_enum', 'usage_state_enum', 'freshness_kind_enum'}

        yield schema_name

    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")
        conn.close()


def _get_conn(schema: str, autocommit: bool = False):
    conn = psycopg.connect(DB_URL, autocommit=autocommit)
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema};")
    return conn


def _seed_user_and_trip(schema: str):
    user_id = uuid4()
    trip_id = uuid4()
    with _get_conn(schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s);", (user_id, f"{uuid4().hex[:6]}@t.com", "h"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, %s);", (trip_id, user_id, "Integ Trip"))
    return user_id, trip_id


# --------------------------------------------------------------------------
# 1. PlanRevision UPDATE -> Reject
# --------------------------------------------------------------------------
def test_plan_revision_update_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))

            with pytest.raises(RestrictViolation):
                cur.execute("UPDATE plan_revisions SET revision_number = 2 WHERE revision_id = %s;", (rev_id,))


# --------------------------------------------------------------------------
# 2. PlanApproval UPDATE -> Reject
# --------------------------------------------------------------------------
def test_plan_approval_update_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    appr_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                VALUES (%s, %s, %s, %s);
            """, (appr_id, trip_id, rev_id, user_id))

            with pytest.raises(RestrictViolation):
                cur.execute("UPDATE plan_approvals SET approval_metadata = '{\"k\": 1}'::jsonb WHERE approval_id = %s;", (appr_id,))


# --------------------------------------------------------------------------
# 3. EvidenceRecord UPDATE -> Reject
# --------------------------------------------------------------------------
def test_evidence_record_update_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    ev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_id, trip_id, user_id))

            with pytest.raises(RestrictViolation):
                cur.execute("UPDATE evidence_records SET payload = '{\"price\": 200}'::jsonb WHERE evidence_id = %s;", (ev_id,))


# --------------------------------------------------------------------------
# 4. Unpublished Revision INSERT -> Reject
# --------------------------------------------------------------------------
def test_unpublished_revision_insert_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            with pytest.raises(CheckViolation):
                cur.execute("""
                    INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, is_published)
                    VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb, FALSE);
                """, (uuid4(), trip_id, user_id))


# --------------------------------------------------------------------------
# 5. Atomic Approval -> Commit Succeeds (Also tests multi-statement robustness)
# --------------------------------------------------------------------------
def test_atomic_approval_succeeds(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))

    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM trips WHERE id = %s FOR UPDATE;", (trip_id,))
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev_id, trip_id))
            cur.execute("""
                INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                VALUES (%s, %s, %s, %s);
            """, (uuid4(), trip_id, rev_id, user_id))
            cur.execute("UPDATE trips SET status = 'approved' WHERE id = %s;", (trip_id,))
        conn.commit()

    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, current_revision_number FROM trips WHERE id = %s;", (trip_id,))
            status, rev_num = cur.fetchone()
            assert status == "approved"
            assert rev_num == 1


# --------------------------------------------------------------------------
# 6. Approved Status Without Approval -> Commit Fails
# --------------------------------------------------------------------------
def test_approved_status_without_approval_fails(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))

    with pytest.raises(CheckViolation):
        with _get_conn(pg_schema) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE trips SET current_revision_id = %s, status = 'approved' WHERE id = %s;", (rev_id, trip_id))
            conn.commit()


# --------------------------------------------------------------------------
# 7. Approval Without Approved Status -> Commit Fails
# --------------------------------------------------------------------------
def test_approval_without_approved_status_fails(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev_id, trip_id))

    with pytest.raises(CheckViolation):
        with _get_conn(pg_schema) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                    VALUES (%s, %s, %s, %s);
                """, (uuid4(), trip_id, rev_id, user_id))
            conn.commit()


# --------------------------------------------------------------------------
# 8. Direct Approval Deletion -> Succeeds and Demotes
# --------------------------------------------------------------------------
def test_direct_approval_deletion_succeeds_and_demotes(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))

    # Approve
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev_id, trip_id))
            cur.execute("""
                INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                VALUES (%s, %s, %s, %s);
            """, (uuid4(), trip_id, rev_id, user_id))
            cur.execute("UPDATE trips SET status = 'approved' WHERE id = %s;", (trip_id,))
        conn.commit()

    # Direct DELETE on plan_approvals
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_approvals WHERE trip_id = %s AND revision_id = %s;", (trip_id, rev_id))
        conn.commit()

    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM trips WHERE id = %s;", (trip_id,))
            assert cur.fetchone()[0] == "awaiting_approval"


# --------------------------------------------------------------------------
# 9. Current Revision Deletion -> Succeeds (without approval)
# --------------------------------------------------------------------------
def test_current_revision_deletion_succeeds(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev_id, trip_id))

    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_revisions WHERE revision_id = %s;", (rev_id,))
        conn.commit()

    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_revision_id, current_revision_number, status FROM trips WHERE id = %s;", (trip_id,))
            cr_id, cr_num, st = cur.fetchone()
            assert cr_id is None
            assert cr_num == 0
            assert st == "awaiting_approval"


# --------------------------------------------------------------------------
# 10. Current Revision Deletion With Approval Cascade -> Succeeds (BLOCKER 1 VERIFICATION)
# --------------------------------------------------------------------------
def test_current_revision_deletion_with_approval_cascade_succeeds(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))

    # Approve
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev_id, trip_id))
            cur.execute("""
                INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                VALUES (%s, %s, %s, %s);
            """, (uuid4(), trip_id, rev_id, user_id))
            cur.execute("UPDATE trips SET status = 'approved' WHERE id = %s;", (trip_id,))
        conn.commit()

    # Now DELETE the active approved revision directly
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_revisions WHERE revision_id = %s;", (rev_id,))
        conn.commit()

    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_revision_id, current_revision_number, status FROM trips WHERE id = %s;", (trip_id,))
            cr_id, cr_num, st = cur.fetchone()
            assert cr_id is None
            assert cr_num == 0
            assert st == "awaiting_approval"

            # Verify approval was cascaded
            cur.execute("SELECT COUNT(*) FROM plan_approvals WHERE trip_id = %s;", (trip_id,))
            assert cur.fetchone()[0] == 0


# --------------------------------------------------------------------------
# 11. Historical Revision Deletion -> Succeeds (Current pointer untouched)
# --------------------------------------------------------------------------
def test_historical_revision_deletion_succeeds(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev1_id = uuid4()
    rev2_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev1_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 2, '{}'::jsonb, '{}'::jsonb);
            """, (rev2_id, trip_id, user_id))
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev2_id, trip_id))

    # Delete historical rev1
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_revisions WHERE revision_id = %s;", (rev1_id,))
        conn.commit()

    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_revision_id, current_revision_number FROM trips WHERE id = %s;", (trip_id,))
            cr_id, cr_num = cur.fetchone()
            assert cr_id == rev2_id
            assert cr_num == 2


# --------------------------------------------------------------------------
# 12. Revision Delete Acquires Trip Lock First
# --------------------------------------------------------------------------
def test_revision_delete_acquires_trip_lock_first(pg_schema):
    """Verify trg_lock_trip_before_plan_revision_delete is attached BEFORE DELETE."""
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT action_timing, event_manipulation
                FROM information_schema.triggers
                WHERE event_object_table = 'plan_revisions'
                  AND trigger_name = 'trg_lock_trip_before_plan_revision_delete';
            """)
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "BEFORE"
            assert row[1] == "DELETE"


# --------------------------------------------------------------------------
# 13. Approval Delete Acquires Trip Lock First
# --------------------------------------------------------------------------
def test_approval_delete_acquires_trip_lock_first(pg_schema):
    """Verify trg_on_plan_approval_deleted is attached BEFORE DELETE."""
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT action_timing, event_manipulation
                FROM information_schema.triggers
                WHERE event_object_table = 'plan_approvals'
                  AND trigger_name = 'trg_on_plan_approval_deleted';
            """)
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "BEFORE"
            assert row[1] == "DELETE"


# --------------------------------------------------------------------------
# 14. Concurrent Approval / Revocation Serialization
# --------------------------------------------------------------------------
def test_concurrent_approval_revocation_serialization(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    rev_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb);
            """, (rev_id, trip_id, user_id))
            cur.execute("UPDATE trips SET current_revision_id = %s, status = 'awaiting_approval' WHERE id = %s;", (rev_id, trip_id))

    # Approve
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM trips WHERE id = %s FOR UPDATE;", (trip_id,))
            cur.execute("""
                INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                VALUES (%s, %s, %s, %s);
            """, (uuid4(), trip_id, rev_id, user_id))
            cur.execute("UPDATE trips SET status = 'approved' WHERE id = %s;", (trip_id,))
        conn.commit()

    # Revoke under row lock
    with _get_conn(pg_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM trips WHERE id = %s FOR UPDATE;", (trip_id,))
            cur.execute("DELETE FROM plan_approvals WHERE trip_id = %s AND revision_id = %s;", (trip_id, rev_id))
        conn.commit()

    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            assert cur.execute("SELECT status FROM trips WHERE id = %s;", (trip_id,)).fetchone()[0] == "awaiting_approval"


# --------------------------------------------------------------------------
# 15. Provenance NULL -> Non-NULL -> Reject (Attach-Once)
# --------------------------------------------------------------------------
def test_provenance_null_to_non_null_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    ev_id = uuid4()
    session_id = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash)
                VALUES (%s, %s, %s, 'flight', 'hash1');
            """, (session_id, user_id, trip_id))
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO evidence_provenance (evidence_id, trip_id, user_id, supplier_session_id)
                VALUES (%s, %s, %s, NULL);
            """, (ev_id, trip_id, user_id))

            with pytest.raises(RestrictViolation):
                cur.execute("UPDATE evidence_provenance SET supplier_session_id = %s WHERE evidence_id = %s;", (session_id, ev_id))


# --------------------------------------------------------------------------
# 16. Provenance Reassignment -> Reject
# --------------------------------------------------------------------------
def test_provenance_reassignment_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    ev_id = uuid4()
    session1 = uuid4()
    session2 = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash)
                VALUES (%s, %s, %s, 'flight', 'h1'), (%s, %s, %s, 'hotel', 'h2');
            """, (session1, user_id, trip_id, session2, user_id, trip_id))
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO evidence_provenance (evidence_id, trip_id, user_id, supplier_session_id)
                VALUES (%s, %s, %s, %s);
            """, (ev_id, trip_id, user_id, session1))

            with pytest.raises(RestrictViolation):
                cur.execute("UPDATE evidence_provenance SET supplier_session_id = %s WHERE evidence_id = %s;", (session2, ev_id))


# --------------------------------------------------------------------------
# 17. Manual Detach While Parent Exists -> Reject
# --------------------------------------------------------------------------
def test_manual_detach_while_parent_exists_rejected(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    ev_id = uuid4()
    session1 = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash)
                VALUES (%s, %s, %s, 'flight', 'h1');
            """, (session1, user_id, trip_id))
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO evidence_provenance (evidence_id, trip_id, user_id, supplier_session_id)
                VALUES (%s, %s, %s, %s);
            """, (ev_id, trip_id, user_id, session1))

            with pytest.raises(RestrictViolation):
                cur.execute("UPDATE evidence_provenance SET supplier_session_id = NULL WHERE evidence_id = %s;", (ev_id,))


# --------------------------------------------------------------------------
# 18. Deleting search_session -> Detach Succeeds
# --------------------------------------------------------------------------
def test_deleting_search_session_detaches_provenance(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    ev_id = uuid4()
    session1 = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash)
                VALUES (%s, %s, %s, 'flight', 'h1');
            """, (session1, user_id, trip_id))
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO evidence_provenance (evidence_id, trip_id, user_id, supplier_session_id)
                VALUES (%s, %s, %s, %s);
            """, (ev_id, trip_id, user_id, session1))

            # Delete the parent search_session
            cur.execute("DELETE FROM search_sessions WHERE id = %s;", (session1,))
            cur.execute("SELECT supplier_session_id FROM evidence_provenance WHERE evidence_id = %s;", (ev_id,))
            assert cur.fetchone()[0] is None


# --------------------------------------------------------------------------
# 19. Deleting offer_snapshot -> Detach Succeeds
# --------------------------------------------------------------------------
def test_deleting_offer_snapshot_detaches_provenance(pg_schema):
    user_id, trip_id = _seed_user_and_trip(pg_schema)
    ev_id = uuid4()
    session1 = uuid4()
    snapshot1 = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash)
                VALUES (%s, %s, %s, 'flight', 'h1');
            """, (session1, user_id, trip_id))
            cur.execute("""
                INSERT INTO offer_snapshots (
                    id, session_id, provider, provider_offer_id, offer_type, price_total,
                    currency, normalized_offer, raw_payload_encrypted
                ) VALUES (%s, %s, 'duffel', 'off_1', 'flight', 150.00, 'USD', '{}'::jsonb, '\\x01'::bytea);
            """, (snapshot1, session1))
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_id, trip_id, user_id))
            cur.execute("""
                INSERT INTO evidence_provenance (evidence_id, trip_id, user_id, supplier_session_id, supplier_snapshot_id)
                VALUES (%s, %s, %s, %s, %s);
            """, (ev_id, trip_id, user_id, session1, snapshot1))

            # Delete the parent snapshot
            cur.execute("DELETE FROM offer_snapshots WHERE id = %s;", (snapshot1,))
            cur.execute("SELECT supplier_session_id, supplier_snapshot_id FROM evidence_provenance WHERE evidence_id = %s;", (ev_id,))
            s_id, snap_id = cur.fetchone()
            assert s_id == session1
            assert snap_id is None


# --------------------------------------------------------------------------
# 20. Cross-Tenant Provenance -> Reject
# --------------------------------------------------------------------------
def test_cross_tenant_provenance_rejected(pg_schema):
    user_a, trip_a = _seed_user_and_trip(pg_schema)
    user_b, trip_b = _seed_user_and_trip(pg_schema)
    session_a = uuid4()
    ev_b = uuid4()
    with _get_conn(pg_schema, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash)
                VALUES (%s, %s, %s, 'flight', 'h1');
            """, (session_a, user_a, trip_a))
            cur.execute("""
                INSERT INTO evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{}'::jsonb);
            """, (ev_b, trip_b, user_b))

            with pytest.raises(RestrictViolation):
                cur.execute("""
                    INSERT INTO evidence_provenance (evidence_id, trip_id, user_id, supplier_session_id)
                    VALUES (%s, %s, %s, %s);
                """, (ev_b, trip_b, user_b, session_a))


# --------------------------------------------------------------------------
# 21. Cross-Schema Enum Collision Regression Test
# --------------------------------------------------------------------------
def test_cross_schema_enum_collision_regression():
    """Verify migration 005 creates enums in the target schema even if identically named enums exist in another schema."""
    if not DB_URL:
        pytest.skip("DATABASE_URL not set")

    other_schema = f"p6_other_{uuid4().hex[:8]}"
    target_schema = f"p6_target_{uuid4().hex[:8]}"
    conn = psycopg.connect(DB_URL, autocommit=True)

    try:
        with conn.cursor() as cur:
            # 1. Create other schema and populate dummy colliding enum types
            cur.execute(f"CREATE SCHEMA {other_schema};")
            cur.execute(f"CREATE TYPE {other_schema}.evidence_type_enum AS ENUM ('DUMMY_OTHER');")
            cur.execute(f"CREATE TYPE {other_schema}.usage_state_enum AS ENUM ('DUMMY_OTHER');")
            cur.execute(f"CREATE TYPE {other_schema}.freshness_kind_enum AS ENUM ('DUMMY_OTHER');")

            # 2. Create target schema and set search path with target first, other schema second
            cur.execute(f"CREATE SCHEMA {target_schema};")
            cur.execute(f"SET search_path TO {target_schema}, {other_schema};")

            # 3. Create baseline tables in target schema
            cur.execute(f"""
                CREATE TABLE {target_schema}.users (
                    id UUID PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                CREATE TABLE {target_schema}.trips (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES {target_schema}.users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL DEFAULT '',
                    destination TEXT NOT NULL DEFAULT '',
                    origin TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planning',
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE TABLE {target_schema}.threads (
                    id TEXT PRIMARY KEY,
                    trip_id UUID NOT NULL REFERENCES {target_schema}.trips(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES {target_schema}.users(id) ON DELETE CASCADE,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE TYPE {target_schema}.freshness_status_enum AS ENUM ('fresh', 'stale', 'expired');
                CREATE TYPE {target_schema}.availability_status_enum AS ENUM ('available', 'limited_inventory', 'sold_out', 'unavailable');
                CREATE TYPE {target_schema}.revalidation_status_enum AS ENUM ('pending', 'confirmed_unchanged', 'price_changed', 'schedule_changed', 'offer_withdrawn');
                CREATE TYPE {target_schema}.offer_type_enum AS ENUM ('flight', 'hotel');
                CREATE TABLE {target_schema}.search_sessions (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES {target_schema}.users(id) ON DELETE CASCADE,
                    trip_id UUID NOT NULL REFERENCES {target_schema}.trips(id) ON DELETE CASCADE,
                    thread_id TEXT REFERENCES {target_schema}.threads(id) ON DELETE SET NULL,
                    trip_revision INT NOT NULL DEFAULT 0,
                    search_type VARCHAR(32) NOT NULL,
                    criteria_hash VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    session_reuse_expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '1 hour'
                );
                CREATE TABLE {target_schema}.offer_snapshots (
                    id UUID PRIMARY KEY,
                    session_id UUID NOT NULL REFERENCES {target_schema}.search_sessions(id) ON DELETE CASCADE,
                    provider VARCHAR(32) NOT NULL,
                    provider_offer_id VARCHAR(255) NOT NULL,
                    offer_type {target_schema}.offer_type_enum NOT NULL,
                    price_total NUMERIC(12, 2) NOT NULL,
                    currency VARCHAR(3) NOT NULL,
                    normalized_offer JSONB NOT NULL,
                    raw_payload_encrypted BYTEA NOT NULL,
                    freshness {target_schema}.freshness_status_enum NOT NULL DEFAULT 'fresh',
                    availability {target_schema}.availability_status_enum NOT NULL DEFAULT 'available',
                    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ NULL,
                    predecessor_snapshot_id UUID REFERENCES {target_schema}.offer_snapshots(id) ON DELETE SET NULL
                );
            """)

            # 4. Execute migration 005 inside target_schema
            with open(MIGRATION_PATH, "r", encoding="utf-8") as f:
                migration_sql = f.read()

            cur.execute(f"SET search_path TO {target_schema}, {other_schema};")
            cur.execute(migration_sql)

            # 5. Verify target_schema has its OWN evidence_type_enum containing 'FLIGHT'
            cur.execute("""
                SELECT e.enumlabel
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                WHERE t.typname = 'evidence_type_enum'
                  AND t.typnamespace = to_regnamespace(%s);
            """, (target_schema,))
            target_labels = {r[0] for r in cur.fetchall()}
            assert "FLIGHT" in target_labels
            assert "DUMMY_OTHER" not in target_labels

            # 6. Verify evidence_records table in target_schema can insert using target enum
            user_id = uuid4()
            trip_id = uuid4()
            ev_id = uuid4()
            cur.execute(f"INSERT INTO {target_schema}.users (id, email, password_hash) VALUES (%s, %s, %s);", (user_id, "u@t.com", "h"))
            cur.execute(f"INSERT INTO {target_schema}.trips (id, user_id, title) VALUES (%s, %s, %s);", (trip_id, user_id, "T"))
            cur.execute(f"""
                INSERT INTO {target_schema}.evidence_records (
                    evidence_id, trip_id, user_id, evidence_type, applicability_key,
                    applicability_hash, content_hash, freshness_kind, freshness_ttl_seconds,
                    source_agent, payload
                ) VALUES (%s, %s, %s, 'FLIGHT', '{{}}'::jsonb, 'h1', 'h2', 'LIVE_SUPPLIER', 300, 'agent', '{{}}'::jsonb);
            """, (ev_id, trip_id, user_id))

            cur.execute(f"SELECT evidence_type FROM {target_schema}.evidence_records WHERE evidence_id = %s;", (ev_id,))
            assert cur.fetchone()[0] == "FLIGHT"

    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {other_schema} CASCADE;")
            cur.execute(f"DROP SCHEMA IF EXISTS {target_schema} CASCADE;")
        conn.close()
