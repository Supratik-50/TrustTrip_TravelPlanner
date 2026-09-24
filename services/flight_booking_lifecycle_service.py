"""Flight Booking Lifecycle Service.

Orchestrates:
- Tenant authorization & parent trip row locking
- Authoritative revision binding & HITL invalidation
- Deep pre-handoff revalidation against FlightBookingProviderPort
- Immutable successor OfferSnapshot generation (with predecessor_snapshot_id)
- Idempotency management via booking_attempts table
- State machine transition enforcement and audit logging in booking_transitions
- Duffel Links hosted checkout generation & zero-URL-fabrication security
- Authenticated provider completion webhooks
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import logging
import os
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
load_dotenv()

import psycopg
from psycopg.rows import dict_row

from domain.booking_lifecycle import (
    BookingIntent,
    BookingMode,
    BookingProvider,
    BookingStatus,
    BookingTransition,
    BookingCompletedException,
    BookingExpiredException,
    BookingInProgressException,
    BookingLockedException,
    BookingProviderRateLimited,
    BookingProviderTimeout,
    BookingProviderUnavailable,
    FareChangedException,
    FlightHandoffDestinationResponse,
    IdempotencyConflictException,
    IdempotencyKeyRequiredException,
    InvalidBookingStateTransitionError,
    InvalidCallbackException,
    OfferChangedException,
    OfferDataInvalidException,
    OfferExpiredException,
    OfferRevalidationResult,
    OfferUnavailableException,
    OwnershipViolationException,
    PassengerBookingModel,
    SandboxBalancePaymentModel,
    StaleRevisionException,
    UnauthorizedCallbackException,
    compute_passenger_hash,
    validate_booking_transition,
    validate_idempotency_key,
)
from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    FreshnessStatus,
    OfferSnapshot,
    PriceBreakdown,
    RevalidationStatus,
)
from services.booking_provider_port import FlightBookingProviderPort, ProviderOfferVerification
from services.duffel_booking_adapter import DuffelBookingAdapter
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import (
    CARRIER_DOMAIN_ALLOWLIST,
    _is_approved_carrier_host,
    _is_approved_duffel_host,
    _validate_and_normalize_url,
)
from services.trip_store import TripStore, resolve_current_revision

logger = logging.getLogger(__name__)


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


def _sanitize_payload_for_persistence(payload: Any) -> Any:
    """Scrub sensitive card/PII attributes from persistence payloads."""
    if isinstance(payload, dict):
        scrubbed = {}
        for k, v in payload.items():
            k_lower = str(k).lower()
            if any(term in k_lower for term in ("card", "pan", "cvv", "cvc", "security_code", "secret", "token", "password", "authorization")):
                scrubbed[k] = "[REDACTED]"
            else:
                scrubbed[k] = _sanitize_payload_for_persistence(v)
        return scrubbed
    elif isinstance(payload, list):
        return [_sanitize_payload_for_persistence(item) for item in payload]
    elif isinstance(payload, (UUID, Decimal)):
        return str(payload)
    return payload


def compute_payload_hash(payload: Any) -> str:
    """Compute deterministic SHA256 of normalized request payload."""
    sanitized = _sanitize_payload_for_persistence(payload)
    canonical_json = json.dumps(sanitized or {}, sort_keys=True, default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class FlightBookingLifecycleService:
    """Enterprise-grade service managing flight booking lifecycle."""

    # In-memory repositories for local development and unit tests
    _IN_MEMORY_INTENTS: dict[str, dict[str, Any]] = {}
    _IN_MEMORY_ATTEMPTS: dict[str, dict[str, Any]] = {}
    _IN_MEMORY_TRANSITIONS: list[dict[str, Any]] = []
    _IN_MEMORY_INTENT_LOCKS: dict[str, asyncio.Lock] = {}
    _IN_MEMORY_GLOBAL_LOCK = asyncio.Lock()

    def __init__(
        self,
        provider_port: FlightBookingProviderPort | None = None,
        trip_store: TripStore | None = None,
        snapshot_store: SearchSessionStore | None = None,
    ) -> None:
        self.use_in_memory = (
            os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}
            or not os.getenv("DATABASE_URL")
        )
        self.provider_port = provider_port or DuffelBookingAdapter()
        self.trip_store = trip_store or TripStore()
        self.snapshot_store = snapshot_store or SearchSessionStore()
        self.public_base_url = os.getenv("TRUSTTRIP_PUBLIC_BASE_URL", "https://trusttrip.com").rstrip("/")

    async def _get_intent_lock(self, intent_id: UUID | str) -> asyncio.Lock:
        async with self._IN_MEMORY_GLOBAL_LOCK:
            key = str(intent_id)
            if key not in self._IN_MEMORY_INTENT_LOCKS:
                self._IN_MEMORY_INTENT_LOCKS[key] = asyncio.Lock()
            return self._IN_MEMORY_INTENT_LOCKS[key]

    # ========================================================================
    # IDEMPOTENCY & AUDIT HELPERS
    # ========================================================================

    def _claim_idempotency_attempt(
        self,
        intent_id: UUID | str | None,
        user_id: UUID | str,
        idempotency_key: str,
        action: str,
        request_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim an idempotency key before mutating work."""
        uid_str = str(user_id)
        iid_str = str(intent_id) if intent_id else None
        req_hash = compute_payload_hash(request_payload)
        now = datetime.now(timezone.utc)
        sanitized_req = _sanitize_payload_for_persistence(request_payload)

        if self.use_in_memory:
            key = f"{uid_str}:{idempotency_key}:{action}"
            existing = self._IN_MEMORY_ATTEMPTS.get(key)
            if existing:
                if existing.get("request_hash") and existing.get("request_hash") != req_hash:
                    raise IdempotencyConflictException(
                        "Idempotency key reused with conflicting request payload."
                    )
                if existing.get("status") == "COMPLETED":
                    return existing.get("response_payload")
                if existing.get("status") == "IN_PROGRESS":
                    raise BookingInProgressException(
                        "An operation with this idempotency key is already in progress."
                    )
                if existing.get("status") == "FAILED":
                    if existing.get("response_payload"):
                        return existing.get("response_payload")

            self._IN_MEMORY_ATTEMPTS[key] = {
                "attempt_id": str(uuid4()),
                "intent_id": iid_str,
                "user_id": uid_str,
                "idempotency_key": idempotency_key,
                "action": action,
                "status": "IN_PROGRESS",
                "request_payload": sanitized_req,
                "request_hash": req_hash,
                "response_payload": None,
                "error_code": None,
                "created_at": now,
                "updated_at": now,
            }
            return None

        # PostgreSQL Atomic Serialization
        with psycopg.connect(_dsn()) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO booking_attempts (
                        intent_id, user_id, idempotency_key, action, status,
                        request_payload, request_hash, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, 'IN_PROGRESS', %s::jsonb, %s, %s, %s)
                    ON CONFLICT (user_id, idempotency_key, action) DO NOTHING
                    RETURNING attempt_id, status, request_hash, response_payload, error_code
                    """,
                    (
                        iid_str,
                        uid_str,
                        idempotency_key,
                        action,
                        json.dumps(sanitized_req or {}),
                        req_hash,
                        now,
                        now,
                    ),
                ).fetchone()

                if row:
                    return None

                existing = conn.execute(
                    """
                    SELECT attempt_id, status, request_hash, response_payload, error_code
                    FROM booking_attempts
                    WHERE user_id = %s AND idempotency_key = %s AND action = %s
                    FOR UPDATE
                    """,
                    (uid_str, idempotency_key, action),
                ).fetchone()

                if not existing:
                    return None

                if existing["request_hash"] and existing["request_hash"] != req_hash:
                    raise IdempotencyConflictException(
                        "Idempotency key reused with conflicting request payload."
                    )

                if existing["status"] == "COMPLETED":
                    return existing["response_payload"]

                if existing["status"] == "IN_PROGRESS":
                    raise BookingInProgressException(
                        "An operation with this idempotency key is already in progress."
                    )

                if existing["status"] == "FAILED":
                    if existing["response_payload"]:
                        return existing["response_payload"]

                return None

    def _record_attempt_completion(
        self,
        user_id: UUID | str,
        idempotency_key: str,
        action: str,
        response_payload: dict[str, Any],
        error_code: str | None = None,
    ) -> None:
        uid_str = str(user_id)
        status = "FAILED" if error_code else "COMPLETED"
        now = datetime.now(timezone.utc)
        sanitized_res = _sanitize_payload_for_persistence(response_payload)

        if self.use_in_memory:
            key = f"{uid_str}:{idempotency_key}:{action}"
            if key not in self._IN_MEMORY_ATTEMPTS:
                self._IN_MEMORY_ATTEMPTS[key] = {
                    "attempt_id": str(uuid4()),
                    "intent_id": None,
                    "user_id": uid_str,
                    "idempotency_key": idempotency_key,
                    "action": action,
                    "status": status,
                    "request_payload": None,
                    "request_hash": None,
                    "response_payload": sanitized_res,
                    "error_code": error_code,
                    "created_at": now,
                    "updated_at": now,
                }
            else:
                self._IN_MEMORY_ATTEMPTS[key]["status"] = status
                self._IN_MEMORY_ATTEMPTS[key]["response_payload"] = sanitized_res
                self._IN_MEMORY_ATTEMPTS[key]["error_code"] = error_code
                self._IN_MEMORY_ATTEMPTS[key]["updated_at"] = now
            return

        with psycopg.connect(_dsn(), autocommit=True) as conn:
            res = conn.execute(
                """
                UPDATE booking_attempts
                SET status = %s, response_payload = %s::jsonb, error_code = %s, updated_at = %s
                WHERE user_id = %s AND idempotency_key = %s AND action = %s
                """,
                (status, json.dumps(sanitized_res), error_code, now, uid_str, idempotency_key, action),
            )
            if res.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO booking_attempts (
                        user_id, idempotency_key, action, status, response_payload, error_code, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (user_id, idempotency_key, action)
                    DO UPDATE SET status = EXCLUDED.status, response_payload = EXCLUDED.response_payload, error_code = EXCLUDED.error_code, updated_at = EXCLUDED.updated_at
                    """,
                    (uid_str, idempotency_key, action, status, json.dumps(sanitized_res), error_code, now, now),
                )

    def _record_transition(
        self,
        intent_id: UUID | str,
        from_status: BookingStatus,
        to_status: BookingStatus,
        trigger: str,
        actor_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        record = {
            "transition_id": uuid4(),
            "intent_id": UUID(str(intent_id)),
            "from_status": from_status,
            "to_status": to_status,
            "trigger": trigger,
            "actor_id": actor_id,
            "reason": reason,
            "metadata": metadata or {},
            "timestamp": now,
        }
        if self.use_in_memory:
            self._IN_MEMORY_TRANSITIONS.append(record)
            return

        with psycopg.connect(_dsn(), autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO booking_transitions (
                    intent_id, from_status, to_status, trigger, actor_id, reason, metadata, created_at
                )
                VALUES (%s, %s::booking_status_enum, %s::booking_status_enum, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    str(intent_id),
                    from_status.value,
                    to_status.value,
                    trigger,
                    actor_id,
                    reason,
                    json.dumps(metadata or {}),
                    now,
                ),
            )

    @staticmethod
    def _extract_included_baggage(source: Any) -> list[dict[str, Any]] | None:
        """Extract and normalize included baggage from verification or raw snapshot payload.

        Returns None if baggage information is legitimately unavailable from provider.
        """
        if source is None:
            return None
        # 1. Direct structured included_baggage attribute
        inc_bag = getattr(source, "included_baggage", None)
        if inc_bag is not None and isinstance(inc_bag, list):
            return sorted(
                [{"type": str(b.get("type", "")).lower().strip(), "quantity": int(b.get("quantity", 1))}
                 for b in inc_bag if isinstance(b, dict) and b.get("type")],
                key=lambda x: (x["type"], x["quantity"]),
            )
        # 2. Check if source is a dict with 'included_baggage'
        if isinstance(source, dict) and isinstance(source.get("included_baggage"), list):
            return sorted(
                [{"type": str(b.get("type", "")).lower().strip(), "quantity": int(b.get("quantity", 1))}
                 for b in source["included_baggage"] if isinstance(b, dict) and b.get("type")],
                key=lambda x: (x["type"], x["quantity"]),
            )
        # 3. Traverse Duffel slices[].segments[].passengers[].baggages[]
        slices = getattr(source, "slices", None) or (source.get("slices") if isinstance(source, dict) else None)
        if not slices and hasattr(source, "raw_offer") and isinstance(source.raw_offer, dict):
            slices = source.raw_offer.get("slices")
        if slices and isinstance(slices, list):
            all_bags = []
            has_bag_field = False
            for sl in slices:
                for seg in (sl.get("segments") or []) if isinstance(sl, dict) else []:
                    for pax in (seg.get("passengers") or []) if isinstance(seg, dict) else []:
                        if isinstance(pax, dict) and "baggages" in pax:
                            has_bag_field = True
                            for b in pax.get("baggages") or []:
                                if isinstance(b, dict) and b.get("type"):
                                    all_bags.append({
                                        "type": str(b.get("type", "")).lower().strip(),
                                        "quantity": int(b.get("quantity", 1)),
                                    })
            if has_bag_field:
                return sorted(all_bags, key=lambda x: (x["type"], x["quantity"]))
        return None

    @staticmethod
    def _extract_conditions(source: Any) -> dict[str, Any] | None:
        """Extract and normalize fare conditions strictly from structured provider data.

        Accepts only:
        - source.conditions
        - source["conditions"]
        - source.raw_offer["conditions"]

        Normalizes change_before_departure and refund_before_departure with
        allowed, penalty_amount, penalty_currency.

        Returns None if conditions are legitimately null / unavailable.
        Textual summaries or keyword inference are strictly forbidden.
        """
        if source is None:
            return None
        cond = getattr(source, "conditions", None) or (source.get("conditions") if isinstance(source, dict) else None)
        if not cond and hasattr(source, "raw_offer") and isinstance(source.raw_offer, dict):
            cond = source.raw_offer.get("conditions")
        if isinstance(cond, dict):
            change = cond.get("change_before_departure")
            refund = cond.get("refund_before_departure")
            if change is None and refund is None:
                return None
            def _norm(c: Any) -> dict[str, Any] | None:
                if not isinstance(c, dict):
                    return None
                return {
                    "allowed": bool(c.get("allowed", False)),
                    "penalty_amount": str(c.get("penalty_amount")) if c.get("penalty_amount") is not None else None,
                    "penalty_currency": str(c.get("penalty_currency")).upper() if c.get("penalty_currency") else None,
                }
            norm_change = _norm(change)
            norm_refund = _norm(refund)
            if norm_change is None and norm_refund is None:
                return None
            return {
                "change_before_departure": norm_change,
                "refund_before_departure": norm_refund,
            }
        return None

    @staticmethod
    def _compare_passenger_composition(
        baseline_pax: list[Any] | None,
        live_pax: list[Any] | None,
    ) -> list[str]:
        """Verify passenger composition without assuming airline type string matches TrustTrip request exactly."""
        divergent: list[str] = []
        if baseline_pax is None and live_pax is None:
            return divergent
        if baseline_pax is not None and live_pax is None:
            raise OfferDataInvalidException("Provider verification response missing required passenger details.")
        if baseline_pax is None and live_pax is not None:
            return divergent

        if len(baseline_pax) != len(live_pax):
            divergent.append("passenger_count")
            return divergent

        from collections import Counter
        def _get_types(p_list):
            res = []
            for p in p_list:
                if isinstance(p, dict):
                    t = p.get("type") or ("adult" if p.get("born_on") or p.get("age", 20) >= 12 else "child")
                else:
                    t = getattr(p, "type", "adult")
                res.append(str(t).lower().strip())
            return Counter(res)

        base_types = _get_types(baseline_pax)
        live_types = _get_types(live_pax)
        if base_types != live_types:
            divergent.append("passenger_composition")
        return divergent

    def _compare_flight_attributes(
        self,
        snapshot: OfferSnapshot,
        verif: ProviderOfferVerification,
    ) -> list[str]:
        """Presence-aware deterministic comparison of all material booking attributes.

        Distinguishes:
        - Case A: Known baseline + known live + different -> OFFER_CHANGED
        - Case B: Known baseline + known live + equal -> unchanged
        - Case C: Both legitimately unavailable -> unchanged (zero fabricated defaults)
        - Case D: Known baseline + live missing -> OFFER_DATA_INVALID
        """
        # 1. Validate Required Core Provider Fields (Fail Closed)
        if not verif or not getattr(verif, "provider_offer_id", None):
            raise OfferDataInvalidException("Provider verification response missing required 'provider_offer_id'.")
        if getattr(verif, "total_amount", None) is None:
            raise OfferDataInvalidException("Provider verification response missing required 'total_amount'.")
        if not getattr(verif, "currency", None) or len(str(verif.currency).strip()) != 3:
            raise OfferDataInvalidException("Provider verification response missing valid 3-letter 'currency'.")

        norm_offer = snapshot.normalized_offer
        first_seg = norm_offer.segments[0] if (norm_offer and norm_offer.segments) else None
        last_seg = norm_offer.segments[-1] if (norm_offer and norm_offer.segments) else None

        if first_seg and not getattr(verif, "origin", None):
            raise OfferDataInvalidException("Provider verification response missing required 'origin'.")
        if last_seg and not getattr(verif, "destination", None):
            raise OfferDataInvalidException("Provider verification response missing required 'destination'.")
        if first_seg and getattr(verif, "departure_time", None) is None:
            raise OfferDataInvalidException("Provider verification response missing required 'departure_time'.")
        if last_seg and getattr(verif, "arrival_time", None) is None:
            raise OfferDataInvalidException("Provider verification response missing required 'arrival_time'.")
        if getattr(verif, "passenger_count", None) is None and norm_offer:
            raise OfferDataInvalidException("Provider verification response missing required 'passenger_count'.")

        divergent: list[str] = []

        # 2. Physical Availability
        available = getattr(verif, "available", True)
        if not available or verif.status == RevalidationStatus.OFFER_WITHDRAWN:
            divergent.append("offer_availability")

        # 3. Provider Offer ID match
        prov_id = getattr(verif, "provider_offer_id", None)
        if prov_id and prov_id != snapshot.provider_offer_id:
            divergent.append("provider_offer_id")

        # 4. Route: Origin & Destination
        if first_seg and verif.origin:
            if verif.origin.strip().upper() != first_seg.origin_iata.strip().upper():
                divergent.append("origin")

        if last_seg and verif.destination:
            if verif.destination.strip().upper() != last_seg.destination_iata.strip().upper():
                divergent.append("destination")

        # 5. Times: Departure & Arrival (deterministic 60s tolerance)
        if first_seg and verif.departure_time:
            s_dep = first_seg.departure_time
            if abs((verif.departure_time - s_dep).total_seconds()) > 60:
                divergent.append("departure_time")

        if last_seg and verif.arrival_time:
            s_arr = last_seg.arrival_time
            if abs((verif.arrival_time - s_arr).total_seconds()) > 60:
                divergent.append("arrival_time")

        # 6. Passenger Composition & Count (FIX 7)
        v_pass_count = getattr(verif, "passenger_count", None)
        if v_pass_count is not None and norm_offer:
            if v_pass_count != norm_offer.passenger_count:
                divergent.append("passenger_count")

        raw = (getattr(self.snapshot_store, "get_raw_payload_internal", None) and self.snapshot_store.get_raw_payload_internal(snapshot.snapshot_id)) or {}
        base_passengers = raw.get("passengers")
        live_passengers = getattr(verif, "passengers", None)
        if base_passengers is not None:
            pax_div = self._compare_passenger_composition(base_passengers, live_passengers)
            divergent.extend(pax_div)

        # 7. Cabin Class (Presence-Aware FIX 8)
        base_cabin = first_seg.cabin_class if (first_seg and getattr(first_seg, "cabin_class", None)) else None
        live_cabin = getattr(verif, "cabin_class", None)
        if base_cabin is not None and live_cabin is not None:
            if base_cabin.strip().lower() != live_cabin.strip().lower():
                divergent.append("cabin_class")
        elif base_cabin is not None and not live_cabin:
            raise OfferDataInvalidException("Provider verification response missing required 'cabin_class'.")

        # 8. Included Baggage (Presence-Aware FIX 5)
        base_baggage = self._extract_included_baggage(raw)
        live_baggage = self._extract_included_baggage(verif)
        if base_baggage is not None and live_baggage is not None:
            if base_baggage != live_baggage:
                divergent.append("baggage")
        elif base_baggage is not None and live_baggage is None:
            raise OfferDataInvalidException("Provider verification response missing previously known included baggage.")
        # If base_baggage is None and live_baggage is not None -> newly available, accepted
        # If both are None -> Case C: legitimately unavailable, zero fabrication, unchanged

        # 9. Fare Conditions (Presence-Aware FIX 6)
        base_conditions = self._extract_conditions(raw)
        live_conditions = self._extract_conditions(verif)
        if base_conditions is not None and live_conditions is not None:
            if base_conditions != live_conditions:
                divergent.append("fare_rules")
        elif base_conditions is not None and live_conditions is None:
            raise OfferDataInvalidException("Provider verification response missing previously known fare conditions.")
        # If base_conditions is None and live_conditions is not None -> newly available, accepted
        # If both are None -> Case C: legitimately unavailable, zero fabrication, unchanged

        # 10. Schedule Changed Flag
        v_sched_changed = getattr(verif, "schedule_changed", False) or getattr(verif, "status", None) == RevalidationStatus.SCHEDULE_CHANGED
        if v_sched_changed:
            if "departure_time" not in divergent and "arrival_time" not in divergent:
                divergent.append("schedule")

        # 11. Price Total
        v_amount = getattr(verif, "total_amount", getattr(verif, "current_price", snapshot.price_total))
        if abs(snapshot.price_total - v_amount) > Decimal("0.01"):
            divergent.append("total_amount")

        # 12. Currency
        v_curr = getattr(verif, "currency", snapshot.currency)
        if v_curr.strip().upper() != snapshot.currency.strip().upper():
            divergent.append("currency")

        return divergent

    # ========================================================================
    # 1. PREPARE BOOKING INTENT
    # ========================================================================

    async def prepare_booking_intent(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        snapshot_id: UUID | str,
        passengers: list[PassengerBookingModel],
        booking_mode: BookingMode = BookingMode.DUFFEL_LINK,
        idempotency_key: str = "",
    ) -> BookingIntent:
        """Create a new BookingIntent bound to trip, tenant, snapshot, and authoritative revision."""
        if not idempotency_key:
            idempotency_key = str(uuid4())
        else:
            idempotency_key = validate_idempotency_key(idempotency_key)
        uid_str = str(user_id)
        tid_str = str(trip_id)
        sid_str = str(snapshot_id)

        req_payload = {
            "trip_id": tid_str,
            "snapshot_id": sid_str,
            "passengers": [p.model_dump() for p in passengers],
            "booking_mode": booking_mode.value,
        }

        cached = self._claim_idempotency_attempt(
            intent_id=None,
            user_id=user_id,
            idempotency_key=idempotency_key,
            action="prepare",
            request_payload=req_payload,
        )
        if cached:
            return BookingIntent.model_validate(cached)

        prep_lock = await self._get_intent_lock(f"prep:{uid_str}:{idempotency_key}")
        async with prep_lock:
            # 1. Authenticate trip ownership
            trip = self.trip_store.get_trip(trip_id, user_id)
            if not trip:
                raise OwnershipViolationException(f"Trip '{tid_str}' not found or access denied.")

            # 2. Resolve authoritative current revision
            current_rev_num = resolve_current_revision(trip)
            if current_rev_num is None:
                raise StaleRevisionException("Authoritative trip revision could not be resolved.")

            rev_id_raw = trip.get("current_revision_id")
            if not rev_id_raw:
                if self.use_in_memory:
                    from services.evidence_store import EvidenceRevisionStore
                    ev = EvidenceRevisionStore()
                    matching = [
                        r for r in ev._IN_MEMORY_REVISIONS.values()
                        if str(r.trip_id) == tid_str and r.revision_number == current_rev_num
                    ]
                    if matching:
                        rev_id_raw = str(matching[0].revision_id)
                else:
                    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
                        row = conn.execute(
                            """
                            SELECT revision_id FROM plan_revisions
                            WHERE trip_id = %s AND revision_number = %s
                            """,
                            (tid_str, current_rev_num),
                        ).fetchone()
                        if row:
                            rev_id_raw = str(row["revision_id"])

            if not rev_id_raw:
                raise StaleRevisionException(
                    f"Trip '{tid_str}' has no persisted plan revision row for revision number {current_rev_num}."
                )

            revision_id = UUID(str(rev_id_raw))

            # 3. Validate snapshot ownership & relevance
            snapshot = self.snapshot_store.get_snapshot(UUID(sid_str), user_id)
            if not snapshot:
                raise OwnershipViolationException(f"Offer snapshot '{sid_str}' not found or access denied.")

            if snapshot.offer_type != "flight":
                raise ValueError(f"Snapshot '{sid_str}' is an offer of type '{snapshot.offer_type}', not 'flight'.")

            session = self.snapshot_store.get_session(snapshot.session_id, user_id)
            if not session or str(session.trip_id) != tid_str:
                raise ValueError("Offer snapshot does not belong to the requested trip.")

            if session.trip_revision != current_rev_num:
                raise StaleRevisionException(
                    f"Snapshot was created for outdated revision {session.trip_revision} (current is {current_rev_num})."
                )

            if snapshot.availability != AvailabilityStatus.AVAILABLE:
                raise OfferUnavailableException("Selected offer is no longer available.")

            # 4. Construct BookingIntent
            intent_id = uuid4()
            now = datetime.now(timezone.utc)
            passenger_hash = compute_passenger_hash(passengers)
            provider_name = snapshot.provider.lower()
            provider = BookingProvider.DUFFEL if provider_name == "duffel" else BookingProvider.TEST_STUB

            first_seg = snapshot.normalized_offer.segments[0] if snapshot.normalized_offer.segments else None
            airline_name = first_seg.marketing_carrier_name if first_seg else "Airline"
            flight_number = f"{first_seg.marketing_carrier_iata}{first_seg.flight_number}" if first_seg else ""

            intent_data = {
                "id": intent_id,
                "user_id": UUID(uid_str),
                "trip_id": UUID(tid_str),
                "revision_id": revision_id,
                "selected_offer_snapshot_id": UUID(sid_str),
                "current_revalidation_snapshot_id": UUID(sid_str),
                "provider": provider,
                "booking_mode": booking_mode,
                "status": BookingStatus.SELECTED,
                "idempotency_key": idempotency_key,
                "provider_reference": None,
                "handoff_url": None,
                "handoff_expires_at": None,
                "passenger_hash": passenger_hash,
                "expected_amount": snapshot.price_total,
                "currency": snapshot.currency,
                "metadata": {
                    "passengers": [p.model_dump() for p in passengers],
                    "airline_name": airline_name,
                    "flight_number": flight_number,
                    "trip_revision": current_rev_num,
                },
                "created_at": now,
                "updated_at": now,
                "expires_at": snapshot.expires_at or (now + timedelta(minutes=30)),
            }

            # 5. Persist
            if self.use_in_memory:
                self._IN_MEMORY_INTENTS[str(intent_id)] = intent_data
            else:
                with psycopg.connect(_dsn(), autocommit=True) as conn:
                    conn.execute(
                        """
                        INSERT INTO booking_intents (
                            id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                            current_revalidation_snapshot_id, provider, booking_mode, status,
                            idempotency_key, passenger_hash, expected_amount, currency,
                            metadata, created_at, updated_at, expires_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s::booking_provider_enum, %s::booking_mode_enum,
                            %s::booking_status_enum, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                        )
                        """,
                        (
                            intent_id,
                            uid_str,
                            tid_str,
                            str(revision_id),
                            sid_str,
                            sid_str,
                            provider.value,
                            booking_mode.value,
                            BookingStatus.SELECTED.value,
                            intent_data["idempotency_key"],
                            passenger_hash,
                            snapshot.price_total,
                            snapshot.currency,
                            json.dumps(intent_data["metadata"]),
                            now,
                            now,
                            intent_data["expires_at"],
                        ),
                    )

            # Audit initial transition
            self._record_transition(
                intent_id=intent_id,
                from_status=BookingStatus.DRAFT,
                to_status=BookingStatus.SELECTED,
                trigger="PREPARE_BOOKING_INTENT",
                actor_id=uid_str,
                reason="Offer selection initialized and bound to revision",
            )

            intent = BookingIntent.model_validate(intent_data)
            self._record_attempt_completion(user_id, idempotency_key, "prepare", intent.model_dump(mode="json"))
            return intent

    # ========================================================================
    # 2. PRE-HANDOFF REVALIDATION GATE
    # ========================================================================

    async def revalidate_booking_intent(
        self,
        user_id: UUID | str,
        intent_id: UUID | str,
        accepted_amount: Decimal | None = None,
        idempotency_key: str = "",
    ) -> OfferRevalidationResult:
        """Revalidate live supplier offer before checkout handoff."""
        if not idempotency_key:
            idempotency_key = str(uuid4())
        else:
            idempotency_key = validate_idempotency_key(idempotency_key)
        uid_str = str(user_id)
        iid_str = str(intent_id)

        cached = self._claim_idempotency_attempt(
            intent_id=intent_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            action="revalidate",
            request_payload={"accepted_amount": str(accepted_amount) if accepted_amount else None},
        )
        if cached:
            return OfferRevalidationResult.model_validate(cached)

        lock = await self._get_intent_lock(intent_id)
        async with lock:
            intent = await self.get_booking_intent(user_id, intent_id)

            # Check revision
            trip = self.trip_store.get_trip(intent.trip_id, user_id)
            if not trip:
                raise OwnershipViolationException("Trip not found.")

            current_rev_num = resolve_current_revision(trip)
            meta = intent.metadata or {}
            intent_rev_num = meta.get("trip_revision")
            rev_mismatch = (intent_rev_num is not None and intent_rev_num != current_rev_num)
            trip_rev_id = trip.get("current_revision_id")
            if trip_rev_id and str(intent.revision_id) != str(trip_rev_id):
                rev_mismatch = True

            if rev_mismatch or intent.status == BookingStatus.EXPIRED:
                if intent.status != BookingStatus.EXPIRED:
                    self._update_intent_status_cas(
                        intent.id, intent.status, BookingStatus.EXPIRED, reason="REVISION_SUPERSEDED"
                    )
                raise StaleRevisionException(
                    f"Trip revision has advanced to {current_rev_num} or intent is expired; current booking intent is stale."
                )

            # Price acknowledgement handling
            current_status = intent.status
            if current_status == BookingStatus.PRICE_CHANGED:
                if accepted_amount is not None and abs(accepted_amount - intent.expected_amount) <= Decimal("0.01"):
                    self._update_intent_status_cas(
                        intent.id, BookingStatus.PRICE_CHANGED, BookingStatus.SELECTED, reason="PRICE_ACCEPTED_BY_USER"
                    )
                    intent = await self.get_booking_intent(user_id, intent_id)
                    current_status = BookingStatus.SELECTED
                else:
                    raise InvalidBookingStateTransitionError(
                        f"Price change requires explicit user acceptance matching expected amount {intent.expected_amount}."
                    )

            if current_status not in {BookingStatus.SELECTED, BookingStatus.READY_FOR_BOOKING}:
                raise InvalidBookingStateTransitionError(
                    f"Intent in state '{current_status}' cannot be revalidated. Only SELECTED or READY_FOR_BOOKING permitted."
                )

            # CAS transition to REVALIDATING
            self._update_intent_status_cas(
                intent.id, current_status, BookingStatus.REVALIDATING, reason="SUPPLIER_CHECK_IN_FLIGHT"
            )

            # Load effective snapshot (current revalidation successor or original)
            effective_snapshot_id = intent.current_revalidation_snapshot_id or intent.selected_offer_snapshot_id
            orig_snapshot = self.snapshot_store.get_snapshot(effective_snapshot_id, user_id)
            if not orig_snapshot:
                self._update_intent_status_cas(
                    intent.id, BookingStatus.REVALIDATING, BookingStatus.FAILED, reason="ORIGINAL_SNAPSHOT_MISSING"
                )
                raise OfferUnavailableException("Original offer snapshot could not be loaded.")

            provider_offer_id = orig_snapshot.provider_offer_id
            now = datetime.now(timezone.utc)

            # Provider call (outside DB transaction)
            try:
                verif = await self.provider_port.revalidate_offer(
                    provider_offer_id=provider_offer_id,
                    expected_price=intent.expected_amount,
                    expected_currency=intent.currency,
                )
            except Exception as exc:
                self._update_intent_status_cas(
                    intent.id, BookingStatus.REVALIDATING, BookingStatus.FAILED, reason=f"PROVIDER_ERROR: {exc}"
                )
                self._record_attempt_completion(user_id, idempotency_key, "revalidate", {}, error_code=type(exc).__name__)
                raise

            # Material comparison
            divergent_fields = self._compare_flight_attributes(orig_snapshot, verif)

            # 1. Offer Withdrawn or unavailable
            v_avail = getattr(verif, "available", True)
            v_status = getattr(verif, "status", None)
            if not v_avail or "offer_availability" in divergent_fields or v_status == RevalidationStatus.OFFER_WITHDRAWN:
                self._update_intent_status_cas(
                    intent.id, BookingStatus.REVALIDATING, BookingStatus.UNAVAILABLE, reason=verif.details or "Offer withdrawn by carrier"
                )
                err = OfferUnavailableException(verif.details or "Offer is no longer available.")
                self._record_attempt_completion(user_id, idempotency_key, "revalidate", {}, error_code="OFFER_UNAVAILABLE")
                raise err

            # 2. Material non-price changes
            material_non_price_changes = [f for f in divergent_fields if f not in ("total_amount", "currency")]
            if material_non_price_changes:
                self._update_intent_status_cas(
                    intent.id, BookingStatus.REVALIDATING, BookingStatus.UNAVAILABLE, reason=f"Material fields changed: {material_non_price_changes}"
                )
                err_msg = f"Flight attributes modified by carrier: {', '.join(material_non_price_changes)}"
                self._record_attempt_completion(user_id, idempotency_key, "revalidate", {}, error_code="OFFER_CHANGED")
                raise OfferChangedException(err_msg)

            # 3. Price or Currency Changed
            v_total = getattr(verif, "total_amount", getattr(verif, "current_price", orig_snapshot.price_total))
            v_curr = getattr(verif, "currency", orig_snapshot.currency)
            v_price_changed = getattr(verif, "price_changed", False) or getattr(verif, "status", None) == RevalidationStatus.PRICE_CHANGED

            if "total_amount" in divergent_fields or "currency" in divergent_fields or v_price_changed:
                successor = self._persist_successor_snapshot(orig_snapshot, verif, user_id)
                self._update_intent_amount(intent, v_total, v_curr)
                self._update_intent_successor_id(intent, successor.snapshot_id)
                self._update_intent_status_cas(
                    intent.id, BookingStatus.REVALIDATING, BookingStatus.PRICE_CHANGED, reason=verif.details or "Fare changed by carrier"
                )
                err = FareChangedException(
                    original_amount=orig_snapshot.price_total,
                    new_amount=v_total,
                    currency=v_curr,
                    message=verif.details or f"Fare changed from {orig_snapshot.price_total} to {v_total} {v_curr}",
                    details={
                        "intent_id": str(intent.id),
                        "successor_snapshot_id": str(successor.snapshot_id),
                        "divergent_fields": divergent_fields,
                    },
                )
                self._record_attempt_completion(
                    user_id, idempotency_key, "revalidate", err.details, error_code="PRICE_CHANGED"
                )
                raise err

            # 4. Verified Unchanged -> Create successor snapshot & mark READY_FOR_BOOKING
            successor = self._persist_successor_snapshot(orig_snapshot, verif, user_id)
            self._update_intent_successor_id(intent, successor.snapshot_id)
            self._update_intent_status_cas(
                intent.id, BookingStatus.REVALIDATING, BookingStatus.READY_FOR_BOOKING, reason="REVALIDATION_VERIFIED"
            )

            reval_res = OfferRevalidationResult(
                is_valid=True,
                status=BookingStatus.READY_FOR_BOOKING,
                original_snapshot_id=orig_snapshot.snapshot_id,
                fresh_snapshot_id=successor.snapshot_id,
                price_changed=False,
                original_amount=orig_snapshot.price_total,
                current_amount=v_total,
                currency=v_curr,
                divergent_fields=[],
                revalidated_at=now,
                details=verif.details or "Verified with carrier",
            )

            self._record_attempt_completion(
                user_id, idempotency_key, "revalidate", reval_res.model_dump(mode="json")
            )
            return reval_res

    # ========================================================================
    # 3. CHECKOUT HANDOFF INITIATION
    # ========================================================================

    async def handoff_booking(
        self,
        user_id: UUID | str,
        intent_id: UUID | str,
        booking_mode: BookingMode | None = None,
        accepted_amount: Decimal | None = None,
        payment: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> FlightHandoffDestinationResponse:
        """Generate verified external booking handoff with fresh pre-handoff revalidation."""
        if not idempotency_key:
            idempotency_key = str(uuid4())
        else:
            idempotency_key = validate_idempotency_key(idempotency_key)
        uid_str = str(user_id)
        iid_str = str(intent_id)

        cached = self._claim_idempotency_attempt(
            intent_id=intent_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            action="handoff",
            request_payload={
                "booking_mode": booking_mode.value if booking_mode else None,
                "accepted_amount": str(accepted_amount) if accepted_amount else None,
                "payment": _sanitize_payload_for_persistence(payment),
            },
        )
        if cached:
            return FlightHandoffDestinationResponse.model_validate(cached)

        lock = await self._get_intent_lock(intent_id)
        async with lock:
            if not self.use_in_memory:
                return await self._handoff_booking_postgres(
                    user_id=user_id,
                    intent_id=intent_id,
                    booking_mode=booking_mode,
                    accepted_amount=accepted_amount,
                    payment=payment,
                    idempotency_key=idempotency_key,
                )

            intent = await self.get_booking_intent(user_id, intent_id)

            # 1. State machine check
            if intent.status != BookingStatus.READY_FOR_BOOKING:
                raise InvalidBookingStateTransitionError(
                    f"Handoff requires intent to be in 'READY_FOR_BOOKING' state (current: '{intent.status}')."
                )

            # 2. Revision guard
            trip = self.trip_store.get_trip(intent.trip_id, user_id)
            if not trip:
                raise OwnershipViolationException("Trip not found.")
            current_rev_num = resolve_current_revision(trip)
            meta = intent.metadata or {}
            intent_rev_num = meta.get("trip_revision")
            trip_rev_id = trip.get("current_revision_id")
            rev_mismatch = (intent_rev_num is not None and intent_rev_num != current_rev_num) or (trip_rev_id and str(intent.revision_id) != str(trip_rev_id))
            if rev_mismatch:
                self._update_intent_status_cas(intent.id, intent.status, BookingStatus.EXPIRED, reason="REVISION_SUPERSEDED")
                raise StaleRevisionException("Trip revision advanced; booking intent is stale.")

            # 3. Accepted amount verification
            if accepted_amount is not None and abs(accepted_amount - intent.expected_amount) > Decimal("0.01"):
                raise ValueError(
                    f"Accepted amount {accepted_amount} does not match authoritative fare {intent.expected_amount}."
                )

            # 4. Resolve snapshot
            effective_snapshot_id = intent.current_revalidation_snapshot_id or intent.selected_offer_snapshot_id
            snapshot = self.snapshot_store.get_snapshot(effective_snapshot_id, user_id)
            if not snapshot:
                raise OfferUnavailableException("Revalidated snapshot not found.")

            now = datetime.now(timezone.utc)
            if snapshot.expires_at and snapshot.expires_at <= now:
                self._update_intent_status_cas(intent.id, intent.status, BookingStatus.EXPIRED, reason="OFFER_TTL_LAPSED")
                raise OfferExpiredException("Offer TTL elapsed before handoff was initiated.")

            # ================================================================
            # BLOCKER 4: FRESH LIVE REVALIDATION AT HANDOFF
            # Never trust earlier READY_FOR_BOOKING result!
            # ================================================================
            verif = await self.provider_port.revalidate_offer(
                provider_offer_id=snapshot.provider_offer_id,
                expected_price=intent.expected_amount,
                expected_currency=intent.currency,
            )
            divergent_fields = self._compare_flight_attributes(snapshot, verif)

            v_avail = getattr(verif, "available", True)
            v_status = getattr(verif, "status", None)
            if not v_avail or "offer_availability" in divergent_fields or v_status == RevalidationStatus.OFFER_WITHDRAWN:
                self._update_intent_status_cas(
                    intent.id, BookingStatus.READY_FOR_BOOKING, BookingStatus.UNAVAILABLE, reason="Offer withdrawn during pre-handoff verification"
                )
                raise OfferUnavailableException("Offer became unavailable immediately before handoff.")

            material_non_price = [f for f in divergent_fields if f not in ("total_amount", "currency")]
            if material_non_price:
                self._update_intent_status_cas(
                    intent.id, BookingStatus.READY_FOR_BOOKING, BookingStatus.UNAVAILABLE, reason=f"Material fields changed at handoff: {material_non_price}"
                )
                raise OfferChangedException(f"Flight attributes changed immediately before handoff: {', '.join(material_non_price)}")

            v_total = getattr(verif, "total_amount", getattr(verif, "current_price", snapshot.price_total))
            v_curr = getattr(verif, "currency", snapshot.currency)
            v_price_changed = getattr(verif, "price_changed", False) or getattr(verif, "status", None) == RevalidationStatus.PRICE_CHANGED

            if "total_amount" in divergent_fields or "currency" in divergent_fields or v_price_changed:
                successor = self._persist_successor_snapshot(snapshot, verif, user_id)
                self._update_intent_amount(intent, v_total, v_curr)
                self._update_intent_successor_id(intent, successor.snapshot_id)
                self._update_intent_status_cas(
                    intent.id, BookingStatus.READY_FOR_BOOKING, BookingStatus.PRICE_CHANGED, reason="Price shifted immediately before handoff"
                )
                raise FareChangedException(
                    original_amount=snapshot.price_total,
                    new_amount=v_total,
                    currency=v_curr,
                    message="Price shifted immediately before handoff. Re-acknowledgement required.",
                    details={"intent_id": str(intent.id), "successor_snapshot_id": str(successor.snapshot_id)},
                )

            # Pre-handoff revalidation passed! Now proceed to provider handoff.
            resolved_mode = booking_mode or intent.booking_mode
            raw_payload = self.snapshot_store.get_raw_payload(snapshot.snapshot_id, user_id) or {}
            first_seg = snapshot.normalized_offer.segments[0] if snapshot.normalized_offer.segments else None
            airline_name = first_seg.marketing_carrier_name if first_seg else "Airline"
            flight_number = f"{first_seg.marketing_carrier_iata}{first_seg.flight_number}" if first_seg else ""

            # --- MODE A: DUFFEL LINKS ---
            if resolved_mode == BookingMode.DUFFEL_LINK:
                success_url = f"{self.public_base_url}/bookings/flight/callback?intent_id={iid_str}&status=success"
                failure_url = f"{self.public_base_url}/bookings/flight/callback?intent_id={iid_str}&status=failure"
                abandonment_url = f"{self.public_base_url}/bookings/flight/callback?intent_id={iid_str}&status=abandoned"

                passengers = meta.get("passengers", [])
                session = await self.provider_port.create_handoff_session(
                    provider_offer_id=snapshot.provider_offer_id,
                    passengers=passengers,
                    success_url=success_url,
                    failure_url=failure_url,
                    abandonment_url=abandonment_url,
                )

                handoff_url = session["handoff_url"]
                is_valid, hostname = _validate_and_normalize_url(handoff_url)
                if not is_valid or not _is_approved_duffel_host(hostname):
                    raise ValueError(f"Provider returned invalid or unapproved checkout URL: {handoff_url}")

                intent = self._update_intent_handoff(
                    intent=intent,
                    url=handoff_url,
                    ref=session["provider_reference"],
                    expires_at=session["expires_at"],
                    mode=resolved_mode,
                )
                self._update_intent_status_cas(intent.id, BookingStatus.READY_FOR_BOOKING, BookingStatus.HANDED_OFF, reason="DUFFEL_LINK_SESSION_MINTED")

                resp = FlightHandoffDestinationResponse(
                    intent_id=intent.id,
                    status=BookingStatus.HANDED_OFF,
                    booking_mode=BookingMode.DUFFEL_LINK,
                    redirect_url=handoff_url,
                    provider_reference=session["provider_reference"],
                    handoff_expires_at=session["expires_at"],
                    airline_name=airline_name,
                    flight_number=flight_number,
                    price_total=intent.expected_amount,
                    currency=intent.currency,
                    instructions="You are being securely redirected to Duffel's hosted checkout to complete your booking.",
                )

            # --- MODE B: DIRECT AIRLINE VERIFIED ---
            elif resolved_mode == BookingMode.DIRECT_AIRLINE_VERIFIED:
                direct_url = raw_payload.get("direct_airline_booking_url") or raw_payload.get("airline_checkout_url")
                if not direct_url or not isinstance(direct_url, str):
                    raise ValueError("Direct airline booking URL is not available for this carrier offer.")

                carrier_iata = first_seg.marketing_carrier_iata if first_seg else ""
                is_valid, hostname = _validate_and_normalize_url(direct_url)
                if not is_valid or not _is_approved_carrier_host(carrier_iata, hostname):
                    raise ValueError(f"Direct airline URL host '{hostname}' is not in the verified carrier allowlist.")

                expires_at = now + timedelta(minutes=15)
                intent = self._update_intent_handoff(
                    intent=intent,
                    url=direct_url,
                    ref=f"airline_{carrier_iata}",
                    expires_at=expires_at,
                    mode=resolved_mode,
                )
                self._update_intent_status_cas(intent.id, BookingStatus.READY_FOR_BOOKING, BookingStatus.HANDED_OFF, reason="DIRECT_AIRLINE_HANDOFF")

                resp = FlightHandoffDestinationResponse(
                    intent_id=intent.id,
                    status=BookingStatus.HANDED_OFF,
                    booking_mode=BookingMode.DIRECT_AIRLINE_VERIFIED,
                    redirect_url=direct_url,
                    provider_reference=f"airline_{carrier_iata}",
                    handoff_expires_at=expires_at,
                    airline_name=airline_name,
                    flight_number=flight_number,
                    price_total=intent.expected_amount,
                    currency=intent.currency,
                    instructions=f"You are being redirected directly to {airline_name}'s official portal to complete booking.",
                )

            # --- MODE C: DIRECT API ORDER (Sandbox Balance Demo Only) ---
            elif resolved_mode == BookingMode.DUFFEL_API_ORDER:
                env_name = os.getenv("ENVIRONMENT", os.getenv("ENV", "")).lower()
                if env_name == "production":
                    raise InvalidBookingStateTransitionError(
                        "DUFFEL_API_ORDER sandbox mode is strictly forbidden in production."
                    )

                if not payment:
                    raise ValueError("Payment details required for DUFFEL_API_ORDER mode.")

                # Validate allowlisted sandbox balance payment model strictly
                SandboxBalancePaymentModel.model_validate(payment)

                passengers = meta.get("passengers", [])
                order_res = await self.provider_port.create_api_order(
                    provider_offer_id=snapshot.provider_offer_id,
                    passengers=passengers,
                    payment_mode="sandbox_balance",
                    payment_details=payment,
                )
                order_id = order_res.get("order_id") or f"ord_{uuid4().hex[:10]}"
                intent = self._update_intent_handoff(
                    intent=intent,
                    url=None,
                    ref=order_id,
                    expires_at=now + timedelta(days=1),
                    mode=resolved_mode,
                )
                self._update_intent_status_cas(intent.id, BookingStatus.READY_FOR_BOOKING, BookingStatus.HANDED_OFF, reason="SANDBOX_ORDER_SUBMITTED")
                self._update_intent_status_cas(intent.id, BookingStatus.HANDED_OFF, BookingStatus.COMPLETED, reason="SANDBOX_ORDER_CONFIRMED")

                resp = FlightHandoffDestinationResponse(
                    intent_id=intent.id,
                    status=BookingStatus.COMPLETED,
                    booking_mode=BookingMode.DUFFEL_API_ORDER,
                    redirect_url=None,
                    provider_reference=order_id,
                    handoff_expires_at=None,
                    airline_name=airline_name,
                    flight_number=flight_number,
                    price_total=intent.expected_amount,
                    currency=intent.currency,
                    instructions="Booking confirmed via Duffel sandbox balance order.",
                )
            else:
                raise ValueError(f"Unsupported booking mode: {resolved_mode}")

            self._record_attempt_completion(user_id, idempotency_key, "handoff", resp.model_dump(mode="json"))
            return resp

    async def _handoff_booking_postgres(
        self,
        user_id: UUID | str,
        intent_id: UUID | str,
        booking_mode: BookingMode | None,
        accepted_amount: Decimal | None,
        payment: dict[str, Any] | None,
        idempotency_key: str,
    ) -> FlightHandoffDestinationResponse:
        """Execute handoff sequence under PostgreSQL SELECT ... FOR UPDATE row-level lock."""
        uid_str = str(user_id)
        iid_str = str(intent_id)

        with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
            with conn.transaction():
                # 1. Row-level lock acquisition
                row = conn.execute(
                    """
                    SELECT * FROM booking_intents
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (iid_str, uid_str),
                ).fetchone()
                if not row:
                    raise OwnershipViolationException(f"Booking intent '{iid_str}' not found or access denied.")

                intent = BookingIntent.model_validate(dict(row))

                # 2. State machine check
                if intent.status != BookingStatus.READY_FOR_BOOKING:
                    raise InvalidBookingStateTransitionError(
                        f"Handoff requires intent to be in 'READY_FOR_BOOKING' state (current: '{intent.status}')."
                    )

                # 3. Revision guard
                trip = self.trip_store.get_trip(intent.trip_id, user_id)
                if not trip:
                    raise OwnershipViolationException("Trip not found.")
                current_rev_num = resolve_current_revision(trip)
                meta = intent.metadata or {}
                intent_rev_num = meta.get("trip_revision")
                trip_rev_id = trip.get("current_revision_id")
                rev_mismatch = (intent_rev_num is not None and intent_rev_num != current_rev_num) or (trip_rev_id and str(intent.revision_id) != str(trip_rev_id))
                if rev_mismatch:
                    conn.execute(
                        "UPDATE booking_intents SET status = 'EXPIRED', updated_at = %s WHERE id = %s",
                        (datetime.now(timezone.utc), iid_str),
                    )
                    raise StaleRevisionException("Trip revision advanced; booking intent is stale.")

                # 4. Accepted amount verification
                if accepted_amount is not None and abs(accepted_amount - intent.expected_amount) > Decimal("0.01"):
                    raise ValueError(
                        f"Accepted amount {accepted_amount} does not match authoritative fare {intent.expected_amount}."
                    )

                # 5. Resolve snapshot
                effective_snapshot_id = intent.current_revalidation_snapshot_id or intent.selected_offer_snapshot_id
                snapshot = self.snapshot_store.get_snapshot(effective_snapshot_id, user_id)
                if not snapshot:
                    raise OfferUnavailableException("Revalidated snapshot not found.")

                now = datetime.now(timezone.utc)
                if snapshot.expires_at and snapshot.expires_at <= now:
                    conn.execute(
                        "UPDATE booking_intents SET status = 'EXPIRED', updated_at = %s WHERE id = %s",
                        (now, iid_str),
                    )
                    raise OfferExpiredException("Offer TTL elapsed before handoff was initiated.")

                # 6. Fresh live provider revalidation
                verif = await self.provider_port.revalidate_offer(
                    provider_offer_id=snapshot.provider_offer_id,
                    expected_price=intent.expected_amount,
                    expected_currency=intent.currency,
                )
                divergent_fields = self._compare_flight_attributes(snapshot, verif)

                v_avail = getattr(verif, "available", True)
                v_status = getattr(verif, "status", None)
                if not v_avail or "offer_availability" in divergent_fields or v_status == RevalidationStatus.OFFER_WITHDRAWN:
                    conn.execute(
                        "UPDATE booking_intents SET status = 'UNAVAILABLE', updated_at = %s WHERE id = %s",
                        (now, iid_str),
                    )
                    raise OfferUnavailableException("Offer became unavailable immediately before handoff.")

                material_non_price = [f for f in divergent_fields if f not in ("total_amount", "currency")]
                if material_non_price:
                    conn.execute(
                        "UPDATE booking_intents SET status = 'UNAVAILABLE', updated_at = %s WHERE id = %s",
                        (now, iid_str),
                    )
                    raise OfferChangedException(f"Flight attributes changed immediately before handoff: {', '.join(material_non_price)}")

                v_total = getattr(verif, "total_amount", getattr(verif, "current_price", snapshot.price_total))
                v_curr = getattr(verif, "currency", snapshot.currency)
                v_price_changed = getattr(verif, "price_changed", False) or getattr(verif, "status", None) == RevalidationStatus.PRICE_CHANGED

                if "total_amount" in divergent_fields or "currency" in divergent_fields or v_price_changed:
                    successor = self._persist_successor_snapshot(snapshot, verif, user_id)
                    conn.execute(
                        """
                        UPDATE booking_intents
                        SET expected_amount = %s, currency = %s, current_revalidation_snapshot_id = %s, status = 'PRICE_CHANGED', updated_at = %s
                        WHERE id = %s
                        """,
                        (v_total, v_curr, str(successor.snapshot_id), now, iid_str),
                    )
                    raise FareChangedException(
                        original_amount=snapshot.price_total,
                        new_amount=v_total,
                        currency=v_curr,
                        message="Price shifted immediately before handoff. Re-acknowledgement required.",
                        details={"intent_id": str(intent.id), "successor_snapshot_id": str(successor.snapshot_id)},
                    )

                # 7. Generate provider handoff session
                resolved_mode = booking_mode or intent.booking_mode
                raw_payload = self.snapshot_store.get_raw_payload(snapshot.snapshot_id, user_id) or {}
                first_seg = snapshot.normalized_offer.segments[0] if snapshot.normalized_offer.segments else None
                airline_name = first_seg.marketing_carrier_name if first_seg else "Airline"
                flight_number = f"{first_seg.marketing_carrier_iata}{first_seg.flight_number}" if first_seg else ""

                if resolved_mode == BookingMode.DUFFEL_LINK:
                    success_url = f"{self.public_base_url}/bookings/flight/callback?intent_id={iid_str}&status=success"
                    failure_url = f"{self.public_base_url}/bookings/flight/callback?intent_id={iid_str}&status=failure"
                    abandonment_url = f"{self.public_base_url}/bookings/flight/callback?intent_id={iid_str}&status=abandoned"

                    passengers = meta.get("passengers", [])
                    session = await self.provider_port.create_handoff_session(
                        provider_offer_id=snapshot.provider_offer_id,
                        passengers=passengers,
                        success_url=success_url,
                        failure_url=failure_url,
                        abandonment_url=abandonment_url,
                    )

                    handoff_url = session["handoff_url"]
                    is_valid, hostname = _validate_and_normalize_url(handoff_url)
                    if not is_valid or not _is_approved_duffel_host(hostname):
                        raise ValueError(f"Provider returned invalid or unapproved checkout URL: {handoff_url}")

                    conn.execute(
                        """
                        UPDATE booking_intents
                        SET status = 'HANDED_OFF'::booking_status_enum,
                            handoff_url = %s,
                            provider_reference = %s,
                            handoff_expires_at = %s,
                            booking_mode = %s::booking_mode_enum,
                            updated_at = %s
                        WHERE id = %s AND status = 'READY_FOR_BOOKING'::booking_status_enum
                        """,
                        (handoff_url, session["provider_reference"], session["expires_at"], resolved_mode.value, now, iid_str),
                    )

                    resp = FlightHandoffDestinationResponse(
                        intent_id=intent.id,
                        status=BookingStatus.HANDED_OFF,
                        booking_mode=BookingMode.DUFFEL_LINK,
                        redirect_url=handoff_url,
                        provider_reference=session["provider_reference"],
                        handoff_expires_at=session["expires_at"],
                        airline_name=airline_name,
                        flight_number=flight_number,
                        price_total=intent.expected_amount,
                        currency=intent.currency,
                        instructions="You are being securely redirected to Duffel's hosted checkout to complete your booking.",
                    )
                elif resolved_mode == BookingMode.DIRECT_AIRLINE_VERIFIED:
                    direct_url = raw_payload.get("direct_airline_booking_url") or raw_payload.get("airline_checkout_url")
                    if not direct_url or not isinstance(direct_url, str):
                        raise ValueError("Direct airline booking URL is not available for this carrier offer.")

                    carrier_iata = first_seg.marketing_carrier_iata if first_seg else ""
                    is_valid, hostname = _validate_and_normalize_url(direct_url)
                    if not is_valid or not _is_approved_carrier_host(carrier_iata, hostname):
                        raise ValueError(f"Direct airline URL host '{hostname}' is not in the verified carrier allowlist.")

                    expires_at = now + timedelta(minutes=15)
                    conn.execute(
                        """
                        UPDATE booking_intents
                        SET status = 'HANDED_OFF'::booking_status_enum,
                            handoff_url = %s,
                            provider_reference = %s,
                            handoff_expires_at = %s,
                            booking_mode = %s::booking_mode_enum,
                            updated_at = %s
                        WHERE id = %s AND status = 'READY_FOR_BOOKING'::booking_status_enum
                        """,
                        (direct_url, f"airline_{carrier_iata}", expires_at, resolved_mode.value, now, iid_str),
                    )

                    resp = FlightHandoffDestinationResponse(
                        intent_id=intent.id,
                        status=BookingStatus.HANDED_OFF,
                        booking_mode=BookingMode.DIRECT_AIRLINE_VERIFIED,
                        redirect_url=direct_url,
                        provider_reference=f"airline_{carrier_iata}",
                        handoff_expires_at=expires_at,
                        airline_name=airline_name,
                        flight_number=flight_number,
                        price_total=intent.expected_amount,
                        currency=intent.currency,
                        instructions=f"You are being redirected directly to {airline_name}'s official portal to complete booking.",
                    )
                elif resolved_mode == BookingMode.DUFFEL_API_ORDER:
                    env_name = os.getenv("ENVIRONMENT", os.getenv("ENV", "")).lower()
                    if env_name == "production":
                        raise InvalidBookingStateTransitionError(
                            "DUFFEL_API_ORDER sandbox mode is strictly forbidden in production."
                        )

                    if not payment:
                        raise ValueError("Payment details required for DUFFEL_API_ORDER mode.")

                    SandboxBalancePaymentModel.model_validate(payment)
                    passengers = meta.get("passengers", [])
                    order_res = await self.provider_port.create_api_order(
                        provider_offer_id=snapshot.provider_offer_id,
                        passengers=passengers,
                        payment_mode="sandbox_balance",
                        payment_details=payment,
                    )
                    order_id = order_res.get("order_id") or f"ord_{uuid4().hex[:10]}"
                    conn.execute(
                        """
                        UPDATE booking_intents
                        SET status = 'HANDED_OFF'::booking_status_enum,
                            provider_reference = %s,
                            booking_mode = %s::booking_mode_enum,
                            updated_at = %s
                        WHERE id = %s AND status = 'READY_FOR_BOOKING'::booking_status_enum
                        """,
                        (order_id, resolved_mode.value, now, iid_str),
                    )
                    conn.execute(
                        """
                        UPDATE booking_intents
                        SET status = 'COMPLETED'::booking_status_enum, updated_at = %s
                        WHERE id = %s AND status = 'HANDED_OFF'::booking_status_enum
                        """,
                        (now, iid_str),
                    )
                    resp = FlightHandoffDestinationResponse(
                        intent_id=intent.id,
                        status=BookingStatus.COMPLETED,
                        booking_mode=BookingMode.DUFFEL_API_ORDER,
                        redirect_url=None,
                        provider_reference=order_id,
                        handoff_expires_at=None,
                        airline_name=airline_name,
                        flight_number=flight_number,
                        price_total=intent.expected_amount,
                        currency=intent.currency,
                        instructions="Booking confirmed via Duffel sandbox balance order.",
                    )
                else:
                    raise ValueError(f"Unsupported booking mode: {resolved_mode}")

        self._record_attempt_completion(user_id, idempotency_key, "handoff", resp.model_dump(mode="json"))
        return resp

    # ========================================================================
    # 4. CANCEL BOOKING INTENT
    # ========================================================================

    async def cancel_booking_intent(
        self,
        user_id: UUID | str,
        intent_id: UUID | str,
        reason: str = "User requested cancellation",
        idempotency_key: str = "",
    ) -> BookingIntent:
        """Cancel an uncompleted intent, transitioning it to ABANDONED."""
        if not idempotency_key:
            idempotency_key = str(uuid4())
        else:
            idempotency_key = validate_idempotency_key(idempotency_key)
        cached = self._claim_idempotency_attempt(
            intent_id=intent_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            action="cancel-intent",
            request_payload={"reason": reason},
        )
        if cached:
            return BookingIntent.model_validate(cached)

        lock = await self._get_intent_lock(intent_id)
        async with lock:
            intent = await self.get_booking_intent(user_id, intent_id)
            if intent.status in {BookingStatus.COMPLETED, BookingStatus.FAILED, BookingStatus.EXPIRED, BookingStatus.ABANDONED}:
                raise InvalidBookingStateTransitionError(
                    f"Cannot cancel booking intent already in terminal state '{intent.status}'."
                )

            self._update_intent_status_cas(
                intent.id, intent.status, BookingStatus.ABANDONED, reason=reason, actor_id=str(user_id)
            )
            intent = await self.get_booking_intent(user_id, intent_id)
            self._record_attempt_completion(
                user_id, idempotency_key, "cancel-intent", intent.model_dump(mode="json")
            )
            return intent

    # ========================================================================
    # 5. QUERY INTENT
    # ========================================================================

    async def get_booking_intent(self, user_id: UUID | str, intent_id: UUID | str) -> BookingIntent:
        """Retrieve booking intent by ID, enforcing tenant ownership."""
        uid_str = str(user_id)
        iid_str = str(intent_id)

        if self.use_in_memory:
            record = self._IN_MEMORY_INTENTS.get(iid_str)
            if not record or str(record["user_id"]) != uid_str:
                raise OwnershipViolationException(f"Booking intent '{iid_str}' not found or access denied.")
            return BookingIntent.model_validate(record)

        with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT * FROM booking_intents
                WHERE id = %s AND user_id = %s
                """,
                (iid_str, uid_str),
            ).fetchone()
            if not row:
                raise OwnershipViolationException(f"Booking intent '{iid_str}' not found or access denied.")
            return BookingIntent.model_validate(dict(row))

    # ========================================================================
    # 6. AUTHENTICATED WEBHOOK CALLBACK
    # ========================================================================

    async def process_provider_callback(
        self,
        payload_bytes: bytes,
        signature_header: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate and process Duffel webhook callback."""
        secret = os.getenv("DUFFEL_WEBHOOK_SECRET") or os.getenv("TRUSTTRIP_WEBHOOK_SECRET")
        if not secret:
            raise UnauthorizedCallbackException("Webhook secret is not configured; failing closed.")
        if not signature_header:
            raise UnauthorizedCallbackException("Missing webhook signature header.")

        valid_sig = False
        expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

        if hmac.compare_digest(expected_sig, signature_header):
            valid_sig = True
        elif "v1=" in signature_header:
            parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
            if "v1" in parts and hmac.compare_digest(expected_sig, parts["v1"]):
                valid_sig = True
            elif "t" in parts and "v1" in parts:
                signed_content = f"{parts['t']}.{payload_bytes.decode('utf-8', errors='replace')}"
                ts_expected = hmac.new(secret.encode("utf-8"), signed_content.encode("utf-8"), hashlib.sha256).hexdigest()
                if hmac.compare_digest(ts_expected, parts["v1"]):
                    valid_sig = True

        if not valid_sig:
            raise UnauthorizedCallbackException("Invalid webhook signature.")

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception:
            raise InvalidCallbackException("Webhook payload is not valid JSON.")

        event_data = payload.get("data") or {}
        event_type = payload.get("type") or payload.get("event") or ""
        event_id = payload.get("id") or str(uuid4())
        provider_ref = event_data.get("id") or event_data.get("order_id") or ""
        metadata = event_data.get("metadata") or {}
        intent_id_str = metadata.get("intent_id") or event_data.get("booking_intent_id")

        if not provider_ref and not intent_id_str:
            raise InvalidCallbackException("Webhook payload missing provider order reference or intent_id.")

        dedup_key = f"webhook:{event_id}"

        # 1. Deduplication check (if event was already processed previously, return idempotent response immediately)
        if self.use_in_memory:
            if dedup_key in self._IN_MEMORY_ATTEMPTS:
                return {"status": "idempotent", "event_id": event_id, "message": "Callback already processed"}
        else:
            with psycopg.connect(_dsn()) as conn:
                existing = conn.execute(
                    "SELECT 1 FROM booking_attempts WHERE user_id = '00000000-0000-0000-0000-000000000000' AND idempotency_key = %s AND action = 'webhook_callback'",
                    (dedup_key,),
                ).fetchone()
                if existing:
                    return {"status": "idempotent", "event_id": event_id, "message": "Callback already processed"}

        # 2. Find target booking intent BEFORE consuming event ID (marking as processed)
        intent = None
        if self.use_in_memory:
            for rec in self._IN_MEMORY_INTENTS.values():
                if (intent_id_str and str(rec["id"]) == intent_id_str) or (provider_ref and rec.get("provider_reference") == provider_ref):
                    intent = BookingIntent.model_validate(rec)
                    break
        else:
            with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
                row = conn.execute(
                    """
                    SELECT * FROM booking_intents
                    WHERE (%s::uuid IS NOT NULL AND id = %s::uuid)
                       OR (%s != '' AND provider_reference = %s)
                    """,
                    (intent_id_str, intent_id_str, provider_ref, provider_ref),
                ).fetchone()
                if row:
                    intent = BookingIntent.model_validate(dict(row))

        if not intent:
            raise InvalidCallbackException(f"No booking intent found matching reference '{provider_ref or intent_id_str}'.")

        # 3. If already completed, idempotent return
        if intent.status == BookingStatus.COMPLETED:
            return {"status": "idempotent", "intent_id": str(intent.id), "booking_status": "COMPLETED"}

        # 4. Validate state machine: can only transition to COMPLETED if currently in HANDED_OFF
        if intent.status != BookingStatus.HANDED_OFF:
            raise InvalidBookingStateTransitionError(
                f"Cannot transition intent in state '{intent.status}' to COMPLETED via webhook. Must be in HANDED_OFF."
            )

        # 5. Record event ID deduplication upon valid event
        if self.use_in_memory:
            self._IN_MEMORY_ATTEMPTS[dedup_key] = {"status": "COMPLETED", "created_at": datetime.now(timezone.utc)}
        else:
            with psycopg.connect(_dsn(), autocommit=True) as conn:
                conn.execute(
                    """
                    INSERT INTO booking_attempts (user_id, idempotency_key, action, status, created_at)
                    VALUES ('00000000-0000-0000-0000-000000000000', %s, 'webhook_callback', 'COMPLETED', NOW())
                    ON CONFLICT (user_id, idempotency_key, action) DO NOTHING
                    """,
                    (dedup_key,),
                )

        if event_type in ("order.created", "order.confirmed", "payment.succeeded"):
            self._update_intent_status_cas(
                intent_id=intent.id,
                expected_current_status=BookingStatus.HANDED_OFF,
                new_status=BookingStatus.COMPLETED,
                reason=f"Authenticated webhook confirmation: {event_type} ({event_id})",
                actor_id="DUFFEL_WEBHOOK",
                metadata={"event_id": event_id, "provider_reference": provider_ref},
            )
            return {"status": "success", "intent_id": str(intent.id), "booking_status": "COMPLETED"}
        elif event_type in ("order.cancelled", "order.failed", "payment.failed"):
            self._update_intent_status_cas(
                intent_id=intent.id,
                expected_current_status=BookingStatus.HANDED_OFF,
                new_status=BookingStatus.FAILED,
                reason=f"Authenticated webhook failure: {event_type} ({event_id})",
                actor_id="DUFFEL_WEBHOOK",
                metadata={"event_id": event_id, "provider_reference": provider_ref},
            )
            return {"status": "success", "intent_id": str(intent.id), "booking_status": "FAILED"}
        else:
            return {"status": "ignored", "event_type": event_type}

    # ========================================================================
    # INTERNAL MUTATION HELPERS
    # ========================================================================

    def _persist_successor_snapshot(
        self,
        orig_snapshot: OfferSnapshot,
        verif: ProviderOfferVerification,
        user_id: UUID | str,
    ) -> OfferSnapshot:
        """Create a new OfferSnapshot inheriting from orig_snapshot with predecessor linkage, generated from NEW LIVE provider response."""
        successor_id = uuid4()
        now = datetime.now(timezone.utc)
        orig_offer = orig_snapshot.normalized_offer

        v_origin = getattr(verif, "origin", None)
        v_destination = getattr(verif, "destination", None)
        v_departure_time = getattr(verif, "departure_time", None)
        v_arrival_time = getattr(verif, "arrival_time", None)
        v_cabin_class = getattr(verif, "cabin_class", None)

        fresh_segments = []
        for i, seg in enumerate(orig_offer.segments):
            seg_dict = seg.model_dump()
            if i == 0 and v_origin:
                seg_dict["origin_iata"] = v_origin
            if i == len(orig_offer.segments) - 1 and v_destination:
                seg_dict["destination_iata"] = v_destination
            if i == 0 and v_departure_time:
                seg_dict["departure_time"] = v_departure_time
            if i == len(orig_offer.segments) - 1 and v_arrival_time:
                seg_dict["arrival_time"] = v_arrival_time
            if v_cabin_class:
                seg_dict["cabin_class"] = v_cabin_class
            fresh_segments.append(FlightSegmentModel.model_validate(seg_dict))

        v_total = getattr(verif, "total_amount", getattr(verif, "current_price", orig_snapshot.price_total))
        v_currency = getattr(verif, "currency", orig_snapshot.currency)
        v_offer_id = getattr(verif, "provider_offer_id", orig_snapshot.provider_offer_id)
        v_available = getattr(verif, "available", True)
        v_passengers = getattr(verif, "passenger_count", None) or orig_offer.passenger_count

        fresh_price = PriceBreakdown(
            base_amount=v_total,
            total_amount=v_total,
            currency=v_currency,
        )

        fresh_offer = FlightOffer(
            snapshot_id=successor_id,
            provider=orig_snapshot.provider,
            provider_offer_id=v_offer_id,
            price=fresh_price,
            stops=orig_offer.stops,
            segments=fresh_segments,
            passenger_count=v_passengers,
            available_seats=orig_offer.available_seats,
            retrieved_at=now,
            expires_at=now + timedelta(minutes=15),
            freshness=FreshnessStatus.FRESH,
            availability=AvailabilityStatus.AVAILABLE if v_available else AvailabilityStatus.UNAVAILABLE,
            limitations=orig_offer.limitations,
            is_recommended=orig_offer.is_recommended,
            recommendation_rank=orig_offer.recommendation_rank,
            candidate_departure_date=orig_offer.candidate_departure_date,
            candidate_return_date=orig_offer.candidate_return_date,
        )

        v_raw = getattr(verif, "raw_offer", None)
        raw = v_raw if v_raw else (self.snapshot_store.get_raw_payload(orig_snapshot.snapshot_id, user_id) or {})
        successor = self.snapshot_store.save_snapshot(
            session_id=orig_snapshot.session_id,
            offer=fresh_offer,
            raw_payload=raw,
            predecessor_snapshot_id=orig_snapshot.snapshot_id,
        )
        return successor

    def _update_intent_status_cas(
        self,
        intent_id: UUID | str,
        expected_current_status: BookingStatus,
        new_status: BookingStatus,
        reason: str = "",
        actor_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Optimistic CAS state transition: UPDATE ... WHERE id = %s AND status = %s."""
        validate_booking_transition(expected_current_status, new_status)
        now = datetime.now(timezone.utc)
        iid_str = str(intent_id)

        if self.use_in_memory:
            record = self._IN_MEMORY_INTENTS.get(iid_str)
            if not record:
                raise OwnershipViolationException(f"Booking intent '{iid_str}' not found.")
            if record["status"] != expected_current_status:
                raise BookingLockedException(
                    f"CAS conflict: expected intent state '{expected_current_status}', found '{record['status']}'."
                )
            record["status"] = new_status
            record["updated_at"] = now
            self._record_transition(
                intent_id=intent_id,
                from_status=expected_current_status,
                to_status=new_status,
                trigger="CAS_STATE_TRANSITION",
                actor_id=actor_id or str(record.get("user_id", "system")),
                reason=reason,
                metadata=metadata,
            )
            return

        with psycopg.connect(_dsn()) as conn:
            with conn.transaction():
                res = conn.execute(
                    """
                    UPDATE booking_intents
                    SET status = %s::booking_status_enum, updated_at = %s
                    WHERE id = %s AND status = %s::booking_status_enum
                    """,
                    (new_status.value, now, iid_str, expected_current_status.value),
                )
                if res.rowcount != 1:
                    raise BookingLockedException(
                        f"CAS state mutation conflict: expected intent state '{expected_current_status}', rowcount was {res.rowcount}."
                    )

    def _update_intent_amount(self, intent: BookingIntent, amount: Decimal, currency: str) -> None:
        now = datetime.now(timezone.utc)
        iid_str = str(intent.id)
        if self.use_in_memory:
            if iid_str in self._IN_MEMORY_INTENTS:
                self._IN_MEMORY_INTENTS[iid_str]["expected_amount"] = amount
                self._IN_MEMORY_INTENTS[iid_str]["currency"] = currency
                self._IN_MEMORY_INTENTS[iid_str]["updated_at"] = now
        else:
            with psycopg.connect(_dsn(), autocommit=True) as conn:
                conn.execute(
                    """
                    UPDATE booking_intents
                    SET expected_amount = %s, currency = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (amount, currency, now, iid_str),
                )

    def _update_intent_successor_id(self, intent: BookingIntent, successor_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        iid_str = str(intent.id)
        sid_str = str(successor_id)
        if self.use_in_memory:
            if iid_str in self._IN_MEMORY_INTENTS:
                self._IN_MEMORY_INTENTS[iid_str]["current_revalidation_snapshot_id"] = successor_id
                self._IN_MEMORY_INTENTS[iid_str]["updated_at"] = now
        else:
            with psycopg.connect(_dsn(), autocommit=True) as conn:
                conn.execute(
                    """
                    UPDATE booking_intents
                    SET current_revalidation_snapshot_id = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (sid_str, now, iid_str),
                )

    def _update_intent_handoff(
        self,
        intent: BookingIntent,
        url: str | None,
        ref: str | None,
        expires_at: datetime | None,
        mode: BookingMode,
    ) -> BookingIntent:
        now = datetime.now(timezone.utc)
        iid_str = str(intent.id)
        if self.use_in_memory:
            if iid_str in self._IN_MEMORY_INTENTS:
                self._IN_MEMORY_INTENTS[iid_str]["handoff_url"] = url
                self._IN_MEMORY_INTENTS[iid_str]["provider_reference"] = ref
                self._IN_MEMORY_INTENTS[iid_str]["handoff_expires_at"] = expires_at
                self._IN_MEMORY_INTENTS[iid_str]["booking_mode"] = mode
                self._IN_MEMORY_INTENTS[iid_str]["updated_at"] = now
        else:
            with psycopg.connect(_dsn(), autocommit=True) as conn:
                conn.execute(
                    """
                    UPDATE booking_intents
                    SET handoff_url = %s, provider_reference = %s, handoff_expires_at = %s,
                        booking_mode = %s::booking_mode_enum, updated_at = %s
                    WHERE id = %s
                    """,
                    (url, ref, expires_at, mode.value, now, iid_str),
                )
        return intent.model_copy(update={
            "handoff_url": url,
            "provider_reference": ref,
            "handoff_expires_at": expires_at,
            "booking_mode": mode,
            "updated_at": now,
        })

    @classmethod
    def invalidate_active_flight_intents(cls, trip_id: UUID | str, reason: str = "REVISION_SUPERSEDED") -> int:
        """Invalidate active non-terminal booking intents for a trip whose flight details changed."""
        tid_str = str(trip_id)
        now = datetime.now(timezone.utc)
        active_statuses = {
            BookingStatus.DRAFT,
            BookingStatus.SELECTED,
            BookingStatus.REVALIDATING,
            BookingStatus.PRICE_CHANGED,
            BookingStatus.READY_FOR_BOOKING,
        }

        use_in_memory = (
            os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}
            or not os.getenv("DATABASE_URL")
        )

        invalidated_count = 0
        if use_in_memory:
            for iid_str, record in cls._IN_MEMORY_INTENTS.items():
                if str(record.get("trip_id")) == tid_str and record.get("status") in active_statuses:
                    old_status = record["status"]
                    record["status"] = BookingStatus.EXPIRED
                    record["updated_at"] = now
                    invalidated_count += 1
                    cls._IN_MEMORY_TRANSITIONS.append({
                        "transition_id": uuid4(),
                        "intent_id": UUID(iid_str),
                        "from_status": old_status,
                        "to_status": BookingStatus.EXPIRED,
                        "trigger": "REVISION_SUPERSEDED",
                        "actor_id": "SYSTEM_HITL",
                        "reason": reason,
                        "metadata": {"trip_id": tid_str},
                        "timestamp": now,
                    })
            return invalidated_count

        with psycopg.connect(_dsn(), autocommit=True) as conn:
            table_check = conn.execute("SELECT to_regclass('booking_intents')").fetchone()
            if not table_check or not table_check[0]:
                return 0

            cur = conn.execute(
                """
                UPDATE booking_intents
                SET status = 'EXPIRED', updated_at = %s
                WHERE trip_id = %s
                  AND status IN ('DRAFT', 'SELECTED', 'REVALIDATING', 'PRICE_CHANGED', 'READY_FOR_BOOKING')
                """,
                (now, tid_str),
            )
            return cur.rowcount
