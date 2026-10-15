"""Regression test suite for Travel Plan PDF generation and Trip List authoritative truthfulness.

Validates:
Issue 1: Travel Plan PDF and Hotel Voucher Dates
1. Populated persisted trip produces a populated travel-plan PDF with actual destination, dates, daily plan, and budget.
2. Correct trip is used when a user has multiple trips.
3. Missing optional information is handled truthfully (no misleading zero-valued budgets or empty sections).
4. Hotel voucher dates are populated when authoritative booking data contains them.
5. Missing or unauthorized trip cannot generate another user's itinerary.

Issue 2: Elimination of Ghost Trips
6. A new account with no trips sees an empty state (zero trips).
7. One real trip produces one card.
8. Bookings (flights & hotels) attach to their parent trip and do not generate duplicate ghost draft cards.
9. A trip created by the current user persists and opens successfully.
10. Tenant isolation: User A cannot see or open User B's trips.
11. Refreshing the trip list does not restore ghost trips.
12. Existing confirmed bookings remain visible and intact.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService
from services.hotel_booking_pdf import build_hotel_booking_pdf
from services.hotel_transaction_store import HotelTransactionStore
from services.travel_plan_pdf import build_travel_plan_pdf
from services.trip_store import TripStore


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract decompressed text streams from a ReportLab PDF."""
    chunks: list[str] = []
    for match in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]*endstream", pdf_bytes, re.DOTALL):
        sb = match.group(1).strip()
        if b"~>" in sb:
            sb = sb[:sb.index(b"~>") + 2]
            try:
                raw = base64.a85decode(sb, adobe=True)
                chunks.append(zlib.decompress(raw).decode("latin1", errors="ignore"))
            except Exception:
                pass
        else:
            try:
                chunks.append(zlib.decompress(sb).decode("latin1", errors="ignore"))
            except Exception:
                pass
    return "\n".join(chunks)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    BookingReadService.clear_in_memory()


def _auth_user(email: str) -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


# ==============================================================================
# ISSUE 1: TRAVEL PLAN PDF AND HOTEL VOUCHER DATES
# ==============================================================================

def test_01_populated_persisted_trip_produces_populated_pdf():
    """A populated persisted trip produces a populated travel-plan PDF with actual data."""
    client, user = _auth_user("traveler1@example.com")
    trip_store = TripStore()

    plan_data = {
        "destination": "Jaipur",
        "origin": "Kolkata",
        "route": "Kolkata to Jaipur",
        "departure_date": "2026-10-16",
        "return_date": "2026-10-21",
        "travel_dates": "2026-10-16 to 2026-10-21",
        "duration_days": 6,
        "travel_style": "Cultural Heritage",
        "trip_summary": "Authentic exploration of Jaipur palaces and forts.",
        "flight_suggestion": "IndiGo 6E-205 CCU to JAI",
        "accommodation_plan": "Heritage Haveli in Old City",
        "weather_guidance": "Pleasant autumn temperatures around 28C.",
        "budget": {
            "flight_estimate_inr": 12000,
            "hotel_estimate_inr": 18000,
            "local_transport_estimate_inr": 3000,
            "activities_estimate_inr": 4500,
            "contingency_inr": 2500,
            "estimated_total_inr": 40000,
            "target_budget_inr": 45000,
            "within_budget": True,
        },
        "daily_plan": [
            {
                "day": 1,
                "title": "Arrival and Pink City Walk",
                "morning": "Flight from CCU to JAI, check in to haveli.",
                "afternoon": "Stroll through Johari Bazaar.",
                "evening": "Sunset at Nahargarh Fort.",
                "estimated_daily_cost_inr": 1500,
                "notes": ["Carry comfortable walking shoes"],
            },
            {
                "day": 2,
                "title": "Amber Fort and Stepwell",
                "morning": "Guided excursion of Amber Palace.",
                "afternoon": "Photography at Panna Meena Ka Kund.",
                "evening": "Dinner at 1135 AD.",
                "estimated_daily_cost_inr": 2500,
                "notes": ["Book morning ticket early"],
            },
        ],
    }

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Royal Jaipur Expedition",
        origin="Kolkata",
        destination="Jaipur",
        status="planning",
        metadata={
            "plan": plan_data,
            "constraints": {
                "start_date": "2026-10-16",
                "end_date": "2026-10-21",
                "origin": "Kolkata",
                "destination": "Jaipur",
            },
        },
    )

    resp = client.post("/api/travel/pdf", json={"trip_id": str(trip["id"])})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")

    text = extract_pdf_text(resp.content)
    assert "Jaipur" in text
    assert "Kolkata" in text
    assert "Amber Fort" in text
    assert "Nahargarh Fort" in text
    assert "12,000" in text or "18,000" in text  # Budget line items present
    assert "Day 1" in text
    assert "Day 2" in text


