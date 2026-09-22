from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain.changes import TripChangeSet, apply_trip_change_set
from domain.flight import FlightPreferences
from domain.hotel import HotelPreferences
from domain.compat import build_trip_request, trip_request_to_legacy
from domain.passenger import PassengerComposition
from domain.trip import Location, TripRequest, TripStatus


def test_single_traveller_is_canonical():
    passengers = PassengerComposition()
    assert passengers.passenger_count == 1
    assert passengers.adults == 1
    assert passengers.children == 0
    assert passengers.infants == 0


def test_passenger_composition_validates_child_ages():
    with pytest.raises(ValidationError):
        PassengerComposition(children=2, child_ages=[7])
    with pytest.raises(ValidationError):
        PassengerComposition(child_ages=[-1])


def test_trip_request_supports_destination_only_planning():
    trip = TripRequest(
        original_query="Japan trip",
        destination=Location(city="Tokyo", country="Japan", iata_code="HND"),
    )
    assert trip.origin is None
    assert trip.destination.city == "Tokyo"


def test_budget_is_validated_and_serializable():
    trip = build_trip_request(
        {
            "origin": "BOM",
            "destination": "LHR",
            "departure_date": "2026-10-05",
            "budget_amount": 250000,
            "currency": "inr",
        }
    )
    assert trip.budget.amount == Decimal("250000")
    assert trip.budget.currency == "INR"
    assert trip.departure_date == date(2026, 10, 5)


def test_negative_budget_is_rejected():
    with pytest.raises(ValidationError):
        build_trip_request({"budget_amount": -1})


def test_destination_change_preserves_origin():
    existing = TripRequest(
        origin=Location(city="Mumbai", country="India", iata_code="BOM"),
        destination=Location(city="London", country="United Kingdom", iata_code="LHR"),
    )
    change = TripChangeSet(
        changed_fields=["destination"],
        old_values={"destination": existing.destination.model_dump()},
        new_values={
            "destination": Location(
                city="Tokyo", country="Japan", iata_code="HND"
            )
        },
    )
    updated = apply_trip_change_set(existing, change)
    assert updated.origin.iata_code == "BOM"
    assert updated.destination.iata_code == "HND"


def test_origin_change_preserves_destination():
    existing = TripRequest(
        origin=Location(city="Mumbai", country="India", iata_code="BOM"),
        destination=Location(city="London", country="United Kingdom", iata_code="LHR"),
    )
    change = TripChangeSet(
        changed_fields=["origin"],
        new_values={
            "origin": Location(city="Delhi", country="India", iata_code="DEL")
        },
    )
    updated = apply_trip_change_set(existing, change)
    assert updated.origin.iata_code == "DEL"
    assert updated.destination.iata_code == "LHR"


def test_partial_change_supports_budget_and_passengers():
    existing = build_trip_request(
        {"origin": "BOM", "destination": "LHR", "adults": 2}
    )
    change = TripChangeSet(
        changed_fields=["budget", "passengers"],
        new_values={
            "budget": {"amount": "250000", "currency": "INR", "mode": "fixed"},
            "passengers": {"adults": 4, "children": 0, "infants": 0},
        },
        affected_agents=["flight_agent", "budget_agent", "itinerary_agent"],
        invalidated_evidence=["flight-search-1"],
        requires_live_search=True,
    )
    updated = apply_trip_change_set(existing, change)
    assert updated.passengers.adults == 4
    assert updated.budget.amount == Decimal("250000")
    assert updated.origin.iata_code == "BOM"
    assert updated.destination.iata_code == "LHR"


def test_hotel_only_change_does_not_change_flight_preferences():
    existing = TripRequest(
        flight_preferences=FlightPreferences(direct_only=True),
        hotel_preferences=HotelPreferences(rooms=1),
    )
    updated = apply_trip_change_set(
        existing,
        TripChangeSet(
            changed_fields=["hotel_preferences"],
            new_values={"hotel_preferences": {"rooms": 2, "star_rating": 4}},
            affected_agents=["hotel_agent", "itinerary_agent"],
        ),
    )
    assert updated.hotel_preferences.rooms == 2
    assert updated.flight_preferences.direct_only is True


def test_flight_only_change_does_not_change_hotel_preferences():
    existing = TripRequest(
        flight_preferences=FlightPreferences(direct_only=False),
        hotel_preferences=HotelPreferences(rooms=2),
    )
    updated = apply_trip_change_set(
        existing,
        TripChangeSet(
            changed_fields=["flight_preferences"],
            new_values={"flight_preferences": {"direct_only": True}},
            affected_agents=["flight_agent", "itinerary_agent"],
        ),
    )
    assert updated.flight_preferences.direct_only is True
    assert updated.hotel_preferences.rooms == 2


def test_legacy_conversion_preserves_agent_shape():
    trip = build_trip_request(
        {"origin": "CCU", "destination": "HND", "adults": 1}
    )
    legacy = trip_request_to_legacy(trip)
    assert legacy["origin"] == "CCU"
    assert legacy["destination"] == "HND"
    assert legacy["passenger_count"] == 1


def test_approval_status_is_distinct():
    trip = TripRequest(status=TripStatus.AWAITING_APPROVAL)
    assert trip.status != TripStatus.APPROVED
