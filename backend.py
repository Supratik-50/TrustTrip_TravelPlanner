import os
import re
import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import Any, TypedDict, Annotated
import operator
import uuid
import asyncio
import json
import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)


from services.flight_service import FlightService
from services.hotel.hotel_service import HotelService
from services.travel_planner import parse_trip_request, candidate_dates, budget_from_live_data, _provider_text, _resolve_place
from services.chat_planner import generate_travel_plan, plan_to_markdown

from mcp_client import (
    tavily_mcp_search,
    extract_destination,
    forecast_mcp_search,
    weather_mcp_search,
)


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. "
            "Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


# LLM is isolated in services.chat_planner. The application uses Groq first
# and Gemini only as a fallback; no LLM is initialized at import time.

# =========================
# State - original fields kept, new control fields added
# =========================
class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str

    # Supervisor + guardrail state
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str

    # Original specialist results
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str

    # New budget + HITL state
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str

    # Real-time planning state
    live_flight_offers: list[dict[str, Any]]
    live_hotel_offers: list[dict[str, Any]]
    live_plan: dict[str, Any]
    replanning_mode: str
    llm_calls: int
    plan_json: dict[str, Any]
    llm_provider: str
    revision_count: int
    planning_blocked: bool
    planning_feedback: list[str]
    provider_replan_required: bool


# =========================
# Shared helpers
# =========================
KNOWN_AGENTS = {
    "itinerary_agent",
    "flight_agent",
    "budget_agent",
    "hotel_agent",
    "weather_agent",
}

# Exactly five specialist agents are coordinated by the Supervisor.
# The execution order is dependency-aware: flight/hotel/weather evidence is
# collected first, Budget aggregates those costs, and Itinerary produces the
# final evidence-grounded plan.
AGENT_ORDER = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }


# =========================
# Supervisor Agent + Input Guardrail
# =========================

GLOBAL_FLIGHT_INFO = """# Global Flight Information

## How international flight planning works
- **Route:** choose a departure airport/city and destination airport/city. City names and 3-letter IATA codes are supported.
- **Connections:** a nonstop flight has no connection; one-stop/two-stop itineraries connect through an intermediate airport. Connection time should be checked before booking.
- **Hubs:** major global hubs include London, Dubai, Doha, Singapore, Frankfurt, Amsterdam, Istanbul, Tokyo, Seoul and major U.S. gateway airports.
- **Baggage:** the included checked/cabin allowance depends on the fare and airline. Always verify the allowance attached to the selected fare.
- **International travel:** check passport, visa/entry, transit, health and airport requirements for the specific itinerary before payment.
- **Pricing:** fares are dynamic. A live quote is meaningful only when a concrete origin, destination and travel date are supplied.

### Trust Trip
For a bookable live recommendation, enter a route such as **Kolkata → Tokyo**, **Delhi → Dubai**, **New York → London**, or any other valid city/airport pair. The planner uses one adult traveller by default unless you specify another passenger mix.
"""
def supervisor_agent(state: TravelState):
    """Deterministic routing to avoid consuming Groq OTPM on guardrail/routing."""
    query = state["user_query"]
    q = query.lower()

    blocked_terms = ["malware", "ransomware", "weapon", "bomb making", "illegal drugs"]
    if any(term in q for term in blocked_terms):
        reason = "This application only handles safe travel-planning and travel-information requests."
        return {"guardrail_allowed": False, "guardrail_reason": reason, "selected_agents": [],
                "trip_constraints": _empty_constraints(), "supervisor_reasoning": reason,
                "final_response": reason, "messages": [AIMessage(content=reason)]}

    # Informational flight questions are not trip-planning requests. Do not run
    # the planner agents or inherit a previous route for them.
    info_request = (
        ("global flight" in q or "flight information" in q or "international flight information" in q)
        and not re.search(r"\b(?:from|to)\s+[a-z]{2,}\b", q)
        and not any(word in q for word in ("plan a trip", "plan my trip", "holiday", "vacation", "itinerary"))
    )
    if info_request:
        return {
            "guardrail_allowed": True,
            "guardrail_reason": "Flight information request accepted.",
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": "Informational flight guidance requested; live trip-planning agents are not required.",
            "final_response": GLOBAL_FLIGHT_INFO,
            "messages": [AIMessage(content=GLOBAL_FLIGHT_INFO)],
            "llm_calls": state.get("llm_calls", 0),
        }

    previous_constraints = state.get("trip_constraints") or {}
    constraints = parse_trip_request(query, previous_constraints)
    selected = AGENT_ORDER.copy()
    mode = "cheaper" if constraints.get("cheaper_mode") else "initial"
    if constraints.get("cheaper_mode"):
        reasoning = "Adaptive re-planning requested: live flight and accommodation searches will be repeated with cost-first ranking."
    else:
        reasoning = "Integrated live planning selected: flights, accommodation, weather, budget and itinerary will be refreshed from provider data."
    return {
        "guardrail_allowed": True,
        "guardrail_reason": "Travel request accepted.",
        "selected_agents": selected,
        "trip_constraints": constraints,
        "replanning_mode": mode,
        "supervisor_reasoning": reasoning,
        "live_flight_offers": [],
        "live_hotel_offers": [],
        "live_plan": {},
        "messages": [AIMessage(content="Travel request routed to live planning agents.")],
        "llm_calls": state.get("llm_calls", 0),
    }