def test_02_correct_trip_used_when_user_has_multiple_trips():
    """When a user has multiple trips, requesting a PDF for trip A uses trip A's data, not trip B's."""
    client, user = _auth_user("multi_trip_user@example.com")
    trip_store = TripStore()

    trip_jaipur = trip_store.create_trip(
        user_id=user["id"],
        title="Jaipur Royal Journey",
        origin="CCU",
        destination="Jaipur",
        metadata={
            "plan": {
                "route": "CCU to Jaipur",
                "destination": "Jaipur",
                "trip_summary": "Jaipur Desert Oasis",
                "daily_plan": [{"day": 1, "title": "Hawa Mahal Visit"}],
            }
        },
    )
    trip_paris = trip_store.create_trip(
        user_id=user["id"],
        title="Parisian Getaway",
        origin="CCU",
        destination="Paris",
        metadata={
            "plan": {
                "route": "CCU to Paris",
                "destination": "Paris",
                "trip_summary": "Eiffel Tower Promenade",
                "daily_plan": [{"day": 1, "title": "Louvre Museum Visit"}],
            }
        },
    )

    # Request PDF for Jaipur
    resp_j = client.post("/api/travel/pdf", json={"trip_id": str(trip_jaipur["id"])})
    assert resp_j.status_code == 200
    text_j = extract_pdf_text(resp_j.content)
    assert "Jaipur" in text_j
    assert "Hawa Mahal" in text_j
    assert "Paris" not in text_j
    assert "Louvre" not in text_j

    # Request PDF for Paris
    resp_p = client.post("/api/travel/pdf", json={"trip_id": str(trip_paris["id"])})
    assert resp_p.status_code == 200
    text_p = extract_pdf_text(resp_p.content)
    assert "Paris" in text_p
    assert "Louvre" in text_p
    assert "Hawa Mahal" not in text_p


def test_03_missing_optional_information_handled_truthfully():
    """When budget or daily plan is absent, display clear, honest messages rather than misleading zeros."""
    client, user = _auth_user("bare_user@example.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Minimalist Trip",
        origin="Delhi",
        destination="Manali",
        metadata={
            "plan": {
                "destination": "Manali",
                "origin": "Delhi",
                "route": "Delhi to Manali",
                "daily_plan": [],  # Empty itinerary
                "budget": {},      # No budget breakdown
            }
        },
    )

    resp = client.post("/api/travel/pdf", json={"trip_id": str(trip["id"])})
    assert resp.status_code == 200
    text = extract_pdf_text(resp.content)

    # Must contain truthful notices
    assert "No day-by-day itinerary activities recorded" in text
    assert "No itemized budget estimates or targets recorded" in text

    # Must NOT have zero-valued flight/hotel table entries
    assert "Flights: ₹0" not in text
    assert "Hotels: ₹0" not in text


