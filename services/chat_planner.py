"""Evidence-grounded travel planner with bounded LLM context and deterministic fallback."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

logger = logging.getLogger("trusttrip.chat_planner")


class CanonicalInvariantViolation(ValueError):
    """Raised when an LLM or untrusted source attempts to alter immutable canonical invariants."""
    pass


class TripDurationUnsupportedError(ValueError):
    """Raised when requested trip duration exceeds supported planner limits."""
    pass


class DayPlan(BaseModel):
    day: int
    title: str
    morning: str
    afternoon: str
    evening: str
    estimated_daily_cost_inr: float = Field(ge=0)
    notes: list[str] = Field(default_factory=list)
    date: str | None = None


class BudgetPlan(BaseModel):
    currency: str = "INR"
    flight_estimate_inr: float = Field(ge=0)
    hotel_estimate_inr: float = Field(ge=0)
    local_transport_estimate_inr: float = Field(ge=0)
    activities_estimate_inr: float = Field(ge=0)
    contingency_inr: float = Field(ge=0)
    estimated_total_inr: float = Field(ge=0)
    target_budget_inr: float | None = None
    within_budget: bool
    pricing_note: str


class TravelPlan(BaseModel):
    trip_summary: str
    route: str
    duration_days: int = Field(ge=1)
    travel_dates: str
    travel_style: str
    daily_plan: list[DayPlan]
    budget: BudgetPlan
    flight_suggestion: str
    accommodation_plan: str
    weather_guidance: str
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    changes_from_feedback: list[str] = Field(default_factory=list)
    research_advisory: dict[str, Any] | None = None

    # Server-side typed structural invariant fields (Phase 8 Final Remediation Blocker 2)
    origin: str | None = None
    destination: str | None = None
    origin_airport: str | None = None
    destination_airport: str | None = None
    departure_date: str | None = None
    return_date: str | None = None
    adults: int | None = None
    children: int | None = None
    infants: int | None = None
    passenger_count: int | None = None
    travelers: int | None = None
    selected_flight_id: str | None = None
    selected_hotel_id: str | None = None
    revision_number: int | None = None

    # Outage policy fields (Phase 8 Invariant)
    status: str = "success"  # "success" | "unavailable"
    planning_unavailable: bool = False
    outage_status_code: int | None = None
    retryable: bool = False


SYSTEM_PROMPT_BASE = """
You are Trust Trip's production travel-planning engine.

Produce a realistic structured itinerary from the user's request and the VERIFIED evidence supplied by the application.

NON-NEGOTIABLE:
1. Never invent a flight, fare, hotel price, availability, weather value, airline, booking reference or provider result.
2. Use live flight/hotel offers only when supplied. If unavailable, say so clearly and still provide a useful itinerary using planning guidance.
3. The deterministic budget object is authoritative; do not change its numbers.
4. Respect route, duration, dates, passengers and budget. If live evidence is unavailable or the budget is exceeded, explain it in warnings instead of failing the whole plan.
5. Keep exactly the requested number of itinerary days.
6. Do not put unsupported exact prices, temperatures, ratings or availability into prose.
7. Human feedback must be reflected in the returned plan and listed in changes_from_feedback.
8. Output only data matching the requested schema.
9. For flight_suggestion, accommodation_plan and weather_guidance, use Markdown emphasis for important factual values. Keep explanatory words normal weight. Never put the whole sentence in bold.
10. If the user asks for general/global flight information without a concrete route, provide useful general guidance in the available fields and do not invent current prices, schedules or availability.
""".strip()

COMPACT_RULES = """
TOKEN EFFICIENCY & COMPACTNESS RULES (MANDATORY):
- Keep all fields short, compact, and concise. Avoid narrative exposition, filler words ("You can", "Start your day by", "Take some time to"), and repeated facts.
- In DayPlan:
  - title: 2 to 4 words (e.g., "Colaba & Heritage").
  - morning, afternoon, evening: exactly 1 concise sentence each (under 10 words, e.g., "Visit Gateway of India and walk Marine Drive.").
  - notes: empty list [] unless an essential destination alert is required (max 1 short item under 8 words).
- In trip_summary: exactly 1 concise sentence (under 15 words).
- In flight_suggestion, accommodation_plan, weather_guidance: exactly 1 concise sentence each (under 18 words).
- In assumptions: max 1 short item or empty [].
- In warnings: empty [] unless budget exceeded or live offer missing (max 1 short item).
- In changes_from_feedback: empty [] if no feedback.
""".strip()

ULTRA_COMPACT_RULES = """
TOKEN EFFICIENCY & ULTRA-COMPACT RULES (MANDATORY FOR LONG ITINERARIES):
- Complete every required TravelPlan field before adding optional descriptive detail.
- You must preserve EVERY requested itinerary day in daily_plan.
- Use short factual phrases. Avoid introductions, narratives, and conversational filler.
- In DayPlan:
  - title: maximum 3 words (e.g., "Colaba Heritage").
  - morning: maximum 5 words (e.g., "Gateway of India visit.").
  - afternoon: maximum 5 words (e.g., "Explore Colaba Causeway.").
  - evening: maximum 5 words (e.g., "Dinner in Bandra.").
  - notes: [] unless an essential alert is genuinely required.
