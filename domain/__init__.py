"""Canonical Trust Trip domain models."""

from .budget import BudgetConstraints, BudgetMode
from .changes import TripChangeSet, apply_trip_change_set
from .compat import build_trip_request, trip_request_to_legacy
from .evidence import EvidenceMetadata, EvidenceSourceType
from .flight import FlightPreferences
from .hotel import HotelPreferences
from .passenger import PassengerComposition
from .research import TripIntent
from .travel import TravelPreferences
from .trip import Location, TripRequest, TripStatus

__all__ = [
    "BudgetConstraints",
    "BudgetMode",
    "EvidenceMetadata",
    "EvidenceSourceType",
    "FlightPreferences",
    "HotelPreferences",
    "Location",
    "PassengerComposition",
    "TravelPreferences",
    "TripChangeSet",
    "TripIntent",
    "TripRequest",
    "TripStatus",
    "apply_trip_change_set",
    "build_trip_request",
    "trip_request_to_legacy",
]
