"""Focused Test Suite for Section 5A — Flight Search Backend Contract.

Verifies:
1. Successful normalized flight search using canonical Trip as single source of truth.
2. Unauthorized trip (tenant isolation) rejected with 403.
3. Missing trip rejected with 404.
4. Missing / unresolvable canonical airport rejected with 400.
5. Invalid dates (missing, past date, return before departure) rejected with 400.
6. Invalid traveler count (0 travelers, >9 travelers) rejected with 400.
7. Provider unavailable handled cleanly (502).
8. Provider timeout handled cleanly (504).
9. Empty provider results handled cleanly (200, empty availability state).
10. Normalized response exposes all required stable fields without raw provider payloads.
11. Internal provider IDs are shielded as UUID snapshot IDs and retained internally.
12. Source currency and amounts are strictly preserved without fake exchange rates.
13. Frontend values cannot tamper with or override canonical trip parameters.
"""
from __future__ import annotations

import base64
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

os.environ.setdefault("PAYLOAD_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode("utf-8"))

import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.flight_service import FlightService
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore

client = TestClient(app)

DEFAULT_DEP_DATE = (date.today() + timedelta(days=14)).isoformat()
DEFAULT_RET_DATE = (date.today() + timedelta(days=24)).isoformat()


def _auth_user(email: str = "testuser@trusttrip.com") -> tuple[dict[str, str], dict[str, Any]]:
    """Helper to register/fetch a test user and generate an auth Bearer header."""
    user_store = UserStore()
    user = user_store.get_by_email(email)
    if not user:
        user = user_store.create_user(email=email, password_hash="hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}
    return headers, user


def _sample_raw_duffel_offer(
    offer_id: str = "off_duffel_test_123",
    price: float = 7500.0,
    currency: str = "INR",
    origin: str = "CCU",
    dest: str = "GOI",
    dep_date: str = DEFAULT_DEP_DATE,
    ret_date: str | None = DEFAULT_RET_DATE,
) -> dict[str, Any]:
    """Helper returning a mock Duffel normalized offer dictionary."""
    outbound_seg = {
        "origin": origin,
        "origin_name": f"{origin} Airport",
        "destination": dest,
        "destination_name": f"{dest} Airport",
        "departure": f"{dep_date}T06:00:00",
        "arrival": f"{dep_date}T08:30:00",
        "duration": "PT2H30M",
        "marketing_carrier": "IndiGo",
        "marketing_carrier_iata": "6E",
        "flight_number": "6E 201",
    }
    segments = [outbound_seg]
    slices = [{"segments": [outbound_seg]}]

    if ret_date:
        return_seg = {
            "origin": dest,
            "origin_name": f"{dest} Airport",
            "destination": origin,
            "destination_name": f"{origin} Airport",
            "departure": f"{ret_date}T18:00:00",
            "arrival": f"{ret_date}T20:30:00",
            "duration": "PT2H30M",
            "marketing_carrier": "IndiGo",
            "marketing_carrier_iata": "6E",
            "flight_number": "6E 202",
        }
        segments.append(return_seg)
        slices.append({"segments": [return_seg]})

    return {
        "id": offer_id,
        "provider": "duffel",
        "price": price,
        "currency": currency,
        "price_inr": price if currency == "INR" else price * 83.0,
        "price_inr_source": "provider price (INR)" if currency == "INR" else "reference FX conversion",
        "exchange_rate": 1.0 if currency == "INR" else 83.0,
        "stops": 0,
        "owner": "IndiGo",
        "owner_iata": "6E",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "segments": segments,
        "passengers": [{"type": "adult"}],
        "passenger_count": 2,
        "conditions": {
            "refund_before_departure": {"allowed": False, "penalty_amount": None},
            "change_before_departure": {"allowed": True, "penalty_amount": "500.00"},
        },
        "raw": {
            "id": offer_id,
            "slices": slices,
            "conditions": {
                "refund_before_departure": {"allowed": False},
                "change_before_departure": {"allowed": True},
            },
        },
    }


def test_successful_normalized_flight_search_canonical_trip():
    """1. Successful normalized flight search using canonical trip."""
    headers, user = _auth_user("traveler1@trusttrip.com")
    trip_store = TripStore()

    # Canonical trip: Kolkata -> Goa, CCU -> GOI, 2 travelers, INR
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Kolkata to Goa Beach Trip",
        origin="Kolkata",
        destination="Goa",
        metadata={
            "constraints": {
                "origin": "CCU",
                "origin_airport": "CCU",
                "destination": "GOI",
                "destination_airport": "GOI",
                "departure_date": DEFAULT_DEP_DATE,
                "return_date": DEFAULT_RET_DATE,
                "travelers_count": 2,
                "currency": "INR",
            }
        },
    )

    sample_offer = _sample_raw_duffel_offer(
        offer_id="off_canonical_test_123",
        price=15000.0,
        currency="INR",
        origin="CCU",
        dest="GOI",
        dep_date=DEFAULT_DEP_DATE,
        ret_date=DEFAULT_RET_DATE,
    )

    with patch.object(
        FlightService,
        "search",
        return_value={"provider": "duffel", "count": 1, "offers": [sample_offer], "bookable": True},
    ) as mock_search:
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["success"] is True
        assert data["provider"] == "duffel"
        assert data["availability_state"] == "available"

        # Verify search context passed to provider came from canonical trip
        called_ctx = mock_search.call_args[0][0]
        assert called_ctx["slices"][0]["origin"] == "CCU"
        assert called_ctx["slices"][0]["destination"] == "GOI"
        assert called_ctx["slices"][0]["departure_date"] == DEFAULT_DEP_DATE
        assert called_ctx["slices"][1]["origin"] == "GOI"
        assert called_ctx["slices"][1]["destination"] == "CCU"
        assert called_ctx["slices"][1]["departure_date"] == DEFAULT_RET_DATE
        assert len(called_ctx["passengers"]) == 2

        # Verify metadata in response
        meta = data["metadata"]
        assert meta["trip_id"] == str(trip["id"])
        assert meta["origin"] == "CCU"
        assert meta["destination"] == "GOI"
        assert meta["departure_date"] == DEFAULT_DEP_DATE
        assert meta["return_date"] == DEFAULT_RET_DATE
        assert meta["traveler_count"] == 2
        assert meta["currency"] == "INR"
        assert meta["count"] == 1

        # Verify offers
        assert len(data["offers"]) == 1
        offer = data["offers"][0]
        assert offer["origin"] == "CCU"
        assert offer["destination"] == "GOI"
        assert offer["price"] == 15000.0
        assert offer["currency"] == "INR"
        assert offer["carrier"] == "IndiGo"
        assert offer["refundable"] is False
        assert offer["changeable"] is True


def test_unauthorized_trip_rejected():
    """2. Unauthorized trip (tenant isolation) is rejected with 403."""
    headers_a, user_a = _auth_user("owner@trusttrip.com")
    headers_b, user_b = _auth_user("intruder@trusttrip.com")
    trip_store = TripStore()

    # User A creates trip
    trip_a = trip_store.create_trip(
        user_id=user_a["id"],
        title="User A Trip",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    # User B attempts to search User A's trip -> Must be rejected with 403
    resp = client.post(
        "/api/flights/search",
        headers=headers_b,
        json={"trip_id": str(trip_a["id"])},
    )
    assert resp.status_code == 403
    assert "Unauthorized" in resp.json()["detail"] or "does not belong" in resp.json()["detail"]


def test_missing_trip_rejected():
    """3. Non-existent trip is rejected with 404."""
    headers, _ = _auth_user("traveler_missing@trusttrip.com")
    non_existent_id = str(uuid4())

    resp = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": non_existent_id},
    )
    assert resp.status_code == 404
    assert "Trip not found" in resp.json()["detail"]


def test_missing_airport_rejected():
    """4. Missing or unresolvable canonical airport is rejected with 400."""
    headers, user = _auth_user("traveler_airport@trusttrip.com")
    trip_store = TripStore()

    # Trip with unresolvable imaginary location
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Trip to Nowhere",
        origin="AtlantisImaginaryLocation",
        destination="ElDoradoImaginaryCity",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    resp = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": str(trip["id"])},
    )
    assert resp.status_code == 400
    assert "cannot be resolved" in resp.json()["detail"] or "invalid canonical" in resp.json()["detail"].lower()


