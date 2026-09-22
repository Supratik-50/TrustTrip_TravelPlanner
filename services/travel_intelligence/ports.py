"""Provider Port Interfaces and Capability Declarations for Travel Intelligence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from domain.live_offer import (
    FlightOffer,
    HotelOffer,
    InfantSupportLevel,
    RevalidationResult,
)
from domain.search_criteria import FlightSearchCriteria, HotelSearchCriteria


@dataclass(frozen=True)
class ProviderCapability:
    """Explicit capabilities declared by a provider adapter."""
    provider_name: str
    infant_support: InfantSupportLevel
    supports_flexible_dates: bool = False
    supports_cancellation_policies: bool = True
    supports_multi_room: bool = False


class FlightSearchProviderPort(Protocol):
    """Port for flight search and supplier fare revalidation."""

    @property
    def provider_name(self) -> str: ...

    @property
    def capability(self) -> ProviderCapability: ...

    async def search_flights(
        self,
        criteria: FlightSearchCriteria,
    ) -> list[tuple[FlightOffer, dict[str, Any]]]:
        """Search flights and return tuple of (normalized FlightOffer, sanitized raw payload)."""
        ...

    async def revalidate_flight(
        self,
        provider_offer_id: str,
        expected_price: float | None = None,
        expected_currency: str | None = None,
    ) -> RevalidationResult:
        """Revalidate flight fare and schedule against supplier using internal provider ID."""
        ...


class HotelSearchProviderPort(Protocol):
    """Port for hotel search and rate revalidation."""

    @property
    def provider_name(self) -> str: ...

    @property
    def capability(self) -> ProviderCapability: ...

    async def search_hotels(
        self,
        criteria: HotelSearchCriteria,
    ) -> list[tuple[HotelOffer, dict[str, Any]]]:
        """Search hotels and return tuple of (normalized HotelOffer, sanitized raw payload)."""
        ...

    async def revalidate_hotel(
        self,
        provider_offer_id: str,
        expected_price: float | None = None,
        expected_currency: str | None = None,
    ) -> RevalidationResult:
        """Revalidate hotel rate and availability against supplier using internal provider ID."""
        ...