- In trip_summary: maximum 12 words.
- In flight_suggestion: maximum 12 words.
- In accommodation_plan: maximum 12 words.
- In weather_guidance: maximum 12 words.
- In assumptions: [] unless genuinely required.
- In warnings: [] unless genuinely required.
- In changes_from_feedback: [] when no feedback exists.
""".strip()


def _get_system_prompt(duration_days: int = 5) -> str:
    rules = ULTRA_COMPACT_RULES if duration_days >= 7 else COMPACT_RULES
    day_directive = (
        f"MANDATORY ITINERARY LENGTH: You MUST generate exactly {duration_days} DayPlan items in daily_plan, "
        f"numbered sequentially from day 1 to day {duration_days}. "
        f"Do NOT omit any days. Do NOT stop before day {duration_days}."
    )
    return f"{SYSTEM_PROMPT_BASE}\n\n{rules}\n\n{day_directive}"


SYSTEM_PROMPT = _get_system_prompt(5)


def _json(value: Any, limit: int = 6000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text[:limit]


def _groq_model() -> str:
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()


def _groq_fallback_model() -> str:
    return os.getenv("GROQ_FALLBACK_MODEL", "qwen/qwen3.8-27b").strip()


def _gemini_model() -> str:
    m = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if m == "gemini-3.8-flash":
        return "gemini-2.5-flash"
    return m or "gemini-2.5-flash"


def _gemini_response_schema() -> dict[str, Any]:
    """Return the TravelPlan JSON schema without JSON Schema keywords Gemini rejects.

    Pydantic emits ``additionalProperties`` for ``dict[str, Any]`` fields.
    The Gemini ``response_schema`` API does not accept that keyword, so strip
    it recursively while preserving every supported structural constraint.
    """
    def remove_unsupported(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: remove_unsupported(item)
                for key, item in value.items()
                if key != "additionalProperties"
            }
        if isinstance(value, list):
            return [remove_unsupported(item) for item in value]
        return value

    return remove_unsupported(TravelPlan.model_json_schema())


def _compact_flight(o: dict[str, Any]) -> dict[str, Any]:
    segs = o.get("segments") or []
    first = segs[0] if segs else {}
    last = segs[-1] if segs else first
    return {
        "id": o.get("id"), "airline": o.get("owner") or first.get("marketing_carrier") or first.get("operating_carrier"),
        "currency": o.get("currency"), "price": o.get("price"), "price_inr": o.get("price_inr"),
        "travellers": o.get("passenger_count") or len(o.get("passengers") or []) or 1,
        "stops": o.get("stops", 0), "departure": first.get("departure"), "arrival": last.get("arrival"),
        "origin": first.get("origin"), "destination": last.get("destination"),
        "duration": first.get("duration"), "recommended": bool(o.get("is_recommended")),
    }


def _compact_hotel(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": o.get("id"), "provider": o.get("provider"), "hotel": o.get("hotel_name"),
        "location": o.get("address"), "rating": o.get("rating") or o.get("stars"),
        "room": o.get("room_name"), "board": o.get("board_name"),
        "currency": o.get("currency"), "price": o.get("price"), "price_inr": o.get("price_inr"),
        "refundable": o.get("refundable"), "recommended": bool(o.get("is_recommended")),
    }


def _compact_context(
    constraints,
    flight_results,
    live_flight_offers,
    hotel_results,
    live_hotel_offers,
    weather_results,
    budget,
    feedback,
    revision,
    research_advisory=None,
    duration_days: int = 5,
):
    # Deliberately keep the entire prompt small enough that input + requested
    # output stays comfortably below the project's ~5K TPM Groq target. The UI
    # still receives the complete provider objects separately.
    flights = [_compact_flight(o) for o in live_flight_offers[:4]]
    hotels = [_compact_hotel(o) for o in live_hotel_offers[:4]]
    weather = re.sub(r"\s+", " ", str(weather_results or "")).strip()[:700]

    # Phase 8 Remediation Blocker 1:
    dep = constraints.get("departure_date") or constraints.get("start_date")
    ret = constraints.get("return_date") or constraints.get("end_date")
    dates_line = ""
    if dep:
        dates_line = f"\nAUTHORITATIVE DATES (MANDATORY): Departure {str(dep)[:10]}" + (f", Return {str(ret)[:10]}" if ret else "") + f". Day 1 MUST start on {str(dep)[:10]}."

    return f"""
USER REQUEST:
{str(constraints.get('query') or '')[:700]}

REQUIRED ITINERARY DURATION:
Exactly {duration_days} days. daily_plan MUST contain exactly {duration_days} DayPlan entries (days 1 to {duration_days}).{dates_line}

CONSTRAINTS:
{_json(constraints, 1300)}

TOP LIVE FLIGHTS:
{_json(flights, 2500)}

FLIGHT STATUS:
{str(flight_results or '')[:700]}

TOP LIVE HOTELS:
{_json(hotels, 2500)}

HOTEL STATUS:
{str(hotel_results or '')[:700]}

WEATHER EVIDENCE:
{weather}

DETERMINISTIC BUDGET:
{_json(budget, 1200)}

HUMAN FEEDBACK:
{str(feedback or 'None')[:500]}

REVISION: {revision}

