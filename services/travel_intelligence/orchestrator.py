"""Travel Intelligence Orchestrator.

Orchestrates concurrent flight and hotel searches, unified snapshot identity assignment,
session criteria deduplication, and partial failure preservation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from domain.live_offer import (
    FlightOffer,
    HotelOffer,
    OfferSnapshot,
    ProviderLimitationWarning,
    SearchSession,
)
from domain.search_criteria import (
    FlightSearchCriteria,
    HotelSearchCriteria,
    compute_criteria_hash,
)
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
from services.travel_intelligence.liteapi_adapter import LiteAPIHotelAdapter
from services.travel_intelligence.ports import (
    FlightSearchProviderPort,
    HotelSearchProviderPort,
)


class TravelIntelligenceOrchestrator:
    """Application orchestrator for real-time travel intelligence."""

    def __init__(
        self,
        flight_adapter: FlightSearchProviderPort | None = None,
        hotel_adapter: HotelSearchProviderPort | None = None,
        session_store: SearchSessionStore | None = None,
    ) -> None:
        self.flight_adapter = flight_adapter or DuffelFlightAdapter()
        self.hotel_adapter = hotel_adapter or LiteAPIHotelAdapter()
        self.session_store = session_store or SearchSessionStore()

    def search_flights_sync(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: FlightSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[FlightOffer], list[str]]:
        """Synchronous execution of flight search with snapshot persistence."""
        criteria_hash = compute_criteria_hash(criteria)
        errors: list[str] = []

        session = self.session_store.create_session(
            user_id=user_id,
            trip_id=trip_id,
            thread_id=thread_id,
            search_type="flights",
            criteria_hash=criteria_hash,
            trip_revision=trip_revision,
        )

        try:
            raw_results = self.flight_adapter._sync_search(criteria) if hasattr(self.flight_adapter, "_sync_search") else []
        except Exception as exc:
            return [], [f"Flight search provider error: {exc}"]

        offers: list[FlightOffer] = []
        for idx, (offer, sanitized_raw) in enumerate(raw_results):
            authoritative_id = uuid4()
            unified_offer = offer.model_copy(
                update={
                    "snapshot_id": authoritative_id,
                    "is_recommended": idx == 0,
                    "recommendation_rank": idx + 1,
                }
            )
            self.session_store.save_snapshot(session.session_id, unified_offer, sanitized_raw)
            offers.append(unified_offer)

        return offers, errors

    async def search_flights(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: FlightSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[FlightOffer], list[str]]:
        """Async execution of flight search with timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.search_flights_sync,
                    user_id,
                    trip_id,
                    criteria,
                    thread_id,
                    trip_revision,
                    force_refresh,
                ),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            return [], ["Flight search timed out after 25 seconds."]

    def search_hotels_sync(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: HotelSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[HotelOffer], list[str]]:
        """Synchronous execution of hotel search with snapshot persistence."""
        criteria_hash = compute_criteria_hash(criteria)
        errors: list[str] = []

        session = self.session_store.create_session(
            user_id=user_id,
            trip_id=trip_id,
            thread_id=thread_id,
            search_type="hotels",
            criteria_hash=criteria_hash,
            trip_revision=trip_revision,
        )

        try:
            raw_results = self.hotel_adapter._sync_search(criteria) if hasattr(self.hotel_adapter, "_sync_search") else []
        except Exception as exc:
            return [], [f"Hotel search provider error: {exc}"]

        offers: list[HotelOffer] = []
        for idx, (offer, sanitized_raw) in enumerate(raw_results):
            authoritative_id = uuid4()
            unified_offer = offer.model_copy(
                update={
                    "snapshot_id": authoritative_id,
                    "is_recommended": idx == 0,
                    "recommendation_rank": idx + 1,
                }
            )
            self.session_store.save_snapshot(session.session_id, unified_offer, sanitized_raw)
            offers.append(unified_offer)

        return offers, errors

    async def search_hotels(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: HotelSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[HotelOffer], list[str]]:
        """Async execution of hotel search with timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.search_hotels_sync,
                    user_id,
                    trip_id,
                    criteria,
                    thread_id,
                    trip_revision,
                    force_refresh,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            return [], ["Hotel search timed out after 20 seconds."]

    async def search_all(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        flight_criteria: FlightSearchCriteria | None,
        hotel_criteria: HotelSearchCriteria | None,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Execute flight and hotel searches concurrently using asyncio.gather."""
        tasks = []
        if flight_criteria:
            tasks.append(
                self.search_flights(
                    user_id=user_id,
                    trip_id=trip_id,
                    criteria=flight_criteria,
                    thread_id=thread_id,
                    trip_revision=trip_revision,
                    force_refresh=force_refresh,
                )
            )
        else:
            tasks.append(asyncio.sleep(0, result=([], [])))

        if hotel_criteria:
            tasks.append(
                self.search_hotels(
                    user_id=user_id,
                    trip_id=trip_id,
                    criteria=hotel_criteria,
                    thread_id=thread_id,
                    trip_revision=trip_revision,
                    force_refresh=force_refresh,
                )
            )
        else:
            tasks.append(asyncio.sleep(0, result=([], [])))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        flight_res = results[0] if not isinstance(results[0], Exception) else ([], [str(results[0])])
        hotel_res = results[1] if not isinstance(results[1], Exception) else ([], [str(results[1])])

        flight_offers, flight_errors = flight_res
        hotel_offers, hotel_errors = hotel_res

        return {
            "flight_offers": flight_offers,
            "flight_errors": flight_errors,
            "hotel_offers": hotel_offers,
            "hotel_errors": hotel_errors,
            "partial_success": bool(flight_offers and hotel_errors or hotel_offers and flight_errors),
        }
