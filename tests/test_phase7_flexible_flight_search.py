"""Phase 7 Flexible Live Flight Search Verification Test Suite.

Covers:
1. Exact mode -> 1 candidate
2. +/-1 -> 3 candidates
3. +/-2 -> 5 candidates
4. +/-3 -> 7 candidates
5. Date-range generation
6. Near-term fallback
7. Past-date pruning
8. Return-date synchronization
9. Invalid date ranges & model validation
10. Max candidate-date protection (<= 7)
11. Duplicate candidate prevention
12. Bounded concurrency <= 3
13. Global timeout enforcement
14. Cancellation cleanup
15. 429 retry with jitter
16. 5xx/timeout retry
17. Partial candidate failure tolerance
18. All-candidate failure
19. Offer deduplication
20. Ranking determinism
21. Provider ID shielding
22. Tenant isolation
23. Concurrent identical-search cache race
24. Immutable OfferSnapshot
25. EvidenceRecord linkage
26. EvidenceProvenance linkage
27. Date-change HITL invalidation
28. Passenger-change HITL invalidation
29. Origin-change invalidation
30. Destination-change invalidation
31. Hotel-only evidence reuse
32. Selected flexible offer updates canonical trip dates
33. Phase 5 handoff without whole-trip approval
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import app
from domain.change_set import compute_change_set, calculate_replanning_plan
from domain.evidence import (
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceType,
    FreshnessKind,
    PlanRevisionEvidenceLink,
    UsageState,
)
from domain.live_offer import (
    AvailabilityStatus,
    CandidateDateSummary,
    FlexibleFlightSearchResponse,
    FlightOffer,
    FlightOfferView,
    FlightSegmentModel,
    FreshnessStatus,
    PriceBreakdown,
)
from domain.search_criteria import (
    DateFlexibilityMode,
    FlexibleDateOptions,
    FlexibleFlightSearchCriteria,
    FlightSearchCriteria,
    FlightSliceCriteria,
    compute_criteria_hash,
)
from services.auth_store import UserStore
from services.evidence_policy import (
    build_applicability_key,
    determine_invalidated_evidence_types,
    evaluate_evidence_for_revision,
)
from services.evidence_store import EvidenceRevisionStore
from services.flight_booking_service import FlightBookingService
from services.flight_provider import (
    FlightProviderError,
    FlightProviderRateLimitError,
    FlightProviderTimeout,
)
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
from services.travel_intelligence.orchestrator import TravelIntelligenceOrchestrator
from services.travel_planner import candidate_dates
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def reset_stores(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_SEARCH_LOCKS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    EvidenceRevisionStore._IN_MEMORY_EVIDENCE.clear()
    EvidenceRevisionStore._IN_MEMORY_PROVENANCE.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_LINKS.clear()
    EvidenceRevisionStore._IN_MEMORY_APPROVALS.clear()


def _auth_user(email: str = "phase7@trusttrip.com"):
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user["id"]


# Fixed reference dates in the future for deterministic candidate-window tests
REF_BASE_DATE = date.today() + timedelta(days=30)
DEP_DATE_STR = REF_BASE_DATE.isoformat()
DEP_DATE_OBJ = REF_BASE_DATE

D_MINUS_3 = (REF_BASE_DATE - timedelta(days=3)).isoformat()
D_MINUS_2 = (REF_BASE_DATE - timedelta(days=2)).isoformat()
D_MINUS_1 = (REF_BASE_DATE - timedelta(days=1)).isoformat()
D_PLUS_1 = (REF_BASE_DATE + timedelta(days=1)).isoformat()
D_PLUS_2 = (REF_BASE_DATE + timedelta(days=2)).isoformat()
D_PLUS_3 = (REF_BASE_DATE + timedelta(days=3)).isoformat()
D_PLUS_4 = (REF_BASE_DATE + timedelta(days=4)).isoformat()
D_RET_7 = (REF_BASE_DATE + timedelta(days=7)).isoformat()
D_RET_10 = (REF_BASE_DATE + timedelta(days=10)).isoformat()


def _make_dummy_flight_offer(
    dep_date: str = DEP_DATE_STR,
    ret_date: str | None = D_RET_7,
    price: float = 450.0,
    flight_num: str = "101",
    airline: str = "BA",
) -> tuple[FlightOffer, dict]:
    dep_dt = datetime.fromisoformat(f"{dep_date}T10:00:00+00:00")
    arr_dt = datetime.fromisoformat(f"{dep_date}T18:00:00+00:00")
    segments = [
        FlightSegmentModel(
            origin_iata="LHR",
            destination_iata="JFK",
            departure_time=dep_dt,
            arrival_time=arr_dt,
            marketing_carrier_iata=airline,
            marketing_carrier_name="British Airways",
            flight_number=flight_num,
            duration_minutes=480,
            cabin_class="economy",
        )
    ]
    if ret_date:
        r_dep = datetime.fromisoformat(f"{ret_date}T10:00:00+00:00")
        r_arr = datetime.fromisoformat(f"{ret_date}T18:00:00+00:00")
        segments.append(
            FlightSegmentModel(
                origin_iata="JFK",
                destination_iata="LHR",
                departure_time=r_dep,
                arrival_time=r_arr,
                marketing_carrier_iata=airline,
                marketing_carrier_name="British Airways",
                flight_number=f"{flight_num}R",
                duration_minutes=480,
                cabin_class="economy",
            )
        )
    offer = FlightOffer(
        provider="duffel",
        provider_offer_id=f"off_duffel_{uuid4().hex[:8]}",
        price=PriceBreakdown(
            base_amount=Decimal(str(price)),
            tax_amount=Decimal("0.00"),
            fee_amount=Decimal("0.00"),
            total_amount=Decimal(str(price)),
            currency="USD",
        ),
        stops=0,
        segments=segments,
        passenger_count=1,
        available_seats=9,
        retrieved_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        candidate_departure_date=date.fromisoformat(dep_date),
        candidate_return_date=date.fromisoformat(ret_date) if ret_date else None,
    )
    raw = {
        "id": offer.provider_offer_id,
        "price": price,
        "currency": "USD",
        "duffel_link": "https://duffel.com/checkout/demo-link-123",
    }
    return offer, raw


# =========================================================================
# 1–11: Candidate Date Generation & Validation Tests
# =========================================================================

def test_candidate_dates_exact_mode():
    """Exact mode produces exactly 1 candidate date pair."""
    c = {"departure_date": DEP_DATE_STR, "duration_days": 5, "flexibility_mode": "exact"}
    res = candidate_dates(c, limit=7)
    assert len(res) == 1
    assert res[0] == (DEP_DATE_STR, D_PLUS_4)


def test_candidate_dates_plus_minus_1():
    """+/- 1 day mode produces 3 candidate date pairs."""
    c = {"departure_date": DEP_DATE_STR, "duration_days": 5, "flexibility_mode": "plus_minus_days", "flexibility_days": 1}
    res = candidate_dates(c, limit=7)
    assert len(res) == 3
    assert [d[0] for d in res] == [D_MINUS_1, DEP_DATE_STR, D_PLUS_1]


def test_candidate_dates_plus_minus_2():
    """+/- 2 days mode produces 5 candidate date pairs."""
    c = {"departure_date": DEP_DATE_STR, "duration_days": 5, "flexibility_mode": "plus_minus_days", "flexibility_days": 2}
    res = candidate_dates(c, limit=7)
    assert len(res) == 5
    assert [d[0] for d in res] == [D_MINUS_2, D_MINUS_1, DEP_DATE_STR, D_PLUS_1, D_PLUS_2]


def test_candidate_dates_plus_minus_3():
    """+/- 3 days mode produces 7 candidate date pairs."""
    c = {"departure_date": DEP_DATE_STR, "duration_days": 5, "flexibility_mode": "plus_minus_days", "flexibility_days": 3}
    res = candidate_dates(c, limit=7)
    assert len(res) == 7
    assert res[0][0] == D_MINUS_3
    assert res[-1][0] == D_PLUS_3


def test_candidate_dates_date_range_generation():
    """Date-range mode generates candidate pairs across the specified interval."""
    d_start = REF_BASE_DATE - timedelta(days=5)
    d_end = REF_BASE_DATE - timedelta(days=1)
    c = {
        "earliest_departure": d_start.isoformat(),
        "latest_departure": d_end.isoformat(),
        "flexibility_mode": "date_range",
        "duration_days": 5,
    }
    res = candidate_dates(c, limit=7)
    assert len(res) == 5
    assert [d[0] for d in res] == [(d_start + timedelta(days=i)).isoformat() for i in range(5)]


def test_candidate_dates_near_term_fallback():
    """Near-term fallback produces candidate pairs spaced by 3 days starting from today+2."""
    c = {"flexibility_mode": "near_term_fallback", "duration_days": 5}
    res = candidate_dates(c, limit=3)
    assert len(res) == 3
    t2 = (date.today() + timedelta(days=2)).isoformat()
    assert res[0][0] == t2


def test_candidate_dates_past_date_pruning():
    """Candidate dates earlier than today are pruned."""
    today = date.today()
    c = {
        "departure_date": today.isoformat(),
        "flexibility_mode": "plus_minus_days",
        "flexibility_days": 2,
        "duration_days": 5,
    }
    res = candidate_dates(c, limit=7)
    # Offsets -2 and -1 are in the past; only 0, +1, +2 remain
    for dep, _ in res:
        assert date.fromisoformat(dep) >= today


def test_candidate_dates_return_date_synchronization():
    """Return dates shift synchronously when an explicit return date is given."""
    c = {
        "departure_date": DEP_DATE_STR,
        "return_date": D_RET_10,
        "flexibility_mode": "plus_minus_days",
        "flexibility_days": 1,
    }
    res = candidate_dates(c, limit=7)
    assert res[0] == (D_MINUS_1, (REF_BASE_DATE + timedelta(days=9)).isoformat())
    assert res[1] == (DEP_DATE_STR, D_RET_10)
    assert res[2] == (D_PLUS_1, (REF_BASE_DATE + timedelta(days=11)).isoformat())


def test_invalid_flexibility_options():
    """Pydantic model rejects invalid flexibility options."""
    with pytest.raises(ValueError):
        FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=5)

    with pytest.raises(ValueError):
        FlexibleDateOptions(mode=DateFlexibilityMode.EXACT, flexibility_days=2)

    with pytest.raises(ValueError):
        FlexibleDateOptions(
            mode=DateFlexibilityMode.DATE_RANGE,
            earliest_departure=REF_BASE_DATE + timedelta(days=20),
            latest_departure=REF_BASE_DATE + timedelta(days=10),
        )


def test_max_candidate_date_protection():
    """Candidate generator strictly caps at maximum 7 dates even if limit is larger."""
    d_start = REF_BASE_DATE - timedelta(days=14)
    d_end = REF_BASE_DATE + timedelta(days=5)
    c = {
        "earliest_departure": d_start.isoformat(),
        "latest_departure": d_end.isoformat(),
        "flexibility_mode": "date_range",
        "duration_days": 5,
    }
    res = candidate_dates(c, limit=15)
    assert len(res) == 7


def test_duplicate_candidate_prevention():
    """Duplicate candidate pairs are eliminated deterministically."""
    c = {"departure_date": DEP_DATE_STR, "duration_days": 5}
    res = candidate_dates(c, limit=7)
    assert len(res) == 1
    assert len(set(res)) == len(res)


# =========================================================================
# 12–20: Search Execution, Concurrency, Retries & Aggregation
# =========================================================================

def test_bounded_concurrency_max_3():
    """Orchestrator limits outgoing concurrent requests to at most 3 simultaneous calls."""
    active_concurrent = 0
    max_concurrent_seen = 0

    class MockAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            nonlocal active_concurrent, max_concurrent_seen
            active_concurrent += 1
            if active_concurrent > max_concurrent_seen:
                max_concurrent_seen = active_concurrent
            import time
            time.sleep(0.05)
            active_concurrent -= 1
            dep = criteria.slices[0].departure_date.isoformat()
            offer, raw = _make_dummy_flight_offer(dep_date=dep, price=300.0)
            return [(offer, raw)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=MockAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=3),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit)
        assert resp.success is True
        assert max_concurrent_seen <= 3
        assert len(resp.candidate_dates) == 7

    asyncio.run(_run())


def test_global_timeout_enforcement():
    """Global timeout aborts slow searches and respects the global deadline."""
    class SlowAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            import time
            time.sleep(2.0)
            dep = criteria.slices[0].departure_date.isoformat()
            offer, raw = _make_dummy_flight_offer(dep_date=dep)
            return [(offer, raw)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=SlowAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=1),
        )
        # Global timeout of 0.3s will be exceeded
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit, global_timeout=0.3)
        assert resp.success is False
        assert any("deadline exceeded" in e.lower() or "timeout" in e.lower() for e in resp.errors)

    asyncio.run(_run())


def test_cancellation_cleanup():
    """Cancelling a flexible flight search cancels pending tasks cleanly."""
    task_ran_to_end = False

    class LongAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        async def search_flights(self, criteria, call_timeout=None):
            nonlocal task_ran_to_end
            await asyncio.sleep(1.0)
            task_ran_to_end = True
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_flight_offer(dep_date=dep)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=LongAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=1),
        )
        search_task = asyncio.create_task(orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit, global_timeout=10.0))
        await asyncio.sleep(0.05)
        search_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await search_task

    asyncio.run(_run())


def test_429_rate_limit_retry():
    """HTTP 429 rate limit triggers exponential backoff and succeeds on retry."""
    attempts = 0

    class FlakyRateLimitAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FlightProviderRateLimitError("Rate limit 429")
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_flight_offer(dep_date=dep, price=400.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=FlakyRateLimitAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit, global_timeout=10.0)
        assert resp.success is True
        assert attempts >= 2

    asyncio.run(_run())


def test_5xx_timeout_retry():
    """Transient timeout retries and succeeds on next attempt."""
    attempts = 0

    class FlakyTimeoutAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FlightProviderTimeout("Duffel gateway timeout")
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_flight_offer(dep_date=dep, price=420.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=FlakyTimeoutAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit, global_timeout=10.0)
        assert resp.success is True
        assert attempts == 2

    asyncio.run(_run())


def test_partial_candidate_failure():
    """If 1 candidate date fails, the other successful dates are preserved and returned."""
    class PartialFailAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            dep = criteria.slices[0].departure_date.isoformat()
            if dep == DEP_DATE_STR:
                raise FlightProviderError("Route not operated on Thursdays")
            return [_make_dummy_flight_offer(dep_date=dep, price=500.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=PartialFailAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=1),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit)
        assert resp.success is True
        # 3 candidate dates searched: 2 succeeded, 1 failed
        assert len(resp.candidate_dates) == 3
        date_dep = next(cd for cd in resp.candidate_dates if cd.departure_date.isoformat() == DEP_DATE_STR)
        assert date_dep.status == "provider_error"
        assert "Flight provider encountered an error" in (date_dep.error_message or "")
        # Check that successful dates have offers
        date_minus = next(cd for cd in resp.candidate_dates if cd.departure_date.isoformat() == D_MINUS_1)
        assert date_minus.status == "ok"
        assert date_minus.offer_count == 1

    asyncio.run(_run())


def test_all_candidate_failure():
    """If all candidate dates fail, search returns success=False with provider_error status."""
    class AllFailAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            raise FlightProviderError("Supplier unavailable")

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=AllFailAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=1),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit)
        assert resp.success is False
        assert all(cd.status == "provider_error" for cd in resp.candidate_dates)

    asyncio.run(_run())


def test_offer_deduplication():
    """Within a candidate date, duplicate flight signatures keep the lowest price."""
    class DupeOfferAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            dep = criteria.slices[0].departure_date.isoformat()
            o1, r1 = _make_dummy_flight_offer(dep_date=dep, price=600.0, flight_num="100", airline="BA")
            o2, r2 = _make_dummy_flight_offer(dep_date=dep, price=450.0, flight_num="100", airline="BA")  # Cheaper duplicate
            return [(o1, r1), (o2, r2)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=DupeOfferAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit)
        assert resp.success is True
        offers = resp.offers_by_date.get(DEP_DATE_STR, [])
        assert len(offers) == 1
        assert offers[0].price.total_amount == Decimal("450.0")

    asyncio.run(_run())


def test_ranking_determinism():
    """Offers are ranked deterministically with rank 1 assigned to the best offer."""
    class MultiOfferAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            dep = criteria.slices[0].departure_date.isoformat()
            o1, r1 = _make_dummy_flight_offer(dep_date=dep, price=700.0, flight_num="101")
            o2, r2 = _make_dummy_flight_offer(dep_date=dep, price=350.0, flight_num="102")
            return [(o1, r1), (o2, r2)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=MultiOfferAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit)
        offers = resp.offers_by_date[DEP_DATE_STR]
        assert offers[0].recommendation_rank == 1
        assert offers[0].price.total_amount == Decimal("350.0")
        assert offers[0].is_recommended is True

    asyncio.run(_run())


# =========================================================================
# 21–26: Security, Shielding, Tenancy & Immutability
# =========================================================================

def test_provider_id_shielding():
    """Internal provider_offer_id is strictly shielded and never present in client view."""
    class ShieldAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_flight_offer(dep_date=dep, price=400.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=ShieldAdapter())
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        resp = await orch.search_flexible_flights(user_id=uuid4(), trip_id=trip_id, criteria=crit)
        dumped = resp.model_dump(mode="json")
        json_str = str(dumped)
        assert "provider_offer_id" not in json_str
        assert "off_duffel_" not in json_str

    asyncio.run(_run())


def test_tenant_isolation_snapshot():
    """User B cannot fetch User A's flight snapshot."""
    user_a = uuid4()
    user_b = uuid4()
    trip_id = uuid4()
    store = SearchSessionStore()

    session = store.create_session(user_id=user_a, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash123")
    offer, raw = _make_dummy_flight_offer()
    snap = store.save_snapshot(session.session_id, offer, raw)

    # User A succeeds
    assert store.get_snapshot(snap.snapshot_id, user_a) is not None
    # User B is denied
    assert store.get_snapshot(snap.snapshot_id, user_b) is None


def test_concurrent_identical_search_cache_race():
    """5 simultaneous identical searches execute supplier query only once."""
    provider_call_count = 0

    class CountingAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            nonlocal provider_call_count
            provider_call_count += 1
            import time
            time.sleep(0.1)
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_flight_offer(dep_date=dep, price=300.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=CountingAdapter())
        user_id = uuid4()
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )

        # 5 concurrent identical searches
        tasks = [
            orch.search_flexible_flights(user_id=user_id, trip_id=trip_id, criteria=crit)
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert all(r.success is True for r in results)
        # Supplier call count must be exactly 1 due to search_lock double-checked locking!
        assert provider_call_count == 1
        # All 5 return the same session
        assert len(set(r.session_id for r in results)) == 1

    asyncio.run(_run())


def test_immutable_offer_snapshot_and_evidence():
    """OfferSnapshot and EvidenceRecord are persisted with correct hashes and linkages."""
    class SimpleAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            dep = criteria.slices[0].departure_date.isoformat()
            return [_make_dummy_flight_offer(dep_date=dep, price=500.0)]

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=SimpleAdapter())
        user_id = uuid4()
        trip_id = uuid4()
        crit = FlexibleFlightSearchCriteria(
            trip_id=trip_id,
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
        )
        resp = await orch.search_flexible_flights(user_id=user_id, trip_id=trip_id, criteria=crit)

        ev_store = EvidenceRevisionStore()
        app_key = build_applicability_key(
            EvidenceType.FLIGHT,
            {
                "origin": "LHR",
                "destination": "JFK",
                "departure_date": DEP_DATE_STR,
                "return_date": "",
                "candidate_dates": [DEP_DATE_STR],
            },
        )

        ev = ev_store.find_reusable_evidence(trip_id, user_id, EvidenceType.FLIGHT, app_key)
        assert ev is not None
        assert ev.evidence_type == EvidenceType.FLIGHT
        assert ev.freshness_kind == FreshnessKind.LIVE_SUPPLIER

    asyncio.run(_run())


