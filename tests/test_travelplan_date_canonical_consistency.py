"""
Regression tests for TravelPlan date schema and canonical consistency normalization.

Validates:
- TEST A: Canonical dates projected into TravelPlan
- TEST B: Serialized plan contains discrete dates (departure_date, return_date, travelers)
- TEST C: PlannerService Step 10 accepts a valid plan with matching dates
- TEST D: Date mismatch fails Step 10 validation
- TEST E: Missing plan date when canonical date exists fails Step 10 validation
- TEST F: Canonical passengers projected into plan.travelers
- Fallback paths (_build_unavailable_plan, _deterministic_fallback) preserve canonical dates/pax
  and genuinely unavailable dates/pax remain None without fabrication.
"""
import uuid
import pytest
from unittest.mock import patch

from services.chat_planner import (
    TravelPlan,
    DayPlan,
    BudgetPlan,
    enforce_complete_canonical_invariants,
    _build_unavailable_plan,
    _deterministic_fallback,
)
from services.planner_service import PlannerService
from services.auth_store import UserStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()


def _dummy_budget():
    return BudgetPlan(
        currency="INR",
        flight_estimate_inr=15000.0,
        hotel_estimate_inr=20000.0,
        local_transport_estimate_inr=3000.0,
        activities_estimate_inr=5000.0,
        contingency_inr=2000.0,
        estimated_total_inr=45000.0,
        target_budget_inr=45000.0,
        within_budget=True,
        pricing_note="Provider-backed where available; other categories are planning estimates.",
    )


def _base_plan(duration_days: int = 8):
    return TravelPlan(
        trip_summary="A trip from Kolkata to Goa",
        route="Kolkata → Goa",
        duration_days=duration_days,
        travel_dates="Flexible dates",
        travel_style="Balanced",
        daily_plan=[
            DayPlan(
                day=i,
                title=f"Explore Goa — Day {i}",
                morning="Arrive and check in",
                afternoon="Visit beach",
                evening="Sunset dinner",
                estimated_daily_cost_inr=5000.0,
            )
            for i in range(1, duration_days + 1)
        ],
        budget=_dummy_budget(),
        flight_suggestion="Recommended flight",
        accommodation_plan="Recommended hotel",
        weather_guidance="Pleasant weather",
        origin="Kolkata",
        destination="Goa",
        origin_airport="CCU",
        destination_airport="GOI",
    )


# ── TEST A — Canonical dates projected into TravelPlan ─────────────────────────
def test_canonical_dates_projected_into_travel_plan():
    plan = _base_plan()
    canonical_ctx = {
        "origin": "Kolkata",
        "destination": "Goa",
        "origin_airport": "CCU",
        "destination_airport": "GOI",
        "departure_date": "2026-10-14",
        "return_date": "2026-10-21",
        "adults": 2,
    }

    normalized = enforce_complete_canonical_invariants(plan, canonical_ctx)

    assert normalized.departure_date == "2026-10-14"
    assert normalized.return_date == "2026-10-21"
    assert normalized.travel_dates == "2026-10-14 to 2026-10-21"
    assert normalized.duration_days == 8


# ── TEST B — Serialized plan contains discrete dates ───────────────────────────
def test_serialized_plan_contains_discrete_dates():
    plan = _base_plan()
    canonical_ctx = {
        "origin": "Kolkata",
        "destination": "Goa",
        "origin_airport": "CCU",
        "destination_airport": "GOI",
        "departure_date": "2026-10-14",
        "return_date": "2026-10-21",
        "passengers": 2,
    }

    normalized = enforce_complete_canonical_invariants(plan, canonical_ctx)
    dumped = normalized.model_dump()

    assert "departure_date" in dumped
    assert dumped["departure_date"] == "2026-10-14"
    assert "return_date" in dumped
    assert dumped["return_date"] == "2026-10-21"
    assert "travelers" in dumped
    assert dumped["travelers"] == 2


# ── TEST C — PlannerService Step 10 accepts a valid plan ──────────────────────
def test_planner_service_step_10_accepts_valid_plan():
    svc = PlannerService()
    user_id = str(uuid.uuid4())
    trip_id = "39b76dc3-cbc6-4604-bf93-87212c0a44e0"

    canonical = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "origin": "Kolkata",
        "start_date": "2026-10-14",
        "end_date": "2026-10-21",
        "duration_days": 8,
        "travelers_count": 2,
        "budget": 45000.0,
        "currency": "INR",
        "metadata": {
            "origin": "CCU",
            "destination": "GOI",
            "origin_city": "Kolkata",
            "destination_city": "Goa",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-21",
            "duration_days": 8,
            "adults": 2,
            "budget_amount": 45000.0,
            "currency": "INR",
        },
    }

    plan = _base_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical["metadata"])
    plan_json = normalized.model_dump()

    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    graph_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": plan_json,
    }

    with patch("services.trip_store.TripStore.get_trip", return_value=canonical):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=graph_result):
                res = svc.plan_travel(
                    user_input="Generate itinerary",
                    authenticated_user_id=user_id,
                    trip_id=trip_id,
                )
                assert res is not None
                assert res["plan_json"]["departure_date"] == "2026-10-14"
                assert res["plan_json"]["return_date"] == "2026-10-21"


