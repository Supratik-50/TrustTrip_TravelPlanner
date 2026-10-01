"""Domain models for Section 5A Flight Search Backend Contract.

Provides normalized, provider-agnostic flight representations:
- NormalizedFlightSegment: individual segment details.
- NormalizedFlightOffer: client-facing normalized offer with internal snapshot ID shielding.
- FlightSearchMetadata: canonical search query metadata.
- FlightSearchResponse: stable top-level API response.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class NormalizedFlightSegment(BaseModel):
    """Normalized individual flight segment."""
    model_config = ConfigDict(extra="ignore")

    origin: str = Field(description="Origin 3-letter IATA airport code")
    origin_name: str | None = Field(default=None, description="Origin airport or city name")
    destination: str = Field(description="Destination 3-letter IATA airport code")
    destination_name: str | None = Field(default=None, description="Destination airport or city name")
    departure_time: str = Field(description="Departure timestamp (ISO 8601)")
    arrival_time: str = Field(description="Arrival timestamp (ISO 8601)")
    duration: str | None = Field(default=None, description="Segment duration (e.g. '2h 30m')")
    carrier: str | None = Field(default=None, description="Airline or carrier name")
    carrier_code: str | None = Field(default=None, description="2-letter IATA airline code")
    flight_number: str | None = Field(default=None, description="Marketing flight number")


class NormalizedFlightOffer(BaseModel):
    """Normalized client-facing flight offer.

    Strictly shields provider internal IDs and raw payloads from the client.
    Exposes a stable, provider-neutral model.
    """
    model_config = ConfigDict(extra="ignore")

    offer_id: str = Field(description="Internal snapshot UUID for selection and booking")
    provider: str = Field(default="duffel", description="Source provider identifier")
    origin: str = Field(description="Outbound origin 3-letter IATA code")
    destination: str = Field(description="Outbound destination 3-letter IATA code")
    departure_time: str = Field(description="Outbound departure timestamp (ISO 8601)")
    arrival_time: str = Field(description="Outbound arrival timestamp (ISO 8601)")
    return_departure_time: str | None = Field(default=None, description="Return departure timestamp for round trips")
    return_arrival_time: str | None = Field(default=None, description="Return arrival timestamp for round trips")
    duration: str | None = Field(default=None, description="Total travel duration")
    stops: int = Field(default=0, description="Total number of layovers/stops")
    carrier: str | None = Field(default=None, description="Primary airline or carrier name")
    carrier_code: str | None = Field(default=None, description="Primary airline 2-letter IATA code")
    cabin_class: str = Field(default="economy", description="Cabin class")
    price: float = Field(description="Total price in source provider currency")
    currency: str = Field(description="Source provider currency code (e.g. 'INR', 'USD')")
    price_inr: float | None = Field(default=None, description="Converted or native INR price")
    price_per_traveler: float | None = Field(default=None, description="Per-traveler price in source currency")
    refundable: bool | None = Field(default=None, description="Refundability indicator when available")
    changeable: bool | None = Field(default=None, description="Changeability indicator when available")
    expires_at: str | None = Field(default=None, description="Offer expiration timestamp (ISO 8601)")
    segments: list[NormalizedFlightSegment] = Field(default_factory=list, description="Ordered flight segments")


class FlightSearchMetadata(BaseModel):
    """Canonical search query metadata derived from authentic Trip."""
    model_config = ConfigDict(extra="ignore")

    trip_id: str
    origin: str
    destination: str
    departure_date: str
    return_date: str | None = None
    traveler_count: int = 1
    currency: str = "INR"
    cabin_class: str = "economy"
    count: int = 0


class FlightSearchResponse(BaseModel):
    """Top-level flight search response adhering to the backend contract."""
    model_config = ConfigDict(extra="ignore")

    success: bool = True
    metadata: FlightSearchMetadata | None = None
    offers: list[NormalizedFlightOffer] = Field(default_factory=list)
    provider: str = "duffel"
    availability_state: str = "available"  # "available", "empty", "unavailable", "error"
    error: str | None = None
