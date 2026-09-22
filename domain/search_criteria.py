"""Canonical Search Criteria Models for Phase 4 Live Travel Intelligence."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pydantic import BaseModel, ConfigDict, Field


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
