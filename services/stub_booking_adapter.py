"""Deterministic, test-only FlightBookingProviderPort implementation.

Provides configurable mocking for all lifecycle paths:
- Unchanged revalidation
- Price change
- Offer withdrawn
- Provider timeout & rate limit
- Handoff URL creation (valid Duffel Link)
- Sandbox balance ordering
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from domain.booking_lifecycle import BookingProviderTimeout, BookingProviderUnavailable
from domain.live_offer import RevalidationResult, RevalidationStatus
from services.booking_provider_port import FlightBookingProviderPort, ProviderOfferVerification
from services.flight_provider import FlightProviderRateLimitError


class StubBookingAdapter(FlightBookingProviderPort):
    """Deterministic in-memory booking adapter for unit & integration testing."""

    def __init__(self) -> None:
        self._provider_name = "test_stub"
        # Overrides for testing failure modes
        self.should_timeout: bool = False
        self.should_rate_limit: bool = False
        self.should_fail_unavailable: bool = False
        self.revalidation_status_override: RevalidationStatus | None = None
        self.revalidation_available_override: bool | None = None
        self.revalidation_price_override: Decimal | None = None
        self.revalidation_currency_override: str | None = None
        self.revalidation_schedule_changed: bool = False
        self.revalidation_origin_override: str | None = None
        self.revalidation_destination_override: str | None = None
        self.revalidation_departure_time_override: datetime | None = None
        self.revalidation_arrival_time_override: datetime | None = None
        self.revalidation_passenger_count_override: int | None = None
        self.revalidation_passengers_override: list[dict[str, Any]] | None = None
        self.revalidation_cabin_override: str | None = None
        self.revalidation_baggage_override: str | None = None
        self.revalidation_included_baggage_override: list[dict[str, Any]] | None = None
        self.revalidation_fare_rules_override: str | None = None
        self.revalidation_conditions_override: dict[str, Any] | None = None
        self.force_missing_baggage: bool = False
        self.force_missing_fare_rules: bool = False
        self.force_missing_cabin: bool = False
        self.force_missing_passengers: bool = False
        self.raw_offer_override: dict[str, Any] | None = None
        self.custom_handoff_url: str | None = None

        # Recorded calls for verification
        self.revalidate_calls: list[dict[str, Any]] = []
        self.handoff_calls: list[dict[str, Any]] = []
        self.order_calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def revalidate_offer(
        self,
        provider_offer_id: str,
        expected_price: Decimal | None = None,
        expected_currency: str | None = None,
        call_timeout: float = 15.0,
    ) -> ProviderOfferVerification:
        self.revalidate_calls.append({
            "provider_offer_id": provider_offer_id,
            "expected_price": expected_price,
            "expected_currency": expected_currency,
        })

        if self.should_timeout:
            raise BookingProviderTimeout("Stub provider simulated a timeout.")
        if self.should_rate_limit:
            raise FlightProviderRateLimitError("Stub provider simulated a 429 rate limit.")
        if self.should_fail_unavailable:
            raise BookingProviderUnavailable("Stub provider simulated a 502 unavailable.")

        now = datetime.now(timezone.utc)
        orig_price = expected_price if expected_price is not None else Decimal("450.00")
        currency = expected_currency or "USD"

        status = self.revalidation_status_override or RevalidationStatus.CONFIRMED_UNCHANGED
        curr_price = self.revalidation_price_override if self.revalidation_price_override is not None else orig_price
        curr_curr = self.revalidation_currency_override or currency

        price_changed = (curr_price != orig_price) or (curr_curr != currency)
        if price_changed and status == RevalidationStatus.CONFIRMED_UNCHANGED:
            status = RevalidationStatus.PRICE_CHANGED

        available = True if self.revalidation_available_override is None else self.revalidation_available_override
        if status == RevalidationStatus.OFFER_WITHDRAWN:
            available = False

        # Provide deterministic flight attributes from snapshot if present
        default_origin = None
        default_dest = None
        default_dep = None
        default_arr = None
        default_cabin = None
        default_included_baggage = None
        default_conditions = None
        default_passengers = None
        default_pax_count = 1

        try:
            from services.search_session_store import SearchSessionStore, decrypt_payload
            store = SearchSessionStore()
            if store.use_in_memory:
                for sid, rec in store._IN_MEMORY_SNAPSHOTS.items():
                    norm = rec.get("normalized_offer")
                    p_id = rec.get("provider_offer_id")
                    if not p_id and norm:
                        p_id = norm.get("provider_offer_id") if isinstance(norm, dict) else getattr(norm, "provider_offer_id", None)
                    if p_id == provider_offer_id:
                        if isinstance(norm, dict):
                            norm_segs = norm.get("segments") or []
                            if norm_segs:
                                default_origin = norm_segs[0].get("origin_iata")
                                default_dest = norm_segs[-1].get("destination_iata")
                                default_dep = norm_segs[0].get("departure_time")
                                default_arr = norm_segs[-1].get("arrival_time")
                                default_cabin = norm_segs[0].get("cabin_class")
                            default_pax_count = norm.get("passenger_count", 1)
                        elif hasattr(norm, "segments"):
                            if norm.segments:
                                default_origin = norm.segments[0].origin_iata
                                default_dest = norm.segments[-1].destination_iata
                                default_dep = norm.segments[0].departure_time
                                default_arr = norm.segments[-1].arrival_time
                                default_cabin = norm.segments[0].cabin_class
                            default_pax_count = getattr(norm, "passenger_count", 1)
                        raw_p = store.get_raw_payload_internal(sid) or {}
                        default_included_baggage = raw_p.get("included_baggage")
                        default_conditions = raw_p.get("conditions")
                        default_passengers = raw_p.get("passengers")
                        break
            else:
                with store._connect() as conn:
                    row = conn.execute(
                        "SELECT id, normalized_offer, raw_payload_encrypted FROM offer_snapshots WHERE provider_offer_id = %s ORDER BY retrieved_at DESC LIMIT 1",
                        (provider_offer_id,),
                    ).fetchone()
                    if row:
                        raw_blob = bytes(row["raw_payload_encrypted"]) if row.get("raw_payload_encrypted") else None
                        raw_p = decrypt_payload(raw_blob, store._key) if raw_blob else {}
                        default_included_baggage = raw_p.get("included_baggage")
                        default_conditions = raw_p.get("conditions")
                        default_passengers = raw_p.get("passengers")
                        norm = row.get("normalized_offer") or {}
                        if isinstance(norm, dict):
                            norm_segs = norm.get("segments") or []
                            if norm_segs:
                                default_origin = norm_segs[0].get("origin_iata")
                                default_dest = norm_segs[-1].get("destination_iata")
                                default_dep = norm_segs[0].get("departure_time")
                                default_arr = norm_segs[-1].get("arrival_time")
                                default_cabin = norm_segs[0].get("cabin_class")
                            default_pax_count = norm.get("passenger_count", 1)
        except Exception:
            pass

        # Fallback to standard route attributes only if snapshot segments were not discovered
        if default_origin is None:
            default_origin = "DEL"
            default_dest = "LHR"
            default_dep = now + timedelta(days=14)
            default_arr = now + timedelta(days=14, hours=9)
            default_cabin = "economy"

        final_origin = None if getattr(self, "force_missing_origin", False) else (
            self.revalidation_origin_override if self.revalidation_origin_override is not None else default_origin
        )
        final_dest = self.revalidation_destination_override if self.revalidation_destination_override is not None else default_dest
        final_dep = self.revalidation_departure_time_override if self.revalidation_departure_time_override is not None else default_dep
        final_arr = self.revalidation_arrival_time_override if self.revalidation_arrival_time_override is not None else default_arr
        final_cabin = None if self.force_missing_cabin else (
            self.revalidation_cabin_override if self.revalidation_cabin_override is not None else default_cabin
        )
        final_baggage = None
        final_included_baggage = None if self.force_missing_baggage else (
            self.revalidation_included_baggage_override if self.revalidation_included_baggage_override is not None else default_included_baggage
        )
        final_fare_rules = None
        final_conditions = None if self.force_missing_fare_rules else (
            self.revalidation_conditions_override if self.revalidation_conditions_override is not None else default_conditions
        )
        final_passengers = None if self.force_missing_passengers else (
            self.revalidation_passengers_override if self.revalidation_passengers_override is not None else default_passengers
        )
        final_passenger_count = (
            len(final_passengers)
            if final_passengers is not None
            else (self.revalidation_passenger_count_override if self.revalidation_passenger_count_override is not None else default_pax_count)
        )
        if self.force_missing_passengers:
            final_passenger_count = None

        return ProviderOfferVerification(
            provider_offer_id=provider_offer_id,
            status=status,
            available=available,
            total_amount=curr_price,
            currency=curr_curr,
            origin=final_origin,
            destination=final_dest,
            departure_time=final_dep,
            arrival_time=final_arr,
            passenger_count=final_passenger_count,
            passengers=final_passengers,
            cabin_class=final_cabin,
            included_baggage=final_included_baggage,
            baggage_summary=final_baggage,
            conditions=final_conditions,
            fare_rules_summary=final_fare_rules,
            raw_offer=self.raw_offer_override or {},
            details=f"Stub revalidation with status {status.value}",
        )

    async def create_handoff_session(
        self,
        provider_offer_id: str,
        passengers: list[dict[str, Any]],
        success_url: str,
        failure_url: str,
        abandonment_url: str,
        call_timeout: float = 20.0,
    ) -> dict[str, Any]:
        self.handoff_calls.append({
            "provider_offer_id": provider_offer_id,
            "passengers": passengers,
            "success_url": success_url,
            "failure_url": failure_url,
            "abandonment_url": abandonment_url,
        })

        if self.should_timeout:
            raise BookingProviderTimeout("Stub provider handoff timed out.")
        if self.should_rate_limit:
            raise FlightProviderRateLimitError("Stub provider handoff rate limited.")
        if self.should_fail_unavailable:
            raise BookingProviderUnavailable("Stub provider handoff 502.")

        session_id = f"ses_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=15)
        handoff_url = self.custom_handoff_url or f"https://links.duffel.com/pay/{session_id}"

        return {
            "handoff_url": handoff_url,
            "provider_reference": session_id,
            "expires_at": expires_at,
        }

    async def create_api_order(
        self,
        provider_offer_id: str,
        passengers: list[dict[str, Any]],
        payment_mode: str,
        payment_details: dict[str, Any] | None = None,
        call_timeout: float = 60.0,
    ) -> dict[str, Any]:
        self.order_calls.append({
            "provider_offer_id": provider_offer_id,
            "passengers": passengers,
            "payment_mode": payment_mode,
            "payment_details": payment_details,
        })

        if self.should_timeout:
            raise BookingProviderTimeout("Stub order timed out.")
        if self.should_rate_limit:
            raise FlightProviderRateLimitError("Stub order rate limited.")
        if self.should_fail_unavailable:
            raise BookingProviderUnavailable("Stub order 502.")

        order_id = f"ord_{uuid4().hex[:12]}"
        pnr = f"TT{uuid4().hex[:4].upper()}"

        return {
            "provider": self.provider_name,
            "order_id": order_id,
            "booking_reference": pnr,
            "status": "confirmed",
            "order_type": "instant",
            "total_amount": "450.00",
            "total_currency": "USD",
            "payment_mode": payment_mode,
            "documents": [{"type": "electronic_ticket", "unique_identifier": f"ETKT-{pnr}"}],
        }

    async def get_order_status(
        self,
        provider_order_id: str,
        call_timeout: float = 15.0,
    ) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "order_id": provider_order_id,
            "booking_reference": f"TT{provider_order_id[-4:].upper()}",
            "status": "confirmed",
            "order_type": "instant",
        }

    async def cancel_order(
        self,
        provider_order_id: str,
        call_timeout: float = 30.0,
    ) -> dict[str, Any]:
        return {
            "order_id": provider_order_id,
            "status": "cancelled",
            "cancellation_id": f"ore_{uuid4().hex[:10]}",
            "refund_amount": "450.00",
            "currency": "USD",
        }
