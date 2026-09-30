"""
Section 4 Chunk C -- Canonical Invariant Validation Tests
=========================================================
All tests exercise Step 10 of PlannerService.plan_travel().

CRITICAL DESIGN RULE:
    Plan-side values must come exclusively from plan_json.
    These tests delete fields from plan_json so absence is genuine --
    no compatibility_constraints substitution can mask it.
"""
import pytest
import uuid
from unittest.mock import patch

from services.planner_service import PlannerService


# -- Shared helpers ------------------------------------------------------------

def _canonical_trip(trip_id, user_id, **overrides):
    base = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "origin": "Kolkata",
        "start_date": "2026-10-14",
        "end_date": "2026-10-24",
        "duration_days": 11,
        "travelers_count": 2,
        "budget": 45000.0,
        "currency": "INR",
        "current_revision_number": 0,
        "metadata": {
            "origin": "CCU",
            "destination": "GOI",
            "origin_city": "Kolkata",
            "destination_city": "Goa",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-24",
            "duration_days": 11,
            "adults": 2,
            "budget_amount": 45000.0,
            "currency": "INR",
        },
    }
    base.update(overrides)
    return base


def _correct_plan():
    return {
        "destination": "Goa",
        "destination_airport": "GOI",
        "origin": "Kolkata",
        "origin_airport": "CCU",
        "departure_date": "2026-10-14",
        "return_date": "2026-10-24",
        "duration_days": 11,
        "travelers": 2,
        "budget": {
            "target_budget": 45000.0,
            "target_budget_inr": 45000.0,
            "currency": "INR",
        },
    }


def _invoke(svc, user_id, trip_id, plan_json_override, canonical_override=None):
    canonical = canonical_override if canonical_override is not None else _canonical_trip(trip_id, user_id)
    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    graph_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": plan_json_override,
    }
    with patch("services.trip_store.TripStore.get_trip", return_value=canonical):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=graph_result):
                return svc.plan_travel(
                    user_input="Update my itinerary",
                    authenticated_user_id=user_id,
                    trip_id=trip_id,
                )


def _reject(svc, user_id, trip_id, plan_json_override, fragment, canonical_override=None):
    canonical = canonical_override if canonical_override is not None else _canonical_trip(trip_id, user_id)
    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    graph_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": plan_json_override,
    }
    with patch("services.trip_store.TripStore.get_trip", return_value=canonical):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=graph_result):
                with pytest.raises(ValueError) as exc:
                    svc.plan_travel(
                        user_input="Update my itinerary",
                        authenticated_user_id=user_id,
                        trip_id=trip_id,
                    )
    assert fragment.lower() in str(exc.value).lower(), f"Expected '{fragment}' in: {exc.value}"


@pytest.fixture
def svc():
    return PlannerService()


@pytest.fixture
def ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


# ==============================================================================
# Test 1 -- departure_date MISSING from plan -> reject
# ==============================================================================
def test_missing_departure_date_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["departure_date"]
    _reject(svc, user_id, trip_id, plan, "missing required departure_date")


# ==============================================================================
# Test 2 -- return_date MISSING from plan -> reject
# ==============================================================================
def test_missing_return_date_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["return_date"]
    _reject(svc, user_id, trip_id, plan, "missing required return_date")


# ==============================================================================
# Test 3 -- duration_days MISSING from plan -> reject
# ==============================================================================
def test_missing_duration_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["duration_days"]
    _reject(svc, user_id, trip_id, plan, "missing required duration_days")


# ==============================================================================
# Test 4 -- travelers MISSING from plan -> reject
# ==============================================================================
def test_missing_travelers_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["travelers"]
    _reject(svc, user_id, trip_id, plan, "missing required travelers count")


# ==============================================================================
# Test 5 -- target_budget MISSING from plan -> reject
# ==============================================================================
def test_missing_target_budget_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["budget"]
    _reject(svc, user_id, trip_id, plan, "missing required target_budget")


# ==============================================================================
# Test 6 -- origin_airport MISSING from plan -> reject
# ==============================================================================
def test_missing_origin_airport_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["origin_airport"]
    _reject(svc, user_id, trip_id, plan, "missing required origin_airport")


# ==============================================================================
# Test 7 -- destination_airport MISSING from plan -> reject
# ==============================================================================
def test_missing_destination_airport_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["destination_airport"]
    _reject(svc, user_id, trip_id, plan, "missing required destination_airport")


# ==============================================================================
# Test 8 -- currency MISSING from plan budget -> reject
# ==============================================================================
def test_missing_currency_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    del plan["budget"]["currency"]
    _reject(svc, user_id, trip_id, plan, "missing required currency")


# ==============================================================================
# Test 9 -- fully correct plan -> pass
# ==============================================================================
def test_correct_plan_passes(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    result = _invoke(svc, user_id, trip_id, plan)
    assert isinstance(result, dict)


# ==============================================================================
# Test 10 -- canonical field absent -> not required (pass)
# ==============================================================================
def test_non_applicable_canonical_field_not_required(svc, ids):
    """When canonical trip has no budget, the plan may omit target_budget.

    Note: The architecture always defaults currency to INR in canonical_constraints
    (via `currency_val = meta.get("currency") or "INR"`), so currency is always
    present even when metadata has none.  The plan must therefore always include
    currency when a canonical trip exists.  This test proves only that a
    non-applicable *budget* field is not required.
    """
    user_id, trip_id = ids
    canonical = _canonical_trip(trip_id, user_id, budget=None)
    canonical["metadata"] = dict(canonical["metadata"])
    canonical["metadata"].pop("budget_amount", None)

    # Plan has no budget target — that is fine because canonical also has none.
    # But plan MUST still carry currency (canonical always has INR by default).
    plan = _correct_plan()
    plan["budget"] = {"currency": "INR"}    # currency present, no target_budget

    result = _invoke(svc, user_id, trip_id, plan, canonical_override=canonical)
    assert isinstance(result, dict)


# ==============================================================================
# Regression 1 -- wrong departure_date value -> reject
# ==============================================================================
def test_wrong_departure_date_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    plan["departure_date"] = "2026-11-01"    # canonical is 2026-10-14
    _reject(svc, user_id, trip_id, plan, "departure date")


# ==============================================================================
# Regression 2 -- wrong travelers count -> reject
# ==============================================================================
def test_wrong_travelers_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    plan["travelers"] = 5    # canonical is 2
    _reject(svc, user_id, trip_id, plan, "traveler count")


# ==============================================================================
# Regression 3 -- wrong destination airport -> reject
# ==============================================================================
def test_wrong_destination_airport_rejected(svc, ids):
    user_id, trip_id = ids
    plan = _correct_plan()
    plan["destination_airport"] = "BOM"    # canonical is GOI
    _reject(svc, user_id, trip_id, plan, "destination airport")
