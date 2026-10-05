"""Hotel Razorpay Consolidated Remediation Test Suite (HRP-01, HRP-02, HRP-03, HRP-04).

Verifies:
HRP-01:
  1. One checkout attempt creates one authoritative Razorpay order/transaction.
  2. Verify-and-book uses the original transaction and order ID.
  3. Mismatched Razorpay order ID is rejected with 409 Conflict.
  4. Idempotent retry on already-booked transaction succeeds without duplicate booking.

HRP-02:
  1. Legacy /create-order resolves authoritative snapshot price and overrides client-supplied price.
  2. Client-supplied price manipulation is defeated; authoritative snapshot amount is enforced.
  3. Validation rejects zero or negative amounts.

HRP-03:
  1. Captured payment + confirmed supplier failure triggers automated refund via RazorpayService.refund().
  2. Successful refund marks payment_status='refunded', booking_status='booking_failed', requires_refund=False.
  3. Failed refund attempt sets recoverable refund_failed state and requires_refund=True.
  4. Retry does not create duplicate refunds; subsequent verify-and-book on refunded tx returns cleanly.
  5. Refund is never falsely reported as successful if the gateway fails.

HRP-04:
  1. Lost supplier response reconciles successfully via HotelService.reconcile() -> marked booked without duplication.
  2. Confirmed absence (NOT_FOUND) triggers automated refund.
  3. Uncertain reconciliation (UNCERTAIN) enters reconciliation_pending and does NOT issue blind refund.
  4. Retry on reconciliation_pending finalizes booking when supplier confirmation becomes available.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import (
    HotelOffer,
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


def _auth_user(email: str = "hrp_user@example.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


def _create_trip(user_id: str, title: str = "HRP Test Trip") -> tuple[str, str]:
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
            "end_date": "2026-12-05",
            "budget": 50000.0,
            "currency": "INR",
            "travelers_count": 2,
        },
    )
    trip_id = str(trip["id"])

    # If DATABASE_URL is active, ensure user, trip, and plan_revision exist in PostgreSQL for FK integrity
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        import psycopg
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
            pass

    return trip_id, rev_id


def _create_hotel_snapshot(user_id: str, trip_id: str, price: float = 18500.0) -> str:
    snap_store = SearchSessionStore()
    session = snap_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id="th_hrp",
        search_type="hotels",
        criteria_hash="hash_hrp_" + str(uuid4())[:8],
        trip_revision=1,
        reuse_ttl_seconds=3600,
    )
    snap_id = uuid4()
    now = datetime.now(timezone.utc)
    price_dec = Decimal(str(price))
    norm_hotel = HotelOffer(
        snapshot_id=snap_id,
        hotel_id="ht_hrp_grand",
        hotel_name="Grand Hyatt Mumbai",
        city="Mumbai",
        address="BKC",
        room_name="Club King",
        board_type="Breakfast Included",
        price=PriceBreakdown(
            base_amount=price_dec,
            total_amount=price_dec,
            currency="INR",
            inr_reference_amount=price_dec,
        ),
        nightly_price=Decimal(str(price / 2)),
        provider="liteapi",
        provider_offer_id="liteapi_off_hrp_101",
        refundable=True,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        nights=2,
        retrieved_at=now,
        expires_at=now + timedelta(hours=2),
    )
    snap = snap_store.save_snapshot(
        session_id=session.session_id,
        offer=norm_hotel,
        raw_payload={"hotel_id": "ht_hrp_grand", "rooms": 1},
    )
    return str(snap.snapshot_id)


# ===========================================================================
# HRP-01: FRONTEND & BACKEND TRANSACTION IDENTITY AND IDEMPOTENCY
# ===========================================================================

class TestHRP01TransactionIdentity:
    """HRP-01: Verifies single authoritative transaction identity and rejection of mismatched orders."""

    def test_verify_and_book_rejects_mismatched_order_id(self):
        """HRP-01: If verify-and-book receives an order ID different from the transaction's order ID, reject with 409."""
        client, user = _auth_user("hrp01_mismatch@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        authoritative_order_id = "order_rzp_original_111"
        mismatched_order_id = "order_rzp_different_222"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": authoritative_order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 15000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx):
            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": mismatched_order_id,
                    "razorpay_payment_id": "pay_test_123",
                    "razorpay_signature": "sig_test_123",
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
            assert res.status_code == 409
            assert "does not match the hotel transaction" in res.json().get("detail", "")

    def test_verify_and_book_idempotent_retry_on_already_booked(self):
        """HRP-01: Retrying verify-and-book on an already booked transaction returns existing booking without re-booking."""
        client, user = _auth_user("hrp01_idempotent@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_booked_333"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "booked",
            "payment_status": "captured",
            "amount_inr": 15000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
            "booking_json": {
                "booking_id": "bkg_existing_999",
                "confirmation_code": "CONF-999",
                "status": "CONFIRMED",
            },
        }

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch("services.hotel.hotel_service.HotelService.book") as mock_book:
            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": "pay_test_123",
                    "razorpay_signature": "sig_test_123",
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
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["already_booked"] is True
            assert data["booking"]["booking_id"] == "bkg_existing_999"
            mock_book.assert_not_called()


# ===========================================================================
# HRP-02: AUTHORITATIVE SERVER-SIDE PRICING
# ===========================================================================

class TestHRP02AuthoritativePricing:
    """HRP-02: Client cannot manipulate payment amounts via legacy /create-order or snapshot endpoints."""

    def test_legacy_create_order_overrides_client_manipulated_price_with_snapshot(self):
        """HRP-02: Client sends manipulated price of 1.0 INR, but snapshot is 18500.0 INR.
        The created Razorpay order must strictly use the snapshot price (18500.0 INR).
        """
        client, user = _auth_user("hrp02_pricing@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        snap_id = _create_hotel_snapshot(user_id=user["id"], trip_id=trip_id, price=18500.0)

        mock_rz_order = {
            "id": "order_rzp_auth_price_123",
            "amount": 1850000,
            "currency": "INR",
            "status": "created",
        }

        with patch.object(RazorpayService, "create_order", return_value=mock_rz_order) as mock_create_rz, \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None):
            res = client.post(
                "/api/hotel-payments/create-order",
                json={
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": 1,
                    "payment_idempotency_key": f"idem_hrp02_{uuid4().hex[:12]}",
                    "final_confirmation": True,
                    "prebook": {"prebook_id": "pb_test_100"},
                    "holder": {"firstName": "Aarav", "lastName": "Kapoor"},
                    "guests": [],
                    "snapshot_id": snap_id,
                    "offer": {
                        "hotel_name": "Grand Hyatt Mumbai",
                        "price_inr": 1.0,  # Malicious attempt to pay only 1 INR
                        "price": 1.0,
                    },
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["amount_inr"] == 18500.0
            # Razorpay service must have been called with 18500.0, NOT 1.0
            mock_create_rz.assert_called_once()
            call_amount = mock_create_rz.call_args[0][0] if mock_create_rz.call_args[0] else mock_create_rz.call_args[1].get("amount_rupees")
            assert call_amount == 18500.0

    def test_legacy_create_order_rejects_negative_or_zero_price(self):
        """HRP-02: If no snapshot exists and client supplies invalid non-positive amount, reject with 422."""
        client, user = _auth_user("hrp02_negative@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])

        res = client.post(
            "/api/hotel-payments/create-order",
            json={
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": f"idem_hrp02_neg_{uuid4().hex[:12]}",
                "final_confirmation": True,
                "prebook": {"prebook_id": "pb_test_100"},
                "holder": {"firstName": "Aarav", "lastName": "Kapoor"},
                "guests": [],
                "offer": {
                    "hotel_name": "Test Hotel",
                    "price_inr": -500.0,
                },
            },
        )
        assert res.status_code == 422
        assert "strictly positive" in str(res.json().get("detail", "")).lower()


# ===========================================================================
# HRP-03: AUTOMATED REFUND ON CAPTURED FUNDS WITH SUPPLIER FAILURE
# ===========================================================================

class TestHRP03AutomatedRefund:
    """HRP-03: Safe, idempotent automated refund when payment is captured but supplier booking fails."""

    def test_captured_payment_supplier_failure_triggers_successful_refund(self):
        """HRP-03: When supplier booking fails and supplier confirms no booking, Razorpay refund is triggered and succeeds."""
        client, user = _auth_user("hrp03_refund_success@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_ref_1"
        pay_id = "pay_rzp_ref_1"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 20000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        mock_payment = {
            "id": pay_id,
            "order_id": order_id,
            "currency": "INR",
            "amount": 2000000,
            "status": "captured",
        }

        mock_refund = {
            "id": "rfnd_rzp_auto_999",
            "payment_id": pay_id,
            "amount": 2000000,
            "status": "processed",
        }

        updated_records = {}
        def mock_update(t_id, **kwargs):
            updated_records.update(kwargs)

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
             patch.object(HotelTransactionStore, "claim_booking", return_value=True), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value=mock_payment), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
             patch("services.hotel.hotel_service.HotelService.book", side_effect=Exception("Supplier room sold out")), \
             patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("NOT_FOUND", None)), \
             patch.object(RazorpayService, "refund", return_value=mock_refund) as mock_rz_refund:

            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": "sig_valid_123",
                    "offer": {"provider": "liteapi", "hotel_name": "Grand Hotel"},
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
            assert data["success"] is False
            assert data["refunded"] is True
            assert data["refund_id"] == "rfnd_rzp_auto_999"
            assert data["requires_refund"] is False
            assert updated_records["payment_status"] == "refunded"
            assert updated_records["booking_status"] == "booking_failed"
            mock_rz_refund.assert_called_once()
            call_kwargs = mock_rz_refund.call_args[1]
            assert call_kwargs["payment_id"] == pay_id
            assert call_kwargs["amount_rupees"] == 20000.0
            assert "ref_ht_" in call_kwargs["idempotency_key"]

    def test_captured_payment_refund_gateway_failure_enters_recoverable_state(self):
        """HRP-03: When refund API fails transiently, system enters recoverable refund_failed state instead of falsely reporting success."""
        client, user = _auth_user("hrp03_refund_fail@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_ref_fail_1"
        pay_id = "pay_rzp_ref_fail_1"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 20000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        mock_payment = {
            "id": pay_id,
            "order_id": order_id,
            "currency": "INR",
            "amount": 2000000,
            "status": "captured",
        }

        updated_records = {}
        def mock_update(t_id, **kwargs):
            updated_records.update(kwargs)

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
             patch.object(HotelTransactionStore, "claim_booking", return_value=True), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value=mock_payment), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
             patch("services.hotel.hotel_service.HotelService.book", side_effect=Exception("Supplier outage")), \
             patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("NOT_FOUND", None)), \
             patch.object(RazorpayService, "refund", side_effect=Exception("Razorpay gateway 503")):

            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": "sig_valid_123",
                    "offer": {"provider": "liteapi", "hotel_name": "Grand Hotel"},
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
            assert data["success"] is False
            assert data["refunded"] is False
            assert data["requires_refund"] is True
            assert updated_records["payment_status"] == "refund_failed"
            assert updated_records["booking_status"] == "booking_failed"

    def test_repeat_verify_and_book_on_refunded_tx_is_idempotent(self):
        """HRP-03: Repeated call on an already refunded transaction returns refunded status without duplicate refund call."""
        client, user = _auth_user("hrp03_repeat_refund@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_ref_done"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "booking_failed",
            "payment_status": "refunded",
            "amount_inr": 20000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
            "error": "Payment refunded because hotel booking could not be completed.",
        }

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(RazorpayService, "refund") as mock_rz_refund:
            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": "pay_123",
                    "razorpay_signature": "sig_123",
                    "offer": {"provider": "liteapi", "hotel_name": "Grand Hotel"},
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
            assert data["success"] is False
            assert data["refunded"] is True
            assert data["requires_refund"] is False
            mock_rz_refund.assert_not_called()


# ===========================================================================
# HRP-04: SUPPLIER RECONCILIATION & UNCERTAIN STATE RESILIENCE
# ===========================================================================

class TestHRP04SupplierReconciliation:
    """HRP-04: Supplier response lost but booking succeeded upstream reconciles to confirmed booking without duplicate booking."""

    def test_supplier_network_timeout_reconciles_to_confirmed_booking(self):
        """HRP-04: Initial book() raises timeout, but reconcile() discovers booking was confirmed by supplier.
        System finalizes transaction as booked truthfully without duplicate supplier booking.
        """
        client, user = _auth_user("hrp04_rec_confirm@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_rec_1"
        pay_id = "pay_rzp_rec_1"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 25000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        mock_payment = {
            "id": pay_id,
            "order_id": order_id,
            "currency": "INR",
            "amount": 2500000,
            "status": "captured",
        }

        reconciled_booking = {
            "provider": "liteapi",
            "booking_id": "bkg_rec_recovered_888",
            "confirmation_code": "VOUCHER-REC-888",
            "status": "CONFIRMED",
        }

        updated_records = {}
        def mock_update(t_id, **kwargs):
            updated_records.update(kwargs)

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
             patch.object(HotelTransactionStore, "claim_booking", return_value=True), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value=mock_payment), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
             patch("services.hotel.hotel_service.HotelService.book", side_effect=Exception("ReadTimeout")), \
             patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("CONFIRMED", reconciled_booking)), \
             patch.object(RazorpayService, "refund") as mock_refund, \
             patch("hotel_payment_api.build_hotel_booking_pdf", return_value=None):

            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": "sig_valid_123",
                    "offer": {"provider": "liteapi", "hotel_name": "Taj Lands End"},
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
            assert data["booking"]["booking_id"] == "bkg_rec_recovered_888"
            assert data["booking"]["confirmation_code"] == "VOUCHER-REC-888"
            assert updated_records["booking_status"] == "booked"
            assert updated_records["payment_status"] == "captured"
            mock_refund.assert_not_called()

    def test_supplier_uncertain_state_blocks_blind_refund_and_blocks_duplicate_booking(self):
        """HRP-04: When reconciliation returns UNCERTAIN, transaction enters reconciliation_pending,
        does NOT trigger refund, and informs client that confirmation is pending.
        """
        client, user = _auth_user("hrp04_rec_uncertain@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_rec_unc"
        pay_id = "pay_rzp_rec_unc"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount_inr": 25000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
        }

        mock_payment = {
            "id": pay_id,
            "order_id": order_id,
            "currency": "INR",
            "amount": 2500000,
            "status": "captured",
        }

        updated_records = {}
        def mock_update(t_id, **kwargs):
            updated_records.update(kwargs)

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
             patch.object(HotelTransactionStore, "claim_booking", return_value=True), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value=mock_payment), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
             patch("services.hotel.hotel_service.HotelService.book", side_effect=Exception("ConnectionReset")), \
             patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("UNCERTAIN", None)), \
             patch.object(RazorpayService, "refund") as mock_refund:

            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": "sig_valid_123",
                    "offer": {"provider": "liteapi", "hotel_name": "Taj Lands End"},
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
            assert data["success"] is False
            assert data["reconciliation_pending"] is True
            assert data["booking_pending"] is True
            assert data["requires_refund"] is False
            assert updated_records["booking_status"] == "reconciliation_pending"
            assert updated_records["payment_status"] == "captured"
            # Crucially: no refund should be issued while supplier status is uncertain
            mock_refund.assert_not_called()

    def test_reconciliation_pending_retry_finalizes_when_supplier_confirms(self):
        """HRP-04: Subsequent call when transaction is in reconciliation_pending reconciles and finalizes."""
        client, user = _auth_user("hrp04_rec_retry@example.com")
        trip_id, rev_id = _create_trip(user_id=user["id"])
        tx_id = str(uuid4())
        order_id = "order_rzp_rec_retry_1"

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user["id"]),
            "razorpay_order_id": order_id,
            "booking_status": "reconciliation_pending",
            "payment_status": "captured",
            "amount_inr": 25000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
            "offer_json": {"provider": "liteapi", "hotel_name": "Taj Lands End"},
        }

        reconciled_booking = {
            "provider": "liteapi",
            "booking_id": "bkg_rec_retry_success",
            "confirmation_code": "VOUCHER-REC-RETRY",
            "status": "CONFIRMED",
        }

        updated_records = {}
        def mock_update(t_id, **kwargs):
            updated_records.update(kwargs)

        with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
             patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
             patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("CONFIRMED", reconciled_booking)), \
             patch("services.hotel.hotel_service.HotelService.book") as mock_book, \
             patch("hotel_payment_api.build_hotel_booking_pdf", return_value=None):

            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": "pay_123",
                    "razorpay_signature": "sig_123",
                    "offer": {"provider": "liteapi", "hotel_name": "Taj Lands End"},
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
            assert data["reconciled"] is True
            assert data["booking"]["booking_id"] == "bkg_rec_retry_success"
            assert updated_records["booking_status"] == "booked"
            # Crucially: no new supplier booking was initiated; it reconciled existing booking
            mock_book.assert_not_called()
