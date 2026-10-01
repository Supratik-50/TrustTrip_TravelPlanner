"""Focused Test Suite for Section 5B — Flight Booking Vertical Slice.

Verifies:
1. Valid offer selection (canonical trip, persisted snapshot, passenger validation).
2. Unknown offer_id rejected with 404.
3. Offer ownership / tenant mismatch rejected with 403.
4. Trip mismatch rejected with 400.
5. Stale / expired offer rejected with 410.
6. Revalidation detecting unavailable / withdrawn inventory.
7. Revalidation detecting changed price.
8. Revalidation succeeding and transitioning to READY_FOR_BOOKING.
9. Explicit human approval (HITL) recording.
10. Booking blocked without approval.
11. Booking blocked without final user confirmation.
12. Immediate pre-booking revalidation detecting price changes.
13. Immediate pre-booking revalidation detecting offer withdrawal.
14. Successful booking path using mocked provider (status COMPLETED, booking reference returned).
15. Upstream provider booking failure handled cleanly (502, status FAILED).
16. Duplicate confirmation protection (409, no second booking call).
17. Raw provider ID shielding across all responses.
18. Authoritative booking status retrieval via GET.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import secrets
from typing import Any
from unittest.mock import MagicMock, patch
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
from services.flight_booking_service import FlightBookingService
from services.flight_provider import FlightProvider, FlightProviderError, FlightProviderTimeout
from services.flight_service import FlightService
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore

client = TestClient(app)


# ============================================================================
# FAKE PROVIDER FOR TESTS
# ============================================================================

class FakeBookingFlightProvider(FlightProvider):
    name = "fake_duffel"

    def __init__(self):
        self.offers: dict[str, dict[str, Any]] = {}
        self.book_calls: list[dict[str, Any]] = []
        self.fail_booking = False
        self.fail_timeout = False

    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.offers.values())

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        if offer_id not in self.offers:
            raise FlightProviderError(f"Offer '{offer_id}' not found.")
        return dict(self.offers[offer_id])

    def book(
        self,
        offer_id: str,
        passengers: list[dict[str, Any]],
        payment: dict[str, Any] | None = None,
        payment_mode: str = "auto",
    ) -> dict[str, Any]:
        if self.fail_timeout:
            raise FlightProviderTimeout("Upstream provider timed out during order creation.")
        if self.fail_booking:
            raise FlightProviderError("Provider rejected booking: card or inventory error.")
        self.book_calls.append({
            "offer_id": offer_id,
            "passengers": passengers,
            "payment": payment,
            "payment_mode": payment_mode,
        })
        return {
            "booking_reference": "REF-TT-9988",
            "order_id": "ord_duffel_test_456",
            "status": "confirmed",
        }


# ============================================================================
# FIXTURES & HELPERS
# ============================================================================

@pytest.fixture(autouse=True)
def clean_in_memory_state(monkeypatch):
    """Ensure clean in-memory state for isolated testing."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    app.dependency_overrides.clear()
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    FlightBookingService._IN_MEMORY_TRANSITIONS.clear()
    yield
    app.dependency_overrides.clear()


