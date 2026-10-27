"""Tests for Itinerary Duration and LLM Fallback Remediation.

Validates:
1. Exact 5-, 7-, 10-, and 12-day daily plans succeed through the planner.
2. 7-day request returning only 5 or 6 days is rejected and triggers configured fallback.
3. Missing, duplicate, out-of-range, or incorrectly dated days fail canonical invariant validation.
4. Malformed or truncated primary-model output triggers fallback.
5. Primary provider errors (429, 503, timeout) trigger fallback.
6. When both models fail, existing structured outage / unavailable result is returned without fabrication.
7. Configurable GPT-OSS and Qwen model selection via GROQ_MODEL.
8. Gemini model configuration and invalid model identifier safeguard.
9. Canonical dates, duration, booking offer identities, and budget invariants remain intact.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from services.chat_planner import (
    BudgetPlan,
    DayPlan,
    TravelPlan,
    CanonicalInvariantViolation,
    TripDurationUnsupportedError,
    generate_travel_plan,
    enforce_complete_canonical_invariants,
    _resolve_duration_days,
    _groq_model,
    _groq_fallback_model,
    _gemini_model,
)


def _make_dummy_budget() -> BudgetPlan:
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
        pricing_note="Provider-backed estimates",
    )


def _make_test_plan(
    num_days: int,
    origin: str = "Bengaluru",
    destination: str = "Kochi",
    origin_code: str = "BLR",
    dest_code: str = "COK",
    dep_date: str = "2026-11-01",
    ret_date: str = "2026-11-05",
    pax: int = 2,
    custom_days: list[DayPlan] | None = None,
) -> TravelPlan:
    if custom_days is not None:
        days = custom_days
    else:
        from datetime import date, timedelta
        base_d = date.fromisoformat(dep_date)
        days = [
            DayPlan(
                day=i,
                title=f"Explore {destination} — Day {i}",
                morning=f"Morning activity {i}",
                afternoon=f"Afternoon activity {i}",
                evening=f"Evening dinner {i}",
                estimated_daily_cost_inr=2500.0,
                notes=[],
                date=str(base_d + timedelta(days=i - 1)),
            )
            for i in range(1, num_days + 1)
        ]

    return TravelPlan(
        trip_summary=f"A {num_days}-day trip to {destination}",
        route=f"{origin} → {destination}",
        duration_days=num_days,
        travel_dates=f"{dep_date} to {ret_date}",
        travel_style="Balanced",
        daily_plan=days,
        budget=_make_dummy_budget(),
        flight_suggestion="Recommended flight",
        accommodation_plan="Recommended hotel",
        weather_guidance="Pleasant weather",
        origin=origin,
        destination=destination,
        origin_airport=origin_code,
        destination_airport=dest_code,
        departure_date=dep_date,
        return_date=ret_date,
        travelers=pax,
        passenger_count=pax,
        adults=pax,
    )


# =========================================================================
# 1. Exactly 5, 7, 10, and 12 Daily Plans
# =========================================================================

@pytest.mark.parametrize("duration,dep,ret", [
    (5, "2026-11-01", "2026-11-05"),
    (7, "2026-11-01", "2026-11-07"),
    (10, "2026-11-01", "2026-11-10"),
    (12, "2026-11-01", "2026-11-12"),
])
def test_exact_5_7_10_12_day_itineraries_supported(duration, dep, ret):
    """Verify 5, 7, 10, and 12 day itineraries proceed through the planner and return exact day counts."""
    constraints = {
        "origin": "Bengaluru",
        "origin_city": "Bengaluru",
        "origin_airport": "BLR",
        "destination": "Kochi",
        "destination_city": "Kochi",
        "destination_airport": "COK",
        "departure_date": dep,
        "return_date": ret,
        "adults": 2,
    }

    mock_plan = _make_test_plan(duration, dep_date=dep, ret_date=ret)

    with patch("services.chat_planner._groq_plan", return_value=mock_plan):
        plan, provider = generate_travel_plan(
            user_query=f"Plan a {duration}-day trip to Kochi",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    assert provider == "groq"
    assert plan.duration_days == duration
    assert len(plan.daily_plan) == duration
    assert [d.day for d in plan.daily_plan] == list(range(1, duration + 1))


# =========================================================================
# 2. GPT-OSS 120B Primary and Qwen 3.8 27B Fallback Invariants (Mocked)
# =========================================================================

def test_gpt_oss_120b_succeeds_qwen_not_called_mocked():
    """1. When GPT-OSS 120B primary succeeds, Qwen fallback is NOT called."""
    dep = "2026-11-01"
    ret = "2026-11-07"
    constraints = {
        "origin": "Bengaluru",
        "origin_airport": "BLR",
        "destination": "Kochi",
        "destination_airport": "COK",
        "departure_date": dep,
        "return_date": ret,
        "adults": 2,
    }
    complete_plan = _make_test_plan(7, dep_date=dep, ret_date=ret)

    with patch("services.chat_planner._groq_plan", return_value=complete_plan) as mock_groq:
        plan, provider = generate_travel_plan(
            user_query="Plan a 7-day trip to Kochi",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    # Must be called exactly once (for the primary model)
    assert mock_groq.call_count == 1
    call_kwargs = mock_groq.call_args.kwargs
    assert call_kwargs.get("model_name") == "openai/gpt-oss-120b"
    assert provider == "groq"
    assert plan.duration_days == 7
    assert len(plan.daily_plan) == 7


def test_gpt_oss_120b_fails_qwen_succeeds_mocked():
    """2. When GPT-OSS 120B primary fails, Qwen fallback is called and succeeds."""
    dep = "2026-11-01"
    ret = "2026-11-07"
    constraints = {
        "origin": "Bengaluru",
        "origin_airport": "BLR",
        "destination": "Kochi",
        "destination_airport": "COK",
        "departure_date": dep,
        "return_date": ret,
        "adults": 2,
    }
    qwen_plan = _make_test_plan(7, dep_date=dep, ret_date=ret)

    # First call (GPT-OSS 120B) fails with 503; second call (Qwen 3.8 27B) succeeds
    with patch("services.chat_planner._groq_plan", side_effect=[
        RuntimeError("Groq 503 Service Unavailable"),
        qwen_plan,
    ]) as mock_groq:
        plan, provider = generate_travel_plan(
            user_query="Plan a 7-day trip to Kochi",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    assert mock_groq.call_count == 2
    # Call 1: Primary (openai/gpt-oss-120b)
    assert mock_groq.call_args_list[0].kwargs.get("model_name") == "openai/gpt-oss-120b"
    # Call 2: Fallback (qwen/qwen3.8-27b)
    assert mock_groq.call_args_list[1].kwargs.get("model_name") == "qwen/qwen3.8-27b"
    assert provider == "groq"
    assert plan.duration_days == 7
    assert len(plan.daily_plan) == 7


def test_gpt_oss_120b_incomplete_itinerary_triggers_qwen_fallback_mocked():
    """3. When GPT-OSS 120B returns an incomplete itinerary (e.g. 5 days for a 7-day trip), Qwen fallback is attempted."""
    dep = "2026-11-01"
    ret = "2026-11-07"
    constraints = {
        "origin": "Bengaluru",
        "origin_airport": "BLR",
        "destination": "Kochi",
        "destination_airport": "COK",
        "departure_date": dep,
        "return_date": ret,
        "adults": 2,
    }

    # Primary returns undergenerated 5 days; Qwen returns complete 7 days
    incomplete_gpt_plan = _make_test_plan(5, dep_date=dep, ret_date=ret)
    complete_qwen_plan = _make_test_plan(7, dep_date=dep, ret_date=ret)

    with patch("services.chat_planner._groq_plan", side_effect=[
        incomplete_gpt_plan,
        complete_qwen_plan,
    ]) as mock_groq:
        plan, provider = generate_travel_plan(
            user_query="Plan a 7-day trip to Kochi",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    assert mock_groq.call_count == 2
    assert mock_groq.call_args_list[0].kwargs.get("model_name") == "openai/gpt-oss-120b"
    assert mock_groq.call_args_list[1].kwargs.get("model_name") == "qwen/qwen3.8-27b"
    assert provider == "groq"
    assert plan.duration_days == 7
    assert len(plan.daily_plan) == 7


# =========================================================================
# 3. Missing, Duplicate, Out-of-Range, or Incorrectly Dated Days
# =========================================================================

def test_duplicate_days_rejected():
    """Daily plan with duplicate day numbers (e.g., 1, 2, 2, 4, 5) raises CanonicalInvariantViolation."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    canonical_ctx = {"departure_date": dep, "return_date": ret}

    duplicate_days = [
        DayPlan(day=1, title="D1", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=2, title="D2a", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=2, title="D2b", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=4, title="D4", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=5, title="D5", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
    ]
    plan = _make_test_plan(5, dep_date=dep, ret_date=ret, custom_days=duplicate_days)

    with pytest.raises(CanonicalInvariantViolation) as exc:
        enforce_complete_canonical_invariants(plan, canonical_ctx)
    assert "duplicate days" in str(exc.value)


def test_missing_and_out_of_range_days_rejected():
    """Daily plan with missing or out-of-range days (e.g., 1, 2, 3, 4, 8) raises CanonicalInvariantViolation."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    canonical_ctx = {"departure_date": dep, "return_date": ret}

    bad_days = [
        DayPlan(day=1, title="D1", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=2, title="D2", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=3, title="D3", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=4, title="D4", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=8, title="D8", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
    ]
    plan = _make_test_plan(5, dep_date=dep, ret_date=ret, custom_days=bad_days)

    with pytest.raises(CanonicalInvariantViolation) as exc:
        enforce_complete_canonical_invariants(plan, canonical_ctx)
    assert "invalid day sequence" in str(exc.value)


def test_incorrectly_dated_day_rejected():
    """DayPlan with an explicit date not matching the sequential calendar date is rejected."""
    dep = "2026-11-01"
    ret = "2026-11-03"
    canonical_ctx = {"departure_date": dep, "return_date": ret}

    wrong_date_days = [
        DayPlan(day=1, title="D1", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000, date="2026-11-01"),
        DayPlan(day=2, title="D2", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000, date="2026-11-02"),
        DayPlan(day=3, title="D3", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000, date="2026-11-09"),  # Wrong date
    ]
    plan = _make_test_plan(3, dep_date=dep, ret_date=ret, custom_days=wrong_date_days)

    with pytest.raises(CanonicalInvariantViolation) as exc:
        enforce_complete_canonical_invariants(plan, canonical_ctx)
    assert "date mismatch" in str(exc.value)


# =========================================================================
# 4. Malformed or Truncated Output Triggers Fallback (Mocked)
# =========================================================================

def test_malformed_truncated_primary_output_triggers_qwen_fallback_mocked():
    """JSON decoding error or output truncation in primary model triggers Qwen fallback."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    constraints = {"departure_date": dep, "return_date": ret, "origin": "Bengaluru", "destination": "Kochi"}
    complete_qwen_plan = _make_test_plan(5, dep_date=dep, ret_date=ret)

    with patch("services.chat_planner._groq_plan", side_effect=[
        ValueError("Unterminated string starting at line 1 column 980"),
        complete_qwen_plan,
    ]):
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    assert provider == "groq"
    assert len(plan.daily_plan) == 5


# =========================================================================
# 5. Primary Provider Errors (429, 503, Timeout) Trigger Fallback (Mocked)
# =========================================================================

@pytest.mark.parametrize("error_exc", [
    RuntimeError("Groq 429 RateLimit: tokens per minute exceeded"),
    RuntimeError("Groq 503 Service Unavailable"),
    TimeoutError("Groq read timeout after 30s"),
])
def test_primary_provider_errors_trigger_qwen_fallback_mocked(error_exc):
    """Network/quota/timeout failures from GPT-OSS 120B trigger Qwen fallback cleanly."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    constraints = {"departure_date": dep, "return_date": ret, "origin": "Bengaluru", "destination": "Kochi"}
    complete_qwen_plan = _make_test_plan(5, dep_date=dep, ret_date=ret)

    with patch("services.chat_planner._groq_plan", side_effect=[
        error_exc,
        complete_qwen_plan,
    ]):
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    assert provider == "groq"
    assert len(plan.daily_plan) == 5


# =========================================================================
# 6. Structured Outage When Both Models Fail (Mocked, Zero Hallucinations)
# =========================================================================

def test_both_models_fail_returns_truthful_outage_result_mocked():
    """4. When both GPT-OSS 120B and Qwen fail, a truthful unavailable plan is returned with preserved evidence and NO hallucinated days."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    constraints = {
        "departure_date": dep,
        "return_date": ret,
        "origin": "Bengaluru",
        "origin_airport": "BLR",
        "destination": "Kochi",
        "destination_airport": "COK",
        "adults": 2,
    }

    with patch("services.chat_planner._groq_plan", side_effect=[
        RuntimeError("Groq 503 Service Unavailable (GPT-OSS 120B)"),
        RuntimeError("Groq 503 Service Unavailable (Qwen 3.8 27B)"),
    ]):
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="Flight verified",
            live_flight_offers=[{"id": "fl_1", "price_inr": 5000, "is_recommended": True}],
            live_hotel_offers=[{"id": "ht_1", "price_inr": 8000, "is_recommended": True}],
            hotel_results="Hotel verified",
            weather_results="Sunny, 28C",
            budget={"flight_estimate": 5000, "hotel_estimate": 8000, "estimated_total": 13000},
        )

    assert provider == "unavailable"
    assert plan.status == "unavailable"
    assert plan.planning_unavailable is True
    assert plan.daily_plan == []  # MUST NOT fabricate days!
    assert plan.outage_status_code == 503
    assert plan.retryable is True
    # Preserved evidence intact
    assert "fl_1" in str(plan.selected_flight_id or "") or "fl_1" in plan.flight_suggestion
    assert "ht_1" in str(plan.selected_hotel_id or "") or "ht_1" in plan.accommodation_plan


