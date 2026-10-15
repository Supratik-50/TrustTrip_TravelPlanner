"""Focused Test Suite for Section 6 — Hotel Search, Response Mapping, and Selection.

Verifies:
1. Valid hotel search for canonical trip returns normalized results.
2. Empty hotel search results handled cleanly (200 OK with empty availability state).
3. Invalid hotel search request (missing destination, checkin >= checkout) rejected (400).
4. Provider failure (HotelProviderError) handled cleanly (502).
5. Normalized response contract exposes required fields without exposing raw internals.
6. Price and currency mapping (provider price, currency, price_inr, exchange_rate).
7. Valid hotel selection binds snapshot and records selection with price/currency.
8. Unknown hotel/result ID rejected (403/404).
9. Cross-user hotel selection strictly rejected (403).
10. Cross-trip hotel selection strictly rejected (400).
11. Raw provider IDs, tokens, and payloads are strictly shielded from the client.
12. Existing flight search and selection behavior continues to work without regression.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.setdefault("PAYLOAD_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode("utf-8"))

import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.hotel.hotel_provider import HotelProviderError
from services.hotel.hotel_service import HotelService
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    """Ensure in-memory mode and clean state for isolated testing."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_REVALIDATIONS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()


def _auth_user(email: str = "traveler@trusttrip.com") -> tuple[dict[str, str], dict[str, Any]]:
    """Helper to register/fetch a test user and return auth headers."""
    user_store = UserStore()
    user = user_store.get_by_email(email)
    if not user:
        user = user_store.create_user(email=email, password_hash="hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}
    return headers, user


def _create_test_trip(user_id: UUID | str, destination: str = "Kyoto, Japan") -> dict[str, Any]:
    """Helper to create a canonical trip."""
    trip_store = TripStore()
    return trip_store.create_trip(
        user_id=user_id,
        title=f"Trip to {destination}",
        destination=destination,
        origin="Tokyo, Japan",
        metadata={
            "constraints": {
                "destination": destination,
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": 0,
                "rooms": 1,
                "budget_inr": 50000.0,
            }
        },
    )


def _sample_raw_hotel_offer(
    provider_offer_id: str = "raw_liteapi_rate_secret_123",
    hotel_name: str = "Kyoto Heritage Inn",
    price: float = 300.0,
    currency: str = "USD",
    price_inr: float = 25500.0,
    refundable: bool = True,
    bookable: bool = True,
    availability: str = "available",
) -> dict[str, Any]:
    return {
        "id": f"liteapi:{provider_offer_id}",
        "provider": "liteapi",
        "provider_offer_id": provider_offer_id,
        "bookable": bookable,
        "availability": availability,
        "hotel_id": "h_kyoto_999",
        "hotel_name": hotel_name,
        "name": hotel_name,
        "photo": "https://images.example.com/kyoto_inn.jpg",
        "address": "12 Shijo-dori, Shimogyo-ku, Kyoto",
        "rating": 9.2,
        "stars": 4,
        "room_name": "Deluxe Tatami Room",
        "board_name": "Breakfast Included",
        "cancellation": {"refundableTag": "RFN" if refundable else "NRF"},
        "refundable": refundable,
        "price": price,
        "currency": currency,
        "price_inr": price_inr,
        "exchange_rate": 85.0,
        "rooms": 1,
        "adults": 2,
        "children": 0,
        "checkin": "2026-10-14",
        "checkout": "2026-10-20",
        "is_recommended": True,
        "recommendation_rank": 1,
    }


# ============================================================================
# 1. Valid hotel search
# ============================================================================
def test_valid_hotel_search():
    headers, user = _auth_user("searcher@trusttrip.com")
    trip = _create_test_trip(user["id"], destination="Kyoto, Japan")

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": [], "providers_used": ["liteapi"]}):
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["availability_state"] == "available"
        assert len(data["offers"]) == 1

        offer = data["offers"][0]
        assert offer["hotel_name"] == "Kyoto Heritage Inn"
        assert offer["city"] == "Kyoto, Japan"
        assert offer["price"] == 300.0
        assert offer["currency"] == "USD"
        assert UUID(offer["snapshot_id"])  # Valid UUID


# ============================================================================
# 2. Empty results
# ============================================================================
def test_empty_hotel_results():
    headers, user = _auth_user("empty@trusttrip.com")
    trip = _create_test_trip(user["id"])

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}):
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 0
        assert data["availability_state"] == "empty"
        assert data["offers"] == []


# ============================================================================
# 3. Invalid hotel search request
# ============================================================================
def test_invalid_hotel_search_request():
    headers, user = _auth_user("invalid@trusttrip.com")

    # Missing / omitted trip_id rejected (400)
    resp = client.post(
        "/api/hotels/search",
        headers=headers,
        json={
            "destination": "",
            "checkin": "2026-10-14",
            "checkout": "2026-10-20",
        },
    )
    assert resp.status_code == 400
    assert "trip_id is required" in resp.json()["detail"].lower()

    # Missing / empty destination on canonical trip rejected (400)
    trip_empty_dest = TripStore().create_trip(
        user_id=user["id"],
        title="Empty dest trip",
        destination="",
        metadata={"constraints": {"destination": ""}},
    )
    resp = client.post(
        "/api/hotels/search",
        headers=headers,
        json={"trip_id": str(trip_empty_dest["id"])},
    )
    assert resp.status_code == 400
    assert "destination" in resp.json()["detail"].lower()

    # checkin >= checkout on canonical trip rejected (400)
    trip_invalid_dates = TripStore().create_trip(
        user_id=user["id"],
        title="Invalid dates trip",
        destination="Paris, France",
        metadata={
            "constraints": {
                "destination": "Paris, France",
                "checkin_date": "2026-10-20",
                "checkout_date": "2026-10-14",
                "travelers_count": 2,
                "children": 0,
                "rooms": 1,
            }
        },
    )
    resp = client.post(
        "/api/hotels/search",
        headers=headers,
        json={"trip_id": str(trip_invalid_dates["id"])},
    )
    assert resp.status_code == 400
    assert "strictly before" in resp.json()["detail"].lower()


# ============================================================================
# 4. Provider failure
# ============================================================================
def test_hotel_provider_failure():
    headers, user = _auth_user("prov_fail@trusttrip.com")
    trip = _create_test_trip(user["id"])

    with patch.object(HotelService, "search", side_effect=HotelProviderError("Upstream supplier timeout")):
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 502
        assert "supplier timeout" in resp.json()["detail"]


# ============================================================================
# 5. Normalized response contract
# ============================================================================
def test_normalized_hotel_response_contract():
    headers, user = _auth_user("contract@trusttrip.com")
    trip = _create_test_trip(user["id"])

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]

        # Verify all required stable fields
        required_fields = [
            "id", "snapshot_id", "name", "hotel_name", "city", "address",
            "stars", "rating", "cancellation_policy", "refundable",
            "price", "total_amount", "currency", "price_inr",
            "checkin", "checkout", "rooms", "availability",
        ]
        for field in required_fields:
            assert field in offer, f"Missing required field: {field}"

        assert isinstance(offer["rooms"], list)
        assert len(offer["rooms"]) > 0
        assert "price_per_night" in offer["rooms"][0]


# ============================================================================
# 6. Price/currency mapping
# ============================================================================
def test_price_currency_mapping():
    headers, user = _auth_user("fx@trusttrip.com")
    trip = _create_test_trip(user["id"])

    mock_offer = _sample_raw_hotel_offer(price=200.0, currency="USD", price_inr=17000.0)
    mock_offer["exchange_rate"] = 85.0
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]

        assert offer["price"] == 200.0
        assert offer["total_amount"] == 200.0
        assert offer["currency"] == "USD"
        assert offer["price_inr"] == 17000.0
        assert offer["exchange_rate"] == 85.0
        # 6 nights: 2026-10-14 to 2026-10-20
        assert offer["nights"] == 6
        assert offer["nightly_price"] == round(200.0 / 6, 2)


# ============================================================================
# 7. Valid hotel selection
# ============================================================================
def test_valid_hotel_selection():
    headers, user = _auth_user("selector@trusttrip.com")
    trip = _create_test_trip(user["id"])

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        search_resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert search_resp.status_code == 200
        snapshot_id = search_resp.json()["offers"][0]["snapshot_id"]

    # Select via trips select-offer endpoint
    sel_resp = client.post(
        f"/api/trips/{trip['id']}/select-offer",
        headers=headers,
        json={
            "offer_type": "hotel",
            "snapshot_id": snapshot_id,
        },
    )
    assert sel_resp.status_code == 200, sel_resp.text
    sel_data = sel_resp.json()
    assert sel_data["success"] is True
    assert sel_data["snapshot_id"] == snapshot_id
    assert sel_data["offer_type"] == "hotel"

    # Verify trip persistence contains selection with price and currency
    meta = sel_data["trip"]["metadata"]
    assert meta["selected_hotel_snapshot_id"] == snapshot_id
    assert meta["selected_hotel"]["snapshot_id"] == snapshot_id
    assert meta["selected_hotel"]["price"] == 300.0
    assert meta["selected_hotel"]["currency"] == "USD"

    # Also test via explicit hotels/select endpoint
    h_sel_resp = client.post(
        "/api/hotels/select",
        headers=headers,
        json={
            "trip_id": str(trip["id"]),
            "snapshot_id": snapshot_id,
        },
    )
    assert h_sel_resp.status_code == 200
    assert h_sel_resp.json()["snapshot_id"] == snapshot_id


