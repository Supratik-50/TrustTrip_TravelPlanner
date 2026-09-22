from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .budget import BudgetConstraints, BudgetMode
from .flight import FlightPreferences
from .hotel import HotelPreferences
from .passenger import PassengerComposition
from .research import TripIntent
from .travel import TravelPreferences
from .trip import Location, TripRequest


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _location(
    query: Any,
    city: Any,
    country: Any,
    iata: Any,
) -> Location | None:
    if not any(value for value in (query, city, country, iata)):
        return None
    return Location(
        query=str(query or ""),
        city=str(city) if city else None,
        country=str(country) if country else None,
        iata_code=str(iata).upper() if iata else None,
    )


def build_trip_request(
    constraints: dict[str, Any] | None = None,
    *,
    trip_id: Any = None,
    user_id: str | None = None,
    thread_id: str | None = None,
    original_query: str | None = None,
    intent: TripIntent = TripIntent.NEW_TRIP,
    revision: int = 0,
) -> TripRequest:
    values = constraints or {}
    adults = int(values.get("adults", 1) or 1)
    children = int(values.get("children", 0) or 0)
    infants = int(values.get("infants", 0) or 0)
    passengers = PassengerComposition(
        adults=adults,
        children=children,
        infants=infants,
        child_ages=list(values.get("child_ages") or []),
    )
    amount = values.get("budget_amount")
    budget = BudgetConstraints(
        amount=Decimal(str(amount)) if amount is not None else None,
        currency=str(values.get("currency") or "INR"),
        mode=BudgetMode.CHEAPER if values.get("cheaper_mode") else BudgetMode.FLEXIBLE,
    )
    trip_values: dict[str, Any] = {
        "user_id": user_id,
        "thread_id": thread_id,
        "original_query": original_query or str(values.get("query") or ""),
        "origin": _location(
            values.get("origin_query"),
            values.get("origin_city"),
            values.get("origin_country"),
            values.get("origin"),
        ),
        "destination": _location(
            values.get("destination_query"),
            values.get("destination_city"),
            values.get("destination_country"),
            values.get("destination"),
        ),
        "departure_date": _as_date(values.get("departure_date")),
        "return_date": _as_date(values.get("return_date")),
        "duration_days": int(values.get("duration_days") or 5),
        "passengers": passengers,
        "budget": budget,
        "flight_preferences": FlightPreferences(
            cabin_class=str(values.get("cabin_class") or "economy"),
            max_connections=int(values.get("max_connections", 2)),
            direct_only=str(values.get("flight_preference") or "").lower() == "nonstop",
            flexible_dates=bool(values.get("flexible_dates")),
        ),
        "hotel_preferences": HotelPreferences(
            rooms=int(values.get("rooms", 1) or 1),
        ),
        "travel_preferences": TravelPreferences(
            style=values.get("travel_style") or None,
        ),
        "intent": intent,
        "revision": revision,
    }
    if trip_id is not None:
        trip_values["trip_id"] = trip_id
    return TripRequest(**trip_values)


def trip_request_to_legacy(trip: TripRequest) -> dict[str, Any]:
    """Expose the current canonical request in the legacy agent shape."""
    result: dict[str, Any] = {
        "origin": trip.origin.iata_code if trip.origin else None,
        "destination": trip.destination.iata_code if trip.destination else None,
        "origin_query": trip.origin.query if trip.origin else "",
        "destination_query": trip.destination.query if trip.destination else "",
        "origin_city": trip.origin.city if trip.origin else "",
        "destination_city": trip.destination.city if trip.destination else "",
        "origin_country": trip.origin.country if trip.origin else "",
        "destination_country": trip.destination.country if trip.destination else "",
        "departure_date": trip.departure_date.isoformat() if trip.departure_date else None,
        "return_date": trip.return_date.isoformat() if trip.return_date else None,
        "duration_days": trip.duration_days,
        "adults": trip.passengers.adults,
        "children": trip.passengers.children,
        "infants": trip.passengers.infants,
        "child_ages": list(trip.passengers.child_ages),
        "passenger_count": trip.passengers.passenger_count,
        "budget_amount": float(trip.budget.amount) if trip.budget.amount is not None else None,
        "currency": trip.budget.currency,
        "cheaper_mode": trip.budget.mode == BudgetMode.CHEAPER,
        "cabin_class": trip.flight_preferences.cabin_class,
        "max_connections": trip.flight_preferences.max_connections,
        "flight_preference": "nonstop" if trip.flight_preferences.direct_only else "",
        "flexible_dates": trip.flight_preferences.flexible_dates,
        "rooms": trip.hotel_preferences.rooms,
        "travel_style": trip.travel_preferences.style or "",
        "query": trip.original_query,
        "intent": trip.intent.value,
    }
    return result
