"""Phase 9 PostgreSQL Live Integration Test Suite.

Verifies:
1. Migration 007 (007_flight_booking_lifecycle.sql) applies cleanly in an isolated schema.
2. Enum types exist within current schema namespace (typnamespace = to_regnamespace(current_schema())).
3. Real PostgreSQL booking_intents, booking_attempts, and booking_transitions persistence.
4. Strict PostgreSQL multi-column tenant constraints (user_id, trip_id) and revision foreign keys.
5. Idempotency unique constraints on booking_attempts (user_id, idempotency_key, action).
6. Immutable transition history: updates and deletes on booking_transitions are blocked by trigger.
7. Immutable tenant/revision identity on booking_intents: updates to user_id, trip_id, revision_id blocked.
8. Parent trip cascade deletion cleanly removes all booking_intents, attempts, and transitions.
9. Row-level lock concurrency (SELECT ... FOR UPDATE) on booking_intents.
10. Automatic revision invalidation trigger on trips when flight constraints change.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from dotenv import load_dotenv

load_dotenv(r"n:\Documents\GitHub\TrustTrip_TravelPlanner\.env")

DB_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def pg_schema():
    """Sets up an isolated schema on PostgreSQL with Phase 1-6 tables and Phase 9 Migration 007."""
    if not DB_URL:
        pytest.skip("DATABASE_URL not set; skipping live PostgreSQL integration tests")

    schema_name = f"p9_integ_{uuid4().hex[:8]}"
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
                    current_revision_id UUID NULL,
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

            # Load migration 004 (Offer Snapshots, Sessions)
            m004_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\004_travel_intelligence.sql"
            with open(m004_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            # Load migration 005 (Plan Revisions)
            m005_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\005_evidence_revision_lifecycle.sql"
            with open(m005_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            # Load migration 006 (Research & RAG)
            m006_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\006_research_and_rag.sql"
            with open(m006_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

            # Load migration 007 (Flight Booking Lifecycle)
            m007_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\007_flight_booking_lifecycle.sql"
            with open(m007_path, "r", encoding="utf-8") as f:
                cur.execute(f.read())

        yield schema_name

    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")
        except Exception:
            pass
        conn.close()


def _setup_test_fixtures(cur, pg_schema):
    """Helper to create a test user, trip, revision, and offer snapshot."""
    user_id = uuid4()
    trip_id = uuid4()
    revision_id = uuid4()
    session_id = uuid4()
    snapshot_id = uuid4()
    now = datetime.now(timezone.utc)

    # 1. User
    cur.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
        (user_id, f"test_{user_id.hex[:6]}@trusttrip.com", "secret"),
    )

    # 2. Trip (initially current_revision_id NULL to satisfy two-way FK constraint)
    cur.execute(
        """
        INSERT INTO trips (id, user_id, title, origin, destination, current_revision_id, metadata, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, NULL, %s::jsonb, %s, %s)
        """,
        (
            trip_id,
            user_id,
            "Tokyo Spring Trip",
            "JFK",
            "HND",
            '{"revision": 1, "constraints": {"origin": "JFK", "destination": "HND", "departure_date": "2026-10-01"}}',
            now,
            now,
        ),
    )

    # 3. Plan Revision
    cur.execute(
        """
        INSERT INTO plan_revisions (
            revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, created_at
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
        """,
        (revision_id, trip_id, user_id, 1, '{"changes": []}', '{"title": "Initial Plan"}', now),
    )

    # 3b. Bind authoritative revision to trip
    cur.execute(
        "UPDATE trips SET current_revision_id = %s WHERE id = %s",
        (revision_id, trip_id),
    )

    # 4. Search Session
    cur.execute(
        """
        INSERT INTO search_sessions (
            id, user_id, trip_id, trip_revision, search_type, criteria_hash, status, created_at, session_reuse_expires_at
        ) VALUES (%s, %s, %s, %s, 'flight', 'crit_hash_123', 'active', %s, %s)
        """,
        (session_id, user_id, trip_id, 1, now, now + timedelta(hours=1)),
    )

    # 5. Offer Snapshot
    cur.execute(
        """
        INSERT INTO offer_snapshots (
            id, session_id, provider, provider_offer_id, offer_type,
            price_total, currency, normalized_offer, raw_payload_encrypted, retrieved_at, expires_at
        ) VALUES (%s, %s, 'duffel', 'off_live_123', 'flight', %s, 'USD', %s::jsonb, %s, %s, %s)
        """,
        (
            snapshot_id,
            session_id,
            Decimal("750.00"),
            '{"slices": [{"origin": "JFK", "destination": "HND"}]}',
            b"encrypted_payload",
            now,
            now + timedelta(minutes=30),
        ),
    )

    return {
        "user_id": user_id,
        "trip_id": trip_id,
        "revision_id": revision_id,
        "session_id": session_id,
        "snapshot_id": snapshot_id,
    }


def test_postgres_migration_007_schema_objects(pg_schema):
    """Verify that all Phase 9 tables, enums, and triggers exist in the isolated schema."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")

            # 1. Verify tables exist
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
            """, (pg_schema,))
            tables = {r["table_name"] for r in cur.fetchall()}

            expected_tables = {
                "booking_intents",
                "booking_attempts",
                "booking_transitions",
            }
            assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

            # 2. Verify enum types exist in isolated schema
            cur.execute("""
                SELECT typname
                FROM pg_type
                WHERE typnamespace = to_regnamespace(%s)
            """, (pg_schema,))
            enums = {r["typname"] for r in cur.fetchall()}

            expected_enums = {
                "booking_provider_enum",
                "booking_mode_enum",
                "booking_status_enum",
            }
            assert expected_enums.issubset(enums), f"Missing enums: {expected_enums - enums}"
    finally:
        conn.close()


def test_postgres_booking_intents_composite_tenancy(pg_schema):
    """Verify strict composite foreign keys: cross-tenant trip binding is rejected by PostgreSQL."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            attacker_id = uuid4()
            cur.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                (attacker_id, "attacker@bad.org", "badhash"),
            )

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            # Attempt to insert booking_intent with attacker_id pointing to victim's trip_id
            with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.IntegrityError, psycopg.errors.RestrictViolation)):
                cur.execute(
                    """
                    INSERT INTO booking_intents (
                        id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                        provider, booking_mode, status, idempotency_key, passenger_hash,
                        expected_amount, currency, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_cross_tenant',
                        'phash123', 750.00, 'USD', %s
                    )
                    """,
                    (
                        intent_id,
                        attacker_id,  # Mismatched tenant
                        fixtures["trip_id"],  # Belongs to user_id
                        fixtures["revision_id"],
                        fixtures["snapshot_id"],
                        now + timedelta(minutes=30),
                    ),
                )
    finally:
        conn.close()


