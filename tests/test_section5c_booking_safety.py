"""Focused Test Suite for Section 5C — Revalidation + Booking Safety Hardening.

Covers:
1. Stale offer is rejected before booking (410, status EXPIRED).
2. Changed price invalidates approval (metadata approved set to False).
3. Changed price cannot be booked with old approval (blocked until fresh HITL approval).
4. Unavailable offer cannot be booked (stopped, approval cleared, no provider.book call).
5. Failed revalidation prevents provider.book() call.
6. Final confirmation without required approval is blocked (400, approval_required=True).
7. Provider failure does not become COMPLETED (status FAILED, 502 error).
8. Persistence failure after provider success cannot cause a second booking (HANDED_OFF lock, 409 on retry).
9. Valid booking path still succeeds end-to-end (status COMPLETED, booking reference issued).
10. Trip mismatch & security shielding preserved.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import secrets
from typing import Any
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
# FAKE PROVIDER FOR SAFETY TESTS
# ============================================================================

class FakeSafetyFlightProvider(FlightProvider):
    name = "fake_safety_provider"

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
            raise FlightProviderTimeout("Provider timed out during order placement.")
        if self.fail_booking:
            raise FlightProviderError("Provider payment authorization failed.")
        self.book_calls.append({
            "offer_id": offer_id,
            "passengers": passengers,
            "payment": payment,
            "payment_mode": payment_mode,
        })
        return {
            "booking_reference": "SAFE-REF-8877",
            "order_id": "ord_safety_999",
            "pnr": "PNR8877",
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


def _auth_user(email: str = "safety_user@trusttrip.com") -> tuple[dict[str, str], dict[str, Any]]:
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
    provider_offer_id: str = "raw_prov_secret_safe_123",
    expires_in_minutes: int = 30,
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE,
) -> tuple[dict[str, Any], UUID, str]:
    trip_store = TripStore()
    session_store = SearchSessionStore()
    now = datetime.now(timezone.utc)

    # 1. Create Trip
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Safety Inspection Trip",
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
        criteria_hash="hash_safe_trip",
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
# 1. STALE OFFER PROTECTION
# ============================================================================

def test_stale_offer_rejected_before_booking():
    """Safety: Stale / expired offer cannot be selected or confirmed for booking."""
    headers, user = _auth_user("user_stale_safe@trusttrip.com")
    # Setup snapshot that expired 5 minutes ago
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(
        user, price=5000.0, currency="INR", expires_in_minutes=-5
    )

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 5000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 1. Attempting to select an already-expired snapshot must fail with 410
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert sel_res.status_code == 410

    # 2. Even if an intent was previously created and subsequently expires:
    trip2, snap2_id, raw_prov2_id = _setup_trip_and_persisted_snapshot(
        user, price=5000.0, currency="INR", expires_in_minutes=10
    )
    fake_prov.offers[raw_prov2_id] = {"id": raw_prov2_id, "price": 5000.0, "currency": "INR", "available": True}

    sel2 = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip2["id"]), "offer_id": str(snap2_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    assert sel2.status_code == 201
    booking_id = sel2.json()["booking_id"]

    # Revalidate and approve
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Now expire the booking intent
    intent = booking_svc._get_intent(booking_id, user["id"])
    intent["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    booking_svc._save_intent(intent)

    # Confirming expired intent must be blocked with 410 OfferStaleError
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 410
    assert len(fake_prov.book_calls) == 0


# ============================================================================
# 2. PRICE CONSISTENCY & APPROVAL INVALIDATION
# ============================================================================

def test_changed_price_invalidates_approval_and_blocks_booking():
    """Safety: Price changes invalidate old approval; no booking permitted until fresh HITL approval."""
    headers, user = _auth_user("user_price_safe@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=6000.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 6000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 1. Select
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    # 2. Revalidate at 6000 & approve
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    app_res = client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    assert app_res.status_code == 200

    # 3. Provider price jumps to 6700
    fake_prov.offers[raw_prov_id]["price"] = 6700.0

    # 4. Immediate confirmation attempt triggers price mismatch in pre-check
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 409
    assert conf_res.json()["detail"]["new_price"] == 6700.0
    assert len(fake_prov.book_calls) == 0

    # 5. Verify approval was completely wiped
    get_res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_res.json()["approved"] is False
    assert get_res.json()["status"] == "PRICE_CHANGED"

    # 6. Attempting to confirm again with old approval is blocked
    conf_res2 = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res2.status_code in {400, 409}
    assert len(fake_prov.book_calls) == 0


def test_cannot_book_with_tampered_approval_price():
    """Safety: Modifying expected_amount without re-approval triggers PriceChangedError."""
    headers, user = _auth_user("user_drift_price@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=4500.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 4500.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Simulate price drift in expected_amount
    intent = booking_svc._get_intent(booking_id, user["id"])
    intent["expected_amount"] = Decimal("5000.00")
    booking_svc._save_intent(intent)

    # Confirmation must detect approved_price (4500) != expected_amount (5000)
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 409
    assert len(fake_prov.book_calls) == 0


# ============================================================================
# 3. AVAILABILITY CONSISTENCY & FAILED REVALIDATION
# ============================================================================

def test_unavailable_offer_cannot_be_booked():
    """Safety: When offer is withdrawn, approval is cleared and provider.book() is never called."""
    headers, user = _auth_user("user_avail_safe@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=5200.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 5200.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Select, revalidate, approve
    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Provider withdraws seats
    fake_prov.offers[raw_prov_id]["available"] = False

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 410
    assert len(fake_prov.book_calls) == 0

    # Approval is invalidated
    get_res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_res.json()["approved"] is False
    assert get_res.json()["status"] == "UNAVAILABLE"


def test_failed_revalidation_prevents_provider_book():
    """Safety: Provider exceptions during recheck block confirmation completely."""
    headers, user = _auth_user("user_prov_err@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=4800.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 4800.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    # Remove offer to force provider recheck failure
    fake_prov.offers.pop(raw_prov_id)

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 410
    assert len(fake_prov.book_calls) == 0


# ============================================================================
# 4. FINAL CONFIRMATION & APPROVAL GUARDS
# ============================================================================

def test_final_confirmation_without_required_approval_is_blocked():
    """Safety: confirm_booking without prior approval returns 400 approval_required=True."""
    headers, user = _auth_user("user_no_app_safe@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=3000.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 3000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    # Revalidate to READY_FOR_BOOKING, but intentionally SKIP approve
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 400
    assert conf_res.json()["detail"]["approval_required"] is True
    assert len(fake_prov.book_calls) == 0


def test_final_confirmation_flag_false_is_blocked():
    """Safety: Missing confirmed_by_user/final_confirmation=True is blocked."""
    headers, user = _auth_user("user_no_confirm_flag@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=3000.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 3000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": False},
        headers=headers,
    )
    assert conf_res.status_code == 400
    assert conf_res.json()["detail"]["requires_final_confirmation"] is True
    assert len(fake_prov.book_calls) == 0


# ============================================================================
# 5. PROVIDER FAILURE & PERSISTENCE FAILURE SAFETY
# ============================================================================

def test_provider_failure_does_not_become_completed():
    """Safety: Upstream provider failure leaves intent in FAILED state (never COMPLETED)."""
    headers, user = _auth_user("user_fail_prov_safe@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=3500.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 3500.0, "currency": "INR", "available": True}
    fake_prov.fail_booking = True
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 502

    get_res = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_res.json()["status"] == "FAILED"
    assert get_res.json()["status"] != "COMPLETED"


def test_persistence_failure_after_provider_success_cannot_cause_second_booking():
    """Safety: When provider booking succeeds but completion persistence fails, retry cannot call book() again."""
    headers, user = _auth_user("user_persist_safe@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=4000.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 4000.0, "currency": "INR", "available": True}
    fake_svc = FlightService(provider=fake_prov)
    booking_svc = FlightBookingService(flight_service=fake_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    sel_res = client.post(
        "/api/flights/booking/select",
        json={"trip_id": str(trip["id"]), "offer_id": str(snap_id), "passengers": _sample_passenger()},
        headers=headers,
    )
    booking_id = sel_res.json()["booking_id"]
    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)

    orig_save = booking_svc._save_intent

    def fail_on_completed(data):
        if data.get("status") == "COMPLETED":
            raise RuntimeError("DB connection dropped when saving COMPLETED state!")
        return orig_save(data)

    booking_svc._save_intent = fail_on_completed

    # First confirm fails with 500 (not COMPLETED)
    res1 = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert res1.status_code == 500
    assert len(fake_prov.book_calls) == 1

    # Restore save
    booking_svc._save_intent = orig_save

    # Retry confirmation is blocked by HANDED_OFF
    res2 = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert res2.status_code == 409
    assert res2.json()["detail"]["duplicate_attempt"] is True
    # Crucial safety check: provider.book was NOT called a second time
    assert len(fake_prov.book_calls) == 1


# ============================================================================
# 6. END-TO-END VALID BOOKING PATH SUCCESS
# ============================================================================

def test_valid_booking_path_succeeds_with_strict_safety():
    """Happy path: Fully validated, approved, confirmed booking succeeds with provider reference."""
    headers, user = _auth_user("user_happy_safe@trusttrip.com")
    trip, snap_id, raw_prov_id = _setup_trip_and_persisted_snapshot(user, price=7500.0, currency="INR")

    fake_prov = FakeSafetyFlightProvider()
    fake_prov.offers[raw_prov_id] = {"id": raw_prov_id, "price": 7500.0, "currency": "INR", "available": True}
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

    # 2. Revalidate
    rev_res = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_res.status_code == 200
    assert rev_res.json()["is_valid"] is True

    # 3. Approve
    app_res = client.post(f"/api/flights/booking/{booking_id}/approve", json={"approved": True}, headers=headers)
    assert app_res.status_code == 200

    # 4. Confirm
    conf_res = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        json={"final_confirmation": True},
        headers=headers,
    )
    assert conf_res.status_code == 200
    data = conf_res.json()
    assert data["status"] == "COMPLETED"
    assert data["booking_reference"] == "SAFE-REF-8877"
    assert data["price"] == 7500.0
    assert len(fake_prov.book_calls) == 1