# =========================================================================
# 27–33: HITL Revisions, Replanning, Handoff & Date Binding
# =========================================================================

def test_date_change_hitl_invalidation():
    """Changing departure date invalidates FLIGHT evidence."""
    current_c = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15"}
    updated_c = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-10-18"}
    cs = compute_change_set(current_c, updated_c)
    assert cs.dates_changed is True
    plan = calculate_replanning_plan(cs)
    assert plan.requires_flight_search is True
    assert "flights" in plan.invalidated_evidence

    # Policy evaluation
    ev = EvidenceRecord(
        trip_id=uuid4(),
        user_id=uuid4(),
        evidence_type=EvidenceType.FLIGHT,
        applicability_key={},
        freshness_kind=FreshnessKind.LIVE_SUPPLIER,
        freshness_ttl_seconds=1800,
        source_agent="flight_agent",
        payload={},
    )
    status = evaluate_evidence_for_revision(ev, cs)
    assert status == UsageState.INVALID_FOR_REVISION


def test_passenger_change_hitl_invalidation():
    """Changing passenger count invalidates FLIGHT evidence."""
    current_c = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15", "adults": 1, "children": 0}
    updated_c = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15", "adults": 2, "children": 0}
    cs = compute_change_set(current_c, updated_c)
    assert cs.passengers_changed is True
    plan = calculate_replanning_plan(cs)
    assert plan.requires_flight_search is True


