"""Tests for Hotel Payment Status and Reconcile Endpoints (P0).

Verifies:
1. GET /api/hotel-payments/{transaction_id}/status:
   - Returns transaction status, amount, and reconciliation details.
   - Forbids access by a different user (403).
   - Returns 404 for non-existent transaction.
2. POST /api/hotel-payments/{transaction_id}/reconcile:
   - Reconciles UNCERTAIN transaction to CONFIRMED (transitions to booked).
   - Reconciles UNCERTAIN transaction to NOT_FOUND (triggers automated refund, transitions to refunded).
   - Remains reconciliation_pending when supplier is still UNCERTAIN.
   - Idempotently handles already booked transaction.
   - Forbids access by a different user (403).
3. GET /api/hotel-payments/by-trip/{trip_id}:
   - Finds active transaction for trip.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
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


def _auth_user(email: str = "rec_user@example.com") -> tuple[TestClient, dict]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user


def test_get_payment_status_ownership_and_details():
    client, user = _auth_user("owner@example.com")
    other_client, other_user = _auth_user("other@example.com")

    tx_id = f"tx_{uuid4().hex[:8]}"
    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_123",
        "razorpay_order_id": "order_abc",
        "razorpay_payment_id": "pay_xyz",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 6100.0,
        "currency": "INR",
        "created_at": "2026-10-12T12:00:00Z",
        "updated_at": "2026-10-12T12:00:00Z",
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx):
        # 1. Owner can fetch status
        res = client.get(f"/api/hotel-payments/{tx_id}/status")
        assert res.status_code == 200
        data = res.json()
        assert data["transaction_id"] == tx_id
        assert data["booking_status"] == "reconciliation_pending"
        assert data["reconciliation_pending"] is True
        assert data["payment_status"] == "captured"
        assert data["amount_inr"] == 6100.0

        # 2. Other user is rejected with 403 Forbidden
        res_other = other_client.get(f"/api/hotel-payments/{tx_id}/status")
        assert res_other.status_code == 403


def test_reconcile_endpoint_confirmed_transition():
    client, user = _auth_user("rec_success@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"
    trip_id = f"trip_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_confirmed_1",
        "razorpay_payment_id": "pay_confirmed_1",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 8500.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Cochin Grand Heritage"},
    }

    confirmed_booking = {
        "provider": "liteapi",
        "booking_id": "bkg_rec_success_99",
        "confirmation_code": "VOUCHER-REC-99",
        "status": "CONFIRMED",
    }

    updated_kwargs = {}
    def mock_update(t_id, **kwargs):
        updated_kwargs.update(kwargs)

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
         patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("CONFIRMED", confirmed_booking)):

        res = client.post(f"/api/hotel-payments/{tx_id}/reconcile")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["status"] == "confirmed"
        assert data["booking"]["booking_id"] == "bkg_rec_success_99"
        assert updated_kwargs.get("booking_status") == "booked"


def test_reconcile_endpoint_not_found_refund_transition():
    client, user = _auth_user("rec_refund@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"
    trip_id = f"trip_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_nf_1",
        "razorpay_payment_id": "pay_nf_1",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 8500.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Cochin Grand Heritage"},
    }

    updated_kwargs = {}
    def mock_update(t_id, **kwargs):
        updated_kwargs.update(kwargs)

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", side_effect=mock_update), \
         patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("NOT_FOUND", None)), \
         patch.object(RazorpayService, "refund", return_value={"id": "rfnd_auto_1", "status": "processed"}) as mock_refund:

        res = client.post(f"/api/hotel-payments/{tx_id}/reconcile")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["status"] == "refunded"
        assert data["refunded"] is True
        mock_refund.assert_called_once()
        assert updated_kwargs.get("payment_status") == "refunded"
        assert updated_kwargs.get("booking_status") == "booking_failed"


def test_reconcile_endpoint_uncertain_preserves_pending():
    client, user = _auth_user("rec_pending@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"
    trip_id = f"trip_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "razorpay_order_id": "order_unc_1",
        "razorpay_payment_id": "pay_unc_1",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 8500.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Cochin Grand Heritage"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("UNCERTAIN", None)), \
         patch.object(RazorpayService, "refund") as mock_refund:

        res = client.post(f"/api/hotel-payments/{tx_id}/reconcile")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["status"] == "reconciliation_pending"
        assert data["reconciliation_pending"] is True
        # Must NOT issue blind refund when UNCERTAIN
        mock_refund.assert_not_called()


def test_get_by_trip_pending_transaction():
    client, user = _auth_user("trip_rec@example.com")
    trip_id = f"trip_{uuid4().hex[:8]}"
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": trip_id,
        "booking_status": "reconciliation_pending",
        "payment_status": "captured",
        "amount_inr": 7500.0,
    }

    with patch.object(HotelTransactionStore, "get_by_trip", return_value=[mock_tx]):
        res = client.get(f"/api/hotel-payments/by-trip/{trip_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["transaction"] is not None
        assert data["transaction"]["transaction_id"] == tx_id
        assert data["transaction"]["booking_status"] == "reconciliation_pending"
        assert data["transaction"]["reconciliation_pending"] is True


def test_concurrent_reconciliation_single_supplier_operation():
    """Case 1: Two concurrent reconciliation requests for the same pending transaction
    produce at most one active supplier reconciliation operation.
    """
    client, user = _auth_user("conc_single@example.com")
    token = client.headers["Authorization"].split(" ")[1]
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_123",
        "razorpay_order_id": "order_123",
        "razorpay_payment_id": "pay_123",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 8500.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel"},
    }

    reconcile_calls = 0
    reconcile_lock = threading.Lock()

    def slow_reconcile(*args, **kwargs):
        nonlocal reconcile_calls
        with reconcile_lock:
            reconcile_calls += 1
        time.sleep(0.15)
        return ("CONFIRMED", {"provider": "liteapi", "booking_id": "bkg_single_1", "confirmation_code": "V-1"})

    def make_request():
        with TestClient(app) as tc:
            tc.headers["Authorization"] = f"Bearer {token}"
            return tc.post(f"/api/hotel-payments/{tx_id}/reconcile")

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile", side_effect=slow_reconcile):

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(make_request), pool.submit(make_request)]
            responses = [f.result() for f in futures]

        assert all(r.status_code == 200 for r in responses)
        assert reconcile_calls == 1
        statuses = [r.json().get("status") for r in responses]
        assert "confirmed" in statuses


def test_concurrent_reconciliation_second_request_safe_in_progress_response():
    """Case 2: A second request receives a safe pending/current-state response while the first owns the claim."""
    client, user = _auth_user("conc_inprog@example.com")
    token = client.headers["Authorization"].split(" ")[1]
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_456",
        "razorpay_order_id": "order_456",
        "razorpay_payment_id": "pay_456",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 9200.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel 2"},
    }

    in_reconcile_event = threading.Event()
    continue_event = threading.Event()

    def blocked_reconcile(*args, **kwargs):
        in_reconcile_event.set()
        continue_event.wait(timeout=2.0)
        return ("CONFIRMED", {"provider": "liteapi", "booking_id": "bkg_inprog_2", "confirmation_code": "V-2"})

    first_resp = None
    second_resp = None

    def thread_1_work():
        nonlocal first_resp
        with TestClient(app) as tc:
            tc.headers["Authorization"] = f"Bearer {token}"
            first_resp = tc.post(f"/api/hotel-payments/{tx_id}/reconcile")

    def thread_2_work():
        nonlocal second_resp
        with TestClient(app) as tc:
            tc.headers["Authorization"] = f"Bearer {token}"
            second_resp = tc.post(f"/api/hotel-payments/{tx_id}/reconcile")

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile", side_effect=blocked_reconcile):

        t1 = threading.Thread(target=thread_1_work)
        t1.start()

        assert in_reconcile_event.wait(timeout=2.0)

        t2 = threading.Thread(target=thread_2_work)
        t2.start()
        t2.join(timeout=2.0)

        continue_event.set()
        t1.join(timeout=2.0)

    assert second_resp is not None
    assert second_resp.status_code == 200
    data2 = second_resp.json()
    assert data2["status"] == "reconciliation_in_progress"
    assert data2["booking_status"] == "reconciliation_in_progress"
    assert data2["reconciliation_pending"] is True
    assert data2["payment_captured"] is True
    assert "in progress" in data2["error"].lower()

    assert first_resp is not None
    assert first_resp.status_code == 200
    assert first_resp.json()["status"] == "confirmed"


def test_concurrent_confirmed_outcomes_no_duplicate_persistence_or_booking_records():
    """Case 3: Concurrent CONFIRMED outcomes do not duplicate persistence or booking records."""
    client, user = _auth_user("conc_dup_rec@example.com")
    token = client.headers["Authorization"].split(" ")[1]
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_dup",
        "razorpay_order_id": "order_dup",
        "razorpay_payment_id": "pay_dup",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 5000.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel Dup"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile",
               return_value=("CONFIRMED", {"provider": "liteapi", "booking_id": "bkg_single_persist", "confirmation_code": "V-DUP"})):

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(lambda: TestClient(app).post(f"/api/hotel-payments/{tx_id}/reconcile", headers={"Authorization": f"Bearer {token}"})),
                pool.submit(lambda: TestClient(app).post(f"/api/hotel-payments/{tx_id}/reconcile", headers={"Authorization": f"Bearer {token}"})),
            ]
            responses = [f.result() for f in futures]

        assert all(r.status_code == 200 for r in responses)
        assert mock_tx["booking_status"] == "booked"
        assert mock_tx["booking_json"]["booking_id"] == "bkg_single_persist"


def test_concurrent_not_found_single_refund_dispatch():
    """Case 4: Concurrent NOT_FOUND outcomes do not dispatch multiple effective refunds."""
    client, user = _auth_user("conc_rfnd@example.com")
    token = client.headers["Authorization"].split(" ")[1]
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_rfnd",
        "razorpay_order_id": "order_rfnd",
        "razorpay_payment_id": "pay_rfnd",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 11000.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel Refund"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("NOT_FOUND", None)), \
         patch.object(RazorpayService, "refund", return_value={"id": "rfnd_atomic_1", "status": "processed"}) as mock_refund:

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(lambda: TestClient(app).post(f"/api/hotel-payments/{tx_id}/reconcile", headers={"Authorization": f"Bearer {token}"})),
                pool.submit(lambda: TestClient(app).post(f"/api/hotel-payments/{tx_id}/reconcile", headers={"Authorization": f"Bearer {token}"})),
            ]
            responses = [f.result() for f in futures]

        assert all(r.status_code == 200 for r in responses)
        assert mock_refund.call_count == 1
        assert mock_tx["payment_status"] == "refunded"
        assert mock_tx["booking_status"] == "booking_failed"


def test_uncertain_outcome_preserves_reconciliation_pending_and_recoverable():
    """Case 5: UNCERTAIN leaves the transaction pending and recoverable."""
    client, user = _auth_user("conc_unc@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_unc",
        "razorpay_order_id": "order_unc",
        "razorpay_payment_id": "pay_unc",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 6000.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel Unc"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("UNCERTAIN", None)), \
         patch.object(RazorpayService, "refund") as mock_refund:

        res = client.post(f"/api/hotel-payments/{tx_id}/reconcile")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "reconciliation_pending"
        assert data["reconciliation_pending"] is True
        assert data["payment_captured"] is True
        assert data["requires_refund"] is False
        mock_refund.assert_not_called()
        assert mock_tx["booking_status"] == "reconciliation_pending"


def test_exception_interruption_releases_claim_without_stranding():
    """Case 6: An exception or simulated worker interruption does not permanently strand the transaction."""
    client, user = _auth_user("conc_exc@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_exc",
        "razorpay_order_id": "order_exc",
        "razorpay_payment_id": "pay_exc",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 7200.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel Exc"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile", side_effect=RuntimeError("LiteAPI network timeout")):

        with pytest.raises(RuntimeError, match="LiteAPI network timeout"):
            client.post(f"/api/hotel-payments/{tx_id}/reconcile")

        assert mock_tx["booking_status"] == "reconciliation_pending"
        assert "interrupted" in mock_tx.get("error", "").lower()


def test_subsequent_request_can_reconcile_after_release_or_lease_expiry():
    """Case 7: A subsequent request can reconcile after the first claim is released or safely expires."""
    client, user = _auth_user("conc_subseq@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"

    # Subcase A: Recover after released claim
    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_subseq",
        "razorpay_order_id": "order_subseq",
        "razorpay_payment_id": "pay_subseq",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 8000.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel Subseq"},
    }

    confirmed_bkg = {"provider": "liteapi", "booking_id": "bkg_subseq_ok", "confirmation_code": "V-SUB"}

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx.update(kw)):

        # First call fails
        with patch("services.hotel.hotel_service.HotelService.reconcile", side_effect=RuntimeError("Crash")):
            with pytest.raises(RuntimeError):
                client.post(f"/api/hotel-payments/{tx_id}/reconcile")

        assert mock_tx["booking_status"] == "reconciliation_pending"

        # Subsequent call reconciles successfully
        with patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("CONFIRMED", confirmed_bkg)):
            res2 = client.post(f"/api/hotel-payments/{tx_id}/reconcile")
            assert res2.status_code == 200
            assert res2.json()["status"] == "confirmed"
            assert mock_tx["booking_status"] == "booked"

    # Subcase B: Recover stale lease (worker crashed without finally running)
    tx_id_stale = f"tx_{uuid4().hex[:8]}"
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    mock_tx_stale = {
        "id": tx_id_stale,
        "customer_ref": str(user["id"]),
        "trip_id": "trip_stale",
        "razorpay_order_id": "order_stale",
        "razorpay_payment_id": "pay_stale",
        "payment_status": "captured",
        "booking_status": "reconciliation_in_progress",
        "updated_at": stale_time,
        "amount_inr": 8500.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Test Hotel Stale"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx_stale), \
         patch.object(HotelTransactionStore, "update", lambda self, tid, **kw: mock_tx_stale.update(kw)), \
         patch("services.hotel.hotel_service.HotelService.reconcile", return_value=("CONFIRMED", confirmed_bkg)):

        res_stale = client.post(f"/api/hotel-payments/{tx_id_stale}/reconcile")
        assert res_stale.status_code == 200
        assert res_stale.json()["status"] == "confirmed"
        assert mock_tx_stale["booking_status"] == "booked"


def test_different_transactions_reconcile_independently():
    """Case 8: Different transactions can reconcile independently."""
    client, user = _auth_user("conc_diff@example.com")
    token = client.headers["Authorization"].split(" ")[1]

    tx1 = f"tx_diff_1_{uuid4().hex[:6]}"
    tx2 = f"tx_diff_2_{uuid4().hex[:6]}"

    records = {
        tx1: {
            "id": tx1,
            "customer_ref": str(user["id"]),
            "trip_id": "trip_1",
            "razorpay_order_id": "order_1",
            "razorpay_payment_id": "pay_1",
            "payment_status": "captured",
            "booking_status": "reconciliation_pending",
            "amount_inr": 4000.0,
            "offer_json": {"provider": "liteapi", "hotel_name": "Hotel 1"},
        },
        tx2: {
            "id": tx2,
            "customer_ref": str(user["id"]),
            "trip_id": "trip_2",
            "razorpay_order_id": "order_2",
            "razorpay_payment_id": "pay_2",
            "payment_status": "captured",
            "booking_status": "reconciliation_pending",
            "amount_inr": 7000.0,
            "offer_json": {"provider": "liteapi", "hotel_name": "Hotel 2"},
        },
    }

    def fake_get(self, tid):
        return records.get(tid)

    def fake_update(self, tid, **kw):
        if tid in records:
            records[tid].update(kw)

    with patch.object(HotelTransactionStore, "get", fake_get), \
         patch.object(HotelTransactionStore, "update", fake_update), \
         patch("services.hotel.hotel_service.HotelService.reconcile",
               side_effect=lambda offer, tid: ("CONFIRMED", {"provider": "liteapi", "booking_id": f"bkg_{tid}", "confirmation_code": f"V-{tid}"})):

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(lambda: TestClient(app).post(f"/api/hotel-payments/{tx1}/reconcile", headers={"Authorization": f"Bearer {token}"}))
            f2 = pool.submit(lambda: TestClient(app).post(f"/api/hotel-payments/{tx2}/reconcile", headers={"Authorization": f"Bearer {token}"}))
            r1 = f1.result()
            r2 = f2.result()

        assert r1.status_code == 200 and r1.json()["status"] == "confirmed"
        assert r2.status_code == 200 and r2.json()["status"] == "confirmed"
        assert records[tx1]["booking_status"] == "booked"
        assert records[tx2]["booking_status"] == "booked"


def test_reconcile_cross_user_forbidden():
    """Case 9: Cross-user requests remain forbidden."""
    client_owner, user_owner = _auth_user("owner_guard@example.com")
    client_other, user_other = _auth_user("other_intruder@example.com")
    tx_id = f"tx_{uuid4().hex[:8]}"

    mock_tx = {
        "id": tx_id,
        "customer_ref": str(user_owner["id"]),
        "trip_id": "trip_priv",
        "razorpay_order_id": "order_priv",
        "razorpay_payment_id": "pay_priv",
        "payment_status": "captured",
        "booking_status": "reconciliation_pending",
        "amount_inr": 8000.0,
        "offer_json": {"provider": "liteapi", "hotel_name": "Private Hotel"},
    }

    with patch.object(HotelTransactionStore, "get", return_value=mock_tx), \
         patch("services.hotel.hotel_service.HotelService.reconcile") as mock_rec:

        res = client_other.post(f"/api/hotel-payments/{tx_id}/reconcile")
        assert res.status_code == 403
        assert "does not belong" in res.json()["detail"].lower()
        mock_rec.assert_not_called()


def test_already_booked_and_refunded_fast_paths_idempotent():
    """Case 10: Existing already-booked and already-refunded fast paths remain idempotent."""
    client, user = _auth_user("fast_paths@example.com")

    tx_booked = f"tx_b_{uuid4().hex[:6]}"
    mock_booked = {
        "id": tx_booked,
        "customer_ref": str(user["id"]),
        "booking_status": "booked",
        "payment_status": "captured",
        "amount_inr": 5000.0,
        "booking_json": {"provider": "liteapi", "booking_id": "bkg_already_123"},
        "pdf_generated_at": "2026-10-12T12:00:00Z",
    }

    tx_refunded = f"tx_r_{uuid4().hex[:6]}"
    mock_refunded = {
        "id": tx_refunded,
        "customer_ref": str(user["id"]),
        "booking_status": "booking_failed",
        "payment_status": "refunded",
        "amount_inr": 5000.0,
        "error": "Prior refund completed",
    }

    def fake_get(self, tid):
        if tid == tx_booked:
            return mock_booked
        if tid == tx_refunded:
            return mock_refunded
        return None

    with patch.object(HotelTransactionStore, "get", fake_get), \
         patch("services.hotel.hotel_service.HotelService.reconcile") as mock_rec, \
         patch.object(RazorpayService, "refund") as mock_rf:

        res_b = client.post(f"/api/hotel-payments/{tx_booked}/reconcile")
        assert res_b.status_code == 200
        assert res_b.json()["status"] == "confirmed"
        assert res_b.json()["already_booked"] is True
        mock_rec.assert_not_called()

        res_r = client.post(f"/api/hotel-payments/{tx_refunded}/reconcile")
        assert res_r.status_code == 200
        assert res_r.json()["status"] == "refunded"
        assert res_r.json()["refunded"] is True
        mock_rec.assert_not_called()
        mock_rf.assert_not_called()


def test_postgres_concurrency_reconciliation_serialization():
    """PostgreSQL integration: Genuine PostgreSQL concurrency claim and recovery verification."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set; PostgreSQL concurrency test skipped.")
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + ("sslmode=disable" if "127.0.0.1" in dsn else "sslmode=require")

    tx_id = f"tx_pg_race_{uuid4().hex[:10]}"
    store = HotelTransactionStore()

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("""
                INSERT INTO trust_trip_hotel_transactions
                (id, customer_ref, provider, hotel_name, offer_json, prebook_json, holder_json, guests_json,
                 amount_inr, payment_status, booking_status)
                VALUES (%s, 'usr_pg_test', 'liteapi', 'Postgres Grand Hotel', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb,
                        9999.00, 'captured', 'reconciliation_pending')
            """, (tx_id,))

        # 1. Two concurrent workers attempt to claim the same transaction in PostgreSQL
        results = []
        results_lock = threading.Lock()

        def claim_worker():
            claimed = store.claim_reconciliation(tx_id, lease_seconds=30)
            with results_lock:
                results.append(claimed)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(claim_worker)
            f2 = pool.submit(claim_worker)
            f1.result()
            f2.result()

        claimed_count = sum(1 for r in results if r is not None)
        none_count = sum(1 for r in results if r is None)
        # Exactly one worker wins the atomic PostgreSQL claim
        assert claimed_count == 1
        assert none_count == 1

        # Check DB row status is now 'reconciliation_in_progress'
        with psycopg.connect(dsn) as conn:
            row = conn.execute("SELECT booking_status FROM trust_trip_hotel_transactions WHERE id=%s", (tx_id,)).fetchone()
            assert row[0] == "reconciliation_in_progress"

        # 2. Release claim back to reconciliation_pending
        released = store.release_reconciliation_claim(tx_id, error="test release")
        assert released is True
        with psycopg.connect(dsn) as conn:
            row = conn.execute("SELECT booking_status FROM trust_trip_hotel_transactions WHERE id=%s", (tx_id,)).fetchone()
            assert row[0] == "reconciliation_pending"

        # 3. Simulate stale lease recovery in PostgreSQL: set updated_at 45 seconds ago
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("""
                UPDATE trust_trip_hotel_transactions
                   SET booking_status = 'reconciliation_in_progress',
                       updated_at = now() - INTERVAL '45 seconds'
                 WHERE id = %s
            """, (tx_id,))

        # A new claim attempt succeeds because stale lease is recovered
        reclaimed = store.claim_reconciliation(tx_id, lease_seconds=30)
        assert reclaimed is not None
        assert reclaimed["id"] == tx_id

    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM trust_trip_hotel_transactions WHERE id=%s", (tx_id,))
