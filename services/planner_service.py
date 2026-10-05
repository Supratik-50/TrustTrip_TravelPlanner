"""Application Use Case Layer for Trust Trip Universal Planner.

Orchestrates intent routing, canonical state resolution, thread lifecycle,
and delegating to LangGraph without polluting API routes or low-level agents.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from domain.changes import apply_trip_change_set
from domain.compat import build_trip_request, trip_request_to_legacy
from domain.research import TripIntent
from domain.trip import TripRequest
from services.intent_router import IntentDecision, IntentRouter
from services.travel_planner import parse_trip_request, _resolve_place
from services.trip_store import TripStore
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
from services.travel_intelligence.liteapi_adapter import LiteAPIHotelAdapter
from domain.live_offer import RevalidationResult, RevalidationStatus

logger = logging.getLogger("trusttrip.planner")

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


class PlannerService:
    """Universal travel planner use case orchestrator."""

    def __init__(self) -> None:
        self.intent_router = IntentRouter()
        self.trip_store = TripStore()

    def plan_travel(
        self,
        user_input: str,
        thread_id: str | None = None,
        authenticated_user_id: str | None = None,
        trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute universal travel planning request."""
        from backend import travel_graph, _serialize_result, _empty_constraints

        # 1. Authorize thread and trip ownership and resolve canonical trip
        target_trip_id = trip_id
        canonical_trip_record: dict[str, Any] | None = None
        if authenticated_user_id:
            if thread_id:
                th = self.trip_store.get_thread(thread_id, authenticated_user_id)
                if not th:
                    raise PermissionError(
                        f"Access denied: Thread '{thread_id}' does not belong to the authenticated user."
                    )
                th_trip_id = th.get("trip_id")
                if target_trip_id and th_trip_id and str(th_trip_id) != str(target_trip_id):
                    raise ValueError(
                        f"Trip ID mismatch: thread bound to trip {th_trip_id} but request specified trip {target_trip_id}"
                    )
                if not target_trip_id and th_trip_id:
                    target_trip_id = str(th_trip_id)
            else:
                created = self.trip_store.create_thread(
                    user_id=authenticated_user_id,
                    trip_id=trip_id,
                )
                thread_id = str(created["id"])
                if not target_trip_id and created.get("trip_id"):
                    target_trip_id = str(created["trip_id"])

            if target_trip_id:
                canonical_trip_record = self.trip_store.get_trip(target_trip_id, authenticated_user_id)
                if not canonical_trip_record:
                    raise PermissionError(
                        f"Access denied: Trip '{target_trip_id}' does not exist or does not belong to the user."
                    )
        else:
            if not thread_id:
                thread_id = f"user_{uuid.uuid4().hex}"

        # 2. Inspect existing LangGraph state for this thread
        config = {"configurable": {"thread_id": thread_id}}
        existing_state: dict[str, Any] = {}
        try:
            existing_state = dict(travel_graph.get_state(config).values or {})
        except Exception:
            existing_state = {}

        existing_trip: TripRequest | None = existing_state.get("trip_request")
        if not existing_trip and existing_state.get("trip_constraints"):
            try:
                existing_trip = build_trip_request(
                    existing_state["trip_constraints"],
                    thread_id=thread_id,
                    user_id=authenticated_user_id,
                )
            except Exception:
                existing_trip = None

        has_canonical_constraints = bool(
            canonical_trip_record
            and (canonical_trip_record.get("destination") or canonical_trip_record.get("origin"))
        )
        canonical_constraints: dict[str, Any] | None = None
        if has_canonical_constraints and canonical_trip_record:
            origin_raw = canonical_trip_record.get("origin") or ""
            dest_raw = canonical_trip_record.get("destination") or ""
            meta = canonical_trip_record.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            meta = dict(meta)

            constraints_dict = meta.get("constraints") or {}
            if not isinstance(constraints_dict, dict):
                constraints_dict = {}

            start_date = (
                constraints_dict.get("start_date")
                or constraints_dict.get("departure_date")
                or meta.get("start_date")
                or meta.get("departure_date")
            )
            end_date = (
                constraints_dict.get("end_date")
                or constraints_dict.get("return_date")
                or meta.get("end_date")
                or meta.get("return_date")
            )
            travelers_count = int(
                constraints_dict.get("travelers_count")
                or constraints_dict.get("passengers")
                or constraints_dict.get("adults")
                or meta.get("travelers_count")
                or meta.get("passengers")
                or meta.get("adults")
                or 1
            )
            budget_val = (
                constraints_dict.get("budget")
                or constraints_dict.get("budget_amount")
                or meta.get("budget")
                or meta.get("budget_amount")
            )
            currency_val = (
                constraints_dict.get("currency")
                or meta.get("currency")
                or "INR"
            )
            style_val = (
                constraints_dict.get("travel_style")
                or constraints_dict.get("style")
                or meta.get("travel_style")
                or meta.get("style")
                or ""
            )
            rev_num = canonical_trip_record.get("current_revision_number", 0)

            orig_code, orig_city, orig_country = _resolve_place(origin_raw) if origin_raw else (None, None, None)
            dest_code, dest_city, dest_country = _resolve_place(dest_raw) if dest_raw else (None, None, None)

            duration_days = 5
            if start_date and end_date:
                try:
                    d_start = date.fromisoformat(str(start_date)[:10])
                    d_end = date.fromisoformat(str(end_date)[:10])
                    calc_dur = (d_end - d_start).days + 1
                    if calc_dur > 0:
                        duration_days = calc_dur
                except Exception:
                    pass
            elif meta.get("duration_days"):
                duration_days = int(meta["duration_days"])

            canonical_constraints = {
                "origin": orig_code,
                "origin_airport": orig_code,
                "origin_code": orig_code,
                "origin_query": origin_raw,
                "origin_city": orig_city or origin_raw,
                "origin_country": orig_country or "",
                "destination": dest_code,
                "destination_airport": dest_code,
                "destination_code": dest_code,
                "destination_query": dest_raw,
                "destination_city": dest_city or dest_raw,
                "destination_country": dest_country or "",
                "departure_date": str(start_date) if start_date else None,
                "return_date": str(end_date) if end_date else None,
                "duration_days": duration_days,
                "adults": travelers_count,
                "budget_amount": float(budget_val) if budget_val is not None else None,
                "currency": currency_val,
                "travel_style": style_val,
                "_is_canonical": True,
                "trip_id": str(canonical_trip_record["id"]),
            }

            authoritative_canonical_trip = build_trip_request(
                canonical_constraints,
                trip_id=canonical_trip_record["id"],
                user_id=authenticated_user_id,
                thread_id=thread_id,
                original_query=user_input,
                intent=TripIntent.FOLLOW_UP,
                revision=rev_num,
            )

            # Active canonical trip anchors existing_trip if not yet initialized
            if not existing_trip or str(getattr(existing_trip, "trip_id", "")) != str(canonical_trip_record["id"]):
                existing_trip = authoritative_canonical_trip

        # 3. Intent Routing
        decision: IntentDecision = self.intent_router.classify_intent(
            query=user_input,
            existing_trip=existing_trip,
            thread_id=thread_id,
            user_id=authenticated_user_id,
        )

        # Observability log (without PII or secrets)
        logger.info(
            "planner.intent_decision user_id=%s thread_id=%s intent=%s confidence=%.2f source=%s reason=%s",
            authenticated_user_id,
            thread_id,
            decision.intent.value,
            decision.confidence,
            decision.source,
            decision.reason,
        )

        # 4. Handle GLOBAL_FLIGHT_RESEARCH boundary (Phase 3 boundary only; full Tavily/RAG research belongs to Phase 8)
        if decision.intent == TripIntent.GLOBAL_FLIGHT_RESEARCH:
            res_dict = {
                "guardrail_allowed": True,
                "guardrail_reason": "Flight information request accepted.",
                "selected_agents": [],
                "trip_constraints": _empty_constraints(),
                "trip_request": existing_trip,
                "supervisor_reasoning": "Informational flight guidance requested; live trip-planning agents are not required.",
                "final_response": GLOBAL_FLIGHT_INFO,
                "messages": [AIMessage(content=GLOBAL_FLIGHT_INFO)],
                "llm_calls": existing_state.get("llm_calls", 0),
                "flight_results": "",
                "hotel_results": "",
                "weather_results": "",
                "budget_results": "",
                "itinerary": "",
                "approval_request": "",
                "approved": False,
                "human_feedback": "",
                "live_flight_offers": [],
                "live_hotel_offers": [],
                "live_plan": {},
                "replanning_mode": "info",
                "plan_json": {},
                "llm_provider": "",
                "revision_count": existing_state.get("revision_count", 0),
                "planning_blocked": False,
                "planning_feedback": [],
                "provider_replan_required": False,
            }
            serialized = _serialize_result(res_dict, thread_id)
            serialized["trip_summary"] = "Global Flight Information Guidance"
            serialized["final_response"] = GLOBAL_FLIGHT_INFO
            serialized["intent"] = decision.intent.value
            serialized["intent_decision"] = decision.model_dump(mode="json")
            serialized["trip_request"] = existing_trip.model_dump(mode="json") if existing_trip else None
            return serialized

        # 5. Handle BOOKING_OPERATION boundary (Phase 3 boundary only; payment and order creation belong to Phase 9)
        if decision.intent == TripIntent.BOOKING_OPERATION:
            booking_msg = (
                "Booking operation recognized. To complete your booking, please confirm passenger "
                "details and proceed through secure Razorpay Checkout on the booking section."
            )
            res_dict = {
                "guardrail_allowed": True,
                "guardrail_reason": "Booking operation recognized.",
                "selected_agents": [],
                "trip_constraints": existing_state.get("trip_constraints", _empty_constraints()),
                "trip_request": existing_trip,
                "supervisor_reasoning": "Booking operation recognized; handing off to secure checkout lifecycle.",
                "final_response": booking_msg,
                "messages": [AIMessage(content=booking_msg)],
                "flight_results": existing_state.get("flight_results", ""),
                "hotel_results": existing_state.get("hotel_results", ""),
                "weather_results": existing_state.get("weather_results", ""),
                "budget_results": existing_state.get("budget_results", ""),
                "itinerary": existing_state.get("itinerary", ""),
                "approval_request": existing_state.get("approval_request", ""),
                "approved": existing_state.get("approved", True),
                "human_feedback": "",
                "live_flight_offers": existing_state.get("live_flight_offers", []),
                "live_hotel_offers": existing_state.get("live_hotel_offers", []),
                "live_plan": existing_state.get("live_plan", {}),
                "replanning_mode": "booking",
                "llm_calls": existing_state.get("llm_calls", 0),
                "plan_json": existing_state.get("plan_json", {}),
                "llm_provider": existing_state.get("llm_provider", ""),
                "revision_count": existing_state.get("revision_count", 0),
                "planning_blocked": False,
                "planning_feedback": [],
                "provider_replan_required": False,
            }
            serialized = _serialize_result(res_dict, thread_id)
            serialized["trip_summary"] = "Booking Checkout Operation"
            serialized["final_response"] = booking_msg
            serialized["intent"] = decision.intent.value
            serialized["intent_decision"] = decision.model_dump(mode="json")
            serialized["trip_request"] = existing_trip.model_dump(mode="json") if existing_trip else None
            serialized["booking_handoff"] = True
            return serialized

        # 6. Resolve Canonical TripRequest for Planning
        if has_canonical_constraints and canonical_constraints and canonical_trip_record:
            # Planning inside an authoritative canonical trip.
            # User preferences enrich the itinerary, but hard trip constraints (origin, destination, dates, travelers, budget)
            # are strictly preserved unless the user explicitly requested a change.
            if decision.changes and decision.changes.changed_fields:
                canonical_trip = apply_trip_change_set(existing_trip, decision.changes)
                canonical_trip = canonical_trip.model_copy(update={
                    "intent": TripIntent.FOLLOW_UP,
                    "original_query": user_input,
                    "revision": existing_trip.revision + 1,
                })
            else:
                prev_legacy = trip_request_to_legacy(existing_trip)
                prev_legacy["_is_canonical"] = True
                parsed = parse_trip_request(user_input, previous=prev_legacy)

                # Authoritative canonical trip constraints ALWAYS WIN over arbitrary prompt text
                parsed["origin"] = canonical_constraints.get("origin")
                parsed["origin_airport"] = canonical_constraints.get("origin_airport")
                parsed["origin_code"] = canonical_constraints.get("origin_code")
                parsed["origin_city"] = canonical_constraints.get("origin_city")
                parsed["origin_country"] = canonical_constraints.get("origin_country")
                parsed["origin_query"] = canonical_constraints.get("origin_query")
                parsed["destination"] = canonical_constraints.get("destination")
                parsed["destination_airport"] = canonical_constraints.get("destination_airport")
                parsed["destination_code"] = canonical_constraints.get("destination_code")
                parsed["destination_city"] = canonical_constraints.get("destination_city")
                parsed["destination_country"] = canonical_constraints.get("destination_country")
                parsed["destination_query"] = canonical_constraints.get("destination_query")
                parsed["departure_date"] = canonical_constraints.get("departure_date")
                parsed["return_date"] = canonical_constraints.get("return_date")
                parsed["duration_days"] = canonical_constraints.get("duration_days")
                parsed["adults"] = canonical_constraints.get("adults")
                if canonical_constraints.get("budget_amount") is not None:
                    parsed["budget_amount"] = canonical_constraints.get("budget_amount")
                parsed["currency"] = canonical_constraints.get("currency")
                parsed["_is_canonical"] = True

                canonical_trip = build_trip_request(
                    parsed,
                    trip_id=existing_trip.trip_id,
                    thread_id=thread_id,
                    user_id=authenticated_user_id,
                    original_query=user_input,
                    intent=TripIntent.FOLLOW_UP,
                    revision=existing_trip.revision + (1 if existing_state else 0),
                )
        elif decision.intent == TripIntent.NEW_TRIP or not existing_trip:
            # Standalone new trip: completely clean context, no previous route/passenger/budget leakage
            raw_constraints = parse_trip_request(user_input, previous={})
            canonical_trip = build_trip_request(
                raw_constraints,
                thread_id=thread_id,
                user_id=authenticated_user_id,
                original_query=user_input,
                intent=TripIntent.NEW_TRIP,
                revision=0,
            )
        else:
            # FOLLOW_UP: Apply discrete changes to existing canonical TripRequest
            if decision.changes and decision.changes.changed_fields:
                canonical_trip = apply_trip_change_set(existing_trip, decision.changes)
                canonical_trip = canonical_trip.model_copy(update={
                    "intent": TripIntent.FOLLOW_UP,
                    "original_query": user_input,
                    "revision": existing_trip.revision + 1,
                })
            else:
                prev_legacy = trip_request_to_legacy(existing_trip)
                parsed = parse_trip_request(user_input, previous=prev_legacy)
                canonical_trip = build_trip_request(
                    parsed,
                    trip_id=existing_trip.trip_id,
                    thread_id=thread_id,
                    user_id=authenticated_user_id,
                    original_query=user_input,
                    intent=TripIntent.FOLLOW_UP,
                    revision=existing_trip.revision + 1,
                )

        # 7. Generate compatibility projection for downstream specialist agents
        compatibility_constraints = trip_request_to_legacy(canonical_trip)
        if canonical_constraints:
            compatibility_constraints["_is_canonical"] = True
            compatibility_constraints["trip_id"] = str(canonical_trip_record["id"])

        # 8. Execute LangGraph Orchestrator
        initial_state = {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": compatibility_constraints,
            "trip_request": canonical_trip,
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
            "live_hotel_offers": [],
            "live_plan": {},
            "replanning_mode": "cheaper" if canonical_trip.budget.mode.value == "cheaper" else "initial",
            "llm_calls": existing_state.get("llm_calls", 0),
            "plan_json": {},
            "llm_provider": "",
            "revision_count": 0 if decision.intent == TripIntent.NEW_TRIP else existing_state.get("revision_count", 0),
            "planning_blocked": False,
            "planning_feedback": [],
            "provider_replan_required": False,
        }

        result = travel_graph.invoke(initial_state, config=config)

        # 9. Serialize result including intent metadata and client views
        serialized = _serialize_result(result, thread_id)
        if not serialized.get("final_response") and serialized.get("answer"):
            serialized["final_response"] = serialized["answer"]
        serialized["intent"] = decision.intent.value
        serialized["intent_decision"] = decision.model_dump(mode="json")
        serialized["trip_request"] = canonical_trip.model_dump(mode="json")

        # Project client views strictly shielding internal provider_offer_id
        if "live_flight_offers" in serialized:
            serialized["live_flight_offers"] = [
                (o.to_client_view().model_dump(mode="json") if hasattr(o, "to_client_view")
                 else (o.model_dump(mode="json") if hasattr(o, "model_dump") else o))
                for o in serialized["live_flight_offers"]
            ]
        if "live_hotel_offers" in serialized:
            serialized["live_hotel_offers"] = [
                (o.to_client_view().model_dump(mode="json") if hasattr(o, "to_client_view")
                 else (o.model_dump(mode="json") if hasattr(o, "model_dump") else o))
                for o in serialized["live_hotel_offers"]
            ]
        # ── Step 10: Server-side canonical invariant validation ──────────────────
        # Three-state rule for every required field:
        #   canonical exists + generated exists + equal   → PASS
        #   canonical exists + generated exists + differs → REJECT
        #   canonical exists + generated MISSING          → REJECT
        #   canonical field absent                        → not required (skip)
        #
        # Plan-side values are read EXCLUSIVELY from plan_json.
        # compatibility_constraints MUST NOT be used to fill in a missing generated
        # value — doing so would manufacture a match for a field the planner never
        # actually produced.
        if has_canonical_constraints and canonical_trip_record and canonical_constraints:
            # If planning was blocked due to an upstream LLM outage, bypass destination validation
            # so the structured outage status can reach the API caller.
            if serialized.get("planning_blocked") or serialized.get("plan_json", {}).get("status") == "unavailable":
                return serialized

            plan_json = serialized.get("plan_json") or {}

            def _plan_val(key: str):
                """Read a field ONLY from the generated plan_json (None if absent/empty)."""
                v = plan_json.get(key)
                return v if v not in (None, "") else None

            def _plan_str(key: str) -> str:
                """Read a string field ONLY from plan_json; returns '' when absent."""
                v = plan_json.get(key)
                return str(v).strip().lower() if v not in (None, "") else ""

            # ── Destination ───────────────────────────────────────────────────
            expected_dest      = str(canonical_trip_record.get("destination") or "").strip().lower()
            expected_dest_city = str(canonical_constraints.get("destination_city") or "").strip().lower()
            expected_dest_code = str(canonical_constraints.get("destination") or "").strip().lower()
            # Read destination from plan_json only (city or IATA sub-fields only).
            raw_plan_dest = plan_json.get("destination") or plan_json.get("destination_city") or ""
            plan_dest = str(raw_plan_dest).strip().lower()
            plan_dest_airport = _plan_str("destination_airport")

            if expected_dest:
                if not plan_dest:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required destination; "
                        f"canonical value is '{canonical_trip_record.get('destination')}'."
                    )
                dest_tokens      = {w for w in re.sub(r"[,\-.]", " ", f"{expected_dest} {expected_dest_city}").split() if len(w) >= 3}
                plan_dest_tokens = {w for w in re.sub(r"[,\-.]", " ", plan_dest).split() if len(w) >= 3}
                is_dest_matching = (
                    bool(dest_tokens & plan_dest_tokens)
                    or (expected_dest_code and expected_dest_code == plan_dest)
                    or (expected_dest_city and (expected_dest_city in plan_dest or plan_dest in expected_dest_city))
                )
                if not is_dest_matching:
                    raise ValueError(
                        f"Consistency violation: Plan destination '{plan_dest}' does not match "
                        f"canonical trip destination '{canonical_trip_record.get('destination')}'."
                    )

            # ── Destination Airport ───────────────────────────────────────────
            # Canonical IATA code present → plan MUST supply a matching airport.
            if expected_dest_code:
                if not plan_dest_airport:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required destination_airport; "
                        f"canonical value is '{expected_dest_code.upper()}'."
                    )
                if plan_dest_airport not in ("tbd", "any") and plan_dest_airport != expected_dest_code:
                    raise ValueError(
                        f"Consistency violation: Plan destination airport '{plan_dest_airport.upper()}' "
                        f"does not match canonical destination airport '{expected_dest_code.upper()}'."
                    )

            # ── Origin ────────────────────────────────────────────────────────
            expected_origin      = str(canonical_trip_record.get("origin") or "").strip().lower()
            expected_origin_city = str(canonical_constraints.get("origin_city") or "").strip().lower()
            expected_origin_code = str(canonical_constraints.get("origin") or "").strip().lower()
            # Read from plan_json only — no compat_constraints fallback.
            raw_plan_origin = plan_json.get("origin") or plan_json.get("origin_city") or ""
            plan_origin = str(raw_plan_origin).strip().lower()
            plan_origin_airport = _plan_str("origin_airport")

            if expected_origin:
                if not plan_origin:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required origin; "
                        f"canonical value is '{canonical_trip_record.get('origin')}'."
                    )
                origin_tokens      = {w for w in re.sub(r"[,\-.]", " ", f"{expected_origin} {expected_origin_city}").split() if len(w) >= 3}
                plan_origin_tokens = {w for w in re.sub(r"[,\-.]", " ", plan_origin).split() if len(w) >= 3}
                is_origin_matching = (
                    bool(origin_tokens & plan_origin_tokens)
                    or (expected_origin_code and expected_origin_code == plan_origin)
                    or (expected_origin_city and (expected_origin_city in plan_origin or plan_origin in expected_origin_city))
                )
                if not is_origin_matching:
                    raise ValueError(
                        f"Consistency violation: Plan origin '{plan_origin}' does not match "
                        f"canonical trip origin '{canonical_trip_record.get('origin')}'."
                    )

            # ── Origin Airport ────────────────────────────────────────────────
            # Canonical IATA code present → plan MUST supply a matching airport.
            if expected_origin_code:
                if not plan_origin_airport:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required origin_airport; "
                        f"canonical value is '{expected_origin_code.upper()}'."
                    )
                if plan_origin_airport not in ("tbd", "any") and plan_origin_airport != expected_origin_code:
                    raise ValueError(
                        f"Consistency violation: Plan origin airport '{plan_origin_airport.upper()}' "
                        f"does not match canonical origin airport '{expected_origin_code.upper()}'."
                    )

            # ── Departure date ────────────────────────────────────────────────
            expected_dep = canonical_trip_record.get("start_date") or canonical_constraints.get("departure_date")
            plan_dep = _plan_val("departure_date")  # plan_json ONLY — no compat fallback
            if expected_dep:
                if plan_dep is None:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required departure_date; "
                        f"canonical value is '{expected_dep}'."
                    )
                if str(expected_dep)[:10] != str(plan_dep)[:10]:
                    raise ValueError(
                        f"Consistency violation: Plan departure date '{plan_dep}' does not match "
                        f"canonical departure date '{expected_dep}'."
                    )

            # ── Return date ───────────────────────────────────────────────────
            expected_ret = canonical_trip_record.get("end_date") or canonical_constraints.get("return_date")
            plan_ret = _plan_val("return_date")  # plan_json ONLY — no compat fallback
            if expected_ret:
                if plan_ret is None:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required return_date; "
                        f"canonical value is '{expected_ret}'."
                    )
                if str(expected_ret)[:10] != str(plan_ret)[:10]:
                    raise ValueError(
                        f"Consistency violation: Plan return date '{plan_ret}' does not match "
                        f"canonical return date '{expected_ret}'."
                    )

            # ── Duration ──────────────────────────────────────────────────────
            expected_duration = canonical_constraints.get("duration_days") or canonical_trip_record.get("duration_days")
            plan_duration = _plan_val("duration_days")  # plan_json ONLY — no compat fallback
            if expected_duration:
                if plan_duration is None:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required duration_days; "
                        f"canonical value is '{expected_duration} days'."
                    )
                if int(expected_duration) != int(plan_duration):
                    raise ValueError(
                        f"Consistency violation: Plan duration '{plan_duration} days' does not match "
                        f"canonical duration '{expected_duration} days'."
                    )

            # ── Travelers / Adult count ───────────────────────────────────────
            expected_travelers = (
                canonical_constraints.get("adults")
                or canonical_trip_record.get("travelers_count")
                or (canonical_trip_record.get("metadata", {}).get("travelers_count")
                    if isinstance(canonical_trip_record.get("metadata"), dict) else None)
            )
            plan_travelers = _plan_val("travelers")  # plan_json ONLY — no compat fallback
            if expected_travelers:
                if plan_travelers is None:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required travelers count; "
                        f"canonical value is '{expected_travelers}'."
                    )
                if int(expected_travelers) != int(plan_travelers):
                    raise ValueError(
                        f"Consistency violation: Plan traveler count '{plan_travelers}' does not match "
                        f"canonical traveler count '{expected_travelers}'."
                    )

            # ── Target Budget ─────────────────────────────────────────────────
            # Use explicit None checks so that a canonical budget of 0 is not
            # mistaken for "no budget" by the truthiness of an `or` expression.
            _cb_amount = canonical_constraints.get("budget_amount")
            _cb_record = canonical_trip_record.get("budget")
            expected_budget = _cb_amount if _cb_amount is not None else _cb_record
            # Budget object read exclusively from the generated plan — no compat fallback.
            plan_budget_obj: dict = plan_json.get("budget") or {}
            if not plan_budget_obj and isinstance(serialized.get("budget_results"), dict):
                plan_budget_obj = serialized["budget_results"]
            # Extract budget value from plan object only (never from compatibility_constraints).
            plan_budget = plan_budget_obj.get("target_budget") if plan_budget_obj.get("target_budget") is not None else plan_budget_obj.get("target_budget_inr")
            if expected_budget is not None:
                if plan_budget is None:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required target_budget; "
                        f"canonical value is '{expected_budget}'."
                    )
                if float(expected_budget) != float(plan_budget):
                    raise ValueError(
                        f"Consistency violation: Plan target budget '{plan_budget}' does not match "
                        f"canonical target budget '{expected_budget}'."
                    )

            # ── Currency ──────────────────────────────────────────────────────
            # Only validate currency when the canonical record has an authoritative value.
            # Do NOT default missing generated currency to 'INR' — treat missing as a violation.
            canonical_currency_raw = canonical_trip_record.get("currency") or canonical_constraints.get("currency")
            if canonical_currency_raw:
                expected_currency = str(canonical_currency_raw).upper()
                plan_currency_raw = plan_budget_obj.get("currency")
                if plan_currency_raw is None:
                    raise ValueError(
                        f"Consistency violation: Plan is missing required currency; "
                        f"canonical value is '{expected_currency}'."
                    )
                if str(plan_currency_raw).upper() != expected_currency:
                    raise ValueError(
                        f"Consistency violation: Plan currency '{str(plan_currency_raw).upper()}' does not match "
                        f"canonical trip currency '{expected_currency}'."
                    )

        return serialized

    async def revalidate_offer(
        self,
        snapshot_id: uuid.UUID | str,
        authenticated_user_id: uuid.UUID | str,
    ) -> RevalidationResult:
        """Secure server-side revalidation of an offer snapshot.

        Enforces tenant authorization, resolves the immutable OfferSnapshot,
        calls the decoupled provider adapter using the internal provider_offer_id,
        records the RevalidationResult, and creates a successor snapshot if the fare changed.
        """
        store = SearchSessionStore()
        snapshot = store.get_snapshot(snapshot_id, authenticated_user_id)
        if not snapshot:
            raise PermissionError(
                f"Access denied: Snapshot '{snapshot_id}' does not exist or does not belong to the user."
            )

        if snapshot.offer_type == "flight":
            adapter = DuffelFlightAdapter()
            result = await adapter.revalidate_flight(
                provider_offer_id=snapshot.provider_offer_id,
                expected_price=float(snapshot.price_total),
                expected_currency=snapshot.currency,
            )
        else:
            adapter = LiteAPIHotelAdapter()
            result = await adapter.revalidate_hotel(
                provider_offer_id=snapshot.provider_offer_id,
                expected_price=float(snapshot.price_total),
                expected_currency=snapshot.currency,
            )

        # Set original snapshot ID on the result
        result = result.model_copy(update={"original_snapshot_id": snapshot.snapshot_id})

        successor = None
        if result.price_changed or result.schedule_changed:
            # Create a successor snapshot linking to the predecessor
            new_id = uuid.uuid4()
            updated_offer = snapshot.normalized_offer.model_copy(
                update={
                    "snapshot_id": new_id,
                    "price": snapshot.normalized_offer.price.model_copy(
                        update={"total_amount": result.current_price}
                    ),
                }
            )
            successor = store.save_snapshot(
                session_id=snapshot.session_id,
                offer=updated_offer,
                raw_payload={"revalidated_at": result.verified_at.isoformat(), "details": result.details},
                predecessor_snapshot_id=snapshot.snapshot_id,
            )
            result = result.model_copy(update={"subsequent_snapshot_id": successor.snapshot_id})

        store.record_revalidation(snapshot.snapshot_id, result, successor_snapshot=successor)
        return result
