"""Persistence Integrity Tests for Section 5A Micro-Remediation A.

Verifies that flight search NEVER returns success=True with available offers
unless every snapshot is verifiably persisted in the store.

Requirements tested:
1. SearchSessionStore initialization failure  => 503, no offers returned
2. Search-session persistence failure          => 503, no offers returned
3. Offer-snapshot persistence failure          => 503, no offers returned
4. Successful persistence + search             => 200, offers returned
5. ID shielding intact after remediation       => offer_id is UUID, not provider ID
6. Empty-result path unchanged                 => 200, availability_state="empty"
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

# Set PAYLOAD_ENCRYPTION_KEY before importing app so SearchSessionStore init succeeds
# for tests that want it to work.
os.environ.setdefault("PAYLOAD_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode("utf-8"))

from app import app  # noqa: E402
from services.auth_service import create_access_token  # noqa: E402
from services.auth_store import UserStore  # noqa: E402
from services.flight_service import FlightService  # noqa: E402
from services.search_session_store import SearchSessionStore  # noqa: E402
from services.trip_store import TripStore  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_user(email: str) -> tuple[dict[str, str], dict[str, Any]]:
    store = UserStore()
    user = store.get_by_email(email)
    if not user:
        user = store.create_user(email=email, password_hash="hashed_pw")
    token = create_access_token(user["id"], user["email"])
    return {"Authorization": f"Bearer {token}"}, user


def _canonical_trip(user_id: str, origin: str = "CCU", dest: str = "GOI") -> dict[str, Any]:
    store = TripStore()
    return store.create_trip(
        user_id=user_id,
        title=f"{origin} to {dest} persistence test",
        origin=origin,
        destination=dest,
        metadata={
            "constraints": {
                "origin": origin,
                "origin_airport": origin,
                "destination": dest,
                "destination_airport": dest,
                "departure_date": "2026-10-14",
                "adults": 2,
                "currency": "INR",
            }
        },
    )


def _sample_provider_offer(
    offer_id: str = "off_persistence_test_001",
    price: float = 5000.0,
    currency: str = "INR",
    origin: str = "CCU",
    dest: str = "GOI",
) -> dict[str, Any]:
    seg = {
        "origin": origin,
        "origin_name": f"{origin} Airport",
        "destination": dest,
        "destination_name": f"{dest} Airport",
        "departure": "2026-10-14T06:00:00",
        "arrival": "2026-10-14T08:30:00",
        "duration": "PT2H30M",
        "marketing_carrier": "IndiGo",
        "marketing_carrier_iata": "6E",
        "flight_number": "6E 201",
    }
    return {
        "id": offer_id,
        "provider": "duffel",
        "price": price,
        "currency": currency,
        "expires_at": "2026-10-10T12:00:00",
        "owner": "IndiGo",
        "owner_iata": "6E",
        "stops": 0,
        "segments": [seg],
        "passenger_count": 2,
        "available_seats": None,
        "inventory_note": "No seat count",
        "payment_requirements": {},
        "passengers": [{"type": "adult"}, {"type": "adult"}],
        "raw": {"slices": [{"segments": [{
            "departing_at": "2026-10-14T06:00:00",
            "arriving_at": "2026-10-14T08:30:00",
            "origin": {"iata_code": origin},
            "destination": {"iata_code": dest},
            "marketing_carrier": {"name": "IndiGo", "iata_code": "6E"},
            "marketing_carrier_flight_number": "6E201",
            "duration": "PT2H30M",
        }]}]},
    }


_GOOD_PROVIDER_RESULT = {
    "provider": "duffel",
    "count": 1,
    "offers": [_sample_provider_offer()],
    "bookable": True,
}


# ---------------------------------------------------------------------------
# Test 1: SearchSessionStore initialization failure => 503
# ---------------------------------------------------------------------------

def test_store_init_failure_returns_503():
    """Store init failure must return 503, not success with offers."""
    headers, user = _auth_user("persist_init_fail@trusttrip.com")
    trip = _canonical_trip(user["id"])

    with (
        patch.object(FlightService, "search", return_value=_GOOD_PROVIDER_RESULT),
        patch(
            "services.flight_search_service.SearchSessionStore",
            side_effect=RuntimeError("PAYLOAD_ENCRYPTION_KEY is missing."),
        ),
    ):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert not body.get("offers"), "Must not return offers when store init fails"


# ---------------------------------------------------------------------------
# Test 2: Search-session persistence failure => 503
# ---------------------------------------------------------------------------

def test_session_create_failure_returns_503():
    """create_session() failure must return 503, not success with offers."""
    headers, user = _auth_user("persist_session_fail@trusttrip.com")
    trip = _canonical_trip(user["id"])

    mock_store = MagicMock(spec=SearchSessionStore)
    mock_store.create_session.side_effect = RuntimeError("DB connection refused")

    with (
        patch.object(FlightService, "search", return_value=_GOOD_PROVIDER_RESULT),
        patch("services.flight_search_service.SearchSessionStore", return_value=mock_store),
    ):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert not body.get("offers"), "Must not return offers when session creation fails"


# ---------------------------------------------------------------------------
# Test 3: Offer-snapshot persistence failure => 503
# ---------------------------------------------------------------------------

def test_snapshot_save_failure_returns_503():
    """save_snapshot() failure must return 503; no partial offer list returned."""
    headers, user = _auth_user("persist_snapshot_fail@trusttrip.com")
    trip = _canonical_trip(user["id"])

    mock_store = MagicMock(spec=SearchSessionStore)
    mock_session = MagicMock()
    mock_session.session_id = uuid4()
    mock_store.create_session.return_value = mock_session
    mock_store.save_snapshot.side_effect = RuntimeError("Disk full: cannot write snapshot")

    with (
        patch.object(FlightService, "search", return_value=_GOOD_PROVIDER_RESULT),
        patch("services.flight_search_service.SearchSessionStore", return_value=mock_store),
    ):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

    assert resp.status_code == 503, f"Expected 503, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert not body.get("offers"), "Must not return offers when snapshot persistence fails"


# ---------------------------------------------------------------------------
# Test 4: Successful persistence + provider search => 200 with offers
# ---------------------------------------------------------------------------

def test_successful_persistence_returns_200_with_offers():
    """When all persistence succeeds, 200 with normalized offers is returned."""
    headers, user = _auth_user("persist_success@trusttrip.com")
    trip = _canonical_trip(user["id"])

    mock_store = MagicMock(spec=SearchSessionStore)
    mock_session = MagicMock()
    mock_session.session_id = uuid4()
    mock_store.create_session.return_value = mock_session
    mock_store.save_snapshot.return_value = None  # success

    with (
        patch.object(FlightService, "search", return_value=_GOOD_PROVIDER_RESULT),
        patch("services.flight_search_service.SearchSessionStore", return_value=mock_store),
    ):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["success"] is True
    assert body["availability_state"] == "available"
    assert len(body["offers"]) == 1
    mock_store.save_snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: ID shielding remains intact after remediation
# ---------------------------------------------------------------------------

def test_id_shielding_intact_after_remediation():
    """offer_id in the response must be a UUID, not the raw Duffel provider ID."""
    raw_provider_id = "off_secret_internal_xyz_789"
    headers, user = _auth_user("persist_shield_check@trusttrip.com")
    trip = _canonical_trip(user["id"])

    sample_offer = _sample_provider_offer(offer_id=raw_provider_id)

    mock_store = MagicMock(spec=SearchSessionStore)
    mock_session = MagicMock()
    mock_session.session_id = uuid4()
    mock_store.create_session.return_value = mock_session
    mock_store.save_snapshot.return_value = None

    with (
        patch.object(
            FlightService,
            "search",
            return_value={"provider": "duffel", "count": 1, "offers": [sample_offer], "bookable": True},
        ),
        patch("services.flight_search_service.SearchSessionStore", return_value=mock_store),
    ):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

    assert resp.status_code == 200, resp.text
    offer = resp.json()["offers"][0]

    assert offer["offer_id"] != raw_provider_id, "Raw provider ID must be shielded"
    uuid_obj = UUID(offer["offer_id"])
    assert str(uuid_obj) == offer["offer_id"], "offer_id must be a valid UUID string"
    assert "raw" not in offer
    assert "raw_payload" not in offer
    assert raw_provider_id not in str(resp.json())


# ---------------------------------------------------------------------------
# Test 6: Empty-result path bypasses persistence entirely => 200 empty
# ---------------------------------------------------------------------------

def test_empty_result_bypasses_persistence_correctly():
    """When provider returns zero offers, persistence store is never instantiated."""
    headers, user = _auth_user("persist_empty@trusttrip.com")
    trip = _canonical_trip(user["id"])

    with (
        patch.object(FlightService, "search", return_value={"provider": "duffel", "count": 0, "offers": []}),
        patch(
            "services.flight_search_service.SearchSessionStore",
            side_effect=RuntimeError("Must not be called for empty results"),
        ) as mock_store_cls,
    ):
        resp = client.post(
            "/api/flights/search",
            headers=headers,
            json={"trip_id": str(trip["id"])},
        )

    mock_store_cls.assert_not_called()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["success"] is True
    assert body["availability_state"] == "empty"
    assert body["offers"] == []
