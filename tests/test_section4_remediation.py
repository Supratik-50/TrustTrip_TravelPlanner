import pytest
import uuid
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from services.travel_planner import (
    parse_trip_request,
    _resolve_place,
    _city_from_airport,
    budget_from_live_data,
    CITY_TO_IATA,
    CITY_TO_NAME,
)
from services.planner_service import PlannerService
from domain.trip import TripRequest, BudgetConstraints, TripIntent
from app import app


# ==============================================================================
# 1. Natural Language Parsing Remediation Tests (HIGH #3, LOW #11)
# ==============================================================================

def _future_year_for_month_day(month: int, day: int) -> int:
    today = date.today()
    return today.year if (today.month < month or (today.month == month and today.day <= day)) else today.year + 1


def test_kolkata_to_goa_exact_user_query():
    """Verify exact prompt from audit: 'Kolkata to Goa, Oct 14 to Oct 24, 2 travelers, budget INR 45000'."""
    query = "Kolkata to Goa, Oct 14 to Oct 24, 2 travelers, budget INR 45000"
    c = parse_trip_request(query)

    expected_year = _future_year_for_month_day(10, 14)
    assert c["origin"] == "CCU"
    assert c["origin_city"] == "Kolkata"
    assert c["destination"] == "GOI"
    assert c["destination_city"] == "Goa"
    assert c["departure_date"] == f"{expected_year}-10-14"
    assert c["return_date"] == f"{expected_year}-10-24"
    # Duration: Oct 14 to Oct 24 inclusive = 11 days (10 nights)
    assert c["duration_days"] == 11
    assert c["adults"] == 2
    assert c["budget_amount"] == 45000.0
    assert c["currency"] == "INR"


@pytest.mark.parametrize("query,expected_adults", [
    ("Kolkata to Goa, Oct 14 to Oct 24, 2 travelers, budget INR 45000", 2),
    ("Kolkata to Goa, Oct 14 to Oct 24, 2 travellers, budget INR 45000", 2),
    ("Kolkata to Goa, Oct 14 to Oct 24, 2 adults, budget INR 45000", 2),
    ("Kolkata to Goa, Oct 14 to Oct 24 for 2 people, budget INR 45000", 2),
    ("Trip from Delhi to Mumbai for 4 passengers, 10 Nov to 15 Nov", 4),
    ("Trip to Bengaluru for 1 traveler", 1),
])
def test_passenger_variations_parsing(query, expected_adults):
    c = parse_trip_request(query)
    assert c["adults"] == expected_adults


@pytest.mark.parametrize("query,month,dep_day,ret_day,expected_days", [
    ("Kolkata to Goa, Oct 14 to Oct 24, 2 travelers", 10, 14, 24, 11),
    ("Kolkata to Goa, 14 Oct to 24 Oct, 2 travelers", 10, 14, 24, 11),
    ("Kolkata to Goa, Oct 14 to 24, 2 travelers", 10, 14, 24, 11),
    ("Kolkata to Goa, 14 to 24 Oct, 2 travelers", 10, 14, 24, 11),
    ("Delhi to Jaipur on 5 Nov to 10 Nov", 11, 5, 10, 6),
])
def test_date_range_variations_parsing(query, month, dep_day, ret_day, expected_days):
    c = parse_trip_request(query)
    expected_year = _future_year_for_month_day(month, dep_day)
    expected_dep = f"{expected_year}-{month:02d}-{dep_day:02d}"
    expected_ret = f"{expected_year}-{month:02d}-{ret_day:02d}"
    assert c["departure_date"] == expected_dep
    assert c["return_date"] == expected_ret
    assert c["duration_days"] == expected_days