def _auth_user(email: str = "traveler@trusttrip.com") -> tuple[dict[str, str], dict[str, Any]]:
    user_store = UserStore()
    user = user_store.get_by_email(email)
    if not user:
        user = user_store.create_user(email=email, password_hash="hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}
    return headers, user


def _setup_trip_and_persisted_snapshot(
    user: dict[str, Any],
    price: float = 7500.0,
    currency: str = "INR",
    provider_offer_id: str = "raw_prov_secret_offer_123",
    expires_in_minutes: int = 30,
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE,
) -> tuple[dict[str, Any], UUID, str]:
    """Helper to persist a canonical trip, search session, and offer snapshot."""
    trip_store = TripStore()
    session_store = SearchSessionStore()
    now = datetime.now(timezone.utc)

    # 1. Create Trip
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Delhi Trip",
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
                "travelers_count": 1,
                "currency": currency,
            }
        },
    )

    # 2. Create SearchSession
    session = session_store.create_session(
        user_id=user["id"],
        trip_id=trip["id"],
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_ccu_del",
        trip_revision=1,
    )

    # 3. Create FlightOffer & Snapshot
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
        provider="duffel",
        provider_offer_id=provider_offer_id,
        price=pb,
        stops=0,
        segments=[seg],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now + timedelta(minutes=expires_in_minutes),
        freshness=FreshnessStatus.FRESH,
        availability=availability,
    )
    raw_payload = {
        "id": provider_offer_id,
        "total_amount": str(price),
        "total_currency": currency,
        "price": price,
        "currency": currency,
        "available": availability == AvailabilityStatus.AVAILABLE,
    }
    session_store.save_snapshot(session.session_id, domain_offer, raw_payload)

    return trip, snapshot_id, provider_offer_id


def _sample_passenger():
    return [{
        "given_name": "Rohan",
        "family_name": "Sharma",
        "born_on": "1992-05-15",
        "title": "mr",
        "gender": "m",
        "email": "rohan@example.com",
    }]


# ============================================================================
# TESTS
# ============================================================================

def test_valid_offer_selection():
    """Test selecting a valid persisted offer snapshot creates a SELECTED booking intent."""
    headers, user = _auth_user("user_sel_1@trusttrip.com")
    trip, snap_id, _ = _setup_trip_and_persisted_snapshot(user, price=8200.0, currency="INR")

    payload = {
        "trip_id": str(trip["id"]),
        "offer_id": str(snap_id),
        "passengers": _sample_passenger(),
    }
    res = client.post("/api/flights/booking/select", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()

    assert data["success"] is True
    assert UUID(data["booking_id"])
    assert data["trip_id"] == str(trip["id"])
    assert data["offer_id"] == str(snap_id)
    assert data["status"] == "SELECTED"
    assert data["price"] == 8200.0
    assert data["currency"] == "INR"
    assert data["human_approval_required"] is True
    assert data["approved"] is False


def test_unknown_offer_id():
    """Test selecting a non-existent offer_id returns 404."""
    headers, user = _auth_user("user_unknown_offer@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Goa Trip",
        origin="BOM",
        destination="GOI",
        metadata={"constraints": {"origin": "BOM", "destination": "GOI"}},
    )

    payload = {
        "trip_id": str(trip["id"]),
        "offer_id": str(uuid4()),  # Non-existent offer ID
        "passengers": _sample_passenger(),
    }
    res = client.post("/api/flights/booking/select", json=payload, headers=headers)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_offer_ownership_mismatch():
    """Test selecting an offer snapshot created by another tenant returns 403."""
    headers_a, user_a = _auth_user("user_owner_a@trusttrip.com")
    headers_b, user_b = _auth_user("user_owner_b@trusttrip.com")

    trip_a, snap_a, _ = _setup_trip_and_persisted_snapshot(user_a)

    trip_b = TripStore().create_trip(
        user_id=user_b["id"],
        title="User B Trip",
        origin="CCU",
        destination="DEL",
        metadata={"constraints": {"origin": "CCU", "destination": "DEL"}},
    )

    # User B attempts to select User A's offer snapshot
    payload = {
        "trip_id": str(trip_b["id"]),
        "offer_id": str(snap_a),
        "passengers": _sample_passenger(),
    }
    res = client.post("/api/flights/booking/select", json=payload, headers=headers_b)
    assert res.status_code == 403
    assert "access denied" in res.json()["detail"].lower()


def test_trip_mismatch():
    """Test selecting an offer generated for Trip 1 when specifying Trip 2 returns 400."""
    headers, user = _auth_user("user_trip_mismatch@trusttrip.com")
    trip_1, snap_1, _ = _setup_trip_and_persisted_snapshot(user)

    trip_2 = TripStore().create_trip(
        user_id=user["id"],
        title="Trip 2",
        origin="CCU",
        destination="DEL",
        metadata={"constraints": {"origin": "CCU", "destination": "DEL"}},
    )

    payload = {
        "trip_id": str(trip_2["id"]),
        "offer_id": str(snap_1),  # generated under trip_1
        "passengers": _sample_passenger(),
    }
    res = client.post("/api/flights/booking/select", json=payload, headers=headers)
    assert res.status_code == 400
    assert "mismatch" in res.json()["detail"].lower()


def test_stale_or_expired_offer_selection():
    """Test selecting an expired offer snapshot returns 410."""
    headers, user = _auth_user("user_stale_sel@trusttrip.com")
    trip, snap_id, _ = _setup_trip_and_persisted_snapshot(user, expires_in_minutes=-10)

    payload = {
        "trip_id": str(trip["id"]),
        "offer_id": str(snap_id),
        "passengers": _sample_passenger(),
    }
    res = client.post("/api/flights/booking/select", json=payload, headers=headers)
    assert res.status_code == 410
    assert "expired" in res.json()["detail"].lower()


def test_revalidation_offer_unavailable():
    """Test revalidation when provider reports offer unavailable returns 410."""
    headers, user = _auth_user("user_reval_unavail@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=6500.0)

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert sel_res.status_code == 201
    booking_id = sel_res.json()["booking_id"]

    # 2. Revalidate with provider reporting unavailable
    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 6500.0, "currency": "INR", "available": False}
    fake_svc = FlightService(provider=fake_prov)
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=fake_svc)

    res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert res.status_code == 410
    assert "no longer available" in res.json()["detail"].lower()


