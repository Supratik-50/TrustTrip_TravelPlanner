"""E2E Integration Tests for Fast-Track Frontend & Backend Sections 5A/5B/5C.

Verifies the 2 required paths:
1. One practical E2E happy path:
   Trip creation -> Flight search -> Display offers -> Select offer -> Revalidate
   -> Human approval -> Final confirmation -> Booking result.
2. One price-change / HITL-blocked path:
   Price change detected during revalidation -> approval invalidated
   -> Confirmation blocked without human approval -> Price accepted
   -> Human approval recorded -> Final confirmation succeeds.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
import os
import secrets
from typing import Any
from unittest.mock import MagicMock
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
from flight_api import _booking_service, _service
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.flight_booking_service import FlightBookingService
from services.flight_provider import FlightProvider, FlightProviderError
from services.flight_service import FlightService
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore

client = TestClient(app)


class FakeDuffelProvider(FlightProvider):
    name = "duffel"

    def __init__(self):
        self.offers: dict[str, dict[str, Any]] = {}
        self.current_price = 8500.0

    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self.offers.values())

    def get_offer(self, offer_id: str) -> dict[str, Any]:
        if offer_id not in self.offers:
            raise FlightProviderError(f"Offer '{offer_id}' not found.")
        off = dict(self.offers[offer_id])
        off["price"] = self.current_price
        off["total_amount"] = str(self.current_price)
        return off

    def book(
        self,
        offer_id: str,
        passengers: list[dict[str, Any]],
        payment: dict[str, Any] | None = None,
        payment_mode: str = "auto",
    ) -> dict[str, Any]:
        return {
            "booking_reference": "TT-E2E-7788",
            "order_id": "ord_duffel_e2e_123",
            "pnr": "PNR-AIR-E2E",
            "e_ticket": "098-9988776655",
            "status": "confirmed",
        }


@pytest.fixture(autouse=True)
def clean_test_environment(monkeypatch):
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


def _create_authenticated_traveler(email: str = "traveler.e2e@trusttrip.com") -> tuple[dict[str, str], dict[str, Any]]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="strong_pw_hash")
    token = create_access_token(user["id"], user["email"])
    return {"Authorization": f"Bearer {token}"}, user


def test_e2e_flight_booking_happy_path():
    """Practical E2E Happy Path.

    Trip Creation -> Search -> Display -> Select -> Revalidate -> Human Approval
    -> Final Confirmation -> Authoritative Status Query.
    """
    headers, user = _create_authenticated_traveler()

    # 1. Trip Creation
    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Kyoto Spring Voyage",
        origin="DEL",
        destination="KIX",
        metadata={
            "constraints": {
                "origin": "DEL",
                "origin_airport": "DEL",
                "destination": "KIX",
                "destination_airport": "KIX",
                "departure_date": "2026-11-15",
                "return_date": "2026-11-25",
                "travelers_count": 1,
                "currency": "INR",
            }
        },
    )
    trip_id = str(trip["id"])

    # Setup Provider
    provider = FakeDuffelProvider()
    raw_provider_id = "off_duffel_raw_secret_999"
    provider.offers[raw_provider_id] = {
        "id": raw_provider_id,
        "price": 8500.0,
        "currency": "INR",
        "total_amount": "8500.00",
        "total_currency": "INR",
        "owner": "Japan Airlines",
        "owner_iata": "JL",
        "stops": 0,
        "segments": [
            {
                "origin": "DEL",
                "origin_name": "Delhi Airport",
                "destination": "KIX",
                "destination_name": "Kansai Airport",
                "departure": "2026-11-15T10:00:00",
                "arrival": "2026-11-15T18:00:00",
                "duration": "PT8H",
                "marketing_carrier": "Japan Airlines",
                "marketing_carrier_iata": "JL",
                "flight_number": "JL 740",
            }
        ],
    }

    mock_flight_svc = FlightService(provider=provider)
    app.dependency_overrides[_service] = lambda: mock_flight_svc

    booking_svc = FlightBookingService(flight_service=mock_flight_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # 2. Flight Search (POST /api/flights/search)
    search_resp = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": trip_id, "cabin_class": "economy"},
    )
    assert search_resp.status_code == 200, search_resp.text
    search_data = search_resp.json()
    assert search_data["success"] is True
    assert search_data["availability_state"] == "available"
    assert len(search_data["offers"]) > 0

    chosen_offer = search_data["offers"][0]
    offer_id = chosen_offer["offer_id"]
    # Verify raw provider ID is NOT exposed
    assert "off_duffel" not in offer_id
    assert raw_provider_id not in str(chosen_offer)

    # 3. Select Offer (POST /api/flights/booking/select)
    passenger_payload = [
        {
            "given_name": "Ananya",
            "family_name": "Roy",
            "born_on": "1992-04-12",
            "title": "ms",
            "gender": "f",
            "email": "ananya@example.com",
            "phone_number": "+919876543210",
        }
    ]
    select_resp = client.post(
        "/api/flights/booking/select",
        headers=headers,
        json={
            "trip_id": trip_id,
            "offer_id": offer_id,
            "passengers": passenger_payload,
        },
    )
    assert select_resp.status_code == 201, select_resp.text
    sel_data = select_resp.json()
    assert sel_data["status"] == "SELECTED"
    booking_id = sel_data["booking_id"]
    assert sel_data["human_approval_required"] is True
    assert sel_data["approved"] is False

    # 4. Revalidate Offer (POST /api/flights/booking/{id}/revalidate)
    reval_resp = client.post(
        f"/api/flights/booking/{booking_id}/revalidate",
        headers=headers,
        json={"booking_id": booking_id},
    )
    assert reval_resp.status_code == 200, reval_resp.text
    reval_data = reval_resp.json()
    assert reval_data["status"] == "READY_FOR_BOOKING"
    assert reval_data["is_valid"] is True
    assert reval_data["price_changed"] is False
    assert reval_data["approved"] is False

    # 5. Confirm without Approval must be rejected (Safety check)
    premature_confirm = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": True},
    )
    assert premature_confirm.status_code == 400
    assert premature_confirm.json().get("detail", {}).get("approval_required") is True

    # 6. Explicit Human Approval (POST /api/flights/booking/{id}/approve)
    approve_resp = client.post(
        f"/api/flights/booking/{booking_id}/approve",
        headers=headers,
        json={"booking_id": booking_id, "approved": True, "notes": "Approved by traveler in workspace"},
    )
    assert approve_resp.status_code == 200, approve_resp.text
    app_data = approve_resp.json()
    assert app_data["approved"] is True
    assert app_data["status"] == "READY_FOR_BOOKING"

    # 7. Final Confirmation (POST /api/flights/booking/{id}/confirm)
    confirm_resp = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": True},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    conf_data = confirm_resp.json()
    assert conf_data["status"] == "COMPLETED"
    assert conf_data["booked"] is True
    assert conf_data["booking_reference"] == "TT-E2E-7788"
    assert conf_data["order_id"] == "ord_duffel_e2e_123"
    assert conf_data["pnr"] == "PNR-AIR-E2E"
    assert conf_data["e_ticket"] == "098-9988776655"

    # 8. Query Authoritative Booking Status (GET /api/flights/booking/{id})
    get_resp = client.get(
        f"/api/flights/booking/{booking_id}",
        headers=headers,
    )
    assert get_resp.status_code == 200, get_resp.text
    get_data = get_resp.json()
    assert get_data["booking_id"] == booking_id
    assert get_data["status"] == "COMPLETED"
    assert get_data["booking_reference"] == "TT-E2E-7788"
    assert get_data["order_id"] == "ord_duffel_e2e_123"
    assert get_data["pnr"] == "PNR-AIR-E2E"
    assert get_data["e_ticket"] == "098-9988776655"
    assert raw_provider_id not in str(get_data)


def test_e2e_price_change_and_hitl_blocked_path():
    """Price-Change & HITL-Blocked Path.

    Revalidation detects price change (409)
    -> Old approval cleared / Confirmation blocked
    -> User accepts new price
    -> User provides fresh human approval
    -> Final confirmation succeeds.
    """
    headers, user = _create_authenticated_traveler("price.change.e2e@trusttrip.com")

    # Trip Creation
    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Paris Vacation",
        origin="DEL",
        destination="CDG",
        metadata={
            "constraints": {
                "origin": "DEL",
                "destination": "CDG",
                "departure_date": "2026-12-01",
                "return_date": "2026-12-10",
                "travelers_count": 1,
                "currency": "EUR",
            }
        },
    )
    trip_id = str(trip["id"])

    # Setup Provider with initial price 600.0
    provider = FakeDuffelProvider()
    provider.current_price = 600.0
    raw_provider_id = "off_duffel_paris_409"
    provider.offers[raw_provider_id] = {
        "id": raw_provider_id,
        "price": 600.0,
        "currency": "EUR",
        "total_amount": "600.00",
        "total_currency": "EUR",
        "owner": "Air France",
        "owner_iata": "AF",
        "stops": 0,
        "segments": [
            {
                "origin": "DEL",
                "origin_name": "Delhi Airport",
                "destination": "CDG",
                "destination_name": "Charles de Gaulle Airport",
                "departure": "2026-12-01T08:00:00",
                "arrival": "2026-12-01T15:00:00",
                "duration": "PT9H",
                "marketing_carrier": "Air France",
                "marketing_carrier_iata": "AF",
                "flight_number": "AF 225",
            }
        ],
    }

    mock_flight_svc = FlightService(provider=provider)
    app.dependency_overrides[_service] = lambda: mock_flight_svc

    booking_svc = FlightBookingService(flight_service=mock_flight_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    # Search & Select
    search_resp = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": trip_id, "cabin_class": "economy"},
    )
    assert search_resp.status_code == 200
    offer_id = search_resp.json()["offers"][0]["offer_id"]

    select_resp = client.post(
        "/api/flights/booking/select",
        headers=headers,
        json={
            "trip_id": trip_id,
            "offer_id": offer_id,
            "passengers": [
                {
                    "given_name": "Dev",
                    "family_name": "Sharma",
                    "born_on": "1988-08-20",
                    "title": "mr",
                    "gender": "m",
                }
            ],
        },
    )
    assert select_resp.status_code == 201
    booking_id = select_resp.json()["booking_id"]

    # Now the airline updates fare bucket: 600.0 -> 660.0
    provider.current_price = 660.0

    # 1. Revalidation detects price change -> 409
    reval_resp = client.post(
        f"/api/flights/booking/{booking_id}/revalidate",
        headers=headers,
        json={"booking_id": booking_id},
    )
    assert reval_resp.status_code == 409
    error_detail = reval_resp.json()["detail"]
    assert error_detail["error"] == "Fare has changed"
    assert float(error_detail["new_price"]) == 660.0
    assert float(error_detail["original_price"]) == 600.0

    # 2. Confirmation attempt must be BLOCKED
    blocked_confirm = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": True},
    )
    assert blocked_confirm.status_code in {400, 409}

    # 3. User accepts changed price via revalidate with accepted_price
    accept_resp = client.post(
        f"/api/flights/booking/{booking_id}/revalidate",
        headers=headers,
        json={"booking_id": booking_id, "accepted_price": 660.0},
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "READY_FOR_BOOKING"
    assert accept_resp.json()["approved"] is False  # Must still require fresh approval!

    # 4. User provides explicit Human Approval
    approve_resp = client.post(
        f"/api/flights/booking/{booking_id}/approve",
        headers=headers,
        json={"booking_id": booking_id, "approved": True, "notes": "Price increase of 60 EUR accepted"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["approved"] is True

    # 5. User tries to confirm without checking final confirmation flag -> BLOCKED
    blocked_checkbox = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": False},
    )
    assert blocked_checkbox.status_code == 400
    assert blocked_checkbox.json()["detail"]["requires_final_confirmation"] is True

    # 6. User explicitly checks final confirmation -> Confirmation succeeds
    final_confirm = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": True},
    )
    assert final_confirm.status_code == 200
    assert final_confirm.json()["status"] == "COMPLETED"
    assert final_confirm.json()["booked"] is True


def test_booking_result_truthful_missing_identifiers_not_fabricated():
    """Verify Issue 1: Missing PNR/E-Ticket does NOT generate fabricated values.

    When upstream provider returns no PNR or e-ticket, backend returns None,
    and no fake or fabricated values are returned or saved.
    """
    headers, user = _create_authenticated_traveler("truthful.pnr@trusttrip.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="London Trip",
        origin="BOM",
        destination="LHR",
        metadata={
            "constraints": {
                "origin": "BOM",
                "destination": "LHR",
                "departure_date": "2026-11-15",
                "return_date": "2026-11-25",
                "travelers_count": 1,
                "currency": "GBP",
            }
        },
    )
    trip_id = str(trip["id"])

    # Provider returns booking without pnr and without e_ticket
    class NoPnrProvider(FlightProvider):
        name = "duffel"

        def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "id": "off_no_pnr_123",
                    "price": 450.0,
                    "currency": "GBP",
                    "owner": "British Airways",
                    "owner_iata": "BA",
                    "stops": 0,
                    "segments": [
                        {
                            "origin": "BOM",
                            "destination": "LHR",
                            "departure": "2026-11-15T02:00:00",
                            "arrival": "2026-11-15T07:00:00",
                            "duration": "PT9H30M",
                            "marketing_carrier": "British Airways",
                            "marketing_carrier_iata": "BA",
                            "flight_number": "BA 138",
                        }
                    ],
                }
            ]

        def get_offer(self, offer_id: str) -> dict[str, Any]:
            return {
                "id": offer_id,
                "price": 450.0,
                "currency": "GBP",
                "available": True,
            }

        def book(self, offer_id: str, passengers: list[dict[str, Any]], payment=None, payment_mode="auto"):
            # Provider does NOT supply PNR or e-ticket
            return {
                "booking_reference": "REF-BA-TRUTHFUL",
                "order_id": "ord_ba_456",
                "status": "confirmed",
            }

    prov = NoPnrProvider()
    mock_svc = FlightService(provider=prov)
    app.dependency_overrides[_service] = lambda: mock_svc
    booking_svc = FlightBookingService(flight_service=mock_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    search_resp = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": trip_id, "cabin_class": "economy"},
    )
    assert search_resp.status_code == 200
    offer_id = search_resp.json()["offers"][0]["offer_id"]

    select_resp = client.post(
        "/api/flights/booking/select",
        headers=headers,
        json={
            "trip_id": trip_id,
            "offer_id": offer_id,
            "passengers": [
                {
                    "given_name": "Sarah",
                    "family_name": "Connor",
                    "born_on": "1985-05-15",
                    "title": "ms",
                    "gender": "f",
                }
            ],
        },
    )
    booking_id = select_resp.json()["booking_id"]

    client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    client.post(f"/api/flights/booking/{booking_id}/approve", headers=headers, json={"approved": True})

    conf_resp = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": True},
    )
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()
    assert conf_data["status"] == "COMPLETED"
    assert conf_data["booking_reference"] == "REF-BA-TRUTHFUL"
    # MUST NOT be fabricated from booking reference or random numbers
    assert conf_data["pnr"] is None
    assert conf_data["e_ticket"] is None

    # Check GET authoritative booking status
    get_resp = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["pnr"] is None
    assert get_data["e_ticket"] is None


def test_stale_and_unavailable_states_reset_confirmation_and_block_booking():
    """Verify Issue 2 & Safety: Unavailable or stale state resets confirmation.

    When an offer is withdrawn by provider, revalidation marks it UNAVAILABLE,
    clears approval, and blocks confirmation.
    """
    headers, user = _create_authenticated_traveler("unavail.reset@trusttrip.com")

    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Tokyo Holiday",
        origin="DEL",
        destination="HND",
        metadata={
            "constraints": {
                "origin": "DEL",
                "destination": "HND",
                "departure_date": "2026-11-01",
                "return_date": "2026-11-10",
                "travelers_count": 1,
                "currency": "INR",
            }
        },
    )
    trip_id = str(trip["id"])

    fake_prov = FakeDuffelProvider()
    fake_prov.current_price = 55000.0
    raw_id = "off_del_hnd_unavail"
    fake_prov.offers[raw_id] = {
        "id": raw_id,
        "price": 55000.0,
        "currency": "INR",
        "total_amount": "55000.00",
        "total_currency": "INR",
        "owner": "Japan Airlines",
        "owner_iata": "JL",
        "stops": 0,
        "segments": [
            {
                "origin": "DEL",
                "destination": "HND",
                "departure": "2026-11-01T20:00:00",
                "arrival": "2026-11-02T06:30:00",
                "duration": "PT7H",
                "marketing_carrier": "Japan Airlines",
                "marketing_carrier_iata": "JL",
                "flight_number": "JL 750",
            }
        ],
    }

    mock_svc = FlightService(provider=fake_prov)
    app.dependency_overrides[_service] = lambda: mock_svc
    booking_svc = FlightBookingService(flight_service=mock_svc)
    app.dependency_overrides[_booking_service] = lambda: booking_svc

    search_resp = client.post("/api/flights/search", headers=headers, json={"trip_id": trip_id, "cabin_class": "economy"})
    offer_id = search_resp.json()["offers"][0]["offer_id"]

    select_resp = client.post(
        "/api/flights/booking/select",
        headers=headers,
        json={
            "trip_id": trip_id,
            "offer_id": offer_id,
            "passengers": [
                {
                    "given_name": "Kenji",
                    "family_name": "Sato",
                    "born_on": "1992-04-10",
                    "title": "mr",
                    "gender": "m",
                }
            ],
        },
    )
    booking_id = select_resp.json()["booking_id"]

    # Revalidate & approve initially
    rev_ok = client.post(f"/api/flights/booking/{booking_id}/revalidate", headers=headers)
    assert rev_ok.status_code == 200
    app_ok = client.post(f"/api/flights/booking/{booking_id}/approve", headers=headers, json={"approved": True})
    assert app_ok.status_code == 200

    # Provider withdraws the offer right before booking
    fake_prov.offers[raw_id]["available"] = False

    # Immediate pre-booking revalidation detects offer withdrawal
    conf_blocked = client.post(
        f"/api/flights/booking/{booking_id}/confirm",
        headers=headers,
        json={"booking_id": booking_id, "final_confirmation": True},
    )
    assert conf_blocked.status_code == 410

    # Verify booking status transitioned to UNAVAILABLE and approval was cleared
    get_resp = client.get(f"/api/flights/booking/{booking_id}", headers=headers)
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["status"] == "UNAVAILABLE"
    assert get_data["approved"] is False
