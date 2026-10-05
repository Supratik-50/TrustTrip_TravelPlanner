"""Wave 3C Verification: Truthful Cancellation, PDF Contracts, and PNR Disambiguation.

Tests cover:
- W3-FIND-09: Strict independence of booking_reference and pnr in BookingReadService
- W3-FIND-05 & W3-FIND-07: Canonical flight cancellation quote and confirm against booking_intents
- W3-FIND-05: Authoritative hotel cancellation request workflow on trust_trip_hotel_transactions
- W3-FIND-06: Elimination of synthetic cancellation (CN-XXXXXX) and travel PDF (export.pdf) fallbacks
- W3-FIND-10: Binary Travel Plan PDF streaming by trip_id and plan
"""
from __future__ import annotations

import io
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

from app import app
from domain.user import UserResponse
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService
from services.flight_provider import FlightProviderError
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    BookingReadService.clear_in_memory()


def _auth_user(email: str = "w3c_user@example.com") -> tuple[TestClient, UserResponse]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    user_resp = UserResponse(
        id=user["id"],
        email=email,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    return client, user_resp


# ==============================================================================
# 1. W3-FIND-09: PNR BEHAVIOR MATRIX & DISAMBIGUATION
# ==============================================================================

def test_pnr_matrix_case1_booking_ref_without_pnr():
    """Case 1: Only booking_reference present -> pnr must be None."""
    _, user = _auth_user("case1@example.com")
    trip_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())

    record = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": str(user.id),
        "status": "COMPLETED",
        "provider": "duffel",
        "provider_reference": "REF-001",
        "metadata": {
            "booking_reference": "REF-001",
            "order_id": "ord_123",
            # No 'pnr' in metadata
        },
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[record["id"]] = record

    read_service = BookingReadService()
    res = read_service.get_trip_bookings(trip_id=trip_id, user_id=user.id)
    flight = res["flight"]

    assert flight is not None
    assert flight["booking_reference"] == "REF-001"
    assert flight["pnr"] is None, "PNR must be None when not explicitly in provider metadata"


def test_pnr_matrix_case2_distinct_booking_ref_and_pnr():
    """Case 2: Both booking_reference and pnr present -> distinct values preserved."""
    _, user = _auth_user("case2@example.com")
    trip_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())

    record = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": str(user.id),
        "status": "COMPLETED",
        "provider": "duffel",
        "provider_reference": "REF-AIRLINE-ABC",
        "metadata": {
            "booking_reference": "REF-AIRLINE-ABC",
            "pnr": "6CHARP",
            "order_id": "ord_456",
        },
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[record["id"]] = record

    read_service = BookingReadService()
    res = read_service.get_trip_bookings(trip_id=trip_id, user_id=user.id)
    flight = res["flight"]

    assert flight is not None
    assert flight["booking_reference"] == "REF-AIRLINE-ABC"
    assert flight["pnr"] == "6CHARP"


def test_pnr_matrix_case3_empty_metadata():
    """Case 3: Neither booking_reference nor pnr in metadata -> pnr is None."""
    _, user = _auth_user("case3@example.com")
    trip_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())

    record = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": str(user.id),
        "status": "COMPLETED",
        "provider": "duffel",
        "metadata": {},
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[record["id"]] = record

    read_service = BookingReadService()
    res = read_service.get_trip_bookings(trip_id=trip_id, user_id=user.id)
    flight = res["flight"]

    assert flight is not None
    assert flight["pnr"] is None


