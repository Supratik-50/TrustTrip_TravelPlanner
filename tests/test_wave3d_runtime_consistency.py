"""TrustTrip Wave 3D-A: Runtime Booking Consistency Regression Tests.

Validates:
W3D-01: Authenticated Ticket PDF delivery & 401 guard
W3D-02: Flight price and currency metadata persistence
W3D-03: Server-derived approval state, 409 idempotency protection, revision specificity
W3D-04: Booking authorization rehydration & hotel checkout gate protection
W3D-05: Filter invalid/failed flight intents from active booking read
"""
from __future__ import annotations

import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app import app
from services.auth_service import create_access_token
from domain.user import UserResponse
from services.trip_store import TripStore, resolve_current_revision
from services.authorization_store import AuthorizationStore, BookingAuthorization
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService
from services.search_session_store import SearchSessionStore
from domain.live_offer import (
    FlightOffer,
    FlightSegmentModel,
    PriceBreakdown,
    AvailabilityStatus,
    FreshnessStatus,
)


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    from services.auth_store import UserStore
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    BookingReadService.clear_in_memory()
    AuthorizationStore._IN_MEMORY_AUTHORIZATIONS.clear()


def _auth_user(email: str = "w3d_tester@trusttrip.com") -> tuple[TestClient, dict]:
    from services.auth_store import UserStore
    user_store = UserStore()
    user = user_store.get_by_email(email)
    if not user:
        try:
            user = user_store.create_user(email=email, password_hash="hash")
        except ValueError:
            user = user_store.get_by_email(email)
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, {"id": user["id"], "email": email}


def _create_canonical_trip(user_id: str, title: str = "W3D Journey") -> tuple[str, str]:
    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user_id,
        title=title,
        destination="Kyoto, Japan",
        origin="SFO",
        metadata={
            "plan": {"destination": "Kyoto, Japan"},
            "budget": 5000,
            "start_date": "2026-11-01",
            "end_date": "2026-11-07",
        },
    )
    tid = str(trip["id"])
    rev_res = trip_store.create_revision_atomic(
        trip_id=tid,
        user_id=user_id,
        feedback="Initial proposal",
    )
    rev_id = str(rev_res["revision_id"])
    return tid, rev_id


# =========================================================================
# W3D-01: Ticket Download 401 & Authenticated Stream
# =========================================================================

def test_ticket_download_unauthenticated_returns_401():
    """W3D-01: An unauthenticated direct request (e.g. browser <a href>) must return 401."""
    client = TestClient(app)
    fake_id = str(uuid4())
    resp = client.get(f"/api/trips/{fake_id}/ticket.pdf")
    assert resp.status_code == 401, f"Expected 401 Unauthorized, got {resp.status_code}"


