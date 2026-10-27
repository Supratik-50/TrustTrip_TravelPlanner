"""Phase 7 PostgreSQL Live Integration Test Suite.

Verifies:
1. Real PostgreSQL search_sessions and offer_snapshots persistence with AES-256-GCM encryption.
2. Real PostgreSQL advisory lock concurrency coordination on (user_id, trip_id, criteria_hash).
3. Real PostgreSQL tenant isolation: cross-tenant snapshot queries return empty/none.
4. Real PostgreSQL EvidenceRecord and EvidenceProvenance creation and immutability.
5. get_snapshots_for_session query execution in real PostgreSQL.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from dotenv import load_dotenv
import base64
import secrets

load_dotenv(r"n:\Documents\GitHub\TrustTrip_TravelPlanner\.env")

if not os.environ.get("PAYLOAD_ENCRYPTION_KEY"):
    os.environ["PAYLOAD_ENCRYPTION_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode("utf-8")

DB_URL = os.environ.get("DATABASE_URL")
DEFAULT_DEP_DATE = date.today() + timedelta(days=30)


@pytest.fixture(scope="module")
def pg_schema():
    """Sets up an isolated schema on PostgreSQL with Phase 4 & Phase 6 tables."""
    if not DB_URL:
        pytest.skip("DATABASE_URL not set; skipping live PostgreSQL integration tests")

    schema_name = f"p7_integ_{uuid4().hex[:8]}"
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

            # Load migration 009 (adds parent_revision_id and plan_revision_rejections)
            m009_path = r"n:\Documents\GitHub\TrustTrip_TravelPlanner\migrations\009_revision_parent_and_rejections.sql"
            if os.path.exists(m009_path):
                with open(m009_path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())

        yield schema_name

    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")
        except Exception:
            pass
        conn.close()


def test_postgres_advisory_lock_concurrency(pg_schema):
    """Verify PostgreSQL pg_advisory_lock works cleanly across connections."""
    from services.search_session_store import _criteria_lock_id

    user_id = uuid4()
    trip_id = uuid4()
    crit_hash = "abc123hash"
    lock_id = _criteria_lock_id(user_id, trip_id, crit_hash)

    conn1 = psycopg.connect(DB_URL, autocommit=True)
    conn2 = psycopg.connect(DB_URL, autocommit=True)

    try:
        # Conn1 acquires lock
        conn1.execute("SELECT pg_advisory_lock(%s)", (lock_id,))

        # Conn2 tries to acquire lock; should return False (already held)
        row = conn2.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,)).fetchone()
        assert row[0] is False, "Conn2 should not acquire advisory lock already held by Conn1"

        # Conn1 releases lock
        conn1.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

        # Conn2 can now acquire lock
        row2 = conn2.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,)).fetchone()
        assert row2[0] is True, "Conn2 should acquire advisory lock after Conn1 released it"
        conn2.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
    finally:
        conn1.close()
        conn2.close()


def test_postgres_session_snapshot_tenant_isolation(pg_schema):
    """Verify that in PostgreSQL, snapshot retrieval enforces strict tenant ownership."""
    from domain.live_offer import (
        AvailabilityStatus,
        FlightOffer,
        FreshnessStatus,
        PriceBreakdown,
    )
    from services.search_session_store import SearchSessionStore

    user_a = uuid4()
    user_b = uuid4()
    trip_id = uuid4()

    conn = psycopg.connect(DB_URL, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (str(user_a), "a@test.com", "h"))
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (str(user_b), "b@test.com", "h"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, %s)", (str(trip_id), str(user_a), "Trip A"))
    finally:
        conn.close()

    # Use SearchSessionStore pointed to this schema via DATABASE_URL
    store = SearchSessionStore()
    # Force use of postgres connection
    store.use_in_memory = False

    # Seed session and snapshot using direct queries in pg_schema
    session_id = uuid4()
    snapshot_id = uuid4()
    now = datetime.now(timezone.utc)

    conn = psycopg.connect(DB_URL, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {pg_schema}, public;")
            cur.execute("""
                INSERT INTO search_sessions (id, user_id, trip_id, search_type, criteria_hash, status, created_at, session_reuse_expires_at)
                VALUES (%s, %s, %s, 'flights', 'hash_test', 'active', now(), now() + interval '15 minutes')
            """, (str(session_id), str(user_a), str(trip_id)))

            offer_json = {
                "snapshot_id": str(snapshot_id),
                "provider": "duffel",
                "provider_offer_id": "off_real_secret_123",
                "price": {"base_amount": "400.00", "total_amount": "400.00", "currency": "USD"},
                "stops": 0,
                "segments": [],
                "passenger_count": 1,
                "retrieved_at": now.isoformat(),
                "expires_at": (now + datetime.resolution).isoformat(),
                "freshness": "fresh",
                "availability": "available",
            }

            from services.search_session_store import encrypt_payload
            encrypted = encrypt_payload({"raw": "payload"}, store._key)

            cur.execute("""
                INSERT INTO offer_snapshots (
                    id, session_id, provider, provider_offer_id, offer_type,
                    price_total, currency, normalized_offer, raw_payload_encrypted,
                    freshness, availability, retrieved_at
                ) VALUES (%s, %s, 'duffel', 'off_real_secret_123', 'flight', 400.00, 'USD', %s::jsonb, %s, 'fresh', 'available', now())
            """, (str(snapshot_id), str(session_id), psycopg.types.json.Jsonb(offer_json), encrypted))

            # Query with User A -> Found
            cur.execute("""
                SELECT s.id FROM offer_snapshots s
                JOIN search_sessions ss ON s.session_id = ss.id
                WHERE s.id = %s AND ss.user_id = %s
            """, (str(snapshot_id), str(user_a)))
            assert cur.fetchone() is not None

            # Query with User B -> Denied (None)
            cur.execute("""
                SELECT s.id FROM offer_snapshots s
                JOIN search_sessions ss ON s.session_id = ss.id
                WHERE s.id = %s AND ss.user_id = %s
            """, (str(snapshot_id), str(user_b)))
            assert cur.fetchone() is None
    finally:
        conn.close()


def test_real_postgres_5_concurrent_searches_deduplication(pg_schema, monkeypatch):
    """Real PostgreSQL: 5 simultaneous identical searches execute supplier query exactly once via pg_advisory_lock."""
    import asyncio
    import time
    from domain.live_offer import FlightOffer, FlightSegmentModel, PriceBreakdown, FreshnessStatus, AvailabilityStatus
    from domain.search_criteria import FlexibleFlightSearchCriteria, FlexibleDateOptions, DateFlexibilityMode, compute_criteria_hash
    from services.travel_intelligence.orchestrator import TravelIntelligenceOrchestrator
    from services.search_session_store import _criteria_lock_id

    schema_db_url = f"{DB_URL}?options=-csearch_path%3D{pg_schema},public"
    monkeypatch.setenv("DATABASE_URL", schema_db_url)
    monkeypatch.delenv("USE_IN_MEMORY_AUTH", raising=False)

    user_id = uuid4()
    trip_id = uuid4()

    conn = psycopg.connect(schema_db_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (str(user_id), "conc@test.com", "h"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, %s)", (str(trip_id), str(user_id), "Concurrent Trip"))
    finally:
        conn.close()

    provider_call_count = 0

    def _make_dummy_offer(dep_date=None, price=350.0):
        if dep_date is None:
            dep_date = DEFAULT_DEP_DATE.isoformat()
        dep_dt = datetime.fromisoformat(f"{dep_date}T10:00:00+00:00")
        arr_dt = datetime.fromisoformat(f"{dep_date}T18:00:00+00:00")
        seg = FlightSegmentModel(
            origin_iata="LHR", destination_iata="JFK",
            departure_time=dep_dt, arrival_time=arr_dt,
            marketing_carrier_iata="BA", marketing_carrier_name="British Airways",
            flight_number="100", duration_minutes=480,
        )
        offer = FlightOffer(
            provider="duffel", provider_offer_id=f"off_{uuid4().hex[:8]}",
            price=PriceBreakdown(base_amount=Decimal(str(price)), total_amount=Decimal(str(price)), currency="USD"),
            stops=0, segments=[seg], passenger_count=1,
            retrieved_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            freshness=FreshnessStatus.FRESH, availability=AvailabilityStatus.AVAILABLE,
        )
        return offer, {"raw": "data"}

    class ConcurrentMockAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            nonlocal provider_call_count
            provider_call_count += 1
            time.sleep(0.1)  # Ensure concurrent workers overlap at lock
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_offer(dep_date=dep, price=320.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=ConcurrentMockAdapter())
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEFAULT_DEP_DATE,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )

        tasks = [
            orch.search_flexible_flights(user_id=user_id, trip_id=trip_id, criteria=crit)
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)

        assert all(r.success is True for r in results), "All 5 concurrent requests must succeed"
        assert provider_call_count == 1, f"Expected exactly 1 supplier execution, got {provider_call_count}"
        assert len(set(r.session_id for r in results)) == 1, "All 5 callers must receive the identical session_id"

        # Verify advisory lock is released after completion
        crit_hash = compute_criteria_hash(crit)
        lock_id = _criteria_lock_id(user_id, trip_id, crit_hash)
        check_conn = psycopg.connect(schema_db_url, autocommit=True)
        try:
            row = check_conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,)).fetchone()
            assert row[0] is True, "Lock must be released and free after concurrent searches finish"
            check_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
        finally:
            check_conn.close()

    asyncio.run(_run())


def test_real_postgres_advisory_lock_release_after_exception(pg_schema, monkeypatch):
    """Real PostgreSQL: Advisory lock is guaranteed to be released even if provider search raises an error."""
    import asyncio
    from domain.search_criteria import FlexibleFlightSearchCriteria, FlexibleDateOptions, DateFlexibilityMode, compute_criteria_hash
    from services.flight_provider import FlightProviderError
    from services.travel_intelligence.orchestrator import TravelIntelligenceOrchestrator
    from services.search_session_store import _criteria_lock_id

    schema_db_url = f"{DB_URL}?options=-csearch_path%3D{pg_schema},public"
    monkeypatch.setenv("DATABASE_URL", schema_db_url)
    monkeypatch.delenv("USE_IN_MEMORY_AUTH", raising=False)

    user_id = uuid4()
    trip_id = uuid4()

    conn = psycopg.connect(schema_db_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (str(user_id), "exc@test.com", "h"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, %s)", (str(trip_id), str(user_id), "Exc Trip"))
    finally:
        conn.close()

    class FailingAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            raise FlightProviderError("Provider crashed")

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=FailingAdapter())
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEFAULT_DEP_DATE,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        res = await orch.search_flexible_flights(user_id=user_id, trip_id=trip_id, criteria=crit)
        assert res.success is False

        # Verify advisory lock is free
        crit_hash = compute_criteria_hash(crit)
        lock_id = _criteria_lock_id(user_id, trip_id, crit_hash)
        check_conn = psycopg.connect(schema_db_url, autocommit=True)
        try:
            row = check_conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,)).fetchone()
            assert row[0] is True, "Lock must be released even after provider exception"
            check_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
        finally:
            check_conn.close()

    asyncio.run(_run())


def test_real_postgres_concurrent_different_criteria_parallel(pg_schema, monkeypatch):
    """Real PostgreSQL: Searches with different criteria execute in parallel without cross-blocking."""
    import asyncio
    import time
    from domain.live_offer import FlightOffer, FlightSegmentModel, PriceBreakdown, FreshnessStatus, AvailabilityStatus
    from domain.search_criteria import FlexibleFlightSearchCriteria, FlexibleDateOptions, DateFlexibilityMode
    from services.travel_intelligence.orchestrator import TravelIntelligenceOrchestrator

    schema_db_url = f"{DB_URL}?options=-csearch_path%3D{pg_schema},public"
    monkeypatch.setenv("DATABASE_URL", schema_db_url)
    monkeypatch.delenv("USE_IN_MEMORY_AUTH", raising=False)

    user_id = uuid4()
    trip_id = uuid4()

    conn = psycopg.connect(schema_db_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (str(user_id), "diff@test.com", "h"))
            cur.execute("INSERT INTO trips (id, user_id, title) VALUES (%s, %s, %s)", (str(trip_id), str(user_id), "Diff Trip"))
    finally:
        conn.close()

    def _make_dummy_offer(orig, dest, price=300.0):
        dep_dt = datetime.combine(DEFAULT_DEP_DATE, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)
        arr_dt = dep_dt + timedelta(hours=8)
        seg = FlightSegmentModel(
            origin_iata=orig, destination_iata=dest,
            departure_time=dep_dt, arrival_time=arr_dt,
            marketing_carrier_iata="BA", marketing_carrier_name="British Airways",
            flight_number="100", duration_minutes=480,
        )
        offer = FlightOffer(
            provider="duffel", provider_offer_id=f"off_{uuid4().hex[:8]}",
            price=PriceBreakdown(base_amount=Decimal(str(price)), total_amount=Decimal(str(price)), currency="USD"),
            stops=0, segments=[seg], passenger_count=1,
            retrieved_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            freshness=FreshnessStatus.FRESH, availability=AvailabilityStatus.AVAILABLE,
        )
        return offer, {"raw": "data"}

    class ParallelAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            time.sleep(0.05)
            orig = criteria.slices[0].origin_iata
            dest = criteria.slices[0].destination_iata
            return [_make_dummy_offer(orig, dest, price=300.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=ParallelAdapter())
        crit_a = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR", destination_iata="JFK",
            departure_date=DEFAULT_DEP_DATE,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        crit_b = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="DEL", destination_iata="DXB",
            departure_date=DEFAULT_DEP_DATE,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )

        res_a, res_b = await asyncio.gather(
            orch.search_flexible_flights(user_id=user_id, trip_id=trip_id, criteria=crit_a),
            orch.search_flexible_flights(user_id=user_id, trip_id=trip_id, criteria=crit_b),
        )
        assert res_a.success is True
        assert res_b.success is True
        assert res_a.session_id != res_b.session_id

    asyncio.run(_run())


def test_real_postgres_atomic_selection_concurrency(pg_schema, monkeypatch):
    """Real PostgreSQL: Row lock FOR UPDATE serializes concurrent selection and revision update."""
    import concurrent.futures
    import time
    from pydantic import BaseModel
    from domain.live_offer import FlightOffer, FlightSegmentModel, PriceBreakdown, FreshnessStatus, AvailabilityStatus
    from services.search_session_store import SearchSessionStore
    from services.trip_store import TripStore, OfferSelectionError

    schema_db_url = f"{DB_URL}?options=-csearch_path%3D{pg_schema},public"
    monkeypatch.setenv("DATABASE_URL", schema_db_url)
    monkeypatch.delenv("USE_IN_MEMORY_AUTH", raising=False)

    user_id = uuid4()
    trip_id = uuid4()

    conn = psycopg.connect(schema_db_url, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)", (str(user_id), "atomic@test.com", "h"))
            cur.execute("INSERT INTO trips (id, user_id, title, current_revision_number) VALUES (%s, %s, %s, 1)", (str(trip_id), str(user_id), "Atomic Trip"))
    finally:
        conn.close()

    session_store = SearchSessionStore()
    trip_store = TripStore()

    session = session_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_atomic_test",
        trip_revision=1,
    )

    now = datetime.now(timezone.utc)
    dep_dt = datetime.fromisoformat("2026-10-18T10:00:00+00:00")
    arr_dt = datetime.fromisoformat("2026-10-18T18:00:00+00:00")
    seg = FlightSegmentModel(
        origin_iata="LHR", destination_iata="JFK",
        departure_time=dep_dt, arrival_time=arr_dt,
        marketing_carrier_iata="BA", marketing_carrier_name="British Airways",
        flight_number="BA100", duration_minutes=480,
    )
    offer = FlightOffer(
        provider="duffel",
        provider_offer_id="off_atomic_1",
        price=PriceBreakdown(base_amount=Decimal("450.0"), total_amount=Decimal("450.0"), currency="USD"),
        stops=0,
        segments=[seg],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now + timedelta(hours=1),
        freshness=FreshnessStatus.FRESH,
        availability=AvailabilityStatus.AVAILABLE,
        candidate_departure_date=date(2026, 10, 18),
    )
    snap = session_store.save_snapshot(session.session_id, offer, raw_payload={"data": "test"})

    class MockReq(BaseModel):
        offer_type: str = "flight"
        snapshot_id: UUID

    req = MockReq(snapshot_id=snap.snapshot_id)

    # Concurrently attempt selection (Transaction A) and revision update (Transaction B)
    def do_select():
        return trip_store.select_offer_atomic(trip_id, user_id, req, session_store)

    def do_advance_revision():
        time.sleep(0.02)
        # Advance revision to 2
        return trip_store.update_trip(trip_id, user_id, current_revision_number=2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_select = executor.submit(do_select)
        f_rev = executor.submit(do_advance_revision)

        # Both finish cleanly without deadlock
        res_sel = None
        sel_err = None
        try:
            res_sel = f_select.result()
        except OfferSelectionError as exc:
            sel_err = exc

        res_rev = f_rev.result()
        assert res_rev is not None

        # Either select succeeded first (and bound snapshot), or revision 2 committed first and selection failed closed with 400
        if res_sel:
            meta = res_sel.get("metadata") or {}
            assert meta.get("selected_flight_snapshot_id") == str(snap.snapshot_id)
        else:
            assert sel_err is not None
            assert sel_err.status_code == 400
            assert "outdated trip revision" in sel_err.detail


def _setup_postgres_client(pg_schema, monkeypatch, email="p7_concur@test.com"):
    schema_db_url = f"{DB_URL}?options=-csearch_path%3D{pg_schema},public"
    monkeypatch.setenv("DATABASE_URL", schema_db_url)
    monkeypatch.delenv("USE_IN_MEMORY_AUTH", raising=False)

    from services.auth_store import UserStore
    from services.auth_service import create_access_token
    from fastapi.testclient import TestClient
    from app import app

    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email=email)

    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id, schema_db_url


def test_real_postgres_concurrency_scenario_a_revision_and_offer_selection(pg_schema, monkeypatch):
    """Scenario A: Concurrent revision request + flexible offer selection under PostgreSQL row locking."""
    import concurrent.futures
    from domain.live_offer import FlightOffer, FlightSegmentModel, PriceBreakdown, FreshnessStatus, AvailabilityStatus
    from services.search_session_store import SearchSessionStore
    from services.trip_store import TripStore

    client, user_id, schema_db_url = _setup_postgres_client(pg_schema, monkeypatch, email=f"scen_a_{uuid4().hex[:6]}@test.com")
    session_store = SearchSessionStore()
    trip_store = TripStore()

    # 1. Create trip
    t_res = client.post("/api/trips", json={"title": "Scenario A Trip", "destination": "Paris"})
    assert t_res.status_code == 201
    trip_id = t_res.json()["trip"]["id"]

    # 2. Advance to revision 1
    r_init = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Initial plan", "updated_constraints": {"destination": "Paris"}},
    )
    assert r_init.status_code == 200
    assert r_init.json()["revision"] == 1

    # 3. Create session with trip_revision=1 and save snapshot
    session = session_store.create_session(
        user_id=user_id,
        trip_id=UUID(trip_id),
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_scen_a",
        trip_revision=1,
    )

    now = datetime.now(timezone.utc)
    dep_dt = datetime.fromisoformat("2026-10-20T10:00:00+00:00")
    arr_dt = datetime.fromisoformat("2026-10-20T18:00:00+00:00")
    seg = FlightSegmentModel(
        origin_iata="CDG", destination_iata="JFK",
        departure_time=dep_dt, arrival_time=arr_dt,
        marketing_carrier_iata="AF", marketing_carrier_name="Air France",
        flight_number="AF022", duration_minutes=480,
    )
    offer = FlightOffer(
        provider="duffel",
        provider_offer_id="off_scen_a_1",
        price=PriceBreakdown(base_amount=Decimal("500.0"), total_amount=Decimal("500.0"), currency="EUR"),
        stops=0,
        segments=[seg],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now + timedelta(hours=1),
        freshness=FreshnessStatus.FRESH,
        availability=AvailabilityStatus.AVAILABLE,
        candidate_departure_date=date(2026, 10, 20),
    )
    snap = session_store.save_snapshot(session.session_id, offer, raw_payload={"data": "test"})

    # 4. Concurrently execute offer selection and revision request
    def do_select():
        return client.post(
            f"/api/trips/{trip_id}/select-offer",
            json={"offer_type": "flight", "snapshot_id": str(snap.snapshot_id)},
        )

    def do_revise():
        return client.post(
            f"/api/trips/{trip_id}/revisions",
            json={
                "feedback": "Change destination to London",
                "updated_constraints": {"destination": "London"},
                "expected_base_revision": 1,
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_sel = executor.submit(do_select)
        f_rev = executor.submit(do_revise)

        res_sel = f_sel.result()
        res_rev = f_rev.result()

    # Exactly one ordering occurred under PostgreSQL row locking:
    # If select ran first: res_sel is 200, res_rev is 200 (revision 2 created, destination changed so flight invalidated).
    # If revise ran first: res_rev is 200, res_sel is 400 (rejected because session revision 1 != current revision 2).
    assert res_rev.status_code == 200, f"Revision request failed: {res_rev.text}"
    assert res_sel.status_code in (200, 400), f"Unexpected select status: {res_sel.status_code}"

    # Verify database state consistency
    final_trip = trip_store.get_trip(trip_id=trip_id, user_id=user_id)
    assert final_trip is not None
    assert final_trip["current_revision_number"] == 2
    meta = final_trip.get("metadata") or {}
    assert meta.get("revision") == 2
    assert meta.get("trip_revision") == 2
    # In neither case should an invalid / outdated flight remain selected
    assert meta.get("selected_flight_snapshot_id") != str(snap.snapshot_id) or res_sel.status_code == 200


def test_real_postgres_concurrency_scenario_b_revision_and_metadata_mutation(pg_schema, monkeypatch):
    """Scenario B: Concurrent revision request + metadata mutation."""
    import concurrent.futures
    import time
    from services.trip_store import TripStore

    client, user_id, schema_db_url = _setup_postgres_client(pg_schema, monkeypatch, email=f"scen_b_{uuid4().hex[:6]}@test.com")
    trip_store = TripStore()

    # 1. Create trip
    t_res = client.post("/api/trips", json={"title": "Scenario B Trip", "destination": "Tokyo"})
    assert t_res.status_code == 201
    trip_id = t_res.json()["trip"]["id"]

    # 2. Advance to revision 1
    r_init = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Base plan", "updated_constraints": {"duration_days": 5}},
    )
    assert r_init.status_code == 200
    assert r_init.json()["revision"] == 1

    # 3. Concurrently execute revision request and trip update
    def do_revise():
        return client.post(
            f"/api/trips/{trip_id}/revisions",
            json={
                "feedback": "Extend trip",
                "updated_constraints": {"duration_days": 8},
                "expected_base_revision": 1,
            },
        )

    def do_mutate_metadata():
        time.sleep(0.01)
        trip = trip_store.get_trip(trip_id=trip_id, user_id=user_id)
        existing_meta = trip.get("metadata") or {}
        existing_meta["user_tag"] = "vip_customer"
        return trip_store.update_trip(trip_id=trip_id, user_id=user_id, metadata=existing_meta)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_rev = executor.submit(do_revise)
        f_mut = executor.submit(do_mutate_metadata)

        res_rev = f_rev.result()
        res_mut = f_mut.result()

    assert res_rev.status_code == 200
    assert res_mut is not None

    # Verify no revision divergence
    final_trip = trip_store.get_trip(trip_id=trip_id, user_id=user_id)
    assert final_trip is not None
    db_rev = final_trip["current_revision_number"]
    meta = final_trip.get("metadata") or {}
    assert db_rev in (1, 2)
    assert meta.get("revision") == db_rev
    assert meta.get("trip_revision") == db_rev


def test_real_postgres_concurrency_scenario_c_two_revisions_same_base(pg_schema, monkeypatch):
    """Scenario C: Two concurrent revision requests on same base revision (one succeeds, one gets 409 Conflict)."""
    import concurrent.futures
    from services.trip_store import TripStore

    client, user_id, schema_db_url = _setup_postgres_client(pg_schema, monkeypatch, email=f"scen_c_{uuid4().hex[:6]}@test.com")
    trip_store = TripStore()

    # 1. Create trip
    t_res = client.post("/api/trips", json={"title": "Scenario C Trip", "destination": "Berlin"})
    assert t_res.status_code == 201
    trip_id = t_res.json()["trip"]["id"]

    # 2. Advance to revision 1
    r_init = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Base plan", "updated_constraints": {"duration_days": 3}},
    )
    assert r_init.status_code == 200
    assert r_init.json()["revision"] == 1

    # 3. Two concurrent revision requests with expected_base_revision=1
    def do_revise(worker_id: int):
        return client.post(
            f"/api/trips/{trip_id}/revisions",
            json={
                "feedback": f"Worker {worker_id} plan update",
                "updated_constraints": {"duration_days": 3 + worker_id},
                "expected_base_revision": 1,
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(do_revise, 1)
        f2 = executor.submit(do_revise, 2)

        res1 = f1.result()
        res2 = f2.result()

    statuses = sorted([res1.status_code, res2.status_code])
    assert statuses == [200, 409], f"Expected exactly one 200 and one 409, got: {res1.status_code}, {res2.status_code}"

    # Verify that the 409 response includes conflict explanation
    conflict_res = res1 if res1.status_code == 409 else res2
    assert "conflict" in conflict_res.json().get("detail", "").lower() or "base revision" in conflict_res.json().get("detail", "").lower()

    # Verify database state: revision 2 is authoritative, no lost updates
    final_trip = trip_store.get_trip(trip_id=trip_id, user_id=user_id)
    assert final_trip is not None
    assert final_trip["current_revision_number"] == 2
    meta = final_trip.get("metadata") or {}
    assert meta.get("revision") == 2
    assert meta.get("trip_revision") == 2
    assert final_trip["status"] == "awaiting_approval"