# ============================================================================
# 8. Unknown hotel/result ID
# ============================================================================
def test_unknown_hotel_result_id():
    headers, user = _auth_user("unknown@trusttrip.com")
    trip = _create_test_trip(user["id"])

    fake_id = str(uuid4())
    resp = client.post(
        f"/api/trips/{trip['id']}/select-offer",
        headers=headers,
        json={
            "offer_type": "hotel",
            "snapshot_id": fake_id,
        },
    )
    assert resp.status_code in {403, 404}


# ============================================================================
# 9. Cross-user selection rejected
# ============================================================================
def test_cross_user_selection_rejected():
    headers_a, user_a = _auth_user("usera@trusttrip.com")
    headers_b, user_b = _auth_user("userb@trusttrip.com")

    trip_a = _create_test_trip(user_a["id"])
    trip_b = _create_test_trip(user_b["id"])

    # User A searches and creates a snapshot
    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        search_resp = client.post("/api/hotels/search", headers=headers_a, json={"trip_id": str(trip_a["id"])})
        assert search_resp.status_code == 200
        snapshot_id_a = search_resp.json()["offers"][0]["snapshot_id"]

    # User B attempts to select User A's snapshot on User B's trip
    resp = client.post(
        f"/api/trips/{trip_b['id']}/select-offer",
        headers=headers_b,
        json={
            "offer_type": "hotel",
            "snapshot_id": snapshot_id_a,
        },
    )
    assert resp.status_code == 403
    assert "not found or does not belong to you" in resp.json()["detail"].lower()


# ============================================================================
# 10. Cross-trip selection rejected
# ============================================================================
def test_cross_trip_selection_rejected():
    headers, user = _auth_user("multitrip@trusttrip.com")
    trip_1 = _create_test_trip(user["id"], destination="Kyoto, Japan")
    trip_2 = _create_test_trip(user["id"], destination="Osaka, Japan")

    # Search on Trip 1
    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        search_resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip_1["id"])})
        assert search_resp.status_code == 200
        snapshot_id_1 = search_resp.json()["offers"][0]["snapshot_id"]

    # Try to select Trip 1's snapshot on Trip 2
    resp = client.post(
        f"/api/trips/{trip_2['id']}/select-offer",
        headers=headers,
        json={
            "offer_type": "hotel",
            "snapshot_id": snapshot_id_1,
        },
    )
    assert resp.status_code == 400
    assert "does not belong to the requested trip" in resp.json()["detail"].lower()


# ============================================================================
# 11. Raw provider data/ID shielding
# ============================================================================
def test_raw_provider_data_and_id_shielding():
    headers, user = _auth_user("shield@trusttrip.com")
    trip = _create_test_trip(user["id"])

    secret_raw_id = "liteapi_secret_rateKey_x999_private_token"
    raw_hotel_code = "h_kyoto_999"
    mock_offer = _sample_raw_hotel_offer(provider_offer_id=secret_raw_id)
    mock_offer["hotel_id"] = raw_hotel_code
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        body_text = resp.text

        # Raw provider ID/token must NOT appear anywhere in the HTTP response body
        assert secret_raw_id not in body_text
        offer = resp.json()["offers"][0]
        assert "provider_offer_id" not in offer
        assert "rateKey" not in offer

        # Raw provider hotel ID must NOT be public; internal snapshot UUID must be used
        assert offer["hotel_id"] != raw_hotel_code
        assert offer["hotel_id"] == offer["snapshot_id"]
        assert UUID(offer["hotel_id"])
        assert UUID(offer["id"])

        # Selection still works using snapshot UUID
        sel_resp = client.post(
            "/api/hotels/select",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "snapshot_id": offer["snapshot_id"],
            },
        )
        assert sel_resp.status_code == 200
        assert sel_resp.json()["snapshot_id"] == offer["snapshot_id"]


# ============================================================================
# 12. Existing flight regression
# ============================================================================
def test_existing_flight_regression():
    headers, user = _auth_user("flight_regr@trusttrip.com")
    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Flight Regression Trip",
        destination="London (LHR)",
        origin="New York (JFK)",
        metadata={
            "constraints": {
                "departure_date": "2026-11-01",
                "return_date": "2026-11-10",
                "travelers_count": 1,
            }
        },
    )

    from services.flight_service import FlightService
    mock_flight_offers = [
        {
            "id": "off_flight_duffel_123",
            "total_amount": 550.0,
            "total_currency": "USD",
            "owner": "British Airways",
            "owner_iata": "BA",
            "slices": [
                {
                    "origin": {"iata_code": "JFK"},
                    "destination": {"iata_code": "LHR"},
                    "segments": [
                        {
                            "origin": {"iata_code": "JFK"},
                            "destination": {"iata_code": "LHR"},
                            "departing_at": "2026-11-01T08:00:00",
                            "arriving_at": "2026-11-01T20:00:00",
                            "operating_carrier": {"name": "British Airways"},
                            "marketing_carrier": {"iata_code": "BA"},
                            "marketing_carrier_flight_number": "178",
                            "duration": "PT7H",
                        }
                    ],
                }
            ],
            "passengers": [{"type": "adult"}],
        }
    ]

    with patch.object(FlightService, "search", return_value={"offers": mock_flight_offers, "count": 1}):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert len(data["offers"]) == 1
        flight = data["offers"][0]
        assert UUID(flight["offer_id"])
        assert flight["carrier"] == "British Airways"


# ============================================================================
# Finding 1: Canonical Trip Context Must Win
# ============================================================================
def test_trip_id_with_conflicting_destination():
    headers, user = _auth_user("canon_dest@trusttrip.com")
    trip = _create_test_trip(user["id"], destination="Kyoto, Japan")

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "destination": "Paris, France",  # Conflicting override
                "city": "Paris",
            },
        )
        assert resp.status_code == 200
        # Verify canonical destination reached provider, not the conflicting request override
        call_args = mock_search.call_args[0][0]
        assert call_args["destination"] == "Kyoto, Japan"


def test_trip_id_with_conflicting_dates():
    headers, user = _auth_user("canon_dates@trusttrip.com")
    trip = _create_test_trip(user["id"])  # Canonical: 2026-10-14 to 2026-10-20

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "checkin": "2027-01-01",  # Conflicting overrides
                "checkout": "2027-01-10",
                "check_in_date": "2027-01-01",
                "check_out_date": "2027-01-10",
            },
        )
        assert resp.status_code == 200
        call_args = mock_search.call_args[0][0]
        assert call_args["checkin"] == "2026-10-14"
        assert call_args["checkout"] == "2026-10-20"


def test_trip_id_with_conflicting_traveler_count():
    headers, user = _auth_user("canon_pax@trusttrip.com")
    trip = _create_test_trip(user["id"])  # Canonical travelers_count: 2

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "adults": 6,  # Conflicting override
                "guests": 6,
            },
        )
        assert resp.status_code == 200
        call_args = mock_search.call_args[0][0]
        assert call_args["adults"] == 2


def test_trip_id_with_conflicting_rooms():
    headers, user = _auth_user("canon_rooms@trusttrip.com")
    trip = _create_test_trip(user["id"])  # Canonical rooms: 1

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "rooms": 4,  # Conflicting override
                "rooms_count": 4,
            },
        )
        assert resp.status_code == 200
        call_args = mock_search.call_args[0][0]
        assert call_args["rooms"] == 1


# ============================================================================
# Finding 2: Persistence Fail-Closed
# ============================================================================
def test_search_store_initialization_failure_fails_closed():
    headers, user = _auth_user("store_init_fail@trusttrip.com")
    trip = _create_test_trip(user["id"])

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        with patch("hotel_api.SearchSessionStore", side_effect=Exception("DB pool exhausted")):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert resp.status_code == 503
            assert "persistence unavailable" in resp.json()["detail"].lower()


def test_create_session_failure_fails_closed():
    headers, user = _auth_user("sess_fail@trusttrip.com")
    trip = _create_test_trip(user["id"])

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        with patch.object(SearchSessionStore, "create_session", side_effect=Exception("Session store write failure")):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert resp.status_code == 503
            assert "session could not be created" in resp.json()["detail"].lower()


def test_snapshot_save_failure_fails_closed():
    headers, user = _auth_user("snap_fail@trusttrip.com")
    trip = _create_test_trip(user["id"])

    mock_offer = _sample_raw_hotel_offer()
    with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": []}):
        with patch.object(SearchSessionStore, "save_snapshot", side_effect=Exception("Disk full")):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert resp.status_code == 503
            assert "one or more offer snapshots could not be saved" in resp.json()["detail"].lower()


