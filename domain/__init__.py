"""Canonical Trust Trip domain models."""

from .budget import BudgetConstraints, BudgetMode
from .changes import TripChangeSet, apply_trip_change_set
from .compat import build_trip_request, trip_request_to_legacy
from .evidence import (
    EvidenceMetadata,
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceSourceType,
    EvidenceType,
    FreshnessKind,
    PlanRevisionEvidenceLink,
    UsageState,
    canonical_hash,
)
from .flight import FlightPreferences
from .hotel import HotelPreferences
from .passenger import PassengerComposition
from .plan_approval import PlanApproval
from .plan_revision import PlanRevision
from .research import TripIntent
from .travel import TravelPreferences
from .trip import Location, TripRequest, TripStatus

__all__ = [
    "BudgetConstraints",
    "BudgetMode",
    "EvidenceMetadata",
    "EvidenceProvenance",
    "EvidenceRecord",
    "EvidenceSourceType",
    "EvidenceType",
    "FlightPreferences",
    "FreshnessKind",
    "HotelPreferences",
    "Location",
    "PassengerComposition",
    "PlanApproval",
    "PlanRevision",
    "PlanRevisionEvidenceLink",
    "TravelPreferences",
    "TripChangeSet",
    "TripIntent",
    "TripRequest",
    "TripStatus",
    "UsageState",
    "apply_trip_change_set",
    "build_trip_request",
    "canonical_hash",
    "trip_request_to_legacy",
]
