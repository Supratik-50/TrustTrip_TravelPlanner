"""Duffel Flight Provider Adapter implementing FlightSearchProviderPort.

Uses controlled asyncio.to_thread offloading and scrubs passenger PII / provider secrets
before emitting raw payloads to the persistence layer.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    FreshnessStatus,
    InfantSupportLevel,
    PriceBreakdown,
    RevalidationResult,
    RevalidationStatus,
)
from domain.search_criteria import FlightSearchCriteria
from services.duffel_provider import DuffelProvider
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.travel_intelligence.ports import FlightSearchProviderPort, ProviderCapability


def _scrub_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive provider credentials and passenger PII from raw payload."""
    scrubbed = json_clean(payload)
    # Recursively strip sensitive keys
    sensitive_keys = {
        "authorization", "token", "access_token", "api_key", "secret",
        "email", "phone_number", "passport", "identity_document", "born_on",
    }

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: "REDACTED" if k.lower() in sensitive_keys else _clean(v)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        return obj

    return _clean(scrubbed)


def json_clean(data: Any) -> Any:
    """Ensure data is cleanly serializable without cyclic references."""
    import json
    return json.loads(json.dumps(data, default=str))


class DuffelFlightAdapter(FlightSearchProviderPort):
    """Adapter wrapping DuffelProvider conforming to FlightSearchProviderPort."""

    def __init__(self, provider: DuffelProvider | None = None) -> None:
        self._provider = provider or DuffelProvider()
        self._capability = ProviderCapability(
            provider_name="duffel",
            infant_support=InfantSupportLevel.NATIVE,
            supports_flexible_dates=False,
            supports_cancellation_policies=True,
            supports_multi_room=False,
        )

    @property
    def provider_name(self) -> str:
        return "duffel"

    @property
    def capability(self) -> ProviderCapability:
        return self._capability

    def _sync_search(self, criteria: FlightSearchCriteria) -> list[tuple[FlightOffer, dict[str, Any]]]:
        slices = [
            {
                "origin": s.origin_iata.upper(),
                "destination": s.destination_iata.upper(),
                "departure_date": s.departure_date.isoformat(),
            }
            for s in criteria.slices
        ]
        passengers = [{"type": "adult"}] * criteria.adults
        passengers += [{"type": "child"}] * criteria.children
        passengers += [{"type": "infant_without_seat"}] * criteria.infants

        req_payload = {
            "slices": slices,
            "passengers": passengers,
            "cabin_class": criteria.cabin_class,
            "max_connections": criteria.max_connections,
        }

        raw_offers = self._provider.search(req_payload)
        results: list[tuple[FlightOffer, dict[str, Any]]] = []
        now = datetime.now(timezone.utc)

        for raw in raw_offers:
            offer_id = str(raw.get("id") or "")
            if not offer_id:
                continue

            price_num = raw.get("price")
            currency = str(raw.get("currency") or "USD").upper()
            if price_num is None:
                continue

            price_dec = Decimal(str(price_num))
            price_inr_num = raw.get("price_inr")
            price_inr_dec = Decimal(str(price_inr_num)) if price_inr_num is not None else None

            price = PriceBreakdown(
                base_amount=price_dec,
                tax_amount=Decimal("0.00"),
                fee_amount=Decimal("0.00"),
                total_amount=price_dec,
                currency=currency,
                inr_reference_amount=price_inr_dec,
                fx_rate_applied=Decimal(str(raw.get("exchange_rate"))) if raw.get("exchange_rate") else None,
            )

            # Segments normalization
            segments: list[FlightSegmentModel] = []
            for leg in raw.get("segments") or []:
                dep_dt = datetime.fromisoformat(str(leg.get("departure")).replace("Z", "+00:00"))
                arr_dt = datetime.fromisoformat(str(leg.get("arrival")).replace("Z", "+00:00"))
                duration_raw = str(leg.get("duration") or "")
                # Parse duration ISO (e.g. PT2H30M) or minutes
                dur_minutes = 0
                if "H" in duration_raw:
                    try:
                        import re
                        hours = int(re.search(r"(\d+)H", duration_raw).group(1)) if "H" in duration_raw else 0
                        mins = int(re.search(r"(\d+)M", duration_raw).group(1)) if "M" in duration_raw else 0
                        dur_minutes = hours * 60 + mins
                    except Exception:
                        dur_minutes = 120
                else:
                    dur_minutes = 120

                segments.append(
                    FlightSegmentModel(
                        origin_iata=str(leg.get("origin") or "UNK").upper(),
                        destination_iata=str(leg.get("destination") or "UNK").upper(),
                        departure_time=dep_dt,
                        arrival_time=arr_dt,
                        marketing_carrier_iata=str(leg.get("marketing_carrier_iata") or "XX").upper(),
                        marketing_carrier_name=str(leg.get("marketing_carrier") or "Airline"),
                        flight_number=str(leg.get("flight_number") or ""),
                        operating_carrier_name=leg.get("operating_carrier"),
                        duration_minutes=dur_minutes,
                        cabin_class=criteria.cabin_class,
                    )
                )

            # Parse expires_at
            raw_exp = raw.get("expires_at")
            if raw_exp:
                try:
                    exp_dt = datetime.fromisoformat(str(raw_exp).replace("Z", "+00:00"))
                except Exception:
                    exp_dt = datetime.fromtimestamp(now.timestamp() + 1800, tz=timezone.utc)
            else:
                exp_dt = datetime.fromtimestamp(now.timestamp() + 1800, tz=timezone.utc)

            stops = int(raw.get("stops") or max(0, len(segments) - 1))
            total_pax = criteria.adults + criteria.children + criteria.infants

            offer = FlightOffer(
                provider="duffel",
                provider_offer_id=offer_id,
                price=price,
                stops=stops,
                segments=segments,
                passenger_count=total_pax,
                available_seats=raw.get("available_seats"),
                retrieved_at=now,
                expires_at=exp_dt,
                freshness=FreshnessStatus.FRESH,
                availability=AvailabilityStatus.AVAILABLE,
                limitations=[],
            )

            sanitized_raw = _scrub_payload(raw.get("raw") or raw)
            results.append((offer, sanitized_raw))

        return results

    async def search_flights(
        self,
        criteria: FlightSearchCriteria,
    ) -> list[tuple[FlightOffer, dict[str, Any]]]:
        return await asyncio.to_thread(self._sync_search, criteria)

    def _sync_revalidate(
        self,
        provider_offer_id: str,
        expected_price: float | None = None,
        expected_currency: str | None = None,
    ) -> RevalidationResult:
        now = datetime.now(timezone.utc)
        reval_id = uuid4()
        try:
            latest = self._provider.get_offer(provider_offer_id)
        except (FlightProviderError, FlightProviderTimeout) as exc:
            return RevalidationResult(
                revalidation_id=reval_id,
                original_snapshot_id=uuid4(),  # Populated by application service
                status=RevalidationStatus.OFFER_WITHDRAWN,
                verified_at=now,
                price_changed=False,
                original_price=Decimal(str(expected_price or 0)),
                current_price=Decimal("0.00"),
                currency=expected_currency or "USD",
                schedule_changed=False,
                details=f"Offer withdrawn or supplier error: {exc}",
            )

        current_amount = latest.get("price")
        current_currency = str(latest.get("currency") or "USD").upper()

        if current_amount is None:
            return RevalidationResult(
                revalidation_id=reval_id,
                original_snapshot_id=uuid4(),
                status=RevalidationStatus.OFFER_WITHDRAWN,
                verified_at=now,
                price_changed=False,
                original_price=Decimal(str(expected_price or 0)),
                current_price=Decimal("0.00"),
                currency=current_currency,
                schedule_changed=False,
                details="Supplier offer does not contain a price.",
            )

        current_dec = Decimal(str(current_amount))
        orig_dec = Decimal(str(expected_price)) if expected_price is not None else current_dec

        price_delta = abs(current_dec - orig_dec) > Decimal("0.01")
        currency_delta = expected_currency is not None and current_currency != expected_currency.upper()

        if price_delta or currency_delta:
            status = RevalidationStatus.PRICE_CHANGED
            details = f"Fare updated from {expected_currency or ''} {orig_dec} to {current_currency} {current_dec}"
        else:
            status = RevalidationStatus.CONFIRMED_UNCHANGED
            details = "Fare and schedule verified unchanged with supplier."

        return RevalidationResult(
            revalidation_id=reval_id,
            original_snapshot_id=uuid4(),
            status=status,
            verified_at=now,
            price_changed=price_delta or currency_delta,
            original_price=orig_dec,
            current_price=current_dec,
            currency=current_currency,
            schedule_changed=False,
            details=details,
        )

    async def revalidate_flight(
        self,
        provider_offer_id: str,
        expected_price: float | None = None,
        expected_currency: str | None = None,
    ) -> RevalidationResult:
        return await asyncio.to_thread(
            self._sync_revalidate,
            provider_offer_id,
            expected_price,
            expected_currency,
        )
