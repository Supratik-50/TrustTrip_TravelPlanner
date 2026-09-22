"""Application Use Case Layer for Trust Trip Universal Planner.

Orchestrates intent routing, canonical state resolution, thread lifecycle,
and delegating to LangGraph without polluting API routes or low-level agents.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from domain.changes import apply_trip_change_set
from domain.compat import build_trip_request, trip_request_to_legacy
from domain.research import TripIntent
from domain.trip import TripRequest
from services.intent_router import IntentDecision, IntentRouter
from services.travel_planner import parse_trip_request
from services.trip_store import TripStore

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

        # 1. Authorize thread and trip ownership
        if authenticated_user_id:
            if thread_id:
                if not self.trip_store.verify_thread_ownership(thread_id, authenticated_user_id):
                    raise PermissionError(
                        f"Access denied: Thread '{thread_id}' does not belong to the authenticated user."
                    )
            else:
                created = self.trip_store.create_thread(
                    user_id=authenticated_user_id,
                    trip_id=trip_id,
                )
                thread_id = str(created["id"])
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
        if decision.intent == TripIntent.NEW_TRIP or not existing_trip:
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
                # Ambiguous refinement: re-parse with existing legacy projection as fallback
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

        # 9. Serialize result including intent metadata
        serialized = _serialize_result(result, thread_id)
        if not serialized.get("final_response") and serialized.get("answer"):
            serialized["final_response"] = serialized["answer"]
        serialized["intent"] = decision.intent.value
        serialized["intent_decision"] = decision.model_dump(mode="json")
        serialized["trip_request"] = canonical_trip.model_dump(mode="json")
        return serialized
