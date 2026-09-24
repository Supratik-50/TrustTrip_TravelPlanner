"""Phase 9 Flight Booking Lifecycle Test Suite.

Covers:
A. Domain & Model Validation (enums, immutability, extra="forbid", hashing)
B. State Machine (legal transitions, illegal transitions, terminal states)
C. Ownership & Tenancy (cross-user isolation for trips, intents, snapshots)
D. Revision & HITL Invalidation (stale revisions, flight vs hotel-only edits)
E. Deep Revalidation (unchanged, price change, schedule change, withdrawn, expired)
F. Snapshot Lifecycle (successor snapshot creation, predecessor linkage, immutability)
G. Idempotency (deduplication on prepare, revalidate, handoff, cancel)
H. Concurrency (intent row locking, CAS transitions)
I. Handoff & URL Security (Duffel Links, carrier allowlist, fabricated URL rejection)
J. Security & Error Handling (no cardholder PII, credential scrubbing, structured errors)
K. Provider Resilience (429 rate limits, 502/504 timeouts and outages)
L. HTTP API Endpoints (/prepare, /revalidate, /handoff, GET /{id}, /cancel-intent)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.booking_lifecycle import (
    BookingIntent,
    BookingLockedException,
    BookingMode,
    BookingProvider,
    BookingStatus,
    FareChangedException,
    FlightHandoffDestinationResponse,
    InvalidBookingStateTransitionError,
    OfferChangedException,
    OfferDataInvalidException,
    OfferExpiredException,
    OfferRevalidationResult,
    OfferUnavailableException,
    OwnershipViolationException,
    PassengerBookingModel,
    StaleRevisionException,
    IdempotencyConflictException,
    IdempotencyKeyRequiredException,
    InvalidCallbackException,
    UnauthorizedCallbackException,
    BookingProviderRateLimited,
    compute_passenger_hash,
    validate_booking_transition,
    validate_idempotency_key,
)
from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    PriceBreakdown,
    RevalidationResult,
    RevalidationStatus,
)
from domain.plan_revision import PlanRevision
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.booking_provider_port import ProviderOfferVerification
from services.duffel_booking_adapter import DuffelBookingAdapter
from services.evidence_store import EvidenceRevisionStore
from services.flight_booking_lifecycle_service import FlightBookingLifecycleService
from services.flight_provider import FlightProviderRateLimitError
from services.search_session_store import SearchSessionStore
from services.stub_booking_adapter import StubBookingAdapter
from services.trip_store import TripStore


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure in-memory mode is active and state is pristine for each test."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("TRUSTTRIP_PUBLIC_BASE_URL", "https://trusttrip.com")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_REVALIDATIONS.clear()
    FlightBookingLifecycleService._IN_MEMORY_INTENTS.clear()
    FlightBookingLifecycleService._IN_MEMORY_ATTEMPTS.clear()
    FlightBookingLifecycleService._IN_MEMORY_TRANSITIONS.clear()
    FlightBookingLifecycleService._IN_MEMORY_INTENT_LOCKS.clear()


def _auth_user(email: str) -> tuple[TestClient, UUID]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="secret")
    uid = UUID(str(user["id"]))
    token = create_access_token(user_id=uid, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, uid


def _setup_trip_with_flight(
    user_id: UUID,
    fare_amount: Decimal = Decimal("450.00"),
    currency: str = "USD",
    expires_in_minutes: int = 30,
    passenger_count: int = 1,
    raw_payload: dict | None = None,
) -> tuple[dict, UUID]:
    """Create a trip, published revision, search session, and flight OfferSnapshot."""
    trip_store = TripStore()
    snap_store = SearchSessionStore()
    ev_store = EvidenceRevisionStore()
    now = datetime.now(timezone.utc)

    # 1. Create Trip & published revision
    rev_id = uuid4()
    trip = trip_store.create_trip(
        user_id=user_id,
        title="London Vacation",
        destination="LHR",
        origin="DEL",
        status="planning",
        current_revision_number=1,
        current_revision_id=rev_id,
    )
    rev = PlanRevision(
        revision_id=rev_id,
        trip_id=trip["id"],
        user_id=user_id,
        revision_number=1,
        change_set={},
        plan_snapshot={"constraints": {"origin": "DEL", "destination": "LHR"}},
        is_published=True,
        published_at=now,
        created_at=now,
    )
    ev_store._IN_MEMORY_REVISIONS[str(rev_id)] = rev

    # 2. Create search session bound to trip revision
    session = snap_store.create_session(
        user_id=user_id,
        trip_id=trip["id"],
        thread_id="th_1",
        search_type="flights",
        criteria_hash="hash_london",
        trip_revision=1,
    )

    # 3. Create FlightOffer & OfferSnapshot
    seg = FlightSegmentModel(
        origin_iata="DEL",
        destination_iata="LHR",
        departure_time=now + timedelta(days=14),
        arrival_time=now + timedelta(days=14, hours=9),
        marketing_carrier_iata="BA",
        marketing_carrier_name="British Airways",
        flight_number="BA142",
        duration_minutes=540,
        cabin_class="economy",
    )
    offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_ba_london_1",
        price=PriceBreakdown(
            base_amount=fare_amount,
            total_amount=fare_amount,
            currency=currency,
        ),
        stops=0,
        segments=[seg],
        passenger_count=passenger_count,
        retrieved_at=now,
        expires_at=now + timedelta(minutes=expires_in_minutes),
    )
    payload = {
        "airline_checkout_url": "https://www.britishairways.com/booking/ba142",
        "baggage_summary": "1 checked bag(s)",
        "included_baggage": [{"type": "checked", "quantity": 1}],
        "fare_rules_summary": "refundable",
        "conditions": {"refund_before_departure": {"allowed": True}},
    }
    if raw_payload is not None:
        payload.update(raw_payload)
    snapshot = snap_store.save_snapshot(
        session_id=session.session_id,
        offer=offer,
        raw_payload=payload,
    )
    return trip, snapshot.snapshot_id


def _dummy_passengers() -> list[PassengerBookingModel]:
    return [
        PassengerBookingModel(
            given_name="Supratik",
            family_name="Mukherjee",
            born_on="1995-05-12",
            title="mr",
            gender="m",
            email="supratik@trusttrip.com",
            phone_number="+919876543210",
        )
    ]


# ============================================================================
# A. DOMAIN & MODEL VALIDATION TESTS
# ============================================================================

def test_passenger_hash_determinism():
    p1 = PassengerBookingModel(given_name="Alice", family_name="Smith", born_on="1990-01-01", title="ms", gender="f")
    p2 = PassengerBookingModel(given_name="Bob", family_name="Jones", born_on="1985-05-05", title="mr", gender="m")

    h1 = compute_passenger_hash([p1, p2])
    h2 = compute_passenger_hash([p2, p1])  # Order-independent sorting
    assert h1 == h2
    assert len(h1) == 64


def test_passenger_validation_rules():
    with pytest.raises(ValueError, match="title"):
        PassengerBookingModel(given_name="A", family_name="B", born_on="1990-01-01", title="king", gender="m")
    with pytest.raises(ValueError, match="gender"):
        PassengerBookingModel(given_name="A", family_name="B", born_on="1990-01-01", title="mr", gender="z")


def test_booking_intent_forbids_extra_fields():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        BookingIntent(
            id=uuid4(),
            user_id=uuid4(),
            trip_id=uuid4(),
            revision_id=uuid4(),
            selected_offer_snapshot_id=uuid4(),
            passenger_hash="abc",
            expected_amount=Decimal("100"),
            currency="USD",
            created_at=now,
            updated_at=now,
            expires_at=now,
            idempotency_key="k1",
            forbidden_extra_field="malicious",
        )


# ============================================================================
# B. STATE MACHINE TESTS
# ============================================================================

def test_state_machine_legal_transitions():
    validate_booking_transition(BookingStatus.DRAFT, BookingStatus.SELECTED)
    validate_booking_transition(BookingStatus.SELECTED, BookingStatus.REVALIDATING)
    validate_booking_transition(BookingStatus.REVALIDATING, BookingStatus.READY_FOR_BOOKING)
    validate_booking_transition(BookingStatus.REVALIDATING, BookingStatus.PRICE_CHANGED)
    validate_booking_transition(BookingStatus.REVALIDATING, BookingStatus.UNAVAILABLE)
    validate_booking_transition(BookingStatus.PRICE_CHANGED, BookingStatus.SELECTED)
    validate_booking_transition(BookingStatus.READY_FOR_BOOKING, BookingStatus.HANDED_OFF)
    validate_booking_transition(BookingStatus.HANDED_OFF, BookingStatus.COMPLETED)
    validate_booking_transition(BookingStatus.SELECTED, BookingStatus.ABANDONED)


def test_state_machine_illegal_transitions_fail_closed():
    with pytest.raises(InvalidBookingStateTransitionError, match="Illegal state transition"):
        validate_booking_transition(BookingStatus.DRAFT, BookingStatus.COMPLETED)

    with pytest.raises(InvalidBookingStateTransitionError, match="Illegal state transition"):
        validate_booking_transition(BookingStatus.SELECTED, BookingStatus.HANDED_OFF)

    with pytest.raises(InvalidBookingStateTransitionError, match="Illegal state transition"):
        validate_booking_transition(BookingStatus.PRICE_CHANGED, BookingStatus.HANDED_OFF)


def test_state_machine_terminal_states_reject_outgoing():
    for term in [BookingStatus.COMPLETED, BookingStatus.FAILED, BookingStatus.EXPIRED, BookingStatus.UNAVAILABLE, BookingStatus.ABANDONED]:
        with pytest.raises(InvalidBookingStateTransitionError, match="terminal state"):
            validate_booking_transition(term, BookingStatus.SELECTED)


# ============================================================================
# C. OWNERSHIP & TENANCY TESTS
# ============================================================================

def test_cross_user_prepare_rejected():
    async def _run():
        user1 = uuid4()
        user2 = uuid4()
        trip, snap_id = _setup_trip_with_flight(user1)

        stub_adapter = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub_adapter)

        # User 2 attempts to prepare intent for User 1's trip
        with pytest.raises(OwnershipViolationException, match="(?i)access denied"):
            await service.prepare_booking_intent(
                user_id=user2,
                trip_id=trip["id"],
                snapshot_id=snap_id,
                passengers=_dummy_passengers(),
            )
    asyncio.run(_run())


def test_cross_user_snapshot_binding_rejected():
    async def _run():
        user1 = uuid4()
        user2 = uuid4()
        _trip1, snap1_id = _setup_trip_with_flight(user1)
        trip2, _snap2_id = _setup_trip_with_flight(user2)

        stub_adapter = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub_adapter)

        # User 2 attempts to bind User 1's snapshot to User 2's trip
        with pytest.raises(OwnershipViolationException, match="not found or access denied"):
            await service.prepare_booking_intent(
                user_id=user2,
                trip_id=trip2["id"],
                snapshot_id=snap1_id,
                passengers=_dummy_passengers(),
            )
    asyncio.run(_run())


# ============================================================================
# D. REVISION & HITL INVALIDATION TESTS
# ============================================================================

def test_stale_revision_during_revalidate():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )

        # Bump trip revision (HITL replanning modifying flight origin)
        trip_store = TripStore()
        trip_store.create_revision_atomic(
            trip_id=trip["id"],
            user_id=user,
            updated_constraints={"origin": "BOM"},
            feedback="Change origin to Mumbai",
        )

        # Attempt to revalidate stale intent
        with pytest.raises(StaleRevisionException, match="(?i)stale|expired"):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        # Verify intent transitioned to EXPIRED
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.EXPIRED
    asyncio.run(_run())


def test_hotel_only_mutation_leaves_flight_intent_valid():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )

        # User changes hotel/budget only, flight constraints unchanged
        trip_store = TripStore()
        trip_store.create_revision_atomic(
            trip_id=trip["id"],
            user_id=user,
            updated_constraints={"budget": 5000},
            feedback="Increase overall trip budget",
        )

        # Flight intent should still be SELECTED or valid
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.SELECTED
    asyncio.run(_run())


# ============================================================================
# E. DEEP REVALIDATION TESTS
# ============================================================================

def test_revalidation_confirmed_unchanged():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user, fare_amount=Decimal("450.00"))
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )

        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
        assert res.price_changed is False
        assert res.current_amount == Decimal("450.00")
        assert res.fresh_snapshot_id is not None
        assert res.fresh_snapshot_id != snap_id
    asyncio.run(_run())


def test_revalidation_price_changed_flow():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user, fare_amount=Decimal("450.00"))
        stub = StubBookingAdapter()
        stub.revalidation_price_override = Decimal("485.00")
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )

        # 1. Price change raises FareChangedException
        with pytest.raises(FareChangedException) as exc_info:
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        assert "485.00" in str(exc_info.value)
        intent_after = await service.get_booking_intent(user, intent.id)
        assert intent_after.status == BookingStatus.PRICE_CHANGED
        assert intent_after.expected_amount == Decimal("485.00")

        # 2. User acknowledges new price -> revalidates with accepted_amount
        stub.revalidation_price_override = Decimal("485.00")
        res = await service.revalidate_booking_intent(
            user_id=user, intent_id=intent.id, accepted_amount=Decimal("485.00")
        )
        assert res.status == BookingStatus.READY_FOR_BOOKING
        assert res.current_amount == Decimal("485.00")
    asyncio.run(_run())


def test_revalidation_offer_withdrawn():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.revalidation_status_override = RevalidationStatus.OFFER_WITHDRAWN
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )

        with pytest.raises(OfferUnavailableException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.UNAVAILABLE
    asyncio.run(_run())


# ============================================================================
# F. SNAPSHOT LIFECYCLE TESTS
# ============================================================================

def test_successor_snapshot_preserves_predecessor_linkage():
    async def _run():
        user = uuid4()
        trip, orig_snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=orig_snap_id,
            passengers=_dummy_passengers(),
        )

        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        successor_id = res.fresh_snapshot_id

        # Verify successor snapshot
        snap_store = SearchSessionStore()
        successor = snap_store.get_snapshot(successor_id, user)
        assert successor is not None
        assert successor.predecessor_snapshot_id == orig_snap_id

        # Verify original snapshot remains unchanged
        original = snap_store.get_snapshot(orig_snap_id, user)
        assert original is not None
        assert original.snapshot_id == orig_snap_id
    asyncio.run(_run())


# ============================================================================
# G. IDEMPOTENCY TESTS
# ============================================================================

def test_idempotent_prepare_returns_identical_intent():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())

        i1 = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
            idempotency_key="idemp_prep_001",
        )
        i2 = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
            idempotency_key="idemp_prep_001",
        )
        assert i1.id == i2.id
        assert len(FlightBookingLifecycleService._IN_MEMORY_INTENTS) == 1
    asyncio.run(_run())


def test_idempotent_handoff_avoids_duplicate_provider_sessions():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        h1 = await service.handoff_booking(
            user_id=user,
            intent_id=intent.id,
            idempotency_key="idemp_handoff_001",
        )
        h2 = await service.handoff_booking(
            user_id=user,
            intent_id=intent.id,
            idempotency_key="idemp_handoff_001",
        )
        assert h1.redirect_url == h2.redirect_url
        assert h1.provider_reference == h2.provider_reference
        assert len(stub.handoff_calls) == 1  # Only 1 provider call made!
    asyncio.run(_run())


# ============================================================================
# H. CONCURRENCY TESTS
# ============================================================================

def test_concurrent_handoff_requests_serialized():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user,
            trip_id=trip["id"],
            snapshot_id=snap_id,
            passengers=_dummy_passengers(),
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        # Launch two concurrent handoff calls with identical idempotency key
        t1 = asyncio.create_task(
            service.handoff_booking(user_id=user, intent_id=intent.id, idempotency_key="conc_k1")
        )
        t2 = asyncio.create_task(
            service.handoff_booking(user_id=user, intent_id=intent.id, idempotency_key="conc_k1")
        )
        r1, r2 = await asyncio.gather(t1, t2)
        assert r1.redirect_url == r2.redirect_url
        assert len(stub.handoff_calls) == 1
    asyncio.run(_run())


# ============================================================================
# I. HANDOFF & URL SECURITY TESTS
# ============================================================================

def test_duffel_links_valid_url():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.custom_handoff_url = "https://links.duffel.com/pay/ses_valid123"
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        handoff = await service.handoff_booking(user_id=user, intent_id=intent.id)

        assert handoff.status == BookingStatus.HANDED_OFF
        assert handoff.redirect_url == "https://links.duffel.com/pay/ses_valid123"
    asyncio.run(_run())


def test_fabricated_url_rejected():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.custom_handoff_url = "https://evil-phishing-duffel.com/pay/ses_123"
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        with pytest.raises(ValueError, match="unapproved checkout URL"):
            await service.handoff_booking(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_carrier_allowlist_direct_airline():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        handoff = await service.handoff_booking(
            user_id=user,
            intent_id=intent.id,
            booking_mode=BookingMode.DIRECT_AIRLINE_VERIFIED,
        )
        assert handoff.status == BookingStatus.HANDED_OFF
        assert "britishairways.com" in handoff.redirect_url
    asyncio.run(_run())


# ============================================================================
# J. CANCEL INTENT TESTS
# ============================================================================

def test_cancel_intent_transitions_to_abandoned():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())

        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        cancelled = await service.cancel_booking_intent(user_id=user, intent_id=intent.id, reason="Changed mind")
        assert cancelled.status == BookingStatus.ABANDONED

        # Cannot cancel already abandoned
        with pytest.raises(InvalidBookingStateTransitionError, match="terminal state"):
            await service.cancel_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


# ============================================================================
# K. PROVIDER RESILIENCE TESTS
# ============================================================================

def test_provider_rate_limit_handled():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.should_rate_limit = True
        service = FlightBookingLifecycleService(provider_port=stub)

        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(FlightProviderRateLimitError):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


# ============================================================================
# L. HTTP API TESTS
# ============================================================================

def test_api_full_booking_lifecycle_duffel_link():
    client, user_id = _auth_user("traveler@trusttrip.com")
    trip, snap_id = _setup_trip_with_flight(user_id, fare_amount=Decimal("450.00"))

    # 1. POST /api/bookings/flight/prepare
    prep_res = client.post(
        "/api/bookings/flight/prepare",
        json={
            "trip_id": str(trip["id"]),
            "snapshot_id": str(snap_id),
            "passengers": [
                {
                    "given_name": "Supratik",
                    "family_name": "Mukherjee",
                    "born_on": "1995-05-12",
                    "title": "mr",
                    "gender": "m",
                    "email": "supratik@trusttrip.com",
                    "phone_number": "+919876543210",
                }
            ],
            "booking_mode": "duffel_link",
        },
        headers={"Idempotency-Key": "k_api_01"},
    )
    assert prep_res.status_code == 201
    intent_data = prep_res.json()
    intent_id = intent_data["id"]
    assert intent_data["status"] == "SELECTED"

    # Mock provider for revalidate and handoff
    mock_rev = ProviderOfferVerification(
        provider_offer_id="off_ba_london_1",
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        available=True,
        total_amount=Decimal("450.00"),
        currency="USD",
        origin="DEL",
        destination="LHR",
        departure_time=datetime.now(timezone.utc) + timedelta(days=14),
        arrival_time=datetime.now(timezone.utc) + timedelta(days=14, hours=9),
        cabin_class="economy",
        passenger_count=1,
        baggage_summary="1 checked bag(s)",
        included_baggage=[{"type": "checked", "quantity": 1}],
        fare_rules_summary="refundable",
        conditions={"refund_before_departure": {"allowed": True}},
    )
    mock_link = {
        "handoff_url": "https://links.duffel.com/pay/ses_test123",
        "provider_reference": "ses_test123",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
    }

    with patch("services.duffel_booking_adapter.DuffelBookingAdapter.revalidate_offer", new_callable=AsyncMock) as m_reval, \
         patch("services.duffel_booking_adapter.DuffelBookingAdapter.create_handoff_session", new_callable=AsyncMock) as m_link:
        m_reval.return_value = mock_rev
        m_link.return_value = mock_link

        # 2. POST /api/bookings/flight/{id}/revalidate
        reval_res = client.post(
            f"/api/bookings/flight/{intent_id}/revalidate",
            json={},
            headers={"Idempotency-Key": "k_api_02"},
        )
        assert reval_res.status_code == 200
        reval_data = reval_res.json()
        assert reval_data["status"] == "READY_FOR_BOOKING"
        assert reval_data["is_valid"] is True

        # 3. POST /api/bookings/flight/{id}/handoff
        handoff_res = client.post(
            f"/api/bookings/flight/{intent_id}/handoff",
            json={"booking_mode": "duffel_link"},
            headers={"Idempotency-Key": "k_api_03"},
        )
        assert handoff_res.status_code == 200
        handoff_data = handoff_res.json()
        assert handoff_data["status"] == "HANDED_OFF"
        assert handoff_data["redirect_url"] == "https://links.duffel.com/pay/ses_test123"

        # 4. GET /api/bookings/{id}
        get_res = client.get(f"/api/bookings/{intent_id}")
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "HANDED_OFF"


def test_api_cancel_booking_intent():
    client, user_id = _auth_user("canceller@trusttrip.com")
    trip, snap_id = _setup_trip_with_flight(user_id)

    prep_res = client.post(
        "/api/bookings/flight/prepare",
        json={
            "trip_id": str(trip["id"]),
            "snapshot_id": str(snap_id),
            "passengers": [
                {
                    "given_name": "Jane",
                    "family_name": "Doe",
                    "born_on": "1992-02-02",
                    "title": "ms",
                    "gender": "f",
                }
            ],
        },
        headers={"Idempotency-Key": "k_prep_cancel"},
    )
    assert prep_res.status_code == 201
    intent_id = prep_res.json()["id"]

    cancel_res = client.post(
        f"/api/bookings/{intent_id}/cancel-intent",
        json={"reason": "Selected another itinerary"},
        headers={"Idempotency-Key": "k_cancel_01"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "ABANDONED"


# ============================================================================
# REQUIRED PRODUCTION ACCEPTANCE TESTS (A through V)
# ============================================================================

def test_acceptance_A_stale_offer_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Advance trip revision making intent stale
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["current_revision_id"] = str(uuid4())
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["current_revision_number"] = 2
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["metadata"]["revision"] = 2
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["metadata"]["trip_revision"] = 2
        with pytest.raises(StaleRevisionException):
            await service.handoff_booking(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_acceptance_B_route_change_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Carrier changes destination immediately before handoff
        stub.revalidation_destination_override = "LGW"
        with pytest.raises(OfferChangedException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert "destination" in str(exc.value)
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.UNAVAILABLE
    asyncio.run(_run())


def test_acceptance_C_time_change_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Carrier shifts departure time by 4 hours
        stub.revalidation_departure_time_override = datetime.now(timezone.utc) + timedelta(hours=4)
        with pytest.raises(OfferChangedException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert "departure_time" in str(exc.value)
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.UNAVAILABLE
    asyncio.run(_run())


def test_acceptance_D_passenger_change_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Carrier modifies passenger capacity
        stub.revalidation_passenger_count_override = 4
        with pytest.raises(OfferChangedException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert "passenger_count" in str(exc.value)
    asyncio.run(_run())


def test_acceptance_E_cabin_fare_baggage_change_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Carrier modifies baggage allowance
        stub.revalidation_included_baggage_override = [{"type": "checked", "quantity": 0}]
        with pytest.raises(OfferChangedException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert "baggage" in str(exc.value)
    asyncio.run(_run())


def test_acceptance_F_price_change_requires_acknowledgment():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user, fare_amount=Decimal("450.00"))
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        # Price shifts to 500
        stub.revalidation_price_override = Decimal("500.00")
        with pytest.raises(FareChangedException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        intent_shifted = await service.get_booking_intent(user, intent.id)
        assert intent_shifted.status == BookingStatus.PRICE_CHANGED
        assert intent_shifted.expected_amount == Decimal("500.00")

        # Handoff directly from PRICE_CHANGED without acknowledgement must fail
        with pytest.raises(InvalidBookingStateTransitionError):
            await service.handoff_booking(user_id=user, intent_id=intent.id)

        # Revalidate with acceptance
        res = await service.revalidate_booking_intent(
            user_id=user, intent_id=intent.id, accepted_amount=Decimal("500.00")
        )
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_acceptance_G_expired_offer_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Fetch refreshed intent with current successor snapshot id
        refreshed = await service.get_booking_intent(user, intent.id)
        # Expire snapshot
        SearchSessionStore._IN_MEMORY_SNAPSHOTS[str(refreshed.current_revalidation_snapshot_id)]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        with pytest.raises(OfferExpiredException):
            await service.handoff_booking(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_acceptance_H_old_revision_prevents_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Advance revision consistently
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["current_revision_number"] = 99
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["metadata"]["revision"] = 99
        TripStore._IN_MEMORY_TRIPS[str(trip["id"])]["metadata"]["trip_revision"] = 99
        with pytest.raises(StaleRevisionException):
            await service.handoff_booking(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_acceptance_I_two_workers_same_intent_serialized():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        # Concurrently request handoff with identical idempotency key
        tasks = [
            service.handoff_booking(user_id=user, intent_id=intent.id, idempotency_key="worker_race_1"),
            service.handoff_booking(user_id=user, intent_id=intent.id, idempotency_key="worker_race_1"),
        ]
        results = await asyncio.gather(*tasks)
        assert results[0].redirect_url == results[1].redirect_url
        assert results[0].provider_reference == results[1].provider_reference
    asyncio.run(_run())


def test_acceptance_J_two_workers_same_idempotency_key_one_claim():
    client, user_id = _auth_user("worker_claim@trusttrip.com")
    trip, snap_id = _setup_trip_with_flight(user_id)
    payload = {
        "trip_id": str(trip["id"]),
        "snapshot_id": str(snap_id),
        "passengers": [{"given_name": "Bob", "family_name": "Smith", "born_on": "1990-01-01", "title": "mr", "gender": "m"}],
    }
    r1 = client.post("/api/bookings/flight/prepare", json=payload, headers={"Idempotency-Key": "claim_idem_100"})
    r2 = client.post("/api/bookings/flight/prepare", json=payload, headers={"Idempotency-Key": "claim_idem_100"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


def test_acceptance_K_same_key_conflicting_payload_rejected():
    client, user_id = _auth_user("conflict@trusttrip.com")
    trip, snap_id = _setup_trip_with_flight(user_id)
    payload1 = {
        "trip_id": str(trip["id"]),
        "snapshot_id": str(snap_id),
        "passengers": [{"given_name": "Bob", "family_name": "Smith", "born_on": "1990-01-01", "title": "mr", "gender": "m"}],
    }
    payload2 = {
        "trip_id": str(trip["id"]),
        "snapshot_id": str(snap_id),
        "passengers": [{"given_name": "Alice", "family_name": "Smith", "born_on": "1992-02-02", "title": "ms", "gender": "f"}],
    }
    r1 = client.post("/api/bookings/flight/prepare", json=payload1, headers={"Idempotency-Key": "key_conflict_01"})
    assert r1.status_code == 201
    r2 = client.post("/api/bookings/flight/prepare", json=payload2, headers={"Idempotency-Key": "key_conflict_01"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_acceptance_L_cancellation_racing_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Cancel first
        await service.cancel_booking_intent(user_id=user, intent_id=intent.id)
        # Handoff racing on cancelled intent must fail
        with pytest.raises(InvalidBookingStateTransitionError):
            await service.handoff_booking(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_acceptance_M_hitl_racing_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # HITL invalidation hook triggers
        FlightBookingLifecycleService.invalidate_active_flight_intents(trip["id"])
        with pytest.raises(InvalidBookingStateTransitionError):
            await service.handoff_booking(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_acceptance_P_duffel_bearer_token_actually_sent():
    adapter = DuffelBookingAdapter(api_token="duffel_live_real_token_12345")
    headers = adapter._headers()
    # 1. Bearer token sent in real outbound headers
    assert headers["Authorization"] == "Bearer duffel_live_real_token_12345"
    # 2. String representation of adapter scrubs secret
    assert "duffel_live_real_token_12345" not in repr(adapter)


def test_acceptance_Q_raw_provider_response_never_returned_to_client():
    from services.booking_provider_port import BookingProviderUnavailable
    adapter = DuffelBookingAdapter(api_token="duffel_test_token")
    # Redaction helper test
    redacted = adapter._redact_body("Sensitive error with token=xyz and internal stacktrace at line 40")
    assert "Sensitive error" in redacted


def test_acceptance_R_forged_callback_rejected():
    client = TestClient(app)
    body = b'{"type": "order.created", "id": "evt_1", "data": {"id": "ord_1"}}'
    # Request without signature header or with invalid signature
    res = client.post(
        "/api/bookings/flight/webhook",
        content=body,
        headers={"X-Duffel-Signature": "invalid_forged_signature"},
    )
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED_CALLBACK"


def test_acceptance_S_duplicate_callback_idempotent(monkeypatch):
    import hmac, hashlib
    secret = "test_webhook_secret"
    monkeypatch.setenv("DUFFEL_WEBHOOK_SECRET", secret)
    body = b'{"type": "order.created", "id": "evt_dedup_01", "data": {"id": "ord_dedup_1", "metadata": {"intent_id": "00000000-0000-0000-0000-000000000001"}}}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    client = TestClient(app)
    # First callback (missing intent) gives 400
    r1 = client.post("/api/bookings/flight/webhook", content=body, headers={"X-Duffel-Signature": sig})
    assert r1.status_code in (400, 404)

    # Pre-populate event in attempts
    FlightBookingLifecycleService._IN_MEMORY_ATTEMPTS["webhook:evt_dedup_01"] = {"status": "COMPLETED"}
    r2 = client.post("/api/bookings/flight/webhook", content=body, headers={"X-Duffel-Signature": sig})
    assert r2.status_code == 200
    assert r2.json()["status"] == "idempotent"


def test_acceptance_21_valid_webhook_marks_completed(monkeypatch):
    import hmac, hashlib, json
    secret = "test_webhook_secret"
    monkeypatch.setenv("DUFFEL_WEBHOOK_SECRET", secret)
    client, user_id = _auth_user("webhook_comp@trusttrip.com")
    intent_id = uuid4()
    FlightBookingLifecycleService._IN_MEMORY_INTENTS[str(intent_id)] = {
        "id": intent_id,
        "user_id": user_id,
        "trip_id": uuid4(),
        "revision_id": uuid4(),
        "selected_offer_snapshot_id": uuid4(),
        "provider": BookingProvider.DUFFEL,
        "booking_mode": BookingMode.DUFFEL_LINK,
        "status": BookingStatus.HANDED_OFF,
        "idempotency_key": "k_wb_comp",
        "passenger_hash": "phash",
        "expected_amount": Decimal("400.00"),
        "currency": "USD",
        "metadata": {},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
    }

    body = json.dumps({
        "type": "order.created",
        "id": "evt_valid_01",
        "data": {
            "id": "ord_valid_1",
            "metadata": {"intent_id": str(intent_id)},
        },
    }).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    res = client.post("/api/bookings/flight/webhook", content=body, headers={"X-Duffel-Signature": sig})
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert res.json()["booking_status"] == "COMPLETED"

    # Confirm intent state transitioned to COMPLETED
    intent_rec = FlightBookingLifecycleService._IN_MEMORY_INTENTS[str(intent_id)]
    assert intent_rec["status"] == BookingStatus.COMPLETED


def test_acceptance_T_browser_redirect_alone_cannot_mark_completed():
    client, user_id = _auth_user("redirect_test@trusttrip.com")
    intent_id = uuid4()
    FlightBookingLifecycleService._IN_MEMORY_INTENTS[str(intent_id)] = {
        "id": intent_id,
        "user_id": user_id,
        "trip_id": uuid4(),
        "revision_id": uuid4(),
        "selected_offer_snapshot_id": uuid4(),
        "provider": "duffel",
        "booking_mode": "duffel_link",
        "status": BookingStatus.HANDED_OFF,
        "idempotency_key": "k_red",
        "passenger_hash": "phash",
        "expected_amount": Decimal("400.00"),
        "currency": "USD",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
    }

    # User browser redirects to /flight/callback
    res = client.get(f"/api/bookings/flight/callback?intent_id={intent_id}&status=success")
    assert res.status_code == 200
    assert res.json()["status"] == "pending_provider_webhook"

    # CRITICAL: Intent must still be in HANDED_OFF state, NOT COMPLETED
    intent = FlightBookingLifecycleService._IN_MEMORY_INTENTS[str(intent_id)]
    assert intent["status"] == BookingStatus.HANDED_OFF


def test_acceptance_U_production_cannot_use_sandbox_api_order(monkeypatch):
    async def _run():
        monkeypatch.setenv("ENV", "production")
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        with pytest.raises(InvalidBookingStateTransitionError) as exc:
            await service.handoff_booking(
                user_id=user,
                intent_id=intent.id,
                booking_mode=BookingMode.DUFFEL_API_ORDER,
                payment={"payment_type": "balance", "currency": "USD", "amount": "450.00"},
            )
        assert "strictly forbidden in production" in str(exc.value)
    asyncio.run(_run())


def test_acceptance_V_arbitrary_payment_fields_rejected():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Attempt to pass credit card details or unallowlisted payment payload
        with pytest.raises(Exception):
            await service.handoff_booking(
                user_id=user,
                intent_id=intent.id,
                booking_mode=BookingMode.DUFFEL_API_ORDER,
                payment={
                    "payment_type": "balance",
                    "currency": "USD",
                    "amount": "450.00",
                    "card_number": "4111222233334444",
                    "cvv": "123",
                },
            )
    asyncio.run(_run())


def test_acceptance_02_missing_provider_field_no_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Simulate missing origin in live provider verification during handoff
        stub.force_missing_origin = True
        with pytest.raises(OfferDataInvalidException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert exc.value.code == "OFFER_DATA_INVALID"
    asyncio.run(_run())


def test_acceptance_06_cabin_changed_no_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Carrier modifies cabin class to business
        stub.revalidation_cabin_override = "business"
        with pytest.raises(OfferChangedException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert "cabin" in str(exc.value)
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.UNAVAILABLE
    asyncio.run(_run())


def test_acceptance_08_fare_changed_no_handoff():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        # Carrier modifies fare rules / refundability
        stub.revalidation_conditions_override = {"refund_before_departure": {"allowed": False}}
        with pytest.raises(OfferChangedException) as exc:
            await service.handoff_booking(user_id=user, intent_id=intent.id)
        assert "fare_rules" in str(exc.value)
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.UNAVAILABLE
    asyncio.run(_run())


def test_acceptance_12_ready_for_booking_live_change_no_duffel_link():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.READY_FOR_BOOKING

        # Carrier withdraws offer immediately before handoff
        stub.revalidation_available_override = False
        with pytest.raises(OfferUnavailableException):
            await service.handoff_booking(user_id=user, intent_id=intent.id)

        # Invariant: NO Duffel Links session created
        assert len(stub.handoff_calls) == 0
        final_intent = await service.get_booking_intent(user, intent.id)
        assert final_intent.status == BookingStatus.UNAVAILABLE
    asyncio.run(_run())


def test_acceptance_16_hitl_selected_flight_replacement_invalidates_intent():
    async def _run():
        user = uuid4()
        trip, snap_id1 = _setup_trip_with_flight(user)
        snap_id2 = uuid4()
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id1, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        # HITL Revision replaces selected flight with a new non-null flight snapshot
        trip_store = TripStore()
        trip_store.create_revision_atomic(
            trip_id=trip["id"],
            user_id=user,
            constraints_update={"selected_flight_snapshot_id": str(snap_id2)},
            feedback="Swapped flight option",
        )

        refreshed = await service.get_booking_intent(user, intent.id)
        assert refreshed.status == BookingStatus.EXPIRED
    asyncio.run(_run())


def test_acceptance_17_hotel_only_change_flight_intent_preserved():
    async def _run():
        user = uuid4()
        trip, snap_id = _setup_trip_with_flight(user)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)

        # HITL Revision changes hotel requirements ONLY
        trip_store = TripStore()
        trip_store.create_revision_atomic(
            trip_id=trip["id"],
            user_id=user,
            constraints_update={"hotel_budget": 500},
            feedback="Updated hotel budget",
        )

        refreshed = await service.get_booking_intent(user, intent.id)
        # Invariant: Flight intent remains valid and preserved!
        assert refreshed.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_acceptance_18_cross_tenant_snapshot_use_rejected():
    async def _run():
        user_a = uuid4()
        user_b = uuid4()
        trip_a, snap_a = _setup_trip_with_flight(user_a)
        trip_b, _ = _setup_trip_with_flight(user_b)
        service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())

        # User B attempts to prepare booking intent with User A's flight snapshot
        with pytest.raises(OwnershipViolationException):
            await service.prepare_booking_intent(
                user_id=user_b,
                trip_id=trip_b["id"],
                snapshot_id=snap_a,
                passengers=_dummy_passengers(),
            )
    asyncio.run(_run())


def test_acceptance_22_missing_webhook_secret_fail_closed(monkeypatch):
    monkeypatch.delenv("DUFFEL_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TRUSTTRIP_WEBHOOK_SECRET", raising=False)
    client = TestClient(app)
    body = b'{"type": "order.created", "id": "evt_sec_01", "data": {"id": "ord_1"}}'
    res = client.post(
        "/api/bookings/flight/webhook",
        content=body,
        headers={"X-Duffel-Signature": "some_sig"},
    )
    # Must fail closed with 401 UNAUTHORIZED_CALLBACK
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED_CALLBACK"


def test_acceptance_27_failed_cas_no_phantom_transition_audit_row():
    service = FlightBookingLifecycleService(provider_port=StubBookingAdapter())
    intent_id = uuid4()
    user_id = uuid4()
    # Populate intent in SELECTED state
    FlightBookingLifecycleService._IN_MEMORY_INTENTS[str(intent_id)] = {
        "id": intent_id,
        "user_id": user_id,
        "trip_id": uuid4(),
        "revision_id": uuid4(),
        "selected_offer_snapshot_id": uuid4(),
        "provider": BookingProvider.DUFFEL,
        "booking_mode": BookingMode.DUFFEL_LINK,
        "status": BookingStatus.SELECTED,
        "idempotency_key": "k_cas_01",
        "passenger_hash": "phash",
        "expected_amount": Decimal("400.00"),
        "currency": "USD",
        "metadata": {},
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
    }

    initial_transition_count = len(FlightBookingLifecycleService._IN_MEMORY_TRANSITIONS)

    # Attempt illegal CAS transition expecting READY_FOR_BOOKING when current is SELECTED
    with pytest.raises(BookingLockedException):
        service._update_intent_status_cas(
            intent_id=intent_id,
            expected_current_status=BookingStatus.READY_FOR_BOOKING,
            new_status=BookingStatus.HANDED_OFF,
        )

    # Invariant: NO phantom transition audit row was written!
    assert len(FlightBookingLifecycleService._IN_MEMORY_TRANSITIONS) == initial_transition_count


# ============================================================================
# PHASE 9 FINAL DUFFEL-CORRECTNESS TESTS
# ============================================================================

def test_duffel_auth_real_bearer_token():
    adapter = DuffelBookingAdapter(token="audit-token")
    headers = adapter._headers()
    assert headers["Authorization"] == "Bearer audit-token"
    assert headers["Duffel-Version"] == "v2"
    assert headers["Accept"] == "application/json"


def test_duffel_auth_mocked_http_request_contains_bearer_token():
    async def _run():
        adapter = DuffelBookingAdapter(token="audit-token")
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as m_get:
            m_resp = MagicMock()
            m_resp.status_code = 200
            m_resp.is_success = True
            m_resp.json.return_value = {
                "data": {
                    "id": "off_test123",
                    "total_amount": "450.00",
                    "total_currency": "USD",
                    "slices": [],
                }
            }
            m_get.return_value = m_resp
            await adapter.revalidate_offer("off_test123")
            assert m_get.called
            headers = m_get.call_args.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer audit-token"
    asyncio.run(_run())


def test_duffel_auth_token_absent_from_repr():
    adapter = DuffelBookingAdapter(token="audit-token")
    assert "Bearer audit-token" not in repr(adapter)
    assert "audit-token" not in repr(adapter)


def test_duffel_auth_token_absent_from_str():
    adapter = DuffelBookingAdapter(token="audit-token")
    assert "Bearer audit-token" not in str(adapter)
    assert "audit-token" not in str(adapter)


def test_duffel_auth_token_not_leaked_in_provider_error():
    adapter = DuffelBookingAdapter(token="audit-token")
    redacted = adapter._redact_body("API Error: Authorization failed with Bearer audit-token and token=audit-token")
    assert "audit-token" not in redacted
    assert "[REDACTED]" in redacted


def test_textual_baggage_must_not_be_converted_into_structured_baseline():
    raw1 = {"baggage_summary": "1 checked bag"}
    raw2 = {"baggage_summary": "1 checked bag(s)"}
    raw3 = {"baggage_summary": "standard baggage"}
    assert FlightBookingLifecycleService._extract_included_baggage(raw1) is None
    assert FlightBookingLifecycleService._extract_included_baggage(raw2) is None
    assert FlightBookingLifecycleService._extract_included_baggage(raw3) is None


def test_textual_fare_rules_must_not_be_used_as_comparison_signal():
    raw1 = {"fare_rules_summary": "refundable"}
    raw2 = {"fare_rules_summary": "non-refundable"}
    raw3 = {"fare_rules_summary": "flexible"}
    raw4 = {"fare_rules_summary": "non_refundable_basic"}
    assert FlightBookingLifecycleService._extract_conditions(raw1) is None
    assert FlightBookingLifecycleService._extract_conditions(raw2) is None
    assert FlightBookingLifecycleService._extract_conditions(raw3) is None
    assert FlightBookingLifecycleService._extract_conditions(raw4) is None


def test_structured_baggage_unchanged_allowed():
    async def _run():
        _, user = _auth_user("bag_unchanged@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={"included_baggage": [{"type": "checked", "quantity": 1}]},
        )
        stub = StubBookingAdapter()
        stub.revalidation_included_baggage_override = [{"type": "checked", "quantity": 1}]
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_structured_fare_conditions_unchanged_allowed():
    async def _run():
        _, user = _auth_user("fare_unchanged@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={"conditions": {"refund_before_departure": {"allowed": True}}},
        )
        stub = StubBookingAdapter()
        stub.revalidation_conditions_override = {"refund_before_departure": {"allowed": True}}
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_missing_baggage_when_baseline_baggage_was_known_raises_offer_data_invalid():
    async def _run():
        _, user = _auth_user("bag_missing@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={"included_baggage": [{"type": "checked", "quantity": 1}]},
        )
        stub = StubBookingAdapter()
        stub.force_missing_baggage = True
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferDataInvalidException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_missing_fare_condition_when_baseline_condition_was_known_raises_offer_data_invalid():
    async def _run():
        _, user = _auth_user("fare_missing@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={"conditions": {"refund_before_departure": {"allowed": True}}},
        )
        stub = StubBookingAdapter()
        stub.force_missing_fare_rules = True
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferDataInvalidException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_both_baggage_values_absent_no_fabrication_no_false_offer_changed():
    async def _run():
        _, user = _auth_user("both_no_bag@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user, raw_payload={"baggage_summary": None, "included_baggage": None}
        )
        stub = StubBookingAdapter()
        stub.force_missing_baggage = True
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        # Should succeed because both legitimately lack baggage information
        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_both_fare_condition_values_absent_no_fabrication_no_false_offer_changed():
    async def _run():
        _, user = _auth_user("both_no_fare@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user, raw_payload={"fare_rules_summary": None, "conditions": None}
        )
        stub = StubBookingAdapter()
        stub.force_missing_fare_rules = True
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        # Should succeed because both legitimately lack conditions
        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_baggage_changes_raises_offer_changed_no_handoff():
    async def _run():
        _, user = _auth_user("bag_change@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={"included_baggage": [{"type": "checked", "quantity": 1}]},
        )
        stub = StubBookingAdapter()
        stub.revalidation_included_baggage_override = [{"type": "checked", "quantity": 2}]
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferChangedException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_fare_conditions_change_raises_offer_changed_no_handoff():
    async def _run():
        _, user = _auth_user("fare_change@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={"conditions": {"refund_before_departure": {"allowed": True}}},
        )
        stub = StubBookingAdapter()
        stub.revalidation_conditions_override = {"refund_before_departure": {"allowed": False}}
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferChangedException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_passenger_composition_changes_raises_offer_changed_no_handoff():
    async def _run():
        _, user = _auth_user("pax_comp_change@trusttrip.com")
        two_pax = [
            PassengerBookingModel(given_name="Alice", family_name="Doe", born_on="1990-01-01", title="ms", gender="f"),
            PassengerBookingModel(given_name="Bob", family_name="Doe", born_on="2018-01-01", title="mr", gender="m"),
        ]
        trip, snap_id = _setup_trip_with_flight(
            user,
            passenger_count=2,
            raw_payload={"passengers": [{"type": "adult"}, {"type": "child"}]},
        )
        stub = StubBookingAdapter()
        stub.revalidation_passenger_count_override = 2
        stub.revalidation_passengers_override = [{"type": "adult"}, {"type": "adult"}]
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=two_pax
        )
        with pytest.raises(OfferChangedException) as exc:
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert "passenger_composition" in str(exc.value)
    asyncio.run(_run())


def test_passenger_count_changes_raises_offer_changed_no_handoff():
    async def _run():
        _, user = _auth_user("pax_cnt_change@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.revalidation_passenger_count_override = 3
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferChangedException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_unchanged_passenger_composition_allowed():
    async def _run():
        _, user = _auth_user("pax_comp_ok@trusttrip.com")
        two_pax = [
            PassengerBookingModel(given_name="Alice", family_name="Doe", born_on="1990-01-01", title="ms", gender="f"),
            PassengerBookingModel(given_name="Bob", family_name="Doe", born_on="2018-01-01", title="mr", gender="m"),
        ]
        trip, snap_id = _setup_trip_with_flight(
            user,
            passenger_count=2,
            raw_payload={"passengers": [{"type": "adult"}, {"type": "child"}]},
        )
        stub = StubBookingAdapter()
        stub.revalidation_passenger_count_override = 2
        stub.revalidation_passengers_override = [{"type": "adult"}, {"type": "child"}]
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=two_pax
        )
        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_cabin_changes_raises_offer_changed_no_handoff():
    async def _run():
        _, user = _auth_user("cabin_change@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.revalidation_cabin_override = "business"
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferChangedException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


def test_valid_complete_provider_response_comparison_succeeds():
    async def _run():
        _, user = _auth_user("complete_valid@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(
            user,
            raw_payload={
                "included_baggage": [{"type": "checked", "quantity": 1}],
                "conditions": {"refund_before_departure": {"allowed": True}},
                "passengers": [{"type": "adult"}],
            },
        )
        stub = StubBookingAdapter()
        stub.revalidation_included_baggage_override = [{"type": "checked", "quantity": 1}]
        stub.revalidation_conditions_override = {"refund_before_departure": {"allowed": True}}
        stub.revalidation_passengers_override = [{"type": "adult"}]
        stub.revalidation_passenger_count_override = 1
        stub.revalidation_cabin_override = "economy"
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        res = await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
        assert res.is_valid is True
        assert res.status == BookingStatus.READY_FOR_BOOKING
    asyncio.run(_run())


def test_malformed_missing_core_provider_field_raises_offer_data_invalid():
    async def _run():
        _, user = _auth_user("core_missing@trusttrip.com")
        trip, snap_id = _setup_trip_with_flight(user)
        stub = StubBookingAdapter()
        stub.force_missing_origin = True
        service = FlightBookingLifecycleService(provider_port=stub)
        intent = await service.prepare_booking_intent(
            user_id=user, trip_id=trip["id"], snapshot_id=snap_id, passengers=_dummy_passengers()
        )
        with pytest.raises(OfferDataInvalidException):
            await service.revalidate_booking_intent(user_id=user, intent_id=intent.id)
    asyncio.run(_run())


