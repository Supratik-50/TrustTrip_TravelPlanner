"""TripChangeSet and Dependency-Aware Replanning Domain Models."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class TripChangeSet(BaseModel):
    """Structured representation of travel requirement mutations across revisions."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    origin_changed: bool = False
    destination_changed: bool = False
    dates_changed: bool = False
    duration_changed: bool = False
    passengers_changed: bool = False
    budget_changed: bool = False
    flight_preferences_changed: bool = False
    hotel_preferences_changed: bool = False
    travel_style_or_activities_changed: bool = False
    freeform_notes: str = ""

    @property
    def has_material_changes(self) -> bool:
        return any([
            self.origin_changed,
            self.destination_changed,
            self.dates_changed,
            self.duration_changed,
            self.passengers_changed,
            self.budget_changed,
            self.flight_preferences_changed,
            self.hotel_preferences_changed,
            self.travel_style_or_activities_changed,
        ])


class ReplanningPlan(BaseModel):
    """Calculated execution plan detailing which agents and caches to invalidate."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    affected_agents: list[str]
    invalidated_evidence: list[str]
    requires_flight_search: bool
    requires_hotel_search: bool


def compute_change_set(
    current_constraints: dict[str, Any],
    updated_constraints: dict[str, Any],
    feedback: str = "",
) -> TripChangeSet:
    """Analyze current vs updated constraints and user feedback to detect requirement changes."""
    fb_lower = feedback.lower()

    # Location / Route
    origin_changed = str(current_constraints.get("origin") or "").strip().lower() != str(updated_constraints.get("origin") or "").strip().lower()
    destination_changed = str(current_constraints.get("destination") or "").strip().lower() != str(updated_constraints.get("destination") or "").strip().lower()

    # Dates & Duration
    dates_changed = (
        current_constraints.get("departure_date") != updated_constraints.get("departure_date")
        or current_constraints.get("return_date") != updated_constraints.get("return_date")
    )
    duration_changed = current_constraints.get("duration_days") != updated_constraints.get("duration_days")

    # Passengers
    pax_keys = ("adults", "children", "infants")
    passengers_changed = any(
        int(current_constraints.get(k, 0) or 0) != int(updated_constraints.get(k, 0) or 0)
        for k in pax_keys
    )

    # Budget
    budget_changed = (
        current_constraints.get("budget_amount") != updated_constraints.get("budget_amount")
        or current_constraints.get("budget_mode") != updated_constraints.get("budget_mode")
        or any(w in fb_lower for w in ("budget", "expensive", "cheaper", "cost", "luxury"))
    )

    # Flight Preferences
    flight_keywords = ("flight", "fly", "airline", "nonstop", "direct flight", "cabin", "business class", "economy")
    flight_preferences_changed = (
        current_constraints.get("flight_preference") != updated_constraints.get("flight_preference")
        or current_constraints.get("cabin_class") != updated_constraints.get("cabin_class")
        or any(w in fb_lower for w in flight_keywords)
    )

    # Hotel Preferences
    hotel_keywords = ("hotel", "resort", "room", "stay", "accommodation", "star rating", "breakfast", "cancellation")
    hotel_preferences_changed = (
        current_constraints.get("hotel_preference") != updated_constraints.get("hotel_preference")
        or current_constraints.get("min_star_rating") != updated_constraints.get("min_star_rating")
        or current_constraints.get("board_type") != updated_constraints.get("board_type")
        or any(w in fb_lower for w in hotel_keywords)
    )

    # Activities / Style
    style_keywords = ("activity", "museum", "beach", "adventure", "hiking", "relaxed", "fast-paced", "itinerary", "sightseeing")
    travel_style_or_activities_changed = (
        current_constraints.get("travel_style") != updated_constraints.get("travel_style")
        or any(w in fb_lower for w in style_keywords)
    )

    return TripChangeSet(
        origin_changed=origin_changed,
        destination_changed=destination_changed,
        dates_changed=dates_changed,
        duration_changed=duration_changed,
        passengers_changed=passengers_changed,
        budget_changed=budget_changed,
        flight_preferences_changed=flight_preferences_changed,
        hotel_preferences_changed=hotel_preferences_changed,
        travel_style_or_activities_changed=travel_style_or_activities_changed,
        freeform_notes=feedback,
    )


def calculate_replanning_plan(change_set: TripChangeSet) -> ReplanningPlan:
    """Evaluate dependency matrix to determine minimal affected agents and search requirements."""
    affected: set[str] = set()
    invalidated: set[str] = set()
    requires_flight = False
    requires_hotel = False

    # Destination change invalidates everything
    if change_set.destination_changed:
        affected.update(["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["flights", "hotels", "weather", "budget", "itinerary"])
        requires_flight = True
        requires_hotel = True
        return ReplanningPlan(
            affected_agents=["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
            invalidated_evidence=sorted(invalidated),
            requires_flight_search=requires_flight,
            requires_hotel_search=requires_hotel,
        )

    # Dates or Duration changed
    if change_set.dates_changed:
        affected.update(["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["flights", "hotels", "weather", "budget", "itinerary"])
        requires_flight = True
        requires_hotel = True

    if change_set.duration_changed:
        affected.update(["hotel_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["hotels", "budget", "itinerary"])
        requires_hotel = True

    # Origin changed affects flights
    if change_set.origin_changed:
        affected.update(["flight_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["flights", "budget"])
        requires_flight = True

    # Passenger count changed affects flights, hotels, and budget
    if change_set.passengers_changed:
        affected.update(["flight_agent", "hotel_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["flights", "hotels", "budget"])
        requires_flight = True
        requires_hotel = True

    # Flight preferences changed
    if change_set.flight_preferences_changed:
        affected.update(["flight_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["flights", "budget"])
        requires_flight = True

    # Hotel preferences changed
    if change_set.hotel_preferences_changed:
        affected.update(["hotel_agent", "budget_agent", "itinerary_agent"])
        invalidated.update(["hotels", "budget"])
        requires_hotel = True

    # Budget changed without travel parameters
    if change_set.budget_changed:
        affected.update(["budget_agent", "itinerary_agent"])
        invalidated.update(["budget"])

    # Activities or travel style changed
    if change_set.travel_style_or_activities_changed or not affected:
        affected.add("itinerary_agent")
        invalidated.add("itinerary")

    # Order agents by standard execution pipeline
    pipeline_order = ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"]
    ordered_agents = [a for a in pipeline_order if a in affected]

    return ReplanningPlan(
        affected_agents=ordered_agents,
        invalidated_evidence=sorted(invalidated),
        requires_flight_search=requires_flight,
        requires_hotel_search=requires_hotel,
    )