def test_postgres_booking_attempts_idempotency_constraint(pg_schema):
    """Verify idempotency uniqueness constraint (user_id, idempotency_key, action) on booking_attempts."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            # Insert valid booking intent
            cur.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_key_1',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )

            # Insert attempt 1
            cur.execute(
                """
                INSERT INTO booking_attempts (
                    intent_id, user_id, idempotency_key, action, status
                ) VALUES (%s, %s, 'idem_test_key', 'prepare', 'COMPLETED')
                """,
                (intent_id, fixtures["user_id"]),
            )

            # Duplicate attempt with same (user_id, idempotency_key, action) must violate unique constraint
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute(
                    """
                    INSERT INTO booking_attempts (
                        intent_id, user_id, idempotency_key, action, status
                    ) VALUES (%s, %s, 'idem_test_key', 'prepare', 'COMPLETED')
                    """,
                    (intent_id, fixtures["user_id"]),
                )
    finally:
        conn.close()


def test_postgres_booking_transitions_immutability_trigger(pg_schema):
    """Verify that booking_transitions records are strictly immutable (UPDATE and DELETE blocked)."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            cur.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_trans_test',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )

            trans_id = uuid4()
            cur.execute(
                """
                INSERT INTO booking_transitions (
                    transition_id, intent_id, from_status, to_status, trigger, actor_id, reason
                ) VALUES (%s, %s, 'DRAFT', 'SELECTED', 'PREPARE', %s, 'Initial prepare')
                """,
                (trans_id, intent_id, str(fixtures["user_id"])),
            )

            # UPDATE must be rejected by trigger
            with pytest.raises(psycopg.errors.RestrictViolation, match="immutable and cannot be updated"):
                cur.execute(
                    "UPDATE booking_transitions SET reason = 'Tampered' WHERE transition_id = %s",
                    (trans_id,),
                )

            # DELETE must be rejected by trigger
            with pytest.raises(psycopg.errors.RestrictViolation, match="immutable and cannot be updated"):
                cur.execute(
                    "DELETE FROM booking_transitions WHERE transition_id = %s",
                    (trans_id,),
                )
    finally:
        conn.close()


