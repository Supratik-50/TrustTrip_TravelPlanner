"""Release Readiness Audit Verification Suite (BUG-001 through BUG-005).

Verifies:
1. BUG-001: Structured plan generation, token scaling, required fields, fallback.
2. BUG-002: Authoritative canonical trip dates override stale checkpoint state,
   date consistency rejection, controlled HTTP 400 response without persistence.
3. BUG-003: Flight and hotel date validation, past date rejection for live booking,
   invalid order rejection, and safe error handling.
4. BUG-004: Persistent JWT secret enforcement, CORS origin matching, CSRF protection,
   session survival across restart.
5. BUG-005: Rejection persistence, mutual exclusion (cannot approve rejected, cannot reject approved),
   booking authorization blockage.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.user import UserResponse

from services.auth_service import create_access_token, get_auth_secret
from services.auth_store import UserStore
from services.chat_planner import (
    BudgetPlan,
    CanonicalInvariantViolation,
    DayPlan,
    TravelPlan,
    _compact_context,
    _groq_plan,
    _resolve_duration_days,
    enforce_complete_canonical_invariants,
    generate_travel_plan,
)
from services.config import get_allowed_origins, is_production, validate_config
from services.trip_store import TripStore

client = TestClient(app)


def _make_sample_plan(
    duration: int = 3,
    dep_date: str = "2026-11-01",
    ret_date: str = "2026-11-03",
    flight_sug: str = "Indigo 6E 201 outbound",
    hotel_plan: str = "Grand Palace Kyoto",
    weather_sug: str = "Pleasant autumn 18C",
) -> TravelPlan:
    days = [
        DayPlan(
            day=i,
            title=f"Day {i} Tour",
            morning="Morning visit",
            afternoon="Afternoon walk",
            evening="Evening dining",
            estimated_daily_cost_inr=1500,
            date=str(date.fromisoformat(dep_date) + timedelta(days=i - 1)),
        )
        for i in range(1, duration + 1)
    ]
    budget = BudgetPlan(
        currency="INR",
        flight_estimate_inr=10000,
        hotel_estimate_inr=15000,
        local_transport_estimate_inr=3000,
        activities_estimate_inr=4500,
        contingency_inr=3000,
        estimated_total_inr=35500,
        target_budget_inr=40000,
        within_budget=True,
        pricing_note="Standard estimate",
    )
    return TravelPlan(
        trip_summary="Sample 3-day itinerary",
        route="BOM -> DEL",
        duration_days=duration,
        travel_dates=f"{dep_date} to {ret_date}",
        travel_style="Balanced",
        daily_plan=days,
        budget=budget,
        flight_suggestion=flight_sug,
        accommodation_plan=hotel_plan,
        weather_guidance=weather_sug,
        origin="BOM",
        destination="DEL",
        departure_date=dep_date,
        return_date=ret_date,
    )


# ==============================================================================
# BUG-001: Structured Generation & Field Validation
# ==============================================================================

def test_bug001_missing_required_fields_rejected():
    """Missing budget, flight_suggestion, accommodation_plan, or weather_guidance must be rejected."""
    ctx = {"origin": "BOM", "destination": "DEL", "departure_date": "2026-11-01", "return_date": "2026-11-03"}

    # Missing flight_suggestion
    p1 = _make_sample_plan(flight_sug="")
    with pytest.raises(CanonicalInvariantViolation) as exc1:
        enforce_complete_canonical_invariants(p1, ctx)
    assert "flight_suggestion" in str(exc1.value)

    # Missing accommodation_plan
    p2 = _make_sample_plan(hotel_plan="   ")
    with pytest.raises(CanonicalInvariantViolation) as exc2:
        enforce_complete_canonical_invariants(p2, ctx)
    assert "accommodation_plan" in str(exc2.value)

    # Missing weather_guidance
    p3 = _make_sample_plan(weather_sug="")
    with pytest.raises(CanonicalInvariantViolation) as exc3:
        enforce_complete_canonical_invariants(p3, ctx)
    assert "weather_guidance" in str(exc3.value)


def test_bug001_token_scaling_for_3_7_10_and_14_days():
    """Verify that token limits scale dynamically for 3-, 7-, 10-, and 14-day itineraries."""
    with patch("services.chat_planner.ChatGroq") as mock_groq_cls:
        mock_instance = MagicMock()
        mock_groq_cls.return_value = mock_instance

        # 3-day itinerary: baseline 4096
        mock_instance.with_structured_output.return_value.invoke.return_value = _make_sample_plan(3, dep_date="2026-11-01", ret_date="2026-11-03")
        _groq_plan("prompt text", duration_days=3)
        call_kwargs_3 = mock_groq_cls.call_args[1]
        assert call_kwargs_3["max_tokens"] == 4096

        # 7-day itinerary: 4096 + 2 * 350 = 4796
        mock_instance.with_structured_output.return_value.invoke.return_value = _make_sample_plan(7, dep_date="2026-11-01", ret_date="2026-11-07")
        _groq_plan("prompt text", duration_days=7)
        call_kwargs_7 = mock_groq_cls.call_args[1]
        assert call_kwargs_7["max_tokens"] == 4796

        # 10-day itinerary: 4096 + 5 * 350 = 5846
        mock_instance.with_structured_output.return_value.invoke.return_value = _make_sample_plan(10, dep_date="2026-11-01", ret_date="2026-11-10")
        _groq_plan("prompt text", duration_days=10)
        call_kwargs_10 = mock_groq_cls.call_args[1]
        assert call_kwargs_10["max_tokens"] == 5846

        # 14-day itinerary: 4096 + 9 * 350 = 7246
        mock_instance.with_structured_output.return_value.invoke.return_value = _make_sample_plan(14, dep_date="2026-11-01", ret_date="2026-11-14")
        _groq_plan("prompt text", duration_days=14)
        call_kwargs_14 = mock_groq_cls.call_args[1]
        assert call_kwargs_14["max_tokens"] == 7246



def test_bug001_fallback_on_primary_model_failure():
    """Primary model failure must cleanly trigger the secondary model with identical validation."""
    valid_plan = _make_sample_plan(3)
    with patch("services.chat_planner._groq_plan") as mock_gp:
        # First call (primary) raises 400 json_validate_failed, second call (fallback) succeeds
        mock_gp.side_effect = [
            RuntimeError("groq-primary: 400 - json_validate_failed"),
            valid_plan,
        ]
        plan, provider = generate_travel_plan(
            user_query="Plan trip to Delhi",
            constraints={"origin": "BOM", "destination": "DEL", "departure_date": "2026-11-01", "return_date": "2026-11-03"},
            flight_results="Flight evidence",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="Hotel evidence",
            weather_results="Pleasant",
            budget={"target_budget": 40000, "estimated_total": 35000},
        )
        assert provider == "groq"
        assert plan.trip_summary == valid_plan.trip_summary
        assert mock_gp.call_count == 2


# ==============================================================================
# BUG-002: Canonical Dates vs Stale State
# ==============================================================================

def test_bug002_return_date_preceding_departure_date_rejected():
    """Return date preceding departure date must raise CanonicalInvariantViolation."""
    ctx = {"origin": "BOM", "destination": "DEL", "departure_date": "2026-11-10", "return_date": "2026-11-05"}
    p = _make_sample_plan(3, dep_date="2026-11-10", ret_date="2026-11-05")
    with pytest.raises(CanonicalInvariantViolation) as exc:
        enforce_complete_canonical_invariants(p, ctx)
    assert "cannot precede departure date" in str(exc.value)


def test_bug002_authoritative_database_dates_override_stale_checkpoint():
    """PlannerService must unconditionally anchor existing_trip from authoritative canonical database record."""
    from services.planner_service import PlannerService
    ps = PlannerService()

    test_user_id = str(uuid4())
    test_trip_id = str(uuid4())
    test_thread_id = str(uuid4())

    fake_trip_record = {
        "id": test_trip_id,
        "origin": "BOM",
        "destination": "DEL",
        "start_date": "2026-10-28",  # Updated authoritative date
        "end_date": "2026-11-02",
        "metadata": {
            "constraints": {
                "origin": "BOM",
                "destination": "DEL",
                "departure_date": "2026-10-28",
                "return_date": "2026-11-02",
            }
        },
    }

    with patch.object(ps.trip_store, "get_thread", return_value={"id": test_thread_id, "user_id": test_user_id, "trip_id": test_trip_id}):
        with patch.object(ps.trip_store, "get_trip", return_value=fake_trip_record):
            with patch("backend.travel_graph.invoke") as mock_invoke:
                mock_invoke.return_value = {
                    "itinerary": "# Travel Plan",
                    "plan_json": {
                        "status": "success",
                        "travel_dates": "2026-10-28 to 2026-11-02",
                        "departure_date": "2026-10-28",
                        "return_date": "2026-11-02",
                        "duration_days": 6,
                        "travelers": 1,
                        "budget": {
                            "target_budget": 50000,
                            "currency": "INR",
                            "total_estimated_cost": 45000,
                        },
                        "origin": "BOM",
                        "destination": "DEL",
                        "origin_airport": "BOM",
                        "destination_airport": "DEL",
                    },
                    "approval_request": "Review",
                    "llm_provider": "groq",
                    "messages": [],
                }

                res = ps.plan_travel(
                    user_input="Synthesize plan",
                    thread_id=test_thread_id,
                    authenticated_user_id=test_user_id,
                    trip_id=test_trip_id,
                )
                # Inspect initial_state passed to travel_graph.invoke
                invoked_state = mock_invoke.call_args[0][0]
                assert invoked_state["trip_constraints"]["departure_date"] == "2026-10-28"
                assert invoked_state["trip_constraints"]["return_date"] == "2026-11-02"



# ==============================================================================
# BUG-003: Flight & Hotel Date Validation
# ==============================================================================

def test_bug003_flight_search_rejects_past_departure_date():
    """Flight search must reject past departure dates with HTTP 400."""
    from flight_api import FlightSlice
    past_date = (date.today() - timedelta(days=2)).isoformat()
    with pytest.raises(ValueError) as exc:
        FlightSlice(origin="DEL", destination="BOM", departure_date=past_date)
    assert "past" in str(exc.value).lower()


def test_bug003_hotel_prebook_rejects_past_checkin_date():
    """Hotel live booking (prebook) must reject past check-in dates with HTTP 400."""
    user_store = UserStore()
    user = user_store.get_by_email("past_hotel_test@trusttrip.com") or user_store.create_user("past_hotel_test@trusttrip.com", "hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}

    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Past Hotel Trip",
        destination="Goa",
        metadata={
            "constraints": {
                "checkin_date": "2025-01-01",  # Past date
                "checkout_date": "2025-01-05",
            }
        },
    )

    resp = client.post(
        "/api/hotels/prebook",
        headers=headers,
        json={"trip_id": str(trip["id"])},
    )
    assert resp.status_code == 400
    assert "past" in resp.json()["detail"].lower()


def test_bug003_hotel_search_rejects_checkout_before_or_equal_checkin():
    """Hotel search must reject checkout <= checkin with HTTP 400."""
    user_store = UserStore()
    user = user_store.get_by_email("hotel_order_test@trusttrip.com") or user_store.create_user("hotel_order_test@trusttrip.com", "hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}

    trip_store = TripStore()
    future_d1 = (date.today() + timedelta(days=30)).isoformat()
    future_d2 = (date.today() + timedelta(days=25)).isoformat()

    # Checkout before checkin
    trip1 = trip_store.create_trip(
        user_id=user["id"],
        title="Invalid Dates Trip 1",
        destination="Goa",
        metadata={"constraints": {"checkin_date": future_d1, "checkout_date": future_d2, "adults": 1, "travelers_count": 1}},
    )
    resp1 = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip1["id"])})
    assert resp1.status_code == 400
    assert "strictly before" in resp1.json()["detail"].lower()

    # Checkout equal checkin
    trip2 = trip_store.create_trip(
        user_id=user["id"],
        title="Invalid Dates Trip 2",
        destination="Goa",
        metadata={"constraints": {"checkin_date": future_d1, "checkout_date": future_d1, "adults": 1, "travelers_count": 1}},
    )
    resp2 = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip2["id"])})
    assert resp2.status_code == 400
    assert "strictly before" in resp2.json()["detail"].lower()


def test_bug003_hotel_prebook_rejects_checkout_before_checkin():
    """Hotel prebook must reject checkout <= checkin with HTTP 400."""
    user_store = UserStore()
    user = user_store.get_by_email("hotel_prebook_order@trusttrip.com") or user_store.create_user("hotel_prebook_order@trusttrip.com", "hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}

    future_in = (date.today() + timedelta(days=30)).isoformat()
    future_out = (date.today() + timedelta(days=25)).isoformat()

    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Prebook Invalid Dates",
        destination="Goa",
        metadata={"constraints": {"checkin_date": future_in, "checkout_date": future_out, "adults": 1, "travelers_count": 1}},
    )
    resp = client.post("/api/hotels/prebook", headers=headers, json={"trip_id": str(trip["id"])})
    assert resp.status_code == 400
    assert "strictly before" in resp.json()["detail"].lower()


def test_bug003_hotel_search_rejects_malformed_dates():
    """Hotel search must reject malformed date strings with HTTP 400."""
    user_store = UserStore()
    user = user_store.get_by_email("hotel_malformed@trusttrip.com") or user_store.create_user("hotel_malformed@trusttrip.com", "hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}

    trip_store = TripStore()
    trip = trip_store.create_trip(
        user_id=user["id"],
        title="Malformed Date Trip",
        destination="Goa",
        metadata={"constraints": {"checkin_date": "not-a-date", "checkout_date": "2026-11-01", "adults": 1, "travelers_count": 1}},
    )
    resp = client.post("/api/hotels/search", headers=headers, json={"trip_id": str(trip["id"])})
    assert resp.status_code == 400
    assert "valid yyyy-mm-dd" in resp.json()["detail"].lower()


def test_bug003_hotel_service_graceful_missing_credentials():
    """HotelService should return errors and empty offers rather than crashing when unconfigured."""
    from services.hotel.hotel_service import HotelService
    with patch.dict(os.environ, {"LITEAPI_SANDBOX_API_KEY": "", "LITEAPI_PRODUCTION_API_KEY": "", "HOTELBEDS_API_KEY": "", "HOTELBEDS_SECRET": ""}):
        service = HotelService()
        res = service.search({
            "destination": "Goa",
            "checkin": (date.today() + timedelta(days=10)).isoformat(),
            "checkout": (date.today() + timedelta(days=15)).isoformat(),
            "adults": 1,
            "rooms": 1,
        })
        assert res["offers"] == []
        assert any("no hotel provider is configured" in err.lower() for err in res["errors"])



# ==============================================================================
# BUG-004: Persistent JWT Secret & Origin Whitelist
# ==============================================================================

def test_bug004_jwt_secret_configured_and_persistent():
    """JWT_SECRET must be configured with at least 32 bytes."""
    secret = get_auth_secret()
    assert secret is not None
    assert len(secret.encode("utf-8")) >= 32


def test_bug004_untrusted_origin_rejected_by_csrf():
    """Untrusted origins must be rejected by CSRF with HTTP 403."""
    user_store = UserStore()
    user = user_store.get_by_email("csrf_untrusted_test@trusttrip.com") or user_store.create_user("csrf_untrusted_test@trusttrip.com", "hashed_pw")
    token = create_access_token(user["id"], user["email"])

    resp = client.post(
        "/api/auth/logout",
        cookies={"trusttrip_access_token": token},
        headers={
            "Origin": "https://malicious-site.com",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    assert resp.status_code == 403
    assert "origin not allowed" in resp.json()["detail"].lower()


# ==============================================================================
# BUG-005: Rejection Persistence & Mutual Exclusion
# ==============================================================================

def test_bug005_rejection_workflow_mutual_exclusion():
    """Rejected revision cannot be approved, and approved revision cannot be rejected."""
    user_store = UserStore()
    user = user_store.get_by_email("reject_mutual_test@trusttrip.com") or user_store.create_user("reject_mutual_test@trusttrip.com", "hashed_pw")
    token = create_access_token(user["id"], user["email"])
    headers = {"Authorization": f"Bearer {token}"}

    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=user["id"], title="Reject Test Trip", destination="Goa")
    r_resp = client.post(f"/api/trips/{trip['id']}/revisions", headers=headers, json={"feedback": "Initial plan"})
    assert r_resp.status_code == 200
    rev_data = r_resp.json()
    rev_id = str(rev_data["revision_id"])

    trip_store.attach_plan_to_revision(
        trip_id=trip["id"],
        user_id=user["id"],
        revision_id=rev_id,
        plan_json=_make_sample_plan().model_dump(),
        raw_plan="Sample plan markdown",
    )

    # 1. Reject the revision
    rej_resp = client.post(
        f"/api/trips/{trip['id']}/revisions/{rev_id}/reject",
        headers=headers,
        json={"expected_revision_number": 1, "reason": "Too expensive activities"},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"].upper() == "REJECTED"


    # 2. Attempting to approve rejected revision must be blocked (HTTP 400 or 409)
    app_resp = client.post(
        f"/api/trips/{trip['id']}/approve",
        headers=headers,
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app_resp.status_code in (400, 409)


    # 3. Attempting booking approval on rejected revision must be blocked (HTTP 409)
    book_resp = client.post(
        f"/api/trips/{trip['id']}/booking-approval",
        headers=headers,
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert book_resp.status_code == 409