def test_04_hotel_voucher_dates_populated_when_authoritative_data_contains_them():
    """Hotel voucher PDF populates Check-in and Check-out dates from authoritative transaction data."""
    trip_id = str(uuid4())
    record = {
        "id": f"tx_ht_{uuid4().hex[:12]}",
        "trip_id": trip_id,
        "customer_ref": "cust_123",
        "provider": "liteapi",
        "hotel_name": "Samode Haveli",
        "booking_status": "booked",
        "amount_inr": 24500.0,
        "booking_json": {
            "booking_id": "LITE-BK-9921",
            "confirmation_code": "SMD-JAI-2026",
            "checkin": "2026-10-16",
            "checkout": "2026-10-21",
        },
        "offer_json": {
            "room_name": "Deluxe Heritage Suite",
            "board_type": "Breakfast Included",
            "checkin": "2026-10-16",
            "checkout": "2026-10-21",
            "rooms": 1,
        },
        "holder_json": {
            "firstName": "Supratik",
            "lastName": "Bhadra",
            "email": "supratik@example.com",
            "phone": "+919876543210",
        },
        "guests_json": [
            {"firstName": "Supratik", "lastName": "Bhadra", "is_child": False},
            {"firstName": "Ananya", "lastName": "Bhadra", "is_child": False},
        ],
    }

    pdf_bytes = build_hotel_booking_pdf(record)
    assert pdf_bytes.startswith(b"%PDF")

    text = extract_pdf_text(pdf_bytes)
    assert "2026-10-16" in text
    assert "2026-10-21" in text
    assert "Samode Haveli" in text
    assert "SMD-JAI-2026" in text
    assert "Deluxe Heritage Suite" in text


def test_05_unauthorized_user_cannot_generate_another_users_itinerary_pdf():
    """User B cannot generate or access User A's travel plan PDF."""
    client_a, user_a = _auth_user("alice@example.com")
    client_b, user_b = _auth_user("bob@example.com")
    trip_store = TripStore()

    trip_a = trip_store.create_trip(
        user_id=user_a["id"],
        title="Alice's Secret Getaway",
        destination="Goa",
    )

    # Bob requests Alice's trip PDF -> 404
    resp_b = client_b.post("/api/travel/pdf", json={"trip_id": str(trip_a["id"])})
    assert resp_b.status_code == 404

    # Anonymous user requests trip PDF -> 401
    anon_client = TestClient(app)
    resp_anon = anon_client.post("/api/travel/pdf", json={"trip_id": str(trip_a["id"])})
    assert resp_anon.status_code == 401


# ==============================================================================
# ISSUE 2: ELIMINATION OF GHOST TRIPS
# ==============================================================================

def test_06_new_account_with_no_trips_sees_empty_state():
    """A fresh user account sees an empty list of trips with zero ghost cards."""
    client, user = _auth_user("fresh_user@example.com")
    resp = client.get("/api/trips")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["trips"] == []


def test_07_one_real_trip_produces_one_card():
    """Creating one trip produces exactly one trip card."""
    client, user = _auth_user("single_trip_user@example.com")
    trip_store = TripStore()

    trip_store.create_trip(
        user_id=user["id"],
        title="Single Real Trip",
        destination="Udaipur",
        origin="Mumbai",
    )

    resp = client.get("/api/trips")
    assert resp.status_code == 200
    trips = resp.json().get("trips", [])
    assert len(trips) == 1
    assert trips[0]["destination"] == "Udaipur"
    assert trips[0]["record_type"] == "trip"