def test_partial_snapshot_persistence_failure_fails_closed():
    headers, user = _auth_user("partial_fail@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer1 = _sample_raw_hotel_offer(provider_offer_id="offer_1", hotel_name="Hotel One")
    offer2 = _sample_raw_hotel_offer(provider_offer_id="offer_2", hotel_name="Hotel Two")

    call_count = 0
    def _mock_save(session_id, offer, raw_payload):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise Exception("Second snapshot save failed")

    with patch.object(HotelService, "search", return_value={"offers": [offer1, offer2], "count": 2, "errors": []}):
        with patch.object(SearchSessionStore, "save_snapshot", side_effect=_mock_save):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            # Fail closed: must return 503, never partial success
            assert resp.status_code == 503


# ============================================================================
# Finding 3: No Fabricated Provider Data
# ============================================================================
def test_malformed_provider_missing_price_skipped():
    headers, user = _auth_user("mal_price@trusttrip.com")
    trip = _create_test_trip(user["id"])

    # Offer with missing price, zero price, and valid price
    bad_offer = _sample_raw_hotel_offer(provider_offer_id="bad_price", hotel_name="No Price Hotel")
    bad_offer["price"] = None

    zero_price_offer = _sample_raw_hotel_offer(provider_offer_id="zero_price", hotel_name="Zero Price Hotel")
    zero_price_offer["price"] = 0.0

    valid_offer = _sample_raw_hotel_offer(provider_offer_id="good_price", hotel_name="Valid Hotel", price=150.0)

    with patch.object(HotelService, "search", return_value={"offers": [bad_offer, zero_price_offer, valid_offer], "count": 3, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offers = resp.json()["offers"]
        assert len(offers) == 1
        assert offers[0]["hotel_name"] == "Valid Hotel"
        assert offers[0]["price"] == 150.0


def test_malformed_provider_missing_availability_skipped():
    headers, user = _auth_user("mal_avail@trusttrip.com")
    trip = _create_test_trip(user["id"])

    bad_avail = _sample_raw_hotel_offer(provider_offer_id="no_avail", hotel_name="Unavailable Hotel")
    bad_avail["availability"] = None
    bad_avail["bookable"] = False

    valid_avail = _sample_raw_hotel_offer(provider_offer_id="avail_ok", hotel_name="Available Hotel")
    valid_avail["availability"] = "available"

    with patch.object(HotelService, "search", return_value={"offers": [bad_avail, valid_avail], "count": 2, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offers = resp.json()["offers"]
        assert len(offers) == 1
        assert offers[0]["hotel_name"] == "Available Hotel"


def test_malformed_provider_missing_name_skipped():
    headers, user = _auth_user("mal_name@trusttrip.com")
    trip = _create_test_trip(user["id"])

    nameless = _sample_raw_hotel_offer(provider_offer_id="no_name")
    nameless["hotel_name"] = None
    nameless["name"] = None

    generic_name = _sample_raw_hotel_offer(provider_offer_id="generic")
    generic_name["hotel_name"] = "Hotel"
    generic_name["name"] = "Hotel"

    valid = _sample_raw_hotel_offer(provider_offer_id="good_name", hotel_name="Ryokan Kurashiki")

    with patch.object(HotelService, "search", return_value={"offers": [nameless, generic_name, valid], "count": 3, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offers = resp.json()["offers"]
        assert len(offers) == 1
        assert offers[0]["hotel_name"] == "Ryokan Kurashiki"


def test_malformed_provider_missing_optional_metadata_truthful():
    headers, user = _auth_user("truthful_meta@trusttrip.com")
    trip = _create_test_trip(user["id"])

    truthful_offer = _sample_raw_hotel_offer(
        provider_offer_id="truthful_1",
        hotel_name="Minimalist Boutique Hotel",
        price=180.0,
    )
    # Strip optional metadata completely
    truthful_offer["stars"] = None
    truthful_offer["rating"] = None
    truthful_offer["address"] = ""
    truthful_offer["review_count"] = None

    with patch.object(HotelService, "search", return_value={"offers": [truthful_offer], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        # Must be truthful None / empty, not fabricated values
        assert offer["stars"] is None
        assert offer["rating"] is None
        assert offer["review_count"] is None
        assert offer["address"] == ""
        assert "City Center" not in offer["address"]


# ============================================================================
# Section 6 Micro-Remediation B: Mandatory Trip-Bound Hotel Search
# ============================================================================
def test_hotel_search_without_trip_id_rejected():
    """Verify hotel search without trip_id is strictly rejected."""
    headers, _ = _auth_user("notrip@trusttrip.com")

    # Missing trip_id key
    resp1 = client.post(
        "/api/hotels/search",
        headers=headers,
        json={"destination": "Kyoto, Japan", "checkin": "2026-10-14", "checkout": "2026-10-20"},
    )
    assert resp1.status_code == 400
    assert "trip_id is required" in resp1.json()["detail"].lower()

    # Empty string trip_id
    resp2 = client.post(
        "/api/hotels/search",
        headers=headers,
        json={"trip_id": "   ", "destination": "Kyoto, Japan"},
    )
    assert resp2.status_code == 400
    assert "trip_id is required" in resp2.json()["detail"].lower()

    # None trip_id
    resp3 = client.post(
        "/api/hotels/search",
        headers=headers,
        json={"trip_id": None},
    )
    assert resp3.status_code == 400
    assert "trip_id is required" in resp3.json()["detail"].lower()

    # Unauthenticated request without trip_id must fail authentication (401)
    resp4 = client.post(
        "/api/hotels/search",
        json={"destination": "Kyoto, Japan"},
    )
    assert resp4.status_code == 401


def test_tripless_request_does_not_call_provider():
    """Verify tripless hotel search does NOT invoke the upstream hotel provider."""
    headers, _ = _auth_user("noprov@trusttrip.com")

    with patch.object(HotelService, "search") as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"destination": "Tokyo, Japan", "checkin": "2026-10-14", "checkout": "2026-10-20"},
        )
        assert resp.status_code == 400
        mock_search.assert_not_called()

    # Also unauthenticated tripless request does not call provider
    with patch.object(HotelService, "search") as mock_search_unauth:
        resp_unauth = client.post(
            "/api/hotels/search",
            json={"destination": "Tokyo, Japan"},
        )
        assert resp_unauth.status_code == 401
        mock_search_unauth.assert_not_called()


def test_tripless_request_does_not_create_session():
    """Verify tripless hotel search does NOT create a SearchSessionStore session or save snapshots."""
    headers, _ = _auth_user("nosess@trusttrip.com")

    with patch.object(SearchSessionStore, "create_session") as mock_create_sess, \
         patch.object(SearchSessionStore, "save_snapshot") as mock_save_snap:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"destination": "Tokyo, Japan", "checkin": "2026-10-14", "checkout": "2026-10-20"},
        )
        assert resp.status_code == 400
        mock_create_sess.assert_not_called()
        mock_save_snap.assert_not_called()

    # In-memory session store should be completely empty
    assert len(SearchSessionStore._IN_MEMORY_SESSIONS) == 0
    assert len(SearchSessionStore._IN_MEMORY_SNAPSHOTS) == 0


def test_tripless_request_returns_no_offers():
    """Verify tripless hotel search returns no offers, hotels, or generated snapshot IDs."""
    headers, _ = _auth_user("nooffers@trusttrip.com")

    resp = client.post(
        "/api/hotels/search",
        headers=headers,
        json={"destination": "Tokyo, Japan", "checkin": "2026-10-14", "checkout": "2026-10-20"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert "offers" not in data
    assert "hotels" not in data
    assert data.get("success") is not True

    # Unauthenticated returns no offers
    resp_unauth = client.post(
        "/api/hotels/search",
        json={"destination": "Tokyo, Japan"},
    )
    assert resp_unauth.status_code == 401
    data_unauth = resp_unauth.json()
    assert "offers" not in data_unauth
    assert "hotels" not in data_unauth


def test_cross_user_hotel_search_rejected():
    """Verify hotel search for a trip owned by another user is rejected with 403."""
    headers_a, user_a = _auth_user("owner_search_a@trusttrip.com")
    headers_b, user_b = _auth_user("attacker_search_b@trusttrip.com")

    trip_a = _create_test_trip(user_a["id"], destination="Kyoto, Japan")

    # User B attempts to search hotels using User A's trip_id
    with patch.object(HotelService, "search") as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers_b,
            json={"trip_id": str(trip_a["id"])},
        )
        assert resp.status_code == 403
        assert "not belong to you" in resp.json()["detail"].lower()
        mock_search.assert_not_called()

    # Search with non-existent trip_id returns 404
    resp_404 = client.post(
        "/api/hotels/search",
        headers=headers_a,
        json={"trip_id": str(uuid4())},
    )
    assert resp_404.status_code == 404


# ============================================================================
# Section 6 Micro-Remediation C: Strict Canonical Trip Data (Finding 1)
# ============================================================================
def test_missing_canonical_destination_rejects_even_with_request_override():
    headers, user = _auth_user("no_dest@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="No dest trip",
        destination="",
        metadata={
            "constraints": {
                "destination": "",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "rooms": 1,
            }
        },
    )
    with patch.object(HotelService, "search") as mock_search, \
         patch.object(SearchSessionStore, "create_session") as mock_sess:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "destination": "Kyoto, Japan", "city": "Kyoto"},
        )
        assert resp.status_code == 400
        assert "canonical destination" in resp.json()["detail"].lower()
        mock_search.assert_not_called()
        mock_sess.assert_not_called()
    assert len(SearchSessionStore._IN_MEMORY_SESSIONS) == 0


def test_missing_canonical_checkin_rejects_even_with_request_override():
    headers, user = _auth_user("no_checkin@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="No checkin trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "rooms": 1,
            }
        },
    )
    with patch.object(HotelService, "search") as mock_search, \
         patch.object(SearchSessionStore, "create_session") as mock_sess:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "checkin": "2026-10-14", "check_in_date": "2026-10-14"},
        )
        assert resp.status_code == 400
        assert "canonical check-in date" in resp.json()["detail"].lower()
        mock_search.assert_not_called()
        mock_sess.assert_not_called()
    assert len(SearchSessionStore._IN_MEMORY_SESSIONS) == 0