# ── TEST D — Mismatch still fails ─────────────────────────────────────────────
def test_planner_service_step_10_rejects_date_mismatch():
    svc = PlannerService()
    user_id = str(uuid.uuid4())
    trip_id = "39b76dc3-cbc6-4604-bf93-87212c0a44e0"

    canonical = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "origin": "Kolkata",
        "start_date": "2026-10-14",
        "end_date": "2026-10-21",
        "duration_days": 8,
        "travelers_count": 2,
        "metadata": {
            "origin": "CCU",
            "destination": "GOI",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-21",
            "adults": 2,
        },
    }

    plan = _base_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical["metadata"])
    plan_json = normalized.model_dump()
    # Deliberately mutate departure_date to simulate mismatch
    plan_json["departure_date"] = "2026-10-15"

    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    graph_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": plan_json,
    }

    with patch("services.trip_store.TripStore.get_trip", return_value=canonical):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=graph_result):
                with pytest.raises(ValueError) as exc:
                    svc.plan_travel(
                        user_input="Generate itinerary",
                        authenticated_user_id=user_id,
                        trip_id=trip_id,
                    )
                assert "Consistency violation: Plan departure date '2026-10-15' does not match" in str(exc.value)


# ── TEST E — Missing plan date still fails when canonical date exists ─────────
def test_planner_service_step_10_rejects_missing_departure_date():
    svc = PlannerService()
    user_id = str(uuid.uuid4())
    trip_id = "39b76dc3-cbc6-4604-bf93-87212c0a44e0"

    canonical = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "origin": "Kolkata",
        "start_date": "2026-10-14",
        "end_date": "2026-10-21",
        "duration_days": 8,
        "travelers_count": 2,
        "metadata": {
            "origin": "CCU",
            "destination": "GOI",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-21",
            "adults": 2,
        },
    }

    plan = _base_plan()
    normalized = enforce_complete_canonical_invariants(plan, canonical["metadata"])
    plan_json = normalized.model_dump()
    # Strip departure_date entirely
    plan_json.pop("departure_date", None)

    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    graph_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": plan_json,
    }

    with patch("services.trip_store.TripStore.get_trip", return_value=canonical):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=graph_result):
                with pytest.raises(ValueError) as exc:
                    svc.plan_travel(
                        user_input="Generate itinerary",
                        authenticated_user_id=user_id,
                        trip_id=trip_id,
                    )
                assert "Consistency violation: Plan is missing required departure_date; canonical value is '2026-10-14'." in str(exc.value)


# ── TEST F — Travelers projection ─────────────────────────────────────────────
def test_travelers_projection_from_canonical_passengers():
    plan = _base_plan()
    canonical_ctx = {
        "origin": "Kolkata",
        "destination": "Goa",
        "origin_airport": "CCU",
        "destination_airport": "GOI",
        "departure_date": "2026-10-14",
        "return_date": "2026-10-21",
        "passengers": 3,
    }

    normalized = enforce_complete_canonical_invariants(plan, canonical_ctx)
    assert normalized.travelers == 3
    assert normalized.passenger_count == 3


# ── Existing fallback paths preserve dates & pax ──────────────────────────────
def test_build_unavailable_plan_preserves_canonical_context():
    constraints = {
        "origin": "Kolkata",
        "destination": "Goa",
        "origin_airport": "CCU",
        "destination_airport": "GOI",
        "departure_date": "2026-10-14",
        "return_date": "2026-10-21",
        "duration_days": 8,
        "adults": 2,
        "passenger_count": 2,
    }
    budget = {
        "flight_estimate": 10000,
        "hotel_estimate": 15000,
        "local_transport_estimate": 2000,
        "activities_estimate": 3000,
        "contingency": 1000,
        "estimated_total": 31000,
    }
    plan = _build_unavailable_plan(
        constraints=constraints,
        flight_offers=[],
        hotel_offers=[],
        budget=budget,
        weather_results="",
        feedback="",
        reason="Upstream outage",
    )
    assert plan.departure_date == "2026-10-14"
    assert plan.return_date == "2026-10-21"
    assert plan.travelers == 2


def test_deterministic_fallback_preserves_canonical_context():
    constraints = {
        "origin": "Kolkata",
        "destination": "Goa",
        "origin_airport": "CCU",
        "destination_airport": "GOI",
        "departure_date": "2026-10-14",
        "return_date": "2026-10-21",
        "duration_days": 8,
        "adults": 2,
        "passenger_count": 2,
    }
    budget = {
        "flight_estimate": 10000,
        "hotel_estimate": 15000,
        "local_transport_estimate": 2000,
        "activities_estimate": 3000,
        "contingency": 1000,
        "estimated_total": 31000,
    }
    plan = _deterministic_fallback(
        constraints=constraints,
        flight_offers=[],
        hotel_offers=[],
        budget=budget,
        weather_results="",
        feedback="",
        reason="Explicit test fallback",
    )
    assert plan.departure_date == "2026-10-14"
    assert plan.return_date == "2026-10-21"
    assert plan.travelers == 2


def test_genuinely_unavailable_dates_remain_none():
    plan = _base_plan()
    canonical_ctx = {
        "origin": "Kolkata",
        "destination": "Goa",
        "origin_airport": "CCU",
        "destination_airport": "GOI",
    }
    normalized = enforce_complete_canonical_invariants(plan, canonical_ctx)
    assert normalized.departure_date is None
    assert normalized.return_date is None