def guardrail_blocked_agent(state: TravelState):
    reason = state.get("guardrail_reason") or "This application only handles safe travel-planning and travel-information requests."
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
        "llm_calls": state.get("llm_calls", 0),
    }


# =========================
# Flight Agent - original behavior kept
# =========================
FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Generate:
1. Likely departure airport
2. Likely arrival airport
3. Airlines serving this route
4. Typical flight duration
5. Estimated airfare range
6. Peak season pricing warning
7. Booking advice

Return concise travel guidance.
"""


def _flight_search_context(constraints: dict[str, Any], departure: str, return_date: str | None = None) -> dict[str, Any]:
    """Canonical, provider-ready context attached to every automatic offer.

    This is deliberately independent of the UI headline. It is the single source
    of truth later consumed by flight refresh/booking and by Suggested Plan cards.
    """
    passengers = ([{"type": "adult"}] * int(constraints.get("adults", 1)))
    passengers += ([{"type": "child"}] * int(constraints.get("children", 0)))
    passengers += ([{"type": "infant_without_seat"}] * int(constraints.get("infants", 0)))
    slices = [{"origin": str(constraints.get("origin") or "").upper(), "destination": str(constraints.get("destination") or "").upper(), "departure_date": departure}]
    if return_date:
        slices.append({"origin": str(constraints.get("destination") or "").upper(), "destination": str(constraints.get("origin") or "").upper(), "departure_date": return_date})
    return {
        "slices": slices,
        "passengers": passengers,
        "cabin_class": constraints.get("cabin_class") or "economy",
        "max_connections": int(constraints.get("max_connections", 2)),
        "origin": str(constraints.get("origin") or "").upper(),
        "destination": str(constraints.get("destination") or "").upper(),
        "departure_date": departure,
        "return_date": return_date,
        "passenger_count": len(passengers),
        "origin_city": constraints.get("origin_city") or "",
        "destination_city": constraints.get("destination_city") or "",
        "origin_country": constraints.get("origin_country") or "",
        "destination_country": constraints.get("destination_country") or "",
        "journey_scope": constraints.get("journey_scope") or "unknown",
    }


def flight_agent(state: TravelState):
    constraints = state.get("trip_constraints") or {}
    origin = constraints.get("origin")
    destination = constraints.get("destination")
    if not origin or not destination:
        missing = "origin" if not origin else "destination"
        return {
            "flight_results": f"Live flight search needs a specific {missing} airport/city. The planner can still build the destination itinerary, but it will not invent a departure airport.",
            "live_flight_offers": [],
            "flight_search_status": "missing_origin" if not origin else "missing_destination",
            "messages": [AIMessage(content=f"A specific {missing} airport or city is required for live flight recommendations.")],
            "llm_calls": state.get("llm_calls", 0),
        }

    service = FlightService()
    offers: list[dict[str, Any]] = []
    errors: list[str] = []
    passenger_list = ([{"type": "adult"}] * int(constraints.get("adults", 1)))
    passenger_list += ([{"type": "child"}] * int(constraints.get("children", 0)))
    passenger_list += ([{"type": "infant_without_seat"}] * int(constraints.get("infants", 0)))
    search_windows = candidate_dates(constraints, limit=1)
    recommendation_date = search_windows[0][0] if search_windows else None
    recommendation_return = search_windows[0][1] if search_windows else None
    for departure, return_date in search_windows:
        slices = [{"origin": origin, "destination": destination, "departure_date": departure}]
        requested_return = constraints.get("return_date") or return_date
        if requested_return:
            slices.append({"origin": destination, "destination": origin, "departure_date": requested_return})
        try:
            result = service.search({
                "slices": slices,
                "passengers": passenger_list,
                "cabin_class": "economy",
                "max_connections": 2,
            })
            canonical_context = _flight_search_context(constraints, departure, requested_return)
            for offer in result.get("offers", [])[:8]:
                offer["planning_departure"] = departure
                offer["planning_return"] = requested_return
                offer["search_context"] = {**canonical_context, "selected_offer": dict(offer)}
            offers.extend(result.get("offers", []))
        except Exception as exc:
            errors.append(str(exc))

    # Cost-first on adaptive requests; otherwise preserve the transparent value ranking.
    offers = [o for o in offers if o.get("price") is not None]
    preference = constraints.get("flight_preference") or ""
    def _departure_hour(offer):
        value = (offer.get("segments") or [{}])[0].get("departure")
        try:
            return int(str(value)[11:13])
        except Exception:
            return 99
    def _preference_penalty(offer):
        if preference == "nonstop":
            return int(offer.get("stops", 99))
        hour = _departure_hour(offer)
        if preference == "morning": return 0 if 5 <= hour < 12 else 1
        if preference == "afternoon": return 0 if 12 <= hour < 17 else 1
        if preference == "evening": return 0 if 17 <= hour < 22 else 1
        if preference == "night": return 0 if (hour >= 22 or hour < 5) else 1
        return 0
    if constraints.get("cheaper_mode"):
        offers.sort(key=lambda o: (_preference_penalty(o), float(o.get("price_inr") if o.get("price_inr") is not None else o.get("price", 10**12)), int(o.get("stops", 99))))
    else:
        offers.sort(key=lambda o: (_preference_penalty(o), -float(o.get("recommendation_score", 0)), float(o.get("price_inr") if o.get("price_inr") is not None else o.get("price", 10**12))))
    offers = offers[:12]
    # The first ranked offer is the deterministic recommendation. Its provider
    # price is the source of truth shown to the user and fed into planning.
    for idx, offer in enumerate(offers):
        offer["is_recommended"] = idx == 0
        offer["recommendation_rank"] = idx + 1
    recommended = offers[0] if offers else None
    lines = [
        f"Live {service.provider.name} flight search: {len(offers)} candidate offer(s) for {origin} → {destination}.",
        f"Date strategy: {'user-specified date' if constraints.get('departure_date') else 'near-term flexible windows because no date was supplied'}.",
    ]
    for idx, o in enumerate(offers[:5], 1):
        label = "RECOMMENDED · " if idx == 1 else ""
        lines.append(f"{label}{idx}. {o.get('currency')} {o.get('price')} total / ₹{o.get('price_inr') if o.get('price_inr') is not None else '—'} INR reference for {o.get('passenger_count') or len(o.get('passengers') or []) or 1} traveller(s) · {o.get('stops', 0)} stop(s) · {o.get('owner') or 'airline'} · {o.get('planning_departure')} → {o.get('planning_return') or 'one-way'}")
    if recommended:
        lines.append(
            f"Recommended flight price: {recommended.get('currency')} {recommended.get('price')} total "
            f"(≈ ₹{recommended.get('price_inr'):,.2f} INR; {recommended.get('passenger_count') or len(recommended.get('passengers') or []) or 1} traveller(s))."
        )
    if errors and not offers:
        lines.append("Provider search errors: " + " | ".join(errors[:2]))
    flight_status = "ok" if offers else ("provider_error" if errors else "no_offers")
    return {
        "flight_results": "\n".join(lines),
        "live_flight_offers": offers,
        "recommended_flight": recommended or {},
        "trip_constraints": {**constraints, **({"flight_recommendation_date": recommendation_date, "flight_recommendation_return": recommendation_return} if recommendation_date else {})},
        "messages": [AIMessage(content="Live flight offers refreshed.")],
        "flight_search_status": flight_status,
        "flight_search_errors": errors[:3],
        "llm_calls": state.get("llm_calls", 0),
    }

# =========================
# Hotel Agent - dual live-provider booking search
# =========================
def hotel_agent(state: TravelState):
    constraints = state.get("trip_constraints") or {}
    destination = constraints.get("destination_city") or ""
    if not constraints.get("destination"):
        return {"hotel_results": "Live hotel search needs a specific destination city/area, not only a country. The planner can still build the itinerary without inventing hotel availability.", "live_hotel_offers": [], "hotel_search_status": "missing_destination_city", "messages": [AIMessage(content="A specific destination city is required for live hotel recommendations.")]}
    departure = constraints.get("departure_date") or constraints.get("flight_recommendation_date")
    duration = int(constraints.get("duration_days") or 5)
    if not destination:
        return {"hotel_results": "Live hotel search needs a destination.", "live_hotel_offers": [], "hotel_search_status": "missing_destination", "messages": [AIMessage(content="Hotel search needs a destination.")]}
    if not departure:
        text = "Live hotel recommendations are waiting for a check-in date. Use Manual Hotel Search when you want to choose dates manually."
        return {"hotel_results": text, "live_hotel_offers": [], "hotel_search_status": "missing_date", "messages": [AIMessage(content=text)]}
    from datetime import date, timedelta
    checkout = constraints.get("return_date") or (date.fromisoformat(departure) + timedelta(days=max(1, duration - 1))).isoformat()
    request = {
        "destination": destination, "checkin": departure, "checkout": checkout,
        "adults": int(constraints.get("adults", 1)), "children": int(constraints.get("children", 0)),
        "rooms": 1, "guest_nationality": "IN", "currency": "USD",
        "budget_inr": constraints.get("budget_amount"), "cheaper_mode": bool(constraints.get("cheaper_mode")),
        "hotelbeds_destination_code": constraints.get("hotelbeds_destination_code"),
    }
    try:
        result = HotelService().search(request)
        offers = result.get("offers", [])
        errors = result.get("errors", [])
        if offers:
            lines=[f"Live dual-provider hotel search returned {len(offers)} normalized offer(s) for {destination}."]
            for i,o in enumerate(offers[:6],1):
                lines.append(f"{i}. {o.get('hotel_name','Hotel')} — {o.get('currency')} {float(o.get('price',0)):.2f} · ₹{float(o.get('price_inr') or 0):,.0f} total · {o.get('room_name','Room')} · {o.get('board_name','')} · provider={o.get('provider')}")
            if errors: lines.append("Provider notes: " + " | ".join(errors[:2]))
            return {"hotel_results": "\n".join(lines), "live_hotel_offers": offers, "hotel_search_status": "ok", "hotel_search_errors": errors[:3], "messages": [AIMessage(content="Dual-provider hotel offers refreshed.")]}
        msg = "No live hotel offer was returned for the requested destination/dates. I will not invent hotel prices or availability."
        if errors: msg += " Provider notes: " + " | ".join(errors[:2])
        return {"hotel_results": msg, "live_hotel_offers": [], "hotel_search_status": "no_offers", "hotel_search_errors": errors[:3], "messages": [AIMessage(content=msg)]}
    except Exception as exc:
        msg=f"Live hotel search is temporarily unavailable: {exc}. Do not invent hotel availability or current prices."
        return {"hotel_results": msg, "live_hotel_offers": [], "hotel_search_status": "unavailable", "hotel_search_errors": [str(exc)], "messages": [AIMessage(content="Hotel search is unavailable.")]}


# =========================
# Weather Agent - original behavior kept
# =========================
def weather_agent(state: TravelState):
    constraints = state.get("trip_constraints") or {}
    city = constraints.get("destination_city") or extract_destination(state["user_query"])

    try:
        weather_data = asyncio.run(
            weather_mcp_search(city)
        )

        forecast_data = asyncio.run(
            forecast_mcp_search(city)
        )

        weather_results = f"""
