"""Flight Booking and Handoff Service for TrustTrip Travel Planner."""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from domain.booking import FlightBookingDestination
from domain.live_offer import RevalidationStatus
from services.planner_service import PlannerService
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter


class FareChangedException(Exception):
    """Raised when revalidation detects price or schedule change during handoff."""

    def __init__(
        self,
        original_snapshot_id: UUID,
        subsequent_snapshot_id: UUID,
        new_price: Decimal,
        currency: str,
        details: str,
    ) -> None:
        self.original_snapshot_id = original_snapshot_id
        self.subsequent_snapshot_id = subsequent_snapshot_id
        self.new_price = new_price
        self.currency = currency
        self.details = details
        super().__init__(f"Flight fare changed to {currency} {new_price}: {details}")


class OfferWithdrawnException(Exception):
    """Raised when supplier withdraws flight offer inventory."""

    def __init__(self, snapshot_id: UUID, details: str) -> None:
        self.snapshot_id = snapshot_id
        self.details = details
        super().__init__(f"Flight offer withdrawn: {details}")


class FlightBookingService:
    """Orchestrates flight selection, tenant authorization, revalidation gate, and external destination resolution."""

    def __init__(
        self,
        store: SearchSessionStore | None = None,
        planner: PlannerService | None = None,
        duffel_adapter: DuffelFlightAdapter | None = None,
    ) -> None:
        self.store = store or SearchSessionStore()
        self.planner = planner or PlannerService()
        self.duffel_adapter = duffel_adapter or DuffelFlightAdapter()

    async def select_and_handoff_flight(
        self,
        snapshot_id: UUID,
        authenticated_user_id: UUID | str,
    ) -> FlightBookingDestination:
        """Select a flight offer and resolve its legitimate external booking destination.

        Does not require whole-trip APPROVED status.
        Validates tenant ownership, enforces pre-handoff revalidation, and resolves destination.
        """
        # 1. Snapshot tenant ownership check
        snapshot = self.store.get_snapshot(snapshot_id, authenticated_user_id)
        if not snapshot:
            raise PermissionError(
                f"Access denied: Snapshot '{snapshot_id}' does not exist or does not belong to the user."
            )

        if snapshot.offer_type != "flight":
            raise ValueError(f"Snapshot '{snapshot_id}' is not a flight offer.")

        # 2. Revalidate before handoff
        result = await self.planner.revalidate_offer(snapshot_id, authenticated_user_id)
        if result.status in {RevalidationStatus.PRICE_CHANGED, RevalidationStatus.SCHEDULE_CHANGED}:
            subsequent_id = result.subsequent_snapshot_id or snapshot_id
            raise FareChangedException(
                original_snapshot_id=snapshot_id,
                subsequent_snapshot_id=subsequent_id,
                new_price=result.current_price,
                currency=result.currency,
                details=result.details,
            )
        if result.status == RevalidationStatus.OFFER_WITHDRAWN:
            raise OfferWithdrawnException(snapshot_id=snapshot_id, details=result.details)

        # 3. Resolve destination using internal raw payload
        raw_payload = self.store.get_raw_payload(snapshot_id, authenticated_user_id)
        return self.duffel_adapter.resolve_booking_destination(
            snapshot_id=snapshot.snapshot_id,
            offer=snapshot.normalized_offer,
            raw_payload=raw_payload,
        )