def test_qwen_invalid_itinerary_rejected_mocked():
    """5. When Qwen fallback returns an invalid itinerary (e.g. duplicate days), it is rejected and returns structured outage."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    constraints = {
        "departure_date": dep,
        "return_date": ret,
        "origin": "Bengaluru",
        "destination": "Kochi",
    }
    duplicate_days = [
        DayPlan(day=1, title="D1", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=2, title="D2a", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=2, title="D2b", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=4, title="D4", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
        DayPlan(day=5, title="D5", morning="M", afternoon="A", evening="E", estimated_daily_cost_inr=1000),
    ]
    invalid_qwen_plan = _make_test_plan(5, dep_date=dep, ret_date=ret, custom_days=duplicate_days)

    with patch("services.chat_planner._groq_plan", side_effect=[
        RuntimeError("Primary GPT-OSS failure"),
        invalid_qwen_plan,
    ]):
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )

    # Must reject invalid plan and fail closed to unavailable
    assert provider == "unavailable"
    assert plan.planning_unavailable is True
    assert plan.daily_plan == []


# =========================================================================
# 7. Configurable Model Selection via GROQ_MODEL and GROQ_FALLBACK_MODEL
# =========================================================================

def test_configurable_groq_primary_and_fallback_models(monkeypatch):
    """Verify that GROQ_MODEL and GROQ_FALLBACK_MODEL configure primary and secondary models."""
    # Defaults: primary is GPT-OSS 120b, fallback is Qwen 3.8 27b
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)
    assert _groq_model() == "openai/gpt-oss-120b"
    assert _groq_fallback_model() == "qwen/qwen3.8-27b"

    # Custom override
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_FALLBACK_MODEL", "qwen/qwen3.8-27b")
    assert _groq_model() == "openai/gpt-oss-20b"
    assert _groq_fallback_model() == "qwen/qwen3.8-27b"


# =========================================================================
# 8. Gemini Model Identifier and Legacy Safeguard
# =========================================================================

def test_gemini_model_config_and_safeguard(monkeypatch):
    """Verify GEMINI_MODEL uses supported gemini-2.5-flash and maps legacy gemini-3.8-flash safely."""
    # Default is gemini-2.5-flash
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert _gemini_model() == "gemini-2.5-flash"

    # Legacy invalid identifier gemini-3.8-flash is automatically corrected
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    assert _gemini_model() == "gemini-2.5-flash"

    # Explicit alternative supported model is honored
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    assert _gemini_model() == "gemini-2.5-pro"


def test_optional_gemini_fallback_only_when_explicitly_enabled_mocked(monkeypatch):
    """Verify Gemini fallback is skipped by default, but called when ENABLE_GEMINI_FALLBACK=1."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    constraints = {"departure_date": dep, "return_date": ret, "origin": "Bengaluru", "destination": "Kochi"}
    gemini_plan = _make_test_plan(5, dep_date=dep, ret_date=ret)

    # Case A: ENABLE_GEMINI_FALLBACK is disabled (default). Gemini MUST NOT be called.
    monkeypatch.delenv("ENABLE_GEMINI_FALLBACK", raising=False)
    with patch("services.chat_planner._groq_plan", side_effect=RuntimeError("Groq down")), \
         patch("services.chat_planner._gemini_plan", return_value=gemini_plan) as mock_gemini:
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )
        assert provider == "unavailable"
        assert mock_gemini.call_count == 0

    # Case B: ENABLE_GEMINI_FALLBACK=1 is explicitly set. Gemini is called.
    monkeypatch.setenv("ENABLE_GEMINI_FALLBACK", "1")
    with patch("services.chat_planner._groq_plan", side_effect=RuntimeError("Groq down")), \
         patch("services.chat_planner._gemini_plan", return_value=gemini_plan) as mock_gemini:
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )
        assert provider == "gemini"
        assert mock_gemini.call_count == 1
        assert len(plan.daily_plan) == 5


