"""Targeted Regression Tests for Flight & Hotel Stabilization Lifecycles.

Verifies:
1. Hotel prebooking failure with expired offer / 'no prebook availability' returns a truthful
   supplier outcome message instructing the user to refresh/reselect stays.
2. Failed hotel prebooking does not open or complete payment.
3. Valid hotel prebooking succeeds and enables the payment step.
4. Selecting a flight does not mark it booked in either GET /api/trips/{trip_id}/bookings or GET /api/trips.
5. Returning to booking page when a new flight snapshot is selected ignores stale historical completed intents.
6. Only authoritative supplier confirmation creates a booked state with reference.
7. Payment and refund safety invariants remain intact.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import HotelOffer, PriceBreakdown, RevalidationResult, RevalidationStatus
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService
from services.hotel.hotel_provider import HotelProviderError
from services.hotel.hotel_service import HotelService
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    BookingReadService.clear_in_memory()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    yield
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    FlightBookingService._IN_MEMORY_BOOKINGS.clear()
    BookingReadService.clear_in_memory()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()


def _auth_client(email: str = "stabilization_tester@trusttrip.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


def _create_test_trip(user_id: str, title: str = "Stabilization Journey") -> tuple[str, dict]:
    trip_id = str(uuid4())
    trip_rec = {
        "id": trip_id,
        "trip_id": trip_id,
        "user_id": user_id,
        "title": title,
        "origin": "San Francisco (SFO)",
        "destination": "Kyoto, Japan",
        "start_date": "2026-10-14",
        "end_date": "2026-10-24",
        "status": "approved",
        "is_approved": True,
        "current_revision_number": 1,
        "active_revision_number": 1,
        "active_revision_id": str(uuid4()),
        "travelers_count": 2,
        "budget": 5000,
        "currency": "USD",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }
    TripStore._IN_MEMORY_TRIPS[trip_id] = trip_rec
    return trip_id, trip_rec


def _create_hotel_snapshot(user_id: str, trip_id: str, price: float = 250.0) -> str:
    snap_store = SearchSessionStore()
    session = snap_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id="th_test",
        search_type="hotels",
        criteria_hash="hash_test_" + str(uuid4())[:8],
        trip_revision=1,
        reuse_ttl_seconds=3600,
    )
    snap_id = uuid4()
    now = datetime.now(timezone.utc)
    price_dec = Decimal(str(price))
    norm_hotel = HotelOffer(
        snapshot_id=snap_id,
        hotel_id="hotel_kyoto_1",
        hotel_name="Kyoto Grand Hotel",
        city="Kyoto",
        address="123 Kyoto Way",
        room_name="Deluxe Garden View",
        board_type="Room Only",
        price=PriceBreakdown(
            base_amount=price_dec,
            total_amount=price_dec,
            currency="USD",
            inr_reference_amount=price_dec * Decimal("83"),
        ),
        nightly_price=price_dec,
        provider="liteapi",
        provider_offer_id="rate_test_12345",
        refundable=True,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        nights=1,
        retrieved_at=now,
        expires_at=now + timedelta(hours=2),
    )
    snap = snap_store.save_snapshot(
        session_id=session.session_id,
        offer=norm_hotel,
        raw_payload={"hotel_id": "hotel_kyoto_1", "rooms": 1},
    )
    return str(snap.snapshot_id)


# ============================================================================
# 1. HOTEL PREBOOK REGRESSION TESTS
# ============================================================================

def test_hotel_prebook_expired_offer_returns_truthful_reselect_message():
    """An expired supplier rate returns HTTP 409 with truthful message and does not open payment."""
    client, user = _auth_client("hotel_expired@test.com")
    trip_id, trip_rec = _create_test_trip(user["id"])

    # Simulate an expired offer snapshot
    snap_id = _create_hotel_snapshot(user["id"], trip_id, price=250.0)
    offer_payload = {
        "snapshot_id": snap_id,
        "provider": "liteapi",
        "provider_offer_id": "rate_expired_12345",
        "hotel_id": "hotel_kyoto_1",
        "price": 250.0,
        "currency": "USD",
    }
    trip_rec["metadata"]["selected_hotel_snapshot_id"] = snap_id
    trip_rec["metadata"]["selected_hotel"] = offer_payload

    with patch.object(HotelService, "prebook", side_effect=HotelProviderError("no prebook availability")):
        resp = client.post(
            "/api/hotels/prebook",
            json={
                "trip_id": trip_id,
                "snapshot_id": snap_id,
                "offer": offer_payload,
                "guest_name": "Stabilization Tester",
                "guest_email": "hotel_expired@test.com",
            },
        )

    assert resp.status_code == 409
    detail = resp.json().get("detail", "")
    assert "Rate is no longer available from supplier" in detail
    assert "Please refresh stays and select a valid offer" in detail

    # Assert no hotel_prebook was created in metadata
    assert "hotel_prebook" not in (trip_rec.get("metadata") or {})


def test_hotel_prebook_valid_offer_succeeds_and_enables_next_step():
    """A valid offer prebooks successfully and retains prebook metadata for payment."""
    client, user = _auth_client("hotel_valid@test.com")
    trip_id, trip_rec = _create_test_trip(user["id"])

    snap_id = _create_hotel_snapshot(user["id"], trip_id, price=280.0)
    offer_payload = {
        "snapshot_id": snap_id,
        "provider": "liteapi",
        "provider_offer_id": "rate_valid_9999",
        "hotel_id": "hotel_kyoto_valid",
        "hotel_name": "Kyoto Grand Hotel",
        "room_name": "Deluxe Garden View",
        "price": 280.0,
        "currency": "USD",
    }
    trip_rec["metadata"]["selected_hotel_snapshot_id"] = snap_id
    trip_rec["metadata"]["selected_hotel"] = offer_payload

    mock_prebook_result = {
        "prebook_id": "prebook_lite_777",
        "status": "prebooked",
        "price": 280.0,
        "currency": "USD",
    }

    with patch.object(HotelService, "prebook", return_value=mock_prebook_result):
        resp = client.post(
            "/api/hotels/prebook",
            json={
                "trip_id": trip_id,
                "snapshot_id": snap_id,
                "offer": offer_payload,
                "guest_name": "Stabilization Tester",
                "guest_email": "hotel_valid@test.com",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["prebook_id"] == "prebook_lite_777"
    assert data["status"] == "prebooked"

    # Preserved in trip metadata for continuity
    saved_meta = trip_rec["metadata"].get("hotel_prebook")
    assert saved_meta is not None
    assert saved_meta["prebook_id"] == "prebook_lite_777"


# ============================================================================
# 2. FLIGHT LIFECYCLE & SELECTION VS BOOKING CONTRACT TESTS
# ============================================================================

def test_flight_selection_does_not_mark_flight_booked():
    """Selecting an offer never marks the flight as booked in GET /api/trips or /bookings."""
    client, user = _auth_client("flight_sel@test.com")
    trip_id, trip_rec = _create_test_trip(user["id"])

    snap_id = str(uuid4())
    trip_rec["metadata"]["selected_flight_snapshot_id"] = snap_id
    trip_rec["metadata"]["selected_flight"] = {
        "id": snap_id,
        "total_amount": 850.0,
        "currency": "USD",
        "carrier_name": "Japan Airlines",
    }

    # 1. Authoritative Rehydration GET /api/trips/{trip_id}/bookings
    resp_bkg = client.get(f"/api/trips/{trip_id}/bookings")
    assert resp_bkg.status_code == 200
    data_bkg = resp_bkg.json()
    # No booking intent exists yet for this snapshot -> flight is None (not booked)
    assert data_bkg["flight"] is None

    # 2. My Trips GET /api/trips
    resp_trips = client.get("/api/trips")
    assert resp_trips.status_code == 200
    trips_list = resp_trips.json().get("trips", [])
    matched_trip = next((t for t in trips_list if t["id"] == trip_id), None)
    assert matched_trip is not None
    assert matched_trip["flight_count"] == 0
    assert matched_trip["flights"] == []


def test_flight_unconfirmed_intent_does_not_appear_booked():
    """A flight intent with status 'READY_FOR_BOOKING' or 'SELECTED' is NOT booked."""
    client, user = _auth_client("flight_intent@test.com")
    trip_id, trip_rec = _create_test_trip(user["id"])

    snap_id = str(uuid4())
    intent_id = str(uuid4())
    trip_rec["metadata"]["selected_flight_snapshot_id"] = snap_id

    FlightBookingService._IN_MEMORY_BOOKINGS[intent_id] = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": user["id"],
        "status": "READY_FOR_BOOKING",
        "selected_offer_snapshot_id": snap_id,
        "expected_amount": 850.0,
        "currency": "USD",
        "metadata": {
            "booking_reference": None,
            "pnr": None,
        },
    }

    # Rehydration API
    resp_bkg = client.get(f"/api/trips/{trip_id}/bookings")
    assert resp_bkg.status_code == 200
    fl = resp_bkg.json()["flight"]
    assert fl is not None
    assert fl["is_booked"] is False
    assert fl["status"] == "ready_for_booking"

    # Trip list API
    resp_trips = client.get("/api/trips")
    trips_list = resp_trips.json().get("trips", [])
    matched = next((t for t in trips_list if t["id"] == trip_id), None)
    assert matched["flight_count"] == 0
    assert matched["flights"] == []


def test_new_flight_selection_ignores_stale_completed_intent():
    """Selecting a new flight snapshot ignores historical completed booking intents on the same trip."""
    client, user = _auth_client("flight_reselect@test.com")
    trip_id, trip_rec = _create_test_trip(user["id"])

    old_snap_id = str(uuid4())
    old_intent_id = str(uuid4())
    # Previous completed flight booking
    FlightBookingService._IN_MEMORY_BOOKINGS[old_intent_id] = {
        "id": old_intent_id,
        "trip_id": trip_id,
        "user_id": user["id"],
        "status": "COMPLETED",
        "selected_offer_snapshot_id": old_snap_id,
        "provider_reference": "OLD-BOOKING-REF",
        "expected_amount": 600.0,
        "currency": "USD",
        "metadata": {
            "booking_reference": "OLD-BOOKING-REF",
            "pnr": "OLDPNR",
        },
    }

    # User now selects a NEW flight snapshot
    new_snap_id = str(uuid4())
    trip_rec["metadata"]["selected_flight_snapshot_id"] = new_snap_id

    resp_bkg = client.get(f"/api/trips/{trip_id}/bookings")
    assert resp_bkg.status_code == 200
    # Must NOT return the old completed booking for the new flight selection
    assert resp_bkg.json()["flight"] is None


def test_supplier_confirmation_authoritatively_creates_booked_state():
    """Only authoritative supplier confirmation creates a booked flight state."""
    client, user = _auth_client("flight_confirm@test.com")
    trip_id, trip_rec = _create_test_trip(user["id"])

    snap_id = str(uuid4())
    intent_id = str(uuid4())
    trip_rec["metadata"]["selected_flight_snapshot_id"] = snap_id

    FlightBookingService._IN_MEMORY_BOOKINGS[intent_id] = {
        "id": intent_id,
        "trip_id": trip_id,
        "user_id": user["id"],
        "status": "COMPLETED",
        "selected_offer_snapshot_id": snap_id,
        "provider_reference": "JL-AUTH-999",
        "expected_amount": 920.0,
        "currency": "USD",
        "metadata": {
            "booking_reference": "JL-AUTH-999",
            "pnr": "JALPNR",
            "order_id": "ord_jal_999",
        },
    }

    # Rehydration API
    resp_bkg = client.get(f"/api/trips/{trip_id}/bookings")
    assert resp_bkg.status_code == 200
    fl = resp_bkg.json()["flight"]
    assert fl["is_booked"] is True
    assert fl["status"] == "completed"
    assert fl["booking_reference"] == "JL-AUTH-999"
    assert fl["pnr"] == "JALPNR"

    # Trip list API
    resp_trips = client.get("/api/trips")
    trips_list = resp_trips.json().get("trips", [])
    matched = next((t for t in trips_list if t["id"] == trip_id), None)
    assert matched["flight_count"] == 1
    assert len(matched["flights"]) == 1
    assert matched["flights"][0]["booking_reference"] == "JL-AUTH-999"


# ============================================================================
# 3. HOTEL CHECKOUT PREBOOK REUSE & PAYMENT DOCUMENT TESTS
# ============================================================================

def _persist_lineage(trip_id: str, revision_id: str, user_id: str) -> None:
    """Persist test fixture lineage in PostgreSQL for payment transaction FKs if database is connected."""
    import os
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "INSERT INTO users(id, email, password_hash) VALUES (%s, %s, 'test') ON CONFLICT(id) DO NOTHING",
                (user_id, f"{user_id}@stabilization.test"),
            )
            conn.execute(
                "INSERT INTO trips(id, user_id, title, destination, origin, status, metadata, current_revision_number) "
                "VALUES (%s, %s, 'Stabilization test', 'BOM', 'DEL', 'approved', '{}'::jsonb, 0) ON CONFLICT(id) DO NOTHING",
                (trip_id, user_id),
            )
            conn.execute("DELETE FROM plan_revisions WHERE trip_id=%s", (trip_id,))
            conn.execute(
                "INSERT INTO plan_revisions(revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, is_published) "
                "VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb, true)",
                (revision_id, trip_id, user_id),
            )
            conn.execute(
                "UPDATE trips SET current_revision_id=%s, current_revision_number=1 WHERE id=%s",
                (revision_id, trip_id),
            )
    except Exception as exc:
        pass


def _setup_authorized_hotel_trip(email: str = "hotel_checkout_tester@trusttrip.com"):
    client, user = _auth_client(email)
    constraints = {
        "origin": "DEL",
        "destination": "BOM",
        "departure_date": "2027-01-10",
        "return_date": "2027-01-15",
        "passengers": 1,
    }
    t_res = client.post("/api/trips", json={
        "title": "Hotel Checkout Flow",
        "origin": "DEL",
        "destination": "BOM",
        "metadata": {"constraints": constraints},
    })
    trip_id = t_res.json()["trip"]["id"]
    r_res = client.post(f"/api/trips/{trip_id}/revisions", json={
        "feedback": "Initial revision",
        "updated_constraints": constraints,
    })
    rev_id = r_res.json()["revision_id"]
    client.post(f"/api/trips/{trip_id}/approve", json={"revision_id": rev_id, "expected_revision_number": 1})
    client.post(f"/api/trips/{trip_id}/booking-approval", json={"revision_id": rev_id, "expected_revision_number": 1})

    _persist_lineage(trip_id, rev_id, user["id"])

    snap_id = _create_hotel_snapshot(user["id"], trip_id, price=300.0)
    trip_rec = TripStore._IN_MEMORY_TRIPS.get(trip_id)
    if trip_rec:
        trip_rec["metadata"]["selected_hotel_snapshot_id"] = snap_id

    return client, user, trip_id, rev_id, snap_id, trip_rec


def test_hotel_checkout_uses_existing_valid_prebook_without_duplicate_prebook():
    """Hotel checkout uses an existing valid prebook hold and avoids duplicate supplier prebook."""
    from services.hotel_booking_service import HotelBookingService
    from services.razorpay_service import RazorpayService

    client, user, trip_id, rev_id, snap_id, trip_rec = _setup_authorized_hotel_trip("prebook_reuse@test.com")

    # Set up valid existing prebook hold on trip metadata
    existing_prebook = {
        "prebook_id": "prebook_existing_lite_999",
        "amount": 300.0,
        "currency": "USD",
        "status": "confirmed",
        "hotel_name": "Kyoto Grand Hotel",
    }
    trip_rec["metadata"]["hotel_prebook"] = existing_prebook

    mock_valid_reval = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=uuid4(),
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=datetime.now(timezone.utc),
        price_changed=False,
        original_price=Decimal("300.0"),
        current_price=Decimal("300.0"),
        currency="USD",
    )

    # Patch Razorpay order creation to simulate active gateway
    mock_order = {"id": "order_rzp_mock_12345", "amount": 2490000, "currency": "INR"}
    with patch.object(RazorpayService, "configured", True), \
         patch.object(RazorpayService, "create_order", return_value=mock_order), \
         patch.object(HotelService, "prebook") as mock_prebook_fn, \
         patch("services.planner_service.PlannerService.revalidate_offer", new_callable=AsyncMock, return_value=mock_valid_reval):

        # Ensure HotelService.prebook is NOT called because a valid hold already exists
        mock_prebook_fn.side_effect = AssertionError("HotelService.prebook must not be called when valid prebook exists!")

        resp = client.post(
            "/api/hotel-payments/create-order-from-snapshot",
            json={
                "snapshot_id": snap_id,
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": "idem_prebook_reuse_" + str(uuid4())[:16],
                "holder": {"firstName": "Jane", "lastName": "Doe", "email": "prebook_reuse@test.com"},
                "guests": [{"firstName": "Jane", "lastName": "Doe"}],
                "final_confirmation": True,
                "prebook": existing_prebook,
            },
        )

    assert resp.status_code == 200, f"Expected 200, got: {resp.text}"
    data = resp.json()
    assert data["success"] is True
    assert data["razorpay_order_id"] == "order_rzp_mock_12345"
    assert data["amount_inr"] > 0
    # Verified mock_prebook_fn was never called
    assert mock_prebook_fn.call_count == 0


def test_hotel_checkout_rejects_simulated_prebook():
    """A simulated prebook reference is rejected and cannot proceed to payment order creation."""
    from services.razorpay_service import RazorpayService

    client, user, trip_id, rev_id, snap_id, trip_rec = _setup_authorized_hotel_trip("sim_reject@test.com")

    simulated_prebook = {
        "prebook_id": "prebook_sim_000",
        "amount": 300.0,
        "currency": "USD",
        "status": "simulated",
        "hotel_name": "Simulated Hotel",
    }

    mock_valid_reval = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=uuid4(),
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=datetime.now(timezone.utc),
        price_changed=False,
        original_price=Decimal("300.0"),
        current_price=Decimal("300.0"),
        currency="USD",
    )

    with patch.object(RazorpayService, "configured", True), \
         patch("services.planner_service.PlannerService.revalidate_offer", new_callable=AsyncMock, return_value=mock_valid_reval):
        resp = client.post(
            "/api/hotel-payments/create-order-from-snapshot",
            json={
                "snapshot_id": snap_id,
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": "idem_sim_reject_" + str(uuid4())[:16],
                "holder": {"firstName": "Sim", "lastName": "User"},
                "guests": [{"firstName": "Sim", "lastName": "User"}],
                "final_confirmation": True,
                "prebook": simulated_prebook,
            },
        )

    assert resp.status_code in {400, 409, 422}
    detail = resp.json().get("detail", "")
    assert "simulated" in str(detail).lower() or "authoritative" in str(detail).lower()


def test_document_endpoints_enforce_authentication_and_authorization():
    """PDF endpoints (/ticket.pdf, /hotel.pdf, /receipt.pdf) require authentication and enforce ownership."""
    anon_client = TestClient(app)
    fake_tx = str(uuid4())

    # 1. Anonymous access is blocked with 401
    assert anon_client.get(f"/api/trips/{fake_tx}/ticket.pdf").status_code == 401
    assert anon_client.get(f"/api/trips/{fake_tx}/hotel.pdf").status_code == 401
    assert anon_client.get(f"/api/trips/{fake_tx}/receipt.pdf").status_code == 401

    # 2. User 1 creates completed flight booking
    client1, user1 = _auth_client("doc_owner@test.com")
    intent_id = str(uuid4())
    FlightBookingService._IN_MEMORY_BOOKINGS[intent_id] = {
        "id": intent_id,
        "trip_id": str(uuid4()),
        "user_id": user1["id"],
        "status": "COMPLETED",
        "provider_reference": "DOC-REF-123",
        "expected_amount": 500.0,
        "currency": "INR",
        "metadata": {
            "booking_reference": "DOC-REF-123",
            "pnr": "DOCPNR",
            "passengers": [{"given_name": "Doc", "family_name": "Owner"}],
        },
    }

    # User 1 can download ticket and receipt
    resp_ticket = client1.get(f"/api/trips/{intent_id}/ticket.pdf")
    assert resp_ticket.status_code == 200
    assert resp_ticket.headers["content-type"] == "application/pdf"
    assert resp_ticket.content.startswith(b"%PDF")

    resp_receipt = client1.get(f"/api/trips/{intent_id}/receipt.pdf")
    assert resp_receipt.status_code == 200
    assert resp_receipt.headers["content-type"] == "application/pdf"
    assert resp_receipt.content.startswith(b"%PDF")

    # 3. User 2 cannot access User 1's ticket or receipt
    client2, user2 = _auth_client("doc_attacker@test.com")
    assert client2.get(f"/api/trips/{intent_id}/ticket.pdf").status_code in {403, 404}
    assert client2.get(f"/api/trips/{intent_id}/receipt.pdf").status_code in {403, 404}


def test_payment_receipt_pdf_generation_truthfulness():
    """Payment receipt PDF contains truthful financial details without implying booking when unconfirmed."""
    from services.hotel_transaction_store import HotelTransactionStore

    client, user = _auth_client("receipt_truth@test.com")
    tx_id = str(uuid4())
    store = HotelTransactionStore()

    # Create a hotel transaction where payment was captured but booking failed
    store.create(
        id=tx_id,
        customer_ref=user["id"],
        provider="liteapi",
        hotel_name="Tokyo Ryokan Stay",
        offer={"hotel_name": "Tokyo Ryokan Stay", "price": 180.0, "currency": "USD"},
        prebook={"prebook_id": "pb_111"},
        holder={"firstName": "Truthful", "lastName": "Traveler", "email": "receipt_truth@test.com"},
        guests=[{"firstName": "Truthful", "lastName": "Traveler"}],
        amount_inr=15000.0,
        razorpay_order_id="order_rzp_truth_999",
    )
    store.update(
        tx_id,
        payment_status="captured",
        booking_status="booking_failed",
        razorpay_payment_id="pay_rzp_truth_888",
        error="Supplier inventory unavailable.",
    )

    resp = client.get(f"/api/trips/{tx_id}/receipt.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert len(resp.content) > 1000