def test_missing_canonical_checkout_rejects_even_with_request_override():
    headers, user = _auth_user("no_checkout@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="No checkout trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "travelers_count": 2,
                "children": 0,
                "rooms": 1,
            }
        },
    )
    with patch.object(HotelService, "search") as mock_search, \
         patch.object(SearchSessionStore, "create_session") as mock_sess:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "checkout": "2026-10-20", "check_out_date": "2026-10-20"},
        )
        assert resp.status_code == 400
        assert "canonical check-out date" in resp.json()["detail"].lower()
        mock_search.assert_not_called()
        mock_sess.assert_not_called()
    assert len(SearchSessionStore._IN_MEMORY_SESSIONS) == 0


def test_missing_canonical_traveler_count_rejects_even_with_request_override():
    headers, user = _auth_user("no_pax@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="No pax trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "rooms": 1,
            }
        },
    )
    with patch.object(HotelService, "search") as mock_search, \
         patch.object(SearchSessionStore, "create_session") as mock_sess:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "adults": 4, "guests": 4},
        )
        assert resp.status_code == 400
        assert "canonical traveler count" in resp.json()["detail"].lower()
        mock_search.assert_not_called()
        mock_sess.assert_not_called()
    assert len(SearchSessionStore._IN_MEMORY_SESSIONS) == 0


def test_missing_canonical_rooms_defaults_to_one():
    """Verify hotel search defaults rooms=1 when omitted from canonical trip."""
    headers, user = _auth_user("no_rooms@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="No rooms trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": 0,
            }
        },
    )
    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "rooms": 2, "rooms_count": 2},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        assert search_args["rooms"] == 1


# ============================================================================
# Section 6 Micro-Remediation C: No Fabricated Hotel Details (Finding 2)
# ============================================================================
def test_missing_currency_rejects_malformed_offer():
    headers, user = _auth_user("no_curr@trusttrip.com")
    trip = _create_test_trip(user["id"])

    # Offer without currency or invalid currency
    bad_offer = _sample_raw_hotel_offer(provider_offer_id="bad_curr_1", hotel_name="No Currency Hotel")
    bad_offer["currency"] = None

    valid_offer = _sample_raw_hotel_offer(provider_offer_id="good_curr_1", hotel_name="Valid JPY Hotel", currency="JPY")

    with patch.object(HotelService, "search", return_value={"offers": [bad_offer, valid_offer], "count": 2, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offers = resp.json()["offers"]
        assert len(offers) == 1
        assert offers[0]["hotel_name"] == "Valid JPY Hotel"
        assert offers[0]["currency"] == "JPY"


def test_missing_room_data_no_fabricated_room():
    headers, user = _auth_user("no_room_data@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(provider_offer_id="no_room_1", hotel_name="Roomless Hotel")
    offer_raw["room_name"] = None
    offer_raw["room"] = None
    offer_raw.pop("rooms", None)

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert offer["room_name"] is None
        assert offer["rooms"] == []


def test_price_only_provider_offer_not_turned_into_selectable_room():
    headers, user = _auth_user("price_only@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(provider_offer_id="price_only_1", hotel_name="Price Only Hotel", price=450.0)
    offer_raw["room_name"] = ""
    offer_raw.pop("rooms", None)

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert offer["price"] == 450.0
        assert offer["room_name"] is None
        assert offer["rooms"] == []


def test_missing_board_type_null_or_empty():
    headers, user = _auth_user("no_board@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(provider_offer_id="no_board_1", hotel_name="No Board Hotel")
    offer_raw["board_name"] = None
    offer_raw["board_type"] = None

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert offer["board_name"] is None


def test_missing_refundable_flag_null_unknown_not_false():
    headers, user = _auth_user("no_ref@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(provider_offer_id="no_ref_1", hotel_name="Unspecified Refund Hotel")
    offer_raw["refundable"] = None
    offer_raw.pop("cancellation", None)

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert offer["refundable"] is None
        assert offer["cancellation_policy"] != "Non-refundable"


def test_valid_provider_room_and_currency_preserved_exactly():
    headers, user = _auth_user("valid_pres@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(
        provider_offer_id="exact_1",
        hotel_name="Grand Alpine Resort",
        price=600.0,
        currency="EUR",
        refundable=True,
    )
    offer_raw["room_name"] = "Panoramic Mountain Suite"
    offer_raw["board_name"] = "Half Board Deluxe"

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert offer["hotel_name"] == "Grand Alpine Resort"
        assert offer["currency"] == "EUR"
        assert offer["room_name"] == "Panoramic Mountain Suite"
        assert offer["board_name"] == "Half Board Deluxe"
        assert offer["refundable"] is True
        assert offer["cancellation_policy"] == "Free cancellation"
        assert len(offer["rooms"]) == 1
        assert offer["rooms"][0]["name"] == "Panoramic Mountain Suite"
        assert offer["rooms"][0]["currency"] == "EUR"
        assert offer["rooms"][0]["total_price"] is None
        assert offer["rooms"][0]["capacity"] is None


# ============================================================================
# Section 6 Micro-Remediation D: Strict Traveler Breakdown (Issue 1)
# ============================================================================

def test_canonical_children_count_reaches_provider():
    """Verify provider search receives canonical trip children count."""
    headers, user = _auth_user("child_canon@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Family trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": 2,
                "rooms": 1,
            }
        },
    )

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        assert search_args["children"] == 2
        assert search_args["adults"] == 2


def test_request_children_cannot_override_canonical_children():
    """Verify client request cannot override canonical trip children count."""
    headers, user = _auth_user("child_no_override@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Family trip fixed",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": 1,
                "rooms": 1,
            }
        },
    )

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "children": 5},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        # Must strictly use canonical children = 1, NOT client-supplied 5
        assert search_args["children"] == 1


def test_missing_canonical_children_defaults_to_zero():
    """Verify hotel search defaults children=0 when omitted from canonical trip."""
    headers, user = _auth_user("no_canon_child@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Trip missing children count",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "rooms": 1,
            }
        },
    )

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "children": 2},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        assert search_args["children"] == 0


def test_provider_not_called_for_invalid_canonical_children():
    """Verify hotel provider is NOT called when canonical children count is invalid."""
    headers, user = _auth_user("provider_not_called@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Invalid children breakdown trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": -1,
                "rooms": 1,
            }
        },
    )

    with patch.object(HotelService, "search") as mock_search:
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 400
        mock_search.assert_not_called()


def test_no_session_or_snapshot_created_on_invalid_canonical_children():
    """Verify no session or snapshot is saved when canonical children validation fails."""
    headers, user = _auth_user("no_session_child@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Invalid child trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": -1,
                "rooms": 1,
            }
        },
    )

    with patch.object(SearchSessionStore, "create_session") as mock_sess:
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 400
        mock_sess.assert_not_called()
    assert len(SearchSessionStore._IN_MEMORY_SESSIONS) == 0
    assert len(SearchSessionStore._IN_MEMORY_SNAPSHOTS) == 0


def test_explicit_three_adult_selection_remains_three_adults():
    """Verify an explicit 3-adult canonical trip maintains 3 adults in hotel search."""
    headers, user = _auth_user("three_adults@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Three Adult Trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 3,
                "adults": 3,
            }
        },
    )
    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        assert search_args["adults"] == 3
        assert search_args["children"] == 0
        assert search_args["rooms"] == 1



# ============================================================================
# Section 6 Micro-Remediation D: Truthful Room Data (Issue 2)
# ============================================================================

