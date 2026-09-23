"""Canonical Live Offer, View Projections, and Intelligence Models for Phase 4."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# =====================================================================
# Status Enumerations (Orthogonal Dimensions)
# =====================================================================

class FreshnessStatus(StrEnum):
    """Temporal freshness relative to platform TTL and supplier expiration."""
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"


class AvailabilityStatus(StrEnum):
    """Physical inventory availability reported by supplier."""
    AVAILABLE = "available"
    LIMITED_INVENTORY = "limited_inventory"
    SOLD_OUT = "sold_out"
    UNAVAILABLE = "unavailable"


class RevalidationStatus(StrEnum):
    """Outcome of active supplier verification check."""
    PENDING = "pending"
    CONFIRMED_UNCHANGED = "confirmed_unchanged"
    PRICE_CHANGED = "price_changed"
    SCHEDULE_CHANGED = "schedule_changed"
    OFFER_WITHDRAWN = "offer_withdrawn"


class InfantSupportLevel(StrEnum):
    """Supplier capability level for infant passengers."""
    NATIVE = "native"
    SUPPLEMENT_ON_REQUEST = "supplement_on_request"
    NOT_SUPPORTED = "not_supported"


# =====================================================================
# Limitation & Warning Structures
# =====================================================================

class ProviderLimitationWarning(BaseModel):
    """Transparent limitation warning propagated from provider adapter to client view."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    code: str
    message: str
    severity: str = "warning"  # "info", "warning", "critical"


# =====================================================================
# Financial Representation
# =====================================================================

class PriceBreakdown(BaseModel):
    """Provider-neutral monetary representation using exact Decimals."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_amount: Decimal = Field(ge=0)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    fee_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    inr_reference_amount: Decimal | None = None
    fx_rate_applied: Decimal | None = None


# =====================================================================
# Flight Segments
# =====================================================================

class FlightSegmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin_iata: str = Field(min_length=3, max_length=3)
    destination_iata: str = Field(min_length=3, max_length=3)
    departure_time: datetime
    arrival_time: datetime
    marketing_carrier_iata: str
    marketing_carrier_name: str
    flight_number: str
    operating_carrier_name: str | None = None
    aircraft_type: str | None = None
    duration_minutes: int = Field(ge=0)
    cabin_class: str = "economy"


# =====================================================================
# Candidate Date Summaries & Multi-Date Responses (Phase 7)
# =====================================================================

class CandidateDateSummary(BaseModel):
    """Aggregated status and lowest price for a specific candidate travel date window."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    departure_date: date
    return_date: date | None = None
    offer_count: int = Field(default=0, ge=0)
    lowest_price: Decimal | None = None
    currency: str = "USD"
    status: str = "ok"  # "ok", "no_offers", "provider_error"
    error_message: str | None = None


# =====================================================================
# Client-Facing View Projections (Publicly Exposed over REST)
# =====================================================================

class FlightOfferView(BaseModel):
    """Client-facing representation: strictly shields provider_offer_id and raw payloads."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    provider: str
    price: PriceBreakdown
    stops: int
    segments: list[FlightSegmentModel]
    passenger_count: int
    available_seats: int | None = None
    retrieved_at: datetime
    expires_at: datetime
    freshness: FreshnessStatus
    availability: AvailabilityStatus
    limitations: list[ProviderLimitationWarning] = Field(default_factory=list)
    is_recommended: bool = False
    recommendation_rank: int = 1
    candidate_departure_date: date | None = None
    candidate_return_date: date | None = None


class FlexibleFlightSearchResponse(BaseModel):
    """Client-facing aggregated response for flexible live flight search across candidate dates."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool = True
    session_id: UUID
    candidate_dates: list[CandidateDateSummary]
    offers_by_date: dict[str, list[FlightOfferView]]
    cheapest_offer: FlightOfferView | None = None
    recommended_offer: FlightOfferView | None = None
    errors: list[str] = Field(default_factory=list)