def test_invalid_dates_rejected():
    """5. Invalid dates (missing, in past, or return before departure) are rejected with 400."""
    headers, user = _auth_user("traveler_dates@trusttrip.com")
    trip_store = TripStore()

    # 5a. Missing departure date
    trip_no_dates = trip_store.create_trip(
        user_id=user["id"],
        title="No Date Trip",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {}},
    )
    resp1 = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": str(trip_no_dates["id"])},
    )
    assert resp1.status_code == 400
    assert "departure date" in resp1.json()["detail"].lower()

    # 5b. Departure date in the past
    trip_past = trip_store.create_trip(
        user_id=user["id"],
        title="Past Date Trip",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": "2020-01-01"}},
    )
    resp2 = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": str(trip_past["id"])},
    )
    assert resp2.status_code == 400
    assert "past" in resp2.json()["detail"].lower()

    # 5c. Return date precedes departure date
    trip_invalid_ret = trip_store.create_trip(
        user_id=user["id"],
        title="Invalid Return Trip",
        origin="CCU",
        destination="GOI",
        metadata={
            "constraints": {
                "departure_date": (date.today() + timedelta(days=20)).isoformat(),
                "return_date": (date.today() + timedelta(days=15)).isoformat(),
            }
        },
    )
    resp3 = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": str(trip_invalid_ret["id"])},
    )
    assert resp3.status_code == 400
    assert "precede" in resp3.json()["detail"].lower()


