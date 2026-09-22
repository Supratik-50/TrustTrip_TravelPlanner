"""Intent Router for Trust Trip Universal Planner.

Determines the user's intent purely as a decision layer without executing
planning, provider searches, or database mutations.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from domain.budget import BudgetConstraints, BudgetMode
from domain.changes import TripChangeSet
from domain.passenger import PassengerComposition
from domain.research import TripIntent
from domain.trip import Location, TripRequest


class IntentDecision(BaseModel):
    """The outcome of intent routing.

    Attributes:
        intent: Canonical trip intent (NEW_TRIP, FOLLOW_UP, GLOBAL_FLIGHT_RESEARCH, BOOKING_OPERATION).
        confidence: Deterministic heuristic routing strength score (0.0 to 1.0) based on
            rule specificity and signal clarity. This is NOT a statistically calibrated probability.
            - 0.95: High-certainty exact phrase (booking handoff, unseeded fresh trip).
            - 0.90: Domain-specific flight information pattern.
            - 0.88: Structured field delta extracted into TripChangeSet.
            - 0.85: Independent standalone route declaration inside an active thread.
            - 0.60: Fallback continuation for ambiguous queries in an active thread.
        source: Rule or heuristic classifier that produced this decision.
        reason: Human-readable explanation of why this intent was selected.
        changes: Typed TripChangeSet if this decision modifies an existing canonical trip.
    """
    intent: TripIntent
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Heuristic routing strength score, not a calibrated statistical probability.",
    )
    source: str
    reason: str
    changes: TripChangeSet | None = None


class IntentRouter:
    """Classifies user travel requests into canonical trip intents."""

    # Explicit booking phrases (boundary only in Phase 3)
    BOOKING_PHRASES = (
        "book this flight", "book the flight", "book flight",
        "book this hotel", "book the hotel", "book hotel",
        "proceed to payment", "proceed to checkout", "checkout",
        "confirm booking", "confirm my booking", "buy tickets",
        "pay now", "make payment", "book now",
    )

    # Informational global research phrases (boundary only in Phase 3)
    GLOBAL_RESEARCH_PHRASES = (
        "global flight", "flight information", "international flight information",
        "how do flight connections work", "baggage rules", "baggage allowance",
        "transit visa requirements", "airline hubs", "how international flight planning works",
    )

    # Modification cue prefixes and keywords
    MODIFICATION_CUES = (
        "change destination", "change the destination", "change origin", "change the origin",
        "change it", "change the", "make it", "make this", "keep the", "instead",
        "add a", "add an", "remove the", "remove", "replace the", "replace",
        "cheaper", "more affordable", "increase my budget", "increase budget",
        "reduce the cost", "lower the cost", "change my budget", "change budget",
        "revise", "modify", "update", "same trip", "same route", "same plan",
        "continue", "next month", "move the trip", "postpone", "add adults",
        "add children", "add kids", "traveling with", "change passengers",
    )

    def classify_intent(
        self,
        query: str,
        existing_trip: TripRequest | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> IntentDecision:
        clean_q = " ".join(str(query).strip().split())
        low = clean_q.lower()

        # 1. BOOKING_OPERATION boundary check
        if self._is_booking_request(low):
            return IntentDecision(
                intent=TripIntent.BOOKING_OPERATION,
                confidence=0.95,
                source="rule:booking_phrases",
                reason="User query explicitly requests checkout, booking, or payment handoff.",
                changes=None,
            )

        # 2. GLOBAL_FLIGHT_RESEARCH boundary check
        if self._is_global_research_request(low):
            return IntentDecision(
                intent=TripIntent.GLOBAL_FLIGHT_RESEARCH,
                confidence=0.90,
                source="rule:global_flight_research",
                reason="User query requests general flight guidance or airline policies without a specific trip booking route.",
                changes=None,
            )

        # 3. If no existing trip context, this must be a NEW_TRIP
        if not existing_trip or not (existing_trip.origin or existing_trip.destination):
            return IntentDecision(
                intent=TripIntent.NEW_TRIP,
                confidence=0.95,
                source="context:no_existing_trip",
                reason="No active canonical trip exists in the current session.",
                changes=None,
            )

        # 4. If existing trip exists, determine whether request is a FOLLOW_UP or an independent NEW_TRIP
        change_set = self._extract_follow_up_changes(clean_q, existing_trip)
        if change_set is not None:
            return IntentDecision(
                intent=TripIntent.FOLLOW_UP,
                confidence=0.88,
                source="context:change_extraction",
                reason=(
                    f"User modifies existing trip {existing_trip.trip_id} "
                    f"(fields: {', '.join(change_set.changed_fields)})."
                ),
                changes=change_set,
            )

        # 5. Check if the user query contains a full standalone route or independent trip declaration
        if self._is_independent_trip_declaration(low):
            return IntentDecision(
                intent=TripIntent.NEW_TRIP,
                confidence=0.85,
                source="rule:independent_route_detected",
                reason="User specified a new standalone trip route or destination without continuation cues.",
                changes=None,
            )

        # 6. Default fallback: when active trip exists and query is ambiguous, treat as refinement/follow-up
        return IntentDecision(
            intent=TripIntent.FOLLOW_UP,
            confidence=0.60,
            source="fallback:existing_trip_continuation",
            reason="Ambiguous query inside active trip thread; defaulting to continuation to preserve context.",
            changes=TripChangeSet(
                changed_fields=[],
                requires_itinerary_regeneration=True,
            ),
        )

    def _is_booking_request(self, low: str) -> bool:
        return any(phrase in low for phrase in self.BOOKING_PHRASES)

    def _is_global_research_request(self, low: str) -> bool:
        has_research_phrase = any(phrase in low for phrase in self.GLOBAL_RESEARCH_PHRASES)
        has_specific_route = bool(re.search(r"\bfrom\s+[a-z]{2,}\s+to\s+[a-z]{2,}\b", low))
        has_planning_cue = any(word in low for word in ("plan a trip", "plan my trip", "holiday in", "vacation to"))
        return has_research_phrase and not has_specific_route and not has_planning_cue

    def _is_independent_trip_declaration(self, low: str) -> bool:
        """Detect if the query defines a brand-new complete trip."""
        # Phrases like "new trip", "start over", "another trip"
        if any(w in low for w in ("new trip", "start over", "another trip", "different trip", "separate trip")):
            return True
        # Explicit full route "from A to B"
        if re.search(r"\bfrom\s+[a-z]{2,}\s+to\s+[a-z]{2,}\b", low):
            # If not explicitly saying "change origin to X and destination to Y"
            if not any(cue in low for cue in ("change", "instead", "same trip")):
                return True
        # Standalone destination pattern "X trip for N days" without change cues
        if re.search(r"\b(?:plan\s+)?(?:\d+\s+days?\s+)?([a-z]+)\s+(?:trip|tour|holiday|vacation)\b", low):
            if not any(cue in low for cue in self.MODIFICATION_CUES):
                return True
        return False

    def _extract_follow_up_changes(
        self,
        query: str,
        existing: TripRequest,
    ) -> TripChangeSet | None:
        """Inspect query to extract discrete modifications to existing canonical state."""
        from services.travel_planner import _find_budget, _find_duration, _find_passengers, _find_route, _resolve_place

        low = query.lower()
        has_mod_cue = any(cue in low for cue in self.MODIFICATION_CUES)

        changed_fields: list[str] = []
        old_values: dict[str, Any] = {}
        new_values: dict[str, Any] = {}
        affected_agents: list[str] = []

        # A. Destination change: "Change destination to Tokyo" / "destination to Paris" / "to Tokyo instead"
        dest_match = re.search(
            r"(?:change|set|make|update)\s+(?:the\s+)?destination\s+(?:to|as)?\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+(?:and|with|for|budget)\b|$)",
            low,
        )
        if not dest_match:
            dest_match = re.search(r"\b(?:fly|go|travel)\s+to\s+([A-Za-z][A-Za-z .'-]+?)\s+instead\b", low)
        if not dest_match:
            dest_match = re.search(r"\b(?:destination\s+to)\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+(?:and|with|for|budget)\b|$)", low)

        if dest_match:
            dest_raw = dest_match.group(1).strip(" .,;:-")
            code, city, country = _resolve_place(dest_raw)
            new_dest = Location(
                query=dest_raw,
                city=city or dest_raw.title(),
                country=country,
                iata_code=code,
            )
            changed_fields.append("destination")
            old_values["destination"] = existing.destination.model_dump() if existing.destination else None
            new_values["destination"] = new_dest
            affected_agents.extend(["flight_agent", "hotel_agent", "weather_agent", "itinerary_agent"])

        # B. Origin change: "Change origin to Delhi" / "fly from Mumbai"
        origin_match = re.search(
            r"(?:change|set|make|update)\s+(?:the\s+)?origin\s+(?:to|as|from)?\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+(?:and|with|for|budget)\b|$)",
            low,
        )
        if not origin_match:
            origin_match = re.search(r"\b(?:fly|depart|leave)\s+from\s+([A-Za-z][A-Za-z .'-]+?)\s+instead\b", low)
        if not origin_match:
            origin_match = re.search(r"\b(?:origin\s+to|origin\s+from)\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+(?:and|with|for|budget)\b|$)", low)

        if origin_match:
            orig_raw = origin_match.group(1).strip(" .,;:-")
            code, city, country = _resolve_place(orig_raw)
            new_orig = Location(
                query=orig_raw,
                city=city or orig_raw.title(),
                country=country,
                iata_code=code,
            )
            changed_fields.append("origin")
            old_values["origin"] = existing.origin.model_dump() if existing.origin else None
            new_values["origin"] = new_orig
            affected_agents.extend(["flight_agent", "budget_agent"])

        # C. Budget change: "Increase budget to 250000" / "make it cheaper"
        budget_val = _find_budget(query)
        cheaper = any(p in low for p in ("cheaper", "more affordable", "lower the cost", "reduce the cost", "save money"))
        if budget_val is not None or cheaper:
            changed_fields.append("budget")
            old_values["budget"] = existing.budget.model_dump()
            new_amount = Decimal(str(budget_val)) if budget_val is not None else existing.budget.amount
            new_mode = BudgetMode.CHEAPER if cheaper else existing.budget.mode
            new_values["budget"] = BudgetConstraints(
                amount=new_amount,
                currency=existing.budget.currency,
                mode=new_mode,
            )
            affected_agents.extend(["flight_agent", "hotel_agent", "budget_agent", "itinerary_agent"])

        # D. Passenger change: "Add two adults" / "for 4 people" / "with 2 kids"
        passengers_data = _find_passengers(query, {
            "adults": existing.passengers.adults,
            "children": existing.passengers.children,
            "infants": existing.passengers.infants,
        })
        # Check if query actually mentions passengers
        has_passenger_mention = bool(re.search(r"\b(?:adult|adults|child|children|kid|kids|infant|infants|people|persons|travellers|travelers)\b", low))
        if has_passenger_mention:
            # Check relative addition e.g. "add 2 adults"
            add_match = re.search(r"\badd\s+(\d+)\s+(adult|adults|child|children|kid|kids|infant|infants)\b", low)
            if add_match:
                count = int(add_match.group(1))
                kind = add_match.group(2)
                curr_adults = existing.passengers.adults
                curr_children = existing.passengers.children
                curr_infants = existing.passengers.infants
                if "adult" in kind:
                    curr_adults += count
                elif "infant" in kind:
                    curr_infants += count
                else:
                    curr_children += count
                new_pass = PassengerComposition(adults=curr_adults, children=curr_children, infants=curr_infants)
            else:
                new_pass = PassengerComposition(
                    adults=passengers_data["adults"],
                    children=passengers_data["children"],
                    infants=passengers_data["infants"],
                )
            changed_fields.append("passengers")
            old_values["passengers"] = existing.passengers.model_dump()
            new_values["passengers"] = new_pass
            affected_agents.extend(["flight_agent", "hotel_agent", "budget_agent"])

        # E. Duration change: "make it 7 days" / "change duration to 4 nights"
        dur_val = _find_duration(query)
        if dur_val is not None and any(cue in low for cue in ("make it", "change", "set", "for", "duration")):
            changed_fields.append("duration_days")
            old_values["duration_days"] = existing.duration_days
            new_values["duration_days"] = dur_val
            affected_agents.extend(["hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"])

        # F. Style / Pace changes
        if any(w in low for w in ("relaxed", "luxury", "budget", "fast-paced", "adventure", "cultural")):
            for style in ("relaxed", "luxury", "budget", "adventure", "cultural"):
                if style in low:
                    changed_fields.append("travel_preferences")
                    old_values["travel_preferences"] = existing.travel_preferences.model_dump()
                    new_values["travel_preferences"] = existing.travel_preferences.model_copy(update={"style": style})
                    affected_agents.append("itinerary_agent")
                    break

        if changed_fields or has_mod_cue:
            return TripChangeSet(
                changed_fields=changed_fields,
                old_values=old_values,
                new_values=new_values,
                affected_agents=list(dict.fromkeys(affected_agents)),
                requires_live_search=any(a in affected_agents for a in ("flight_agent", "hotel_agent")),
                requires_itinerary_regeneration=True,
            )

        return None
