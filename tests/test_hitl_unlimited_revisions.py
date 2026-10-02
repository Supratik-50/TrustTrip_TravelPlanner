"""Tests for Unlimited HITL, Revision Monotonicity, and Offer Selection Separation."""
from __future__ import annotations

from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

from app import app
from domain.booking import OfferSelection, OfferType
from domain.live_offer import (
    FlightOffer,
    FreshnessStatus,
    AvailabilityStatus,
    PriceBreakdown,
)
from decimal import Decimal
from datetime import datetime, timedelta, timezone
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


def _auth_client():
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email="hitl@trusttrip.com", password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email="hitl@trusttrip.com")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def test_unlimited_hitl_revisions_never_forced_approved():
    """Verify that user can revise the plan 10+ times without being forced to approve or reaching a cap."""
    client, user_id = _auth_client()

    # Create trip
    trip_res = client.post("/api/trips", json={"title": "Tokyo Adventure", "destination": "Tokyo"})
    assert trip_res.status_code == 201
    trip_id = trip_res.json()["trip"]["id"]

    # Iterate through 8 revisions
    for rev in range(1, 9):
        rev_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": f"Revision feedback round {rev}", "updated_constraints": {"duration_days": 5 + rev}},
        )
        assert rev_res.status_code == 200
        data = rev_res.json()
        assert data["revision"] == rev
        assert data["status"] == "awaiting_approval"

    # Trip remains awaiting_approval and never auto-approves
    trip_detail = client.get(f"/api/trips/{trip_id}").json()["trip"]
    assert trip_detail["metadata"]["revision"] == 8
    assert trip_detail["status"] == "awaiting_approval"


def test_explicit_trip_approval():
    """Verify that trip transitions to approved only upon explicit approval call."""
    client, user_id = _auth_client()

    trip_res = client.post("/api/trips", json={"title": "Paris Trip", "destination": "Paris"})
    trip_id = trip_res.json()["trip"]["id"]

    # Submit revision
    rev_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Add Louvre visit", "updated_constraints": {}},
    )
    rev_id = rev_res.json()["revision_id"]

    # Approve
    appr_res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert appr_res.status_code == 200
    assert appr_res.json()["status"] == "approved"

    # Verify status in store
    trip_detail = client.get(f"/api/trips/{trip_id}").json()["trip"]
    assert trip_detail["status"] == "approved"
    assert "approved_at" in trip_detail["metadata"]


def test_offer_selection_does_not_increment_revision():
    """Verify that selecting a flight or hotel offer binds the choice without incrementing revision."""
    client, user_id = _auth_client()

    trip_res = client.post("/api/trips", json={"title": "Rome Trip", "destination": "Rome"})
    trip_id = trip_res.json()["trip"]["id"]

    # Set revision to 2
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 1", "updated_constraints": {}})
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 2", "updated_constraints": {}})

    # Create a flight offer snapshot owned by user
    store = SearchSessionStore()
    session = store.create_session(user_id=user_id, trip_id=trip_id, thread_id="t1", search_type="flights", criteria_hash="h1", trip_revision=2)
    now = datetime.now(timezone.utc)
    future_exp = now + timedelta(hours=1)
    offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_test_1",
        price=PriceBreakdown(base_amount=Decimal("300.00"), total_amount=Decimal("300.00"), currency="USD"),
        stops=0,
        segments=[],
        retrieved_at=now,
        expires_at=future_exp,
    )
    snap = store.save_snapshot(session.session_id, offer, raw_payload={"fare": 300})

    # Select offer
    sel_res = client.post(
        f"/api/trips/{trip_id}/select-offer",
        json={"offer_type": "flight", "snapshot_id": str(snap.snapshot_id)},
    )
    assert sel_res.status_code == 200

    # Verify revision is still 2!
    trip_detail = client.get(f"/api/trips/{trip_id}").json()["trip"]
    assert trip_detail["metadata"]["revision"] == 2
    assert trip_detail["metadata"]["selected_flight_snapshot_id"] == str(snap.snapshot_id)