Return the complete structured travel plan. Preserve provider facts exactly. Follow brevity limits strictly.
""".strip()


def _resolve_duration_days(constraints: dict[str, Any]) -> int:
    dep = constraints.get("departure_date") or constraints.get("start_date")
    ret = constraints.get("return_date") or constraints.get("end_date")
    is_one_way = bool(constraints.get("is_one_way", False)) or (dep and not ret)
    if dep and is_one_way:
        return 1
    if dep and ret:
        try:
            from datetime import date
            d1 = date.fromisoformat(str(dep)[:10])
            d2 = date.fromisoformat(str(ret)[:10])
            calc = (d2 - d1).days + 1
            if calc > 0:
                return calc
        except Exception:
            pass
    for k in ("duration_days", "duration", "days", "trip_duration_days"):
        if constraints.get(k) is not None:
            try:
                v = int(constraints[k])
                if v > 0:
                    return v
            except (ValueError, TypeError):
                pass
    return 5


def _groq_plan(prompt: str, system_prompt: str = SYSTEM_PROMPT, model_name: str | None = None, duration_days: int = 5) -> TravelPlan:
    key = os.getenv("GROQ_API_KEY", "").strip().strip('"')
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    effective_model = (model_name or _groq_model()).strip()
    configured = int(os.getenv("GROQ_MAX_TOKENS", "4096"))
    base_toks = max(configured, 4096)
    if duration_days > 5:
        max_toks = min(base_toks + (duration_days - 5) * 350, 8192)
    else:
        max_toks = base_toks
    extra_kwargs = {}
    if "r1" in effective_model.lower() or "o1" in effective_model.lower():
        extra_kwargs["reasoning_effort"] = "none"
    llm = ChatGroq(
        model=effective_model,
        api_key=key,
        temperature=0.12,
        max_tokens=max_toks,
        **extra_kwargs,
    )
    structured = llm.with_structured_output(TravelPlan, method="json_schema")
    return structured.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)])


def _gemini_plan(prompt: str, system_prompt: str = SYSTEM_PROMPT) -> TravelPlan:
    key = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    chat = client.chats.create(
        model=_gemini_model(),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.12,
            max_output_tokens=2500,
            response_mime_type="application/json",
            response_schema=_gemini_response_schema(),
        ),
    )
    response = chat.send_message(prompt)
    return TravelPlan.model_validate_json(response.text)


def _evidence_flight(offers: list[dict[str, Any]]) -> str:
    if not offers:
        return "No live flight offer was returned. Use Manual Flight Search or try another route/date."
    o = next((x for x in offers if x.get("is_recommended")), offers[0])
    segs = o.get("segments") or []
    first, last = (segs[0] if segs else {}), (segs[-1] if segs else {})
    airline = o.get("owner") or first.get("marketing_carrier") or "Airline"
    cur, price = o.get("currency"), o.get("price")
    inr = o.get("price_inr")
    travellers = o.get("passenger_count") or len(o.get("passengers") or []) or 1
    stops = o.get("stops", 0)
    inr_text = f"₹{float(inr):,.2f} INR" if inr is not None else "INR conversion unavailable"
    travel_date = o.get("planning_departure") or (o.get("search_context") or {}).get("departure_date")
    date_text = f" on **{travel_date}**" if travel_date else ""
    return f"**Recommended flight:** **{airline}** for **{cur} {price}** ({inr_text}) for **{travellers} traveller(s)**{date_text}, with **{stops} stop(s)**, from **{first.get('origin','—')}** to **{last.get('destination','—')}**."


def _evidence_hotel(offers: list[dict[str, Any]]) -> str:
    if not offers:
        return "No live hotel offer was returned. Try another date/provider or use Manual Hotel Search."
    o = next((x for x in offers if x.get("is_recommended")), offers[0])
    price = o.get("price_inr")
    price_text = f"₹{float(price):,.2f}" if price is not None else f"{o.get('currency','')} {o.get('price','—')}"
    provider = o.get("provider", "provider")
    return f"**Recommended hotel:** **{o.get('hotel_name','Hotel')}** for **{price_text} total**, offering **{o.get('room_name','Room')}** with **{o.get('board_name','Room Only')}**. This is a live provider offer from **{provider}**."


def _evidence_weather(weather_results: str, destination: str) -> str:
    text = re.sub(r"\s+", " ", str(weather_results or "")).strip()
    if not text:
        return f"Live weather data for **{destination}** is unavailable. Verify the forecast before departure."
    # Do not invent weather numbers in fallback mode; preserve provider text.
    return text[:1800]


PROD_ENV_NAMES = frozenset({"production", "prod", "staging", "stage", "live", "deployment"})
TEST_ENV_NAMES = frozenset({"test", "testing"})


def is_production_environment() -> bool:
    """Return True if running in any recognized production-like or staging environment."""
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    app_env = os.getenv("APP_ENV", "").strip().lower()
    return env in PROD_ENV_NAMES or app_env in PROD_ENV_NAMES


def is_explicit_test_environment() -> bool:
    """Return True strictly if running in an explicit, positively-verified test environment.

    Production environments ALWAYS fail closed (return False), even if test flags are set.
    """
    if is_production_environment():
        return False
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    app_env = os.getenv("APP_ENV", "").strip().lower()
    testing_flag = os.getenv("TESTING", "").strip().lower() in ("1", "true")
    return env in TEST_ENV_NAMES or app_env in TEST_ENV_NAMES or testing_flag


def is_test_fallback_authorized(*, allow_deterministic_test_fallback: bool = False) -> bool:
    """Verify authorization for deterministic fallback.

    Phase 8 Non-Negotiable Invariant:
    Deterministic fallback MUST NEVER execute in production.

    Allowed ONLY IF:
    1. Execution environment is NOT production-like (ENVIRONMENT / APP_ENV not in ('prod', 'production', 'staging', ...)).
    2. Execution environment is positively verified as an explicit TEST environment (e.g. ENVIRONMENT='test' or TESTING='1').
    3. Caller explicitly passes allow_deterministic_test_fallback=True.

    ALLOW_DETERMINISTIC_TEST_FALLBACK is NOT a standalone authorization mechanism
    and cannot authorize fallback in production or outside a verified test environment.
    """
    # 1. Reject any production environment unconditionally
    if is_production_environment():
        return False

    # 2. Reject if not positively verified as an explicit test environment
    if not is_explicit_test_environment():
        return False

    # 3. Require explicit parameter authorization
    return bool(allow_deterministic_test_fallback)


class PlannerOutageError(RuntimeError):
    """Raised when all configured LLM providers fail or have outages and fallback is not enabled."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[str] | None = None,
        plan: TravelPlan | None = None,
        status_code: int = 503,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.errors = errors or []
        self.plan = plan
        self.status_code = status_code
        self.retryable = retryable


def _build_unavailable_plan(
    *,
    constraints: dict[str, Any],
    flight_offers: list[dict[str, Any]],
    hotel_offers: list[dict[str, Any]],
    budget: dict[str, Any],
    weather_results: str,
    feedback: str,
    reason: str,
) -> TravelPlan:
    duration = max(1, int(constraints.get("duration_days") or 5))
    origin = constraints.get("origin_city") or constraints.get("origin") or "Origin"
    destination = constraints.get("destination_city") or constraints.get("destination") or "Destination"
    travel_dates = constraints.get("travel_dates") or constraints.get("departure_date") or "Flexible dates"

    b = BudgetPlan(
        currency="INR",
        flight_estimate_inr=float(budget.get("flight_estimate", 0)),
        hotel_estimate_inr=float(budget.get("hotel_estimate", 0)),
        local_transport_estimate_inr=float(budget.get("local_transport_estimate", 0)),
        activities_estimate_inr=float(budget.get("activities_estimate", 0)),
        contingency_inr=float(budget.get("contingency", 0)),
        estimated_total_inr=float(budget.get("estimated_total", 0)),
        target_budget_inr=budget.get("target_budget"),
        within_budget=bool(budget.get("within_budget", True)),
        pricing_note=str(budget.get("hotel_price_source", "Provider-backed where available; other categories are planning estimates.")),
    )
    warnings = [
        "Automated itinerary planning synthesis is temporarily unavailable due to upstream LLM provider outage."
    ]
    if reason:
        warnings.append(reason[:700])
    if feedback:
        warnings.append(f"Feedback preserved: {feedback[:400]}")

    rec_fl = next((x for x in flight_offers if x.get("is_recommended")), flight_offers[0]) if flight_offers else {}
    rec_ht = next((x for x in hotel_offers if x.get("is_recommended")), hotel_offers[0]) if hotel_offers else {}
    sel_flight = constraints.get("selected_flight_id") or rec_fl.get("id") or rec_fl.get("offer_id")
    sel_hotel = constraints.get("selected_hotel_id") or rec_ht.get("id") or rec_ht.get("offer_id")
    rev = constraints.get("current_revision_number") if constraints.get("current_revision_number") is not None else constraints.get("revision_number")

    dep = constraints.get("departure_date") or constraints.get("start_date")
    ret = constraints.get("return_date") or constraints.get("end_date")
    pax = (
        constraints.get("passenger_count")
        or constraints.get("passengers")
        or constraints.get("travelers")
        or constraints.get("travelers_count")
        or constraints.get("adults")
    )
    if isinstance(pax, list):
        pax = len(pax)

    return TravelPlan(
        status="unavailable",
        planning_unavailable=True,
        trip_summary=f"Travel plan synthesis unavailable for {origin} to {destination}. Verified evidence preserved.",
        route=f"{origin} → {destination}",
        duration_days=duration,
        travel_dates=travel_dates,
        travel_style=constraints.get("travel_style") or "Balanced",
        daily_plan=[],  # Invariant: NO synthetic/hallucinated day activities when synthesis is unavailable!
        budget=b,
        flight_suggestion=_evidence_flight(flight_offers),
        accommodation_plan=_evidence_hotel(hotel_offers),
        weather_guidance=_evidence_weather(weather_results, destination),
        assumptions=["Synthesis unavailable due to upstream LLM outage; verified provider evidence preserved."],
        warnings=warnings,
        changes_from_feedback=[feedback] if feedback else [],
        origin=origin,
        destination=destination,
        origin_airport=constraints.get("origin_airport") or constraints.get("origin_code"),
        destination_airport=constraints.get("destination_airport") or constraints.get("destination_code"),
        departure_date=str(dep)[:10] if dep else None,
        return_date=str(ret)[:10] if ret else None,
        adults=constraints.get("adults"),
        children=constraints.get("children"),
        infants=constraints.get("infants"),
        passenger_count=constraints.get("passenger_count"),
        travelers=int(pax) if pax is not None else None,
        selected_flight_id=str(sel_flight) if sel_flight else None,
        selected_hotel_id=str(sel_hotel) if sel_hotel else None,
        revision_number=int(rev) if rev is not None else None,
    )