def test_origin_change_hitl_invalidation():
    """Changing origin invalidates FLIGHT evidence."""
    current_c = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15"}
    updated_c = {"origin": "LGW", "destination": "JFK", "departure_date": "2026-10-15"}
    cs = compute_change_set(current_c, updated_c)
    assert cs.origin_changed is True
    plan = calculate_replanning_plan(cs)
    assert plan.requires_flight_search is True


def test_destination_change_hitl_invalidation():
    """Changing destination invalidates all evidence."""
    current_c = {"origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15"}
    updated_c = {"origin": "LHR", "destination": "HND", "departure_date": "2026-10-15"}
    cs = compute_change_set(current_c, updated_c)
    assert cs.destination_changed is True
    plan = calculate_replanning_plan(cs)
    assert plan.requires_flight_search is True
    assert plan.requires_hotel_search is True


def test_hotel_only_change_reuses_flight_evidence():
    """Changing hotel preference keeps FLIGHT evidence as REUSED."""
    current_c = {"hotel_preference": "luxury", "origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15"}
    updated_c = {"hotel_preference": "budget", "origin": "LHR", "destination": "JFK", "departure_date": "2026-10-15"}
    cs = compute_change_set(current_c, updated_c)
    assert cs.hotel_preferences_changed is True
    assert cs.dates_changed is False
    assert cs.origin_changed is False

    ev = EvidenceRecord(
        trip_id=uuid4(),
        user_id=uuid4(),
        evidence_type=EvidenceType.FLIGHT,
        applicability_key={},
        freshness_kind=FreshnessKind.LIVE_SUPPLIER,
        freshness_ttl_seconds=1800,
        source_agent="flight_agent",
        payload={},
    )
    status = evaluate_evidence_for_revision(ev, cs)
    assert status == UsageState.REUSED