def test_provider_room_with_no_price_no_inherited_hotel_price():
    """Verify provider room with missing price does NOT inherit hotel total price."""
    headers, user = _auth_user("no_room_price@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(
        provider_offer_id="offer_no_rprice",
        hotel_name="Grand Hyatt Tokyo",
        price=750.0,
    )
    # Provider room with no price
    offer_raw["rooms"] = [
        {"name": "Deluxe King Room", "capacity": 2}
    ]

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert len(offer["rooms"]) == 1
        room = offer["rooms"][0]
        assert room["name"] == "Deluxe King Room"
        # Must NOT inherit hotel price 750.0
        assert room["total_price"] is None
        assert room["price_per_night"] is None
        assert room["capacity"] == 2


def test_provider_room_with_no_capacity_no_inherited_adult_count():
    """Verify provider room with missing capacity does NOT inherit trip adult count."""
    headers, user = _auth_user("no_room_cap@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(
        provider_offer_id="offer_no_rcap",
        hotel_name="Tokyo Tower Hotel",
        price=500.0,
    )
    # Provider room with price but no capacity
    offer_raw["rooms"] = [
        {"name": "Standard Double", "price": 450.0}
    ]

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert len(offer["rooms"]) == 1
        room = offer["rooms"][0]
        assert room["name"] == "Standard Double"
        assert room["total_price"] == 450.0
        # Must NOT inherit trip adult count (which is 2)
        assert room["capacity"] is None


def test_valid_room_price_and_capacity_preserved_exactly():
    """Verify explicit provider room price and capacity are preserved without alteration."""
    headers, user = _auth_user("exact_room@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(
        provider_offer_id="offer_exact_room",
        hotel_name="Resort Palace",
        price=900.0,
    )
    offer_raw["rooms"] = [
        {
            "id": "rm_suite_1",
            "name": "Oceanfront Penthouse",
            "description": "Top-floor suite with private balcony",
            "total_price": 850.0,
            "capacity": 4,
            "beds": "2 King Beds",
            "amenities": ["Balcony", "Jacuzzi"],
        }
    ]

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert len(offer["rooms"]) == 1
        room = offer["rooms"][0]
        # Issue 2: public room.id must be an internal UUID — never the provider room code
        from uuid import UUID as _UUID
        try:
            _UUID(room["id"])
        except (ValueError, KeyError):
            pytest.fail(f"room.id must be a valid UUID, got: {room.get('id')!r}")
        # Remediation G, Issue 2: provider_room_id must NOT appear in public room response
        assert "provider_room_id" not in room, "provider_room_id must not appear in public room response"
        # Provider room ID is preserved strictly in internal server-side snapshot
        snap_store = SearchSessionStore()
        raw_payload = snap_store.get_raw_payload(offer["snapshot_id"], user["id"])
        internal_rooms = (raw_payload or {}).get("_internal_rooms") or []
        assert any(r.get("provider_room_id") == "rm_suite_1" for r in internal_rooms), (
            "Provider room id must be retained in internal server-side snapshot"
        )
        assert room["name"] == "Oceanfront Penthouse"
        assert room["description"] == "Top-floor suite with private balcony"
        assert room["total_price"] == 850.0
        assert room["capacity"] == 4
        assert room["beds"] == "2 King Beds"
        assert room["amenities"] == ["Balcony", "Jacuzzi"]
        nights = (date(2026, 10, 20) - date(2026, 10, 14)).days
        assert room["price_per_night"] == round(850.0 / nights, 2)


def test_price_only_hotel_offer_does_not_create_fake_selectable_room():
    """Verify hotel offer with only total price does NOT create any fake room records."""
    headers, user = _auth_user("price_only_fake@trusttrip.com")
    trip = _create_test_trip(user["id"])

    # Offer with hotel price but no room breakdown and no room_name
    offer_raw = _sample_raw_hotel_offer(
        provider_offer_id="offer_price_only",
        hotel_name="Budget Inn",
        price=180.0,
    )
    offer_raw["room_name"] = None
    offer_raw.pop("rooms", None)

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert offer["price"] == 180.0
        assert offer["rooms"] == []


def test_missing_optional_room_fields_remain_null_or_empty():
    """Verify missing optional room fields (description, beds, amenities) remain None."""
    headers, user = _auth_user("bare_room@trusttrip.com")
    trip = _create_test_trip(user["id"])

    offer_raw = _sample_raw_hotel_offer(
        provider_offer_id="offer_bare_room",
        hotel_name="Minimalist Capsule",
        price=95.0,
    )
    offer_raw["rooms"] = [
        {"name": "Standard Capsule"}
    ]

    with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offer = resp.json()["offers"][0]
        assert len(offer["rooms"]) == 1
        room = offer["rooms"][0]
        assert room["name"] == "Standard Capsule"
        assert room["description"] is None
        assert room["beds"] is None
        assert room["amenities"] is None
        assert room["total_price"] is None
        assert room["price_per_night"] is None
        assert room["capacity"] is None


# ============================================================================
# Section 6 Micro-Remediation D: Frontend HotelCard Truthfulness Contract (Issue 3)
# ============================================================================

def test_frontend_hotel_card_truthfulness_contract():
    """Verify normalized offer fields strictly support HotelCard truthfulness (no fabricated facts)."""
    headers, user = _auth_user("truth_contract@trusttrip.com")
    trip = _create_test_trip(user["id"])

    # 1. Offer with missing stars, missing rating, no breakfast, no wifi
    offer_missing = _sample_raw_hotel_offer(
        provider_offer_id="truth_missing_1",
        hotel_name="Truthful Simple Stay",
        price=200.0,
    )
    offer_missing["stars"] = None
    offer_missing["rating"] = None
    offer_missing["board_name"] = "Room Only"
    offer_missing["amenities"] = []
    offer_missing["tags"] = []

    # 2. Offer with explicit stars, rating, breakfast, and wifi
    offer_full = _sample_raw_hotel_offer(
        provider_offer_id="truth_full_1",
        hotel_name="Truthful Luxury Stay",
        price=500.0,
    )
    offer_full["stars"] = 5
    offer_full["rating"] = 9.4
    offer_full["board_name"] = "Buffet Breakfast Included"
    offer_full["amenities"] = ["High-Speed Wi-Fi", "Swimming Pool"]

    with patch.object(HotelService, "search", return_value={"offers": [offer_missing, offer_full], "count": 2, "errors": []}):
        resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
        assert resp.status_code == 200
        offers = {o["hotel_name"]: o for o in resp.json()["offers"]}

        # Offer with missing facts must have nulls, NOT fabricated defaults
        o_miss = offers["Truthful Simple Stay"]
        assert o_miss["stars"] is None, "Missing stars must remain null, never fabricated as 4"
        assert o_miss["rating"] is None, "Missing rating must remain null"
        assert "breakfast" not in (o_miss["board_name"] or "").lower()
        assert not any("wifi" in a.lower() for a in o_miss.get("amenities", []))

        # Offer with explicit facts must preserve them truthfully
        o_full = offers["Truthful Luxury Stay"]
        assert o_full["stars"] == 5
        assert o_full["rating"] == 9.4
        assert "breakfast" in (o_full["board_name"] or "").lower()
        assert any("wi-fi" in a.lower() or "wifi" in a.lower() for a in o_full.get("amenities", []))


# ============================================================================
# Section 6 Micro-Remediation E: Trip-Bound Search Modifiers (Issue 1)
# ============================================================================

def test_request_cheaper_mode_cannot_alter_canonical_search_context():
    """Verify client request cheaper_mode cannot inject into or alter canonical provider search params."""
    headers, user = _auth_user("no_inject_cheaper@trusttrip.com")
    trip = _create_test_trip(user["id"])

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "cheaper_mode": True},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        # Request-supplied cheaper_mode MUST NOT be present in canonical provider search context
        assert "cheaper_mode" not in search_args or search_args["cheaper_mode"] is False


def test_request_hotelbeds_destination_code_cannot_alter_canonical_search_context():
    """Verify client request hotelbeds_destination_code cannot inject into canonical provider search params."""
    headers, user = _auth_user("no_inject_hbeds@trusttrip.com")
    trip = _create_test_trip(user["id"])

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"]), "hotelbeds_destination_code": "INJECTED_CODE_999"},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        # Request-supplied code MUST NOT be injected
        assert "hotelbeds_destination_code" not in search_args


