"""Deterministic Research Intent Router for TrustTrip Phase 8.

Classifies incoming queries into deterministic research intents or live-shopping handoff.
Never delegates privileged tool selection to an LLM.
"""
from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from domain.research import ResearchIntent, ResearchMode


class IntentDecision(BaseModel):
    """Result of deterministic intent classification."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: ResearchIntent
    confidence: float = Field(ge=0.0, le=1.0)
    matched_rules: list[str] = Field(default_factory=list)
    requires_live_handoff: bool = False
    suggested_mode: ResearchMode = ResearchMode.AUTO
    clarification_prompt: str | None = None


# Regex patterns for deterministic classification
_LIVE_SHOPPING_PATTERNS = [
    r"\b(book|buy|purchase|reserve)\b.*\b(flight|ticket|seat|fare)\b",
    r"\b(cheapest|lowest|best)\b.*\b(fare|price|ticket|flight)\b",
    r"\b(fares?|prices?|tickets?)\s+(from|to|between|under|\$|inr|usd|eur|gbp)\b",
    r"\b(how much (is|are|does it cost)|cost of (a )?flight)\b",
    r"\bflights?\s+(available|availability|schedule|departing|flying)\s+on\b",
    r"\bfind\s+(me\s+)?(a\s+)?flights?\b",
    r"\bshow\s+(me\s+)?(available\s+)?flights?\b",
]

_POLICY_PATTERNS = [
    r"\b(baggage|luggage|carry-on|checked bag)\s+(allowance|policy|rules?|limits?|fees?)\b",
    r"\b(cancellation|refund|change|reschedule)\s+(policy|rules?|fee|terms)\b",
    r"\b(visa|passport|entry requirements?|transit visa|customs|immigration)\b",
    r"\b(pet|animal|service dog|infant|lap child)\s+policy\b",
    r"\b(covid|vaccin(e|ation)|health requirements?)\b",
]

_AIRLINE_PATTERNS = [
    r"\b(airline|carrier|fleet|aircraft|boeing|airbus|livery)\b",
    r"\b(cabin class|business class|first class|economy|seat pitch|legroom|in-flight entertainment|wifi onboard)\b",
    r"\b(delta|united|american airlines|emirates|qatar|british airways|singapore airlines|lufthansa|air india|indigo)\b",
]

_AIRPORT_PATTERNS = [
    r"\b(airport|terminal|concourse|gate|lounge|transit|layover)\b",
    r"\b(heathrow|jfk|lax|changi|dubai international|frankfurt|delhi airport|bom|lhr|cdg)\b",
    r"\b(duty free|security wait|customs clearance time|airport transfer|connection time)\b",
]

_ROUTE_PATTERNS = [
    r"\b(direct flight|non-stop|layover in|stopover|connecting flight|flight duration|flight time|distance)\b",
    r"\b(which airlines fly|airlines operating|routes between|who flies)\b",
]

_DESTINATION_PATTERNS = [
    r"\b(best time to visit|weather in|climate in|rainy season|peak season|off-peak|attractions in|culture in|safety in|tipping in)\b",
    r"\b(what to pack|local customs|currency in|getting around|neighborhoods in)\b",
]

_ADVISORY_PATTERNS = [
    r"\b(travel advisory|travel alert|travel warning|is it safe to travel|state department advisory|foreign office advice)\b",
    r"\b(safety warning|civil unrest|strike advisory|weather warning)\b",
]

_DOCUMENT_PATTERNS = [
    r"\b(in (my|the|this|our) (doc|document|pdf|file|upload|handbook|itinerary document|notes))\b",
    r"\b(according to (my|the) (doc|document|pdf|file|upload))\b",
    r"\b(what does the (uploaded\s+)?(doc|document|pdf|file) say)\b",
    r"\b(uploaded (doc|document|pdf|file))\b",
    r"\b(from (the|my) (doc|document|pdf|file))\b",
]


class ResearchIntentRouter:
    """Classifies user queries into research intents or live-shopping handoffs."""

    def classify(
        self,
        query: str,
        has_documents: bool = False,
        explicit_mode: ResearchMode = ResearchMode.AUTO,
        document_ids: list[Any] | None = None,
    ) -> IntentDecision:
        """Deterministically determine research intent."""
        clean_q = query.strip().lower()
        has_docs = bool(has_documents or document_ids)

        if not clean_q:
            return IntentDecision(
                intent=ResearchIntent.AMBIGUOUS_RESEARCH,
                confidence=0.0,
                matched_rules=["empty_query"],
                clarification_prompt="Please provide a travel research question or flight query.",
            )

        # Check ambiguous or vague queries
        tokens = [w for w in re.findall(r"\w+", clean_q) if len(w) > 1]
        if len(tokens) <= 2 and any(w in tokens for w in ("something", "anything", "whatever", "stuff")):
            return IntentDecision(
                intent=ResearchIntent.AMBIGUOUS_RESEARCH,
                confidence=0.30,
                matched_rules=["vague_query"],
                requires_live_handoff=False,
                suggested_mode=ResearchMode.AUTO,
                clarification_prompt="Could you clarify your travel research question? For live bookings, specify your departure and destination dates.",
            )

        # 1. Check live flight shopping signals first (hard boundary)
        live_matches = [p for p in _LIVE_SHOPPING_PATTERNS if re.search(p, clean_q)]
        # However, if query explicitly asks about policy or baggage even if containing flight word
        policy_matches = [p for p in _POLICY_PATTERNS if re.search(p, clean_q)]

        if live_matches and not policy_matches:
            return IntentDecision(
                intent=ResearchIntent.LIVE_FLIGHT_SEARCH,
                confidence=0.95,
                matched_rules=live_matches,
                requires_live_handoff=True,
                suggested_mode=ResearchMode.AUTO,
            )

        # 2. Check document/RAG references
        doc_matches = [p for p in _DOCUMENT_PATTERNS if re.search(p, clean_q)]
        has_doc_intent = bool(doc_matches) or (has_docs and explicit_mode in (ResearchMode.RAG, ResearchMode.HYBRID))

        # Check domain-specific research patterns
        advisory_matches = [p for p in _ADVISORY_PATTERNS if re.search(p, clean_q)]
        airport_matches = [p for p in _AIRPORT_PATTERNS if re.search(p, clean_q)]
        airline_matches = [p for p in _AIRLINE_PATTERNS if re.search(p, clean_q)]
        route_matches = [p for p in _ROUTE_PATTERNS if re.search(p, clean_q)]
        dest_matches = [p for p in _DESTINATION_PATTERNS if re.search(p, clean_q)]

        has_web_intent = bool(
            policy_matches or advisory_matches or airport_matches or
            airline_matches or route_matches or dest_matches or
            explicit_mode == ResearchMode.WEB
        )

        # Combined Web + RAG (documents present + research topic or explicit hybrid)
        if (has_docs and has_web_intent) or (has_doc_intent and has_web_intent) or (has_docs and explicit_mode == ResearchMode.HYBRID):
            return IntentDecision(
                intent=ResearchIntent.COMBINED_WEB_RAG_RESEARCH,
                confidence=0.90,
                matched_rules=(doc_matches + policy_matches + advisory_matches)[:5] or ["documents_attached"],
                requires_live_handoff=False,
                suggested_mode=ResearchMode.HYBRID,
            )

        if doc_matches or (has_doc_intent and (explicit_mode == ResearchMode.RAG or (has_docs and not has_web_intent))):
            return IntentDecision(
                intent=ResearchIntent.DOCUMENT_RAG_RESEARCH,
                confidence=0.90,
                matched_rules=doc_matches or ["document_attached"],
                requires_live_handoff=False,
                suggested_mode=ResearchMode.RAG,
            )

        if policy_matches:
            return IntentDecision(
                intent=ResearchIntent.POLICY_RESEARCH,
                confidence=0.90,
                matched_rules=policy_matches,
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        if advisory_matches:
            return IntentDecision(
                intent=ResearchIntent.TRAVEL_ADVISORY_RESEARCH,
                confidence=0.90,
                matched_rules=advisory_matches,
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        if airport_matches:
            return IntentDecision(
                intent=ResearchIntent.AIRPORT_RESEARCH,
                confidence=0.85,
                matched_rules=airport_matches,
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        if airline_matches:
            return IntentDecision(
                intent=ResearchIntent.AIRLINE_RESEARCH,
                confidence=0.85,
                matched_rules=airline_matches,
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        if route_matches:
            return IntentDecision(
                intent=ResearchIntent.ROUTE_RESEARCH,
                confidence=0.85,
                matched_rules=route_matches,
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        if dest_matches:
            return IntentDecision(
                intent=ResearchIntent.DESTINATION_RESEARCH,
                confidence=0.85,
                matched_rules=dest_matches,
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        # Fallback to general flight research if mentions flights, flying, travel
        if re.search(r"\b(flights?|fly|flying|plane|airlines?|travel|trip)\b", clean_q):
            return IntentDecision(
                intent=ResearchIntent.GLOBAL_FLIGHT_RESEARCH,
                confidence=0.75,
                matched_rules=["general_flight_keyword"],
                requires_live_handoff=False,
                suggested_mode=ResearchMode.WEB,
            )

        # Otherwise ambiguous
        return IntentDecision(
            intent=ResearchIntent.AMBIGUOUS_RESEARCH,
            confidence=0.30,
            matched_rules=["no_confident_match"],
            requires_live_handoff=False,
            suggested_mode=ResearchMode.AUTO,
            clarification_prompt="Could you clarify your travel research question? For live bookings, specify your departure and destination dates.",
        )
