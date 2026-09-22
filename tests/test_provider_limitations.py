"""Tests for Provider Limitation Warning Propagation."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from domain.live_offer import (
    HotelOffer,
    LivePlanView,
    PriceBreakdown,
    ProviderLimitationWarning,
)
from domain.search_criteria import HotelSearchCriteria
from services.travel_intelligence.liteapi_adapter import LiteAPIHotelAdapter


def test_liteapi_adapter_attaches_infant_limitation_warning():
    """Verify LiteAPI adapter attaches a cot warning when infants are requested."""
    criteria = HotelSearchCriteria(
        destination_city="Paris",
        checkin_date=date(2026, 12, 1),
        checkout_date=date(2026, 12, 5),
        adults=2,
        children=0,
        infants=1,
    )

    mock_raw_offers = [
        {
            "id": "liteapi:rate_1",
            "provider_offer_id": "rate_1",
            "hotel_id": "h1",
            "hotel_name": "Paris Hotel",
            "price": 300.0,
            "currency": "USD",
            "rooms": 1,
            "stars": 4,
            "rating": 8.5,
            "room_name": "Superior Room",
            "board_name": "Breakfast Included",
            "refundable": True,
        }
    ]

    mock_provider = MagicMock()
    mock_provider.search.return_value = mock_raw_offers

    adapter = LiteAPIHotelAdapter(provider=mock_provider)
    results = adapter._sync_search(criteria)

    assert len(results) == 1
    offer, _ = results[0]

    assert offer.infants_count == 1
    assert len(offer.limitations) == 1
    limitation = offer.limitations[0]
    assert limitation.code == "INFANT_COT_REQUESTED"
    assert "1 infant(s) included via supplementary cot request" in limitation.message

    # Verify client view also retains the limitation
    client_view = offer.to_client_view()
    assert len(client_view.limitations) == 1
    assert client_view.limitations[0].code == "INFANT_COT_REQUESTED"

    # Verify LivePlanView propagates warnings
    live_plan = LivePlanView(
        plan_id=uuid4(),
        trip_id=uuid4(),
        revision=1,
        generated_itinerary_markdown="# Paris Trip",
        flight_offers=[],
        hotel_offers=[client_view],
        warnings=[limitation.message],
    )
    assert len(live_plan.warnings) == 1
    assert "cot request" in live_plan.warnings[0]