def test_genoa_vs_goa_disambiguation():
    """Verify resolution policy:
    - Goa (India) -> GOI
    - Genoa/Genova (Italy) -> GOA
    - Ambiguous literal 'GOA' defaults to Goa, India (GOI) unless qualified with Italy.
    """
    assert _resolve_place("Goa")[0] == "GOI"
    assert _resolve_place("goa")[0] == "GOI"
    assert _resolve_place("Genoa")[0] == "GOA"
    assert _resolve_place("genova")[0] == "GOA"
    assert _resolve_place("Genoa, Italy")[0] == "GOA"
    assert _resolve_place("GOA")[0] == "GOI"  # ambiguous literal defaults to India
    assert _resolve_place("GOA, Italy")[0] == "GOA"


def test_city_from_airport_canonical_names():
    """Verify city name resolution doesn't produce title-cased airport codes (LOW #11)."""
    assert _city_from_airport("CCU") == "Kolkata"
    assert _city_from_airport("STV") == "Surat"
    assert _city_from_airport("MAA") == "Chennai"
    assert _city_from_airport("DXB") == "Dubai"
    assert _city_from_airport("GOI") == "Goa"
    assert _city_from_airport("GOA") == "Genoa"
    assert _city_from_airport("AMD") == "Ahmedabad"


# ==============================================================================
# 2. Hotel Pricing Basis & Budget Normalization (MEDIUM #7)
# ==============================================================================

def test_hotel_budget_normalization_per_night_vs_full_stay():
    """Verify hotel offer pricing basis normalization."""
    constraints = {
        "budget_amount": 50000.0,
        "currency": "INR",
        "duration_days": 11,  # 10 nights
        "adults": 2,
    }

    # Case A: Offer priced per night (rate = 3000/night for 10 nights = 30000 stay)
    hotel_offers_per_night = [{
        "id": "h1",
        "price_total": 3000.0,
        "currency": "INR",
        "pricing_basis": "per_night",
        "name": "Beach Resort",
    }]
    budget_a = budget_from_live_data(constraints, [], hotel_offers_per_night)
    assert budget_a["hotel_estimate_inr"] == 30000.0  # 3000 * 10 nights
    assert budget_a["hotel_pricing_basis"] == "per_night"

    # Case B: Offer priced for full stay (rate = 25000 for total stay)
    hotel_offers_full_stay = [{
        "id": "h2",
        "price_total": 25000.0,
        "currency": "INR",
        "pricing_basis": "full_stay",
        "name": "Heritage Villa",
    }]
    budget_b = budget_from_live_data(constraints, [], hotel_offers_full_stay)
    assert budget_b["hotel_estimate_inr"] == 25000.0  # Not multiplied
    assert budget_b["hotel_pricing_basis"] == "full_stay"


# ==============================================================================
# 3. Thread & Trip Cross-Validation (HIGH #2, MEDIUM #5)
# ==============================================================================

def test_planner_service_trip_id_mismatch_raises_value_error():
    service = PlannerService()
    user_id = str(uuid.uuid4())
    thread_id = f"th_{uuid.uuid4()}"
    trip_a = str(uuid.uuid4())
    trip_b = str(uuid.uuid4())

    mock_thread = {
        "thread_id": thread_id,
        "user_id": user_id,
        "trip_id": trip_a,
    }

    with patch("services.trip_store.TripStore.get_thread", return_value=mock_thread):
        with pytest.raises(ValueError) as excinfo:
            service.plan_travel(
                user_input="Kolkata to Goa, Oct 14 to Oct 24, 2 travelers",
                thread_id=thread_id,
                authenticated_user_id=user_id,
                trip_id=trip_b,  # Mismatch: thread is bound to trip_a
            )
        assert "Trip ID mismatch" in str(excinfo.value)


# ==============================================================================
# 4. Canonical Invariant Validation (HIGH #2, MEDIUM #6)
# ==============================================================================

