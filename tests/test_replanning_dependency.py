"""Tests for TripChangeSet and Dependency-Aware Replanning Matrix."""
from __future__ import annotations

from domain.change_set import compute_change_set, calculate_replanning_plan


def test_destination_change_invalidates_all_agents():
    """Changing destination must re-run flight, hotel, weather, budget, and itinerary agents."""
    current = {"origin": "DEL", "destination": "DXB", "departure_date": "2026-11-01"}
    updated = {"origin": "DEL", "destination": "TYO", "departure_date": "2026-11-01"}

    change_set = compute_change_set(current, updated, feedback="Let's go to Tokyo instead")
    assert change_set.destination_changed is True
    assert change_set.origin_changed is False

    plan = calculate_replanning_plan(change_set)
    assert plan.requires_flight_search is True
    assert plan.requires_hotel_search is True
    assert set(plan.affected_agents) == {"flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"}


def test_origin_change_invalidates_flights_and_budget_only():
    """Changing origin must re-run flights and budget, but preserve hotel and weather."""
    current = {"origin": "DEL", "destination": "DXB", "departure_date": "2026-11-01"}
    updated = {"origin": "BOM", "destination": "DXB", "departure_date": "2026-11-01"}

    change_set = compute_change_set(current, updated, feedback="Fly from Mumbai instead")
    assert change_set.origin_changed is True
    assert change_set.destination_changed is False

    plan = calculate_replanning_plan(change_set)
    assert plan.requires_flight_search is True
    assert plan.requires_hotel_search is False
    assert "hotel_agent" not in plan.affected_agents
    assert "weather_agent" not in plan.affected_agents
    assert "flight_agent" in plan.affected_agents
    assert "itinerary_agent" in plan.affected_agents


def test_passenger_change_invalidates_flights_hotels_budget():
    """Changing passengers must re-run flights and hotels for capacity."""
    current = {"origin": "DEL", "destination": "DXB", "adults": 1, "children": 0, "infants": 0}
    updated = {"origin": "DEL", "destination": "DXB", "adults": 2, "children": 1, "infants": 1}

    change_set = compute_change_set(current, updated, feedback="My family is joining")
    assert change_set.passengers_changed is True

    plan = calculate_replanning_plan(change_set)
    assert plan.requires_flight_search is True
    assert plan.requires_hotel_search is True
    assert "flight_agent" in plan.affected_agents
    assert "hotel_agent" in plan.affected_agents
    assert "weather_agent" not in plan.affected_agents


def test_hotel_preference_change_invalidates_hotels_and_budget_only():
    """Changing hotel preference must re-run hotel agent without re-running flight agent."""
    current = {"origin": "DEL", "destination": "DXB", "hotel_preference": "budget"}
    updated = {"origin": "DEL", "destination": "DXB", "hotel_preference": "luxury"}

    change_set = compute_change_set(current, updated, feedback="I want 5 star luxury hotels with breakfast")
    assert change_set.hotel_preferences_changed is True
    assert change_set.destination_changed is False

    plan = calculate_replanning_plan(change_set)
    assert plan.requires_hotel_search is True
    assert plan.requires_flight_search is False
    assert "hotel_agent" in plan.affected_agents
    assert "flight_agent" not in plan.affected_agents


def test_activity_style_change_reruns_only_itinerary_with_zero_supplier_searches():
    """Changing activity preferences must re-run only itinerary agent with zero supplier searches."""
    current = {"origin": "DEL", "destination": "DXB", "travel_style": "relaxed"}
    updated = {"origin": "DEL", "destination": "DXB", "travel_style": "adventure"}

    change_set = compute_change_set(current, updated, feedback="More outdoor hiking and adventure activities")
    assert change_set.travel_style_or_activities_changed is True
    assert change_set.destination_changed is False

    plan = calculate_replanning_plan(change_set)
    assert plan.requires_flight_search is False
    assert plan.requires_hotel_search is False
    assert plan.affected_agents == ["itinerary_agent"]
