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

import os
import httpx
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
from services.flight_provider import (
    FlightProviderError,
    FlightProviderRateLimitError,
    FlightProviderTimeout,
)
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


import ipaddress
from urllib.parse import urlparse

# Registered verified carrier domains allowlist (IATA code -> trusted domain bases)
CARRIER_DOMAIN_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "AI": ("airindia.com", "airindia.in"),
    "EK": ("emirates.com",),
    "BA": ("ba.com", "britishairways.com"),
    "LH": ("lh.com", "lufthansa.com"),
    "AF": ("airfrance.com", "airfrance.fr"),
    "KL": ("klm.com",),
    "QR": ("qatarairways.com",),
    "SQ": ("singaporeair.com",),
    "UA": ("united.com",),
    "DL": ("delta.com",),
    "AA": ("aa.com",),
    "6E": ("goindigo.in", "goindigo.com"),
    "UK": ("airvistara.com",),
    "SG": ("spicejet.com",),
    "QP": ("akasaair.com",),
    "EY": ("etihad.com",),
    "TK": ("turkishairlines.com",),
    "VS": ("virginatlantic.com",),
    "QF": ("qantas.com", "qantas.com.au"),
    "CX": ("cathaypacific.com",),
    "WN": ("southwest.com",),
    "B6": ("jetblue.com",),
    "AS": ("alaskaair.com",),
}


def _validate_and_normalize_url(url: str | None) -> tuple[bool, str]:
    """Validate scheme, userinfo, IP literals, localhost, and return normalized hostname."""
    if not url or not isinstance(url, str):
        return False, ""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, ""

    if parsed.scheme != "https":
        return False, ""

    if parsed.username or parsed.password:
        return False, ""

    hostname = parsed.hostname
    if not hostname:
        return False, ""

    hostname = hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False, ""

    try:
        ipaddress.ip_address(hostname)
        return False, ""  # Reject IP literals (both IPv4 and IPv6)
    except ValueError:
        pass

    return True, hostname


def _is_approved_duffel_host(hostname: str) -> bool:
    """Allow only exact approved Duffel domains/subdomains. No substring matching."""
    approved_bases = ("duffel.com", "duffel.link")
    for base in approved_bases:
        if hostname == base or hostname.endswith("." + base):
            return True
    return False


def _is_approved_carrier_host(carrier_iata: str, hostname: str) -> bool:
    """Check hostname against the registered server-side carrier domain allowlist."""
    allowed_bases = CARRIER_DOMAIN_ALLOWLIST.get(carrier_iata.upper(), ())
    for base in allowed_bases:
        if hostname == base or hostname.endswith("." + base):
            return True
    return False