def test_ticket_download_authenticated_succeeds_with_persisted_metadata():
    """W3D-01: Authenticated client retrieves completed flight ticket PDF."""
    client, user = _auth_user("ticket_auth_test@trusttrip.com")
    tid, _ = _create_canonical_trip(user["id"], "Ticket Trip")

    flight_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[flight_id] = {
        "id": flight_id,
        "trip_id": tid,
        "user_id": user["id"],
        "status": "COMPLETED",
        "provider_reference": "27LXIN",
        "expected_amount": 314.64,
        "currency": "USD",
        "metadata": {
            "booking_reference": "27LXIN",
            "order_id": "ord_duffel_w3d",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "passengers": [
                {"first_name": "Supratik", "last_name": "Chowdhury", "email": "sup@example.com"}
            ],
            "slices": [
                {
                    "origin": "SFO",
                    "destination": "KIX",
                    "departure_time": "2026-11-01T10:00:00Z",
                    "segments": [
                        {
                            "marketing_carrier": {"name": "Japan Airlines"},
                            "marketing_carrier_flight_number": "JL001",
                            "origin": {"iata_code": "SFO"},
                            "destination": {"iata_code": "KIX"},
                        }
                    ],
                }
            ],
            "documents": [{"type": "electronic_ticket", "unique_identifier": "131-9988776655"}],
        },
    }

    resp = client.get(f"/api/trips/{flight_id}/ticket.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 100


# =========================================================================
# W3D-02: Flight Price & Currency Persistence
# =========================================================================

def test_flight_offer_selection_persists_amount_and_currency():
    """W3D-02: Selecting a flight persists full selected_flight metadata with price and USD currency."""
    client, user = _auth_user("flight_price_persist@trusttrip.com")
    tid, rev_id = _create_canonical_trip(user["id"], "Flight Pricing Trip")

    snap_store = SearchSessionStore()
    sess = snap_store.create_session(
        user_id=user["id"],
        trip_id=tid,
        thread_id="test_thread",
        search_type="flight",
        criteria_hash="hash_w3d_flight",
        trip_revision=1,
    )

    dep_dt = datetime.now(timezone.utc) + timedelta(days=30)
    arr_dt = dep_dt + timedelta(hours=11)

    domain_offer = FlightOffer(
        provider="duffel",
        provider_offer_id="off_w3d_314",
        price=PriceBreakdown(
            base_amount=300.0,
            tax_amount=14.64,
            fee_amount=0.0,
            total_amount=314.64,
            currency="USD",
            inr_reference_amount=26500.0,
        ),
        segments=[
            FlightSegmentModel(
                origin_iata="SFO",
                destination_iata="KIX",
                departure_time=dep_dt,
                arrival_time=arr_dt,
                marketing_carrier_iata="JL",
                marketing_carrier_name="Japan Airlines",
                flight_number="JL001",
                duration_minutes=660,
                cabin_class="economy",
            )
        ],
        retrieved_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )

    snapshot = snap_store.save_snapshot(
        sess.session_id,
        domain_offer,
        raw_payload={"provider": "duffel", "id": "off_w3d_314"},
    )

    trip_store = TripStore()
    sel_req = MagicMock()
    sel_req.snapshot_id = snapshot.snapshot_id
    sel_req.offer_type = "flight"

    updated = trip_store.select_offer_atomic(tid, user["id"], sel_req, snap_store)
    meta = updated["metadata"]
    assert "selected_flight" in meta, "selected_flight must be persisted in trip metadata"
    sf = meta["selected_flight"]
    assert sf["price"] == 314.64
    assert sf["total_amount"] == "314.64"
    assert sf["currency"] == "USD"
    assert sf["carrier_name"] == "Japan Airlines"
    assert len(sf["slices"]) == 1

    # Verify rehydration via GET /api/trips/{trip_id}
    get_resp = client.get(f"/api/trips/{tid}")
    assert get_resp.status_code == 200
    trip_data = get_resp.json()["trip"]
    assert "selected_flight_offer" in trip_data
    assert trip_data["selected_flight_offer"]["currency"] == "USD"
    assert trip_data["selected_flight_offer"]["total_amount"] == "314.64"


# =========================================================================
# W3D-03: Server-Derived Approval State & 409 Conflict Protection
# =========================================================================

def test_trip_approval_409_conflict_on_duplicate_approval():
    """W3D-03: Duplicate approval of an already-approved revision returns 409 Conflict."""
    client, user = _auth_user("reapproval_test@trusttrip.com")
    tid, rev_id = _create_canonical_trip(user["id"], "Approval Trip")

    # First approval should succeed
    resp1 = client.post(
        f"/api/trips/{tid}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1, "notes": "First approval"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "approved"

    # Second approval of the SAME revision must return 409 Conflict
    resp2 = client.post(
        f"/api/trips/{tid}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1, "notes": "Duplicate approval"},
    )
    assert resp2.status_code == 409
    assert "already approved" in resp2.json()["detail"].lower()


# =========================================================================
# W3D-04: Booking Authorization Rehydration & Hotel Gate Protection
# =========================================================================

def test_trip_read_exposes_active_booking_authorization():
    """W3D-04: GET /api/trips/{trip_id} exposes active booking authorization for current revision."""
    client, user = _auth_user("auth_rehydrate@trusttrip.com")
    tid, rev_id = _create_canonical_trip(user["id"], "Authorized Trip")

    auth_store = AuthorizationStore()
    auth_id = str(uuid4())
    auth_obj = BookingAuthorization(
        authorization_id=auth_id,
        trip_id=tid,
        revision_id=rev_id,
        user_id=user["id"],
        revision_number=1,
        authorized_at=datetime.now(timezone.utc),
        authorization_metadata={"notes": "Authorized by traveler"},
    )
    auth_store._IN_MEMORY_AUTHORIZATIONS[auth_id] = auth_obj

    resp = client.get(f"/api/trips/{tid}")
    assert resp.status_code == 200
    trip_data = resp.json()["trip"]
    assert trip_data.get("is_booking_authorized") is True
    assert "booking_authorization" in trip_data
    b_auth = trip_data["booking_authorization"]
    assert b_auth["authorization_id"] == auth_id
    assert b_auth["revision_id"] == rev_id
    assert b_auth["revision_number"] == 1


def test_stale_revision_does_not_expose_old_authorization():
    """W3D-04: Authorization for revision 1 must NOT attach when trip is on revision 2."""
    client, user = _auth_user("stale_auth_test@trusttrip.com")
    tid, rev_id_1 = _create_canonical_trip(user["id"], "Stale Auth Trip")

    auth_store = AuthorizationStore()
    auth_id = str(uuid4())
    auth_obj = BookingAuthorization(
        authorization_id=auth_id,
        trip_id=tid,
        revision_id=rev_id_1,
        user_id=user["id"],
        revision_number=1,
        authorized_at=datetime.now(timezone.utc),
        authorization_metadata={},
    )
    auth_store._IN_MEMORY_AUTHORIZATIONS[auth_id] = auth_obj

    # Bump trip to revision 2
    TripStore().create_revision_atomic(
        trip_id=tid,
        user_id=user["id"],
        feedback="Updated plan constraints",
    )

    resp = client.get(f"/api/trips/{tid}")
    assert resp.status_code == 200
    trip_data = resp.json()["trip"]
    # Authorization for rev 1 must NOT be attached to rev 2 trip
    assert trip_data.get("booking_authorization") is None
    assert not trip_data.get("is_booking_authorized")


# =========================================================================
# W3D-05: Filter Invalid Flight Bookings
# =========================================================================

def test_booking_read_service_excludes_failed_flight_intents():
    """W3D-05: list_recent_flights_for_user excludes FAILED intents and returns only completed."""
    client, user = _auth_user("filter_failed_flights@trusttrip.com")
    tid, _ = _create_canonical_trip(user["id"], "Deduplication Trip")

    failed_intent_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[failed_intent_id] = {
        "id": failed_intent_id,
        "trip_id": tid,
        "user_id": user["id"],
        "status": "FAILED",
        "provider_reference": None,
        "expected_amount": 314.64,
        "currency": "USD",
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "metadata": {},
    }

    completed_intent_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[completed_intent_id] = {
        "id": completed_intent_id,
        "trip_id": tid,
        "user_id": user["id"],
        "status": "COMPLETED",
        "provider_reference": "27LXIN",
        "expected_amount": 314.64,
        "currency": "USD",
        "created_at": datetime.now(timezone.utc),
        "metadata": {"booking_reference": "27LXIN"},
    }

    read_service = BookingReadService()
    flights = read_service.list_recent_flights_for_user(user["id"])

    # Failed intent must be excluded
    returned_ids = [f["id"] for f in flights]
    assert failed_intent_id not in returned_ids, "Failed flight intent must be excluded"
    assert completed_intent_id in returned_ids, "Completed flight intent must be present"

    # Verify GET /api/trips attached flights
    trips_resp = client.get("/api/trips")
    assert trips_resp.status_code == 200
    my_trips = trips_resp.json()["trips"]
    matching_trip = next((t for t in my_trips if t["id"] == tid), None)
    assert matching_trip is not None
    assert len(matching_trip["flights"]) == 1
    assert matching_trip["flights"][0]["booking_reference"] == "27LXIN"