def _deterministic_fallback(*, constraints, flight_offers, hotel_offers, budget, weather_results, feedback, reason) -> TravelPlan:
    duration = max(1, int(constraints.get("duration_days") or 5))
    origin = constraints.get("origin_city") or constraints.get("origin") or "Origin"
    destination = constraints.get("destination_city") or constraints.get("destination") or "Destination"
    travel_dates = constraints.get("travel_dates") or constraints.get("departure_date") or "Flexible dates"
    daily_cost = float(budget.get("activities_estimate", 0) + budget.get("local_transport_estimate", 0)) / duration if duration else 0
    days = []
    for i in range(1, duration + 1):
        days.append(DayPlan(
            day=i,
            title=f"Explore {destination} — Day {i}",
            morning="Start with a relaxed breakfast and a major attraction or local area appropriate for the destination.",
            afternoon="Continue with a nearby attraction, local food experience or cultural activity; keep travel time practical.",
            evening="Keep the evening flexible for a nearby attraction, shopping or local dining and return to accommodation at a reasonable time.",
            estimated_daily_cost_inr=round(max(0, daily_cost), 2),
            notes=["Exact attraction opening times and local conditions should be verified before departure."],
        ))
    b = BudgetPlan(
        currency="INR", flight_estimate_inr=float(budget.get("flight_estimate", 0)),
        hotel_estimate_inr=float(budget.get("hotel_estimate", 0)),
        local_transport_estimate_inr=float(budget.get("local_transport_estimate", 0)),
        activities_estimate_inr=float(budget.get("activities_estimate", 0)),
        contingency_inr=float(budget.get("contingency", 0)),
        estimated_total_inr=float(budget.get("estimated_total", 0)),
        target_budget_inr=budget.get("target_budget"), within_budget=bool(budget.get("within_budget", True)),
        pricing_note=str(budget.get("hotel_price_source", "Provider-backed where available; other categories are planning estimates.")),
    )
    warnings = ["LLM generation was unavailable; this plan was generated deterministically from verified provider data and planning constraints."]
    if reason: warnings.append(reason[:700])
    if feedback: warnings.append(f"Feedback requested: {feedback[:400]}")
    rec_fl = next((x for x in flight_offers if x.get("is_recommended")), flight_offers[0]) if flight_offers else {}
    rec_ht = next((x for x in hotel_offers if x.get("is_recommended")), hotel_offers[0]) if hotel_offers else {}
    sel_flight = constraints.get("selected_flight_id") or rec_fl.get("id") or rec_fl.get("offer_id")
    sel_hotel = constraints.get("selected_hotel_id") or rec_ht.get("id") or rec_ht.get("offer_id")
    rev = constraints.get("current_revision_number") if constraints.get("current_revision_number") is not None else constraints.get("revision_number")

    dep = constraints.get("departure_date") or constraints.get("start_date")
    ret = constraints.get("return_date") or constraints.get("end_date")
    pax = (
        constraints.get("passenger_count")
        or constraints.get("passengers")
        or constraints.get("travelers")
        or constraints.get("travelers_count")
        or constraints.get("adults")
    )
    if isinstance(pax, list):
        pax = len(pax)

    return TravelPlan(
        trip_summary=f"A {duration}-day trip from {origin} to {destination} for {constraints.get('passenger_count',1)} traveller(s).",
        route=f"{origin} → {destination}", duration_days=duration, travel_dates=travel_dates,
        travel_style=constraints.get("travel_style") or "Balanced",
        daily_plan=days, budget=b,
        flight_suggestion=_evidence_flight(flight_offers),
        accommodation_plan=_evidence_hotel(hotel_offers),
        weather_guidance=_evidence_weather(weather_results, destination),
        assumptions=["Daily activities are planning guidance, not live attraction reservations."], warnings=warnings,
        changes_from_feedback=[feedback] if feedback else [],
        origin=origin,
        destination=destination,
        origin_airport=constraints.get("origin_airport") or constraints.get("origin_code"),
        destination_airport=constraints.get("destination_airport") or constraints.get("destination_code"),
        departure_date=str(dep)[:10] if dep else None,
        return_date=str(ret)[:10] if ret else None,
        adults=constraints.get("adults"),
        children=constraints.get("children"),
        infants=constraints.get("infants"),
        passenger_count=constraints.get("passenger_count"),
        travelers=int(pax) if pax is not None else None,
        selected_flight_id=str(sel_flight) if sel_flight else None,
        selected_hotel_id=str(sel_hotel) if sel_hotel else None,
        revision_number=int(rev) if rev is not None else None,
    )


