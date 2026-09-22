"""Integration tests for Hotel Booking via verified snapshots and rate revalidation gates."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import (
    HotelOffer,
    PriceBreakdown,
    RevalidationResult,
    RevalidationStatus,
)
from services.auth_store import UserStore
from services.hotel_transaction_store import HotelTransactionStore
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


def _create_hotel_snapshot(user_id):
    store = SearchSessionStore()
    trip_id = uuid4()
    session = store.create_session(user_id=user_id, trip_id=trip_id, thread_id="t1", search_type="hotels", criteria_hash="h1")
    now = datetime.now(timezone.utc)
    offer = HotelOffer(
        snapshot_id=uuid4(),
        provider="liteapi",
        provider_offer_id="off_hotel_1",
        hotel_id="lp_123",
        hotel_name="Grand Hyatt Dubai",
        city="Dubai",
        room_name="Deluxe King Room",
        board_type="Breakfast Included",
        refundable=True,
        price=PriceBreakdown(base_amount=Decimal("350.00"), total_amount=Decimal("350.00"), currency="USD", inr_reference_amount=Decimal("29750.00")),
        nightly_price=Decimal("175.00"),
        nights=2,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        infants_count=0,
        retrieved_at=now,
    )
    snapshot = store.save_snapshot(session.session_id, offer, raw_payload={"rate": 350})
    return snapshot


def test_hotel_checkout_from_snapshot_success():
    """Verify hotel checkout order is created from a verified snapshot with rate validation."""
    client, user_id = _auth_user("hotel_checkout@trusttrip.com")
    snapshot = _create_hotel_snapshot(user_id)

    mock_rev = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=snapshot.snapshot_id,
        status=RevalidationStatus.CONFIRMED_UNCHANGED,
        verified_at=datetime.now(timezone.utc),
        price_changed=False,
        original_price=Decimal("350.00"),
        current_price=Decimal("350.00"),
        currency="USD",
    )

    with patch("services.planner_service.LiteAPIHotelAdapter.revalidate_hotel", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev
        res = client.post(
            "/api/hotel-payments/create-order-from-snapshot",
            json={
                "snapshot_id": str(snapshot.snapshot_id),
                "holder": {"firstName": "John", "lastName": "Doe", "email": "john@example.com", "phone": "+14155552671"},
                "guests": [{"firstName": "John", "lastName": "Doe", "is_child": False}],
                "final_confirmation": True,
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "transaction_id" in data
    assert "razorpay_order_id" in data
    assert data["hotel_name"] == "Grand Hyatt Dubai"


def test_hotel_checkout_from_snapshot_rejects_cross_tenant():
    """Verify that User B cannot initiate checkout for User A's hotel snapshot."""
    client_a, user_a = _auth_user("hotel_owner@trusttrip.com")
    client_b, user_b = _auth_user("hotel_intruder@trusttrip.com")

    snapshot_a = _create_hotel_snapshot(user_a)

    res = client_b.post(
        "/api/hotel-payments/create-order-from-snapshot",
        json={
            "snapshot_id": str(snapshot_a.snapshot_id),
            "holder": {"firstName": "Intruder", "lastName": "Guy", "email": "bad@example.com", "phone": "+14155550000"},
            "guests": [],
            "final_confirmation": True,
        },
    )
    assert res.status_code == 403


def test_hotel_checkout_rate_change_returns_409():
    """Verify that when rate increases during pre-booking check, 409 is returned with successor snapshot."""
    client, user_id = _auth_user("hotel_rate_drift@trusttrip.com")
    snapshot = _create_hotel_snapshot(user_id)

    now = datetime.now(timezone.utc)
    mock_rev = RevalidationResult(
        revalidation_id=uuid4(),
        original_snapshot_id=snapshot.snapshot_id,
        status=RevalidationStatus.PRICE_CHANGED,
        verified_at=now,
        price_changed=True,
        original_price=Decimal("350.00"),
        current_price=Decimal("380.00"),
        currency="USD",
        details="Hotel rate updated by supplier to 380 USD",
    )

    with patch("services.planner_service.LiteAPIHotelAdapter.revalidate_hotel", new_callable=AsyncMock) as mock_r:
        mock_r.return_value = mock_rev
        res = client.post(
            "/api/hotel-payments/create-order-from-snapshot",
            json={
                "snapshot_id": str(snapshot.snapshot_id),
                "holder": {"firstName": "John", "lastName": "Doe", "email": "john@example.com", "phone": "+14155552671"},
                "guests": [{"firstName": "John", "lastName": "Doe", "is_child": False}],
                "final_confirmation": True,
            },
        )

    assert res.status_code == 409
    data = res.json()["detail"]
    assert data["error"] == "Fare has changed"
    assert data["original_snapshot_id"] == str(snapshot.snapshot_id)
    assert data["subsequent_snapshot_id"] != str(snapshot.snapshot_id)


