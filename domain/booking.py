"""Booking domain models, destination types, and offer selection definitions."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FlightDestinationType(StrEnum):
    DIRECT_AIRLINE_BOOKING = "direct_airline_booking"
    DUFFEL_LINK = "duffel_link"
    UNAVAILABLE = "unavailable"


class OfferType(StrEnum):
    FLIGHT = "flight"
    HOTEL = "hotel"


class OfferSelection(BaseModel):
    """Explicit flight or hotel selection by user.

    Does not mutate travel requirements or advance revision.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    offer_type: OfferType
    snapshot_id: UUID


class FlightBookingDestination(BaseModel):
    """Provider-neutral external flight booking destination returned to client."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    destination_type: FlightDestinationType
    redirect_url: str | None = None
    airline_name: str
    flight_number: str
    price_total: Decimal
    currency: str
    expires_at: datetime
    provider_label: str
    instructions: str


class FlightHandoffRequest(BaseModel):
    """Client request to initiate flight handoff."""
    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID


class HotelCheckoutSnapshotRequest(BaseModel):
    """Client request to create hotel payment order via verified snapshot_id.

    Section 10 requires exact revision identity and a database-backed payment
    idempotency key before creating a payment order.
    """
    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID
    trip_id: UUID
    revision_id: UUID
    expected_revision_number: int = Field(ge=1)
    payment_idempotency_key: str = Field(min_length=1, max_length=255)
    holder: dict[str, Any]
    guests: list[dict[str, Any]]
    customer_ref: str | None = None
    final_confirmation: bool = False
