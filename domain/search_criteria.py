"""Canonical Search Criteria Models for Live Travel Intelligence (Phases 4 & 7)."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DateFlexibilityMode(StrEnum):
    EXACT = "exact"
    PLUS_MINUS_DAYS = "plus_minus_days"
    DATE_RANGE = "date_range"
    NEAR_TERM_FALLBACK = "near_term_fallback"


class FlexibleDateOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DateFlexibilityMode = DateFlexibilityMode.EXACT
    flexibility_days: int = Field(default=0, ge=0, le=3)
    earliest_departure: date | None = None
    latest_departure: date | None = None

    @model_validator(mode="after")
    def validate_flexibility(self) -> FlexibleDateOptions:
        if self.mode == DateFlexibilityMode.PLUS_MINUS_DAYS:
            if not (1 <= self.flexibility_days <= 3):
                raise ValueError("flexibility_days must be between 1 and 3 for plus_minus_days mode.")
        elif self.mode == DateFlexibilityMode.DATE_RANGE:
            if self.earliest_departure is None or self.latest_departure is None:
                raise ValueError("earliest_departure and latest_departure are required for date_range mode.")
            if self.earliest_departure < date.today():
                raise ValueError("earliest_departure cannot be in the past.")
            if self.earliest_departure > self.latest_departure:
                raise ValueError("earliest_departure must be before or equal to latest_departure.")
            if (self.latest_departure - self.earliest_departure).days > 14:
                raise ValueError("date_range window span cannot exceed 14 days.")
        elif self.mode == DateFlexibilityMode.EXACT:
            if self.flexibility_days != 0:
                raise ValueError("flexibility_days must be 0 for exact mode.")
        return self


class FlightSliceCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin_iata: str = Field(min_length=3, max_length=3)
    destination_iata: str = Field(min_length=3, max_length=3)
    departure_date: date


class FlightSearchCriteria(BaseModel):
    """Canonical flight query criteria for deterministic hashing."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    slices: list[FlightSliceCriteria]
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin_class: str = "economy"
    max_connections: int = Field(default=2, ge=0, le=4)
    currency: str = "USD"
    flexibility: FlexibleDateOptions | None = None


class FlexibleFlightSearchCriteria(BaseModel):
    """Client-facing and orchestrator query criteria for flexible live flight search."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    trip_id: UUID
    origin_iata: str = Field(min_length=3, max_length=3)
    destination_iata: str = Field(min_length=3, max_length=3)
    departure_date: date
    return_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=60)
    flexibility: FlexibleDateOptions = Field(default_factory=FlexibleDateOptions)
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin_class: str = "economy"
    max_connections: int = Field(default=2, ge=0, le=4)
    currency: str = "USD"

    @model_validator(mode="after")
    def validate_criteria(self) -> FlexibleFlightSearchCriteria:
        orig = self.origin_iata.strip().upper()
        dest = self.destination_iata.strip().upper()
        if not orig.isalpha() or not dest.isalpha():
            raise ValueError("IATA codes must consist of 3 alphabetic characters.")
        if orig == dest:
            raise ValueError("Origin and destination IATA codes cannot be identical.")
        today = date.today()
        if self.departure_date < today:
            raise ValueError("departure_date cannot be in the past.")
        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("return_date cannot precede departure_date.")
        valid_cabins = {"economy", "premium_economy", "business", "first"}
        if self.cabin_class.lower() not in valid_cabins:
            raise ValueError(f"cabin_class must be one of {sorted(valid_cabins)}.")
        if self.infants > self.adults:
            raise ValueError("Number of infants without seat cannot exceed number of accompanying adults.")
        return self


class HotelSearchCriteria(BaseModel):
    """Canonical hotel query criteria for deterministic hashing."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination_city: str = Field(min_length=2)
    checkin_date: date
    checkout_date: date
    adults: int = Field(default=1, ge=1, le=12)
    children: int = Field(default=0, ge=0, le=12)
    child_ages: list[int] = Field(default_factory=list)
    infants: int = Field(default=0, ge=0, le=4)
    rooms: int = Field(default=1, ge=1, le=8)
    min_star_rating: int | None = Field(default=None, ge=1, le=5)
    cancellation_required: bool = False
    currency: str = "USD"


def compute_criteria_hash(criteria: BaseModel) -> str:
    """Compute deterministic SHA-256 hash across canonical criteria with sorted keys."""
    serialized = json.dumps(criteria.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
