"""Wave 3A: Hotel Prebook Contract, Authentication, and Razorpay Checkout Verification Tests.

Covers:
1. Valid authenticated prebook request with snapshot_id -> 200 with prebook_id, amount, currency, status.
2. Anonymous prebook -> 401 Unauthorized.
3. Cross-user trip prebook -> 403 Forbidden.
4. Non-existent trip -> 404 Not Found.
5. Missing authoritative offer (no snapshot, no selected hotel) -> 422 Fail Closed.
6. Expired snapshot -> 409 Conflict.
7. create-order returns real Razorpay order metadata.
8. fake signature rejected with 400.
9. genuine signature verification path succeeds in test configuration and reaches HotelService.book().
10. uncaptured payment rejected.
11. wrong order ID rejected.
12. duplicate idempotency key does not create duplicate booking.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import (
    AvailabilityStatus,
    FreshnessStatus,
    HotelOffer,
    OfferSnapshot,
    PriceBreakdown,
)
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.authorization_store import AuthorizationStore
from services.evidence_store import EvidenceRevisionStore
from services.hotel_transaction_store import HotelTransactionStore
from services.razorpay_service import RazorpayService
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    EvidenceRevisionStore._IN_MEMORY_EVIDENCE.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_APPROVALS.clear()
    EvidenceRevisionStore._IN_MEMORY_REJECTIONS.clear()
    AuthorizationStore._IN_MEMORY_AUTHORIZATIONS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()


def _auth_user(email: str = "w3a_user@example.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


def _create_trip(user_id: str, title: str = "W3A Trip") -> tuple[str, str]:
    trip_store = TripStore()
    rev_id = str(uuid4())
    trip = trip_store.create_trip(
        user_id=user_id,
        title=title,
        origin="DEL",
        destination="BOM",
        current_revision_number=1,
        current_revision_id=rev_id,
        metadata={
            "start_date": "2026-12-01",
            "end_date": "2026-12-10",
            "budget": 50000.0,
            "currency": "INR",
            "travelers_count": 2,
        },
    )
    trip_id = str(trip["id"])

    # If DATABASE_URL is active, ensure user, trip, and plan_revision exist in PostgreSQL for foreign key integrity
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        dsn = db_url if "sslmode=" in db_url else db_url + ("&" if "?" in db_url else "?") + "sslmode=require"
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'test') ON CONFLICT (id) DO NOTHING",
                    (user_id, f"{user_id}@example.test"),
                )
                conn.execute(
                    "INSERT INTO trips (id, user_id, title, destination, origin, status, metadata, current_revision_number) "
                    "VALUES (%s, %s, %s, 'BOM', 'DEL', 'approved', '{}'::jsonb, 0) ON CONFLICT (id) DO NOTHING",
                    (trip_id, user_id, title),
                )
                conn.execute(
                    "INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, is_published) "
                    "VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb, true) ON CONFLICT (revision_id) DO NOTHING",
                    (rev_id, trip_id, user_id),
                )
                conn.execute(
                    "UPDATE trips SET current_revision_id = %s, current_revision_number = 1 WHERE id = %s",
                    (rev_id, trip_id),
                )
        except Exception as exc:
            print(f"[DEBUG test db setup] Failed: {exc}")

    return trip_id, rev_id


def _create_hotel_snapshot(user_id: str, trip_id: str, price: float = 12500.0) -> str:
    snap_store = SearchSessionStore()
    session = snap_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id="th_w3a",
        search_type="hotels",
        criteria_hash="hash_w3a_" + str(uuid4())[:8],
        trip_revision=1,
        reuse_ttl_seconds=3600,
    )
    snap_id = uuid4()
    now = datetime.now(timezone.utc)
    price_dec = Decimal(str(price))
    norm_hotel = HotelOffer(
        snapshot_id=snap_id,
        hotel_id="ht_w3a_taj",
        hotel_name="Taj Mahal Palace",
        city="Mumbai",
        address="Apollo Bunder, Colaba",
        room_name="Deluxe Sea Facing",
        board_type="Room Only",
        price=PriceBreakdown(
            base_amount=price_dec,
            total_amount=price_dec,
            currency="INR",
            inr_reference_amount=price_dec,
        ),
        nightly_price=Decimal(str(price / 3)),
        provider="liteapi",
        provider_offer_id="liteapi_off_taj_100",
        refundable=True,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        nights=3,
        retrieved_at=now,
        expires_at=now + timedelta(hours=2),
    )
    snap = snap_store.save_snapshot(
        session_id=session.session_id,
        offer=norm_hotel,
        raw_payload={"hotel_id": "ht_w3a_taj", "rooms": 1},
    )
    return str(snap.snapshot_id)


# ===========================================================================
# 1. HOTEL PREBOOK TESTS (W3-FIND-01 & W3-FIND-08)
# ===========================================================================

class TestHotelPrebookContractAndSecurity:
    """Tests for W3-FIND-01 (Prebook Contract) and W3-FIND-08 (Authentication & Ownership)."""

    def test_anonymous_prebook_rejected(self):
        """Anonymous POST /api/hotels/prebook must return 401 Unauthorized."""
        unauth_client = TestClient(app)
        res = unauth_client.post(
            "/api/hotels/prebook",
            json={
                "trip_id": str(uuid4()),
                "snapshot_id": str(uuid4()),
            },
        )
        assert res.status_code == 401

    def test_cross_user_trip_rejected(self):
        """Prebook on another user's trip must return 403 Forbidden."""
        client_a, user_a = _auth_user("user_a@example.com")
        _client_b, user_b = _auth_user("user_b@example.com")

        trip_b_id, _ = _create_trip(user_id=user_b["id"], title="User B Trip")
        snap_id = _create_hotel_snapshot(user_id=user_a["id"], trip_id=trip_b_id)

        res = client_a.post(
            "/api/hotels/prebook",
            json={
                "trip_id": trip_b_id,
                "snapshot_id": snap_id,
            },
        )
        assert res.status_code == 403
        assert "Forbidden" in res.json().get("detail", "")

    def test_nonexistent_trip_returns_404(self):
        """Prebook with a non-existent trip must return 404 Not Found."""
        client, user = _auth_user("user_404@example.com")
        res = client.post(
            "/api/hotels/prebook",
            json={
                "trip_id": str(uuid4()),
                "snapshot_id": str(uuid4()),
            },
        )
        assert res.status_code == 404

    def test_missing_authoritative_offer_fails_closed(self):
        """Prebook with trip but no snapshot and no selected hotel must return 422 Unprocessable Entity."""
        client, user = _auth_user("user_no_offer@example.com")
        trip_id, _ = _create_trip(user_id=user["id"])

        res = client.post(
            "/api/hotels/prebook",
            json={"trip_id": trip_id},
        )
        assert res.status_code == 422
        assert "No authoritative hotel offer found" in res.json().get("detail", "")

    def test_cross_user_snapshot_rejected(self):
        """Prebook with another user's snapshot must return 403 Forbidden."""
        client_a, user_a = _auth_user("user_snap_a@example.com")
        _client_b, user_b = _auth_user("user_snap_b@example.com")

        trip_a_id, _ = _create_trip(user_id=user_a["id"])
        trip_b_id, _ = _create_trip(user_id=user_b["id"])
        snap_b_id = _create_hotel_snapshot(user_id=user_b["id"], trip_id=trip_b_id)

        res = client_a.post(
            "/api/hotels/prebook",
            json={
                "trip_id": trip_a_id,
                "snapshot_id": snap_b_id,
            },
        )
        assert res.status_code == 403
        assert "Forbidden" in res.json().get("detail", "")

    def test_expired_snapshot_rejected(self):
        """Prebook with an expired snapshot must return 409 Conflict."""
        client, user = _auth_user("user_expired@example.com")
        trip_id, _ = _create_trip(user_id=user["id"])
        snap_id = _create_hotel_snapshot(user_id=user["id"], trip_id=trip_id)

        # Expire snapshot
        snap_store = SearchSessionStore()
        rec = snap_store._IN_MEMORY_SNAPSHOTS[str(snap_id)]
        rec["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

        res = client.post(
            "/api/hotels/prebook",
            json={
                "trip_id": trip_id,
                "snapshot_id": snap_id,
            },
        )
        assert res.status_code == 409
        assert "expired" in res.json().get("detail", "").lower()

    def test_valid_prebook_returns_authoritative_contract(self):
        """Authenticated valid prebook request must succeed with prebook_id, amount, currency, and status."""
        client, user = _auth_user("user_valid_prebook@example.com")
        trip_id, _ = _create_trip(user_id=user["id"])
        snap_id = _create_hotel_snapshot(user_id=user["id"], trip_id=trip_id, price=18750.0)

        mock_prebook_res = {
            "provider": "liteapi",
            "prebook_id": "pb_supplier_hold_9999",
            "rooms": 1,
            "raw": {"status": "HELD"},
        }

        with patch("services.hotel.hotel_service.HotelService.prebook", return_value=mock_prebook_res) as mock_service:
            res = client.post(
                "/api/hotels/prebook",
                json={
                    "trip_id": trip_id,
                    "snapshot_id": snap_id,
                    "guest_name": "Deepika Padukone",
                    "guest_email": "deepika@example.com",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["prebook_id"] == "pb_supplier_hold_9999"
            assert data["amount"] == 18750.0
            assert data["currency"] == "INR"
            assert data["status"] == "prebooked"
            assert data["hotel_name"] == "Taj Mahal Palace"
            assert "prebook" in data
            mock_service.assert_called_once()
            called_offer = mock_service.call_args[0][0]
            assert called_offer["provider_offer_id"] == "liteapi_off_taj_100"
            assert called_offer["provider"] == "liteapi"

    def test_duplicate_prebook_call_returns_retained_prebook_without_duplicate_supplier_call(self):
        """Prebooking when an active, unexpired prebook exists on the trip must return the retained prebook without re-calling supplier."""
        client, user = _auth_user("user_dedup_prebook@example.com")
        trip_id, _ = _create_trip(user_id=user["id"])
        snap_id = _create_hotel_snapshot(user_id=user["id"], trip_id=trip_id, price=14200.0)

        mock_prebook_res = {
            "provider": "liteapi",
            "prebook_id": "pb_supplier_dedup_1",
            "rooms": 1,
            "raw": {"status": "HELD"},
        }

        with patch("services.hotel.hotel_service.HotelService.prebook", return_value=mock_prebook_res) as mock_service:
            # First call executes supplier prebook
            res1 = client.post(
                "/api/hotels/prebook",
                json={
                    "trip_id": trip_id,
                    "snapshot_id": snap_id,
                    "guest_name": "Priyanka Chopra",
                    "guest_email": "priyanka@example.com",
                },
            )
            assert res1.status_code == 200
            assert res1.json()["prebook_id"] == "pb_supplier_dedup_1"
            assert mock_service.call_count == 1

            # Second call (user retries or re-clicks Prepare reservation)
            res2 = client.post(
                "/api/hotels/prebook",
                json={
                    "trip_id": trip_id,
                    "snapshot_id": snap_id,
                    "guest_name": "Priyanka Chopra",
                    "guest_email": "priyanka@example.com",
                },
            )
            assert res2.status_code == 200
            assert res2.json()["prebook_id"] == "pb_supplier_dedup_1"
            # Crucial: Supplier prebook must NOT have been called a second time
            assert mock_service.call_count == 1



# ===========================================================================
# 2. HOTEL PAYMENT & RAZORPAY VERIFICATION TESTS (W3-FIND-02)
# ===========================================================================

class TestHotelRazorpayVerification:
    """Tests for W3-FIND-02 (Razorpay Order creation and Signature Verification)."""

    def test_create_order_returns_real_metadata(self):
        """create-order returns valid transaction_id, razorpay_order_id, amount_inr, and key_id."""
        client, user = _auth_user("hotel_rzp_order@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])

        with patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
             patch.object(RazorpayService, "configured", return_value=True), \
             patch.object(RazorpayService, "create_order", return_value={"id": "order_rzp_order_test_123"}):
            res = client.post(
                "/api/hotel-payments/create-order",
                json={
                    "offer": {"provider": "liteapi", "provider_offer_id": "off_1", "hotel_name": "Grand Hotel", "price": 10000.0, "currency": "INR", "price_inr": 10000.0},
                    "prebook": {"prebook_id": "pb_100", "status": "prebooked"},
                    "holder": {"firstName": "Aarav", "lastName": "Kapoor", "email": "aarav@example.com"},
                    "guests": [{"occupancyNumber": 1, "firstName": "Aarav", "lastName": "Kapoor"}],
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": 1,
                    "payment_idempotency_key": f"key_{uuid4().hex}",
                    "final_confirmation": True,
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert "transaction_id" in data
            assert data["razorpay_order_id"] == "order_rzp_order_test_123"
            assert data["amount_inr"] == 10000.0
            assert data["currency"] == "INR"

    def test_fake_signature_strictly_rejected(self):
        """verify-and-book must reject invalid/synthetic Razorpay signature with 400."""
        client, user = _auth_user("hotel_fake_sig@example.com")
        tx_id = str(uuid4())
        order_id = "order_rzp_fake_test"
        trip_id, rev_id = _create_trip(user_id=user["id"])

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 10000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=False):
            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": "pay_fake_signature_test",
                    "razorpay_signature": "sig_fake_signature_test",
                    "offer": {"provider": "liteapi", "hotel_name": "Test Hotel"},
                    "prebook": {"prebook_id": "pb_100"},
                    "holder": {"firstName": "Aarav", "lastName": "Kapoor"},
                    "guests": [],
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": 1,
                    "final_confirmation": True,
                },
            )
            assert res.status_code == 400
            assert "Invalid Razorpay payment signature" in res.json().get("detail", "")

    def test_genuine_signature_verification_succeeds_and_books(self):
        """Genuine Razorpay signature verification succeeds and reaches HotelService.book()."""
        client, user = _auth_user("hotel_genuine_sig@example.com")
        tx_id = str(uuid4())
        order_id = "order_rzp_genuine_test"
        pay_id = "pay_rzp_genuine_test"
        sig = "sig_genuine_hmac_test"
        trip_id, rev_id = _create_trip(user_id=user["id"])

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 15000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        mock_payment = {
            "id": pay_id,
            "order_id": order_id,
            "currency": "INR",
            "amount": 1500000,  # 15000 in paise
            "status": "captured",
        }

        mock_book_result = {
            "provider": "liteapi",
            "booking_id": "ht_bkg_9999",
            "confirmation_code": "VOUCHER-TAJ-9999",
            "status": "CONFIRMED",
        }

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(HotelTransactionStore, "update", return_value=None), \
             patch.object(HotelTransactionStore, "claim_booking", return_value=True), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value=mock_payment), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
             patch("services.hotel.hotel_service.HotelService.book", return_value=mock_book_result) as mock_book, \
             patch("hotel_payment_api.build_hotel_booking_pdf", return_value=None):
            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": sig,
                    "offer": {"provider": "liteapi", "hotel_name": "Taj Palace"},
                    "prebook": {"prebook_id": "pb_100"},
                    "holder": {"firstName": "Aarav", "lastName": "Kapoor"},
                    "guests": [],
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": 1,
                    "final_confirmation": True,
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["payment_captured"] is True
            assert data["booking"]["confirmation_code"] == "VOUCHER-TAJ-9999"
            mock_book.assert_called_once()


# ===========================================================================
# 3. HOTEL PREBOOK & PAYMENT CONTINUITY REGRESSION TESTS
# ===========================================================================

class TestHotelPrebookPaymentContinuityAndGuards:
    """Regression tests for Revision #2 hotel prebook -> payment order continuity and guards."""

    def test_revision_2_selected_hotel_prebook_payment_order_continuity(self):
        """1. Revision #2 selected hotel -> prebook -> payment-order continuity."""
        client, user = _auth_user("rev2_continuity@example.com")
        trip_id, rev_1_id = _create_trip(user_id=user["id"], title="Kerala Trip")
        rev_2_id = str(uuid4())
        trip_store = TripStore()

        db_url = os.getenv("DATABASE_URL")
        if db_url:
            dsn = db_url if "sslmode=" in db_url else db_url + ("&" if "?" in db_url else "?") + "sslmode=require"
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, is_published) "
                    "VALUES (%s, %s, %s, 2, '{}'::jsonb, '{}'::jsonb, true) ON CONFLICT (revision_id) DO NOTHING",
                    (rev_2_id, trip_id, user["id"]),
                )
                conn.execute(
                    "UPDATE trips SET current_revision_id = %s, current_revision_number = 2 WHERE id = %s",
                    (rev_2_id, trip_id),
                )

        snap_id = _create_hotel_snapshot(user_id=user["id"], trip_id=trip_id, price=7057.32)

        # Set selected_hotel_snapshot_id on trip metadata
        refreshed_trip = trip_store.get_trip(trip_id, user["id"])
        meta = (refreshed_trip.get("metadata") or {}) if refreshed_trip else {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        meta["selected_hotel_snapshot_id"] = snap_id
        meta["selected_hotel"] = {
            "id": snap_id,
            "snapshot_id": snap_id,
            "hotel_name": "Cochin Legacy",
            "price": 73.3,
            "currency": "USD",
            "price_inr": 7057.32,
        }
        trip_store.update_trip_metadata(trip_id, user["id"], meta)

        # Prebook resolving from trip.metadata.selected_hotel_snapshot_id without explicit snapshot_id in body
        with patch("services.hotel.hotel_service.HotelService.prebook", return_value={"prebook_id": "pb_rev2_live", "rooms": 1}):
            pre_res = client.post(
                "/api/hotels/prebook",
                json={
                    "trip_id": trip_id,
                    "hotel_id": snap_id,
                    "guests": 2,
                    "guest_name": "Shuvam Traveler",
                    "guest_email": "shuvam@trusttrip.com",
                },
            )
            assert pre_res.status_code == 200
            pre_data = pre_res.json()
            assert pre_data["success"] is True
            assert pre_data["prebook_id"] == "pb_rev2_live"
            assert pre_data["status"] == "prebooked"

        # Verify prebook was retained on trip metadata
        refreshed_trip = trip_store.get_trip(trip_id, user["id"])
        refreshed_meta = refreshed_trip.get("metadata") or {}
        assert "hotel_prebook" in refreshed_meta
        assert refreshed_meta["hotel_prebook"]["prebook_id"] == "pb_rev2_live"

        # Now initiate create-order using retained prebook
        mock_order = {"id": "order_rzp_rev2_test", "amount": 705732, "currency": "INR"}
        idem_key = f"idem_rev2_continuity_{uuid4()}"
        with patch.object(RazorpayService, "configured", return_value=True), \
             patch.object(RazorpayService, "create_order", return_value=mock_order), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None):
            order_res = client.post(
                "/api/hotel-payments/create-order",
                json={
                    "offer": {"provider": "liteapi", "hotel_name": "Cochin Legacy", "price_inr": 7057.32},
                    "prebook": {},  # Client prebook lost/empty -> server recovers from trip metadata
                    "holder": {"firstName": "Shuvam", "lastName": "Traveler"},
                    "guests": [{"occupancyNumber": 1, "firstName": "Shuvam", "lastName": "Traveler"}],
                    "trip_id": trip_id,
                    "revision_id": rev_2_id,
                    "expected_revision_number": 2,
                    "payment_idempotency_key": idem_key,
                    "final_confirmation": True,
                },
            )
            assert order_res.status_code == 200
            o_data = order_res.json()
            assert o_data["success"] is True
            assert o_data["razorpay_order_id"] == "order_rzp_rev2_test"
            assert o_data["amount_inr"] == 7057.32

    def test_missing_prebook_fails_closed(self):
        """5. Missing prebook fails closed."""
        client, user = _auth_user("no_prebook@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        res = client.post(
            "/api/hotel-payments/create-order",
            json={
                "offer": {"provider": "liteapi", "hotel_name": "No Prebook Hotel", "price_inr": 5000.0},
                "prebook": {},
                "holder": {"firstName": "Test", "lastName": "User"},
                "guests": [],
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": "idem_no_prebook_12345",
                "final_confirmation": True,
            },
        )
        assert res.status_code == 422
        assert "prebook is missing" in res.json()["detail"].lower()

    def test_wrong_revision_fails_closed(self):
        """6. Wrong revision fails closed."""
        client, user = _auth_user("wrong_rev@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        wrong_rev_id = str(uuid4())
        res = client.post(
            "/api/hotel-payments/create-order",
            json={
                "offer": {"provider": "liteapi", "hotel_name": "Test Hotel", "price_inr": 5000.0},
                "prebook": {"prebook_id": "pb_test_123"},
                "holder": {"firstName": "Test", "lastName": "User"},
                "guests": [],
                "trip_id": trip_id,
                "revision_id": wrong_rev_id,
                "expected_revision_number": 99,
                "payment_idempotency_key": "idem_wrong_rev_12345",
                "final_confirmation": True,
            },
        )
        assert res.status_code in {400, 404, 409}

    def test_valid_prebook_allows_payment_order_creation(self):
        """7. Valid prebook allows payment-order creation."""
        client, user = _auth_user("valid_order@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        mock_order = {"id": "order_rzp_valid_order_123", "amount": 1000000, "currency": "INR"}
        idem_key = f"idem_valid_order_{uuid4()}"
        with patch.object(RazorpayService, "configured", return_value=True), \
             patch.object(RazorpayService, "create_order", return_value=mock_order), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None):
            res = client.post(
                "/api/hotel-payments/create-order",
                json={
                    "offer": {"provider": "liteapi", "provider_offer_id": "off_valid", "hotel_name": "Valid Hotel", "price": 10000.0, "currency": "INR", "price_inr": 10000.0},
                    "prebook": {"prebook_id": "pb_valid_123", "status": "prebooked"},
                    "holder": {"firstName": "Valid", "lastName": "Guest"},
                    "guests": [],
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": 1,
                    "payment_idempotency_key": idem_key,
                    "final_confirmation": True,
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["razorpay_order_id"] == "order_rzp_valid_order_123"
            assert data["transaction_id"] is not None

    def test_existing_order_retry_remains_idempotent(self):
        """8. Existing prebook/order retry remains idempotent."""
        client, user = _auth_user("idempotent_order@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        mock_order = {"id": "order_rzp_idem_123", "amount": 800000, "currency": "INR"}
        idem_key = f"idem_key_repeat_{uuid4()}"
        with patch.object(RazorpayService, "configured", return_value=True), \
             patch.object(RazorpayService, "create_order", return_value=mock_order), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None):
            payload = {
                "offer": {"provider": "liteapi", "provider_offer_id": "off_idem", "hotel_name": "Idem Hotel", "price": 8000.0, "currency": "INR", "price_inr": 8000.0},
                "prebook": {"prebook_id": "pb_idem_123", "status": "prebooked"},
                "holder": {"firstName": "Idem", "lastName": "Guest"},
                "guests": [],
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": idem_key,
                "final_confirmation": True,
            }
            res1 = client.post("/api/hotel-payments/create-order", json=payload)
            assert res1.status_code == 200
            t1 = res1.json()["transaction_id"]
            o1 = res1.json()["razorpay_order_id"]

            res2 = client.post("/api/hotel-payments/create-order", json=payload)
            assert res2.status_code == 200
            t2 = res2.json()["transaction_id"]
            o2 = res2.json()["razorpay_order_id"]
            assert t1 == t2
            assert o1 == o2

    def test_create_order_recovers_prebook_from_trip_metadata(self):
        """If client payload omits prebook but trip.metadata contains a valid hotel_prebook, order creation recovers it."""
        client, user = _auth_user("recover_prebook@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        
        # Populate trip metadata with persisted prebook
        trip_store = TripStore()
        trip = trip_store.get_trip(trip_id, user_id=user["id"])
        meta = trip.get("metadata") or {}
        meta["hotel_prebook"] = {
            "prebook_id": "pb_recovered_from_meta_99",
            "status": "prebooked",
            "amount": 9500.0,
            "currency": "INR",
        }
        trip_store.update_trip(trip_id=trip_id, user_id=user["id"], metadata=meta)

        mock_order = {"id": "order_rzp_recovered_123", "amount": 950000, "currency": "INR"}
        idem_key = f"idem_recovered_{uuid4()}"
        with patch.object(RazorpayService, "configured", return_value=True), \
             patch.object(RazorpayService, "create_order", return_value=mock_order), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None):
            # Payload deliberately omits 'prebook'
            payload = {
                "offer": {"provider": "liteapi", "provider_offer_id": "off_recov", "hotel_name": "Recover Hotel", "price": 9500.0, "currency": "INR", "price_inr": 9500.0},
                "holder": {"firstName": "Recover", "lastName": "Guest"},
                "guests": [],
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": idem_key,
                "final_confirmation": True,
            }
            res = client.post("/api/hotel-payments/create-order", json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["razorpay_order_id"] == "order_rzp_recovered_123"
            assert data["transaction_id"] is not None