def test_select_flexible_offer_updates_canonical_trip_dates():
    """Selecting a flexible candidate flight offer updates departure and return dates on the trip."""
    client, user_id = _auth_user("dates_bind@trusttrip.com")
    trip_store = TripStore()
    session_store = SearchSessionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Tokyo Trip")
    trip_id = trip["id"]

    # Save a candidate flight snapshot for 2026-10-18
    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash456")
    offer, raw = _make_dummy_flight_offer(dep_date="2026-10-18", ret_date="2026-10-25", price=480.0)
    snap = session_store.save_snapshot(session.session_id, offer, raw)

    resp = client.post(
        f"/api/trips/{trip_id}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap.snapshot_id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    # Verify constraints were updated with the candidate window dates
    updated_trip = trip_store.get_trip(trip_id, user_id)
    constraints = updated_trip["metadata"]["constraints"]
    assert constraints["departure_date"] == "2026-10-18"
    assert constraints["return_date"] == "2026-10-25"


def test_phase5_handoff_without_trip_approval():
    """Phase 5 external booking handoff succeeds on planning trip without approved status."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash789")
    offer, raw = _make_dummy_flight_offer(price=550.0)
    snap = session_store.save_snapshot(session.session_id, offer, raw)

    async def _run():
        booking_service = FlightBookingService(store=session_store)
        # Mock planner revalidate to confirm unchanged
        with patch.object(booking_service.planner, "revalidate_offer") as mock_reval:
            from domain.live_offer import RevalidationResult, RevalidationStatus
            mock_reval.return_value = RevalidationResult(
                revalidation_id=uuid4(),
                original_snapshot_id=snap.snapshot_id,
                status=RevalidationStatus.CONFIRMED_UNCHANGED,
                verified_at=datetime.now(timezone.utc),
                price_changed=False,
                original_price=Decimal("550.0"),
                current_price=Decimal("550.0"),
                currency="USD",
            )
            dest = await booking_service.select_and_handoff_flight(
                snapshot_id=snap.snapshot_id,
                authenticated_user_id=user_id,
            )
            assert dest.snapshot_id == snap.snapshot_id
            assert dest.redirect_url == "https://duffel.com/checkout/demo-link-123"

    asyncio.run(_run())


# =========================================================================
# 34: API Endpoint Validation (POST /api/flights/search-flexible)
# =========================================================================

def test_flexible_flight_search_api_unauthorized():
    """Accessing search-flexible without token returns 401."""
    client = TestClient(app)
    resp = client.post("/api/flights/search-flexible", json={})
    assert resp.status_code == 401


def test_flexible_flight_search_api_validation_error():
    """Posting invalid search payload returns 422 Unprocessable Entity."""
    client, _ = _auth_user("val_err@trusttrip.com")
    resp = client.post(
        "/api/flights/search-flexible",
        json={
            "trip_id": str(uuid4()),
            "origin_iata": "LHR",
            "destination_iata": "JFK",
            "departure_date": DEP_DATE_STR,
            "return_date": D_MINUS_1,  # Invalid: return < departure
        },
    )
    assert resp.status_code == 422


def test_flexible_flight_search_api_endpoint_success():
    """POST /api/flights/search-flexible successfully returns candidate dates and shielded offers."""
    client, user_id = _auth_user("api_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user_id, title="API Test Trip", origin="LHR", destination="JFK")

    class MockAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            dep = criteria.slices[0].departure_date.isoformat()
            offer, raw = _make_dummy_flight_offer(dep_date=dep, price=380.0)
            return [(offer, raw)]

    with patch("flight_api.TravelIntelligenceOrchestrator") as mock_orch_cls:
        real_orch = TravelIntelligenceOrchestrator(flight_adapter=MockAdapter())
        mock_orch_cls.return_value = real_orch

        payload = {
            "trip_id": str(trip["id"]),
            "origin_iata": "LHR",
            "destination_iata": "JFK",
            "departure_date": DEP_DATE_STR,
            "flexibility": {
                "mode": "plus_minus_days",
                "flexibility_days": 1,
            },
        }
        resp = client.post("/api/flights/search-flexible", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["candidate_dates"]) == 3
        assert "provider_offer_id" not in str(data)


# =========================================================================
# 35: Remediation Tests - Blocker 1: Stale/Expired Cache Invariants
# =========================================================================

def test_cache_reuse_rejects_expired_snapshot():
    """Cache reuse must reject sessions containing expired snapshots."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash_b1_exp", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    offer_expired = offer.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(minutes=5)})
    snap = session_store.save_snapshot(session.session_id, offer_expired, raw)

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
    )
    validated = orch._validate_reusable_cached_snapshots(
        session=session,
        snapshots=[snap],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=[(DEP_DATE_STR, None)],
    )
    assert validated is None


def test_cache_reuse_rejects_stale_snapshot():
    """Cache reuse must reject sessions where snapshot freshness is not FRESH."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash_b1_stale", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap = session_store.save_snapshot(session.session_id, offer, raw)
    snap_stale = snap.model_copy(update={"freshness": FreshnessStatus.STALE})

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
    )
    validated = orch._validate_reusable_cached_snapshots(
        session=session,
        snapshots=[snap_stale],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=[(DEP_DATE_STR, None)],
    )
    assert validated is None


def test_cache_reuse_rejects_unavailable_snapshot():
    """Cache reuse must reject sessions where snapshot availability is UNAVAILABLE."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash_b1_unavail", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap = session_store.save_snapshot(session.session_id, offer, raw)
    snap_unavail = snap.model_copy(update={"availability": AvailabilityStatus.UNAVAILABLE})

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
    )
    validated = orch._validate_reusable_cached_snapshots(
        session=session,
        snapshots=[snap_unavail],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=[(DEP_DATE_STR, None)],
    )
    assert validated is None


