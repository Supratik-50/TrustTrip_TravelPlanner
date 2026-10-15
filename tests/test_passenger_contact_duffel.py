"""Regression tests for Passenger Contact Validation & Duffel Provider Payload.

Verifies:
1. Passenger 2 has no email or phone, while the lead passenger has valid contact details.
   The Duffel provider applies the explicitly supported provider-compatible mapping:
   companion passengers inherit the lead passenger's validated contact details.
2. The frontend labels and validation agree with backend/provider requirements:
   Lead passenger requires email and phone; companion passengers have optional contact details.
3. Missing required lead contact details are rejected before the supplier call (0 provider requests).
4. The Duffel payload contains only valid, contract-compliant contact information (E.164 phones, valid emails, zero fabricated data).
5. A provider validation error is surfaced clearly and does not falsely mark the booking as confirmed (intent transitions to FAILED).
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from services.duffel_provider import DuffelProvider, _normalize_phone
from services.flight_provider import FlightProviderError
from services.flight_booking_service import (
    FlightBookingService,
    BookingStatus,
    ProviderBookingError,
)
from services.flight_service import FlightService
from services.trip_store import TripStore
from services.search_session_store import SearchSessionStore
from decimal import Decimal
from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    FreshnessStatus,
    PriceBreakdown,
)


from services.auth_store import UserStore


def _create_mock_duffel_offer(offer_id: str = "off_test_101", num_passengers: int = 2, price: float = 12000.0) -> dict[str, Any]:
    """Create a mock Duffel offer structure with passenger IDs."""
    passengers = [{"id": f"pas_test_{i+1}", "type": "adult"} for i in range(num_passengers)]
    return {
        "id": offer_id,
        "passengers": passengers,
        "price": price,
        "currency": "INR",
        "payment_requirements": {"requires_instant_payment": True},
        "segments": [{"departure": "2026-10-16T10:00:00Z"}],
        "raw": {
            "slices": [
                {
                    "departure_date": "2026-10-16",
                    "segments": [
                        {
                            "departing_at": "2026-10-16T10:00:00Z",
                            "origin": {"iata_code": "CCU"},
                            "destination": {"iata_code": "JAI"},
                        }
                    ],
                }
            ]
        },
    }


def test_01_companion_without_contact_inherits_lead_contact_in_duffel_payload():
    """Passenger 2 has no email or phone, while Passenger 1 has valid contact details.

    The Duffel provider maps the lead contact to companion passengers without fabricating
    fake data, satisfying Duffel's per-passenger contact requirement.
    """
    provider = DuffelProvider(token="duffel_test_mock_token")
    provider.get_offer = MagicMock(return_value=_create_mock_duffel_offer("off_test_multi", 2))

    captured_requests: list[dict[str, Any]] = []

    def mock_request(method: str, path: str, **kwargs: Any):
        captured_requests.append({"method": method, "path": path, "kwargs": kwargs})
        return {
            "data": {
                "id": "ord_duffel_confirmed_123",
                "booking_reference": "DUFFEL-PNR-777",
                "status": "confirmed",
                "passengers": [
                    {"id": "pas_test_1", "name": "Rahul Sharma"},
                    {"id": "pas_test_2", "name": "Priya Sharma"},
                ],
            }
        }

    provider._request = MagicMock(side_effect=mock_request)

    # Lead has contact; Companion has empty/None contact
    passengers = [
        {
            "given_name": "Rahul",
            "family_name": "Sharma",
            "born_on": "1990-05-15",
            "gender": "m",
            "title": "mr",
            "email": "rahul.sharma@example.com",
            "phone_number": "+919876543210",
        },
        {
            "given_name": "Priya",
            "family_name": "Sharma",
            "born_on": "1993-08-20",
            "gender": "f",
            "title": "mrs",
            "email": None,
            "phone_number": None,
        },
    ]

    res = provider.book(
        offer_id="off_test_multi",
        passengers=passengers,
        payment_mode="sandbox_balance",
    )

    assert res["provider"] == "duffel"
    assert res["booking_reference"] == "DUFFEL-PNR-777"
    assert len(captured_requests) == 1
    call = captured_requests[0]
    assert call["path"] == "/air/orders"
    req_data = call["kwargs"]["json"]["data"]
    pax_payload = req_data["passengers"]

    # Passenger 1 retains their explicit contact details
    assert pax_payload[0]["email"] == "rahul.sharma@example.com"
    assert pax_payload[0]["phone_number"] == "+919876543210"

    # Passenger 2 inherits lead contact details (provider-compatible mapping)
    assert pax_payload[1]["email"] == "rahul.sharma@example.com"
    assert pax_payload[1]["phone_number"] == "+919876543210"

    # Now verify that when Passenger 2 DOES provide their own email/phone, it is preserved
    captured_requests.clear()
    passengers[1]["email"] = "priya.sharma@custom.org"
    passengers[1]["phone_number"] = "+919123456789"

    provider.book(
        offer_id="off_test_multi",
        passengers=passengers,
        payment_mode="sandbox_balance",
    )
    req_data2 = captured_requests[0]["kwargs"]["json"]["data"]
    pax2 = req_data2["passengers"][1]
    assert pax2["email"] == "priya.sharma@custom.org"
    assert pax2["phone_number"] == "+919123456789"


def test_02_frontend_labels_and_validation_agreement():
    """Verify that frontend TravelerModal static contract agrees with backend requirements:
    Lead passenger is marked required (*) for email & phone, and companions are marked (optional).
    """
    import os
    modal_path = os.path.join("frontend", "src", "components", "booking", "TravelerModal.tsx")
    assert os.path.exists(modal_path), f"File not found: {modal_path}"

    with open(modal_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Contact Email label specifies * for index 0 and (optional) for companions
    assert "Contact Email {activePassengerIndex === 0 ? <span className=\"text-red-400 font-semibold\">*</span> : <span className=\"text-[var(--text-muted)] font-normal lowercase\">(optional)</span>}" in content

    # 2. Contact Phone label specifies * for index 0 and (optional) for companions
    assert "Contact Phone {activePassengerIndex === 0 ? <span className=\"text-red-400 font-semibold\">*</span> : <span className=\"text-[var(--text-muted)] font-normal lowercase\">(optional)</span>}" in content

    # 3. Validation enforces lead email is required
    assert "Contact email is required for the lead passenger." in content

    # 4. Validation enforces lead phone is required
    assert "Contact phone number is required for the lead passenger." in content


def test_03_missing_required_lead_contact_details_rejected_before_supplier_call():
    """Missing required lead contact details (email or phone) are rejected before the supplier call.
    Zero HTTP requests must be sent to Duffel /air/orders.
    """
    provider = DuffelProvider(token="duffel_test_mock_token")
    provider.get_offer = MagicMock(return_value=_create_mock_duffel_offer("off_test_missing_lead", 1))

    mock_req = MagicMock()
    provider._request = mock_req

    # Case A: Lead passenger missing email
    with pytest.raises(FlightProviderError) as exc_email:
        provider.book(
            offer_id="off_test_missing_lead",
            passengers=[
                {
                    "given_name": "Aarav",
                    "family_name": "Patel",
                    "born_on": "1995-04-12",
                    "gender": "m",
                    "title": "mr",
                    "email": "",  # Empty email
                    "phone_number": "+919876543210",
                }
            ],
            payment_mode="sandbox_balance",
        )
    assert "Lead passenger contact email is required" in str(exc_email.value)
    assert mock_req.call_count == 0

    # Case B: Lead passenger missing phone
    with pytest.raises(FlightProviderError) as exc_phone:
        provider.book(
            offer_id="off_test_missing_lead",
            passengers=[
                {
                    "given_name": "Aarav",
                    "family_name": "Patel",
                    "born_on": "1995-04-12",
                    "gender": "m",
                    "title": "mr",
                    "email": "aarav.patel@example.com",
                    "phone_number": None,  # Missing phone
                }
            ],
            payment_mode="sandbox_balance",
        )
    assert "Lead passenger contact phone number is required" in str(exc_phone.value)
    assert mock_req.call_count == 0

    # Case C: Lead passenger invalid phone format
    with pytest.raises(FlightProviderError) as exc_invalid_phone:
        provider.book(
            offer_id="off_test_missing_lead",
            passengers=[
                {
                    "given_name": "Aarav",
                    "family_name": "Patel",
                    "born_on": "1995-04-12",
                    "gender": "m",
                    "title": "mr",
                    "email": "aarav.patel@example.com",
                    "phone_number": "123",  # Malformed phone
                }
            ],
            payment_mode="sandbox_balance",
        )
    assert "valid international phone number" in str(exc_invalid_phone.value)
    assert mock_req.call_count == 0


def test_04_duffel_payload_contains_only_valid_contract_compliant_contact_information():
    """Verify that phone numbers are normalized to E.164 and all passenger objects contain
    contract-compliant contact information without fabricated values.
    """
    provider = DuffelProvider(token="duffel_test_mock_token")
    provider.get_offer = MagicMock(return_value=_create_mock_duffel_offer("off_test_e164", 3))

    captured_requests: list[dict[str, Any]] = []
    provider._request = MagicMock(side_effect=lambda method, path, **kwargs: (
        captured_requests.append(kwargs),
        {"data": {"id": "ord_1", "status": "confirmed", "booking_reference": "REF123"}}
    )[1])

    passengers = [
        # Lead: 10-digit national number with spaces (e.g. '98765 43210') -> normalizes to +919876543210
        {
            "given_name": "Vikram",
            "family_name": "Mehta",
            "born_on": "1988-02-14",
            "gender": "m",
            "title": "mr",
            "email": "vikram@example.com",
            "phone_number": "98765 43210",
        },
        # Companion 1: No phone, no email -> inherits lead
        {
            "given_name": "Ananya",
            "family_name": "Mehta",
            "born_on": "1991-06-25",
            "gender": "f",
            "title": "mrs",
            "email": "",
            "phone_number": "",
        },
        # Companion 2: International number with dashes ('+1-555-0199') -> normalizes to +15550199
        {
            "given_name": "Dev",
            "family_name": "Mehta",
            "born_on": "2016-11-03",
            "gender": "m",
            "title": "mr",
            "email": "dev.kid@family.org",
            "phone_number": "+1-555-0199",
        },
    ]

    provider.book(
        offer_id="off_test_e164",
        passengers=passengers,
        payment_mode="sandbox_balance",
    )

    pax_list = captured_requests[0]["json"]["data"]["passengers"]
    assert len(pax_list) == 3

    # Lead
    assert pax_list[0]["phone_number"] == "+919876543210"
    assert pax_list[0]["email"] == "vikram@example.com"

    # Companion 1 (inherits lead)
    assert pax_list[1]["phone_number"] == "+919876543210"
    assert pax_list[1]["email"] == "vikram@example.com"
    # Never fabricated fake values
    assert "dummy" not in pax_list[1]["email"]
    assert "placeholder" not in pax_list[1]["email"]

    # Companion 2 (custom normalized)
    assert pax_list[2]["phone_number"] == "+15550199"
    assert pax_list[2]["email"] == "dev.kid@family.org"


@pytest.mark.anyio
async def test_05_provider_validation_error_surfaced_clearly_without_false_confirmation():
    """A Duffel validation error (e.g. 422 rejected passenger details) is surfaced clearly
    and does NOT falsely mark the booking as confirmed (intent transitions to FAILED).
    """
    user_store = UserStore()
    user = user_store.create_user(email=f"duffel_pax_{uuid4().hex[:6]}@example.com", password_hash="hash")
    user_id = user["id"]
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user_id, title="Duffel Error Test Trip", destination="Jaipur")

    # Setup session and snapshot
    session_store = SearchSessionStore()
    session = session_store.create_session(
        user_id=user["id"],
        trip_id=trip["id"],
        thread_id=None,
        search_type="flights",
        criteria_hash="hash_duffel_error_test",
        trip_revision=1,
    )
    snapshot_id = uuid4()
    provider_offer_id = "off_duffel_error_sim"
    now = datetime.now(timezone.utc)
    pb = PriceBreakdown(
        base_amount=Decimal("6500.00"),
        tax_amount=Decimal("0.00"),
        fee_amount=Decimal("0.00"),
        total_amount=Decimal("6500.00"),
        currency="INR",
        inr_reference_amount=Decimal("6500.00"),
    )
    seg = FlightSegmentModel(
        origin_iata="DEL",
        destination_iata="JAI",
        departure_time=now,
        arrival_time=now,
        marketing_carrier_iata="AI",
        marketing_carrier_name="Air India",
        flight_number="AI 101",
        duration_minutes=60,
        cabin_class="economy",
    )
    domain_offer = FlightOffer(
        snapshot_id=snapshot_id,
        provider="duffel",
        provider_offer_id=provider_offer_id,
        price=pb,
        stops=0,
        segments=[seg],
        passenger_count=1,
        availability=AvailabilityStatus.AVAILABLE,
        freshness=FreshnessStatus.FRESH,
        retrieved_at=now,
        expires_at=now + timedelta(hours=2),
    )
    raw_payload = {"id": provider_offer_id, "slices": [{"departure_date": "2026-10-16", "segments": [{"departing_at": "2026-10-16T10:00:00Z"}]}]}
    session_store.save_snapshot(session.session_id, domain_offer, raw_payload)

    # Setup mock Duffel provider that fails with HTTP 422
    mock_duffel = DuffelProvider(token="duffel_test_mock_token")
    mock_duffel.get_offer = MagicMock(return_value=_create_mock_duffel_offer(provider_offer_id, 1, price=6500.0))
    mock_duffel.find_order_by_offer = MagicMock(return_value=None)

    # Simulate Duffel 422 error response
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.status_code = 422
    mock_response.json = MagicMock(return_value={
        "errors": [
            {
                "code": "invalid_phone_number",
                "message": "Phone number country code is invalid for operating carrier.",
                "source": {"field": "phone_number"},
            }
        ]
    })

    with patch("requests.request", return_value=mock_response):
        flight_svc = FlightService(provider=mock_duffel)
        booking_svc = FlightBookingService(flight_service=flight_svc)

        # 1. Select offer
        passengers = [
            {
                "given_name": "Rohan",
                "family_name": "Verma",
                "born_on": "1992-07-10",
                "gender": "m",
                "title": "mr",
                "email": "rohan.verma@example.com",
                "phone_number": "+919876543210",
            }
        ]
        sel = await booking_svc.select_offer(
            user_id=user_id,
            trip_id=trip["id"],
            offer_id=snapshot_id,
            passengers=passengers,
        )
        b_id = sel.booking_id

        # 2. Revalidate & approve
        mock_duffel.recheck = MagicMock(return_value={"id": provider_offer_id, "price": 6500.0, "currency": "INR", "available": True})
        await booking_svc.revalidate_offer(user_id=user_id, booking_id=b_id)
        await booking_svc.record_approval(user_id=user_id, booking_id=b_id, approved=True)

        # 3. Confirm booking -> Duffel throws 422
        with pytest.raises(ProviderBookingError) as exc_info:
            await booking_svc.confirm_booking(
                user_id=user_id,
                booking_id=b_id,
                final_confirmation=True,
                payment_mode="sandbox_balance",
            )

        err_msg = str(exc_info.value)
        assert "Duffel rejected the passenger details" in err_msg
        assert "phone_number" in err_msg

        # 4. Verify intent is strictly FAILED and NOT completed
        intent = booking_svc._get_intent(b_id, user_id)
        assert intent["status"] == BookingStatus.FAILED.value
        assert "FlightProviderError" in intent["metadata"]["failure_reason"]