def test_canonical_invariant_validation_rejects_mismatches():
    service = PlannerService()
    user_id = str(uuid.uuid4())
    trip_id = str(uuid.uuid4())

    canonical_trip = {
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
        }
    }

    # Simulate LangGraph orchestrator output where destination was corrupted/hallucinated to Paris
    mismatched_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": {
            "destination": "Paris",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-24",
            "duration_days": 11,
            "travelers": 2,
            "budget": {"target_budget_inr": 45000.0, "currency": "INR"}
        }
    }

    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    with patch("services.trip_store.TripStore.get_trip", return_value=canonical_trip):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=mismatched_result):
                with pytest.raises(ValueError) as excinfo:
                    service.plan_travel(
                        user_input="Update my itinerary with cultural activities",
                        authenticated_user_id=user_id,
                        trip_id=trip_id,
                    )
                assert "Consistency violation: Plan destination" in str(excinfo.value)


# ==============================================================================
# 5. /api/travel Endpoint Remediation Tests (HIGH #1, HIGH #2, MEDIUM #5)
# ==============================================================================

def test_api_travel_thread_trip_mismatch_returns_400():
    from app import get_current_user
    client = TestClient(app)
    user_id = str(uuid.uuid4())
    thread_id = f"th_{uuid.uuid4()}"
    trip_a = str(uuid.uuid4())
    trip_b = str(uuid.uuid4())

    mock_user = MagicMock()
    mock_user.id = user_id

    mock_thread = {
        "thread_id": thread_id,
        "user_id": user_id,
        "trip_id": trip_a,
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("services.trip_store.TripStore.get_thread", return_value=mock_thread):
            response = client.post(
                "/api/travel",
                json={
                    "message": "Trip details",
                    "thread_id": thread_id,
                    "trip_id": trip_b,  # Mismatch
                },
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Trip ID mismatch" in data["error"]
    finally:
        app.dependency_overrides.clear()


def test_api_travel_unauthorized_thread_returns_403():
    from app import get_current_user
    client = TestClient(app)
    user_id = str(uuid.uuid4())
    thread_id = f"th_{uuid.uuid4()}"

    mock_user = MagicMock()
    mock_user.id = user_id

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("services.trip_store.TripStore.get_thread", return_value=None):
            response = client.post(
                "/api/travel",
                json={
                    "message": "Trip details",
                    "thread_id": thread_id,
                },
            )
            assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_api_travel_failure_propagation_never_forces_success_true():
    """Verify that if planning fails, success=False is preserved and not persisted (HIGH #1)."""
    from app import get_current_user
    client = TestClient(app)
    user_id = str(uuid.uuid4())
    trip_id = str(uuid.uuid4())

    mock_user = MagicMock()
    mock_user.id = user_id

    trip_record = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
    }

    failed_plan_result = {
        "success": False,
        "error": "Flight search provider unavailable.",
        "status_code": 503,
        "plan_json": {},
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("services.trip_store.TripStore.get_trip", return_value=trip_record):
            with patch("app.run_travel_agent", return_value=failed_plan_result):
                with patch("services.trip_store.TripStore.update_trip") as mock_update:
                    response = client.post(
                        "/api/travel",
                        json={
                            "message": "Kolkata to Goa, Oct 14 to Oct 24, 2 travelers",
                            "trip_id": trip_id,
                        },
                    )
                    assert response.status_code == 503
                    data = response.json()
                    assert data["success"] is False
                    assert (
                        "Flight search provider unavailable" in data["error"]
                        or "Upstream AI travel planning service is temporarily overloaded" in data["error"]
                    )
                    mock_update.assert_not_called()
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# 6. HITL Preservation Test
# ==============================================================================

def test_hitl_fields_preserved_in_serialization():
    from backend import _serialize_result

    raw_result = {
        "messages": [MagicMock(content="Here is your travel plan.")],
        "approval_request": "Please review flight CCU-GOI and hotel Beach Resort.",
        "itinerary": "Day 1: Arrival in Goa...",
        "guardrail_allowed": True,
        "planning_blocked": False,
    }

    serialized = _serialize_result(raw_result, "th_test_hitl")
    assert serialized["success"] is True
    assert serialized["approval_request"] == "Please review flight CCU-GOI and hotel Beach Resort."
    assert "requires_approval" in serialized


# ==============================================================================
# 7. Chunk B — Issue 1: Unknown Hotel Pricing Basis (MEDIUM #7 re-audit)
# ==============================================================================

@pytest.mark.parametrize("basis,price,nights,expect_included", [
    ("per_night",  3000.0, 0, True),   # recognized: per_night, must be multiplied
    ("full_stay",  25000.0, 0, True),  # recognized: full_stay, taken as-is
    ("mystery",    3000.0, 0, False),  # unrecognized: must be SKIPPED
    (None,         3000.0, 0, False),  # missing basis: must be SKIPPED
    ("unknown",    3000.0, 0, False),  # explicit "unknown" basis: must be SKIPPED
])
def test_hotel_unrecognized_pricing_basis_skipped(basis, price, nights, expect_included):
    """Unknown/missing pricing_basis must never be silently treated as full_stay."""
    constraints = {"duration_days": 5, "adults": 1}
    offer: dict = {"id": "h_test", "price_total": price, "currency": "INR"}
    if nights:
        offer["nights"] = nights
    if basis is not None:
        offer["pricing_basis"] = basis

    result = budget_from_live_data(constraints, [], [offer])

    if expect_included:
        # Offer was used — hotel_estimate must differ from pure fallback
        assert result["hotel_estimate_inr"] > 0
        assert result["hotel_pricing_basis"] in ("per_night", "full_stay")
    else:
        # Offer was SKIPPED — must fall back to text-snippet / planning estimate
        assert result["hotel_pricing_basis"] in ("estimated_per_night", "unknown")
        # The raw price must NOT have leaked directly into the estimate
        assert result["hotel_estimate_inr"] != price


# ==============================================================================
# 8. Chunk B — Issue 2: Missing required canonical plan fields (re-audit)
# ==============================================================================

def test_canonical_invariant_raises_on_falsy_duration():
    """Canonical trip has duration_days but plan produces no duration — must be rejected.

    Old guard: `if expected_duration and plan_duration:` silently skipped when
    plan_duration was falsy (None, 0, "").  The new guard raises explicitly.

    This test simulates the case where plan_json omits duration_days and
    compatibility_constraints also lacks it (by patching trip_request_to_legacy),
    so plan_duration resolves to None — triggering the new missing-field error.
    """
    service = PlannerService()
    user_id = str(uuid.uuid4())
    trip_id = str(uuid.uuid4())

    canonical_trip = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "origin": "Kolkata",
        "start_date": "2026-10-14",
        "end_date": "2026-10-24",
        "duration_days": 11,
        "travelers_count": 2,
        "budget": None,
        "currency": "INR",
        "metadata": {
            "origin": "CCU", "destination": "GOI",
            "origin_city": "Kolkata", "destination_city": "Goa",
            "departure_date": "2026-10-14", "return_date": "2026-10-24",
            "duration_days": 11, "adults": 2,
        },
    }

    # plan_json has no duration_days key at all; airports and currency are present
    # (required by Chunk C rules since canonical has IATA codes CCU/GOI and currency INR).
    plan_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": {
            "destination": "Goa",
            "destination_airport": "GOI",
            "origin": "Kolkata",
            "origin_airport": "CCU",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-24",
            # duration_days intentionally absent
            "travelers": 2,
            "budget": {"currency": "INR"},
        },
    }

    # Patch trip_request_to_legacy so compatibility_constraints also lacks duration_days.
    # This forces plan_duration to resolve to None, hitting the new "missing required" guard.
    def _legacy_without_duration(trip_request):
        result = {
            "origin": "CCU", "origin_city": "Kolkata", "origin_country": "",
            "destination": "GOI", "destination_city": "Goa", "destination_country": "",
            "departure_date": "2026-10-14", "return_date": "2026-10-24",
            "adults": 2, "budget_amount": None, "currency": "INR",
            # duration_days intentionally absent from compatibility projection
        }
        return result

    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    with patch("services.trip_store.TripStore.get_trip", return_value=canonical_trip):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=plan_result):
                with patch("services.planner_service.trip_request_to_legacy", side_effect=_legacy_without_duration):
                    with pytest.raises(ValueError) as excinfo:
                        service.plan_travel(
                            user_input="Update my itinerary",
                            authenticated_user_id=user_id,
                            trip_id=trip_id,
                        )
                    assert "Consistency violation" in str(excinfo.value)
                    assert "duration" in str(excinfo.value).lower()