def test_08_duplicate_mock_backend_results_cannot_produce_duplicate_cards():
    """When a trip has booked flights and hotels, transactions do NOT spawn ghost 'Trip Journey' cards."""
    client, user = _auth_user("booked_traveler@example.com")
    trip_store = TripStore()
    uid = str(user["id"])

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Jaipur Booking Trip",
        destination="Jaipur",
        origin="Kolkata",
    )
    tid = str(trip["id"])

    # Simulate completed flight intent attached to this trip
    fl_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[fl_id] = {
        "id": fl_id,
        "trip_id": tid,
        "user_id": uid,
        "status": "COMPLETED",
        "provider_reference": "DL-6E205",
        "expected_amount": 14500.0,
        "currency": "INR",
        "metadata": {
            "booking_reference": "DL-6E205",
            "pnr": "6EPNR1",
            "passengers": [{"name": "Test Passenger"}],
        },
        "created_at": datetime.now(timezone.utc),
    }

    # Simulate booked hotel transaction attached to this trip
    ht_id = f"tx_ht_{uuid4().hex[:12]}"
    BookingReadService.save_in_memory_hotel({
        "id": ht_id,
        "trip_id": tid,
        "customer_ref": uid,
        "provider": "liteapi",
        "hotel_name": "Jaipur Heritage Haveli",
        "booking_status": "booked",
        "amount_inr": 22000.0,
        "booking_json": {
            "booking_id": "LITE-7711",
            "confirmation_code": "CONF-JAI-1",
        },
        "offer_json": {
            "hotel_name": "Jaipur Heritage Haveli",
            "room_name": "Deluxe Room",
            "checkin": "2026-10-16",
            "checkout": "2026-10-21",
        },
        "created_at": datetime.now(timezone.utc),
    })

    resp = client.get("/api/trips")
    assert resp.status_code == 200
    trips = resp.json().get("trips", [])

    # CRITICAL: exactly ONE trip must be returned (the real trip), NOT 3 trips!
    assert len(trips) == 1, f"Expected 1 trip, got {len(trips)}. Ghost transaction cards detected!"
    trip_item = trips[0]
    assert trip_item["id"] == tid
    assert trip_item["title"] == "Jaipur Booking Trip"
    assert trip_item["record_type"] == "trip"

    # Confirmed bookings must be attached inside the trip
    assert trip_item["flight_count"] == 1
    assert trip_item["hotel_count"] == 1
    assert len(trip_item["flights"]) == 1
    assert trip_item["flights"][0]["booking_reference"] == "DL-6E205"
    assert len(trip_item["hotels"]) == 1
    assert trip_item["hotels"][0]["confirmation_code"] == "CONF-JAI-1"

    # Verify no phantom "Trip Journey" draft cards exist
    titles = [t.get("title") for t in trips]
    assert "Trip Journey" not in titles


def test_09_trip_created_persists_and_opens_successfully():
    """A trip created via POST /api/trips persists and can be immediately opened via GET /api/trips/{id}."""
    client, user = _auth_user("creator@example.com")

    create_res = client.post(
        "/api/trips",
        json={
            "title": "Shimla Winter Expedition",
            "destination": "Shimla",
            "origin": "Chandigarh",
            "status": "planning",
            "metadata": {"travelers_count": 2},
        },
    )
    assert create_res.status_code == 201
    created_trip = create_res.json()["trip"]
    trip_id = created_trip["id"]

    # Open the trip immediately
    get_res = client.get(f"/api/trips/{trip_id}")
    assert get_res.status_code == 200
    opened = get_res.json()["trip"]
    assert opened["id"] == trip_id
    assert opened["destination"] == "Shimla"
    assert opened["origin"] == "Chandigarh"


def test_10_user_a_cannot_see_or_open_user_b_trips():
    """Tenant isolation: User A cannot see User B's trips in GET /api/trips or open them via GET /api/trips/{id}."""
    client_a, user_a = _auth_user("tenant_a@example.com")
    client_b, user_b = _auth_user("tenant_b@example.com")
    trip_store = TripStore()

    trip_a = trip_store.create_trip(
        user_id=user_a["id"],
        title="User A Trip",
        destination="Goa",
    )

    # User B lists trips -> Trip A must NOT appear
    b_trips = client_b.get("/api/trips").json().get("trips", [])
    assert not any(t["id"] == str(trip_a["id"]) for t in b_trips)

    # User B tries to open Trip A directly -> 404
    b_open = client_b.get(f"/api/trips/{trip_a['id']}")
    assert b_open.status_code == 404


def test_11_refreshing_trip_list_does_not_restore_ghost_trips():
    """Consecutive GET requests to /api/trips return identical authoritative lists without phantom trips."""
    client, user = _auth_user("refresher@example.com")
    trip_store = TripStore()

    trip_store.create_trip(
        user_id=user["id"],
        title="Varanasi Spiritual Walk",
        destination="Varanasi",
    )

    resp1 = client.get("/api/trips").json().get("trips", [])
    resp2 = client.get("/api/trips").json().get("trips", [])
    resp3 = client.get("/api/trips").json().get("trips", [])

    assert len(resp1) == 1
    assert len(resp2) == 1
    assert len(resp3) == 1
    assert resp1[0]["id"] == resp2[0]["id"] == resp3[0]["id"]
    assert "Trip Journey" not in [t["title"] for t in resp1]
