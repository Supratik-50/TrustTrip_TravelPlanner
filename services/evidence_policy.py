"""Phase 6 Evidence Reuse, Invalidation, and Dependency Policy."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from domain.change_set import TripChangeSet
from domain.evidence import EvidenceRecord, EvidenceType, UsageState


def determine_invalidated_evidence_types(change_set: TripChangeSet) -> set[EvidenceType]:
    """Compute which evidence types are invalidated by requirement changes."""
    invalidated: set[EvidenceType] = set()

    # Route / destination changes invalidate location-dependent observations
    if change_set.destination_changed:
        invalidated.update({
            EvidenceType.FLIGHT,
            EvidenceType.HOTEL,
            EvidenceType.WEATHER,
            EvidenceType.ITINERARY,
            EvidenceType.ACTIVITY,
            EvidenceType.BUDGET,
        })
        return invalidated

    if change_set.origin_changed:
        invalidated.add(EvidenceType.FLIGHT)

    # Date / timing changes
    if change_set.dates_changed:
        invalidated.update({
            EvidenceType.FLIGHT,
            EvidenceType.HOTEL,
            EvidenceType.WEATHER,
            EvidenceType.ITINERARY,
            EvidenceType.ACTIVITY,
        })

    if change_set.duration_changed:
        invalidated.update({
            EvidenceType.HOTEL,
            EvidenceType.ITINERARY,
            EvidenceType.ACTIVITY,
        })

    # Passenger changes
    if change_set.passengers_changed:
        invalidated.update({
            EvidenceType.FLIGHT,
            EvidenceType.HOTEL,
        })

    # Domain preference changes
    if change_set.flight_preferences_changed:
        invalidated.add(EvidenceType.FLIGHT)

    if change_set.hotel_preferences_changed:
        invalidated.add(EvidenceType.HOTEL)

    if change_set.travel_style_or_activities_changed:
        invalidated.update({
            EvidenceType.ITINERARY,
            EvidenceType.ACTIVITY,
        })

    if change_set.budget_changed or (invalidated & {EvidenceType.FLIGHT, EvidenceType.HOTEL}):
        invalidated.add(EvidenceType.BUDGET)

    return invalidated


def evaluate_evidence_for_revision(
    evidence: EvidenceRecord,
    change_set: TripChangeSet,
    now: datetime | None = None,
) -> UsageState:
    """Evaluate whether existing evidence can be safely reused or must be invalidated."""
    invalidated_types = determine_invalidated_evidence_types(change_set)
    if evidence.evidence_type in invalidated_types:
        return UsageState.INVALID_FOR_REVISION

    now_dt = now or datetime.now(timezone.utc)
    if not evidence.is_fresh(now_dt):
        return UsageState.INVALID_FOR_REVISION

    return UsageState.REUSED


def build_applicability_key(evidence_type: EvidenceType, constraints: dict[str, Any]) -> dict[str, Any]:
    """Build canonical applicability key for an evidence type based on query constraints."""
    dest = str(constraints.get("destination") or constraints.get("destination_city") or "").strip().lower()
    orig = str(constraints.get("origin") or constraints.get("origin_city") or "").strip().lower()
    dep = str(constraints.get("departure_date") or "").strip()
    ret = str(constraints.get("return_date") or "").strip()
    adults = int(constraints.get("adults") or 1)
    children = int(constraints.get("children") or 0)
    infants = int(constraints.get("infants") or 0)

    if evidence_type == EvidenceType.FLIGHT:
        return {
            "origin": orig,
            "destination": dest,
            "departure_date": dep,
            "return_date": ret,
            "adults": adults,
            "children": children,
            "infants": infants,
            "cabin_class": str(constraints.get("cabin_class") or "economy").lower(),
        }
    elif evidence_type == EvidenceType.HOTEL:
        return {
            "destination": dest,
            "checkin_date": dep,
            "checkout_date": ret,
            "adults": adults,
            "children": children,
            "infants": infants,
            "rooms": int(constraints.get("rooms") or 1),
            "min_star_rating": int(constraints.get("min_star_rating") or 0),
        }
    elif evidence_type == EvidenceType.WEATHER:
        return {
            "destination": dest,
            "start_date": dep,
            "end_date": ret,
        }
    elif evidence_type in (EvidenceType.ITINERARY, EvidenceType.ACTIVITY):
        return {
            "destination": dest,
            "start_date": dep,
            "end_date": ret,
            "duration_days": int(constraints.get("duration_days") or 5),
            "style": str(constraints.get("travel_style") or "").lower(),
        }
    elif evidence_type == EvidenceType.BUDGET:
        return {
            "destination": dest,
            "budget_amount": float(constraints.get("budget_amount") or 0.0),
            "budget_mode": str(constraints.get("budget_mode") or "flexible").lower(),
            "passengers": adults + children + infants,
        }

    return {"destination": dest, "departure_date": dep}