Current Weather:
{weather_data}

Forecast:
{forecast_data}
"""

    except Exception as exc:
        print(
            f"WEATHER AGENT MCP ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        weather_results = (
            f"Live weather information for {city} "
            "is temporarily unavailable. Give general "
            "seasonal guidance and advise the traveler "
            "to verify the forecast before departure."
        )

    return {
        "weather_results": weather_results,
        "messages": [
            AIMessage(
                content="Weather information processed."
            )
        ],
    }


# =========================
# Budget Agent - new specialist
# =========================
def budget_agent(state: TravelState):
    constraints = state.get("trip_constraints") or {}
    flights = state.get("live_flight_offers") or []
    hotel_text = state.get("hotel_results", "")
    hotel_offers = state.get("live_hotel_offers", [])
    summary = budget_from_live_data(constraints, flights, hotel_text, hotel_offers)
    target = summary.get("target_budget")
    status = "within target" if summary["within_budget"] else "above target"
    text = (
        f"Live planning budget ({summary['currency']}): flights ≈ {summary['flight_estimate']:.0f}; "
        f"hotel ≈ {summary['hotel_estimate']:.0f}; local transport ≈ {summary['local_transport_estimate']:.0f}; "
        f"activities ≈ {summary['activities_estimate']:.0f}; contingency ≈ {summary['contingency']:.0f}; "
        f"estimated total ≈ {summary['estimated_total']:.0f}."
    )
    if target is not None:
        text += f" Target: {float(target):.0f}; plan is {status}."
    text += f" Hotel pricing source: {summary['hotel_price_source']}."
    return {
        "budget_results": text,
        "live_plan": {"budget": summary},
        "messages": [AIMessage(content="Live budget recalculated.")],
        "llm_calls": state.get("llm_calls", 0),
    }

# =========================
# Itinerary Agent - original behavior extended with selected results
# =========================
def itinerary_agent(state: TravelState):
    """Generate one validated structured plan from provider evidence.

    This is the only LLM node. A rejected draft returns here directly, so
    specialist/provider agents are not called again and flight suggestions
    remain unchanged unless the user starts a new planning request.
    """
    constraints = state.get("trip_constraints") or {}
    budget = (state.get("live_plan") or {}).get("budget") or {}
    feedback = str(state.get("human_feedback", "")).strip()
    revision = int(state.get("revision_count", 0))

    # Never ask an LLM to "make something realistic" when the verified
    # provider evidence already tells us that the request is infeasible or
    # live data is unavailable. Return an actionable explanation instead.
    planning_feedback: list[str] = []
    if constraints.get("destination_country") and not constraints.get("destination"):
        planning_feedback.append(
            f"The request names the destination country ({constraints.get('destination_country')}) but not a specific destination city/airport. The itinerary can be planned at country level, but live flight and hotel availability will not be invented until a concrete airport/city is supplied."
        )
    if constraints.get("origin_country") and not constraints.get("origin"):
        planning_feedback.append(
            f"The request names the origin country ({constraints.get('origin_country')}) but not a specific departure city/airport. A live flight recommendation requires a concrete departure airport; do not invent one."
        )
    flight_status = state.get("flight_search_status", "ok")
    if constraints.get("origin") and constraints.get("destination") and constraints.get("departure_date"):
        if flight_status == "provider_error":
            planning_feedback.append(
                "Live flight data could not be retrieved right now. I will not invent flight prices or schedules. Please try again shortly or use Manual Flight Search."
            )
        elif flight_status == "no_offers":
            planning_feedback.append(
                "No live flight offer was returned for the requested route/date. I will not create a fictional flight recommendation. Try another date, route, or use Manual Flight Search."
            )

    if state.get("hotel_search_status") in {"unavailable", "no_offers"}:
        planning_feedback.append(
            "Live hotel data is temporarily unavailable, so I will not invent current hotel prices or availability. Please retry the hotel search, choose a different accommodation source, or continue only after live hotel data is available."
        )

    target = budget.get("target_budget")
    estimated = budget.get("estimated_total")
    if target is not None and estimated is not None and float(estimated) > float(target):
        over = float(estimated) - float(target)
        planning_feedback.append(
            f"Your target budget is ₹{float(target):,.0f}, while the current evidence-based estimate is about ₹{float(estimated):,.0f} (about ₹{over:,.0f} above target). A realistic plan cannot meet that budget with the current route/date. Please increase the budget, choose a lower-cost hotel/accommodation (budget hotel, hostel or guesthouse), choose cheaper dates, reduce activities, or change the destination."
        )

    # Missing live data or a budget conflict must not destroy the complete travel plan.
    # Pass these facts as warnings to the bounded planner; it will preserve all
    # available provider evidence and clearly mark anything that needs verification.
    warning_feedback = "\n".join(planning_feedback)

    try:
        plan, provider = generate_travel_plan(
            user_query=state.get("user_query", ""),
            constraints=constraints,
            flight_results=state.get("flight_results", ""),
            live_flight_offers=state.get("live_flight_offers", []),
            live_hotel_offers=state.get("live_hotel_offers", []),
            hotel_results=state.get("hotel_results", ""),
            weather_results=state.get("weather_results", ""),
            budget=budget,
            feedback=(feedback + ("\n\nSYSTEM WARNINGS:\n" + warning_feedback if warning_feedback else "")),
            revision_number=revision,
        )
        plan_json = plan.model_dump()
        itinerary = plan_to_markdown(plan)
        return {
            "itinerary": itinerary,
            "plan_json": plan_json,
            "llm_provider": provider,
            "llm_calls": state.get("llm_calls", 0) + 1,
            "approval_request": "Review this structured plan. Approve it, or tell Trust Trip exactly what you want changed.",
            "messages": [AIMessage(content=itinerary)],
        }
    except Exception as exc:
        print(f"ITINERARY PLANNER ERROR: {type(exc).__name__}: {exc}", flush=True)
        # generate_travel_plan already has a deterministic fallback. This guard
        # only protects the graph from an unexpected coding/serialization error.
        raise RuntimeError(f"Travel plan generation failed unexpectedly: {exc}") from exc


def human_approval_agent(state: TravelState):
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "plan_json": state.get("plan_json", {}),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "revision_number": state.get("revision_count", 0),
            "expected_response": {"approved": True, "feedback": "Optional revision feedback"},
        }
    )
    approved = bool(review.get("approved", False))
    feedback = str(review.get("feedback", "")).strip()
    current = state.get("trip_constraints") or {}
    updated = parse_trip_request(feedback, current) if feedback else current

    # A revision that changes route/date/passenger count is a provider-level
    # change, not merely a wording change. Re-run the provider agents so the
    # flight offer and budget match the new request. Ordinary itinerary edits
    # keep the existing live offers and consume no extra flight search.
    structural_keys = ("origin", "destination", "departure_date", "adults", "children", "infants")
    flight_words = (
        "flight", "flights", "fly", "airline", "nonstop", "non-stop",
        "direct flight", "morning flight", "afternoon flight", "evening flight",
        "night flight", "cheaper fare", "cheaper flight", "cheapest flight",
    )
    flight_request_change = any(word in feedback.lower() for word in flight_words)
    hotel_words = ("hotel", "hotels", "room", "rooms", "accommodation", "resort", "stay", "cheaper hotel", "breakfast", "free cancellation")
    hotel_request_change = any(word in feedback.lower() for word in hotel_words)
    provider_replan = (
        not approved and bool(feedback) and (
            any(updated.get(key) != current.get(key) for key in structural_keys)
            or flight_request_change
            or hotel_request_change
            or updated.get("flight_preference") != current.get("flight_preference")
            or updated.get("cheaper_mode") != current.get("cheaper_mode")
        )
    )
    return {
        "approved": approved,
        "human_feedback": feedback,
        "trip_constraints": updated,
        "provider_replan_required": provider_replan,
        "selected_agents": AGENT_ORDER.copy() if provider_replan else state.get("selected_agents", AGENT_ORDER.copy()),
        "revision_count": int(state.get("revision_count", 0)) + (0 if approved else 1),
        "messages": [AIMessage(content="Human review completed." + (" Provider data will be refreshed for the changed travel requirements." if provider_replan else ""))],
    }


def route_after_human(state: TravelState) -> str:
    if state.get("approved"):
        return "final_agent"
    max_revisions = int(os.getenv("MAX_PLAN_REVISIONS", "5"))
    if int(state.get("revision_count", 0)) >= max_revisions:
        return "final_agent"
    if state.get("provider_replan_required"):
        return "flight_agent"
    return "itinerary_agent"


def final_agent(state: TravelState):
    # Informational requests can arrive here directly from the supervisor.
    direct_response = str(state.get("final_response") or "").strip()
    if not state.get("selected_agents") and direct_response:
        return {"final_response": direct_response, "messages": [AIMessage(content=direct_response)]}

    draft = str(state.get("itinerary", "")).strip()
    plan = state.get("plan_json") or {}
    if not draft:
        draft = "Your travel plan is ready."
    if int(state.get("revision_count", 0)) >= int(os.getenv("MAX_PLAN_REVISIONS", "5")) and not state.get("approved"):
        draft += "\n\n**Review limit reached:** this draft is shown without further automatic revisions."
    draft += (
        "\n\n**Booking note:** Flight booking remains separate. The selected fare must be rechecked and you must explicitly confirm before any booking request is submitted."
    )
    return {"final_response": draft, "messages": [AIMessage(content=draft)]}


# =========================
# Dynamic Supervisor Routing
# =========================
ROUTE_MAP = {
    "guardrail_blocked": "guardrail_blocked",
    "flight_agent": "flight_agent",
    "hotel_agent": "hotel_agent",
    "weather_agent": "weather_agent",
    "budget_agent": "budget_agent",
    "itinerary_agent": "itinerary_agent",
}


def _selected_agents(state: TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"

    selected = _selected_agents(state)
    if not selected and str(state.get("final_response") or "").strip():
        return "final_agent"
    return selected[0] if selected else "itinerary_agent"


def route_after_agent(current_agent: str):
    def route(state: TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1 :]:
            if next_agent in selected:
                return next_agent

        return "itinerary_agent"

    return route


# =========================
# Build Graph
# =========================
graph = StateGraph(TravelState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)

graph.add_conditional_edges(
    "flight_agent", route_after_agent("flight_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "weather_agent", route_after_agent("weather_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "budget_agent", route_after_agent("budget_agent"), ROUTE_MAP
)

def route_after_itinerary(state: TravelState) -> str:
    return "final_agent" if state.get("planning_blocked") else "human_approval"

graph.add_conditional_edges("itinerary_agent", route_after_itinerary, {"human_approval": "human_approval", "final_agent": "final_agent"})
graph.add_conditional_edges("human_approval", route_after_human, {"flight_agent": "flight_agent", "itinerary_agent": "itinerary_agent", "final_agent": "final_agent"})
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)

# =========================
# PostgreSQL Checkpointer - original persistence kept
# =========================
DATABASE_URL = get_database_url()
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row,
)
checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)


# =========================
# FastAPI-facing helpers
# =========================
def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get(
            "itinerary", ""
        )

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "hotel_search_status": result.get("hotel_search_status", ""),
        "hotel_search_errors": result.get("hotel_search_errors", [])[:3],
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": (
            interrupt_payload.get("draft_itinerary", "")
            if interrupt_payload
            else result.get("itinerary", "")
        ),
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "live_flight_offers": result.get("live_flight_offers", [])[:12],
        "live_hotel_offers": result.get("live_hotel_offers", [])[:20],
        "live_plan": result.get("live_plan", {}),
        "replanning_mode": result.get("replanning_mode", "initial"),
        "llm_calls": result.get("llm_calls", 0),
        "plan_json": result.get("plan_json", {}),
        "llm_provider": result.get("llm_provider", ""),
        "revision_count": result.get("revision_count", 0),
        "planning_blocked": result.get("planning_blocked", False),
        "planning_feedback": result.get("planning_feedback", []),
        "flight_search_status": result.get("flight_search_status", "ok"),
        "flight_search_errors": result.get("flight_search_errors", []),
        "flight_provider": os.getenv("FLIGHT_PROVIDER", "duffel").strip().lower(),
        "duffel_mode": os.getenv("DUFFEL_MODE", "test").strip().lower(),
        "hotel_search_status": result.get("hotel_search_status", "ok"),
        "provider_replan_required": result.get("provider_replan_required", False),
    }


def run_travel_agent(user_input: str, thread_id: str | None = None):
    """Start a new travel-planning run and pause at human approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {"configurable": {"thread_id": thread_id}}

    previous = {}
    if request_thread := thread_id:
        try:
            previous = dict(travel_graph.get_state({"configurable": {"thread_id": request_thread}}).values or {})
        except Exception:
            previous = {}

    result = travel_graph.invoke(
        {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": previous.get("trip_constraints") or _empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "final_response": "",
            "live_flight_offers": [],
            "live_plan": {},
            "replanning_mode": "initial",
            "llm_calls": 0,
            "plan_json": {},
            "llm_provider": "",
            "revision_count": 0,
            "planning_blocked": False,
            "planning_feedback": [],
            "provider_replan_required": False,
        },
        config=config,
    )

    return _serialize_result(result, thread_id)


def resume_travel_agent(
    thread_id: str,
    approved: bool,
    feedback: str = "",
):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError("thread_id is required to resume a travel plan.")

    config = {"configurable": {"thread_id": thread_id}}
    result = travel_graph.invoke(
        Command(
            resume={
                "approved": approved,
                "feedback": feedback.strip(),
            }
        ),
        config=config,
    )

    return _serialize_result(result, thread_id)
import re