def test_zero_fabricated_days_or_booking_confirmations_mocked():
    """7. Verify zero fabricated days, fake booking confirmations, or hallucinated ticket numbers under failure."""
    dep = "2026-11-01"
    ret = "2026-11-05"
    constraints = {
        "departure_date": dep,
        "return_date": ret,
        "origin": "Bengaluru",
        "destination": "Kochi",
    }
    with patch("services.chat_planner._groq_plan", side_effect=RuntimeError("Provider outage")):
        plan, provider = generate_travel_plan(
            user_query="Plan trip",
            constraints=constraints,
            flight_results="No flights found",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="No hotels found",
            weather_results="Sunny",
            budget={},
        )
    assert provider == "unavailable"
    assert plan.daily_plan == []
    # Verify no fake booking/ticket IDs were hallucinated
    assert plan.selected_flight_id is None
    assert plan.selected_hotel_id is None
    assert "CN-" not in str(plan.model_dump())
    assert "CONFIRMED" not in str(plan.model_dump()).upper()


# =========================================================================
# 9. Configurable Duration Limit via MAX_TRIP_DURATION_DAYS
# =========================================================================

def test_configurable_max_trip_duration_limit(monkeypatch):
    """Verify MAX_TRIP_DURATION_DAYS controls the guard limit."""
    monkeypatch.setenv("MAX_TRIP_DURATION_DAYS", "14")

    # 12-day trip is allowed under 14-day limit
    assert _resolve_duration_days({"departure_date": "2026-11-01", "return_date": "2026-11-12"}) == 12

    # 15-day trip exceeds 14-day limit
    with pytest.raises(TripDurationUnsupportedError) as exc:
        generate_travel_plan(
            user_query="15 day trip",
            constraints={"departure_date": "2026-11-01", "return_date": "2026-11-15"},
            flight_results="",
            live_flight_offers=[],
            live_hotel_offers=[],
            hotel_results="",
            weather_results="",
            budget={},
        )
    assert "14 days" in str(exc.value)