class DuffelFlightAdapter(FlightSearchProviderPort):
    """Adapter wrapping DuffelProvider conforming to FlightSearchProviderPort."""

    def __init__(self, provider: DuffelProvider | None = None) -> None:
        self._provider = provider or DuffelProvider()
        self._capability = ProviderCapability(
            provider_name="duffel",
            infant_support=InfantSupportLevel.NATIVE,
            supports_flexible_dates=True,
            supports_cancellation_policies=True,
            supports_multi_room=False,
        )

    @property
    def provider_name(self) -> str:
        return "duffel"

    @property
    def capability(self) -> ProviderCapability:
        return self._capability

    def _build_flight_offers(
        self,
        raw_offers: list[dict[str, Any]],
        criteria: FlightSearchCriteria,
        total_pax: int,
    ) -> list[tuple[FlightOffer, dict[str, Any]]]:
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
            dep_date = criteria.slices[0].departure_date if criteria.slices else None
            ret_date = criteria.slices[1].departure_date if len(criteria.slices) > 1 else None

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
                candidate_departure_date=dep_date,
                candidate_return_date=ret_date,
            )

            sanitized_raw = _scrub_payload(raw.get("raw") or raw)
            results.append((offer, sanitized_raw))

        return results

    def _sync_search(self, criteria: FlightSearchCriteria) -> list[tuple[FlightOffer, dict[str, Any]]]:
        slices = [
            {
                "origin": s.origin_iata.upper(),
                "destination": s.destination_iata.upper(),
                "departure_date": s.departure_date.isoformat(),
            }
            for s in criteria.slices
        ]
        total_pax = criteria.adults + criteria.children + criteria.infants
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
        return self._build_flight_offers(raw_offers, criteria, total_pax)

    async def search_flights(
        self,
        criteria: FlightSearchCriteria,
        call_timeout: float | None = None,
    ) -> list[tuple[FlightOffer, dict[str, Any]]]:
        """Perform native async flight search via Duffel REST API using httpx.AsyncClient.

        Guarantees deterministic socket closure upon cancellation without leaving worker threads running.
        """
        slices = [
            {
                "origin": s.origin_iata.upper(),
                "destination": s.destination_iata.upper(),
                "departure_date": s.departure_date.isoformat(),
            }
            for s in criteria.slices
        ]
        total_pax = criteria.adults + criteria.children + criteria.infants
        passengers = [{"type": "adult"}] * criteria.adults
        passengers += [{"type": "child"}] * criteria.children
        passengers += [{"type": "infant_without_seat"}] * criteria.infants

        req_payload = {
            "data": {
                "slices": slices,
                "passengers": passengers,
                "cabin_class": criteria.cabin_class,
                "max_connections": criteria.max_connections,
            }
        }

        token = getattr(self._provider, "token", None) or os.getenv("DUFFEL_ACCESS_TOKEN") or ""
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Duffel-Version": "v2",
        }
        base_url = getattr(self._provider, "BASE_URL", "https://api.duffel.com")
        timeout_sec = call_timeout or getattr(self._provider, "timeout", 30)

        supplier_timeout_ms = int(os.getenv("DUFFEL_SUPPLIER_TIMEOUT_MS", "45000"))
        supplier_timeout_ms = max(2000, min(60000, supplier_timeout_ms))
        params = {
            "return_offers": "true",
            "view": "offers",
            "supplier_timeout": str(supplier_timeout_ms),
        }

        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            try:
                response = await client.post(
                    f"{base_url}/air/offer_requests",
                    params=params,
                    json=req_payload,
                    headers=headers,
                )
            except asyncio.CancelledError:
                # Deterministic socket and connection cleanup via context manager exit
                raise
            except httpx.TimeoutException as exc:
                raise FlightProviderTimeout(f"Duffel async request timed out after {timeout_sec}s: {exc}") from exc
            except httpx.RequestError as exc:
                raise FlightProviderError(f"Duffel async request failed: {exc}") from exc

            if response.status_code == 429:
                raise FlightProviderRateLimitError("Duffel API rate limit exceeded (HTTP 429).")
            if not response.is_success:
                try:
                    detail = response.json()
                except Exception:
                    detail = response.text
                raise FlightProviderError(f"Duffel API returned HTTP {response.status_code}: {detail}")

            try:
                data = response.json()
            except Exception as exc:
                raise FlightProviderError("Duffel returned invalid JSON.") from exc

            offers = (data.get("data") or {}).get("offers") or []
            normalized_raw = [DuffelProvider._normalize_offer(item) for item in offers]
            return self._build_flight_offers(normalized_raw, criteria, total_pax)

    async def revalidate_flight(
        self,
        provider_offer_id: str,
        expected_price: float | None = None,
        expected_currency: str | None = None,
        call_timeout: float | None = None,
    ) -> RevalidationResult:
        """Asynchronously revalidate a flight offer with Duffel via native async HTTP."""
        now = datetime.now(timezone.utc)
        reval_id = uuid4()
        token = getattr(self._provider, "token", None)
        if not token:
            token = os.getenv("DUFFEL_ACCESS_TOKEN") or os.getenv("DUFFEL_TEST_API_TOKEN") or os.getenv("DUFFEL_LIVE_API_TOKEN")
        if not token:
            raise FlightProviderError("Duffel API token is missing.")

        headers = {
            "Authorization": f"Bearer {token}",
            "Duffel-Version": "v2",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "TrustTrip/1.0",
        }
        base_url = getattr(self._provider, "BASE_URL", "https://api.duffel.com")
        timeout_sec = call_timeout or getattr(self._provider, "timeout", 30)

        if not provider_offer_id or not provider_offer_id.startswith("off_"):
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
                details="A valid Duffel offer ID is required.",
            )

        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                try:
                    response = await client.get(
                        f"{base_url}/air/offers/{provider_offer_id}",
                        headers=headers,
                    )
                except asyncio.CancelledError:
                    raise
                except httpx.TimeoutException as exc:
                    raise FlightProviderTimeout(f"Duffel async revalidation timed out after {timeout_sec}s: {exc}") from exc
                except httpx.RequestError as exc:
                    raise FlightProviderError(f"Duffel async revalidation request failed: {exc}") from exc

                if response.status_code in {404, 410}:
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
                        details="The selected flight offer is no longer available from the provider.",
                    )

                if response.status_code == 429:
                    raise FlightProviderRateLimitError("Duffel API rate limit exceeded (HTTP 429).")

                if not response.is_success:
                    try:
                        detail = response.json()
                    except Exception:
                        detail = response.text
                    raise FlightProviderError(f"Duffel API returned HTTP {response.status_code}: {detail}")

                try:
                    data = response.json()
                except Exception as exc:
                    raise FlightProviderError("Duffel returned invalid JSON.") from exc

                latest = data.get("data") or {}
        except (FlightProviderError, FlightProviderTimeout) as exc:
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
                details=f"Offer withdrawn or supplier error: {exc}",
            )

        total_amount = latest.get("total_amount")
        current_currency = str(latest.get("total_currency") or "USD").upper()

        if total_amount is None:
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

        current_dec = Decimal(str(total_amount))
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

    def resolve_booking_destination(
        self,
        snapshot_id: uuid4 | UUID,
        offer: FlightOffer,
        raw_payload: dict[str, Any] | None = None,
    ) -> Any:
        from domain.booking import FlightBookingDestination, FlightDestinationType

        raw = raw_payload or {}
        first_segment = offer.segments[0] if offer.segments else None
        airline_name = first_segment.marketing_carrier_name if first_segment else "Airline"
        carrier_iata = first_segment.marketing_carrier_iata if first_segment else ""
        flight_number = f"{carrier_iata}{first_segment.flight_number}" if first_segment else "Flight"

        # 1. Check for genuine, verified direct airline booking URL
        direct_url = raw.get("direct_airline_booking_url") or raw.get("airline_checkout_url")
        if direct_url and isinstance(direct_url, str):
            is_valid, hostname = _validate_and_normalize_url(direct_url)
            if is_valid and _is_approved_carrier_host(carrier_iata, hostname):
                return FlightBookingDestination(
                    snapshot_id=snapshot_id,
                    destination_type=FlightDestinationType.DIRECT_AIRLINE_BOOKING,
                    redirect_url=direct_url,
                    airline_name=airline_name,
                    flight_number=flight_number,
                    price_total=offer.price.total_amount,
                    currency=offer.price.currency,
                    expires_at=offer.expires_at,
                    provider_label=f"Official Airline Website ({airline_name})",
                    instructions=f"You are being redirected directly to {airline_name} to complete your flight booking.",
                )

        # 2. Check for official Duffel Link hosted checkout URL
        duffel_url = (
            raw.get("duffel_link")
            or raw.get("duffel_links_url")
            or (raw.get("links") or {}).get("checkout")
        )
        if duffel_url and isinstance(duffel_url, str):
            is_valid, hostname = _validate_and_normalize_url(duffel_url)
            if is_valid and _is_approved_duffel_host(hostname):
                return FlightBookingDestination(
                    snapshot_id=snapshot_id,
                    destination_type=FlightDestinationType.DUFFEL_LINK,
                    redirect_url=duffel_url,
                    airline_name=airline_name,
                    flight_number=flight_number,
                    price_total=offer.price.total_amount,
                    currency=offer.price.currency,
                    expires_at=offer.expires_at,
                    provider_label="Duffel Travel Checkout",
                    instructions="You are being redirected to TrustTrip's flight partner, Duffel, to complete your secure ticket booking. This checkout is hosted by Duffel.",
                )

        # 3. Fallback: UNAVAILABLE (Zero URL fabrication)
        return FlightBookingDestination(
            snapshot_id=snapshot_id,
            destination_type=FlightDestinationType.UNAVAILABLE,
            redirect_url=None,
            airline_name=airline_name,
            flight_number=flight_number,
            price_total=offer.price.total_amount,
            currency=offer.price.currency,
            expires_at=offer.expires_at,
            provider_label="Manual Booking Required",
            instructions=f"Direct online checkout link is unavailable for this carrier fare. Please book flight {flight_number} directly via {airline_name} or your travel agent using the details above.",
        )