def test_hotel_booking_succeeds_even_if_pdf_fails():
    """Verify that if supplier booking succeeds but PDF generation fails, booking remains BOOKED."""
    client, user_id = _auth_user("hotel_pdf_failure@trusttrip.com")
    store = HotelTransactionStore()
    tx_id = f"htx_{uuid4().hex[:12]}"
    store.create(
        id=tx_id,
        customer_ref=str(user_id),
        provider="liteapi",
        hotel_name="Grand Hyatt",
        offer={"provider_offer_id": "off_1", "provider": "liteapi", "hotel_name": "Grand Hyatt"},
        prebook={"offer_id": "off_1"},
        holder={"first_name": "John", "last_name": "Doe"},
        guests=[],
        amount_inr=15000.0,
        razorpay_order_id="order_pdf_fail_1",
    )

    with patch("services.razorpay_service.RazorpayService.verify_checkout_signature", return_value=True), \
         patch("services.razorpay_service.RazorpayService.fetch_payment", return_value={"status": "captured"}), \
         patch("services.hotel.hotel_service.HotelService.book", return_value={"booking_id": "b_12345", "status": "confirmed"}), \
         patch("hotel_payment_api.build_hotel_booking_pdf", side_effect=RuntimeError("ReportLab engine crashed")):

        res = client.post(
            "/api/hotel-payments/verify-and-book",
            json={
                "transaction_id": tx_id,
                "razorpay_order_id": "order_pdf_fail_1",
                "razorpay_payment_id": "pay_pdf_fail_1",
                "razorpay_signature": "sig_valid",
                "offer": {"provider_offer_id": "off_1"},
                "prebook": {"offer_id": "off_1"},
                "holder": {"first_name": "John", "last_name": "Doe"},
                "guests": [],
                "final_confirmation": True,
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["booking"]["booking_id"] == "b_12345"
    assert data["pdf_available"] is False
    assert "requires_refund" not in data or data.get("requires_refund") is False

    tx = store.get(tx_id)
    assert tx["booking_status"] == "booked"
    assert tx["error"] is None


def test_hotel_booking_atomic_concurrency_and_idempotent_retry():
    """Verify atomic compare-and-set prevents duplicate supplier booking invocations on retries."""
    client, user_id = _auth_user("hotel_atomic@trusttrip.com")
    store = HotelTransactionStore()
    tx_id = f"htx_{uuid4().hex[:12]}"
    store.create(
        id=tx_id,
        customer_ref=str(user_id),
        provider="liteapi",
        hotel_name="Grand Hyatt",
        offer={"provider_offer_id": "off_2", "provider": "liteapi", "hotel_name": "Grand Hyatt"},
        prebook={"offer_id": "off_2"},
        holder={"first_name": "Alice", "last_name": "Smith"},
        guests=[],
        amount_inr=20000.0,
        razorpay_order_id="order_atomic_1",
    )

    with patch("services.razorpay_service.RazorpayService.verify_checkout_signature", return_value=True), \
         patch("services.razorpay_service.RazorpayService.fetch_payment", return_value={"status": "captured"}), \
         patch("services.hotel.hotel_service.HotelService.book", return_value={"booking_id": "b_atomic_99", "status": "confirmed"}) as mock_book, \
         patch("hotel_payment_api.build_hotel_booking_pdf", return_value=b"%PDF-1.4 mock"):

        payload = {
            "transaction_id": tx_id,
            "razorpay_order_id": "order_atomic_1",
            "razorpay_payment_id": "pay_atomic_1",
            "razorpay_signature": "sig_valid",
            "offer": {"provider_offer_id": "off_2"},
            "prebook": {"offer_id": "off_2"},
            "holder": {"first_name": "Alice", "last_name": "Smith"},
            "guests": [],
            "final_confirmation": True,
        }

        # First request succeeds and books
        res1 = client.post("/api/hotel-payments/verify-and-book", json=payload)
        assert res1.status_code == 200
        assert res1.json()["success"] is True

        # Second retry request hits idempotent short-circuit
        res2 = client.post("/api/hotel-payments/verify-and-book", json=payload)
        assert res2.status_code == 200
        assert res2.json()["success"] is True
        assert res2.json().get("already_booked") is True

        # Supplier booking was called EXACTLY once
        assert mock_book.call_count == 1