def test_canonical_trip_modifiers_respected_in_provider_context():
    """Verify canonical trip constraints for modifiers are properly forwarded to provider."""
    headers, user = _auth_user("canonical_modifiers@trusttrip.com")
    trip = TripStore().create_trip(
        user_id=user["id"],
        title="Canonical Modifiers Trip",
        destination="Kyoto, Japan",
        metadata={
            "constraints": {
                "destination": "Kyoto, Japan",
                "checkin_date": "2026-10-14",
                "checkout_date": "2026-10-20",
                "travelers_count": 2,
                "children": 0,
                "rooms": 1,
                "cheaper_mode": True,
                "hotelbeds_destination_code": "KYO",
            }
        },
    )

    with patch.object(HotelService, "search", return_value={"offers": [], "count": 0, "errors": []}) as mock_search:
        resp = client.post(
            "/api/hotels/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )
        assert resp.status_code == 200
        mock_search.assert_called_once()
        search_args = mock_search.call_args[0][0]
        assert search_args.get("cheaper_mode") is True
        assert search_args.get("hotelbeds_destination_code") == "KYO"

# ============================================================================
# MICRO-REMEDIATION F — Regression Tests
# ============================================================================

class TestHotelbedsProviderTruthfulness:
    """Provider adapter must NOT fabricate hotel_name, room_name, board_name,
    refundable, or currency when the provider does not supply them."""

    def _make_provider(self, monkeypatch):
        from services.hotel.hotelbeds_provider import HotelbedsProvider
        monkeypatch.setenv("HOTELBEDS_API_KEY", "test-key")
        monkeypatch.setenv("HOTELBEDS_SECRET", "test-secret")
        return HotelbedsProvider()

    def _search_with_mocked_dest(self, provider, raw_response, request=None):
        """Call provider.search with both _destination_code and _request mocked."""
        if request is None:
            request = {"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1}
        with patch.object(provider, "_destination_code", return_value="KYO"), \
             patch.object(provider, "_request", return_value=raw_response):
            return provider.search(request)

    def test_hotelbeds_omits_hotel_name_when_missing(self, monkeypatch):
        """hotel_name must be None when provider does not supply hotel.name."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H001",
                    # name deliberately absent
                    "categoryName": None,
                    "rooms": [{
                        "rates": [{
                            "net": "100.00",
                            "rateKey": "rk_001",
                            "rateType": "BOOKABLE",
                        }]
                    }]
                }]
            },
            "currency": "GBP",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["hotel_name"] is None, "hotel_name must be None when provider omits it"

    def test_hotelbeds_omits_room_name_when_missing(self, monkeypatch):
        """room_name must be None when provider does not supply room.name — never fall back to roomCode."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H002",
                    "name": "Test Hotel",
                    "categoryName": 3,
                    "rooms": [{
                        "roomCode": "DELUXE_PROVIDER_CODE",
                        # name deliberately absent
                        "rates": [{
                            "net": "150.00",
                            "rateKey": "rk_002",
                            "rateType": "BOOKABLE",
                        }]
                    }]
                }]
            },
            "currency": "GBP",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["room_name"] is None, "room_name must be None when provider omits it — NOT fall back to roomCode"

    def test_hotelbeds_omits_board_name_when_missing(self, monkeypatch):
        """board_name must be None when provider does not supply boardName — never fabricate 'Room Only'."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H003",
                    "name": "Test Hotel",
                    "categoryName": 3,
                    "rooms": [{
                        "name": "Deluxe Room",
                        "rates": [{
                            "net": "200.00",
                            "rateKey": "rk_003",
                            "rateType": "BOOKABLE",
                            # boardName deliberately absent
                        }]
                    }]
                }]
            },
            "currency": "GBP",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["board_name"] is None, "board_name must be None — never fabricate 'Room Only'"

    def test_hotelbeds_omits_refundable_for_unknown_rate_type(self, monkeypatch):
        """refundable must be None when rateType is unknown — never infer True from absence."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H004",
                    "name": "Test Hotel",
                    "categoryName": 3,
                    "rooms": [{
                        "name": "Standard Room",
                        "rates": [{
                            "net": "120.00",
                            "rateKey": "rk_004",
                            # rateType deliberately absent — unknown policy
                        }]
                    }]
                }]
            },
            "currency": "GBP",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["refundable"] is None, "refundable must be None when rateType is unknown — must not infer True"

    def test_hotelbeds_nonrefundable_rate_type_with_missing_cancellation_policy_yields_none(self, monkeypatch):
        """NONREFUNDABLE rateType + missing cancellation policy => refundable is None.
        rateType must never determine refundable status."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H005",
                    "name": "Test Hotel",
                    "categoryName": 3,
                    "rooms": [{
                        "name": "Economy Room",
                        "rates": [{
                            "net": "80.00",
                            "rateKey": "rk_005",
                            "rateType": "NONREFUNDABLE",
                        }]
                    }]
                }]
            },
            "currency": "GBP",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["refundable"] is None, "NONREFUNDABLE rateType without explicit cancellation policy must yield None"

    def test_hotelbeds_omits_currency_when_missing(self, monkeypatch):
        """currency must be None when provider does not supply it — never default to EUR."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H006",
                    "name": "Test Hotel",
                    "categoryName": 3,
                    "rooms": [{
                        "name": "Standard Room",
                        "rates": [{
                            "net": "90.00",
                            "rateKey": "rk_006",
                            "rateType": "BOOKABLE",
                        }]
                    }]
                }]
            },
            # currency deliberately absent at response level
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["currency"] is None, "currency must be None when provider omits it — must NOT default to EUR"


class TestLiteAPIProviderTruthfulness:
    """LiteAPI provider adapter must NOT fabricate hotel_name, room_name,
    board_name, refundable, or currency when the provider does not supply them."""

    def _make_provider(self, monkeypatch):
        from services.hotel.liteapi_provider import LiteAPIProvider
        monkeypatch.setenv("LITEAPI_SANDBOX_API_KEY", "test-liteapi-key")
        monkeypatch.setenv("LITEAPI_MODE", "sandbox")
        return LiteAPIProvider()

    def _make_response(self, hotel_name=None, room_name=None, board_name=None, refundable_tag=None, currency=None):
        """Build a minimal LiteAPI-style response with optional truthful fields."""
        hotel: dict = {"id": "h_lite_001"}
        if hotel_name is not None:
            hotel["name"] = hotel_name
        room: dict = {}
        if room_name is not None:
            room["name"] = room_name
        rate: dict = {"offerId": "offer_lite_001"}
        if board_name is not None:
            rate["boardName"] = board_name
        if refundable_tag is not None:
            rate["cancellationPolicies"] = {"refundableTag": refundable_tag}
        total: dict = {"amount": 200}
        if currency is not None:
            total["currency"] = currency
        rate["retailRate"] = {"total": [total]}
        room["rates"] = [rate]
        return {
            "hotels": [hotel],
            "data": [{"hotelId": "h_lite_001", "roomTypes": [room]}],
        }

    def test_liteapi_omits_hotel_name_when_missing(self, monkeypatch):
        """hotel_name must be None when provider does not supply it — never 'Hotel'."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response()  # no hotel_name
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["hotel_name"] is None, "hotel_name must be None when provider omits it — must not fabricate 'Hotel'"

    def test_liteapi_omits_room_name_when_missing(self, monkeypatch):
        """room_name must be None when provider does not supply it — never 'Room'."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response(hotel_name="Kyoto Grand")  # no room_name
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["room_name"] is None, "room_name must be None when provider omits it — must not fabricate 'Room'"

    def test_liteapi_omits_board_name_when_missing(self, monkeypatch):
        """board_name must be None when provider does not supply it — never 'Room Only'."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response(hotel_name="Kyoto Grand", room_name="Deluxe")  # no board_name
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["board_name"] is None, "board_name must be None when provider omits it — must not fabricate 'Room Only'"

    def test_liteapi_omits_refundable_when_tag_missing(self, monkeypatch):
        """refundable must be None when cancellationPolicies.refundableTag is absent — never False."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response(hotel_name="Kyoto Grand", currency="USD")  # no refundable_tag
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["refundable"] is None, "refundable must be None when tag is absent — must not infer False"

    def test_liteapi_explicit_rfn_produces_true(self, monkeypatch):
        """refundable must be True when refundableTag is explicitly 'RFN'."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response(hotel_name="Kyoto Grand", refundable_tag="RFN", currency="USD")
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["refundable"] is True

    def test_liteapi_explicit_nrf_produces_false(self, monkeypatch):
        """refundable must be False when refundableTag is explicitly 'NRF'."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response(hotel_name="Kyoto Grand", refundable_tag="NRF", currency="USD")
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["refundable"] is False

    def test_liteapi_omits_currency_when_missing(self, monkeypatch):
        """currency must be None when provider does not supply it — never default to USD."""
        provider = self._make_provider(monkeypatch)
        resp = self._make_response(hotel_name="Kyoto Grand")  # no currency
        with patch.object(provider, "_request", return_value=resp):
            offers = provider.search({"checkin": "2026-10-14", "checkout": "2026-10-20", "adults": 2, "children": 0, "rooms": 1})
        assert len(offers) == 1
        assert offers[0]["currency"] is None, "currency must be None when provider omits it — must NOT default to USD"


