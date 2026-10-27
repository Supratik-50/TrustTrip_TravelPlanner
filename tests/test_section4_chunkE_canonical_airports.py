from datetime import date, timedelta
import pytest
from services.chat_planner import (
    enforce_complete_canonical_invariants,
    CanonicalInvariantViolation,
    TravelPlan,
    BudgetPlan,
    DayPlan,
)


def _make_plan(**overrides):
    b = BudgetPlan(
        currency="INR",
        flight_estimate_inr=10000.0,
        hotel_estimate_inr=8000.0,
        local_transport_estimate_inr=2000.0,
        activities_estimate_inr=3000.0,
        contingency_inr=1000.0,
        estimated_total_inr=24000.0,
        target_budget_inr=25000.0,
        within_budget=True,
        pricing_note="Within budget",
    )
    dep = date.today() + timedelta(days=14)
    ret = dep + timedelta(days=4)
    daily_plan = [
        DayPlan(
            day=i,
            title=f"Day {i}",
            morning=f"Morning activity {i}",
            afternoon=f"Afternoon activity {i}",
            evening=f"Evening activity {i}",
            estimated_daily_cost_inr=1000.0,
            date=(dep + timedelta(days=i - 1)).isoformat(),
        )
        for i in range(1, 6)
    ]
    data = {
        "trip_summary": "Test trip summary",
        "route": "Test route",
        "duration_days": 5,
        "travel_dates": f"{dep.isoformat()} to {ret.isoformat()}",
        "travel_style": "Balanced",
        "daily_plan": daily_plan,
        "budget": b,
        "flight_suggestion": "Test flight suggestion",
        "accommodation_plan": "Test hotel suggestion",
        "weather_guidance": "Pleasant weather",
    }
    data.update(overrides)
    return TravelPlan(**data)


def test_canonical_iata_populates_kolkata_to_goa():
    canonical = {
        "origin": "CCU",
        "destination": "GOI",
    }
    plan = _make_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical)
    assert normalized.origin == "Kolkata"
    assert normalized.origin_airport == "CCU"
    assert normalized.destination == "Goa"
    assert normalized.destination_airport == "GOI"


def test_canonical_delhi_to_jaipur():
    canonical = {
        "origin": "DEL",
        "destination": "JAI",
    }
    plan = _make_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical)
    assert normalized.origin_airport == "DEL"
    assert normalized.destination_airport == "JAI"


def test_canonical_paris_to_kyoto():
    canonical = {
        "origin": "Paris",
        "destination": "Kyoto",
    }
    plan = _make_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical)
    assert normalized.origin_airport == "CDG"
    assert normalized.destination_airport == "KIX"


def test_correct_existing_explicit_airports_preserved():
    canonical = {
        "origin": "CCU",
        "destination": "GOI",
    }
    plan = _make_plan(
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
    )
    normalized = enforce_complete_canonical_invariants(plan, canonical)
    assert normalized.origin == "Kolkata"
    assert normalized.origin_airport == "CCU"
    assert normalized.destination == "Goa"
    assert normalized.destination_airport == "GOI"


def test_missing_canonical_airport_remains_optional():
    canonical = {
        "origin": "AtlantisNonAirportLocation",
        "destination": "EldoradoNonAirportLocation",
    }
    plan = _make_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical)
    assert normalized.origin_airport is None
    assert normalized.destination_airport is None


def test_airport_code_tampering_rejected():
    canonical = {
        "origin": "CCU",
        "destination": "GOI",
    }
    plan = _make_plan(
        origin="Kolkata",
        destination="Goa",
        origin_airport="BOM",  # Tampered
        destination_airport="GOI",
    )
    with pytest.raises(CanonicalInvariantViolation) as exc_info:
        enforce_complete_canonical_invariants(plan, canonical)
    assert "tampering detected" in str(exc_info.value).lower()