def enforce_complete_canonical_invariants(
    plan: TravelPlan,
    canonical_context: dict[str, Any] | Any,
    *,
    selected_flight: dict[str, Any] | None = None,
    selected_hotel: dict[str, Any] | None = None,
    revision: int | None = None,
) -> TravelPlan:
    """Enforce strict server-side structural validation against canonical request and provider objects.

    HARD REJECT (Raises CanonicalInvariantViolation):
    - Origin: plan.origin does not match canonical origin
    - Destination: plan.destination does not match canonical destination
    - Origin airport code: plan.origin_airport does not match canonical origin airport
    - Destination airport code: plan.destination_airport does not match canonical destination airport
    - Adults: plan.adults does not match canonical adults
    - Children: plan.children does not match canonical children
    - Infants: plan.infants does not match canonical infants
    - Passenger count: plan.passenger_count does not match canonical passenger count
    - Selected flight offer ID: plan.selected_flight_id does not match canonical flight offer ID
    - Selected hotel offer ID: plan.selected_hotel_id does not match canonical hotel offer ID
    - Budget currency: plan.budget.currency does not match canonical budget currency
    - Revision identity: plan.revision_number does not match canonical revision

    CANONICAL NORMALIZATION (Deterministic alignment of approved derived/display fields):
    - Departure date and return date alignment
    - Duration synchronization: (return_date - departure_date).days + 1 (or 1 if one-way)
    - One-way semantics label formatting: "{departure_date} (one-way)"
    - Budget target amount pinning to canonical budget_amount
    """
    from datetime import date

    if hasattr(canonical_context, "model_dump"):
        ctx = canonical_context.model_dump()
    elif hasattr(canonical_context, "dict"):
        ctx = canonical_context.dict()
    elif isinstance(canonical_context, dict):
        ctx = canonical_context
    else:
        ctx = dict(canonical_context)

    # 1. HARD REJECT: Origin, Destination & Airport Identifiers
    from services.travel_planner import _resolve_place

    origin_code = ctx.get("origin_airport") or ctx.get("origin_code")
    origin_city = ctx.get("origin_city")
    if not origin_code:
        raw_o = ctx.get("origin")
        if raw_o:
            s_o = str(raw_o).strip()
            c_o, city_o, _ = _resolve_place(s_o)
            if c_o:
                origin_code = c_o
            if not origin_city and city_o:
                origin_city = city_o

    dest_code = ctx.get("destination_airport") or ctx.get("destination_code")
    dest_city = ctx.get("destination_city")
    if not dest_code:
        raw_d = ctx.get("destination")
        if raw_d:
            s_d = str(raw_d).strip()
            c_d, city_d, _ = _resolve_place(s_d)
            if c_d:
                dest_code = c_d
            if not dest_city and city_d:
                dest_city = city_d

    valid_origins = {
        str(ctx.get("origin") or "").strip().lower(),
        str(ctx.get("origin_city") or "").strip().lower(),
        str(ctx.get("origin_query") or "").strip().lower(),
        str(ctx.get("origin_airport") or "").strip().lower(),
    } - {""}
    if origin_code:
        valid_origins.add(str(origin_code).strip().lower())
    if origin_city:
        valid_origins.add(str(origin_city).strip().lower())

    if valid_origins:
        if plan.origin is not None:
            p_orig = plan.origin.strip().lower()
            if not any(v in p_orig or p_orig in v for v in valid_origins):
                raise CanonicalInvariantViolation(
                    f"Canonical origin tampering detected: expected one of {valid_origins}, got '{plan.origin}'"
                )
            if origin_code and p_orig == str(origin_code).strip().lower() and origin_city:
                plan.origin = origin_city
        else:
            plan.origin = str(origin_city or ctx.get("origin_city") or ctx.get("origin"))

    valid_dests = {
        str(ctx.get("destination") or "").strip().lower(),
        str(ctx.get("destination_city") or "").strip().lower(),
        str(ctx.get("destination_query") or "").strip().lower(),
        str(ctx.get("destination_airport") or "").strip().lower(),
    } - {""}
    if dest_code:
        valid_dests.add(str(dest_code).strip().lower())
    if dest_city:
        valid_dests.add(str(dest_city).strip().lower())

    if valid_dests:
        if plan.destination is not None:
            p_dest = plan.destination.strip().lower()
            if not any(v in p_dest or p_dest in v for v in valid_dests):
                raise CanonicalInvariantViolation(
                    f"Canonical destination tampering detected: expected one of {valid_dests}, got '{plan.destination}'"
                )
            if dest_code and p_dest == str(dest_code).strip().lower() and dest_city:
                plan.destination = dest_city
        else:
            plan.destination = str(dest_city or ctx.get("destination_city") or ctx.get("destination"))

    if origin_code:
        if plan.origin_airport is not None and plan.origin_airport.strip().upper() != str(origin_code).strip().upper():
            raise CanonicalInvariantViolation(
                f"Canonical origin airport code tampering detected: expected '{origin_code}', got '{plan.origin_airport}'"
            )
        elif plan.origin_airport is None:
            plan.origin_airport = str(origin_code).strip().upper()

    if dest_code:
        if plan.destination_airport is not None and plan.destination_airport.strip().upper() != str(dest_code).strip().upper():
            raise CanonicalInvariantViolation(
                f"Canonical destination airport code tampering detected: expected '{dest_code}', got '{plan.destination_airport}'"
            )
        elif plan.destination_airport is None:
            plan.destination_airport = str(dest_code).strip().upper()

    # 2. HARD REJECT: Passenger Composition (Adults, Children, Infants, Passenger Count)
    adults = ctx.get("adults")
    if adults is not None:
        if plan.adults is not None and plan.adults != int(adults):
            raise CanonicalInvariantViolation(
                f"Adult passenger composition tampering detected: expected {adults}, got {plan.adults}"
            )
        elif plan.adults is None:
            plan.adults = int(adults)

    children = ctx.get("children")
    if children is not None:
        if plan.children is not None and plan.children != int(children):
            raise CanonicalInvariantViolation(
                f"Child passenger composition tampering detected: expected {children}, got {plan.children}"
            )
        elif plan.children is None:
            plan.children = int(children)

    infants = ctx.get("infants")
    if infants is not None:
        if plan.infants is not None and plan.infants != int(infants):
            raise CanonicalInvariantViolation(
                f"Infant passenger composition tampering detected: expected {infants}, got {plan.infants}"
            )
        elif plan.infants is None:
            plan.infants = int(infants)

    canonical_passengers = ctx.get("passenger_count")
    if canonical_passengers is None:
        p_val = ctx.get("passengers")
        if isinstance(p_val, list):
            canonical_passengers = len(p_val)
        elif p_val is not None:
            try:
                canonical_passengers = int(p_val)
            except (ValueError, TypeError):
                pass
    if canonical_passengers is None:
        canonical_passengers = ctx.get("travelers") or ctx.get("travelers_count")
    if canonical_passengers is None and adults is not None:
        canonical_passengers = (adults or 0) + (children or 0) + (infants or 0)

    if canonical_passengers is not None:
        canonical_p_int = int(canonical_passengers)
        if plan.passenger_count is not None and plan.passenger_count != canonical_p_int:
            raise CanonicalInvariantViolation(
                f"Passenger composition tampering detected: expected {canonical_p_int} travellers, got {plan.passenger_count}"
            )
        elif plan.passenger_count is None:
            plan.passenger_count = canonical_p_int

    # 3. HARD REJECT: Selected Flight & Hotel Offer Identity
    canonical_flight = None
    if selected_flight:
        canonical_flight = selected_flight.get("id") or selected_flight.get("offer_id")
    if not canonical_flight:
        canonical_flight = ctx.get("selected_flight_id") or ctx.get("selected_flight_offer_id")
    if canonical_flight:
        canon_flt_str = str(canonical_flight)
        plan_flt_str = str(plan.selected_flight_id) if plan.selected_flight_id is not None else None
        if plan_flt_str is not None:
            if plan_flt_str == canon_flt_str:
                pass
            elif len(plan_flt_str) >= 20 and (canon_flt_str.startswith(plan_flt_str) or plan_flt_str.startswith(canon_flt_str)):
                plan.selected_flight_id = canon_flt_str
            else:
                raise CanonicalInvariantViolation(
                    f"Selected flight offer tampering detected: expected '{canonical_flight}', got '{plan.selected_flight_id}'"
                )
        else:
            plan.selected_flight_id = canon_flt_str

    canonical_hotel = None
    if selected_hotel:
        canonical_hotel = selected_hotel.get("id") or selected_hotel.get("offer_id")
    if not canonical_hotel:
        canonical_hotel = ctx.get("selected_hotel_id") or ctx.get("selected_hotel_offer_id")
    if canonical_hotel:
        canon_htl_str = str(canonical_hotel)
        plan_htl_str = str(plan.selected_hotel_id) if plan.selected_hotel_id is not None else None
        if plan_htl_str is not None:
            if plan_htl_str == canon_htl_str:
                pass
            elif len(plan_htl_str) >= 30 and (canon_htl_str.startswith(plan_htl_str) or plan_htl_str.startswith(canon_htl_str)):
                # Restore full canonical provider token when LLM output was truncated by token budget
                plan.selected_hotel_id = canon_htl_str
            else:
                raise CanonicalInvariantViolation(
                    f"Selected hotel offer tampering detected: expected '{canonical_hotel}', got '{plan.selected_hotel_id}'"
                )
        else:
            plan.selected_hotel_id = canon_htl_str

    # 4. HARD REJECT: Budget Currency
    canonical_currency = ctx.get("currency") or "INR"
    if plan.budget.currency != canonical_currency:
        raise CanonicalInvariantViolation(
            f"Budget currency tampering detected: expected '{canonical_currency}', got '{plan.budget.currency}'"
        )

    # 5. HARD REJECT: Revision Identity
    canonical_rev = revision if revision is not None else (
        ctx.get("current_revision_number") if ctx.get("current_revision_number") is not None else ctx.get("revision_number")
    )
    if canonical_rev is not None:
        if plan.revision_number is not None and plan.revision_number != int(canonical_rev):
            raise CanonicalInvariantViolation(
                f"Revision identity tampering detected: expected revision {canonical_rev}, got {plan.revision_number}"
            )
        elif plan.revision_number is None:
            plan.revision_number = int(canonical_rev)

    # 6. CANONICAL NORMALIZATION: Dates, Duration, One-way & Budget Target
    dep = ctx.get("departure_date") or ctx.get("start_date")
    ret = ctx.get("return_date") or ctx.get("end_date")
    is_one_way = bool(ctx.get("is_one_way", False)) or (dep and not ret)

    if dep:
        plan.departure_date = str(dep)[:10]
    else:
        plan.departure_date = None

    if ret:
        plan.return_date = str(ret)[:10]
    else:
        plan.return_date = None

    canonical_travelers = (
        canonical_passengers
        if canonical_passengers is not None
        else (ctx.get("travelers") or ctx.get("travelers_count") or ctx.get("adults"))
    )
    if canonical_travelers is not None:
        try:
            plan.travelers = int(canonical_travelers)
        except (ValueError, TypeError):
            plan.travelers = None
    else:
        plan.travelers = None

    if dep and is_one_way:
        plan.travel_dates = f"{str(dep)[:10]} (one-way)"
        plan.duration_days = 1
    elif dep and ret:
        plan.travel_dates = f"{str(dep)[:10]} to {str(ret)[:10]}"
        try:
            d1 = date.fromisoformat(str(dep)[:10])
            d2 = date.fromisoformat(str(ret)[:10])
            if d2 < d1:
                raise CanonicalInvariantViolation(
                    f"Canonical return date ({ret}) cannot precede departure date ({dep})."
                )
            calc_duration = (d2 - d1).days + 1
            if calc_duration > 0:
                plan.duration_days = calc_duration
        except CanonicalInvariantViolation:
            raise
        except Exception:
            if ctx.get("duration_days"):
                plan.duration_days = int(ctx["duration_days"])
    elif ctx.get("duration_days"):
        plan.duration_days = int(ctx["duration_days"])

    target_b = ctx.get("budget_amount") if "budget_amount" in ctx else ctx.get("target_budget")
    if target_b is not None:
        plan.budget.target_budget_inr = float(target_b)

    is_unavailable = getattr(plan, "planning_unavailable", False) or plan.status == "unavailable"

    # Validate mandatory structured plan content when synthesis succeeded
    if not is_unavailable:
        if not plan.flight_suggestion or not str(plan.flight_suggestion).strip():
            raise CanonicalInvariantViolation("Required field missing: flight_suggestion must not be empty.")
        if not plan.accommodation_plan or not str(plan.accommodation_plan).strip():
            raise CanonicalInvariantViolation("Required field missing: accommodation_plan must not be empty.")
        if not plan.weather_guidance or not str(plan.weather_guidance).strip():
            raise CanonicalInvariantViolation("Required field missing: weather_guidance must not be empty.")
        if not plan.budget:
            raise CanonicalInvariantViolation("Required field missing: budget must be present.")

    # 7. HARD REJECT / VALIDATION: Complete Daily Plan Invariants
    expected_duration = plan.duration_days
    if not expected_duration or expected_duration <= 0:
        expected_duration = _resolve_duration_days(ctx)
        plan.duration_days = expected_duration

    if not is_unavailable and plan.daily_plan:
        # Validate day count matches expected duration
        actual_count = len(plan.daily_plan)
        if actual_count != expected_duration:
            raise CanonicalInvariantViolation(
                f"Incomplete daily plan: expected {expected_duration} days, got {actual_count}"
            )

        # Validate sequential day numbers (1 to expected_duration)
        actual_days = [dp.day for dp in plan.daily_plan]
        expected_days = list(range(1, expected_duration + 1))
        if actual_days != expected_days:
            seen = set()
            duplicates = [d for d in actual_days if d in seen or seen.add(d)]
            missing = [d for d in expected_days if d not in set(actual_days)]
            out_of_range = [d for d in actual_days if d < 1 or d > expected_duration]
            details = []
            if duplicates:
                details.append(f"duplicate days: {duplicates}")
            if missing:
                details.append(f"missing days: {missing}")
            if out_of_range:
                details.append(f"out of range days: {out_of_range}")
            raise CanonicalInvariantViolation(
                f"Daily plan has invalid day sequence ({', '.join(details) or 'non-sequential'}): "
                f"expected {expected_days}, got {actual_days}"
            )

        # Validate sequential dates if departure_date is present
        if dep:
            base_d = None
            try:
                base_d = date.fromisoformat(str(dep)[:10])
            except (ValueError, TypeError):
                base_d = None

            if base_d:
                from datetime import timedelta
                for idx, dp in enumerate(plan.daily_plan):
                    expected_d_str = str(base_d + timedelta(days=idx))
                    if dp.date:
                        if str(dp.date)[:10] != expected_d_str:
                            raise CanonicalInvariantViolation(
                                f"Daily plan Day {dp.day} date mismatch: expected '{expected_d_str}', got '{dp.date}'"
                            )
                    else:
                        dp.date = expected_d_str



    return plan