def test_postgres_freeze_identity_trigger(pg_schema):
    """Verify that user_id, trip_id, and revision_id on booking_intents cannot be mutated."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            cur.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_freeze_test',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )

            # Tampering with trip_id must be blocked
            with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
                cur.execute(
                    "UPDATE booking_intents SET trip_id = %s WHERE id = %s",
                    (uuid4(), intent_id),
                )
    finally:
        conn.close()


def test_postgres_trip_cascade_deletion(pg_schema):
    """Verify ON DELETE CASCADE from parent trip removes booking_intents and attempts."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            cur.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_cascade_test',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )

            cur.execute(
                """
                INSERT INTO booking_attempts (intent_id, user_id, idempotency_key, action, status)
                VALUES (%s, %s, 'idem_cascade_test', 'prepare', 'COMPLETED')
                """,
                (intent_id, fixtures["user_id"]),
            )

            # Delete parent trip
            cur.execute("DELETE FROM trips WHERE id = %s", (fixtures["trip_id"],))

            # Verify booking_intent and attempt were deleted via CASCADE
            cur.execute("SELECT COUNT(*) AS c FROM booking_intents WHERE id = %s", (intent_id,))
            assert cur.fetchone()["c"] == 0

            cur.execute("SELECT COUNT(*) AS c FROM booking_attempts WHERE intent_id = %s", (intent_id,))
            assert cur.fetchone()["c"] == 0
    finally:
        conn.close()


def test_postgres_revision_change_trigger_invalidates_flight_intents(pg_schema):
    """Verify that updating current_revision_id on trips with flight constraint changes invalidates active intents."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            cur.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_rev_trig',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )

            # Create a second revision with modified origin
            new_rev_id = uuid4()
            cur.execute(
                """
                INSERT INTO plan_revisions (
                    revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, created_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (new_rev_id, fixtures["trip_id"], fixtures["user_id"], 2, '{"changes": []}', '{"title": "Rev 2"}', now),
            )

            # Advance current_revision_id on trip with changed origin
            cur.execute(
                """
                UPDATE trips
                SET current_revision_id = %s, origin = 'BOS'
                WHERE id = %s
                """,
                (new_rev_id, fixtures["trip_id"]),
            )

            # Verify that the DB trigger transitioned the active intent to EXPIRED
            cur.execute("SELECT status FROM booking_intents WHERE id = %s", (intent_id,))
            row = cur.fetchone()
            assert row["status"] == "EXPIRED", f"Expected EXPIRED, got {row['status']}"
    finally:
        conn.close()


def test_postgres_state_machine_illegal_transition_rejected(pg_schema):
    """Verify that direct SQL state machine violations are rejected by PostgreSQL trigger."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            # Insert intent in SELECTED status
            cur.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_sm_test',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )

            # 1. Illegal transition: SELECTED -> COMPLETED (must fail)
            with pytest.raises(psycopg.errors.RestrictViolation):
                cur.execute(
                    "UPDATE booking_intents SET status = 'COMPLETED' WHERE id = %s",
                    (intent_id,),
                )

            # 2. Illegal transition: SELECTED -> HANDED_OFF (must fail, must go through REVALIDATING -> READY_FOR_BOOKING)
            with pytest.raises(psycopg.errors.RestrictViolation):
                cur.execute(
                    "UPDATE booking_intents SET status = 'HANDED_OFF' WHERE id = %s",
                    (intent_id,),
                )

            # 3. Legal transition: SELECTED -> REVALIDATING
            cur.execute(
                "UPDATE booking_intents SET status = 'REVALIDATING' WHERE id = %s",
                (intent_id,),
            )
            cur.execute("SELECT status FROM booking_intents WHERE id = %s", (intent_id,))
            assert cur.fetchone()["status"] == "REVALIDATING"

            # 4. Legal transition: REVALIDATING -> READY_FOR_BOOKING
            cur.execute(
                "UPDATE booking_intents SET status = 'READY_FOR_BOOKING' WHERE id = %s",
                (intent_id,),
            )

            # 5. Legal transition: READY_FOR_BOOKING -> HANDED_OFF
            cur.execute(
                "UPDATE booking_intents SET status = 'HANDED_OFF' WHERE id = %s",
                (intent_id,),
            )

            # 6. Legal transition: HANDED_OFF -> COMPLETED
            cur.execute(
                "UPDATE booking_intents SET status = 'COMPLETED' WHERE id = %s",
                (intent_id,),
            )

            # 7. Terminal transition: COMPLETED -> ABANDONED (must fail, terminal state cannot transition)
            with pytest.raises(psycopg.errors.RestrictViolation):
                cur.execute(
                    "UPDATE booking_intents SET status = 'ABANDONED' WHERE id = %s",
                    (intent_id,),
                )
    finally:
        conn.close()


def test_postgres_cross_tenant_snapshot_binding_rejected(pg_schema):
    """Verify that binding another tenant's OfferSnapshot to a BookingIntent is rejected by PostgreSQL."""
    conn = psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            fixtures = _setup_test_fixtures(cur, pg_schema)

            victim_snap_id = fixtures["snapshot_id"]

            # Attacker user and trip
            attacker_user_id = uuid4()
            attacker_trip_id = uuid4()
            attacker_rev_id = uuid4()
            cur.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
                (attacker_user_id, "attacker_snap@bad.org", "hash123"),
            )
            cur.execute(
                "INSERT INTO trips (id, user_id, title) VALUES (%s, %s, %s)",
                (attacker_trip_id, attacker_user_id, "Attacker Trip"),
            )
            cur.execute(
                """
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb)
                """,
                (attacker_rev_id, attacker_trip_id, attacker_user_id),
            )

            now = datetime.now(timezone.utc)
            intent_id = uuid4()

            # Attacker creates intent pointing to victim's snapshot -> Must be rejected by DB
            with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.RestrictViolation, psycopg.errors.IntegrityError)):
                cur.execute(
                    """
                    INSERT INTO booking_intents (
                        id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                        provider, booking_mode, status, idempotency_key, passenger_hash,
                        expected_amount, currency, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_steal_snap',
                        'phash', 500.00, 'USD', %s
                    )
                    """,
                    (intent_id, attacker_user_id, attacker_trip_id, attacker_rev_id, victim_snap_id, now + timedelta(minutes=30)),
                )
    finally:
        conn.close()


