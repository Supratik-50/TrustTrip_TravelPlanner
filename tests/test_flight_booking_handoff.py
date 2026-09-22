"""Tests for Flight Selection, Handoff, 409 Successor Revalidation, and Destination Resolution."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.booking import FlightDestinationType
from domain.live_offer import (
    FlightOffer,
    FlightSegmentModel,
    PriceBreakdown,
    RevalidationResult,
    RevalidationStatus,
)
from services.auth_store import UserStore
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_REVALIDATIONS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()


def _auth_user(email: str):
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def _create_flight_snapshot(user_id, raw_payload=None):
    store = SearchSessionStore()
    trip_id = uuid4()
    session = store.create_session(user_id=user_id, trip_id=trip_id, thread_id="t1", search_type="flights", criteria_hash="h1")
    now = datetime.now(timezone.utc)
    segment = FlightSegmentModel(
        origin_iata="DEL",
        destination_iata="DXB",
        departure_time=now,
        arrival_time=now,
        marketing_carrier_iata="EK",
        marketing_carrier_name="Emirates",
        flight_number="EK511",
        duration_minutes=240,
        cabin_class="economy",
    )
    offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_emirates_1",
        price=PriceBreakdown(base_amount=Decimal("450.00"), total_amount=Decimal("450.00"), currency="USD"),
        stops=0,
        segments=[segment],
        retrieved_at=now,
        expires_at=now,
    )
    snapshot = store.save_snapshot(session.session_id, offer, raw_payload=raw_payload or {})
    return snapshot


def test_flight_handoff_duffel_link():
    """Verify that a Duffel Link payload resolves to DUFFEL_LINK destination type."""
    client, user_id = _auth_user("duffel_link@trusttrip.com")
    raw = {"duffel_link": "https://links.duffel.com/pay/ses_12345"}
    snapshot = _create_flight_snapshot(user_id, raw)

    # Mock revalidate unchanged
    mock_rev = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=snapshot.snapshot_id,
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=datetime.now(timezone.utc),
        price_changed=False,
        original_price=Decimal("450.00"),
        current_price=Decimal("450.00"),
        currency="USD",
    )
    with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev
        res = client.post("/api/flights/select-booking", json={"snapshot_id": str(snapshot.snapshot_id)})

    assert res.status_code == 200
    data = res.json()
    assert data["destination_type"] == FlightDestinationType.DUFFEL_LINK
    assert data["redirect_url"] == "https://links.duffel.com/pay/ses_12345"
    assert "Duffel Travel Checkout" in data["provider_label"]


def test_flight_handoff_direct_airline():
    """Verify that a verified direct airline checkout URL resolves to DIRECT_AIRLINE_BOOKING."""
    client, user_id = _auth_user("airline_direct@trusttrip.com")
    raw = {"direct_airline_booking_url": "https://www.emirates.com/checkout?ref=abc"}
    snapshot = _create_flight_snapshot(user_id, raw)

    mock_rev = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=snapshot.snapshot_id,
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=datetime.now(timezone.utc),
        price_changed=False,
        original_price=Decimal("450.00"),
        current_price=Decimal("450.00"),
        currency="USD",
    )
    with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev
        res = client.post("/api/flights/select-booking", json={"snapshot_id": str(snapshot.snapshot_id)})

    assert res.status_code == 200
    data = res.json()
    assert data["destination_type"] == FlightDestinationType.DIRECT_AIRLINE_BOOKING
    assert data["redirect_url"] == "https://www.emirates.com/checkout?ref=abc"
    assert "Emirates" in data["provider_label"]


def test_flight_handoff_unavailable_zero_fabrication():
    """Verify fallback to UNAVAILABLE when no legitimate URL exists (never fabricates URLs)."""
    client, user_id = _auth_user("unavailable@trusttrip.com")
    snapshot = _create_flight_snapshot(user_id, raw_payload={})

    mock_rev = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=snapshot.snapshot_id,
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=datetime.now(timezone.utc),
        price_changed=False,
        original_price=Decimal("450.00"),
        current_price=Decimal("450.00"),
        currency="USD",
    )
    with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev
        res = client.post("/api/flights/select-booking", json={"snapshot_id": str(snapshot.snapshot_id)})

    assert res.status_code == 200
    data = res.json()
    assert data["destination_type"] == FlightDestinationType.UNAVAILABLE
    assert data["redirect_url"] is None
    assert "Manual Booking Required" in data["provider_label"]


def test_flight_handoff_rejects_cross_tenant_access():
    """Verify that User B cannot handoff User A's flight snapshot."""
    client_a, user_a = _auth_user("user_a@trusttrip.com")
    client_b, user_b = _auth_user("user_b@trusttrip.com")

    snapshot_a = _create_flight_snapshot(user_a)

    res = client_b.post("/api/flights/select-booking", json={"snapshot_id": str(snapshot_a.snapshot_id)})
    assert res.status_code == 403