class TestRoomIdIsInternalUUID:
    """Public room.id in the /api/hotels/search response must be an internal UUID,
    never the provider-supplied room identifier."""

    def test_room_id_in_search_response_is_uuid(self):
        """When a hotel offer has room-level data, each room.id in the response is a UUID."""
        headers, user = _auth_user("uuid_room_test@trusttrip.com")
        trip = _create_test_trip(user["id"])

        # Offer with an explicit provider room ID that should NOT leak
        mock_offer = {
            **_sample_raw_hotel_offer(),
            "rooms": [{
                "id": "PROVIDER_ROOM_CODE_SECRET_ABC",
                "name": "Deluxe Twin",
                "total_price": 300.0,
                "capacity": 2,
            }]
        }

        with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": [], "providers_used": ["liteapi"]}):
            resp = client.post(
                "/api/hotels/search",
                headers=headers,
                json={"trip_id": str(trip["id"])},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] == 1
        offer = data["offers"][0]
        assert "rooms" in offer and len(offer["rooms"]) == 1
        room = offer["rooms"][0]

        # The public room.id must be a valid UUID (not the provider code)
        try:
            UUID(room["id"])
        except (ValueError, KeyError):
            pytest.fail(f"room.id must be a UUID, got: {room.get('id')!r}")

        # The provider room code must NOT appear as the public room.id
        assert room["id"] != "PROVIDER_ROOM_CODE_SECRET_ABC", (
            "Provider room code must not be exposed as public room.id"
        )

    def test_single_named_room_id_in_search_response_is_uuid(self):
        """When a hotel offer has a single room at the offer level, room.id is a UUID."""
        headers, user = _auth_user("uuid_single_room_test@trusttrip.com")
        trip = _create_test_trip(user["id"])

        # Offer with a named room at the offer level (not in a rooms array)
        mock_offer = {
            **_sample_raw_hotel_offer(),
            "room_name": "Standard King",
        }
        # Ensure 'rooms' is not a list to trigger single-room branch
        mock_offer.pop("rooms", None)

        with patch.object(HotelService, "search", return_value={"offers": [mock_offer], "count": 1, "errors": [], "providers_used": ["liteapi"]}):
            resp = client.post(
                "/api/hotels/search",
                headers=headers,
                json={"trip_id": str(trip["id"])},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] == 1
        offer = data["offers"][0]
        rooms = offer.get("rooms", [])
        if rooms:
            room = rooms[0]
            try:
                UUID(room["id"])
            except (ValueError, KeyError):
                pytest.fail(f"Single-room room.id must be a UUID, got: {room.get('id')!r}")


# ============================================================================
# Section 6 Micro-Remediation G: Targeted Regression Tests
# ============================================================================

