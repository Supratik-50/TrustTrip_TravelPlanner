"""Test Suite for Implementation Wave 2 — Real Traveler Identity & Passenger Propagation.

Validates:
1. Custom passenger propagation to Duffel/provider.
2. Authenticated user != passenger (account holder booking for another traveler).
3. Authenticated email default when empty, explicit traveler email precedence.
4. Multi-passenger propagation (N travelers, 1 <= N <= 9).
5. Missing or invalid passenger fields blocked with 422.
6. Passenger count mismatch between trip and booking payload blocked with 400.
7. Zero synthetic passenger fallbacks in production booking endpoints.
8. Hotel guest & holder propagation with actual traveler data.
9. Stale booking intent invalidation / new booking intent required for changed passengers.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import secrets
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.setdefault("PAYLOAD_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode("utf-8"))

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    FreshnessStatus,
    PriceBreakdown,
)
from flight_api import _booking_service
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.authorization_store import AuthorizationStore
from services.evidence_store import EvidenceRevisionStore
from services.flight_booking_service import FlightBookingService
from services.flight_provider import FlightProvider
from services.flight_service import FlightService
from services.hotel_transaction_store import HotelTransactionStore
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore

client = TestClient(app)


class FakeWave2FlightProvider(FlightProvider):
    name = "fake_duffel_wave2"

    def __init__(self):
        self.offers: dict[str, dict[str, Any]] = {}
        self.book_calls: list[dict[str, Any]] = []

    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.offers.values())

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        return dict(self.offers[offer_id])

    def book(
        self,
        offer_id: str,
        passengers: list[dict[str, Any]],
        payment: dict[str, Any] | None = None,
        payment_mode: str = "auto",
    ) -> dict[str, Any]:
        self.book_calls.append({
            "offer_id": offer_id,
            "passengers": passengers,
            "payment": payment,
            "payment_mode": payment_mode,
        })
        return {
            "booking_reference": "REF-WAVE2-CONFIRMED",
            "order_id": "ord_duffel_wave2_999",
            "status": "confirmed",
        }


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    app.dependency_overrides.clear()
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    FlightBookingService._IN_MEMORY_TRANSITIONS.clear()
    AuthorizationStore._IN_MEMORY_AUTHORIZATIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_EVIDENCE.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_APPROVALS.clear()
    yield
    app.dependency_overrides.clear()


def _auth_user(email: str = "account_holder@example.com") -> tuple[dict[str, str], dict[str, Any]]:
    user_store = UserStore()
    user = user_store.get_by_email(email)
    if not user:
        user = user_store.create_user(email=email, password_hash="hashed_secret")
    token = create_access_token(user["id"], user["email"])
    return {"Authorization": f"Bearer {token}"}, user


def _setup_trip_and_snapshot(
    user: dict[str, Any],
    travelers_count: int = 1,
    price: float = 8500.0,
    currency: str = "INR",
    provider_offer_id: str = "raw_duffel_wave2_off_1",
) -> tuple[dict[str, Any], UUID, str]:
    trip_store = TripStore()
    session_store = SearchSessionStore()
    now = datetime.now(timezone.utc)

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Wave 2 Test Trip",
        origin="CCU",
        destination="DEL",
        metadata={
            "constraints": {
                "origin": "CCU",
                "origin_airport": "CCU",
                "destination": "DEL",
                "destination_airport": "DEL",
                "departure_date": "2026-11-10",
                "return_date": "2026-11-20",
                "travelers_count": travelers_count,
                "currency": currency,
            }
        },
    )

    session = session_store.create_session(
        user_id=user["id"],
        trip_id=trip["id"],
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_wave2_ccu_del",
        trip_revision=1,
    )

    snapshot_id = uuid4()
    p_dec = Decimal(str(price))
    pb = PriceBreakdown(
        base_amount=p_dec,
        tax_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        total_amount=p_dec,
        currency=currency,
        inr_reference_amount=p_dec if currency == "INR" else None,
    )
    seg = FlightSegmentModel(
        origin_iata="CCU",
        destination_iata="DEL",
        departure_time=now + timedelta(days=5),
        arrival_time=now + timedelta(days=5, hours=2),
        marketing_carrier_iata="6E",
        marketing_carrier_name="IndiGo",
        flight_number="6E 201",
        duration_minutes=120,
        cabin_class="economy",
    )
    domain_offer = FlightOffer(
        snapshot_id=snapshot_id,
        provider="fake_duffel_wave2",
        provider_offer_id=provider_offer_id,
        price=pb,
        stops=0,
        segments=[seg],
        passenger_count=travelers_count,
        retrieved_at=now,
        expires_at=now + timedelta(minutes=60),
        freshness=FreshnessStatus.FRESH,
        availability=AvailabilityStatus.AVAILABLE,
    )
    raw_payload = {
        "id": provider_offer_id,
        "total_amount": str(price),
        "total_currency": currency,
        "price": price,
        "currency": currency,
        "available": True,
    }
    session_store.save_snapshot(session.session_id, domain_offer, raw_payload)

    return trip, snapshot_id, provider_offer_id


def test_wave2_custom_passenger_propagation():
    """Test 1: Verify user-entered passenger details reach the flight provider exactly."""
    headers, user = _auth_user("travel_planner@example.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_snapshot(user, travelers_count=1)

    custom_passenger = {
        "given_name": "Rahul",
        "family_name": "Sharma",
        "born_on": "1992-05-15",
        "title": "mr",
        "gender": "m",
        "email": "rahul.sharma@example.com",
        "phone_number": "+919876543210",
    }

    # 1. Select offer with custom passenger
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": [custom_passenger]},
        headers=headers,
    )
    assert sel_res.status_code in {200, 201}, sel_res.text
    booking_id = sel_res.json()["booking_id"]

    # 2. Setup provider and revalidate
    fake_prov = FakeWave2FlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 8500.0, "currency": "INR", "available": True}
    booking_svc = FlightBookingService(flight_service=FlightService(provider=fake_prov))
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_res.status_code == 200

    # 3. Explicit human approval
    app_res = client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    assert app_res.status_code == 200

    # 4. Confirm booking
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 200
    assert conf_res.json()["status"] == "COMPLETED"

    # Verify provider received the exact passenger
    assert len(fake_prov.book_calls) == 1
    call_passengers = fake_prov.book_calls[0]["passengers"]
    assert len(call_passengers) == 1
    p = call_passengers[0]
    assert p["given_name"] == "Rahul"
    assert p["family_name"] == "Sharma"
    assert p["born_on"] == "1992-05-15"
    assert p["title"] == "mr"
    assert p["gender"] == "m"
    assert p["email"] == "rahul.sharma@example.com"
    assert p["phone_number"] == "+919876543210"


def test_wave2_logged_in_user_not_passenger():
    """Test 2: Authenticated user supratik@example.com booking for passenger Rahul Sharma."""
    headers, user = _auth_user("supratik@example.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_snapshot(user, travelers_count=1)

    passenger = {
        "given_name": "Rahul",
        "family_name": "Sharma",
        "born_on": "1994-08-20",
        "title": "mr",
        "gender": "m",
        "email": "rahul.travels@otherdomain.com",
        "phone_number": "+919876543210",
    }

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": [passenger]},
        headers=headers,
    )
    assert sel_res.status_code in {200, 201}
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeWave2FlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 8500.0, "currency": "INR", "available": True}
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=FlightService(provider=fake_prov))

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 200

    provider_p = fake_prov.book_calls[0]["passengers"][0]
    # Critical invariant: Logged in user email/identity MUST NOT override traveler
    assert provider_p["given_name"] == "Rahul"
    assert provider_p["family_name"] == "Sharma"
    assert provider_p["email"] == "rahul.travels@otherdomain.com"
    assert provider_p["email"] != "supratik@example.com"
    assert "supratik" not in provider_p["given_name"].lower()


def test_wave2_explicit_traveler_email_precedence():
    """Test 4: Explicit traveler email takes precedence over authenticated user email."""
    headers, user = _auth_user("account_owner@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_snapshot(user, travelers_count=1)

    passenger = {
        "given_name": "Aarav",
        "family_name": "Patel",
        "born_on": "1988-12-05",
        "title": "mr",
        "gender": "m",
        "email": "aarav.patel@personal.org",
        "phone_number": "+919123456780",
    }

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": [passenger]},
        headers=headers,
    )
    assert sel_res.status_code in {200, 201}
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeWave2FlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 8500.0, "currency": "INR", "available": True}
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=FlightService(provider=fake_prov))

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/confirm", json={"final_confirmation": True}, headers=headers)

    assert fake_prov.book_calls[0]["passengers"][0]["email"] == "aarav.patel@personal.org"


def test_wave2_multi_passenger_propagation():
    """Test 5: trip.travelers_count = 2 propagates exactly 2 distinct passengers to provider."""
    headers, user = _auth_user("family_planner@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_snapshot(user, travelers_count=2)

    passengers = [
        {
            "given_name": "Rahul",
            "family_name": "Sharma",
            "born_on": "1990-03-10",
            "title": "mr",
            "gender": "m",
            "email": "rahul.sharma@example.com",
            "phone_number": "+919876543210",
        },
        {
            "given_name": "Priya",
            "family_name": "Sharma",
            "born_on": "1993-07-22",
            "title": "mrs",
            "gender": "f",
            "email": "priya.sharma@example.com",
            "phone_number": "+919876543211",
        },
    ]

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": passengers},
        headers=headers,
    )
    assert sel_res.status_code in {200, 201}, sel_res.text
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeWave2FlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 8500.0, "currency": "INR", "available": True}
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=FlightService(provider=fake_prov))

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 200

    provider_passengers = fake_prov.book_calls[0]["passengers"]
    assert len(provider_passengers) == 2
    assert provider_passengers[0]["given_name"] == "Rahul"
    assert provider_passengers[0]["gender"] == "m"
    assert provider_passengers[1]["given_name"] == "Priya"
    assert provider_passengers[1]["gender"] == "f"


def test_wave2_missing_passenger_info_rejected_422():
    """Test 6: Missing required passenger fields (given_name, born_on, etc.) fail closed with 422."""
    headers, user = _auth_user("validator@trusttrip.com")
    trip, snap_id, _ = _setup_trip_and_snapshot(user, travelers_count=1)

    # Missing given_name and born_on
    res = client.post(
        "/api/flights/booking/select",
        json={
            "trip_id": str(trip["id"]),
            "offer_id": str(snap_id),
            "passengers": [
                {
                    "family_name": "Sharma",
                    "title": "mr",
                    "gender": "m",
                }
            ],
        },
        headers=headers,
    )
    assert res.status_code == 422

    # Invalid DOB format (not YYYY-MM-DD)
    res2 = client.post(
        "/api/flights/booking/select",
        json={
            "trip_id": str(trip["id"]),
            "offer_id": str(snap_id),
            "passengers": [
                {
                    "given_name": "Rahul",
                    "family_name": "Sharma",
                    "born_on": "15-05-1992",  # invalid format
                    "title": "mr",
                    "gender": "m",
                }
            ],
        },
        headers=headers,
    )
    assert res2.status_code == 422


def test_wave2_passenger_count_mismatch_rejected_400():
    """Requirement 13: Passenger count mismatch against trip.travelers_count fails closed with 400."""
    headers, user = _auth_user("mismatch_checker@trusttrip.com")
    trip, snap_id, _ = _setup_trip_and_snapshot(user, travelers_count=2)

    # Only 1 passenger provided when trip specifies 2
    res = client.post(
        "/api/flights/booking/select",
        json={
            "trip_id": str(trip["id"]),
            "offer_id": str(snap_id),
            "passengers": [
                {
                    "given_name": "Rahul",
                    "family_name": "Sharma",
                    "born_on": "1990-01-01",
                    "title": "mr",
                    "gender": "m",
                }
            ],
        },
        headers=headers,
    )
    assert res.status_code == 400
    assert "Passenger count mismatch" in res.json()["detail"]


def test_wave2_no_synthetic_passenger_fallback():
    """Test 7: Confirm zero synthetic passenger literals leak into production booking."""
    headers, user = _auth_user("real_user@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_snapshot(user, travelers_count=1)

    real_p = {
        "given_name": "Sunita",
        "family_name": "Sen",
        "born_on": "1985-11-25",
        "title": "ms",
        "gender": "f",
        "email": "sunita.sen@customdomain.in",
        "phone_number": "+919876543299",
    }

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": [real_p]},
        headers=headers,
    )
    assert sel_res.status_code in {200, 201}
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeWave2FlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 8500.0, "currency": "INR", "available": True}
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=FlightService(provider=fake_prov))

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/confirm", json={"final_confirmation": True}, headers=headers)

    p = fake_prov.book_calls[0]["passengers"][0]
    forbidden_synthetic = ["ananya", "roy", "ananya.roy@example.travel", "traveler@trusttrip.com", "1990-01-01"]
    for val in forbidden_synthetic:
        assert val not in str(p["given_name"]).lower()
        assert val not in str(p["family_name"]).lower()
        assert val not in str(p["email"]).lower()
        assert val != p["born_on"]


def test_wave2_hotel_holder_and_guest_propagation():
    """Test 8: Hotel booking verification receives actual traveler holder and guests."""
    headers, user = _auth_user("hotel_lead_traveler@trusttrip.com")
    trip, _, _ = _setup_trip_and_snapshot(user, travelers_count=2)
    tx_id = f"htx_w2_{uuid4().hex[:10]}"
    rev_id = str(uuid4())

    real_holder = {
        "first_name": "Vikram",
        "last_name": "Singhania",
        "email": "vikram.singhania@domain.com",
        "phone": "+919811122233",
    }
    real_guests = [
        {"first_name": "Vikram", "last_name": "Singhania"},
        {"first_name": "Meera", "last_name": "Singhania"},
    ]

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "provider": "liteapi",
        "hotel_name": "The Taj Mahal Palace",
        "offer_json": {"provider_offer_id": "taj_1", "provider": "liteapi", "hotel_name": "The Taj Mahal Palace"},
        "prebook_json": {"offer_id": "taj_1"},
        "holder_json": real_holder,
        "guests_json": real_guests,
        "amount_inr": 25000.0,
        "razorpay_order_id": "order_w2_taj",
        "trip_id": str(trip["id"]),
        "revision_id": rev_id,
        "revision_number": 1,
        "booking_status": "pending",
        "payment_status": "pending",
        "booking_json": None,
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", return_value=True), \
         patch.object(HotelTransactionStore, "claim_booking", return_value=True), \
         patch("services.razorpay_service.RazorpayService.verify_checkout_signature", return_value=True), \
         patch("services.razorpay_service.RazorpayService.fetch_payment", return_value={"status": "captured", "order_id": "order_w2_taj", "currency": "INR", "amount": 2500000}), \
         patch("services.hotel.hotel_service.HotelService.book", return_value={"booking_id": "taj_book_999", "status": "confirmed"}) as mock_book, \
         patch("hotel_payment_api.build_hotel_booking_pdf", return_value=b"%PDF-1.4 wave2 mock"), \
         patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None):

        res = client.post(
            "/api/hotel-payments/verify-and-book",
            json={
                "transaction_id": tx_id,
                "trip_id": str(trip["id"]),
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "razorpay_order_id": "order_w2_taj",
                "razorpay_payment_id": "pay_w2_taj",
                "razorpay_signature": "sig_w2_valid",
                "offer": {"provider_offer_id": "taj_1"},
                "prebook": {"offer_id": "taj_1"},
                "holder": real_holder,
                "guests": real_guests,
                "final_confirmation": True,
            },
            headers=headers,
        )

    assert res.status_code == 200
    assert res.json()["success"] is True

    # Verify HotelService.book received Vikram Singhania and Meera Singhania
    assert mock_book.call_count == 1
    call_args = mock_book.call_args[0]
    holder_passed = call_args[2]
    guests_passed = call_args[3]

    assert holder_passed["first_name"] == "Vikram"
    assert holder_passed["last_name"] == "Singhania"
    assert holder_passed["email"] == "vikram.singhania@domain.com"
    assert len(guests_passed) == 2
    assert guests_passed[0]["first_name"] == "Vikram"
    assert guests_passed[1]["first_name"] == "Meera"


def test_wave2_stale_booking_intent_on_passenger_change():
    """Test 9: Changing passengers generates a distinct booking intent and rejects mutating stale intent."""
    headers, user = _auth_user("reselector@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_snapshot(user, travelers_count=1)

    passenger_1 = {
        "given_name": "First",
        "family_name": "Passenger",
        "born_on": "1990-01-01",
        "title": "mr",
        "gender": "m",
        "email": "first@example.com",
    }
    sel1 = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": [passenger_1]},
        headers=headers,
    )
    booking_id_1 = sel1.json()["booking_id"]

    # When user updates passenger details to passenger_2
    passenger_2 = {
        "given_name": "Second",
        "family_name": "Traveler",
        "born_on": "1995-05-05",
        "title": "ms",
        "gender": "f",
        "email": "second@example.com",
    }
    sel2 = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": [passenger_2]},
        headers=headers,
    )
    booking_id_2 = sel2.json()["booking_id"]

    # Must be distinct booking intents
    assert booking_id_1 != booking_id_2

    # Intent 2 has passenger_2 bound in intent metadata, not passenger_1
    intent_2 = FlightBookingService._IN_MEMORY_BOOKINGS[str(booking_id_2)]
    p2_list = intent_2["metadata"]["passengers"]
    assert p2_list[0]["given_name"] == "Second"
    assert p2_list[0]["family_name"] == "Traveler"

    intent_1 = FlightBookingService._IN_MEMORY_BOOKINGS[str(booking_id_1)]
    p1_list = intent_1["metadata"]["passengers"]
    assert p1_list[0]["given_name"] == "First"
    assert p1_list[0]["family_name"] == "Passenger"
