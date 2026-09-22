"""Evidence-grounded travel planner with bounded LLM context and deterministic fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq


class DayPlan(BaseModel):
    day: int
    title: str
    morning: str
    afternoon: str
    evening: str
    estimated_daily_cost_inr: float = Field(ge=0)
    notes: list[str] = Field(default_factory=list)


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


SYSTEM_PROMPT = """
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
"""


def _json(value: Any, limit: int = 6000) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text[:limit]


def _groq_model() -> str:
    # GPT-OSS 20B is the safer default for the user's 8K TPM environment.
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")


def _gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")


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


def _compact_context(constraints, flight_results, live_flight_offers, hotel_results, live_hotel_offers, weather_results, budget, feedback, revision):
    # Deliberately keep the entire prompt small enough that input + requested
    # output stays comfortably below the project's ~5K TPM Groq target. The UI
    # still receives the complete provider objects separately.
    flights = [_compact_flight(o) for o in live_flight_offers[:4]]
    hotels = [_compact_hotel(o) for o in live_hotel_offers[:4]]
    weather = re.sub(r"\s+", " ", str(weather_results or "")).strip()[:700]
    return f"""
USER REQUEST:
{str(constraints.get('query') or '')[:700]}

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

Return the complete structured travel plan. Preserve provider facts exactly.
""".strip()


def _groq_plan(prompt: str) -> TravelPlan:
    key = os.getenv("GROQ_API_KEY", "").strip().strip('"')
    if not key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    llm = ChatGroq(
        model=_groq_model(), api_key=key, temperature=0.12,
        # Keep total requested TPM comfortably below the user's 8K limit.
        max_tokens=1700, reasoning_effort="low",
    )
    structured = llm.with_structured_output(TravelPlan, method="json_schema")
    return structured.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])


def _gemini_plan(prompt: str) -> TravelPlan:
    key = os.getenv("GEMINI_API_KEY", "").strip().strip('"')
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    # Use the stateful Chat helper rather than Models.generate_content. This is
    # the supported path for chat-style generation and avoids the AFC warning
    # seen with direct model generation in recent google-genai SDKs.
    chat = client.chats.create(
        model=_gemini_model(),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.12, max_output_tokens=1700,
            response_mime_type="application/json", response_schema=TravelPlan.model_json_schema(),
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
    )


def generate_travel_plan(*, user_query: str, constraints: dict[str, Any], flight_results: str, live_flight_offers: list[dict[str, Any]], live_hotel_offers: list[dict[str, Any]], hotel_results: str, weather_results: str, budget: dict[str, Any], feedback: str = "", revision_number: int = 0) -> tuple[TravelPlan, str]:
    prompt = _compact_context(constraints, flight_results, live_flight_offers, hotel_results, live_hotel_offers, weather_results, budget, feedback, revision_number)
    errors: list[str] = []
    try:
        return _groq_plan(prompt), "groq"
    except Exception as exc:
        errors.append(f"groq: {type(exc).__name__}: {exc}")
    try:
        return _gemini_plan(prompt), "gemini"
    except Exception as exc:
        errors.append(f"gemini: {type(exc).__name__}: {exc}")
    # Never leave the user with no travel plan just because both LLM quotas are exhausted.
    return _deterministic_fallback(
        constraints=constraints, flight_offers=live_flight_offers, hotel_offers=live_hotel_offers,
        budget=budget, weather_results=weather_results, feedback=feedback,
        reason="LLM providers were unavailable: " + " | ".join(errors)[:1200],
    ), "deterministic"


def plan_to_markdown(plan: TravelPlan) -> str:
    p = plan.model_dump()
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
