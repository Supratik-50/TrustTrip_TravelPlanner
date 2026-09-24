"""Provider port protocol for flight booking, revalidation, and checkout handoff.

Decouples core business logic from Duffel or any future upstream GDS/aggregator.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from pydantic import BaseModel, ConfigDict, Field
from domain.live_offer import RevalidationResult, RevalidationStatus
from domain.booking_lifecycle import BookingProviderUnavailable, BookingProviderRateLimited


class ProviderOfferVerification(BaseModel):
    """Detailed live verification data parsed from upstream provider."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_offer_id: str
    status: RevalidationStatus
    available: bool = True
    total_amount: Decimal
    currency: str
    origin: str | None = None
    destination: str | None = None
    departure_time: datetime | None = None
    arrival_time: datetime | None = None
    passenger_count: int | None = None
    passengers: list[dict[str, Any]] | None = None
    cabin_class: str | None = None
    included_baggage: list[dict[str, Any]] | None = None
    baggage_summary: str | None = None
    conditions: dict[str, Any] | None = None
    fare_rules_summary: str | None = None
    slices: list[dict[str, Any]] | None = None
    expires_at: datetime | None = None
    raw_offer: dict[str, Any] = Field(default_factory=dict)
    details: str = ""

    @property
    def price_changed(self) -> bool:
        return self.status == RevalidationStatus.PRICE_CHANGED

    @property
    def schedule_changed(self) -> bool:
        return self.status == RevalidationStatus.SCHEDULE_CHANGED

    @property
    def current_price(self) -> Decimal:
        return self.total_amount

    @property
    def original_price(self) -> Decimal:
        return self.total_amount


class FlightBookingProviderPort(Protocol):
    """Abstract port for flight offer revalidation, handoff session creation, and ordering."""

    @property
    def provider_name(self) -> str:
        """Normalized provider name (e.g. 'duffel')."""
        ...

    async def revalidate_offer(
        self,
        provider_offer_id: str,
        expected_price: Decimal | None = None,
        expected_currency: str | None = None,
        call_timeout: float = 15.0,
    ) -> ProviderOfferVerification:
        """Verify live availability, price, routing, schedule, cabin, and fare attributes with supplier."""
        ...

    async def create_handoff_session(
        self,
        provider_offer_id: str,
        passengers: list[dict[str, Any]],
        success_url: str,
        failure_url: str,
        abandonment_url: str,
        call_timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Create a hosted checkout session (e.g. Duffel Links).

        Returns:
            {
                'handoff_url': str,
                'provider_reference': str,
                'expires_at': datetime,
            }
        """
        ...

    async def create_api_order(
        self,
        provider_offer_id: str,
        passengers: list[dict[str, Any]],
        payment_mode: str,
        payment_details: dict[str, Any] | None = None,
        call_timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Execute a direct API order creation (e.g. sandbox balance demo or instant order)."""
        ...

    async def get_order_status(
        self,
        provider_order_id: str,
        call_timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Fetch current order, ticketing, and PNR status from provider."""
        ...

    async def cancel_order(
        self,
        provider_order_id: str,
        call_timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Request cancellation quote and confirm cancellation for an order."""
        ...
