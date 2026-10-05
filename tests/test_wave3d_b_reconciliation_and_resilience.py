"""TrustTrip Wave 3D-B: Flight Confirmation Reconciliation & Gemini 503 Resilience Tests.

Validates:
W3D-06: Flight confirmation reconciliation via find_order_by_offer on 502/timeout,
        safe retry on HANDED_OFF intents, and avoiding duplicate provider bookings.
W3D-07: Structured HTTP 503 responses for Gemini upstream service unavailability,
        preserving retryable=True and Retry-After header, distinct from 429 rate limits.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.booking_lifecycle import BookingStatus
from domain.flight_booking import (
    DuplicateBookingError,
    FlightPassenger,
    ProviderBookingError,
)
from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    PriceBreakdown,
)
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.chat_planner import PlannerOutageError, generate_travel_plan
from services.flight_booking_service import FlightBookingService
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    FlightBookingService._IN_MEMORY_TRANSITIONS.clear()


def _auth_user(email: str = "w3db_tester@trusttrip.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.get_by_email(email)
    if not user:
        try:
            user = user_store.create_user(email=email, password_hash="hash")
        except ValueError:
            user = user_store.get_by_email(email)
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, {"id": user["id"], "email": email}


def _create_test_snapshot(store: SearchSessionStore, user_id: str, provider_offer_id: str = "off_w3db_123") -> str:
    dep_dt = datetime.now(timezone.utc) + timedelta(days=20)
    arr_dt = dep_dt + timedelta(hours=11)
    seg = FlightSegmentModel(
        origin_iata="SFO",
        destination_iata="HND",
        departure_time=dep_dt,
        arrival_time=arr_dt,
        marketing_carrier_iata="JL",
        marketing_carrier_name="Japan Airlines",
        flight_number="JL001",
        duration_minutes=660,
        cabin_class="economy",
    )
    norm_offer = FlightOffer(
        provider="duffel",
        provider_offer_id=provider_offer_id,
        price=PriceBreakdown(
            base_amount=Decimal("400.00"),
            tax_amount=Decimal("50.00"),
            fee_amount=Decimal("0.00"),
            total_amount=Decimal("450.00"),
            currency="USD",
        ),
        segments=[seg],
        retrieved_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    sess = store.create_session(
        user_id=user_id,
        trip_id=str(uuid4()),
        thread_id="test_thread",
        search_type="flight",
        criteria_hash="hash_w3db_flight",
        trip_revision=1,
    )
    snap = store.save_snapshot(
        sess.session_id,
        norm_offer,
        raw_payload={"provider": "duffel", "id": provider_offer_id},
    )
    return str(snap.snapshot_id)


def _setup_approved_intent(
    service: FlightBookingService,
    user_id: str,
    trip_id: str,
    snapshot_id: str,
    provider_offer_id: str = "off_w3db_123",
) -> str:
    booking_id = str(uuid4())
    now = datetime.now(timezone.utc)
    intent_data = {
        "id": booking_id,
        "user_id": user_id,
        "trip_id": trip_id,
        "selected_offer_snapshot_id": snapshot_id,
        "current_revalidation_snapshot_id": snapshot_id,
        "status": BookingStatus.READY_FOR_BOOKING.value,
        "expected_amount": Decimal("450.00"),
        "currency": "USD",
        "idempotency_key": f"book_{booking_id}",
        "passenger_hash": "hash123",
        "provider_reference": None,
        "metadata": {
            "approved": True,
            "approved_at": now.isoformat(),
            "approved_price": 450.00,
            "passengers": [
                {
                    "type": "adult",
                    "given_name": "Supratik",
                    "family_name": "Sanyal",
                    "born_on": "1995-05-15",
                    "email": "supratik@trusttrip.com",
                    "phone_number": "+919876543210",
                }
            ],
            "passenger_count": 1,
            "offer_id": "fl_off_test",
            "trip_id": trip_id,
        },
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(minutes=30),
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[booking_id] = intent_data
    return booking_id


# =========================================================================
# W3D-06: Flight Confirmation Reconciliation Tests
# =========================================================================

def test_w3d06_provider_timeout_with_existing_order_reconciles_successfully():
    """W3D-06: When provider.book() times out but find_order_by_offer() finds an existing order,

    the booking intent must reconcile to COMPLETED instead of FAILED, without duplicate booking.
    """
    _, user = _auth_user("reconcile_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Tokyo Trip", destination="Tokyo", origin="SFO")
    tid = str(trip["id"])

    service = FlightBookingService()
    snapshot_id = _create_test_snapshot(service.store, user["id"], "off_duffel_reconcile_1")
    booking_id = _setup_approved_intent(service, user["id"], tid, snapshot_id, "off_duffel_reconcile_1")

    # Mock provider.recheck
    service.flight_service.recheck = MagicMock(return_value={"price": 450.00, "available": True})

    # Mock provider.book to timeout
    service.flight_service.provider.book = MagicMock(
        side_effect=FlightProviderTimeout("Duffel timed out after 150 seconds")
    )

    # Mock find_order_by_offer to return the successfully created Duffel order
    reconciled_order = {
        "id": "ord_reconciled_101",
        "booking_reference": "PNR7788",
        "type": "instant",
        "total_amount": "450.00",
        "total_currency": "USD",
        "documents": [{"unique_identifier": "ETK-990011"}],
        "slices": [],
    }
    service.flight_service.find_order_by_offer = MagicMock(return_value=reconciled_order)

    # Execute confirmation
    res = asyncio.run(
        service.confirm_booking(
            user_id=user["id"],
            booking_id=booking_id,
            final_confirmation=True,
        )
    )

    # Assertions
    assert res.success is True
    assert res.booked is True
    assert res.status == BookingStatus.COMPLETED.value
    assert res.booking_reference == "PNR7788"
    assert res.order_id == "ord_reconciled_101"
    assert res.e_ticket == "ETK-990011"

    # Verify intent in storage is COMPLETED, not FAILED
    saved_intent = service._get_intent(booking_id, user["id"])
    assert saved_intent["status"] == BookingStatus.COMPLETED.value
    assert saved_intent["metadata"]["booking_reference"] == "PNR7788"
    assert saved_intent["metadata"]["order_id"] == "ord_reconciled_101"

    # Verify book() was only called once (no duplicate order attempt)
    assert service.flight_service.provider.book.call_count == 1
    assert service.flight_service.find_order_by_offer.call_count == 1


def test_w3d06_provider_timeout_without_existing_order_fails():
    """W3D-06: When provider.book() times out and find_order_by_offer() returns None,

    the intent must be marked FAILED and ProviderBookingError must be raised.
    """
    _, user = _auth_user("fail_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Tokyo Trip", destination="Tokyo", origin="SFO")
    tid = str(trip["id"])

    service = FlightBookingService()
    snapshot_id = _create_test_snapshot(service.store, user["id"], "off_duffel_fail_1")
    booking_id = _setup_approved_intent(service, user["id"], tid, snapshot_id, "off_duffel_fail_1")

    service.flight_service.recheck = MagicMock(return_value={"price": 450.00, "available": True})
    service.flight_service.provider.book = MagicMock(
        side_effect=FlightProviderTimeout("Duffel timed out after 150 seconds")
    )
    service.flight_service.find_order_by_offer = MagicMock(return_value=None)

    with pytest.raises(ProviderBookingError) as exc_info:
        asyncio.run(
            service.confirm_booking(
                user_id=user["id"],
                booking_id=booking_id,
                final_confirmation=True,
            )
        )

    assert "timed out" in str(exc_info.value).lower()
    saved_intent = service._get_intent(booking_id, user["id"])
    assert saved_intent["status"] == BookingStatus.FAILED.value


def test_w3d06_reconciliation_lookup_failure_preserves_handed_off():
    """W3D-06: When provider.book() fails and reconciliation lookup also errors out (ambiguous network outage),

    the booking intent must remain in HANDED_OFF (not marked FAILED with certainty) to block duplicate rebooking.
    """
    _, user = _auth_user("ambiguous_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Tokyo Trip", destination="Tokyo", origin="SFO")
    tid = str(trip["id"])

    service = FlightBookingService()
    snapshot_id = _create_test_snapshot(service.store, user["id"], "off_duffel_ambiguous_1")
    booking_id = _setup_approved_intent(service, user["id"], tid, snapshot_id, "off_duffel_ambiguous_1")

    service.flight_service.recheck = MagicMock(return_value={"price": 450.00, "available": True})
    service.flight_service.provider.book = MagicMock(
        side_effect=FlightProviderError("Connection reset by peer")
    )
    # find_order_by_offer also fails due to the connection outage
    service.flight_service.find_order_by_offer = MagicMock(
        side_effect=RuntimeError("DNS failure on orders endpoint")
    )

    with pytest.raises(ProviderBookingError) as exc_info:
        asyncio.run(
            service.confirm_booking(
                user_id=user["id"],
                booking_id=booking_id,
                final_confirmation=True,
            )
        )

    assert "ambiguous" in str(exc_info.value).lower()
    saved_intent = service._get_intent(booking_id, user["id"])
    # Crucial safety property: must NOT be FAILED
    assert saved_intent["status"] == BookingStatus.HANDED_OFF.value


def test_w3d06_retry_on_handed_off_reconciles_existing_order_without_rebooking():
    """W3D-06: A retry on an intent in HANDED_OFF queries find_order_by_offer;

    if the order exists, it reconciles to COMPLETED without calling provider.book() again.
    """
    _, user = _auth_user("retry_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Tokyo Trip", destination="Tokyo", origin="SFO")
    tid = str(trip["id"])

    service = FlightBookingService()
    snapshot_id = _create_test_snapshot(service.store, user["id"], "off_duffel_retry_1")
    booking_id = _setup_approved_intent(service, user["id"], tid, snapshot_id, "off_duffel_retry_1")

    # Manually place intent in HANDED_OFF (as if a previous timeout occurred)
    FlightBookingService._IN_MEMORY_BOOKINGS[booking_id]["status"] = BookingStatus.HANDED_OFF.value

    reconciled_order = {
        "id": "ord_retry_success_999",
        "booking_reference": "PNRRETRY99",
        "type": "instant",
        "total_amount": "450.00",
        "total_currency": "USD",
        "documents": [{"unique_identifier": "ETK-RETRY"}],
        "slices": [],
    }
    service.flight_service.find_order_by_offer = MagicMock(return_value=reconciled_order)
    service.flight_service.provider.book = MagicMock()

    # Retry confirmation
    res = asyncio.run(
        service.confirm_booking(
            user_id=user["id"],
            booking_id=booking_id,
            final_confirmation=True,
        )
    )

    assert res.success is True
    assert res.booking_reference == "PNRRETRY99"
    assert res.order_id == "ord_retry_success_999"
    # provider.book() was NOT called again!
    assert service.flight_service.provider.book.call_count == 0


def test_w3d06_get_booking_self_heals_handed_off_intent():
    """W3D-06: Polling get_booking on a HANDED_OFF intent reconciles an existing order to COMPLETED."""
    _, user = _auth_user("selfheal_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Tokyo Trip", destination="Tokyo", origin="SFO")
    tid = str(trip["id"])

    service = FlightBookingService()
    snapshot_id = _create_test_snapshot(service.store, user["id"], "off_duffel_heal_1")
    booking_id = _setup_approved_intent(service, user["id"], tid, snapshot_id, "off_duffel_heal_1")

    FlightBookingService._IN_MEMORY_BOOKINGS[booking_id]["status"] = BookingStatus.HANDED_OFF.value

    reconciled_order = {
        "id": "ord_heal_777",
        "booking_reference": "PNRHEAL77",
        "type": "instant",
        "total_amount": "450.00",
        "total_currency": "USD",
        "documents": [],
        "slices": [],
    }
    service.flight_service.find_order_by_offer = MagicMock(return_value=reconciled_order)

    status_res = asyncio.run(service.get_booking(user_id=user["id"], booking_id=booking_id))

    assert status_res["status"] == BookingStatus.COMPLETED.value
    assert status_res["booking_reference"] == "PNRHEAL77"
    assert status_res["order_id"] == "ord_heal_777"


# =========================================================================
# W3D-07: Gemini 503 Structured Error Handling Tests
# =========================================================================

def test_w3d07_chat_planner_classifies_503_as_retryable():
    """W3D-07: PlannerOutageError raised on 503 contains status_code=503 and retryable=True."""
    with patch("services.chat_planner._groq_plan", side_effect=RuntimeError("Groq 503 Service Unavailable")), \
         patch("services.chat_planner._gemini_plan", side_effect=RuntimeError("HTTP 503: Service Unavailable - Gemini backend overloaded")):

        with pytest.raises(PlannerOutageError) as exc_info:
            generate_travel_plan(
                user_query="Plan trip to Tokyo",
                constraints={"origin": "SFO", "destination": "Tokyo", "days": 5},
                flight_results="",
                live_flight_offers=[],
                live_hotel_offers=[],
                hotel_results="",
                weather_results="",
                budget={},
                raise_on_outage=True,
            )

        err = exc_info.value
        assert err.status_code == 503
        assert err.retryable is True


def test_w3d07_chat_planner_classifies_429_as_non_retryable():
    """W3D-07: PlannerOutageError raised on 429 contains status_code=429 and retryable=False."""
    with patch("services.chat_planner._groq_plan", side_effect=RuntimeError("Groq 429 Rate Limit")), \
         patch("services.chat_planner._gemini_plan", side_effect=RuntimeError("Gemini 429: ResourceExhausted - Quota exceeded")):

        with pytest.raises(PlannerOutageError) as exc_info:
            generate_travel_plan(
                user_query="Plan trip to Tokyo",
                constraints={"origin": "SFO", "destination": "Tokyo", "days": 5},
                flight_results="",
                live_flight_offers=[],
                live_hotel_offers=[],
                hotel_results="",
                weather_results="",
                budget={},
                raise_on_outage=True,
            )

        err = exc_info.value
        assert err.status_code == 429
        assert err.retryable is False


def test_w3d07_api_travel_returns_structured_http_503_on_outage_state():
    """W3D-07: POST /api/travel returns HTTP 503 with Retry-After: 5 and retryable=True on 503 outage."""
    client, user = _auth_user("gemini503_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Paris Trip", destination="Paris", origin="JFK")

    outage_result = {
        "success": False,
        "planning_blocked": True,
        "outage_status_code": 503,
        "status_code": 503,
        "retryable": True,
        "error": "Upstream AI travel planning service is temporarily overloaded. Please try again shortly.",
        "llm_provider": "unavailable",
    }

    with patch("app.run_travel_agent", return_value=outage_result):
        resp = client.post(
            "/api/travel",
            json={
                "message": "Plan a 5-day cultural trip to Paris",
                "trip_id": str(trip["id"]),
            },
        )

    assert resp.status_code == 503
    data = resp.json()
    assert data["success"] is False
    assert data["retryable"] is True
    assert "Retry-After" in resp.headers
    assert resp.headers["Retry-After"] == "5"
    assert "temporarily overloaded" in data["error"].lower() or "unavailable" in data["error"].lower()


def test_w3d07_api_travel_returns_structured_http_503_on_unhandled_exception():
    """W3D-07: POST /api/travel top-level exception handler maps 503 exception to HTTP 503 with Retry-After header."""
    client, user = _auth_user("gemini503_exc_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Tokyo Trip", destination="Tokyo", origin="SFO")

    with patch("app.run_travel_agent", side_effect=RuntimeError("HTTP 503: Service Unavailable - Gemini backend overloaded")):
        resp = client.post(
            "/api/travel",
            json={
                "message": "Plan a 5-day trip to Tokyo",
                "trip_id": str(trip["id"]),
            },
        )

    assert resp.status_code == 503
    data = resp.json()
    assert data["success"] is False
    assert data["retryable"] is True
    assert "Retry-After" in resp.headers
    assert resp.headers["Retry-After"] == "5"


def test_w3d07_api_travel_returns_429_not_503():
    """W3D-07: POST /api/travel returns HTTP 429 with retryable=False when quota/rate limit is hit, not 503."""
    client, user = _auth_user("gemini429_test@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Rome Trip", destination="Rome", origin="JFK")

    outage_result = {
        "success": False,
        "planning_blocked": True,
        "outage_status_code": 429,
        "status_code": 429,
        "retryable": False,
        "error": "AI planning rate limit exceeded. Please wait before retrying.",
        "llm_provider": "unavailable",
    }

    with patch("app.run_travel_agent", return_value=outage_result):
        resp = client.post(
            "/api/travel",
            json={
                "message": "Plan a 3-day trip to Rome",
                "trip_id": str(trip["id"]),
            },
        )

    assert resp.status_code == 429
    data = resp.json()
    assert data["success"] is False
    assert data["retryable"] is False
    assert "rate limit" in data["error"].lower() or "quota" in data["error"].lower()


def test_w3d07_empty_message_validation_returns_400():
    """W3D-07: Genuine validation error (empty message) remains HTTP 400 and is not masked by outage handlers."""
    client, _ = _auth_user("val_err_test@trusttrip.com")
    resp = client.post("/api/travel", json={"message": "   "})
    assert resp.status_code == 400
    data = resp.json()
    assert data["success"] is False
    assert "empty" in data["error"].lower()