class HotelOfferView(BaseModel):
    """Client-facing representation: strictly shields provider_offer_id and raw payloads."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    provider: str
    hotel_id: str
    hotel_name: str
    city: str
    address: str = ""
    star_rating: float | None = None
    review_score: float | None = None
    room_name: str
    board_type: str
    refundable: bool
    cancellation_deadline: datetime | None = None
    price: PriceBreakdown
    nightly_price: Decimal
    nights: int
    rooms_count: int
    adults_count: int
    children_count: int
    infants_count: int
    rooms_inferred: bool = False
    retrieved_at: datetime
    expires_at: datetime | None = None
    freshness: FreshnessStatus
    availability: AvailabilityStatus
    limitations: list[ProviderLimitationWarning] = Field(default_factory=list)
    is_recommended: bool = False
    recommendation_rank: int = 1


# =====================================================================
# Internal Domain Models (Backend & Adapter Orchestration Only)
# =====================================================================

class FlightOffer(BaseModel):
    """Internal domain model: holds provider_offer_id for supplier rechecks."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID = Field(default_factory=uuid4)
    provider: str
    provider_offer_id: str
    price: PriceBreakdown
    stops: int = Field(default=0, ge=0)
    segments: list[FlightSegmentModel]
    passenger_count: int = Field(default=1, ge=1)
    available_seats: int | None = None
    retrieved_at: datetime
    expires_at: datetime
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    limitations: list[ProviderLimitationWarning] = Field(default_factory=list)
    is_recommended: bool = False
    recommendation_rank: int = 1
    candidate_departure_date: date | None = None
    candidate_return_date: date | None = None

    def to_client_view(self) -> FlightOfferView:
        data = self.model_dump(exclude={"provider_offer_id"})
        return FlightOfferView(**data)

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            return val
        if key == "id":
            return str(self.snapshot_id)
        if key == "price_inr" and self.price.inr_reference_amount is not None:
            return float(self.price.inr_reference_amount)
        if key == "price":
            return float(self.price.total_amount)
        if key == "currency":
            return self.price.currency
        return default

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and not hasattr(self, key):
            raise KeyError(key)
        return val


class HotelOffer(BaseModel):
    """Internal domain model: holds provider_offer_id for supplier rechecks."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID = Field(default_factory=uuid4)
    provider: str
    provider_offer_id: str
    hotel_id: str
    hotel_name: str
    city: str
    address: str = ""
    star_rating: float | None = None
    review_score: float | None = None
    room_name: str
    board_type: str
    refundable: bool
    cancellation_deadline: datetime | None = None
    price: PriceBreakdown
    nightly_price: Decimal
    nights: int = Field(ge=1)
    rooms_count: int = Field(default=1, ge=1)
    adults_count: int = Field(default=1, ge=1)
    children_count: int = Field(default=0, ge=0)
    infants_count: int = Field(default=0, ge=0)
    rooms_inferred: bool = False
    retrieved_at: datetime
    expires_at: datetime | None = None
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    limitations: list[ProviderLimitationWarning] = Field(default_factory=list)
    is_recommended: bool = False
    recommendation_rank: int = 1

    def to_client_view(self) -> HotelOfferView:
        data = self.model_dump(exclude={"provider_offer_id"})
        return HotelOfferView(**data)

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        if key == "id":
            return str(self.snapshot_id)
        if key == "board_name":
            return self.board_type
        if key == "price_inr" and self.price.inr_reference_amount is not None:
            return float(self.price.inr_reference_amount)
        if key == "price":
            return float(self.price.total_amount)
        if key == "currency":
            return self.price.currency
        return default

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and not hasattr(self, key):
            raise KeyError(key)
        return val


# =====================================================================
# Read Model: LivePlanView
# =====================================================================

class LivePlanView(BaseModel):
    """Client-facing live plan presentation."""
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(default_factory=uuid4)
    trip_id: UUID
    revision: int
    generated_itinerary_markdown: str
    flight_offers: list[FlightOfferView]
    hotel_offers: list[HotelOfferView]
    selected_flight_snapshot_id: UUID | None = None
    selected_hotel_snapshot_id: UUID | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =====================================================================
# Persistence & Audit Entities
# =====================================================================

class SearchSession(BaseModel):
    """Server-side record of a supplier search execution."""
    model_config = ConfigDict(extra="forbid")

    session_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    trip_id: UUID
    thread_id: str | None = None
    trip_revision: int = 0
    search_type: str
    criteria_hash: str
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_reuse_expires_at: datetime


class OfferSnapshot(BaseModel):
    """Immutable point-in-time snapshot of an offer."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    provider: str
    provider_offer_id: str
    offer_type: str  # 'flight' or 'hotel'
    price_total: Decimal
    currency: str
    normalized_offer: FlightOffer | HotelOffer
    freshness: FreshnessStatus
    availability: AvailabilityStatus
    retrieved_at: datetime
    expires_at: datetime | None = None
    predecessor_snapshot_id: UUID | None = None


class RevalidationResult(BaseModel):
    """Immutable audit record of a revalidation check against a supplier."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    revalidation_id: UUID = Field(default_factory=uuid4)
    original_snapshot_id: UUID
    subsequent_snapshot_id: UUID | None = None
    status: RevalidationStatus
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    price_changed: bool = False
    original_price: Decimal
    current_price: Decimal
    currency: str
    schedule_changed: bool = False
    details: str = ""