def test_w3c_aud_01_read_service_cancellation_status_normalization():
    """W3C-AUD-01: Explicit cancellation metadata takes precedence over raw DB status enum."""
    _, user = _auth_user("aud01@example.com")
    read_service = BookingReadService()

    # Case A: PostgreSQL-compatible cancelled booking (status=ABANDONED + cancellation metadata)
    trip_a = str(uuid.uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS["intent_a"] = {
        "id": "intent_a",
        "trip_id": trip_a,
        "user_id": str(user.id),
        "status": "ABANDONED",
        "provider": "duffel",
        "metadata": {
            "status": "CANCELLED",
            "cancellation": {"status": "confirmed", "refund_amount": "5000.00"},
        },
    }
    flight_a = read_service.get_trip_bookings(trip_id=trip_a, user_id=user.id)["flight"]
    assert flight_a is not None
    assert flight_a["status"] == "cancelled"
    assert flight_a["is_booked"] is False

    # Case B: Explicit cancellation timestamp
    trip_b = str(uuid.uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS["intent_b"] = {
        "id": "intent_b",
        "trip_id": trip_b,
        "user_id": str(user.id),
        "status": "ABANDONED",
        "provider": "duffel",
        "metadata": {
            "cancelled_at": "2026-10-05T23:00:00Z",
        },
    }
    flight_b = read_service.get_trip_bookings(trip_id=trip_b, user_id=user.id)["flight"]
    assert flight_b is not None
    assert flight_b["status"] == "cancelled"
    assert flight_b["is_booked"] is False

    # Case C: Normal abandoned booking without cancellation metadata must remain abandoned
    trip_c = str(uuid.uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS["intent_c"] = {
        "id": "intent_c",
        "trip_id": trip_c,
        "user_id": str(user.id),
        "status": "ABANDONED",
        "provider": "duffel",
        "metadata": {},
    }
    flight_c = read_service.get_trip_bookings(trip_id=trip_c, user_id=user.id)["flight"]
    assert flight_c is not None
    assert flight_c["status"] == "abandoned"
    assert flight_c["is_booked"] is False

    # Case D: Normal completed booking
    trip_d = str(uuid.uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS["intent_d"] = {
        "id": "intent_d",
        "trip_id": trip_d,
        "user_id": str(user.id),
        "status": "COMPLETED",
        "provider": "duffel",
        "metadata": {},
    }
    flight_d = read_service.get_trip_bookings(trip_id=trip_d, user_id=user.id)["flight"]
    assert flight_d is not None
    assert flight_d["status"] == "completed"
    assert flight_d["is_booked"] is True

    # Case E: In-memory cancelled booking
    trip_e = str(uuid.uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS["intent_e"] = {
        "id": "intent_e",
        "trip_id": trip_e,
        "user_id": str(user.id),
        "status": "CANCELLED",
        "provider": "duffel",
        "metadata": {},
    }
    flight_e = read_service.get_trip_bookings(trip_id=trip_e, user_id=user.id)["flight"]
    assert flight_e is not None
    assert flight_e["status"] == "cancelled"
    assert flight_e["is_booked"] is False

    # Also verify list_recent_flights_for_user normalization
    recent = read_service.list_recent_flights_for_user(user_id=user.id)
    recent_by_id = {r["id"]: r for r in recent}
    assert recent_by_id["intent_a"]["booking_status"] == "cancelled"
    assert recent_by_id["intent_b"]["booking_status"] == "cancelled"
    assert recent_by_id["intent_c"]["booking_status"] == "abandoned"
    assert recent_by_id["intent_d"]["booking_status"] == "booked"
    assert recent_by_id["intent_e"]["booking_status"] == "cancelled"


# ==============================================================================
# 2. W3-FIND-05 & W3-FIND-07: CANONICAL FLIGHT CANCELLATION
# ==============================================================================

def test_canonical_flight_cancellation_quote_and_confirm():
    """Confirmed flight cancellation operates against canonical booking_intents."""
    client_user, user = _auth_user("flight_cancel@example.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user.id, title="Paris Vacation")
    trip_id = str(trip["id"])
    intent_id = str(uuid.uuid4())

    record = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": str(user.id),
        "status": "COMPLETED",
        "expected_amount": 50000.0,
        "currency": "INR",
        "provider": "duffel",
        "provider_reference": "REF-PARIS",
        "metadata": {
            "order_id": "ord_paris_999",
            "booking_reference": "REF-PARIS",
            "pnr": "PARPNR",
        },
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[record["id"]] = record

    # Step 1: Cancellation Quote
    with patch("services.flight_service.FlightService.cancellation_quote") as mock_quote:
        mock_quote.return_value = {
            "id": "cnc_quote_123",
            "refund_amount": "45000.00",
            "refund_currency": "INR",
            "expires_at": "2026-10-10T12:00:00Z",
        }
        quote_resp = client_user.post(
            "/api/trips/cancel/quote",
            json={"transaction_id": intent_id, "final_confirmation": True},
        )
        assert quote_resp.status_code == 200
        data = quote_resp.json()
        assert data["success"] is True
        assert data["quote"]["id"] == "cnc_quote_123"
        mock_quote.assert_called_once_with("ord_paris_999")

    # Step 2: Confirm Cancellation
    with patch("services.flight_service.FlightService.confirm_cancellation") as mock_confirm:
        mock_confirm.return_value = {
            "id": "cnc_conf_456",
            "refund_amount": "45000.00",
            "refund_currency": "INR",
            "confirmed_at": "2026-10-06T10:00:00Z",
        }
        confirm_resp = client_user.post(
            "/api/trips/cancel/confirm",
            json={
                "transaction_id": intent_id,
                "cancellation_id": "cnc_quote_123",
                "final_confirmation": True,
            },
        )
        assert confirm_resp.status_code == 200
        c_data = confirm_resp.json()
        assert c_data["success"] is True
        assert c_data["status"] == "cancelled"
        assert c_data["cancellation"]["id"] == "cnc_conf_456"
        # Rule 9: No Razorpay payment ID was present -> refund must be None, never fabricated!
        assert c_data["refund"] is None

    # Step 3: Verify local state transitioned to CANCELLED
    fb_service = FlightBookingService()
    updated_intent = fb_service._get_intent(intent_id, user.id)
    assert updated_intent["status"] == "CANCELLED"
    assert updated_intent["metadata"]["cancellation"]["id"] == "cnc_conf_456"
    assert "cancelled_at" in updated_intent["metadata"]

    # Step 4: Verify subsequent cancellation attempts fail closed with 409
    repeat_resp = client_user.post(
        "/api/trips/cancel/quote",
        json={"transaction_id": intent_id, "final_confirmation": True},
    )
    assert repeat_resp.status_code == 409
    assert "already cancelled" in repeat_resp.json()["detail"].lower()


def test_canonical_flight_cancellation_ownership_enforcement():
    """Cross-tenant flight cancellation attempt must be rejected with 403/404."""
    _, user1 = _auth_user("owner@example.com")
    client_user2, _ = _auth_user("attacker@example.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user1.id, title="User 1 Trip")
    trip_id = str(trip["id"])
    intent_id = str(uuid.uuid4())

    record = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": str(user1.id),
        "status": "COMPLETED",
        "provider": "duffel",
        "metadata": {"order_id": "ord_secret"},
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[record["id"]] = record

    # USER_2 attempts to cancel USER_1's booking intent
    resp = client_user2.post(
        "/api/trips/cancel/quote",
        json={"transaction_id": intent_id, "final_confirmation": True},
    )
    assert resp.status_code in (403, 404)


def test_canonical_flight_cancellation_provider_failure_does_not_mutate_state():
    """If Duffel cancellation fails, local intent state must NOT become CANCELLED."""
    client_user, user = _auth_user("fail_safe@example.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user.id, title="Trip")
    trip_id = str(trip["id"])
    intent_id = str(uuid.uuid4())

    record = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": str(user.id),
        "status": "COMPLETED",
        "provider": "duffel",
        "metadata": {"order_id": "ord_fail"},
    }
    FlightBookingService._IN_MEMORY_BOOKINGS[record["id"]] = record

    with patch("services.flight_service.FlightService.confirm_cancellation") as mock_confirm:
        mock_confirm.side_effect = FlightProviderError("Duffel 500 error: carrier unavailable")
        resp = client_user.post(
            "/api/trips/cancel/confirm",
            json={"transaction_id": intent_id, "cancellation_id": "cnc_1", "final_confirmation": True},
        )
        assert resp.status_code == 502

    # Verify state is STILL COMPLETED
    fb_service = FlightBookingService()
    intent = fb_service._get_intent(intent_id, user.id)
    assert intent["status"] == "COMPLETED"


# ==============================================================================
# 3. W3-FIND-05: HOTEL CANCELLATION WORKFLOW
# ==============================================================================

def test_hotel_cancellation_concierge_request_flow():
    """Hotel cancellation transitions state to cancellation_requested truthfully."""
    client_user, user = _auth_user("hotel_user@example.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user.id, title="Rome Stay")
    trip_id = str(trip["id"])
    hotel_id = f"htx_{uuid.uuid4().hex[:8]}"

    hotel_rec = {
        "id": hotel_id,
        "trip_id": trip_id,
        "customer_ref": str(user.id),
        "provider": "liteapi",
        "hotel_name": "Grand Hotel Roma",
        "booking_status": "confirmed",
        "amount_inr": 25000.0,
        "booking_json": {
            "booking_reference": "LT-ROME-1",
        },
    }
    BookingReadService.save_in_memory_hotel(hotel_rec)

    resp = client_user.post(
        "/api/trips/cancel/hotel",
        json={
            "transaction_id": hotel_id,
            "trip_id": trip_id,
            "reason": "Change of plans",
            "contact_email": "guest@example.com",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "cancellation_requested"
    assert data["provider_cancelled"] is False, "Hotelbeds/LiteAPI cannot be reported as automated cancellation"
    assert "submitted" in data["message"].lower()

    # Verify in memory record updated
    saved = BookingReadService._IN_MEMORY_HOTEL_BOOKINGS[hotel_id]
    assert saved["booking_status"] == "cancellation_requested"
    assert "cancellation_request" in saved["booking_json"]

    # Duplicate request reports existing pending status
    resp2 = client_user.post(
        "/api/trips/cancel/hotel",
        json={"transaction_id": hotel_id, "trip_id": trip_id},
    )
    assert resp2.status_code == 200
    assert "already pending" in resp2.json()["message"].lower()


def test_hotel_cancellation_cross_user_forbidden():
    """User 2 cannot request cancellation for User 1's hotel booking."""
    _, user1 = _auth_user("hotel_owner@example.com")
    client_user2, _ = _auth_user("hotel_attacker@example.com")

    hotel_id = f"htx_{uuid.uuid4().hex[:8]}"
    hotel_rec = {
        "id": hotel_id,
        "customer_ref": str(user1.id),
        "provider": "liteapi",
        "booking_status": "confirmed",
    }
    BookingReadService.save_in_memory_hotel(hotel_rec)

    resp = client_user2.post(
        "/api/trips/cancel/hotel",
        json={"transaction_id": hotel_id},
    )
    assert resp.status_code == 403


# ==============================================================================
# 4. W3-FIND-06 & W3-FIND-10: TRAVEL PLAN PDF CONTRACT
# ==============================================================================

def test_travel_plan_pdf_streaming_by_trip_id():
    """POST /api/travel/pdf with trip_id streams real binary PDF bytes."""
    client_user, user = _auth_user("pdf_user@example.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user.id,
        title="Tokyo Adventure",
        destination="Tokyo, Japan",
        origin="San Francisco, USA",
        metadata={
            "plan": {
                "title": "Tokyo Adventure",
                "destination": "Tokyo, Japan",
                "origin": "San Francisco, USA",
                "itinerary": [
                    {"day": 1, "description": "Arrival at Haneda and hotel check-in"},
                ],
            }
        },
    )
    trip_id = str(trip["id"])

    resp = client_user.post(
        "/api/travel/pdf",
        json={"trip_id": trip_id},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF"), "Response body must be real binary PDF data"


def test_travel_plan_pdf_trip_id_unauthorized():
    """Anonymous or cross-user request for travel plan PDF by trip_id fails."""
    _, user1 = _auth_user("owner_pdf@example.com")
    client_user2, _ = _auth_user("attacker_pdf@example.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user1.id, title="Secret Trip")
    trip_id = str(trip["id"])

    # Anonymous call
    anon_client = TestClient(app)
    resp = anon_client.post("/api/travel/pdf", json={"trip_id": trip_id})
    assert resp.status_code == 401

    # Cross-tenant call
    resp2 = client_user2.post("/api/travel/pdf", json={"trip_id": trip_id})
    assert resp2.status_code == 404


# ==============================================================================
# 5. W3-FIND-06: ZERO SYNTHETIC FALLBACK AUDIT
# ==============================================================================

def test_static_audit_zero_synthetic_fallbacks():
    """Ensure CN-XXXXXX and export.pdf are completely eradicated from frontend source."""
    with open("frontend/src/lib/apiClient.ts", "r", encoding="utf-8") as f:
        api_code = f.read()
    with open("frontend/src/mock/bookingMockData.ts", "r", encoding="utf-8") as f:
        mock_code = f.read()

    assert "CN-" not in api_code, "apiClient.ts must not fabricate CN- cancellation tokens"
    assert "CN-" not in mock_code, "bookingMockData.ts must not fabricate CN- cancellation tokens"
    assert "export.pdf" not in api_code, "apiClient.ts must not fabricate /api/travel/pdf/export.pdf"
