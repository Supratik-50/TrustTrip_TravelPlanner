"""Unit tests for Phase 4 Canonical Search Criteria and Live Offer Domain Models."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightOfferView,
    FlightSegmentModel,
    FreshnessStatus,
    HotelOffer,
    HotelOfferView,
    PriceBreakdown,
    ProviderLimitationWarning,
)
from domain.search_criteria import (
    FlightSearchCriteria,
    FlightSliceCriteria,
    HotelSearchCriteria,
    compute_criteria_hash,
)


def test_flight_criteria_deterministic_hash_is_invariant_to_dict_ordering():
    """Verify criteria hash produces the exact same SHA-256 irrespective of field order."""
    c1 = FlightSearchCriteria(
        slices=[
            FlightSliceCriteria(origin_iata="DEL", destination_iata="DXB", departure_date=date(2026, 11, 10))
        ],
        adults=2,
        children=1,
        infants=1,
        cabin_class="economy",
        max_connections=2,
        currency="USD",
    )
    c2 = FlightSearchCriteria(
        slices=[
            FlightSliceCriteria(origin_iata="DEL", destination_iata="DXB", departure_date=date(2026, 11, 10))
        ],
        currency="USD",
        max_connections=2,
        cabin_class="economy",
        infants=1,
        children=1,
        adults=2,
    )
    assert compute_criteria_hash(c1) == compute_criteria_hash(c2)
    assert len(compute_criteria_hash(c1)) == 64


def test_hotel_criteria_includes_infants_and_preserves_independence():
    """Verify HotelSearchCriteria models infants separately from children."""
    c = HotelSearchCriteria(
        destination_city="Dubai",
        checkin_date=date(2026, 11, 10),
        checkout_date=date(2026, 11, 15),
        adults=2,
        children=1,
        child_ages=[7],
        infants=1,
        rooms=1,
    )
    assert c.adults == 2
    assert c.children == 1
    assert c.infants == 1
    assert c.child_ages == [7]
    assert compute_criteria_hash(c)


def test_price_breakdown_uses_exact_decimals():
    """Verify PriceBreakdown does not use floating point numbers for authoritative money."""
    pb = PriceBreakdown(
        base_amount=Decimal("450.50"),
        tax_amount=Decimal("45.05"),
        fee_amount=Decimal("10.00"),
        total_amount=Decimal("505.55"),
        currency="USD",
        inr_reference_amount=Decimal("41960.65"),
        fx_rate_applied=Decimal("83.00"),
    )
    assert isinstance(pb.base_amount, Decimal)
    assert isinstance(pb.total_amount, Decimal)
    assert pb.total_amount == Decimal("505.55")


def test_flight_offer_to_client_view_strips_provider_offer_id():
    """Verify client projection removes provider_offer_id entirely."""
    now = datetime.now(timezone.utc)
    internal_offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id="off_secret_duffel_12345",
        price=PriceBreakdown(base_amount=Decimal("300.00"), total_amount=Decimal("300.00"), currency="USD"),
        stops=0,
        segments=[
            FlightSegmentModel(
                origin_iata="DEL",
                destination_iata="BOM",
                departure_time=now,
                arrival_time=now,
                marketing_carrier_iata="AI",
                marketing_carrier_name="Air India",
                flight_number="101",
                duration_minutes=130,
            )
        ],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now,
    )
    client_view = internal_offer.to_client_view()
    assert isinstance(client_view, FlightOfferView)
    assert not hasattr(client_view, "provider_offer_id")
    assert "provider_offer_id" not in client_view.model_dump()
    assert client_view.snapshot_id == internal_offer.snapshot_id
    assert client_view.provider == "duffel"


def test_hotel_offer_to_client_view_strips_provider_offer_id():
    """Verify hotel client projection removes provider_offer_id entirely."""
    now = datetime.now(timezone.utc)
    internal_hotel = HotelOffer(
        snapshot_id=uuid4(),
        provider="liteapi",
        provider_offer_id="rate_secret_liteapi_9999",
        hotel_id="hotel_001",
        hotel_name="Grand Hyatt",
        city="Dubai",
        room_name="Deluxe King",
        board_type="Bed and Breakfast",
        refundable=True,
        price=PriceBreakdown(base_amount=Decimal("200.00"), total_amount=Decimal("200.00"), currency="USD"),
        nightly_price=Decimal("100.00"),
        nights=2,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        infants_count=1,
        retrieved_at=now,
        limitations=[
            ProviderLimitationWarning(
                provider="liteapi",
                code="INFANT_COT",
                message="Infant cot on request",
                severity="info",
            )
        ],
    )
    client_view = internal_hotel.to_client_view()
    assert isinstance(client_view, HotelOfferView)
    assert not hasattr(client_view, "provider_offer_id")
    assert "provider_offer_id" not in client_view.model_dump()
    assert client_view.snapshot_id == internal_hotel.snapshot_id
    assert len(client_view.limitations) == 1
    assert client_view.limitations[0].code == "INFANT_COT"
