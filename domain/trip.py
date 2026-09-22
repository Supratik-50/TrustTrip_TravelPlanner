from __future__ import annotations

from datetime import date
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .budget import BudgetConstraints
from .flight import FlightPreferences
from .hotel import HotelPreferences
from .passenger import PassengerComposition
from .research import TripIntent
from .travel import TravelPreferences


class TripStatus(StrEnum):
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REPLANNING = "replanning"
    BOOKING = "booking"
    COMPLETED = "completed"
    FAILED = "failed"


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = ""
    city: str | None = None
    country: str | None = None
    iata_code: str | None = Field(default=None, min_length=3, max_length=3)
    airport: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.iata_code is not None


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: UUID = Field(default_factory=uuid4)
    user_id: str | None = None
    thread_id: str | None = None
    original_query: str = ""
    origin: Location | None = None
    destination: Location | None = None
    departure_date: date | None = None
    return_date: date | None = None
    duration_days: int = Field(default=5, ge=1)
    passengers: PassengerComposition = Field(default_factory=PassengerComposition)
    budget: BudgetConstraints = Field(default_factory=BudgetConstraints)
    flight_preferences: FlightPreferences = Field(default_factory=FlightPreferences)
    hotel_preferences: HotelPreferences = Field(default_factory=HotelPreferences)
    travel_preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    intent: TripIntent = TripIntent.NEW_TRIP
    revision: int = Field(default=0, ge=0)
    status: TripStatus = TripStatus.PLANNING