_enforce_canonical_invariants = enforce_complete_canonical_invariants


def generate_travel_plan(
    *,
    user_query: str,
    constraints: dict[str, Any],
    flight_results: str,
    live_flight_offers: list[dict[str, Any]],
    live_hotel_offers: list[dict[str, Any]],
    hotel_results: str,
    weather_results: str,
    budget: dict[str, Any],
    feedback: str = "",
    revision_number: int = 0,
    research_advisory: Any = None,
    allow_deterministic_test_fallback: bool = False,
    raise_on_outage: bool = False,
) -> tuple[TravelPlan, str]:
    duration = _resolve_duration_days(constraints)
    max_supported_days = int(os.getenv("MAX_TRIP_DURATION_DAYS", "30"))
    if duration > max_supported_days:
        raise TripDurationUnsupportedError(
            f"Trips longer than {max_supported_days} days are not currently supported by the planner. "
            f"Please choose a trip duration of {max_supported_days} days or fewer."
        )

    prompt = _compact_context(
        constraints,
        flight_results,
        live_flight_offers,
        hotel_results,
        live_hotel_offers,
        weather_results,
        budget,
        feedback,
        revision_number,
        research_advisory=research_advisory,
        duration_days=duration,
    )

    def _attach_advisory(p: TravelPlan) -> TravelPlan:
        if research_advisory:
            if hasattr(research_advisory, "model_dump"):
                p.research_advisory = research_advisory.model_dump()
            elif isinstance(research_advisory, dict):
                p.research_advisory = dict(research_advisory)
            else:
                p.research_advisory = {"summary": str(research_advisory)}
        return p

    sel_flight = live_flight_offers[0] if live_flight_offers else None
    sel_hotel = live_hotel_offers[0] if live_hotel_offers else None

    sys_prompt = _get_system_prompt(duration)

    errors: list[str] = []

    # 1. Primary Model (Groq: default openai/gpt-oss-120b or user-configured GROQ_MODEL)
    groq_primary = _groq_model()
    try:
        logger.info("itinerary_planner.dispatch provider=groq model=%s duration=%d", groq_primary, duration)
        raw_plan = _groq_plan(prompt, system_prompt=sys_prompt, model_name=groq_primary, duration_days=duration)
        plan = enforce_complete_canonical_invariants(
            raw_plan, constraints,
            selected_flight=sel_flight, selected_hotel=sel_hotel, revision=revision_number,
        )
        if not getattr(plan, "planning_unavailable", False) and len(plan.daily_plan) == 0:
            raise CanonicalInvariantViolation(f"Empty daily plan: expected {duration} days, got 0")
        return _attach_advisory(plan), "groq"
    except Exception as exc:
        err_str = f"groq-primary ({groq_primary}): {type(exc).__name__}: {exc}"
        logger.warning("itinerary_planner.primary_failure %s", err_str)
        errors.append(err_str)

    # 2. Secondary Fallback Model (Groq: default qwen/qwen3.8-27b or user-configured GROQ_FALLBACK_MODEL)
    groq_fallback = _groq_fallback_model()
    if groq_fallback and groq_fallback != groq_primary:
        try:
            logger.info("itinerary_planner.dispatch provider=groq-fallback model=%s duration=%d", groq_fallback, duration)
            raw_plan = _groq_plan(prompt, system_prompt=sys_prompt, model_name=groq_fallback, duration_days=duration)
            plan = enforce_complete_canonical_invariants(
                raw_plan, constraints,
                selected_flight=sel_flight, selected_hotel=sel_hotel, revision=revision_number,
            )
            if not getattr(plan, "planning_unavailable", False) and len(plan.daily_plan) == 0:
                raise CanonicalInvariantViolation(f"Empty daily plan: expected {duration} days, got 0")
            return _attach_advisory(plan), "groq"
        except Exception as exc:
            err_str = f"groq-fallback ({groq_fallback}): {type(exc).__name__}: {exc}"
            logger.warning("itinerary_planner.fallback_failure %s", err_str)
            errors.append(err_str)

    # 3. Optional Tertiary Model (Gemini: only if explicitly enabled via ENABLE_GEMINI_FALLBACK)
    if os.getenv("ENABLE_GEMINI_FALLBACK", "0").lower() in ("1", "true"):
        gemini_model_name = _gemini_model()
        try:
            logger.info("itinerary_planner.dispatch provider=gemini model=%s duration=%d", gemini_model_name, duration)
            raw_plan = _gemini_plan(prompt, system_prompt=sys_prompt)
            plan = enforce_complete_canonical_invariants(
                raw_plan, constraints,
                selected_flight=sel_flight, selected_hotel=sel_hotel, revision=revision_number,
            )
            return _attach_advisory(plan), "gemini"
        except Exception as exc:
            err_str = f"gemini ({gemini_model_name}): {type(exc).__name__}: {exc}"
            logger.warning("itinerary_planner.gemini_failure %s", err_str)
            errors.append(err_str)

    # Production LLM/provider failure MUST NOT silently invoke a deterministic test/fallback planner.
    # Deterministic fallback is strictly restricted to explicit hermetic testing under verified test authorization.
    if is_test_fallback_authorized(allow_deterministic_test_fallback=allow_deterministic_test_fallback):
        raw_plan = _deterministic_fallback(
            constraints=constraints, flight_offers=live_flight_offers, hotel_offers=live_hotel_offers,
            budget=budget, weather_results=weather_results, feedback=feedback,
            reason="Explicit test fallback authorized in verified test environment; LLM providers unavailable: " + " | ".join(errors)[:1200],
        )
        plan = enforce_complete_canonical_invariants(
            raw_plan, constraints,
            selected_flight=sel_flight, selected_hotel=sel_hotel, revision=revision_number,
        )
        return _attach_advisory(plan), "deterministic"

    # Production Outage Invariant:
    # Evidence preserved -> planning/synthesis unavailable -> structured error/status -> NO silent test substitution
    outage_reason = "LLM providers were unavailable: " + " | ".join(errors)[:1200]
    raw_plan = _build_unavailable_plan(
        constraints=constraints,
        flight_offers=live_flight_offers,
        hotel_offers=live_hotel_offers,
        budget=budget,
        weather_results=weather_results,
        feedback=feedback,
        reason=outage_reason,
    )
    plan = enforce_complete_canonical_invariants(
        raw_plan, constraints,
        selected_flight=sel_flight, selected_hotel=sel_hotel, revision=revision_number,
    )
    plan = _attach_advisory(plan)

    # Classify upstream outage: 503 (temporary unavailability, retryable) vs 429 (rate/quota limit)
    has_503 = False
    has_429 = False
    for err_msg in errors:
        msg_lower = err_msg.lower()
        if "503" in err_msg or "service unavailable" in msg_lower or "overloaded" in msg_lower:
            has_503 = True
        elif "429" in err_msg or "resourceexhausted" in msg_lower or "rate limit" in msg_lower or "quota" in msg_lower:
            has_429 = True

    if has_503:
        outage_status_code = 503
        is_retryable = True
    elif has_429:
        outage_status_code = 429
        is_retryable = False
    else:
        outage_status_code = 500
        is_retryable = False

    plan.outage_status_code = outage_status_code
    plan.retryable = is_retryable

    if raise_on_outage:
        raise PlannerOutageError(
            outage_reason,
            errors=errors,
            plan=plan,
            status_code=outage_status_code,
            retryable=is_retryable,
        )
    return plan, "unavailable"


