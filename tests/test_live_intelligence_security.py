"""Security and Tenancy Tests for Live Travel Intelligence."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import (
    FlightOffer,
    FlightSegmentModel,
    PriceBreakdown,
)
from services.auth_service import create_access_token
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import _scrub_payload


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_REVALIDATIONS.clear()


def test_cross_tenant_snapshot_access_is_blocked():
    """Verify that User B cannot retrieve or revalidate User A's offer snapshot."""
    user_a_id = uuid4()
    user_b_id = uuid4()
    trip_id = uuid4()

    store = SearchSessionStore()
    session = store.create_session(
        user_id=user_a_id,
        trip_id=trip_id,
        thread_id="thread_user_a",
        search_type="flights",
        criteria_hash="hash_a",
    )

    now = datetime.now(timezone.utc)
    offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_test_a",
        price=PriceBreakdown(base_amount=Decimal("150.00"), total_amount=Decimal("150.00"), currency="USD"),
        stops=0,
        segments=[
            FlightSegmentModel(
                origin_iata="DEL",
                destination_iata="DXB",
                departure_time=now,
                arrival_time=now,
                marketing_carrier_iata="AI",
                marketing_carrier_name="Air India",
                flight_number="101",
                duration_minutes=180,
            )
        ],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now,
    )

    snapshot = store.save_snapshot(session.session_id, offer, raw_payload={"owner": "User A"})

    # User A can retrieve
    assert store.get_snapshot(snapshot.snapshot_id, user_a_id) is not None

    # User B MUST NOT be able to retrieve User A's snapshot
    assert store.get_snapshot(snapshot.snapshot_id, user_b_id) is None


def test_revalidate_endpoint_enforces_tenant_authorization():
    """Verify HTTP endpoint rejects cross-tenant revalidation attempts with 403."""
    user_a_id = uuid4()
    user_b_id = uuid4()
    trip_id = uuid4()

    from services.auth_store import UserStore
    user_store = UserStore()
    user_a = user_store.create_user(email="usera@test.com", password_hash="hash")
    user_b = user_store.create_user(email="userb@test.com", password_hash="hash")
    user_a_id = user_a["id"]
    user_b_id = user_b["id"]

    store = SearchSessionStore()
    session = store.create_session(
        user_id=user_a_id,
        trip_id=trip_id,
        thread_id="th_a",
        search_type="flights",
        criteria_hash="hash_a",
    )

    now = datetime.now(timezone.utc)
    offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_test_a",
        price=PriceBreakdown(base_amount=Decimal("150.00"), total_amount=Decimal("150.00"), currency="USD"),
        stops=0,
        segments=[],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now,
    )
    snapshot = store.save_snapshot(session.session_id, offer, raw_payload={})

    token_b = create_access_token(user_id=user_b_id, email="userb@test.com")

    client = TestClient(app)
    # User B requests User A's snapshot
    response = client.post(
        "/api/offers/revalidate",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"snapshot_id": str(snapshot.snapshot_id)},
    )
    assert response.status_code == 403
    assert "Access denied" in response.json()["detail"]


def test_revalidate_endpoint_rejects_provider_offer_id():
    """Verify HTTP endpoint rejects client supplying a provider_offer_id instead of a valid UUID snapshot_id."""
    from services.auth_store import UserStore
    user_store = UserStore()
    user = user_store.create_user(email="user_c@test.com", password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email="user_c@test.com")
    client = TestClient(app)

    # Client passes raw Duffel offer ID
    response = client.post(
        "/api/offers/revalidate",
        headers={"Authorization": f"Bearer {token}"},
        json={"snapshot_id": "off_0000A1b2C3d4"},
    )
    assert response.status_code == 422  # Pydantic validation rejects non-UUID string


def test_pii_and_secrets_are_scrubbed_from_raw_payload():
    """Verify raw payloads are sanitized of sensitive keys before persistence."""
    dirty_payload = {
        "id": "off_123",
        "access_token": "secret_token_abc",
        "api_key": "secret_key_xyz",
        "passengers": [
            {
                "given_name": "John",
                "family_name": "Doe",
                "email": "john.doe@example.com",
                "phone_number": "+919876543210",
                "passport": "P12345678",
            }
        ],
        "slices": [{"origin": "DEL", "destination": "DXB"}],
    }

    clean = _scrub_payload(dirty_payload)
    assert clean["access_token"] == "REDACTED"
    assert clean["api_key"] == "REDACTED"
    pax = clean["passengers"][0]
    assert pax["email"] == "REDACTED"
    assert pax["phone_number"] == "REDACTED"
    assert pax["passport"] == "REDACTED"
    assert clean["slices"][0]["origin"] == "DEL"
