"""Integration tests for TravelIntelligenceOrchestrator."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    FreshnessStatus,
    HotelOffer,
    InfantSupportLevel,
    PriceBreakdown,
    RevalidationResult,
    RevalidationStatus,
)
from domain.search_criteria import (
    FlightSearchCriteria,
    FlightSliceCriteria,
    HotelSearchCriteria,
)
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.orchestrator import TravelIntelligenceOrchestrator
from services.travel_intelligence.ports import (
    FlightSearchProviderPort,
    HotelSearchProviderPort,
    ProviderCapability,
)


class MockFlightAdapter(FlightSearchProviderPort):
    provider_name = "mock_flight"
    capability = ProviderCapability(
        provider_name="mock_flight",
        infant_support=InfantSupportLevel.NATIVE,
    )

    def __init__(self, delay: float = 0.01, should_fail: bool = False):
        self.delay = delay
        self.should_fail = should_fail

    def _sync_search(self, criteria: FlightSearchCriteria) -> list[tuple[FlightOffer, dict[str, Any]]]:
        import time
        time.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Flight supplier connection error")
        now = datetime.now(timezone.utc)
        offer = FlightOffer(
            snapshot_id=uuid4(),
            provider="mock_flight",
            provider_offer_id="mock_off_1",
            price=PriceBreakdown(base_amount=Decimal("200.00"), total_amount=Decimal("200.00"), currency="USD"),
            stops=0,
            segments=[],
            passenger_count=1,
            retrieved_at=now,
            expires_at=now,
        )
        return [(offer, {"raw": "data"})]

    async def search_flights(self, criteria: FlightSearchCriteria) -> list[tuple[FlightOffer, dict[str, Any]]]:
        await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Flight supplier connection error")
        return self._sync_search(criteria)

    async def revalidate_flight(self, provider_offer_id: str, expected_price=None, expected_currency=None) -> RevalidationResult:
        return RevalidationResult(
            revalidation_id=uuid4(),
            original_snapshot_id=uuid4(),
            status=RevalidationStatus.CONFIRMED_UNCHANGED,
            verified_at=datetime.now(timezone.utc),
            price_changed=False,
            original_price=Decimal(str(expected_price or 0)),
            current_price=Decimal(str(expected_price or 0)),
            currency=expected_currency or "USD",
        )


class MockHotelAdapter(HotelSearchProviderPort):
    provider_name = "mock_hotel"
    capability = ProviderCapability(
        provider_name="mock_hotel",
        infant_support=InfantSupportLevel.SUPPLEMENT_ON_REQUEST,
    )

    def __init__(self, delay: float = 0.01, should_fail: bool = False):
        self.delay = delay
        self.should_fail = should_fail

    def _sync_search(self, criteria: HotelSearchCriteria) -> list[tuple[HotelOffer, dict[str, Any]]]:
        import time
        time.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Hotel supplier timeout")
        now = datetime.now(timezone.utc)
        offer = HotelOffer(
            snapshot_id=uuid4(),
            provider="mock_hotel",
            provider_offer_id="mock_hotel_1",
            hotel_id="h1",
            hotel_name="Grand Hotel",
            city="Dubai",
            room_name="Deluxe",
            board_type="Room Only",
            refundable=True,
            price=PriceBreakdown(base_amount=Decimal("150.00"), total_amount=Decimal("150.00"), currency="USD"),
            nightly_price=Decimal("75.00"),
            nights=2,
            rooms_count=1,
            adults_count=2,
            children_count=0,
            infants_count=0,
            retrieved_at=now,
        )
        return [(offer, {"raw": "hotel_data"})]

    async def search_hotels(self, criteria: HotelSearchCriteria) -> list[tuple[HotelOffer, dict[str, Any]]]:
        await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Hotel supplier timeout")
        return self._sync_search(criteria)

    async def revalidate_hotel(self, provider_offer_id: str, expected_price=None, expected_currency=None) -> RevalidationResult:
        return RevalidationResult(
            revalidation_id=uuid4(),
            original_snapshot_id=uuid4(),
            status=RevalidationStatus.CONFIRMED_UNCHANGED,
            verified_at=datetime.now(timezone.utc),
            price_changed=False,
            original_price=Decimal(str(expected_price or 0)),
            current_price=Decimal(str(expected_price or 0)),
            currency=expected_currency or "USD",
        )


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()


def test_orchestrator_concurrent_search_success():
    """Verify orchestrator executes flight and hotel searches concurrently and unifies snapshot IDs."""
    async def _run():
        flight_adapter = MockFlightAdapter(delay=0.05)
        hotel_adapter = MockHotelAdapter(delay=0.05)
        store = SearchSessionStore()
        orchestrator = TravelIntelligenceOrchestrator(
            flight_adapter=flight_adapter,
            hotel_adapter=hotel_adapter,
            session_store=store,
        )

        user_id = uuid4()
        trip_id = uuid4()

        flight_criteria = FlightSearchCriteria(
            slices=[FlightSliceCriteria(origin_iata="DEL", destination_iata="DXB", departure_date=date(2026, 11, 10))],
        )
        hotel_criteria = HotelSearchCriteria(
            destination_city="Dubai",
            checkin_date=date(2026, 11, 10),
            checkout_date=date(2026, 11, 12),
        )

        start = asyncio.get_event_loop().time()
        result = await orchestrator.search_all(
            user_id=user_id,
            trip_id=trip_id,
            flight_criteria=flight_criteria,
            hotel_criteria=hotel_criteria,
        )
        elapsed = asyncio.get_event_loop().time() - start

        # Concurrent execution should complete in ~max(delay, delay), not sum
        assert elapsed < 0.15

        flight_offers = result["flight_offers"]
        hotel_offers = result["hotel_offers"]
        assert len(flight_offers) == 1
        assert len(hotel_offers) == 1
        assert not result["partial_success"]

        # Verify unified snapshot persistence
        flight_snap = store.get_snapshot(flight_offers[0].snapshot_id, user_id)
        assert flight_snap is not None
        assert flight_snap.snapshot_id == flight_offers[0].snapshot_id

        hotel_snap = store.get_snapshot(hotel_offers[0].snapshot_id, user_id)
        assert hotel_snap is not None
        assert hotel_snap.snapshot_id == hotel_offers[0].snapshot_id

    asyncio.run(_run())


def test_orchestrator_partial_success_when_one_provider_fails():
    """Verify orchestrator preserves flight results when hotel provider fails."""
    async def _run():
        flight_adapter = MockFlightAdapter(delay=0.01)
        hotel_adapter = MockHotelAdapter(delay=0.01, should_fail=True)
        store = SearchSessionStore()
        orchestrator = TravelIntelligenceOrchestrator(
            flight_adapter=flight_adapter,
            hotel_adapter=hotel_adapter,
            session_store=store,
        )

        user_id = uuid4()
        trip_id = uuid4()

        flight_criteria = FlightSearchCriteria(
            slices=[FlightSliceCriteria(origin_iata="DEL", destination_iata="DXB", departure_date=date(2026, 11, 10))],
        )
        hotel_criteria = HotelSearchCriteria(
            destination_city="Dubai",
            checkin_date=date(2026, 11, 10),
            checkout_date=date(2026, 11, 12),
        )

        result = await orchestrator.search_all(
            user_id=user_id,
            trip_id=trip_id,
            flight_criteria=flight_criteria,
            hotel_criteria=hotel_criteria,
        )

        assert result["partial_success"] is True
        assert len(result["flight_offers"]) == 1
        assert len(result["hotel_offers"]) == 0
        assert len(result["hotel_errors"]) == 1
        assert "Hotel supplier timeout" in result["hotel_errors"][0]

    asyncio.run(_run())
