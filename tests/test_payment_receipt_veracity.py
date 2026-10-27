import os

os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "phase-2a-cookie-auth-lifecycle-test-secret-at-least-32-bytes-long"
os.environ["AUTH_SECRET"] = "phase-2a-cookie-auth-lifecycle-test-secret-at-least-32-bytes-long"

import base64
import zlib
from uuid import uuid4
from fastapi.testclient import TestClient
import pytest

from app import app
from services.hotel_transaction_store import HotelTransactionStore
from services.flight_booking_service import FlightBookingService
from services.auth_store import UserStore
from services.auth_service import create_access_token


@pytest.fixture
def auth_client():
    user_store = UserStore()
    email = f"receipt_veracity_{uuid4().hex[:8]}@example.com"
    user = user_store.create_user(email=email, password_hash="hashed_pw")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-Requested-With": "XMLHttpRequest",
    })
    return client, user


def _extract_pdf_text(data: bytes) -> str:
    """Extract decoded text from ReportLab PDF streams using standard library."""
    idx = 0
    extracted_text = []
    while True:
        s_idx = data.find(b"stream", idx)
        if s_idx == -1:
            break
        e_idx = data.find(b"endstream", s_idx)
        if e_idx == -1:
            break
        stream_content = data[s_idx + 6:e_idx].strip()
        try:
            a85 = base64.a85decode(stream_content, adobe=True)
            decomp = zlib.decompress(a85)
            extracted_text.append(decomp.decode("latin1", errors="replace"))
        except Exception:
            try:
                decomp = zlib.decompress(stream_content)
                extracted_text.append(decomp.decode("latin1", errors="replace"))
            except Exception:
                extracted_text.append(stream_content.decode("latin1", errors="replace"))
        idx = e_idx + 9
    return " ".join(extracted_text)


def test_flight_receipt_without_razorpay_does_not_claim_razorpay_settlement(auth_client):
    """Flight bookings settled via provider balance must NOT claim Razorpay capture or gateway."""
    client, user = auth_client
    intent_id = str(uuid4())

    FlightBookingService._IN_MEMORY_BOOKINGS[intent_id] = {
        "id": intent_id,
        "user_id": user["id"],
        "trip_id": str(uuid4()),
        "provider": "duffel",
        "provider_offer_id": "off_veracity_flight_123",
        "provider_reference": "FLIGHT-PNR-VERIFY",
        "status": "COMPLETED",
        "expected_amount": 255.32,
        "currency": "USD",
        "metadata": {
            "booking_reference": "FLIGHT-PNR-VERIFY",
            "passengers": [{"given_name": "Truthful", "family_name": "Traveler"}],
            # Zero razorpay_payment_id or razorpay_order_id
        },
    }

    resp = client.get(f"/api/trips/{intent_id}/receipt.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")

    text = _extract_pdf_text(resp.content)

    # Must NOT claim Razorpay financial settlement
    assert "verifying financial settlement via Razorpay" not in text
    assert "Razorpay Payment Gateway" not in text
    assert "Razorpay Order ID" not in text
    assert "Razorpay Payment ID" not in text

    # Must truthfully state Direct Provider Settlement
    assert "Direct Provider Settlement" in text
    assert "SETTLED DIRECT VIA SUPPLIER" in text
    assert "FLIGHT-PNR-VERIFY" in text


def test_hotel_receipt_with_verified_razorpay_truthfully_states_captured(auth_client):
    """Hotel bookings with verified Razorpay payment IDs must truthfully state Razorpay capture."""
    client, user = auth_client
    hotel_store = HotelTransactionStore()
    tx_id = str(uuid4())

    hotel_store.create(
        id=tx_id,
        customer_ref=str(user["id"]),
        provider="liteapi",
        hotel_name="Grand Verified Palace",
        offer={"hotel_name": "Grand Verified Palace", "price": 5000.0, "currency": "INR"},
        prebook={"prebook_id": "pb_veracity_789"},
        holder={"firstName": "Truthful", "lastName": "Guest", "email": user["email"]},
        guests=[{"firstName": "Truthful", "lastName": "Guest"}],
        amount_inr=5000.0,
        razorpay_order_id="order_truth_verified_001",
    )
    hotel_store.update(
        tx_id,
        payment_status="captured",
        booking_status="confirmed",
        razorpay_payment_id="pay_truth_verified_999",
    )

    resp = client.get(f"/api/trips/{tx_id}/receipt.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"

    text = _extract_pdf_text(resp.content)

    # Must contain verified Razorpay details
    assert "verifying financial settlement via Razorpay" in text
    assert "Razorpay Payment Gateway" in text
    assert "order_truth_verified_001" in text
    assert "pay_truth_verified_999" in text
    assert "CAPTURED" in text


def test_hotel_receipt_with_order_only_does_not_claim_captured(auth_client):
    """Transactions with an order ID but no payment capture must NOT claim CAPTURED status."""
    client, user = auth_client
    hotel_store = HotelTransactionStore()
    tx_id = str(uuid4())

    hotel_store.create(
        id=tx_id,
        customer_ref=str(user["id"]),
        provider="liteapi",
        hotel_name="Pending Payment Inn",
        offer={"hotel_name": "Pending Payment Inn", "price": 3000.0, "currency": "INR"},
        prebook={"prebook_id": "pb_veracity_order_only"},
        holder={"firstName": "Unpaid", "lastName": "Guest", "email": user["email"]},
        guests=[{"firstName": "Unpaid", "lastName": "Guest"}],
        amount_inr=3000.0,
        razorpay_order_id="order_pending_capture_555",
    )

    resp = client.get(f"/api/trips/{tx_id}/receipt.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"

    text = _extract_pdf_text(resp.content)

    # Must NOT claim captured payment
    assert "verifying financial settlement via Razorpay" not in text
    assert "order_pending_capture_555" in text
    assert "NOT CAPTURED" in text
    assert "Pending Capture" in text
    assert "CAPTURED" not in text or "NOT CAPTURED" in text