class TestHotelbedsRefundabilityRemediationG:
    """Issue 1: Hotelbeds refundability must NEVER be inferred from BOOKABLE rateType.
    Must be determined ONLY by explicit provider cancellation policy.
    """
    def _make_provider(self, monkeypatch):
        from services.hotel.hotelbeds_provider import HotelbedsProvider
        monkeypatch.setenv("HOTELBEDS_API_KEY", "test-hb-key")
        monkeypatch.setenv("HOTELBEDS_SECRET", "test-hb-secret")
        monkeypatch.setenv("HOTELBEDS_DESTINATION_CODES_JSON", '{"kyoto": "KYO"}')
        return HotelbedsProvider()

    def _search_with_mocked_dest(self, provider, raw_response):
        with patch.object(provider, "_request", return_value=raw_response):
            return provider.search({
                "destination": "Kyoto",
                "checkin": "2026-10-14",
                "checkout": "2026-10-20",
                "adults": 2,
                "children": 0,
                "rooms": 1,
            })

    def test_case_a_bookable_no_cancellation_policy_yields_none(self, monkeypatch):
        """Case A: BOOKABLE + no cancellation policy => refundable is None."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H001",
                    "name": "Kyoto Hotel",
                    "categoryName": 4,
                    "rooms": [{
                        "name": "Standard Room",
                        "rates": [{
                            "net": "100.00",
                            "rateKey": "rk_001",
                            "rateType": "BOOKABLE",
                            # cancellationPolicies absent
                        }]
                    }]
                }]
            },
            "currency": "EUR",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["refundable"] is None, "BOOKABLE without cancellation policy must yield None, never True"

    def test_case_b_unknown_or_missing_cancellation_policy_yields_none(self, monkeypatch):
        """Case B: unknown/missing cancellation policy => refundable is None."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H002",
                    "name": "Kyoto Hotel",
                    "categoryName": 4,
                    "rooms": [{
                        "name": "Standard Room",
                        "rates": [{
                            "net": "100.00",
                            "rateKey": "rk_002",
                            "rateType": "UNKNOWN_TYPE",
                            "cancellationPolicies": [],  # empty policy list
                        }]
                    }]
                }]
            },
            "currency": "EUR",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["refundable"] is None, "Unknown policy must yield None"

    def test_case_c_explicit_refundable_cancellation_status_yields_true(self, monkeypatch):
        """Case C: explicit refundable provider cancellation status => refundable=True."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H003",
                    "name": "Kyoto Hotel",
                    "categoryName": 4,
                    "rooms": [{
                        "name": "Standard Room",
                        "rates": [{
                            "net": "100.00",
                            "rateKey": "rk_003",
                            "rateType": "BOOKABLE",
                            "cancellationPolicies": [{"refundableTag": "RFN"}],
                        }]
                    }]
                }]
            },
            "currency": "EUR",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["refundable"] is True, "Explicit refundable policy must yield True"

    def test_case_d_explicit_nonrefundable_cancellation_status_yields_false(self, monkeypatch):
        """Case D: explicit non-refundable provider cancellation status => refundable=False."""
        provider = self._make_provider(monkeypatch)
        raw_response = {
            "hotels": {
                "hotels": [{
                    "code": "H004",
                    "name": "Kyoto Hotel",
                    "categoryName": 4,
                    "rooms": [{
                        "name": "Standard Room",
                        "rates": [{
                            "net": "100.00",
                            "rateKey": "rk_004",
                            "rateType": "BOOKABLE",
                            "cancellationPolicies": [{"refundableTag": "NRF"}],
                        }]
                    }]
                }]
            },
            "currency": "EUR",
        }
        offers = self._search_with_mocked_dest(provider, raw_response)
        assert len(offers) == 1
        assert offers[0]["refundable"] is False, "Explicit nonrefundable policy must yield False"

    def test_case_e_downstream_api_does_not_turn_none_into_free_cancellation(self):
        """Case E: downstream API does not turn None into 'Free cancellation'."""
        headers, user = _auth_user("none_not_free@trusttrip.com")
        trip = _create_test_trip(user["id"])

        offer_raw = _sample_raw_hotel_offer(provider_offer_id="hb_none_policy", hotel_name="Ambiguous Policy Inn")
        offer_raw["refundable"] = None
        offer_raw["cancellation"] = []

        with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert resp.status_code == 200
            offer = resp.json()["offers"][0]
            assert offer["refundable"] is None
            assert offer["cancellation_policy"] is None
            assert offer["cancellation_policy"] != "Free cancellation"


class TestPublicRoomIdSecurityBoundaryRemediationG:
    """Issue 2: Raw provider_room_id must NEVER appear in public response."""

    def test_public_room_id_boundary_and_snapshot_retention(self):
        """Verify:
        A. room['id'] is a valid UUID
        B. provider room code is NOT equal to room['id']
        C. 'provider_room_id' not in room
        D. no equivalent provider room identifier appears in public room dictionary
        E. internal persisted snapshot still retains provider room identifier
        F. room selection can resolve public UUID -> internal stored room -> provider room identifier
        """
        headers, user = _auth_user("room_boundary@trusttrip.com")
        trip = _create_test_trip(user["id"])

        provider_room_code = "SECRET_PROVIDER_ROOM_XYZ_999"
        offer_raw = _sample_raw_hotel_offer(
            provider_offer_id="h_prov_99",
            hotel_name="Grand Security Hotel",
            price=500.0,
            currency="EUR",
        )
        offer_raw["rooms"] = [
            {
                "id": provider_room_code,
                "name": "Presidential Suite",
                "price": 500.0,
                "capacity": 2,
                "description": "Luxury suite with panoramic view",
            }
        ]

        with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert resp.status_code == 200

        data = resp.json()
        offer = data["offers"][0]
        assert len(offer["rooms"]) == 1
        room = offer["rooms"][0]

        # A. room["id"] is a valid UUID
        try:
            parsed_uuid = UUID(room["id"])
        except (ValueError, KeyError):
            pytest.fail(f"room.id must be a valid UUID, got: {room.get('id')!r}")

        # B. provider room code is NOT equal to room["id"]
        assert room["id"] != provider_room_code

        # C. "provider_room_id" not in room
        assert "provider_room_id" not in room

        # D. no equivalent provider room identifier appears in the public room dictionary
        assert provider_room_code not in json.dumps(room)

        # E. the internal persisted snapshot still retains whatever provider room identifier is required
        snap_store = SearchSessionStore()
        raw_payload = snap_store.get_raw_payload(offer["snapshot_id"], user["id"])
        assert raw_payload is not None
        internal_rooms = raw_payload.get("_internal_rooms") or []
        assert len(internal_rooms) == 1
        internal_room = internal_rooms[0]
        assert internal_room["provider_room_id"] == provider_room_code
        assert internal_room["id"] == str(parsed_uuid)

        # F. room selection can resolve public UUID -> internal stored room -> provider room identifier
        resolved_room = next((r for r in internal_rooms if r["id"] == room["id"]), None)
        assert resolved_room is not None
        assert resolved_room["provider_room_id"] == provider_room_code

    def test_single_room_branch_public_room_id_boundary(self):
        """Verify single named room branch also adheres to public room ID boundary."""
        headers, user = _auth_user("single_room_boundary@trusttrip.com")
        trip = _create_test_trip(user["id"])

        offer_raw = _sample_raw_hotel_offer(
            provider_offer_id="h_prov_single",
            hotel_name="Boutique Inn",
            price=300.0,
            currency="USD",
        )
        offer_raw["room_name"] = "Deluxe King Room"
        offer_raw.pop("rooms", None)

        with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
            resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert resp.status_code == 200

        offer = resp.json()["offers"][0]
        assert len(offer["rooms"]) == 1
        room = offer["rooms"][0]

        UUID(room["id"])  # Valid UUID
        assert "provider_room_id" not in room
        assert "room_" not in room["id"]  # id is pure UUID, not room_{snapshot_id}


class TestSelectedRoomPersistenceRemediationG:
    """Issue 3: Selected hotel room must be validated against persisted snapshot and actually persisted."""

    def _setup_search_with_rooms(self, user_email="select_room_test@trusttrip.com"):
        headers, user = _auth_user(user_email)
        trip = _create_test_trip(user["id"])

        offer_raw = _sample_raw_hotel_offer(
            provider_offer_id="h_select_001",
            hotel_name="Grand Selection Palace",
            price=450.0,
            currency="USD",
        )
        offer_raw["rooms"] = [
            {
                "id": "prov_room_alpha",
                "name": "Alpha Deluxe Suite",
                "total_price": 450.0,
                "capacity": 2,
                "beds": "1 King Bed",
                "description": "Ocean view suite",
            },
            {
                "id": "prov_room_beta",
                "name": "Beta Executive Suite",
                "total_price": 600.0,
                "capacity": 3,
                "beds": "2 Queen Beds",
                "description": "Executive floor suite",
            },
        ]

        with patch.object(HotelService, "search", return_value={"offers": [offer_raw], "count": 1, "errors": []}):
            search_resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert search_resp.status_code == 200

        data = search_resp.json()
        hotel_offer = data["offers"][0]
        return headers, user, trip, hotel_offer

    def test_case_a_valid_hotel_and_valid_room_persists_selected_room(self):
        """A. valid hotel + valid room UUID => selected room is persisted."""
        headers, user, trip, hotel_offer = self._setup_search_with_rooms("valid_room_persist@trusttrip.com")
        room = hotel_offer["rooms"][0]

        select_resp = client.post(
            f"/api/trips/{trip['id']}/select-offer",
            headers=headers,
            json={
                "offer_type": "hotel",
                "snapshot_id": hotel_offer["snapshot_id"],
                "room_id": room["id"],
            },
        )
        assert select_resp.status_code == 200, select_resp.text
        updated_trip = select_resp.json()["trip"]
        meta = updated_trip.get("metadata", {})
        assert "selected_room" in meta
        sel_room = meta["selected_room"]
        assert sel_room["id"] == room["id"]
        assert sel_room["name"] == "Alpha Deluxe Suite"
        assert sel_room["total_price"] == 450.0
        assert "provider_room_id" not in sel_room

    def test_case_b_random_room_uuid_rejected(self):
        """B. random room UUID => rejected."""
        headers, user, trip, hotel_offer = self._setup_search_with_rooms("random_room_rej@trusttrip.com")
        random_uuid = str(uuid4())

        select_resp = client.post(
            f"/api/trips/{trip['id']}/select-offer",
            headers=headers,
            json={
                "offer_type": "hotel",
                "snapshot_id": hotel_offer["snapshot_id"],
                "room_id": random_uuid,
            },
        )
        assert select_resp.status_code == 400
        assert "does not exist in the selected hotel snapshot" in select_resp.json()["detail"]

    def test_case_c_room_uuid_from_another_hotel_in_same_snapshot_rejected(self):
        """C. room UUID from another hotel in the same snapshot => rejected."""
        headers, user = _auth_user("cross_hotel_room@trusttrip.com")
        trip = _create_test_trip(user["id"])

        offer_1 = _sample_raw_hotel_offer(provider_offer_id="h_cross_1", hotel_name="Hotel 1")
        offer_1["rooms"] = [{"id": "r1", "name": "Room 1", "price": 200.0}]
        offer_2 = _sample_raw_hotel_offer(provider_offer_id="h_cross_2", hotel_name="Hotel 2")
        offer_2["rooms"] = [{"id": "r2", "name": "Room 2", "price": 300.0}]

        with patch.object(HotelService, "search", return_value={"offers": [offer_1, offer_2], "count": 2, "errors": []}):
            search_resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
            assert search_resp.status_code == 200

        data = search_resp.json()
        hotel_1 = data["offers"][0]
        hotel_2 = data["offers"][1]
        hotel_2_room_id = hotel_2["rooms"][0]["id"]

        # Attempt to select hotel_1 with hotel_2's room UUID
        select_resp = client.post(
            f"/api/trips/{trip['id']}/select-offer",
            headers=headers,
            json={
                "offer_type": "hotel",
                "snapshot_id": hotel_1["snapshot_id"],
                "room_id": hotel_2_room_id,
            },
        )
        assert select_resp.status_code == 400
        assert "does not exist in the selected hotel snapshot" in select_resp.json()["detail"]

    def test_case_d_room_uuid_from_another_trip_user_rejected(self):
        """D. room UUID from another trip/user => rejected."""
        headers_a, user_a, trip_a, hotel_a = self._setup_search_with_rooms("user_a_room@trusttrip.com")
        headers_b, user_b, trip_b, hotel_b = self._setup_search_with_rooms("user_b_room@trusttrip.com")

        user_b_room_id = hotel_b["rooms"][0]["id"]

        # User A tries to select their hotel but with User B's room UUID
        select_resp = client.post(
            f"/api/trips/{trip_a['id']}/select-offer",
            headers=headers_a,
            json={
                "offer_type": "hotel",
                "snapshot_id": hotel_a["snapshot_id"],
                "room_id": user_b_room_id,
            },
        )
        assert select_resp.status_code == 400
        assert "does not exist in the selected hotel snapshot" in select_resp.json()["detail"]

    def test_case_e_provider_room_id_supplied_directly_rejected(self):
        """E. provider room ID supplied directly by client => ignored/rejected."""
        headers, user, trip, hotel_offer = self._setup_search_with_rooms("prov_id_direct@trusttrip.com")

        # Client sends raw provider room code "prov_room_alpha"
        select_resp = client.post(
            f"/api/trips/{trip['id']}/select-offer",
            headers=headers,
            json={
                "offer_type": "hotel",
                "snapshot_id": hotel_offer["snapshot_id"],
                "room_id": "prov_room_alpha",
            },
        )
        assert select_resp.status_code == 400
        assert "must be a valid TrustTrip UUID" in select_resp.json()["detail"]

    def test_case_f_persisted_selected_room_does_not_expose_provider_room_id(self):
        """F. persisted selected-room representation does not expose the provider room ID through the trip API."""
        headers, user, trip, hotel_offer = self._setup_search_with_rooms("no_prov_leak@trusttrip.com")
        room = hotel_offer["rooms"][0]

        select_resp = client.post(
            f"/api/trips/{trip['id']}/select-offer",
            headers=headers,
            json={
                "offer_type": "hotel",
                "snapshot_id": hotel_offer["snapshot_id"],
                "room_id": room["id"],
            },
        )
        assert select_resp.status_code == 200
        trip_data = select_resp.json()["trip"]
        meta_str = json.dumps(trip_data.get("metadata", {}))
        assert "prov_room_alpha" not in meta_str
        assert "provider_room_id" not in meta_str

    def test_case_g_reloading_trip_retrieves_selected_room_correctly(self):
        """G. reloading the trip retrieves the selected room correctly."""
        headers, user, trip, hotel_offer = self._setup_search_with_rooms("reload_trip_room@trusttrip.com")
        room = hotel_offer["rooms"][1]  # Select beta room

        # Select via /api/hotels/select
        select_resp = client.post(
            "/api/hotels/select",
            headers=headers,
            json={
                "trip_id": str(trip["id"]),
                "snapshot_id": hotel_offer["snapshot_id"],
                "room_id": room["id"],
            },
        )
        assert select_resp.status_code == 200

        # Reload trip via GET /api/trips/{trip_id}
        get_resp = client.get(f"/api/trips/{trip['id']}", headers=headers)
        assert get_resp.status_code == 200
        trip_body = get_resp.json()
        reloaded_trip = trip_body.get("trip") or trip_body
        reloaded_meta = reloaded_trip.get("metadata", {})
        if isinstance(reloaded_meta, str):
            reloaded_meta = json.loads(reloaded_meta)
        assert "selected_room" in reloaded_meta
        assert reloaded_meta["selected_room"]["id"] == room["id"]
        assert reloaded_meta["selected_room"]["name"] == "Beta Executive Suite"
        assert reloaded_meta["selected_room"]["total_price"] == 600.0
        assert "provider_room_id" not in reloaded_meta["selected_room"]




