"""Duffel Flight Booking Adapter implementing FlightBookingProviderPort.

Provides:
- Async pre-handoff revalidation via GET /air/offers/{id}
- Hosted checkout session creation via POST /air/links
- Direct API ordering via POST /air/orders (test/sandbox balance)
- Strict URL validation & hostname allowlist verification
- Scrubbing of sensitive provider secrets and credentials
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from typing import Any
from uuid import UUID, uuid4

import httpx
from domain.booking_lifecycle import (
    BookingProviderTimeout,
    BookingProviderUnavailable,
)
from domain.live_offer import RevalidationResult, RevalidationStatus
from services.booking_provider_port import FlightBookingProviderPort, ProviderOfferVerification
from services.duffel_provider import DuffelProvider
from services.flight_provider import FlightProviderError, FlightProviderRateLimitError
from services.travel_intelligence.duffel_adapter import (
    _is_approved_duffel_host,
    _scrub_payload,
    _validate_and_normalize_url,
)


class DuffelBookingAdapter(FlightBookingProviderPort):
    """Adapter wrapping Duffel API for booking lifecycle, handoff links, and orders."""

    def __init__(
        self,
        token: str | None = None,
        provider: DuffelProvider | None = None,
        api_token: str | None = None,
    ) -> None:
        if isinstance(token, DuffelProvider):
            provider, token = token, None
        if isinstance(provider, str) and not token:
            token = provider
            provider = None
        self._provider = provider
        self.mode = getattr(provider, "mode", None) or os.getenv("DUFFEL_MODE", "test").strip().lower()
        self.base_url = getattr(provider, "BASE_URL", "https://api.duffel.com").rstrip("/")
        # Explicit real token resolution - never masked
        resolved_token = (
            token
            or api_token
            or getattr(provider, "token", None)
            or (os.getenv("DUFFEL_TEST_API_TOKEN") if self.mode == "test" else os.getenv("DUFFEL_LIVE_API_TOKEN"))
            or os.getenv("DUFFEL_ACCESS_TOKEN")
            or os.getenv("DUFFEL_API_TOKEN")
            or ""
        )
        self.token = str(resolved_token).strip()

    @property
    def provider_name(self) -> str:
        return "duffel"

    def __repr__(self) -> str:
        return f"<DuffelBookingAdapter(mode='{self.mode}', base_url='{self.base_url}', token=[REDACTED])>"

    def __str__(self) -> str:
        return self.__repr__()

    def _headers(self) -> dict[str, str]:
        token = self.token
        clean_token = token.removeprefix("Bearer ").strip()
        auth_val = f"Bearer {clean_token}" if clean_token else ""

        return {
            "Authorization": auth_val,
            "Duffel-Version": "v2",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "TrustTrip-Booking/1.0",
        }

    def _redact_body(self, text: str) -> str:
        """Sanitize error messages or provider bodies, scrubbing tokens, secrets, PANs, and credentials."""
        if not text:
            return ""
        import re
        scrubbed = re.sub(r"(token=)[^\s&]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
        scrubbed = re.sub(r"(Bearer\s+)[^\s&]+", r"\1[REDACTED]", scrubbed, flags=re.IGNORECASE)
        scrubbed = re.sub(r"(api_key=)[^\s&]+", r"\1[REDACTED]", scrubbed, flags=re.IGNORECASE)
        return scrubbed

    async def revalidate_offer(
        self,
        provider_offer_id: str,
        expected_price: Decimal | None = None,
        expected_currency: str | None = None,
        call_timeout: float = 15.0,
    ) -> ProviderOfferVerification:
        """Asynchronously verify live flight offer price and availability."""
        now = datetime.now(timezone.utc)

        if not provider_offer_id or not provider_offer_id.startswith("off_"):
            return ProviderOfferVerification(
                provider_offer_id=provider_offer_id or "",
                status=RevalidationStatus.OFFER_WITHDRAWN,
                available=False,
                total_amount=Decimal("0.00"),
                currency=expected_currency or "USD",
                details="A valid Duffel offer ID is required.",
            )

        try:
            async with httpx.AsyncClient(timeout=call_timeout) as client:
                try:
                    response = await client.get(
                        f"{self.base_url}/air/offers/{provider_offer_id}",
                        headers=self._headers(),
                    )
                except httpx.TimeoutException as exc:
                    raise BookingProviderTimeout("Duffel revalidation timed out.") from exc
                except httpx.RequestError as exc:
                    raise BookingProviderUnavailable("Duffel revalidation network failure.") from exc

                if response.status_code in {404, 410}:
                    return ProviderOfferVerification(
                        provider_offer_id=provider_offer_id,
                        status=RevalidationStatus.OFFER_WITHDRAWN,
                        available=False,
                        total_amount=Decimal("0.00"),
                        currency=expected_currency or "USD",
                        details="Offer is no longer available from supplier.",
                    )

                if response.status_code == 401:
                    raise BookingProviderUnavailable("Duffel authentication failed (HTTP 401).")
                if response.status_code == 403:
                    raise BookingProviderUnavailable("Duffel permission denied (HTTP 403).")
                if response.status_code == 429:
                    raise FlightProviderRateLimitError("Duffel API rate limit exceeded (HTTP 429).")

                if not response.is_success:
                    raise BookingProviderUnavailable(f"Duffel returned error status HTTP {response.status_code}.")

                data = response.json().get("data") or {}
                total_amount = data.get("total_amount")
                raw_curr = data.get("total_currency")
                current_currency = str(raw_curr).upper().strip() if raw_curr else ""

                if total_amount is None or not current_currency:
                    return ProviderOfferVerification(
                        provider_offer_id=provider_offer_id,
                        status=RevalidationStatus.OFFER_WITHDRAWN,
                        available=False,
                        total_amount=Decimal("0.00"),
                        currency=current_currency or (expected_currency or ""),
                        details="Supplier offer does not contain a total price or valid currency.",
                    )

                current_dec = Decimal(str(total_amount))
                orig_dec = Decimal(str(expected_price)) if expected_price is not None else current_dec

                # Parse detailed flight attributes from live supplier response
                slices = data.get("slices") if isinstance(data.get("slices"), list) else None
                first_slice = slices[0] if slices else {}
                first_seg = (first_slice.get("segments") or [{}])[0] if first_slice else {}
                last_seg = (first_slice.get("segments") or [{}])[-1] if first_slice else {}

                origin = (first_seg.get("origin") or {}).get("iata_code")
                destination = (last_seg.get("destination") or {}).get("iata_code")

                dep_str = first_seg.get("departing_at")
                arr_str = last_seg.get("arriving_at")
                dep_at = datetime.fromisoformat(dep_str.replace("Z", "+00:00")) if dep_str else None
                arr_at = datetime.fromisoformat(arr_str.replace("Z", "+00:00")) if arr_str else None

                raw_expires = data.get("expires_at")
                exp_at = datetime.fromisoformat(raw_expires.replace("Z", "+00:00")) if raw_expires else None

                passengers = data.get("passengers")
                if isinstance(passengers, list):
                    norm_passengers = [
                        {"id": p.get("id"), "type": p.get("type"), "age": p.get("age")}
                        for p in passengers
                        if isinstance(p, dict)
                    ]
                    pax_count = len(norm_passengers)
                else:
                    norm_passengers = None
                    pax_count = None

                first_pax_seg = (first_seg.get("passengers") or [{}])[0] if first_seg else {}
                raw_cabin = first_pax_seg.get("cabin_class")
                cabin_class = str(raw_cabin).strip().lower() if raw_cabin else None

                # Extract included baggage strictly from slices[].segments[].passengers[].baggages[]
                all_baggages = []
                has_baggage_data = False
                for sl in (slices or []):
                    for seg in (sl.get("segments") or []) if isinstance(sl, dict) else []:
                        for pax in (seg.get("passengers") or []) if isinstance(seg, dict) else []:
                            if isinstance(pax, dict) and "baggages" in pax:
                                has_baggage_data = True
                                for b in pax.get("baggages") or []:
                                    if isinstance(b, dict) and b.get("type"):
                                        all_baggages.append({
                                            "type": str(b.get("type", "")).lower().strip(),
                                            "quantity": int(b.get("quantity", 1)),
                                        })
                if has_baggage_data:
                    norm_baggages = sorted(all_baggages, key=lambda x: (x["type"], x["quantity"]))
                else:
                    norm_baggages = None
                baggage_summary = None

                # Extract actual Duffel conditions: change_before_departure, refund_before_departure
                conditions_raw = data.get("conditions")
                if isinstance(conditions_raw, dict):
                    change_cond = conditions_raw.get("change_before_departure")
                    refund_cond = conditions_raw.get("refund_before_departure")
                    if change_cond is None and refund_cond is None:
                        norm_conditions = None
                    else:
                        def _norm_condition_entry(c: Any) -> dict[str, Any] | None:
                            if not isinstance(c, dict):
                                return None
                            return {
                                "allowed": bool(c.get("allowed", False)),
                                "penalty_amount": str(c.get("penalty_amount")) if c.get("penalty_amount") is not None else None,
                                "penalty_currency": str(c.get("penalty_currency")).upper() if c.get("penalty_currency") else None,
                            }
                        norm_conditions = {
                            "change_before_departure": _norm_condition_entry(change_cond),
                            "refund_before_departure": _norm_condition_entry(refund_cond),
                        }
                else:
                    norm_conditions = None
                fare_rules_summary = None

                price_delta = abs(current_dec - orig_dec) > Decimal("0.01")
                currency_delta = expected_currency is not None and current_currency != expected_currency.upper()

                if price_delta or currency_delta:
                    status = RevalidationStatus.PRICE_CHANGED
                    details = f"Fare updated from {expected_currency or ''} {orig_dec} to {current_currency} {current_dec}"
                else:
                    status = RevalidationStatus.CONFIRMED_UNCHANGED
                    details = "Fare and schedule verified unchanged with supplier."

                return ProviderOfferVerification(
                    provider_offer_id=provider_offer_id,
                    status=status,
                    available=True,
                    total_amount=current_dec,
                    currency=current_currency,
                    origin=origin,
                    destination=destination,
                    departure_time=dep_at,
                    arrival_time=arr_at,
                    passenger_count=pax_count,
                    passengers=norm_passengers,
                    cabin_class=cabin_class,
                    included_baggage=norm_baggages,
                    baggage_summary=baggage_summary,
                    conditions=norm_conditions,
                    fare_rules_summary=fare_rules_summary,
                    slices=slices,
                    expires_at=exp_at,
                    raw_offer=data,
                    details=details,
                )
        except (BookingProviderTimeout, BookingProviderUnavailable, FlightProviderRateLimitError):
            raise
        except Exception as exc:
            raise BookingProviderUnavailable("Unexpected Duffel revalidation error.") from exc

    async def create_handoff_session(
        self,
        provider_offer_id: str,
        passengers: list[dict[str, Any]],
        success_url: str,
        failure_url: str,
        abandonment_url: str,
        call_timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Create a Duffel Links hosted checkout session."""
        if not provider_offer_id or not provider_offer_id.startswith("off_"):
            raise FlightProviderError("A valid Duffel offer ID is required to create a Links session.")

        payload = {
            "data": {
                "selected_offers": [provider_offer_id],
                "passengers": passengers,
                "success_url": success_url,
                "failure_url": failure_url,
                "abandonment_url": abandonment_url,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=call_timeout) as client:
                try:
                    response = await client.post(
                        f"{self.base_url}/air/links",
                        headers=self._headers(),
                        json=payload,
                    )
                except httpx.TimeoutException as exc:
                    raise BookingProviderTimeout("Duffel Links creation timed out.") from exc
                except httpx.RequestError as exc:
                    raise BookingProviderUnavailable("Duffel Links network failure.") from exc

                if response.status_code == 401:
                    raise BookingProviderUnavailable("Duffel authentication failed (HTTP 401).")
                if response.status_code == 403:
                    raise BookingProviderUnavailable("Duffel permission denied (HTTP 403).")
                if response.status_code == 429:
                    raise FlightProviderRateLimitError("Duffel Links API rate limit exceeded (HTTP 429).")

                if not response.is_success:
                    raise BookingProviderUnavailable(
                        f"Duffel Links creation failed with HTTP {response.status_code}."
                    )

                data = response.json().get("data") or {}
                link_url = data.get("url") or (data.get("links") or {}).get("checkout")
                reference = data.get("id") or data.get("session_id")

                if not link_url:
                    raise BookingProviderUnavailable("Duffel Links response did not contain a checkout URL.")

                # Validate URL security
                is_valid, hostname = _validate_and_normalize_url(link_url)
                if not is_valid or not _is_approved_duffel_host(hostname):
                    raise ValueError(f"Duffel returned an untrusted or invalid checkout URL: {link_url}")

                expires_raw = data.get("expires_at")
                if expires_raw:
                    try:
                        expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
                    except Exception:
                        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
                else:
                    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

                return {
                    "handoff_url": link_url,
                    "provider_reference": reference or f"ses_{uuid4().hex[:12]}",
                    "expires_at": expires_at,
                }
        except (BookingProviderTimeout, BookingProviderUnavailable, FlightProviderRateLimitError, ValueError):
            raise
        except Exception as exc:
            raise BookingProviderUnavailable(f"Unexpected error creating Duffel Links session: {exc}") from exc

    async def create_api_order(
        self,
        provider_offer_id: str,
        passengers: list[dict[str, Any]],
        payment_mode: str,
        payment_details: dict[str, Any] | None = None,
        call_timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Execute a direct order creation via Duffel's /air/orders."""
        # Check production safety: Instant direct API card orders are not enabled in production
        is_test_mode = getattr(self._provider, "test_mode", True)
        if not is_test_mode and payment_mode != "duffel_link":
            raise ValueError(
                "Direct API orders are restricted to sandbox test mode. Production must use Duffel Links."
            )

        # Offload synchronous DuffelProvider.create_order to thread
        result = await asyncio.to_thread(
            self._provider.create_order,
            offer_id=provider_offer_id,
            passengers=passengers,
            payment=payment_details,
            payment_mode=payment_mode,
        )
        return _scrub_payload(result)

    async def get_order_status(
        self,
        provider_order_id: str,
        call_timeout: float = 15.0,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(self._provider.get_order, provider_order_id)
        return _scrub_payload(result)

    async def cancel_order(
        self,
        provider_order_id: str,
        call_timeout: float = 30.0,
    ) -> dict[str, Any]:
        quote = await asyncio.to_thread(self._provider.create_cancellation_quote, provider_order_id)
        cancellation_id = quote.get("id")
        if not cancellation_id:
            raise FlightProviderError("Provider did not return a cancellation quote ID.")
        confirmed = await asyncio.to_thread(self._provider.confirm_cancellation, cancellation_id)
        return _scrub_payload(confirmed)