def test_canonical_invariant_does_not_raise_on_correct_plan():
    """A plan that correctly echoes all canonical fields must pass validation."""
    service = PlannerService()
    user_id = str(uuid.uuid4())
    trip_id = str(uuid.uuid4())

    canonical_trip = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "origin": "Kolkata",
        "start_date": "2026-10-14",
        "end_date": "2026-10-24",
        "duration_days": 11,
        "travelers_count": 2,
        "budget": None,
        "currency": "INR",
        "metadata": {
            "origin": "CCU", "destination": "GOI",
            "origin_city": "Kolkata", "destination_city": "Goa",
            "departure_date": "2026-10-14", "return_date": "2026-10-24",
            "duration_days": 11, "adults": 2,
        },
    }

    # Fully correct plan: all fields that canonical has must be present and matching.
    # Chunk C tightened rules: origin_airport, destination_airport, and budget currency
    # are now required when canonical has IATA codes (CCU, GOI) and currency (INR).
    plan_result = {
        "messages": [],
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": {
            "destination": "Goa",
            "destination_airport": "GOI",
            "origin": "Kolkata",
            "origin_airport": "CCU",
            "departure_date": "2026-10-14",
            "return_date": "2026-10-24",
            "duration_days": 11,
            "travelers": 2,
            "budget": {"currency": "INR"},
        },
    }

    mock_thread = {"id": f"th_{uuid.uuid4()}", "trip_id": trip_id, "user_id": user_id}
    with patch("services.trip_store.TripStore.get_trip", return_value=canonical_trip):
        with patch("services.trip_store.TripStore.create_thread", return_value=mock_thread):
            with patch("backend.travel_graph.invoke", return_value=plan_result):
                # Must complete without raising
                result = service.plan_travel(
                    user_input="Update my itinerary",
                    authenticated_user_id=user_id,
                    trip_id=trip_id,
                )
                assert isinstance(result, dict)


