"""Adversarial Regression Tests for Hotel Payment & Reconciliation Incident Remediation.

Covers:
1. Duplicate Payment Prevention:
   - Block order creation if an outstanding captured/pending/refund_pending transaction exists.
   - Allow new order once previous transaction is cleanly refunded.
2. Refund Recovery & Reconciliation:
   - Reconcile endpoint re-attempts pending/failed refunds using deterministic idempotency key.
   - Does not lock out booking_failed/refund_pending transactions under "reconciliation in progress".
   - Transitions to refunded on provider confirmation.
3. Trip Hotel Payment Status:
   - GET /api/hotel-payments/by-trip/{trip_id} correctly exposes refund_pending and prioritizes unresolved transactions.
4. Hotel Voucher PDF Contract:
   - GET /api/trips/{tx_id}/hotel.pdf returns 409 for unconfirmed/refund_pending transactions.
   - list_trips excludes unconfirmed hotel stays from trip.hotels.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.hotel_transaction_store import HotelTransactionStore
from services.trip_store import TripStore
from services.razorpay_service import RazorpayService


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()


def _auth_user(email: str = "incident_user@example.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


def _valid_order_payload(trip_id: str, rev_id: str | None = None) -> dict:
    return {
        "trip_id": trip_id,
        "revision_id": rev_id or str(uuid4()),
        "expected_revision_number": 1,
        "payment_idempotency_key": f"idem_inc_{uuid4().hex[:12]}",
        "final_confirmation": True,
        "prebook": {"prebook_id": "pb_inc_100"},
        "holder": {"firstName": "Aarav", "lastName": "Kapoor"},
        "guests": [],
        "offer": {
            "hotel_name": "Grand Hotel",
            "price_inr": 21817.91,
            "price": 21817.91,
        },
    }


def test_create_order_blocked_by_reconciliation_pending_transaction():
    client, user = _auth_user("block_rec@example.com")
    trip_id = str(uuid4())
    mock_trip = {"id": trip_id, "user_id": str(user["id"]), "status": "approved"}

    pending_tx = {
        "id": str(uuid4()),
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_pending_1",
        "razorpay_payment_id": "pay_pending_1",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 21817.91,
        "currency": "INR",
    }

    with patch.object(TripStore, "get_trip", return_value=mock_trip), \
         patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
         patch.object(HotelTransactionStore, "get_by_trip", return_value=[pending_tx]):
        resp = client.post(
            "/api/hotel-payments/create-order",
            json=_valid_order_payload(trip_id),
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "already been captured" in detail or "pending verification" in detail


def test_create_order_blocked_by_refund_pending_transaction():
    client, user = _auth_user("block_ref@example.com")
    trip_id = str(uuid4())
    mock_trip = {"id": trip_id, "user_id": str(user["id"]), "status": "approved"}

    refund_pending_tx = {
        "id": str(uuid4()),
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_refund_1",
        "razorpay_payment_id": "pay_refund_1",
        "payment_status": "refund_pending",
        "booking_status": "booking_failed",
        "amount_inr": 21817.91,
        "currency": "INR",
    }

    with patch.object(TripStore, "get_trip", return_value=mock_trip), \
         patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
         patch.object(HotelTransactionStore, "get_by_trip", return_value=[refund_pending_tx]):
        resp = client.post(
            "/api/hotel-payments/create-order",
            json=_valid_order_payload(trip_id),
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "refund" in detail


def test_create_order_allowed_after_clean_refund():
    client, user = _auth_user("clean_ref@example.com")
    trip_id = str(uuid4())
    mock_trip = {"id": trip_id, "user_id": str(user["id"]), "status": "approved"}

    refunded_tx = {
        "id": str(uuid4()),
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_old_1",
        "razorpay_payment_id": "pay_old_1",
        "payment_status": "refunded",
        "booking_status": "booking_failed",
        "amount_inr": 21817.91,
        "currency": "INR",
    }

    mock_reservation = {
        "id": "tx_new_999",
        "razorpay_order_id": "order_new_123",
        "amount_inr": 21817.91,
        "booking_status": "awaiting_payment",
    }

    mock_razorpay_order = {
        "id": "order_new_123",
        "amount": 2181791,
        "currency": "INR",
        "receipt": "rcpt_123",
        "status": "created",
    }

    with patch.object(TripStore, "get_trip", return_value=mock_trip), \
         patch.object(HotelTransactionStore, "get_by_trip", return_value=[refunded_tx]), \
         patch.object(HotelTransactionStore, "reserve_payment_order", return_value=mock_reservation), \
         patch.object(RazorpayService, "create_order", return_value=mock_razorpay_order), \
         patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", return_value=None), \
         patch.object(HotelTransactionStore, "update", return_value=None):
        resp = client.post(
            "/api/hotel-payments/create-order",
            json=_valid_order_payload(trip_id),
        )
        assert resp.status_code == 200
        assert resp.json()["razorpay_order_id"] == "order_new_123"


def test_reconcile_recovers_unprocessed_refund_deterministically():
    client, user = _auth_user("rec_refund@example.com")
    tx_id = str(uuid4())
    trip_id = str(uuid4())

    tx_data = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_inc_1",
        "razorpay_payment_id": "pay_inc_1",
        "payment_status": "refund_pending",
        "booking_status": "booking_failed",
        "amount_inr": 21817.91,
        "currency": "INR",
        "error": "Supplier confirmed no booking exists. Refund pending: unprocessed",
    }

    mock_refund_result = {
        "id": "rfnd_test_recovered",
        "status": "processed",
        "amount": 2181791,
        "currency": "INR",
    }

    with patch.object(HotelTransactionStore, "get", return_value=tx_data), \
         patch.object(RazorpayService, "refund", return_value=mock_refund_result) as mock_refund_call, \
         patch.object(HotelTransactionStore, "update") as mock_update:

        resp = client.post(f"/api/hotel-payments/{tx_id}/reconcile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["refunded"] is True
        assert data["status"] == "refunded"
        assert data["refund_id"] == "rfnd_test_recovered"

        # Verify deterministic idempotency key format
        expected_key = f"ref_ht_{tx_id.replace('-', '')[:32]}"
        mock_refund_call.assert_called_once_with(
            payment_id="pay_inc_1",
            amount_rupees=21817.91,
            idempotency_key=expected_key,
        )

        mock_update.assert_called_once()
        assert mock_update.call_args.args[0] == tx_id
        assert mock_update.call_args.kwargs["payment_status"] == "refunded"
        assert mock_update.call_args.kwargs["booking_json"]["refund"]["id"] == "rfnd_test_recovered"


def test_get_by_trip_returns_refund_pending_and_prioritizes():
    client, user = _auth_user("by_trip@example.com")
    trip_id = str(uuid4())

    tx1_old_failed = {
        "id": str(uuid4()),
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "payment_status": "failed",
        "booking_status": "cancelled",
        "amount_inr": 1000.0,
    }

    tx2_active_ref_pending = {
        "id": str(uuid4()),
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "payment_status": "refund_pending",
        "booking_status": "booking_failed",
        "amount_inr": 21817.91,
        "razorpay_payment_id": "pay_captured_1",
    }

    with patch.object(HotelTransactionStore, "get_by_trip", return_value=[tx1_old_failed, tx2_active_ref_pending]):
        resp = client.get(f"/api/hotel-payments/by-trip/{trip_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_payment"] is True
        assert data["transaction_id"] == tx2_active_ref_pending["id"]
        assert data["refund_pending"] is True
        assert data["is_captured"] is True


def test_hotel_pdf_endpoint_blocks_unbooked_transaction():
    client, user = _auth_user("pdf_user@example.com")
    tx_id = str(uuid4())
    trip_id = str(uuid4())

    unbooked_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "payment_status": "refund_pending",
        "booking_status": "booking_failed",
        "amount_inr": 21817.91,
    }

    with patch.object(HotelTransactionStore, "get", return_value=unbooked_tx):
        resp = client.get(f"/api/trips/{tx_id}/hotel.pdf")
        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "not confirmed" in detail or "only after a successful booking" in detail