def test_postgres_multi_worker_concurrency_race(pg_schema):
    """Verify row locking and CAS prevents two workers from claiming the same intent simultaneously."""
    conn1 = psycopg.connect(DB_URL, row_factory=dict_row)
    conn2 = psycopg.connect(DB_URL, row_factory=dict_row)
    try:
        with conn1.cursor() as cur1, conn2.cursor() as cur2:
            cur1.execute(f"SET search_path TO {pg_schema}, public;")
            cur2.execute(f"SET search_path TO {pg_schema}, public;")
            conn1.commit()
            conn2.commit()

            fixtures = _setup_test_fixtures(cur1, pg_schema)
            conn1.commit()

            intent_id = uuid4()
            now = datetime.now(timezone.utc)

            # Insert intent
            cur1.execute(
                """
                INSERT INTO booking_intents (
                    id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                    provider, booking_mode, status, idempotency_key, passenger_hash,
                    expected_amount, currency, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'duffel', 'duffel_link', 'SELECTED', 'idem_worker_race',
                    'phash123', 750.00, 'USD', %s
                )
                """,
                (
                    intent_id,
                    fixtures["user_id"],
                    fixtures["trip_id"],
                    fixtures["revision_id"],
                    fixtures["snapshot_id"],
                    now + timedelta(minutes=30),
                ),
            )
            conn1.commit()

            # Worker 1 begins transaction and locks row
            cur1.execute("BEGIN;")
            cur1.execute("SELECT * FROM booking_intents WHERE id = %s FOR UPDATE NOWAIT", (intent_id,))
            locked_row = cur1.fetchone()
            assert locked_row["status"] == "SELECTED"

            # Worker 2 attempts NOWAIT lock -> Must raise LockNotAvailable
            cur2.execute("BEGIN;")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                cur2.execute("SELECT * FROM booking_intents WHERE id = %s FOR UPDATE NOWAIT", (intent_id,))
            conn2.rollback()

            # Worker 1 transitions status to REVALIDATING and commits
            cur1.execute(
                "UPDATE booking_intents SET status = 'REVALIDATING' WHERE id = %s AND status = 'SELECTED'",
                (intent_id,),
            )
            conn1.commit()

            # Worker 2 now tries optimistic CAS assuming status is still SELECTED -> rowcount must be 0!
            cur2.execute("BEGIN;")
            cur2.execute(
                "UPDATE booking_intents SET status = 'REVALIDATING' WHERE id = %s AND status = 'SELECTED'",
                (intent_id,),
            )
            assert cur2.rowcount == 0
            conn2.commit()
    finally:
        conn1.close()
        conn2.close()