def test_invalid_traveler_count_rejected():
    """6. Invalid traveler counts (0 travelers or >9 travelers) are rejected with 400."""
    headers, user = _auth_user("traveler_count@trusttrip.com")
    trip_store = TripStore()

    # 6a. 0 adults
    trip_zero = trip_store.create_trip(
        user_id=user["id"],
        title="Zero Travelers",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE, "adults": 0}},
    )
    resp1 = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": str(trip_zero["id"])},
    )
    assert resp1.status_code == 400
    assert "traveler count" in resp1.json()["detail"].lower()

    # 6b. 10 adults (exceeds max 9)
    trip_ten = trip_store.create_trip(
        user_id=user["id"],
        title="Ten Travelers",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE, "adults": 10}},
    )
    resp2 = client.post(
        "/api/flights/search",
        headers=headers,
        json={"trip_id": str(trip_ten["id"])},
    )
    assert resp2.status_code == 400
    assert "traveler count" in resp2.json()["detail"].lower()


def test_provider_unavailable_handled_cleanly():
    """7. Provider unavailable / error is handled cleanly with HTTP 502."""
    headers, user = _auth_user("traveler_down@trusttrip.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Trip Provider Down",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    with patch.object(FlightService, "search", side_effect=FlightProviderError("Duffel API connection error")):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 502
        assert "unavailable" in resp.json()["detail"].lower() or "error" in resp.json()["detail"].lower()


def test_provider_timeout_handled_cleanly():
    """8. Provider timeout is handled cleanly with HTTP 504."""
    headers, user = _auth_user("traveler_timeout@trusttrip.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Trip Timeout",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    with patch.object(FlightService, "search", side_effect=FlightProviderTimeout("Duffel timed out after 30s")):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()


def test_empty_provider_result_handled_cleanly():
    """9. Empty provider results return HTTP 200 with empty availability state."""
    headers, user = _auth_user("traveler_empty@trusttrip.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Trip Empty Results",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    with patch.object(FlightService, "search", return_value={"provider": "duffel", "count": 0, "offers": []}):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["availability_state"] == "empty"
        assert data["offers"] == []
        assert data["metadata"]["count"] == 0


def test_normalized_response_stable_fields():
    """10. Normalized response exposes all expected stable fields without schema drift."""
    headers, user = _auth_user("traveler_fields@trusttrip.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Stable Fields Trip",
        origin="CCU",
        destination="GOI",
        metadata={
            "constraints": {
                "departure_date": DEFAULT_DEP_DATE,
                "return_date": DEFAULT_RET_DATE,
                "travelers_count": 2,
            }
        },
    )

    sample_offer = _sample_raw_duffel_offer(
        offer_id="off_test_stable_fields",
        price=8200.0,
        currency="INR",
        origin="CCU",
        dest="GOI",
        dep_date=DEFAULT_DEP_DATE,
        ret_date=DEFAULT_RET_DATE,
    )

    with patch.object(FlightService, "search", return_value={"provider": "duffel", "count": 1, "offers": [sample_offer]}):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]

        # Verify all required Section 5A fields are present and typed correctly
        assert isinstance(offer["offer_id"], str)
        assert offer["provider"] == "duffel"
        assert offer["origin"] == "CCU"
        assert offer["destination"] == "GOI"
        assert DEFAULT_DEP_DATE in offer["departure_time"]
        assert DEFAULT_DEP_DATE in offer["arrival_time"]
        assert DEFAULT_RET_DATE in offer["return_departure_time"]
        assert DEFAULT_RET_DATE in offer["return_arrival_time"]
        assert offer["stops"] == 0
        assert offer["carrier"] == "IndiGo"
        assert offer["carrier_code"] == "6E"
        assert offer["cabin_class"] == "economy"
        assert offer["price"] == 8200.0
        assert offer["currency"] == "INR"
        assert offer["price_per_traveler"] == 4100.0
        assert offer["refundable"] is False
        assert offer["changeable"] is True
        assert len(offer["segments"]) == 2

        # Check segment fields
        seg = offer["segments"][0]
        assert seg["origin"] == "CCU"
        assert seg["destination"] == "GOI"
        assert seg["carrier"] == "IndiGo"
        assert seg["flight_number"] == "6E 201"


def test_provider_internal_ids_shielded_and_no_raw_payload_exposed():
    """11. Provider internal IDs (e.g. off_xxx) are shielded as UUIDs and raw payloads are omitted."""
    headers, user = _auth_user("traveler_shield@trusttrip.com")
    trip_store = TripStore()
    raw_provider_id = "off_secret_provider_internal_id_999"

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Shielding Trip",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    sample_offer = _sample_raw_duffel_offer(offer_id=raw_provider_id)

    with patch.object(FlightService, "search", return_value={"provider": "duffel", "count": 1, "offers": [sample_offer]}):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]

        # 1. Raw provider ID must NOT be exposed as offer_id
        assert offer["offer_id"] != raw_provider_id
        # offer_id must be a valid UUID string
        uuid_obj = UUID(offer["offer_id"])
        assert str(uuid_obj) == offer["offer_id"]

        # 2. Raw payload dictionary must NOT be in the API response
        assert "raw" not in offer
        assert "raw_payload" not in offer
        assert raw_provider_id not in str(resp.json())


def test_currency_preserved_correctly():
    """12. Provider currency (e.g. USD) is preserved as source currency with optional reference INR."""
    headers, user = _auth_user("traveler_usd@trusttrip.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user["id"],
        title="USD International Trip",
        origin="CCU",
        destination="GOI",
        metadata={"constraints": {"departure_date": DEFAULT_DEP_DATE}},
    )

    # Provider returns 200.0 USD with 16600.0 INR reference
    sample_offer = _sample_raw_duffel_offer(
        offer_id="off_usd_test",
        price=200.0,
        currency="USD",
    )
    sample_offer["price_inr"] = 16600.0

    with patch.object(FlightService, "search", return_value={"provider": "duffel", "count": 1, "offers": [sample_offer]}):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]

        assert offer["currency"] == "USD"
        assert offer["price"] == 200.0
        assert offer["price_inr"] == 16600.0