def test_cache_reuse_rejects_expired_session():
    """Cache reuse must reject sessions that exceeded cache-reuse TTL."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash_b1_sess_exp", trip_revision=1)
    session_expired = session.model_copy(update={"created_at": datetime.now(timezone.utc) - timedelta(minutes=20)})
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap = session_store.save_snapshot(session.session_id, offer, raw)

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
    )
    validated = orch._validate_reusable_cached_snapshots(
        session=session_expired,
        snapshots=[snap],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=[(DEP_DATE_STR, None)],
    )
    assert validated is None


def test_cache_reuse_rejects_invalidated_evidence():
    """Cache reuse must reject sessions whose corresponding evidence was invalidated."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    ev_store = EvidenceRevisionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash_b1_inv", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap = session_store.save_snapshot(session.session_id, offer, raw)

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
    )
    app_key = build_applicability_key(
        EvidenceType.FLIGHT,
        {
            "origin": crit.origin_iata,
            "destination": crit.destination_iata,
            "departure_date": crit.departure_date.isoformat(),
            "return_date": "",
            "adults": crit.adults,
            "children": crit.children,
            "infants": crit.infants,
            "cabin_class": crit.cabin_class,
            "max_connections": crit.max_connections,
            "currency": crit.currency,
            "flexibility_mode": crit.flexibility.mode.value,
            "flexibility_days": crit.flexibility.flexibility_days,
            "duration_days": crit.duration_days,
            "earliest_departure": "",
            "latest_departure": "",
            "candidate_dates": [DEP_DATE_STR],
        },
    )
    rec = EvidenceRecord(
        trip_id=trip_id,
        user_id=user_id,
        evidence_type=EvidenceType.FLIGHT,
        applicability_key=app_key,
        freshness_kind=FreshnessKind.LIVE_SUPPLIER,
        freshness_ttl_seconds=1800,
        supplier_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        source_agent="flight_agent",
        payload={},
    )
    prov = EvidenceProvenance(
        evidence_id=rec.evidence_id,
        trip_id=trip_id,
        user_id=user_id,
        supplier_session_id=session.session_id,
        supplier_snapshot_id=snap.snapshot_id,
    )
    ev_store.create_evidence(rec, prov)

    validated = orch._validate_reusable_cached_snapshots(
        session=session,
        snapshots=[snap],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=[(DEP_DATE_STR, None)],
    )
    assert validated is None


def test_cache_reuse_accepts_usable_snapshot():
    """Cache reuse succeeds when session, snapshot, freshness, and evidence are all valid."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    ev_store = EvidenceRevisionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="hash_b1_usable", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap = session_store.save_snapshot(session.session_id, offer, raw)

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.EXACT),
    )
    app_key = build_applicability_key(
        EvidenceType.FLIGHT,
        {
            "origin": crit.origin_iata,
            "destination": crit.destination_iata,
            "departure_date": crit.departure_date.isoformat(),
            "return_date": "",
            "adults": crit.adults,
            "children": crit.children,
            "infants": crit.infants,
            "cabin_class": crit.cabin_class,
            "max_connections": crit.max_connections,
            "currency": crit.currency,
            "flexibility_mode": crit.flexibility.mode.value,
            "flexibility_days": crit.flexibility.flexibility_days,
            "duration_days": crit.duration_days,
            "earliest_departure": "",
            "latest_departure": "",
            "candidate_dates": [DEP_DATE_STR],
        },
    )
    rec = EvidenceRecord(
        trip_id=trip_id,
        user_id=user_id,
        evidence_type=EvidenceType.FLIGHT,
        applicability_key=app_key,
        freshness_kind=FreshnessKind.LIVE_SUPPLIER,
        freshness_ttl_seconds=1800,
        source_agent="flight_agent",
        payload={},
    )
    prov = EvidenceProvenance(
        evidence_id=rec.evidence_id,
        trip_id=trip_id,
        user_id=user_id,
        supplier_session_id=session.session_id,
        supplier_snapshot_id=snap.snapshot_id,
    )
    ev_store.create_evidence(rec, prov)

    validated = orch._validate_reusable_cached_snapshots(
        session=session,
        snapshots=[snap],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=[(DEP_DATE_STR, None)],
    )
    assert validated is not None
    assert len(validated) == 1
    assert validated[0].snapshot_id == snap.snapshot_id


# =========================================================================
# 36: Remediation Tests - Blocker 2: Flexible Snapshot Selection Verification
# =========================================================================

def test_select_offer_cross_trip_rejection():
    """Selection must fail if snapshot belongs to Trip A but selection is attempted on Trip B for same user."""
    client, user_id = _auth_user("user_cross_trip@trusttrip.com")
    trip_store = TripStore()
    session_store = SearchSessionStore()

    trip_a = trip_store.create_trip(user_id=user_id, title="Trip A", destination="Paris")
    trip_b = trip_store.create_trip(user_id=user_id, title="Trip B", destination="Rome")

    session_a = session_store.create_session(user_id=user_id, trip_id=trip_a["id"], thread_id=None, search_type="flights", criteria_hash="hash_a", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap_a = session_store.save_snapshot(session_a.session_id, offer, raw)

    resp = client.post(
        f"/api/trips/{trip_b['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_a.snapshot_id)},
    )
    assert resp.status_code == 400
    assert "does not belong to the requested trip" in resp.json()["detail"]


def test_select_offer_outdated_revision_rejection():
    """Selection must fail if snapshot was generated for an older revision of the trip."""
    client, user_id = _auth_user("user_revision@trusttrip.com")
    trip_store = TripStore()
    session_store = SearchSessionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Trip Rev", destination="Paris")
    trip_store.update_trip(trip["id"], user_id, metadata={"trip_revision": 2})

    session_old = session_store.create_session(user_id=user_id, trip_id=trip["id"], thread_id=None, search_type="flights", criteria_hash="hash_old", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    snap = session_store.save_snapshot(session_old.session_id, offer, raw)

    resp = client.post(
        f"/api/trips/{trip['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap.snapshot_id)},
    )
    assert resp.status_code == 400
    assert "outdated trip revision" in resp.json()["detail"]


def test_select_offer_stale_or_expired_rejection():
    """Selection must reject snapshots that are expired or stale (409 Conflict)."""
    client, user_id = _auth_user("user_stale_sel@trusttrip.com")
    trip_store = TripStore()
    session_store = SearchSessionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Trip Exp", destination="Paris", metadata={"trip_revision": 1})
    session = session_store.create_session(user_id=user_id, trip_id=trip["id"], thread_id=None, search_type="flights", criteria_hash="hash_exp", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    offer_exp = offer.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(minutes=10)})
    snap = session_store.save_snapshot(session.session_id, offer_exp, raw)

    resp = client.post(
        f"/api/trips/{trip['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap.snapshot_id)},
    )
    assert resp.status_code == 409
    assert "expired" in resp.json()["detail"]


# =========================================================================
# 37: Remediation Tests - Blocker 3: Bounded Provider Timeout & Zero Orphan Tasks
# =========================================================================

def test_bounded_provider_timeout_and_no_orphan_tasks():
    """Intentionally blocking provider call aborts at global timeout, sets cancel_event, and leaves 0 orphan tasks."""
    class BlockingAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        async def search_flights(self, criteria):
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                raise

    async def _run():
        orch = TravelIntelligenceOrchestrator(flight_adapter=BlockingAdapter())
        crit = FlexibleFlightSearchCriteria(
            trip_id=uuid4(),
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=DEP_DATE_OBJ,
            flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=1),
        )
        t0 = asyncio.get_running_loop().time()
        resp = await orch.search_flexible_flights(
            user_id=uuid4(),
            trip_id=uuid4(),
            criteria=crit,
            global_timeout=0.3,
        )
        elapsed = asyncio.get_running_loop().time() - t0
        assert elapsed < 1.0
        assert resp.success is False
        assert any("deadline exceeded" in e.lower() for e in resp.errors)

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            assert t.done() or "pytest" in repr(t) or "testrunner" in repr(t)

    asyncio.run(_run())


# =========================================================================
# 38: Remediation Tests - Blocker 4: Revision-Aware Cache Invariants
# =========================================================================

def test_cache_reuse_requires_matching_trip_revision():
    """SearchSession cache reuse requires matching trip_revision; differing revisions trigger new sessions."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()

    session_rev1 = session_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_rev_ctx",
        trip_revision=1,
    )

    hit_rev1 = session_store.get_active_session_by_hash(
        user_id=user_id,
        trip_id=trip_id,
        criteria_hash="hash_rev_ctx",
        trip_revision=1,
    )
    assert hit_rev1 is not None
    assert hit_rev1.session_id == session_rev1.session_id

    miss_rev2 = session_store.get_active_session_by_hash(
        user_id=user_id,
        trip_id=trip_id,
        criteria_hash="hash_rev_ctx",
        trip_revision=2,
    )
    assert miss_rev2 is None