def test_flight_handoff_409_successor_flow():
    """Verify that when S1 changes price, 409 is returned with S2, and retrying S2 revalidates and succeeds."""
    client, user_id = _auth_user("reval_409@trusttrip.com")
    snapshot_s1 = _create_flight_snapshot(user_id, raw_payload={"fare": 450})

    now = datetime.now(timezone.utc)
    # S1 revalidation returns PRICE_CHANGED with S2
    mock_rev_s1 = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=snapshot_s1.snapshot_id,
        status=RevalidationStatus.PRICE_CHANGED,
        verified_at=now,
        price_changed=True,
        original_price=Decimal("450.00"),
        current_price=Decimal("490.00"),
        currency="USD",
        details="Fare increased by 40 USD",
    )

    with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev_s1
        res_s1 = client.post("/api/flights/select-booking", json={"snapshot_id": str(snapshot_s1.snapshot_id)})

    # Must return 409 with subsequent_snapshot_id
    assert res_s1.status_code == 409
    data_s1 = res_s1.json()["detail"]
    assert data_s1["error"] == "Fare has changed"
    assert data_s1["original_snapshot_id"] == str(snapshot_s1.snapshot_id)
    s2_id = data_s1["subsequent_snapshot_id"]
    assert s2_id != str(snapshot_s1.snapshot_id)

    # User accepts and retries with S2
    mock_rev_s2 = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=s2_id,
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=now,
        price_changed=False,
        original_price=Decimal("490.00"),
        current_price=Decimal("490.00"),
        currency="USD",
    )
    with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev_s2
        res_s2 = client.post("/api/flights/select-booking", json={"snapshot_id": s2_id})

    assert res_s2.status_code == 200
    assert res_s2.json()["snapshot_id"] == s2_id


def test_flight_handoff_hostile_urls_rejected():
    """Verify adversarial hostile URLs are rejected and fallback to UNAVAILABLE."""
    client, user_id = _auth_user("adversarial@trusttrip.com")
    hostile_cases = [
        {"duffel_link": "https://duffel.com.attacker.com/checkout"},
        {"duffel_link": "https://evil-duffel.com/checkout"},
        {"airline_checkout_url": "https://evil.com/phish"},
        {"airline_checkout_url": "https://user:pass@emirates.com/checkout"},
        {"airline_checkout_url": "https://emirates.com.evil.com/checkout"},
        {"airline_checkout_url": "javascript:alert(1)"},
        {"airline_checkout_url": "data:text/html,<script>alert(1)</script>"},
        {"airline_checkout_url": "http://emirates.com/checkout"},
        {"airline_checkout_url": "https://localhost/checkout"},
        {"airline_checkout_url": "https://127.0.0.1/checkout"},
        {"airline_checkout_url": "https://[::1]/checkout"},
        {"airline_checkout_url": "https://[fe80::1]/checkout"},
    ]
    for raw in hostile_cases:
        snapshot = _create_flight_snapshot(user_id, raw)
        mock_rev = RevalidationResult(
            revalidation_id=uuid4(),
            original_snapshot_id=snapshot.snapshot_id,
            status=RevalidationStatus.CONFIRMED_UNCHANGED,
            verified_at=datetime.now(timezone.utc),
            price_changed=False,
            original_price=Decimal("450.00"),
            current_price=Decimal("450.00"),
            currency="USD",
        )
        with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_r:
            mock_r.return_value = mock_rev
            res = client.post("/api/flights/select-booking", json={"snapshot_id": str(snapshot.snapshot_id)})

        assert res.status_code == 200
        data = res.json()
        assert data["destination_type"] == FlightDestinationType.UNAVAILABLE
        assert data["redirect_url"] is None