def test_frontend_cannot_override_canonical_trip_values():
    """13. Frontend values in request body cannot tamper with or override canonical trip."""
    headers, user = _auth_user("tamper_test@trusttrip.com")
    trip_store = TripStore()

    # Canonical trip: CCU -> GOI, 2 travelers
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Tamper Resistant Trip",
        origin="CCU",
        destination="GOI",
        metadata={
            "constraints": {
                "departure_date": DEFAULT_DEP_DATE,
                "return_date": DEFAULT_RET_DATE,
                "travelers_count": 2,
            }
        },
    )

    sample_offer = _sample_raw_duffel_offer(origin="CCU", dest="GOI")

    with patch.object(
        FlightService,
        "search",
        return_value={"provider": "duffel", "count": 1, "offers": [sample_offer]},
    ) as mock_search:
        # Frontend sends malicious overrides trying to redirect search to JFK -> LHR on 2029-01-01 for 8 adults
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "origin": "JFK",
                "destination": "LHR",
                "departure_date": "2029-01-01",
                "adults": 8,
            },
        )
        assert resp.status_code == 200

        # Verify the backend strictly used CCU -> GOI and 2 travelers from canonical trip
        called_ctx = mock_search.call_args[0][0]
        assert called_ctx["slices"][0]["origin"] == "CCU"
        assert called_ctx["slices"][0]["destination"] == "GOI"
        assert called_ctx["slices"][0]["departure_date"] == DEFAULT_DEP_DATE
        assert len(called_ctx["passengers"]) == 2  # Not 8!