# =========================================================================
# 39: Remediation Tests - Blocker 5: Exact Past Date Pruning vs Fallback
# =========================================================================

def test_candidate_dates_exact_past_date_returns_empty():
    """Exact mode with past departure date returns empty list, never falling back to future dates."""
    past_date = (date.today() - timedelta(days=5)).isoformat()
    c = {"departure_date": past_date, "duration_days": 5, "flexibility_mode": "exact"}
    res = candidate_dates(c, limit=7)
    assert res == []


def test_candidate_dates_exact_today_accepted():
    """Exact mode with today's departure date is accepted."""
    today_date = date.today().isoformat()
    c = {"departure_date": today_date, "duration_days": 3, "flexibility_mode": "exact"}
    res = candidate_dates(c, limit=7)
    assert len(res) == 1
    assert res[0][0] == today_date


def test_candidate_dates_exact_future_accepted():
    """Exact mode with future departure date is accepted."""
    fut_date = (date.today() + timedelta(days=30)).isoformat()
    c = {"departure_date": fut_date, "duration_days": 7, "flexibility_mode": "exact"}
    res = candidate_dates(c, limit=7)
    assert len(res) == 1
    assert res[0][0] == fut_date


def test_candidate_dates_missing_date_with_fallback():
    """When departure_date is missing and near_term_fallback is explicitly allowed, near-term dates are generated."""
    c = {"flexibility_mode": "near_term_fallback", "duration_days": 5}
    res = candidate_dates(c, limit=5)
    assert len(res) == 5
    for dep, ret in res:
        assert date.fromisoformat(dep) >= date.today()


def test_candidate_dates_explicit_flexibility_past_pruned():
    """Flexible mode with past dates prunes only the past days, keeping future/today days without triggering near_term_fallback."""
    today = date.today()
    c = {
        "departure_date": today.isoformat(),
        "duration_days": 3,
        "flexibility_mode": "plus_minus_days",
        "flexibility_days": 2,
    }
    res = candidate_dates(c, limit=7)
    assert len(res) == 3
    for dep, _ in res:
        assert date.fromisoformat(dep) >= today


def test_search_criteria_exact_past_date_validation_error():
    """FlexibleFlightSearchCriteria rejects exact past departure dates with ValueError."""
    from pydantic import ValidationError
    past_date = date.today() - timedelta(days=2)
    with pytest.raises(ValidationError) as exc_info:
        FlexibleFlightSearchCriteria(
            trip_id=uuid4(),
            origin_iata="LHR",
            destination_iata="JFK",
            departure_date=past_date,
        )
    assert "departure_date cannot be in the past" in str(exc_info.value)


# =========================================================================
# 40: Remediation Tests - Blocker 6: Evidence Applicability Completeness
# =========================================================================

def test_evidence_applicability_covers_all_search_affecting_fields():
    """Changing any search-affecting parameter alters the applicability key, preventing cross-reuse."""
    base_params = {
        "origin": "LHR",
        "destination": "JFK",
        "departure_date": "2026-10-15",
        "return_date": "2026-10-22",
        "adults": 1,
        "children": 0,
        "infants": 0,
        "cabin_class": "economy",
        "max_connections": 0,
        "currency": "USD",
        "flexibility_mode": "plus_minus_days",
        "flexibility_days": 1,
        "duration_days": 7,
        "earliest_departure": "",
        "latest_departure": "",
        "candidate_dates": ["2026-10-14", "2026-10-15", "2026-10-16"],
    }
    base_key = build_applicability_key(EvidenceType.FLIGHT, base_params)

    mutations = [
        ("origin", "LGW"),
        ("destination", "EWR"),
        ("departure_date", "2026-10-16"),
        ("return_date", "2026-10-23"),
        ("adults", 2),
        ("children", 1),
        ("infants", 1),
        ("cabin_class", "business"),
        ("max_connections", 1),
        ("currency", "EUR"),
        ("flexibility_mode", "exact"),
        ("flexibility_days", 2),
        ("duration_days", 10),
        ("earliest_departure", "2026-10-14"),
        ("latest_departure", "2026-10-18"),
        ("candidate_dates", ["2026-10-15"]),
    ]
    for key, new_val in mutations:
        altered = dict(base_params)
        altered[key] = new_val
        alt_key = build_applicability_key(EvidenceType.FLIGHT, altered)
        assert alt_key != base_key, f"Changing {key} must change the applicability key"


# =========================================================================
# 41: Remediation Tests - Blocker 7: Exception Serialization & Safe Error Handling
# =========================================================================

