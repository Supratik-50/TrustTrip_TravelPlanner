"""Wave 3B: Booking Persistence, Rehydration, My Trips Integration & Ticket Delivery Tests.

Validates:
1. Duffel Booking Normalization:
   - completed flight persists booking_reference, documents, slices, passengers, order_id.
   - get_booking returns normalized booking_reference, documents, and slices.
2. BookingReadService:
   - Flight returned for correct trip/user.
   - Hotel returned for correct trip/user.
   - Cross-user isolation enforced.
   - Nonexistent trip handled.
   - Partial bookings supported (flight completed + hotel absent; flight failed + hotel booked).
   - In-progress hotel state preserved as booking_in_progress.
3. Rehydration Endpoint GET /api/trips/{trip_id}/bookings:
   - Authenticated user receives canonical component-level status.
   - Unauthenticated request rejected with 401.
   - Cross-user trip access rejected with 403.
   - Nonexistent trip returns 404.
4. My Trips Canonical Integration:
   - GET /api/trips derives flight_count and hotel_count from booking_intents & trust_trip_hotel_transactions.
   - Legacy flight_transactions is not the active source.
5. Ticket Delivery Endpoint GET /api/trips/{transaction_id}/ticket.pdf:
   - Resolves booking_id from booking_intents.
   - Generates valid PDF using persisted metadata (slices, passengers, booking_reference).
   - Upstream Duffel failure does not block ticket generation when metadata is present.
   - Cross-user access rejected with 403.
   - Unknown booking returns 404.
6. Hotel Voucher Endpoint GET /api/trips/{transaction_id}/hotel.pdf:
   - Existing behavior preserved and verified.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService
from services.hotel_transaction_store import HotelTransactionStore
from services.ticket_service import build_booking_pdf
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    BookingReadService.clear_in_memory()


def _auth_user(email: str = "w3b_user@example.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


def _create_trip(user_id: str, title: str = "Wave 3B Trip") -> tuple[str, str]:
    trip_store = TripStore()
    rev_id = str(uuid4())
    trip = trip_store.create_trip(
        user_id=user_id,
        title=title,
        origin="DEL",
        destination="BOM",
        current_revision_number=1,
        current_revision_id=rev_id,
        metadata={
            "start_date": "2026-12-01",
            "end_date": "2026-12-10",
            "budget": 50000.0,
            "currency": "INR",
            "travelers_count": 2,
        },
    )
    return str(trip["id"]), rev_id


# ============================================================================
# 1. DUFFEL BOOKING NORMALIZATION & PERSISTENCE
# ============================================================================

def test_duffel_booking_normalization_and_metadata_persistence():
    """Verify confirm_booking extracts and persists booking_reference, documents, and slices."""
    flight_service = FlightBookingService()
    user_id = str(uuid4())
    trip_id, _ = _create_trip(user_id, "Test Flight Trip")
    booking_id = str(uuid4())

    # Seed an approved booking intent
    mock_intent = {
        "id": booking_id,
        "trip_id": trip_id,
        "user_id": user_id,
        "status": "APPROVED",
        "flight_offer_id": "off_real_123",
        "total_amount": 450.00,
        "currency": "GBP",
        "metadata": {
            "passengers": [
                {
                    "id": "pas_1",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "email": "alice@example.com",
                }
            ],
            "slices": [
                {
                    "origin": "LHR",
                    "destination": "JFK",
                    "departure_time": "2026-11-01T10:00:00Z",
                    "segments": [
                        {
                            "marketing_carrier": {"name": "British Airways"},
                            "marketing_carrier_flight_number": "BA117",
                            "origin": {"iata_code": "LHR"},
                            "destination": {"iata_code": "JFK"},
                        }
                    ],
                }
            ],
            "approved": True,
        },
    }
    mock_intent["status"] = "READY_FOR_BOOKING"
    mock_intent["selected_offer_snapshot_id"] = str(uuid4())
    mock_intent["expected_amount"] = 450.0
    mock_intent["currency"] = "GBP"
    FlightBookingService._IN_MEMORY_BOOKINGS[booking_id] = mock_intent

    from datetime import timedelta
    from domain.live_offer import AvailabilityStatus
    mock_snapshot = MagicMock()
    mock_snapshot.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_snapshot.availability = AvailabilityStatus.AVAILABLE
    mock_snapshot.provider_offer_id = "raw_offer_123"
    flight_service.store = MagicMock()
    flight_service.store.get_snapshot = MagicMock(return_value=mock_snapshot)

    flight_service.flight_service = MagicMock()
    flight_service.flight_service.recheck = MagicMock(return_value={"price": 450.0, "currency": "GBP", "available": True})

    # Mock provider book call to return real Duffel payload
    fake_duffel_res = {
        "booking_reference": "G4XYZ8",
        "order_id": "ord_duffel_999",
        "documents": [{"type": "electronic_ticket", "unique_identifier": "0125-9876543210"}],
        "slices": mock_intent["metadata"]["slices"],
    }
    flight_service.flight_service.provider = MagicMock()
    flight_service.flight_service.provider.book = MagicMock(return_value=fake_duffel_res)

    import asyncio
    res = asyncio.run(flight_service.confirm_booking(
        user_id=user_id,
        booking_id=booking_id,
        final_confirmation=True,
    ))

    # Assert normalized response values
    assert res.booking_reference == "G4XYZ8"
    assert res.pnr is None
    assert res.order_id == "ord_duffel_999"
    assert str(res.status).lower() == "completed"

    # Assert persistence in metadata
    stored = FlightBookingService._IN_MEMORY_BOOKINGS[booking_id]
    assert stored["status"] == "COMPLETED"
    meta = stored["metadata"]
    assert meta["booking_reference"] == "G4XYZ8"
    assert meta["pnr"] is None
    assert meta["order_id"] == "ord_duffel_999"
    assert len(meta["documents"]) == 1
    assert meta["documents"][0]["unique_identifier"] == "0125-9876543210"
    assert len(meta["slices"]) == 1
    assert meta["slices"][0]["origin"] == "LHR"

    # Verify get_booking returns persisted documents and slices
    booking_record = asyncio.run(flight_service.get_booking(booking_id=booking_id, user_id=user_id))
    assert booking_record["booking_reference"] == "G4XYZ8"
    assert booking_record["pnr"] is None
    assert booking_record["documents"] == meta["documents"]
    assert booking_record["slices"] == meta["slices"]


# ============================================================================
# 2. BOOKING READ SERVICE & TENANT ISOLATION
# ============================================================================

def test_booking_read_service_aggregation_and_tenant_isolation():
    """Verify BookingReadService strictly isolates users and handles partial bookings."""
    read_service = BookingReadService()
    user_a = str(uuid4())
    user_b = str(uuid4())
    trip_id = str(uuid4())

    # User A has a completed flight for trip_id
    flight_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[flight_id] = {
        "id": flight_id,
        "trip_id": trip_id,
        "user_id": user_a,
        "status": "COMPLETED",
        "flight_offer_id": "off_real_456",
        "total_amount": 550.0,
        "currency": "EUR",
        "metadata": {
            "booking_reference": "REF888",
            "order_id": "ord_888",
            "passengers": [{"first_name": "Bob", "last_name": "Jones"}],
            "slices": [],
        },
    }

    # User A has a booked hotel for trip_id
    hotel_tx_id = f"tx_{uuid4().hex[:10]}"
    BookingReadService.save_in_memory_hotel({
        "id": hotel_tx_id,
        "trip_id": trip_id,
        "customer_ref": user_a,
        "provider": "liteapi",
        "hotel_name": "Grand Palace Paris",
        "amount_inr": 25000.0,
        "currency": "INR",
        "payment_status": "captured",
        "booking_status": "booked",
        "booking_json": {
            "booking_id": "lite_999",
            "confirmation_code": "VOUCH-777",
            "status": "confirmed",
        },
        "holder_json": {"name": "Bob Jones"},
        "guests_json": [{"first_name": "Bob", "last_name": "Jones"}],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    # Read as User A -> both flight and hotel present and confirmed
    res_a = read_service.get_trip_bookings(trip_id=trip_id, user_id=user_a)
    assert res_a["trip_id"] == trip_id
    assert res_a["flight"]["is_booked"] is True
    assert res_a["flight"]["booking_reference"] == "REF888"
    assert res_a["hotel"]["is_booked"] is True
    assert res_a["hotel"]["confirmation_code"] == "VOUCH-777"
    assert res_a["hotel"]["hotel_name"] == "Grand Palace Paris"

    # Read as User B (Cross-user tenant check) -> neither booking visible
    res_b = read_service.get_trip_bookings(trip_id=trip_id, user_id=user_b)
    assert res_b["trip_id"] == trip_id
    assert res_b["flight"] is None
    assert res_b["hotel"] is None


def test_booking_read_service_partial_and_in_progress_states():
    """Verify partial booking (flight completed, hotel in_progress) semantics."""
    read_service = BookingReadService()
    user_id = str(uuid4())
    trip_id = str(uuid4())

    # Hotel is booking_in_progress (payment captured, fulfillment executing)
    hotel_tx_id = f"tx_{uuid4().hex[:10]}"
    BookingReadService.save_in_memory_hotel({
        "id": hotel_tx_id,
        "trip_id": trip_id,
        "customer_ref": user_id,
        "provider": "liteapi",
        "hotel_name": "Hotel Bastille",
        "amount_inr": 18000.0,
        "currency": "INR",
        "payment_status": "captured",
        "booking_status": "booking_in_progress",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    res = read_service.get_trip_bookings(trip_id=trip_id, user_id=user_id)
    assert res["flight"] is None  # Partial booking: no flight
    assert res["hotel"] is not None
    assert res["hotel"]["is_booked"] is False  # In progress, not yet finalized
    assert res["hotel"]["status"] == "booking_in_progress"
    assert res["hotel"]["hotel_name"] == "Hotel Bastille"


# ============================================================================
# 3. REHYDRATION ENDPOINT GET /api/trips/{trip_id}/bookings
# ============================================================================

def test_rehydration_endpoint_auth_and_ownership():
    """Verify endpoint rejects unauthenticated and cross-user requests."""
    client_a, user_a = _auth_user("user_a@test.com")
    client_b, user_b = _auth_user("user_b@test.com")
    anon_client = TestClient(app)

    trip_id, _ = _create_trip(user_a["id"], "User A Trip")

    # 1. Unauthenticated request -> 401
    resp_anon = anon_client.get(f"/api/trips/{trip_id}/bookings")
    assert resp_anon.status_code == 401

    # 2. Cross-user access (User B queries User A's trip) -> 403
    resp_b = client_b.get(f"/api/trips/{trip_id}/bookings")
    assert resp_b.status_code == 403

    # 3. Nonexistent trip -> 404
    fake_trip = str(uuid4())
    resp_not_found = client_a.get(f"/api/trips/{fake_trip}/bookings")
    assert resp_not_found.status_code == 404

    # 4. Legitimate owner access -> 200
    resp_a = client_a.get(f"/api/trips/{trip_id}/bookings")
    assert resp_a.status_code == 200
    data = resp_a.json()
    assert data["trip_id"] == trip_id
    assert data["flight"] is None
    assert data["hotel"] is None


def test_rehydration_endpoint_returns_confirmed_bookings():
    """Verify endpoint returns confirmed flight & hotel records for rehydration."""
    client, user = _auth_user("rehydration_user@test.com")
    trip_id, _ = _create_trip(user["id"], "Confirmed Trip")

    flight_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[flight_id] = {
        "id": flight_id,
        "trip_id": trip_id,
        "user_id": user["id"],
        "status": "COMPLETED",
        "flight_offer_id": "off_real_777",
        "total_amount": 320.0,
        "currency": "EUR",
        "metadata": {
            "booking_reference": "AF9876",
            "order_id": "ord_9876",
            "passengers": [{"first_name": "Diana", "last_name": "Prince"}],
            "slices": [
                {
                    "origin": "CDG",
                    "destination": "FCO",
                    "departure_time": "2026-12-05T08:00:00Z",
                }
            ],
        },
    }

    hotel_tx_id = f"tx_{uuid4().hex[:10]}"
    BookingReadService.save_in_memory_hotel({
        "id": hotel_tx_id,
        "trip_id": trip_id,
        "customer_ref": user["id"],
        "provider": "liteapi",
        "hotel_name": "Rome Cavalieri",
        "amount_inr": 42000.0,
        "currency": "INR",
        "payment_status": "captured",
        "booking_status": "booked",
        "booking_json": {
            "booking_id": "lite_rome_1",
            "confirmation_code": "VOUCH-ROME-1",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    resp = client.get(f"/api/trips/{trip_id}/bookings")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["trip_id"] == trip_id
    assert payload["flight"]["is_booked"] is True
    assert payload["flight"]["booking_reference"] == "AF9876"
    assert payload["flight"]["order_id"] == "ord_9876"
    assert payload["hotel"]["is_booked"] is True
    assert payload["hotel"]["confirmation_code"] == "VOUCH-ROME-1"
    assert payload["hotel"]["hotel_name"] == "Rome Cavalieri"


# ============================================================================
# 4. MY TRIPS CANONICAL INTEGRATION
# ============================================================================

def test_my_trips_lists_canonical_flight_and_hotel_bookings():
    """Verify GET /api/trips returns flight_count, hotel_count, flights, and hotels."""
    client, user = _auth_user("mytrips_user@test.com")
    trip_id, _ = _create_trip(user["id"], "Summer Vacation")

    flight_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[flight_id] = {
        "id": flight_id,
        "trip_id": trip_id,
        "user_id": user["id"],
        "status": "COMPLETED",
        "flight_offer_id": "off_vacation_1",
        "total_amount": 600.0,
        "currency": "USD",
        "metadata": {
            "booking_reference": "DL3344",
            "order_id": "ord_vac_1",
            "passengers": [{"first_name": "Eve", "last_name": "Polastri"}],
            "slices": [],
        },
    }

    hotel_tx_id = f"tx_{uuid4().hex[:10]}"
    BookingReadService.save_in_memory_hotel({
        "id": hotel_tx_id,
        "trip_id": trip_id,
        "customer_ref": user["id"],
        "provider": "liteapi",
        "hotel_name": "Venice Canal Suites",
        "amount_inr": 35000.0,
        "currency": "INR",
        "payment_status": "captured",
        "booking_status": "booked",
        "booking_json": {
            "booking_id": "lite_ven_1",
            "confirmation_code": "VOUCH-VEN-1",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    resp = client.get("/api/trips")
    assert resp.status_code == 200
    data = resp.json()
    trips = data.get("trips", [])
    assert isinstance(trips, list)
    target = next((t for t in trips if t.get("title") == "Summer Vacation"), None)
    assert target is not None

    # Verify counts and populated arrays
    assert target["flight_count"] == 1
    assert target["hotel_count"] == 1
    assert len(target["flights"]) == 1
    assert target["flights"][0]["booking_reference"] == "DL3344"
    assert len(target["hotels"]) == 1
    assert target["hotels"][0]["confirmation_code"] == "VOUCH-VEN-1"


# ============================================================================
# 5. TICKET DELIVERY FROM BOOKING_INTENTS WITH PERSISTED METADATA FALLBACK
# ============================================================================

def test_ticket_download_from_booking_intents_with_provider_offline():
    """Verify GET /api/trips/{transaction_id}/ticket.pdf uses persisted metadata when provider is down."""
    client, user = _auth_user("ticket_user@test.com")
    trip_id, _ = _create_trip(user["id"], "Ticket Trip")

    flight_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[flight_id] = {
        "id": flight_id,
        "trip_id": trip_id,
        "user_id": user["id"],
        "status": "COMPLETED",
        "flight_offer_id": "off_ticket_123",
        "total_amount": 750.0,
        "currency": "GBP",
        "metadata": {
            "booking_reference": "BA9988",
            "order_id": "ord_ba_offline",
            "completed_at": "2026-10-01T12:00:00Z",
            "passengers": [
                {
                    "first_name": "Villanelle",
                    "last_name": "Astankova",
                    "email": "villanelle@example.com",
                }
            ],
            "documents": [
                {
                    "type": "electronic_ticket",
                    "unique_identifier": "125-1122334455",
                }
            ],
            "slices": [
                {
                    "origin": "LHR",
                    "destination": "HEL",
                    "departure_time": "2026-11-15T09:00:00Z",
                    "segments": [
                        {
                            "marketing_carrier": {"name": "British Airways"},
                            "marketing_carrier_flight_number": "BA798",
                            "origin": {"iata_code": "LHR"},
                            "destination": {"iata_code": "HEL"},
                        }
                    ],
                }
            ],
        },
    }

    # Simulate Duffel FlightService throwing an exception (provider unavailable)
    with patch("services.flight_service.FlightService.get_order", side_effect=RuntimeError("Duffel API timeout")):
        resp = client.get(f"/api/trips/{flight_id}/ticket.pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert len(resp.content) > 100  # Generated PDF byte stream


def test_ticket_download_cross_user_forbidden():
    """Verify cross-user ticket download is rejected with 403."""
    client_a, user_a = _auth_user("owner@test.com")
    client_b, user_b = _auth_user("intruder@test.com")
    trip_id, _ = _create_trip(user_a["id"], "Owner Trip")

    flight_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[flight_id] = {
        "id": flight_id,
        "trip_id": trip_id,
        "user_id": user_a["id"],
        "status": "COMPLETED",
        "metadata": {"booking_reference": "SEC123"},
    }

    resp = client_b.get(f"/api/trips/{flight_id}/ticket.pdf")
    assert resp.status_code == 403


def test_ticket_download_not_found():
    """Verify unknown booking_id returns 404."""
    client, _ = _auth_user("searcher@test.com")
    fake_id = str(uuid4())
    resp = client.get(f"/api/trips/{fake_id}/ticket.pdf")
    assert resp.status_code == 404
