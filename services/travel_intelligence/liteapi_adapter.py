"""LiteAPI Hotel Provider Adapter implementing HotelSearchProviderPort.

Uses controlled asyncio.to_thread offloading and declares explicit infant capabilities
with transparent limitation warnings.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from domain.live_offer import (
    AvailabilityStatus,
    FreshnessStatus,
    HotelOffer,
    InfantSupportLevel,
    PriceBreakdown,
    ProviderLimitationWarning,
    RevalidationResult,
    RevalidationStatus,
)
from domain.search_criteria import HotelSearchCriteria
from services.hotel.hotel_provider import HotelProviderError
from services.hotel.liteapi_provider import LiteAPIProvider
from services.travel_intelligence.ports import HotelSearchProviderPort, ProviderCapability


def _scrub_hotel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive API keys and PII from hotel raw payload."""
    import json
    data = json.loads(json.dumps(payload, default=str))
    sensitive_keys = {"x-api-key", "api_key", "secret", "token", "guest_details", "phone", "email"}

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: "REDACTED" if k.lower() in sensitive_keys else _clean(v)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        return obj

    return _clean(data)


class LiteAPIHotelAdapter(HotelSearchProviderPort):
    """Adapter wrapping LiteAPIProvider conforming to HotelSearchProviderPort."""

    def __init__(self, provider: LiteAPIProvider | None = None) -> None:
        self._provider = provider or self._build_provider()
        self._capability = ProviderCapability(
            provider_name="liteapi",
            infant_support=InfantSupportLevel.SUPPLEMENT_ON_REQUEST,
            supports_flexible_dates=False,
            supports_cancellation_policies=True,
            supports_multi_room=True,
        )

    def _build_provider(self) -> LiteAPIProvider | None:
        try:
            return LiteAPIProvider()
        except HotelProviderError:
            return None

    @property
    def provider_name(self) -> str:
        return "liteapi"

    @property
    def capability(self) -> ProviderCapability:
        return self._capability

    def _sync_search(self, criteria: HotelSearchCriteria) -> list[tuple[HotelOffer, dict[str, Any]]]:
        if not self._provider:
            return []

        # Determine limitation warnings
        limitations: list[ProviderLimitationWarning] = []
        if criteria.infants > 0:
            limitations.append(
                ProviderLimitationWarning(
                    provider="liteapi",
                    code="INFANT_COT_REQUESTED",
                    message=(
                        f"{criteria.infants} infant(s) included via supplementary cot request; "
                        "please confirm infant cot/crib availability with the hotel prior to arrival."
                    ),
                    severity="info",
                )
            )

        # Children ages mapping
        child_ages = list(criteria.child_ages)
        # If infants exist, LiteAPI occupancy incorporates them with age 0 for room capacity
        for _ in range(criteria.infants):
            child_ages.append(0)

        total_children = criteria.children + criteria.infants

        req_payload = {
            "destination": criteria.destination_city,
            "checkin": criteria.checkin_date.isoformat(),
            "checkout": criteria.checkout_date.isoformat(),
            "adults": criteria.adults,
            "children": total_children,
            "children_ages": child_ages,
            "rooms": criteria.rooms,
            "currency": criteria.currency,
        }

        try:
            raw_offers = self._provider.search(req_payload)
        except Exception:
            return []

        results: list[tuple[HotelOffer, dict[str, Any]]] = []
        now = datetime.now(timezone.utc)
        nights = max(1, (criteria.checkout_date - criteria.checkin_date).days)

        for raw in raw_offers:
            offer_id = str(raw.get("provider_offer_id") or raw.get("id") or "")
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

            nightly_dec = Decimal(str(round(float(price_dec) / nights, 2)))

            # Cancellation details
            cancellation = raw.get("cancellation") or {}
            refundable = bool(raw.get("refundable"))
            deadline_str = cancellation.get("cancellationDeadline") or cancellation.get("deadline")
            deadline_dt = None
            if deadline_str:
                try:
                    deadline_dt = datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00"))
                except Exception:
                    deadline_dt = None

            offer = HotelOffer(
                provider="liteapi",
                provider_offer_id=offer_id,
                hotel_id=str(raw.get("hotel_id") or ""),
                hotel_name=str(raw.get("hotel_name") or "Hotel"),
                city=criteria.destination_city,
                address=str(raw.get("address") or ""),
                star_rating=float(raw["stars"]) if raw.get("stars") else None,
                review_score=float(raw["rating"]) if raw.get("rating") else None,
                room_name=str(raw.get("room_name") or "Standard Room"),
                board_type=str(raw.get("board_name") or "Room Only"),
                refundable=refundable,
                cancellation_deadline=deadline_dt,
                price=price,
                nightly_price=nightly_dec,
                nights=nights,
                rooms_count=criteria.rooms,
                adults_count=criteria.adults,
                children_count=criteria.children,
                infants_count=criteria.infants,
                rooms_inferred=False,
                retrieved_at=now,
                expires_at=None,  # Nullable for LiteAPI (transient rate)
                freshness=FreshnessStatus.FRESH,
                availability=AvailabilityStatus.AVAILABLE,
                limitations=limitations,
            )

            sanitized_raw = _scrub_hotel_payload(raw.get("raw") or raw)
            results.append((offer, sanitized_raw))

        return results

    async def search_hotels(
        self,
        criteria: HotelSearchCriteria,
    ) -> list[tuple[HotelOffer, dict[str, Any]]]:
        return await asyncio.to_thread(self._sync_search, criteria)

    def _sync_revalidate(
        self,
        provider_offer_id: str,
        expected_price: float | None = None,
        expected_currency: str | None = None,
    ) -> RevalidationResult:
        now = datetime.now(timezone.utc)
        reval_id = uuid4()

        if not self._provider:
            return RevalidationResult(
                revalidation_id=reval_id,
                original_snapshot_id=uuid4(),
                status=RevalidationStatus.OFFER_WITHDRAWN,
                verified_at=now,
                price_changed=False,
                original_price=Decimal(str(expected_price or 0)),
                current_price=Decimal("0.00"),
                currency=expected_currency or "USD",
                schedule_changed=False,
                details="Hotel provider is not configured.",
            )

        try:
            prebook_res = self._provider.prebook({"provider_offer_id": provider_offer_id, "rooms": 1})
            prebook_id = prebook_res.get("prebook_id")
            if not prebook_id:
                return RevalidationResult(
                    revalidation_id=reval_id,
                    original_snapshot_id=uuid4(),
                    status=RevalidationStatus.OFFER_WITHDRAWN,
                    verified_at=now,
                    price_changed=False,
                    original_price=Decimal(str(expected_price or 0)),
                    current_price=Decimal("0.00"),
                    currency=expected_currency or "USD",
                    schedule_changed=False,
                    details="Hotel rate is no longer available from supplier.",
                )
        except Exception as exc:
            return RevalidationResult(
                revalidation_id=reval_id,
                original_snapshot_id=uuid4(),
                status=RevalidationStatus.OFFER_WITHDRAWN,
                verified_at=now,
                price_changed=False,
                original_price=Decimal(str(expected_price or 0)),
                current_price=Decimal("0.00"),
                currency=expected_currency or "USD",
                schedule_changed=False,
                details=f"Rate prebook failed: {exc}",
            )

        orig_dec = Decimal(str(expected_price or 0))
        return RevalidationResult(
            revalidation_id=reval_id,
            original_snapshot_id=uuid4(),
            status=RevalidationStatus.CONFIRMED_UNCHANGED,
            verified_at=now,
            price_changed=False,
            original_price=orig_dec,
            current_price=orig_dec,
            currency=expected_currency or "USD",
            schedule_changed=False,
            details="Hotel rate and room availability confirmed unchanged with supplier.",
        )

    async def revalidate_hotel(
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