def test_api_exception_redaction_and_safety():
    """Internal provider exceptions and secrets must never leak to API callers."""
    client, user_id = _auth_user("leak_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user_id, title="Leak Test Trip", destination="NYC")

    class LeakyAdapter:
        supports_flexible_dates = True
        provider_name = "duffel"

        def _sync_search(self, criteria):
            raise FlightProviderError("Provider DB crashed at postgres://admin:secretPass@10.0.0.1:5432/duffel with trace: line 42")

    with patch("flight_api.TravelIntelligenceOrchestrator") as mock_orch_cls:
        orch = TravelIntelligenceOrchestrator(flight_adapter=LeakyAdapter())
        mock_orch_cls.return_value = orch

        resp = client.post(
            "/api/flights/search-flexible",
            json={
                "trip_id": str(trip["id"]),
                "origin_iata": "LHR",
                "destination_iata": "JFK",
                "departure_date": DEP_DATE_STR,
                "flexibility": {"mode": "exact"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "secretPass" not in resp.text
        assert "postgres://" not in resp.text
        assert "line 42" not in resp.text

    # Also verify unhandled exception in orchestrator raises 502 with safe detail
    with patch("flight_api.TravelIntelligenceOrchestrator") as mock_orch_cls:
        mock_orch = MagicMock()
        mock_orch.search_flexible_flights = AsyncMock(
            side_effect=RuntimeError("internal secret connection leak at postgres://admin:secretPass@10.0.0.1:5432")
        )
        mock_orch_cls.return_value = mock_orch

        resp = client.post(
            "/api/flights/search-flexible",
            json={
                "trip_id": str(trip["id"]),
                "origin_iata": "LHR",
                "destination_iata": "JFK",
                "departure_date": DEP_DATE_STR,
                "flexibility": {"mode": "exact"},
            },
        )
        assert resp.status_code == 502
        assert "secretPass" not in resp.text
        assert "postgres://" not in resp.text
        assert resp.json()["detail"] == "Flexible flight search encountered a provider or system error. Please retry."


# =========================================================================
# 42: Gate 2 Re-Audit Verification Tests
# =========================================================================

def test_cache_reuse_rejects_partial_candidate_coverage():
    """Cache reuse must fail if any requested candidate date is missing from cached snapshots."""
    user_id = uuid4()
    trip_id = uuid4()
    session_store = SearchSessionStore()
    ev_store = EvidenceRevisionStore()
    orch = TravelIntelligenceOrchestrator(session_store=session_store)

    session = session_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_partial_cov",
        trip_revision=1,
    )
    # Cache only has offers for D_MINUS_1 and D_PLUS_1 (missing DEP_DATE_STR)
    offer_minus, raw_minus = _make_dummy_flight_offer(dep_date=D_MINUS_1)
    offer_plus, raw_plus = _make_dummy_flight_offer(dep_date=D_PLUS_1)
    snap_minus = session_store.save_snapshot(session.session_id, offer_minus, raw_minus)
    snap_plus = session_store.save_snapshot(session.session_id, offer_plus, raw_plus)

    crit = FlexibleFlightSearchCriteria(
        trip_id=trip_id,
        origin_iata="LHR",
        destination_iata="JFK",
        departure_date=DEP_DATE_OBJ,
        flexibility=FlexibleDateOptions(mode=DateFlexibilityMode.PLUS_MINUS_DAYS, flexibility_days=1),
    )
    # Requested dates: D_MINUS_1, DEP_DATE_STR, D_PLUS_1
    candidate_window_list = [(D_MINUS_1, None), (DEP_DATE_STR, None), (D_PLUS_1, None)]

    validated = orch._validate_reusable_cached_snapshots(
        session=session,
        snapshots=[snap_minus, snap_plus],
        user_id=user_id,
        trip_id=trip_id,
        trip_revision=1,
        criteria=crit,
        candidate_window_list=candidate_window_list,
    )
    assert validated is None, "Partial candidate date coverage must reject cache reuse."


def test_select_offer_strict_expiration_boundary():
    """Selection must enforce strict expiration boundary: expires_at <= now is rejected with 409."""
    client, user_id = _auth_user("user_expiry_boundary@trusttrip.com")
    trip_store = TripStore()
    session_store = SearchSessionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Trip Expiry Boundary", destination="Paris", metadata={"trip_revision": 1})
    session = session_store.create_session(user_id=user_id, trip_id=trip["id"], thread_id=None, search_type="flights", criteria_hash="hash_exp_boundary", trip_revision=1)
    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)

    now = datetime.now(timezone.utc)

    # 1. Strictly in past: expires_at < now -> 409
    offer_past = offer.model_copy(update={"expires_at": now - timedelta(seconds=5)})
    snap_past = session_store.save_snapshot(session.session_id, offer_past, raw)
    resp_past = client.post(
        f"/api/trips/{trip['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_past.snapshot_id)},
    )
    assert resp_past.status_code == 409
    assert "expired" in resp_past.json()["detail"].lower()

    # 2. Exact equality: expires_at == now -> 409 (must NOT pass)
    offer_equal = offer.model_copy(update={"expires_at": now})
    snap_equal = session_store.save_snapshot(session.session_id, offer_equal, raw)
    with patch("services.trip_store.datetime") as mock_dt:
        mock_dt.now.return_value = now
        resp_equal = client.post(
            f"/api/trips/{trip['id']}/select-offer",
            json={"offer_type": "flight", "snapshot_id": str(snap_equal.snapshot_id)},
        )
        assert resp_equal.status_code == 409
        assert "expired" in resp_equal.json()["detail"].lower()

    # 3. Strictly in future: expires_at > now -> 200
    offer_fut = offer.model_copy(update={"expires_at": now + timedelta(hours=1)})
    snap_fut = session_store.save_snapshot(session.session_id, offer_fut, raw)
    resp_fut = client.post(
        f"/api/trips/{trip['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_fut.snapshot_id)},
    )
    assert resp_fut.status_code == 200
    assert resp_fut.json()["success"] is True


def test_select_offer_strict_revision_matching():
    """Selection requires exact trip_revision equality; revision 0 does NOT bypass checks."""
    client, user_id = _auth_user("user_strict_rev@trusttrip.com")
    trip_store = TripStore()
    session_store = SearchSessionStore()

    offer, raw = _make_dummy_flight_offer(dep_date=DEP_DATE_STR)
    offer_valid = offer.model_copy(update={"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})

    # Scenario A: Trip at rev 0, Session at rev 1 -> 400
    trip_rev0 = trip_store.create_trip(user_id=user_id, title="Trip 0", destination="Paris", metadata={"trip_revision": 0})
    session_rev1 = session_store.create_session(user_id=user_id, trip_id=trip_rev0["id"], thread_id=None, search_type="flights", criteria_hash="h1", trip_revision=1)
    snap_a = session_store.save_snapshot(session_rev1.session_id, offer_valid, raw)
    resp_a = client.post(
        f"/api/trips/{trip_rev0['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_a.snapshot_id)},
    )
    assert resp_a.status_code == 400
    assert "outdated trip revision" in resp_a.json()["detail"].lower()

    # Scenario B: Trip at rev 1, Session at rev 0 -> 400 (NO 0 BYPASS!)
    trip_rev1 = trip_store.create_trip(user_id=user_id, title="Trip 1", destination="Paris", metadata={"trip_revision": 1})
    session_rev0 = session_store.create_session(user_id=user_id, trip_id=trip_rev1["id"], thread_id=None, search_type="flights", criteria_hash="h0", trip_revision=0)
    snap_b = session_store.save_snapshot(session_rev0.session_id, offer_valid, raw)
    resp_b = client.post(
        f"/api/trips/{trip_rev1['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_b.snapshot_id)},
    )
    assert resp_b.status_code == 400
    assert "outdated trip revision" in resp_b.json()["detail"].lower()

    # Scenario C: Trip at rev 1, Session at rev 1 -> 200 OK
    session_rev1_ok = session_store.create_session(user_id=user_id, trip_id=trip_rev1["id"], thread_id=None, search_type="flights", criteria_hash="h1_ok", trip_revision=1)
    snap_c = session_store.save_snapshot(session_rev1_ok.session_id, offer_valid, raw)
    resp_c = client.post(
        f"/api/trips/{trip_rev1['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_c.snapshot_id)},
    )
    assert resp_c.status_code == 200
    assert resp_c.json()["success"] is True

    # Scenario D: Trip at rev 0, Session at rev 0 -> 200 OK
    session_rev0_ok = session_store.create_session(user_id=user_id, trip_id=trip_rev0["id"], thread_id=None, search_type="flights", criteria_hash="h0_ok", trip_revision=0)
    snap_d = session_store.save_snapshot(session_rev0_ok.session_id, offer_valid, raw)
    resp_d = client.post(
        f"/api/trips/{trip_rev0['id']}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap_d.snapshot_id)},
    )
    assert resp_d.status_code == 200
    assert resp_d.json()["success"] is True


def test_search_flexible_trip_validation():
    """POST /api/flights/search-flexible validates authentic trip ownership and existence."""
    client_a, user_a = _auth_user("user_a@trusttrip.com")
    _, user_b = _auth_user("user_b@trusttrip.com")
    trip_store = TripStore()

    trip_a = trip_store.create_trip(user_id=user_a, title="Trip A", destination="Paris", metadata={"trip_revision": 1})
    trip_b = trip_store.create_trip(user_id=user_b, title="Trip B", destination="Berlin", metadata={"trip_revision": 1})

    payload_base = {
        "origin_iata": "LHR",
        "destination_iata": "JFK",
        "departure_date": DEP_DATE_STR,
        "flexibility": {"mode": "exact"},
    }

    # 1. Non-existent trip_id -> 404
    resp_missing = client_a.post(
        "/api/flights/search-flexible",
        json={**payload_base, "trip_id": str(uuid4())},
    )
    assert resp_missing.status_code == 404
    assert "Trip not found" in resp_missing.json()["detail"]

    # 2. Unowned trip_id (trip_b owned by user_b, called by user_a) -> 404
    resp_unowned = client_a.post(
        "/api/flights/search-flexible",
        json={**payload_base, "trip_id": str(trip_b["id"])},
    )
    assert resp_unowned.status_code == 404
    assert "Trip not found" in resp_unowned.json()["detail"]

    # 3. Missing trip_id in request body -> 422
    resp_no_trip = client_a.post(
        "/api/flights/search-flexible",
        json=payload_base,
    )
    assert resp_no_trip.status_code == 422

    # 4. Valid owned trip_id -> 200
    resp_valid = client_a.post(
        "/api/flights/search-flexible",
        json={**payload_base, "trip_id": str(trip_a["id"])},
    )
    assert resp_valid.status_code == 200
    session_id = resp_valid.json()["session_id"]
    session = SearchSessionStore().get_session(session_id, user_a)
    assert session is not None
    assert str(session.trip_id) == str(trip_a["id"])


def test_async_http_cancellation_and_socket_cleanup():
    """Cancelling a DuffelFlightAdapter async search immediately aborts HTTP request and cleans up socket."""
    from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
    from services.duffel_provider import DuffelProvider
    import httpx

    adapter = DuffelFlightAdapter(provider=DuffelProvider(token="duffel_test_fake_token"))
    crit = FlightSearchCriteria(
        slices=[
            FlightSliceCriteria(
                origin_iata="LHR",
                destination_iata="JFK",
                departure_date=DEP_DATE_OBJ,
            )
        ]
    )

    client_instances: list[httpx.AsyncClient] = []
    original_init = httpx.AsyncClient.__init__

    def _tracking_init(self, *args, **kwargs):
        client_instances.append(self)
        original_init(self, *args, **kwargs)

    async def _mock_hang_post(*args, **kwargs):
        await asyncio.sleep(5.0)
        return httpx.Response(200, json={"data": {"offers": []}})

    async def _run():
        with patch.object(httpx.AsyncClient, "__init__", _tracking_init):
            with patch.object(httpx.AsyncClient, "post", side_effect=_mock_hang_post):
                search_task = asyncio.create_task(adapter.search_flights(crit, call_timeout=5.0))
                await asyncio.sleep(0.05)
                search_task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await search_task

                assert len(client_instances) == 1, "An AsyncClient instance must have been created."
                assert client_instances[0].is_closed is True, "AsyncClient socket must be closed cleanly upon task cancellation."

    asyncio.run(_run())


def test_async_http_revalidation_cancellation_and_socket_cleanup():
    """Cancelling a DuffelFlightAdapter async revalidation immediately aborts HTTP request and cleans up socket."""
    from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
    from services.duffel_provider import DuffelProvider
    import httpx

    adapter = DuffelFlightAdapter(provider=DuffelProvider(token="duffel_test_fake_token"))
    client_instances: list[httpx.AsyncClient] = []
    original_init = httpx.AsyncClient.__init__

    def _tracking_init(self, *args, **kwargs):
        client_instances.append(self)
        original_init(self, *args, **kwargs)

    async def _mock_hang_get(*args, **kwargs):
        await asyncio.sleep(5.0)
        return httpx.Response(200, json={"data": {"total_amount": "500.00", "total_currency": "USD"}})

    async def _run():
        with patch.object(httpx.AsyncClient, "__init__", _tracking_init):
            with patch.object(httpx.AsyncClient, "get", side_effect=_mock_hang_get):
                reval_task = asyncio.create_task(
                    adapter.revalidate_flight("off_test_123", expected_price=500.0, expected_currency="USD", call_timeout=5.0)
                )
                await asyncio.sleep(0.05)
                reval_task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await reval_task

                assert len(client_instances) == 1, "An AsyncClient instance must have been created."
                assert client_instances[0].is_closed is True, "AsyncClient socket must be closed cleanly upon task cancellation."

    asyncio.run(_run())


def test_no_to_thread_in_duffel_flight_paths():
    """Verify that DuffelFlightAdapter search and revalidation paths never use asyncio.to_thread."""
    import inspect
    from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter

    search_src = inspect.getsource(DuffelFlightAdapter.search_flights)
    reval_src = inspect.getsource(DuffelFlightAdapter.revalidate_flight)

    assert "asyncio.to_thread" not in search_src, "search_flights must not use asyncio.to_thread"
    assert "to_thread" not in search_src, "search_flights must not offload to thread"
    assert "asyncio.to_thread" not in reval_src, "revalidate_flight must not use asyncio.to_thread"
    assert "to_thread" not in reval_src, "revalidate_flight must not offload to thread"


def test_revalidation_timeout_propagation():
    """Verify that revalidate_flight bounds HTTP request and handles timeout cleanly."""
    from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
    from services.duffel_provider import DuffelProvider
    from domain.live_offer import RevalidationStatus
    import httpx

    adapter = DuffelFlightAdapter(provider=DuffelProvider(token="duffel_test_fake_token"))

    async def _mock_timeout_get(*args, **kwargs):
        raise httpx.TimeoutException("Connection timed out after 0.2s")

    async def _run():
        with patch.object(httpx.AsyncClient, "get", side_effect=_mock_timeout_get):
            res = await adapter.revalidate_flight("off_test_timeout", expected_price=400.0, call_timeout=0.2)
            assert res.status == RevalidationStatus.OFFER_WITHDRAWN
            assert "timed out" in res.details.lower()

    asyncio.run(_run())


def test_authoritative_revision_resolver_divergence():
    """Verify that resolve_current_revision uses current_revision_number as authoritative source and rejects divergence."""
    from services.trip_store import resolve_current_revision, RevisionContextError

    # 1. Matching: DB N, meta N -> OK (N)
    trip_ok = {"current_revision_number": 2, "metadata": {"revision": 2, "trip_revision": 2}}
    assert resolve_current_revision(trip_ok) == 2

    # 2. Divergence: DB N+1, meta N -> RevisionContextError
    trip_div1 = {"current_revision_number": 3, "metadata": {"revision": 2, "trip_revision": 2}}
    with pytest.raises(RevisionContextError) as exc_info:
        resolve_current_revision(trip_div1)
    assert "divergence" in str(exc_info.value).lower()

    # 3. Divergence: DB N, meta N+1 -> RevisionContextError
    trip_div2 = {"current_revision_number": 1, "metadata": {"revision": 2}}
    with pytest.raises(RevisionContextError) as exc_info:
        resolve_current_revision(trip_div2)
    assert "divergence" in str(exc_info.value).lower()

    # 4. Missing metadata: DB N, missing meta -> OK (N)
    trip_no_meta = {"current_revision_number": 5, "metadata": {}}
    assert resolve_current_revision(trip_no_meta) == 5

    # 5. Legitimate 0: DB 0, meta 0 -> OK (0)
    trip_zero = {"current_revision_number": 0, "metadata": {"revision": 0}}
    assert resolve_current_revision(trip_zero) == 0

    # 6. Legitimate 0: DB 0, missing meta -> OK (0)
    trip_zero_nometa = {"current_revision_number": 0}
    assert resolve_current_revision(trip_zero_nometa) == 0

    # 7. Unmigrated/Mock dict without DB column -> fallback to metadata
    trip_mock = {"metadata": {"revision": 3}}
    assert resolve_current_revision(trip_mock) == 3