def test_revalidation_changed_price():
    """Test revalidation detecting a price change returns 409 and does not allow booking."""
    headers, user = _auth_user("user_reval_price@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=5000.0, currency="INR")

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert sel_res.status_code == 201
    booking_id = sel_res.json()["booking_id"]

    # 2. Provider returns price 5500.0 (changed from 5000.0)
    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 5500.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=fake_svc)

    res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["original_price"] == 5000.0
    assert detail["new_price"] == 5500.0


def test_revalidation_success_ready_for_booking():
    """Test successful revalidation transitions intent to READY_FOR_BOOKING."""
    headers, user = _auth_user("user_reval_ok@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert sel_res.status_code == 201
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    app.dependency_overrides[_booking_service] = lambda: FlightBookingService(flight_service=fake_svc)

    res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "READY_FOR_BOOKING"
    assert data["is_valid"] is True
    assert data["price_changed"] is False
    assert data["current_price"] == 7000.0


def test_approval_recorded_successfully():
    """Test human approval (HITL) is recorded on a READY_FOR_BOOKING intent."""
    headers, user = _auth_user("user_approval_rec@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 1. Revalidate to reach READY_FOR_BOOKING
    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_res.status_code == 200

    # 2. Record human approval
    appr_res = client.post(
        f"/api/flights/booking/{booking_id}/approve",
        json={"approved": True, "notes": "Approved by user after review"},
        headers=headers,
    )
    assert appr_res.status_code == 200
    data = appr_res.json()
    assert data["success"] is True
    assert data["approved"] is True
    assert data["status"] == "READY_FOR_BOOKING"
    assert "approved_at" in data


def test_booking_blocked_without_approval():
    """Test booking is blocked with 400 if human approval has not been given."""
    headers, user = _auth_user("user_block_approval@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Revalidate
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)

    # Confirm WITHOUT approval -> Must be blocked!
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 400
    detail = conf_res.json()["detail"]
    assert detail.get("approval_required") is True
    assert "human approval is mandatory" in detail.get("error", "").lower()
    # Ensure provider book method was NEVER called
    assert len(fake_prov.book_calls) == 0


def test_booking_blocked_without_final_confirmation():
    """Test booking is blocked if final_confirmation is False, even if approved."""
    headers, user = _auth_user("user_block_final_conf@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Call confirm with final_confirmation=False
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": False},
        headers=headers,
    )
    assert conf_res.status_code == 400
    detail = conf_res.json()["detail"]
    assert detail.get("requires_final_confirmation") is True
    assert len(fake_prov.book_calls) == 0


def test_immediate_pre_booking_revalidation_changed_price():
    """Test immediate pre-booking revalidation catches price increase right before book call."""
    headers, user = _auth_user("user_pre_reval_price@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 1. Revalidate & approve at 7000
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # 2. Fare changes right before confirm call
    fake_prov.offers[raw_prov_id]["price"] = 9200.0

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 409
    assert "fare has changed" in conf_res.json()["detail"]["error"].lower()
    # Verify provider book was NOT called
    assert len(fake_prov.book_calls) == 0


def test_immediate_pre_booking_revalidation_unavailable():
    """Test immediate pre-booking revalidation catches offer withdrawal right before book call."""
    headers, user = _auth_user("user_pre_reval_unavail@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Inventory is withdrawn right before confirm
    fake_prov.offers[raw_prov_id]["available"] = False

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 410
    assert "no longer available" in conf_res.json()["detail"].lower()
    assert len(fake_prov.book_calls) == 0


def test_successful_booking_path_mocked_provider():
    """Test complete successful vertical slice: Select -> Revalidate -> Approve -> Confirm."""
    headers, user = _auth_user("user_e2e_booking@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert sel_res.status_code == 201
    booking_id = sel_res.json()["booking_id"]
    assert sel_res.json()["status"] == "SELECTED"

    # 2. Revalidate
    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_res.status_code == 200
    assert rev_res.json()["status"] == "READY_FOR_BOOKING"

    # 3. Approve
    app_res = client.post(
        f"/api/flights/booking/{booking_id}/approve",
        json={"approved": True, "notes": "Approved by traveler"},
        headers=headers,
    )
    assert app_res.status_code == 200
    assert app_res.json()["approved"] is True

    # 4. Confirm
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 200
    data = conf_res.json()
    assert data["success"] is True
    assert data["booked"] is True
    assert data["status"] == "COMPLETED"
    assert data["booking_reference"] == "REF-TT-9988"
    assert data["order_id"] == "ord_duffel_test_456"

    # Verify provider book call was executed with internal provider ID
    assert len(fake_prov.book_calls) == 1
    assert fake_prov.book_calls[0]["offer_id"] == raw_prov_id


def test_provider_booking_failure():
    """Test upstream provider failure during booking call returns 502 and marks status FAILED."""
    headers, user = _auth_user("user_provider_fail@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_prov.fail_booking = True  # Trigger provider failure on book()
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Select, Revalidate, Approve
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Confirm -> fails
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 502
    assert "provider booking failed" in conf_res.json()["detail"].lower()


def test_duplicate_confirmation_protection():
    """Test confirming an already COMPLETED booking is rejected with 409."""
    headers, user = _auth_user("user_dup_conf@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7000.0, currency="INR")

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Select, Revalidate, Approve, Confirm
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    first_conf = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert first_conf.status_code == 200
    assert len(fake_prov.book_calls) == 1

    # Second confirmation attempt -> must be blocked
    second_conf = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert second_conf.status_code == 409
    detail = second_conf.json()["detail"]
    assert detail.get("duplicate_attempt") is True
    assert "duplicate confirmation blocked" in detail.get("error", "").lower()
    # Verify provider book() was NOT called a second time
    assert len(fake_prov.book_calls) == 1


def test_raw_provider_id_shielding():
    """Test that the raw provider offer ID is NEVER exposed across any booking endpoint response."""
    headers, user = _auth_user("user_shielding@trusttrip.com")
    secret_prov_id = "super_secret_duffel_raw_id_xyz999"
    trip, snap_id, _ = _setup_trip_and_persisted_snapshot(
        user, price=7000.0, currency="INR", provider_offer_id=secret_prov_id
    )

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[secret_prov_id] = {"id": secret_prov_id, "price": 7000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert secret_prov_id not in sel_res.text
    booking_id = sel_res.json()["booking_id"]

    # 2. Revalidate
    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert secret_prov_id not in rev_res.text

    # 3. Approve
    app_res = client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    assert secret_prov_id not in app_res.text

    # 4. Confirm
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert secret_prov_id not in conf_res.text

    # 5. GET details
    get_res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert secret_prov_id not in get_res.text



def test_get_booking_details():
    """Test querying booking details returns current state without leaking secrets."""
    headers, user = _auth_user("user_get_booking@trusttrip.com")
    trip, snap_id, _ = _setup_trip_and_persisted_snapshot(user, price=4500.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["booking_id"] == booking_id
    assert data["trip_id"] == str(trip["id"])
    assert data["status"] == "SELECTED"
    assert data["expected_amount"] == 4500.0
    assert data["currency"] == "INR"
    assert data["approved"] is False


def test_price_change_clears_approval():
    """Test that a price change immediately clears existing approval and approved_at."""
    headers, user = _auth_user("user_price_clear_app@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=5000.0, currency="INR")

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    # 2. Revalidate at 5000.0
    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 5000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_res.status_code == 200

    # 3. Approve
    app_res = client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    assert app_res.status_code == 200
    assert app_res.json()["approved"] is True

    # 4. Provider price changes to 5600.0
    fake_prov.offers[raw_prov_id]["price"] = 5600.0

    # 5. Revalidate detects price change -> 409
    rev2_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev2_res.status_code == 409

    # 6. Verify booking metadata has approval cleared
    get_res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["status"] == "PRICE_CHANGED"
    assert data["approved"] is False


def test_price_change_requires_fresh_approval():
    """Test that after a price change, confirmation is blocked until revalidated and fresh approval is recorded."""
    headers, user = _auth_user("user_fresh_app_req@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=5000.0, currency="INR")

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    # 2. Revalidate & approve
    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 5000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # 3. Provider price changes to 5800.0
    fake_prov.offers[raw_prov_id]["price"] = 5800.0

    # Revalidation triggers PRICE_CHANGED
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)

    # 4. Confirmation must be blocked
    conf_blocked = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_blocked.status_code in {400, 409}

    # 5. Revalidate accepting new price -> transitions PRICE_CHANGED -> SELECTED -> REVALIDATING -> READY_FOR_BOOKING
    rev_ok = client.post(
        f"/api/flights/booking/{booking_id}/revalidate",
        json={"accepted_price": 5800.0},
        headers=headers,
    )
    assert rev_ok.status_code == 200
    assert rev_ok.json()["status"] == "READY_FOR_BOOKING"
    assert rev_ok.json()["approved"] is False  # Must still be False

    # 6. Confirmation STILL blocked before fresh approval
    conf_still_blocked = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_still_blocked.status_code == 400
    assert conf_still_blocked.json()["detail"]["approval_required"] is True

    # 7. Record fresh approval
    app_fresh = client.post(
        f"/api/flights/booking/{booking_id}/approve",
        json={"approved": True, "notes": "Approved updated fare 5800 INR"},
        headers=headers,
    )
    assert app_fresh.status_code == 200
    assert app_fresh.json()["approved"] is True

    # 8. Confirmation now succeeds
    conf_success = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_success.status_code == 200
    assert conf_success.json()["status"] == "COMPLETED"
    assert conf_success.json()["price"] == 5800.0


def test_unavailable_offer_clears_approval():
    """Test that when an offer becomes unavailable, approval metadata is cleared."""
    headers, user = _auth_user("user_unavail_app@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=6000.0, currency="INR")

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    # 2. Revalidate & approve
    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 6000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # 3. Provider marks offer unavailable
    fake_prov.offers[raw_prov_id]["available"] = False

    # 4. Revalidate -> 410
    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_res.status_code == 410

    # 5. Booking details show UNAVAILABLE and approval cleared
    get_res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["status"] == "UNAVAILABLE"
    assert data["approved"] is False


def test_confirmation_from_handed_off_is_rejected():
    """Test that an intent currently in HANDED_OFF rejects confirmation as duplicate/in-progress."""
    headers, user = _auth_user("user_handed_off_rej@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=6500.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 6500.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Manually transition intent to HANDED_OFF
    intent = booking_svc._get_intent(booking_id, user["id"])
    intent["status"] = "HANDED_OFF"
    intent["metadata"]["approved"] = True
    booking_svc._save_intent(intent)

    # Attempt to confirm from HANDED_OFF
    res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["duplicate_attempt"] is True
    assert "HANDED_OFF" in res.json()["detail"]["error"]


def test_confirmation_from_handed_off_does_not_call_provider_book():
    """Test that confirmation attempt from HANDED_OFF does not call provider.book()."""
    headers, user = _auth_user("user_handed_off_nobook@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=6500.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 6500.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Set status to HANDED_OFF
    intent = booking_svc._get_intent(booking_id, user["id"])
    intent["status"] = "HANDED_OFF"
    intent["metadata"]["approved"] = True
    booking_svc._save_intent(intent)

    book_call_count_before = len(fake_prov.book_calls)

    res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert res.status_code == 409
    assert len(fake_prov.book_calls) == book_call_count_before


def test_provider_booking_succeeds_but_completed_persistence_fails():
    """Test that if provider booking succeeds but local COMPLETED persistence fails, status remains HANDED_OFF and 500 is returned."""
    headers, user = _auth_user("user_persist_fail@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7200.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7200.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)

    # Revalidate & approve
    app.dependency_overrides[_booking_service] = lambda: booking_svc
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Hook _save_intent to fail when status is COMPLETED
    orig_save = booking_svc._save_intent

    def failing_save(intent_data):
        if intent_data.get("status") == "COMPLETED":
            raise RuntimeError("Database connection lost during COMPLETED transition!")
        return orig_save(intent_data)

    booking_svc._save_intent = failing_save

    # Confirm
    res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert res.status_code == 500
    # Must NOT claim COMPLETED
    assert "COMPLETED" not in res.text

    # Provider book WAS called
    assert len(fake_prov.book_calls) == 1

    # Stored intent remains in HANDED_OFF
    stored_intent = booking_svc._get_intent(booking_id, user["id"])
    assert stored_intent["status"] == "HANDED_OFF"


def test_retry_after_persistence_failure_does_not_call_provider_again():
    """Test that retrying confirmation after completion persistence failure is blocked and does NOT call provider.book() again."""
    headers, user = _auth_user("user_retry_nobook@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7200.0, currency="INR")

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    fake_prov = FakeBookingFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7200.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)

    app.dependency_overrides[_booking_service] = lambda: booking_svc
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Fail save on COMPLETED
    orig_save = booking_svc._save_intent

    def failing_save(intent_data):
        if intent_data.get("status") == "COMPLETED":
            raise RuntimeError("Database error on COMPLETED")
        return orig_save(intent_data)

    booking_svc._save_intent = failing_save

    # First attempt: provider succeeds, persistence fails, returns 500
    res1 = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert res1.status_code == 500
    assert len(fake_prov.book_calls) == 1

    # Restore normal save
    booking_svc._save_intent = orig_save

    # Retry confirmation attempt
    res2 = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    # Must be rejected as duplicate / in progress (409)
    assert res2.status_code == 409
    assert res2.json()["detail"]["duplicate_attempt"] is True

    # Critical check: provider.book() was NOT called a second time!
    assert len(fake_prov.book_calls) == 1