# ==============================================================================
# 9. Chunk B — Issue 3: Persistence failure must propagate as 500 (re-audit)
# ==============================================================================

def test_api_travel_persistence_failure_returns_500_not_success():
    """If trip metadata persistence raises, the response must be 500 success=False."""
    from app import get_current_user
    client = TestClient(app)
    user_id = str(uuid.uuid4())
    trip_id = str(uuid.uuid4())

    mock_user = MagicMock()
    mock_user.id = user_id

    trip_record = {
        "id": trip_id,
        "user_id": user_id,
        "destination": "Goa",
        "metadata": {},
    }

    success_plan_result = {
        "success": True,
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": {"destination": "Goa"},
        "approval_request": "Please review the plan.",
        "requires_approval": True,
        "messages": [],
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("services.trip_store.TripStore.get_trip", return_value=trip_record):
            with patch("app.run_travel_agent", return_value=success_plan_result):
                with patch(
                    "services.trip_store.TripStore.update_trip",
                    side_effect=Exception("DB connection lost"),
                ):
                    response = client.post(
                        "/api/travel",
                        json={
                            "message": "Kolkata to Goa, Oct 14 to Oct 24, 2 travelers",
                            "trip_id": trip_id,
                        },
                    )
                    assert response.status_code == 500
                    data = response.json()
                    assert data["success"] is False
    finally:
        app.dependency_overrides.clear()