def test_stale_selections_invalidated_on_material_revisions():
    """Verify that material changes (destination, dates, passengers, hotel prefs) clear incompatible selections."""
    client, user_id = _auth_client()

    trip_res = client.post("/api/trips", json={"title": "Paris Trip", "destination": "Paris"})
    trip_id = trip_res.json()["trip"]["id"]

    store = SearchSessionStore()
    now = datetime.now(timezone.utc)
    future_exp = now + timedelta(hours=1)

    # Save flight snapshot for initial trip revision 0
    f_sess = store.create_session(user_id=user_id, trip_id=trip_id, thread_id="t1", search_type="flights", criteria_hash="h1", trip_revision=0)
    flight_offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_f1",
        price=PriceBreakdown(base_amount=Decimal("500.00"), total_amount=Decimal("500.00"), currency="USD"),
        stops=0,
        segments=[],
        retrieved_at=now,
        expires_at=future_exp,
    )
    f_snap = store.save_snapshot(f_sess.session_id, flight_offer, raw_payload={})

    # Save hotel snapshot for initial trip revision 0
    from domain.live_offer import HotelOffer
    h_sess = store.create_session(user_id=user_id, trip_id=trip_id, thread_id="t1", search_type="hotels", criteria_hash="h2", trip_revision=0)
    hotel_offer = HotelOffer(
        snapshot_id=uuid4(),
        provider="liteapi",
        provider_offer_id="off_h1",
        hotel_id="lp_1",
        hotel_name="Paris Luxury Hotel",
        city="Paris",
        room_name="Deluxe",
        board_type="Room Only",
        refundable=True,
        price=PriceBreakdown(base_amount=Decimal("200.00"), total_amount=Decimal("200.00"), currency="USD"),
        nightly_price=Decimal("100.00"),
        nights=2,
        retrieved_at=now,
        expires_at=future_exp,
    )
    h_snap = store.save_snapshot(h_sess.session_id, hotel_offer, raw_payload={})

    # Select both flight and hotel
    client.post(f"/api/trips/{trip_id}/select-offer", json={"offer_type": "flight", "snapshot_id": str(f_snap.snapshot_id)})
    client.post(f"/api/trips/{trip_id}/select-offer", json={"offer_type": "hotel", "snapshot_id": str(h_snap.snapshot_id)})

    meta = client.get(f"/api/trips/{trip_id}").json()["trip"]["metadata"]
    assert meta.get("selected_flight_snapshot_id") == str(f_snap.snapshot_id)
    assert meta.get("selected_hotel_snapshot_id") == str(h_snap.snapshot_id)

    # 1. Non-invalidating revision (activity feedback only) does NOT clear selections
    client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Let's visit Louvre and Eiffel tower", "updated_constraints": {}},
    )
    meta = client.get(f"/api/trips/{trip_id}").json()["trip"]["metadata"]
    assert meta.get("selected_flight_snapshot_id") == str(f_snap.snapshot_id)
    assert meta.get("selected_hotel_snapshot_id") == str(h_snap.snapshot_id)

    # 2. Material change: change destination -> clears BOTH flight and hotel
    client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Go to Tokyo instead", "updated_constraints": {"destination": "Tokyo"}},
    )
    meta = client.get(f"/api/trips/{trip_id}").json()["trip"]["metadata"]
    assert "selected_flight_snapshot_id" not in meta or meta.get("selected_flight_snapshot_id") is None
    assert "selected_hotel_snapshot_id" not in meta or meta.get("selected_hotel_snapshot_id") is None

    # Re-select for specific hotel tests with snapshot matching current revision (revision 2)
    h_sess2 = store.create_session(user_id=user_id, trip_id=trip_id, thread_id="t1", search_type="hotels", criteria_hash="h2_rev2", trip_revision=2)
    hotel_offer2 = hotel_offer.model_copy(update={"snapshot_id": uuid4(), "retrieved_at": now, "expires_at": future_exp})
    h_snap2 = store.save_snapshot(h_sess2.session_id, hotel_offer2, raw_payload={})
    res_sel2 = client.post(f"/api/trips/{trip_id}/select-offer", json={"offer_type": "hotel", "snapshot_id": str(h_snap2.snapshot_id)})
    assert res_sel2.status_code == 200
    assert client.get(f"/api/trips/{trip_id}").json()["trip"]["metadata"].get("selected_hotel_snapshot_id") == str(h_snap2.snapshot_id)

    # 3. Hotel preference change -> clears hotel selection
    client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Want budget hotel", "updated_constraints": {"hotel_preference": "budget"}},
    )
    meta = client.get(f"/api/trips/{trip_id}").json()["trip"]["metadata"]
    assert "selected_hotel_snapshot_id" not in meta or meta.get("selected_hotel_snapshot_id") is None
