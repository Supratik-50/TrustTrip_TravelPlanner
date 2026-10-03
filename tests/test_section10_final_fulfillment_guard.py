"""Section 10 final supplier-fulfillment eligibility guards."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import app
from domain.booking_authorization import BookingEligibilityError


def _captured_transaction():
    return {
        "id": str(uuid4()),
        "trip_id": str(uuid4()),
        "revision_id": str(uuid4()),
        "revision_number": 3,
        "customer_ref": str(uuid4()),
        "booking_status": "payment_captured",
        "payment_status": "captured",
        "amount": 120.0,
        "offer_id": "authoritative-offer",
        "passenger_json": "[]",
        "metadata": json.dumps({"customer_final_confirmation": True}),
    }


def test_captured_flight_payment_stale_revision_blocks_supplier_and_persists_review():
    import payment_api

    tx = _captured_transaction()
    updates = []

    class Store:
        def get(self, transaction_id):
            return tx

        def claim_booking(self, transaction_id):
            return True

        def update(self, transaction_id, **values):
            updates.append(values)
            tx.update(values)

    async def run():
        with patch("payment_api._store", return_value=Store()), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible",
                   side_effect=BookingEligibilityError("revision became stale")), \
             patch("payment_api.FlightService") as supplier:
            try:
                await payment_api._fulfil_paid_transaction(tx["id"])
            except HTTPException as exc:
                assert exc.status_code == 409
            else:
                raise AssertionError("stale revision must stop fulfillment")
            supplier.assert_not_called()

    asyncio.run(run())
    assert tx["payment_status"] == "captured"
    assert tx["booking_status"] == "stale_revision_review"
    assert not any(update.get("booking_status") == "booked" for update in updates)


def test_webhook_flight_fulfillment_uses_same_final_guard():
    import payment_api
    from services.auth_store import UserStore

    tx = _captured_transaction()
    tx["razorpay_order_id"] = f"order-{uuid4().hex}"
    updates = []

    class Store:
        def get_by_razorpay_order(self, order_id):
            return tx

        def get(self, transaction_id):
            return tx

        def claim_booking(self, transaction_id):
            return True

        def update(self, transaction_id, **values):
            updates.append(values)
            tx.update(values)

        def update_metadata(self, transaction_id, **values):
            metadata = json.loads(tx["metadata"])
            metadata.update(values)
            tx["metadata"] = json.dumps(metadata)

    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": f"pay-{uuid4().hex}", "order_id": tx["razorpay_order_id"],
            "status": "captured", "currency": "INR", "amount": 12000,
        }}},
    }
    invoked = []
    actual_fulfillment = payment_api._fulfil_paid_transaction

    async def guarded_fulfillment(transaction_id):
        invoked.append(transaction_id)
        try:
            await actual_fulfillment(transaction_id)
        except HTTPException as exc:
            assert exc.status_code == 409

    with patch("payment_api._store", return_value=Store()), \
         patch("payment_api._razorpay") as razorpay, \
         patch("services.authorization_store.AuthorizationStore.assert_booking_eligible",
               side_effect=BookingEligibilityError("revision became stale")), \
         patch("payment_api._fulfil_paid_transaction", side_effect=guarded_fulfillment), \
         patch("payment_api.FlightService") as supplier:
        razorpay.return_value.verify_webhook.return_value = True
        response = TestClient(app).post(
            "/api/payments/webhook", content=json.dumps(payload),
            headers={"X-Razorpay-Signature": "valid", "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        supplier.assert_not_called()

    assert tx["payment_status"] == "captured"
    assert tx["booking_status"] == "stale_revision_review"
    assert invoked == [tx["id"]]
    assert not any(update.get("booking_status") == "booked" for update in updates)


def test_captured_flight_payment_eligible_succeeds_fulfillment_no_name_error():
    """Prove that an eligible captured payment completes supplier fulfillment with no NameError."""
    import payment_api

    tx = _captured_transaction()
    updates = []

    class Store:
        def get(self, transaction_id):
            return tx

        def claim_booking(self, transaction_id):
            return True

        def update(self, transaction_id, **values):
            updates.append(values)
            tx.update(values)

        def update_metadata(self, transaction_id, **values):
            metadata = json.loads(tx["metadata"])
            metadata.update(values)
            tx["metadata"] = json.dumps(metadata)

    async def run():
        with patch("payment_api._store", return_value=Store()), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible",
                   return_value=None), \
             patch("payment_api.FlightService") as mock_flight_cls:
            mock_svc = mock_flight_cls.return_value
            mock_svc.book_after_recheck.return_value = {
                "booked": True,
                "booking": {
                    "order_id": "ord_duffel_123",
                    "booking_reference": "PNR777",
                    "documents": [],
                    "raw": {"live_mode": False},
                },
            }
            res = await payment_api._fulfil_paid_transaction(tx["id"])
            assert res["success"] is True
            assert res["payment_captured"] is True
            assert res["booking"]["booking_reference"] == "PNR777"
            mock_svc.book_after_recheck.assert_called_once()

    asyncio.run(run())
    assert tx["payment_status"] == "captured"
    assert tx["booking_status"] == "booked"
    assert tx["booking_reference"] == "PNR777"


def test_captured_flight_payment_missing_lineage_fails_closed():
    """Prove that captured payment missing revision lineage is rejected and marked for review."""
    import payment_api

    tx = _captured_transaction()
    tx["revision_id"] = None  # Missing lineage
    updates = []

    class Store:
        def get(self, transaction_id):
            return tx

        def claim_booking(self, transaction_id):
            return True

        def update(self, transaction_id, **values):
            updates.append(values)
            tx.update(values)

    async def run():
        with patch("payment_api._store", return_value=Store()), \
             patch("payment_api.FlightService") as supplier:
            try:
                await payment_api._fulfil_paid_transaction(tx["id"])
            except HTTPException as exc:
                assert exc.status_code == 409
                assert exc.detail["code"] == "BOOKING_NOT_ELIGIBLE"
            else:
                raise AssertionError("missing lineage must reject fulfillment")
            supplier.assert_not_called()

    asyncio.run(run())
    assert tx["booking_status"] == "stale_revision_review"