def plan_to_markdown(plan: TravelPlan) -> str:
    p = plan.model_dump()
    if getattr(plan, "planning_unavailable", False) or p.get("status") == "unavailable":
        lines = [
            f"# {p['route']} — Planning Synthesis Unavailable",
            f"\n**Notice:** Travel planning synthesis is temporarily unavailable due to upstream LLM provider outage. Verified evidence (flights, accommodation, weather, research) has been preserved.",
            f"**Dates:** {p['travel_dates']}  \n**Style:** {p['travel_style']}",
            "\n## Preserved Evidence & Status",
            f"### **Flight Status & Offers**\n{p['flight_suggestion']}",
            f"### **Accommodation**\n{p['accommodation_plan']}",
            f"### **Weather Guidance**\n{p['weather_guidance']}",
        ]
        b = p.get("budget") or {}
        if b:
            lines += [
                "\n## Deterministic Budget Estimate",
                f"- Flights: ₹{b.get('flight_estimate_inr', 0):,.0f}",
                f"- Hotels: ₹{b.get('hotel_estimate_inr', 0):,.0f}",
                f"- Local transport: ₹{b.get('local_transport_estimate_inr', 0):,.0f}",
                f"- Activities: ₹{b.get('activities_estimate_inr', 0):,.0f}",
                f"- Contingency: ₹{b.get('contingency_inr', 0):,.0f}",
                f"- **Estimated total: ₹{b.get('estimated_total_inr', 0):,.0f}**",
            ]
        adv = p.get("research_advisory")
        if adv and isinstance(adv, dict):
            lines.append("\n## Preserved Research Advisory")
            if adv.get("summary"):
                lines.append(f"**Research Summary:** {adv['summary']}")
            if adv.get("allowed_facts"):
                lines.append("**Verified Facts:**")
                lines.extend(f"- {fact}" for fact in adv["allowed_facts"][:5])
            if adv.get("citation_ids"):
                lines.append(f"**Citations:** {', '.join(adv['citation_ids'][:5])}")
        if p.get("warnings"):
            lines.append("\n## Provider Outage Diagnostics")
            lines.extend(f"- {x}" for x in p["warnings"])
        return "\n".join(lines)

    lines = [f"# {p['route']} — {p['duration_days']} Days", f"\n**Summary:** {p['trip_summary']}", f"**Dates:** {p['travel_dates']}  \n**Style:** {p['travel_style']}", "\n## Day-by-Day Plan"]
    for day in p["daily_plan"]:
        lines += [f"### Day {day['day']}: {day['title']}", f"- **Morning:** {day['morning']}", f"- **Afternoon:** {day['afternoon']}", f"- **Evening:** {day['evening']}", f"- **Estimated day cost:** ₹{day['estimated_daily_cost_inr']:,.0f}"]
        lines.extend(f"- {note}" for note in day.get("notes", []))
    b = p["budget"]
    lines += ["\n## Budget", f"- Flights: ₹{b['flight_estimate_inr']:,.0f}", f"- Hotels: ₹{b['hotel_estimate_inr']:,.0f}", f"- Local transport: ₹{b['local_transport_estimate_inr']:,.0f}", f"- Activities: ₹{b['activities_estimate_inr']:,.0f}", f"- Contingency: ₹{b['contingency_inr']:,.0f}", f"- **Estimated total: ₹{b['estimated_total_inr']:,.0f}**", f"- **Budget status:** {'Within target' if b['within_budget'] else 'Above target'}", f"- {b['pricing_note']}", "\n## **Flight Suggestion**", p["flight_suggestion"], "\n## **Accommodation**", p["accommodation_plan"], "\n## **Weather**", p["weather_guidance"]]
    if p["changes_from_feedback"]: lines += ["\n## Changes Made From Your Feedback"] + [f"- {x}" for x in p["changes_from_feedback"]]
    if p["assumptions"]: lines += ["\n## Assumptions"] + [f"- {x}" for x in p["assumptions"]]
    if p["warnings"]: lines += ["\n## Important Notes"] + [f"- {x}" for x in p["warnings"]]
    return "\n".join(lines)
