"""Flight Search Service implementing the Section 5A Backend Contract.

Derives search parameters strictly from the authentic canonical Trip,
queries the configured flight provider, shields raw provider identifiers,
normalizes flight offers into a stable representation, and persists snapshots.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import logging
import re
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from domain.flight_search import (
    FlightSearchMetadata,
    FlightSearchResponse,
    NormalizedFlightOffer,
    NormalizedFlightSegment,
)
from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FlightSegmentModel,
    FreshnessStatus,
    PriceBreakdown,
)
from domain.search_criteria import (
    FlightSearchCriteria,
    FlightSliceCriteria,
    compute_criteria_hash,
)
from services.flight_provider import (
    FlightProviderError,
    FlightProviderRateLimitError,
    FlightProviderTimeout,
)
from services.flight_service import FlightService
from services.search_session_store import SearchSessionStore
from services.travel_planner import _resolve_place

logger = logging.getLogger(__name__)


def _extract_airport_code(val: Any) -> str | None:
    """Resolve an airport code or place string into a 3-letter uppercase IATA code."""
    if not val or not isinstance(val, str):
        return None
    cleaned = val.strip().upper()
    if len(cleaned) == 3 and cleaned.isalpha():
        return cleaned
    code, _, _ = _resolve_place(val)
    if code and len(code) == 3 and code.isalpha():
        return code.upper()
    return None


def _format_duration(dur: Any) -> str | None:
    """Format duration into human-readable e.g. '2h 30m'."""
    if dur is None:
        return None
    s = str(dur).strip()
    if not s:
        return None
    # Parse ISO 8601 duration e.g. PT2H30M
    if "PT" in s or ("H" in s and "M" in s):
        h_match = re.search(r"(\d+)H", s)
        m_match = re.search(r"(\d+)M", s)
        hours = int(h_match.group(1)) if h_match else 0
        mins = int(m_match.group(1)) if m_match else 0
        if hours and mins:
            return f"{hours}h {mins}m"
        if hours:
            return f"{hours}h"
        if mins:
            return f"{mins}m"
    return s


def _parse_iso_datetime(val: Any) -> datetime | None:
    """Parse ISO datetime safely."""
    if not val:
        return None
    s = str(val).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _duration_minutes(value: Any, departure: Any = None, arrival: Any = None) -> int | None:
    """Read a provider ISO duration (or derive elapsed minutes from timestamps)."""
    if value is not None:
        text = str(value).strip()
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", text)
        if match and (match.group(1) is not None or match.group(2) is not None):
            return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
        if text.isdigit():
            return int(text)
    dep_dt = _parse_iso_datetime(departure)
    arr_dt = _parse_iso_datetime(arrival)
    if dep_dt is None or arr_dt is None:
        return None
    minutes = int((arr_dt - dep_dt).total_seconds() // 60)
    return minutes if minutes >= 0 else None


def resolve_canonical_trip_criteria(trip: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate canonical flight constraints from an authoritative Trip record.

    Fails closed with HTTPException(400) if required flight criteria are missing or invalid.
    """
    origin_raw = trip.get("origin") or ""
    dest_raw = trip.get("destination") or ""

    meta = trip.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    meta = dict(meta)

    constraints = meta.get("constraints") or {}
    if isinstance(constraints, str):
        try:
            constraints = json.loads(constraints)
        except Exception:
            constraints = {}
    constraints = dict(constraints)

    # 1. Resolve Origin Airport
    orig_candidate = (
        constraints.get("origin_airport")
        or constraints.get("origin_code")
        or meta.get("origin_airport")
        or meta.get("origin_code")
        or constraints.get("origin")
        or origin_raw
    )
    origin_iata = _extract_airport_code(orig_candidate)
    if not origin_iata:
        raise HTTPException(
            status_code=400,
            detail=f"Missing or invalid canonical origin airport for trip: '{orig_candidate or origin_raw}' cannot be resolved to an airport.",
        )

    # 2. Resolve Destination Airport
    dest_candidate = (
        constraints.get("destination_airport")
        or constraints.get("destination_code")
        or meta.get("destination_airport")
        or meta.get("destination_code")
        or constraints.get("destination")
        or dest_raw
    )
    dest_iata = _extract_airport_code(dest_candidate)
    if not dest_iata:
        raise HTTPException(
            status_code=400,
            detail=f"Missing or invalid canonical destination airport for trip: '{dest_candidate or dest_raw}' cannot be resolved to an airport.",
        )

    if origin_iata == dest_iata:
        raise HTTPException(
            status_code=400,
            detail="Origin and destination airports cannot be identical.",
        )

    # 3. Resolve & Validate Dates
    dep_raw = (
        constraints.get("departure_date")
        or constraints.get("start_date")
        or meta.get("departure_date")
        or meta.get("start_date")
        or trip.get("departure_date")
        or trip.get("start_date")
    )
    if not dep_raw:
        raise HTTPException(
            status_code=400,
            detail="Missing canonical departure date.",
        )

    try:
        dep_date = date.fromisoformat(str(dep_raw)[:10])
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid canonical departure date format (expected YYYY-MM-DD).",
        )

    if dep_date < date.today():
        raise HTTPException(
            status_code=400,
            detail="Canonical departure date cannot be in the past.",
        )

    ret_raw = (
        constraints.get("return_date")
        or constraints.get("end_date")
        or meta.get("return_date")
        or meta.get("end_date")
        or trip.get("return_date")
        or trip.get("end_date")
    )
    ret_date: date | None = None
    if ret_raw:
        try:
            ret_date = date.fromisoformat(str(ret_raw)[:10])
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid canonical return date format (expected YYYY-MM-DD).",
            )
        if ret_date < dep_date:
            raise HTTPException(
                status_code=400,
                detail="Canonical return date cannot precede departure date.",
            )

    # 4. Resolve & Validate Traveler Count
    def _first_not_none(*vals, default=None):
        for v in vals:
            if v is not None:
                return v
        return default

    adults_raw = _first_not_none(
        constraints.get("adults"),
        constraints.get("passengers"),
        constraints.get("travelers_count"),
        meta.get("adults"),
        meta.get("passengers"),
        meta.get("travelers_count"),
        default=1,
    )
    try:
        adults = int(adults_raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid traveler count (adults must be an integer).",
        )

    children_raw = _first_not_none(constraints.get("children"), meta.get("children"), default=0)
    infants_raw = _first_not_none(constraints.get("infants"), meta.get("infants"), default=0)
    try:
        children = int(children_raw)
        infants = int(infants_raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid traveler count (children and infants must be integers).",
        )

    total_travelers = adults + children + infants
    if adults < 1 or total_travelers > 9 or children < 0 or infants < 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid traveler count (must have at least 1 adult and at most 9 total travelers).",
        )

    # 5. Currency
    currency = (
        constraints.get("currency")
        or meta.get("currency")
        or trip.get("currency")
        or "INR"
    ).upper().strip()

    return {
        "origin_iata": origin_iata,
        "destination_iata": dest_iata,
        "departure_date": dep_date,
        "return_date": ret_date,
        "adults": adults,
        "children": children,
        "infants": infants,
        "total_travelers": total_travelers,
        "currency": currency,
    }


def normalize_provider_offer(
    offer: dict[str, Any],
    canonical_origin: str,
    canonical_dest: str,
    total_travelers: int,
) -> tuple[NormalizedFlightOffer, UUID, str]:
    """Transform a raw provider offer into a NormalizedFlightOffer.

    Returns:
        (normalized_offer, snapshot_id, provider_offer_id)
    """
    snapshot_id = uuid4()
    provider_offer_id = str(offer.get("id") or "")
    provider_name = str(offer.get("provider") or "duffel")

    raw = offer.get("raw") if isinstance(offer.get("raw"), dict) else offer
    slices = raw.get("slices") or []

    # Segments normalization
    raw_segments = offer.get("segments") or []
    norm_segments: list[NormalizedFlightSegment] = []
    segment_minutes: list[int | None] = []
    for s in raw_segments:
        departure = s.get("departure") or s.get("departing_at")
        arrival = s.get("arrival") or s.get("arriving_at")
        duration_minutes = _duration_minutes(s.get("duration"), departure, arrival)
        segment_minutes.append(duration_minutes)
        norm_segments.append(
            NormalizedFlightSegment(
                origin=str(s.get("origin") or "").upper(),
                origin_name=s.get("origin_name"),
                destination=str(s.get("destination") or "").upper(),
                destination_name=s.get("destination_name"),
                departure_time=str(s.get("departure") or s.get("departing_at") or ""),
                arrival_time=str(s.get("arrival") or s.get("arriving_at") or ""),
                duration=_format_duration(s.get("duration")) or (f"{duration_minutes // 60}h {duration_minutes % 60}m" if duration_minutes is not None else None),
                carrier=s.get("marketing_carrier") or s.get("operating_carrier") or offer.get("owner"),
                carrier_code=s.get("marketing_carrier_iata") or s.get("operating_carrier_iata") or offer.get("owner_iata"),
                flight_number=s.get("flight_number"),
            )
        )

    # Departure and arrival timestamps
    outbound_dep: str = ""
    outbound_arr: str = ""
    return_dep: str | None = None
    return_arr: str | None = None

    if len(slices) >= 2:
        # Structured round-trip slices
        s0_segs = slices[0].get("segments") or []
        s1_segs = slices[1].get("segments") or []
        if s0_segs:
            outbound_dep = str(s0_segs[0].get("departing_at") or s0_segs[0].get("departure") or "")
            outbound_arr = str(s0_segs[-1].get("arriving_at") or s0_segs[-1].get("arrival") or "")
        if s1_segs:
            return_dep = str(s1_segs[0].get("departing_at") or s1_segs[0].get("departure") or "")
            return_arr = str(s1_segs[-1].get("arriving_at") or s1_segs[-1].get("arrival") or "")
    elif norm_segments:
        # Infer outbound vs return from segments
        return_start_idx = None
        for idx, seg in enumerate(norm_segments):
            if idx > 0 and seg.origin == canonical_dest:
                return_start_idx = idx
                break

        if return_start_idx is not None:
            outbound_dep = norm_segments[0].departure_time
            outbound_arr = norm_segments[return_start_idx - 1].arrival_time
            return_dep = norm_segments[return_start_idx].departure_time
            return_arr = norm_segments[-1].arrival_time
        else:
            outbound_dep = norm_segments[0].departure_time
            outbound_arr = norm_segments[-1].arrival_time

    # Pricing
    total_price = float(offer.get("price") or 0.0)
    source_currency = str(offer.get("currency") or "INR").upper()
    price_inr = offer.get("price_inr")
    if price_inr is not None:
        try:
            price_inr = float(price_inr)
        except (ValueError, TypeError):
            price_inr = None

    price_per_traveler = round(total_price / max(1, total_travelers), 2) if total_price else None

    # Refundable / Changeable conditions
    refundable: bool | None = None
    changeable: bool | None = None
    conditions = raw.get("conditions") or {}
    if isinstance(conditions, dict):
        ref_cond = conditions.get("refund_before_departure")
        if isinstance(ref_cond, dict) and "allowed" in ref_cond:
            refundable = bool(ref_cond["allowed"])
        chg_cond = conditions.get("change_before_departure")
        if isinstance(chg_cond, dict) and "allowed" in chg_cond:
            changeable = bool(chg_cond["allowed"])

    if refundable is None and offer.get("refundable") is not None:
        refundable = bool(offer["refundable"])
    if changeable is None and offer.get("changeable") is not None:
        changeable = bool(offer["changeable"])

    # Carrier / Airline
    carrier = offer.get("owner")
    carrier_code = offer.get("owner_iata")
    if not carrier and norm_segments:
        carrier = norm_segments[0].carrier
        carrier_code = norm_segments[0].carrier_code

    # Duffel's slice duration covers the complete journey, including connection
    # time. Fall back to elapsed time across the outbound segment sequence.
    outbound_slice = (slices or [{}])[0]
    outbound_segs = outbound_slice.get("segments") or []
    itinerary_minutes = _duration_minutes(
        outbound_slice.get("duration"),
        outbound_segs[0].get("departing_at") if outbound_segs else (norm_segments[0].departure_time if norm_segments else None),
        outbound_segs[-1].get("arriving_at") if outbound_segs else (norm_segments[-1].arrival_time if norm_segments else None),
    )
    if itinerary_minutes is None and norm_segments:
        itinerary_minutes = _duration_minutes(None, norm_segments[0].departure_time, norm_segments[-1].arrival_time)
    duration_str = (f"{itinerary_minutes // 60}h {itinerary_minutes % 60}m" if itinerary_minutes is not None else None)

    normalized = NormalizedFlightOffer(
        offer_id=str(snapshot_id),
        provider=provider_name,
        origin=canonical_origin,
        destination=canonical_dest,
        departure_time=outbound_dep,
        arrival_time=outbound_arr,
        return_departure_time=return_dep,
        return_arrival_time=return_arr,
        duration=duration_str,
        stops=int(offer.get("stops") or 0),
        carrier=carrier,
        carrier_code=carrier_code,
        cabin_class=str(offer.get("cabin_class") or "economy"),
        price=total_price,
        currency=source_currency,
        price_inr=price_inr,
        price_per_traveler=price_per_traveler,
        refundable=refundable,
        changeable=changeable,
        expires_at=offer.get("expires_at"),
        segments=norm_segments,
    )

    return normalized, snapshot_id, provider_offer_id


async def search_flights_for_trip(
    trip: dict[str, Any],
    user_id: str | UUID,
    cabin_class: str = "economy",
    max_connections: int = 2,
    flight_service: FlightService | None = None,
    session_store: SearchSessionStore | None = None,
) -> FlightSearchResponse:
    """Execute canonical trip flight search adhering to the Section 5A backend contract."""
    # 1. Resolve & validate authoritative trip constraints
    criteria = resolve_canonical_trip_criteria(trip)
    orig = criteria["origin_iata"]
    dest = criteria["destination_iata"]
    dep_date: date = criteria["departure_date"]
    ret_date: date | None = criteria["return_date"]
    adults = criteria["adults"]
    children = criteria["children"]
    infants = criteria["infants"]
    total_travelers = criteria["total_travelers"]
    currency = criteria["currency"]

    # 2. Build provider slices and passengers
    slices = [
        {
            "origin": orig,
            "destination": dest,
            "departure_date": dep_date.isoformat(),
        }
    ]
    if ret_date:
        slices.append(
            {
                "origin": dest,
                "destination": orig,
                "departure_date": ret_date.isoformat(),
            }
        )

    passengers = [{"type": "adult"}] * adults
    if children > 0:
        passengers += [{"type": "child"}] * children
    if infants > 0:
        passengers += [{"type": "infant_without_seat"}] * infants

    search_context = {
        "slices": slices,
        "passengers": passengers,
        "cabin_class": cabin_class,
        "max_connections": max_connections,
    }

    # 3. Call existing flight provider abstraction
    svc = flight_service or FlightService()
    try:
        raw_result = await asyncio.to_thread(svc.search, search_context)
    except FlightProviderTimeout as exc:
        logger.warning("Flight provider timed out during search: %s", exc)
        raise HTTPException(status_code=504, detail="Flight provider request timed out.") from exc
    except FlightProviderRateLimitError as exc:
        logger.warning("Flight provider rate limit exceeded: %s", exc)
        raise HTTPException(status_code=429, detail="Flight provider rate limit exceeded.") from exc
    except FlightProviderError as exc:
        logger.warning("Flight provider error during search: %s", exc)
        raise HTTPException(status_code=502, detail=f"Flight provider is unavailable: {exc}") from exc
    except Exception as exc:
        logger.error("Unexpected flight search error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Flight provider search encountered an unexpected error.") from exc

    raw_offers = raw_result.get("offers") or []
    provider_name = str(raw_result.get("provider") or getattr(svc.provider, "name", "duffel"))

    # 4. Handle empty provider results cleanly
    if not raw_offers:
        metadata = FlightSearchMetadata(
            trip_id=str(trip["id"]),
            origin=orig,
            destination=dest,
            departure_date=dep_date.isoformat(),
            return_date=ret_date.isoformat() if ret_date else None,
            traveler_count=total_travelers,
            currency=currency,
            cabin_class=cabin_class,
            count=0,
        )
        return FlightSearchResponse(
            success=True,
            metadata=metadata,
            offers=[],
            provider=provider_name,
            availability_state="empty",
        )

    # 5. Persistence: initialize store — required for snapshot-backed offers
    store = session_store
    if store is None:
        try:
            store = SearchSessionStore()
        except Exception as exc:
            logger.error(
                "SearchSessionStore could not be initialized (PAYLOAD_ENCRYPTION_KEY may be missing): %s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Flight search persistence is unavailable (search session store could not be initialized). "
                    "Configure PAYLOAD_ENCRYPTION_KEY and retry."
                ),
            ) from exc

    # 6. Create the search session — required before persisting snapshots
    search_session_id = uuid4()
    try:
        criteria_obj = FlightSearchCriteria(
            slices=[
                FlightSliceCriteria(
                    origin_iata=orig,
                    destination_iata=dest,
                    departure_date=dep_date,
                )
            ] + (
                [
                    FlightSliceCriteria(
                        origin_iata=dest,
                        destination_iata=orig,
                        departure_date=ret_date,
                    )
                ] if ret_date else []
            ),
            adults=adults,
            children=children,
            infants=infants,
            cabin_class=cabin_class,
            max_connections=max_connections,
            currency=currency,
        )
        crit_hash = compute_criteria_hash(criteria_obj)
        session = store.create_session(
            user_id=user_id,
            trip_id=trip["id"],
            thread_id=None,
            search_type="flights",
            criteria_hash=crit_hash,
            trip_revision=trip.get("current_revision_number", 0),
        )
        search_session_id = session.session_id
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not persist search session: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Flight search persistence unavailable: search session could not be created.",
        ) from exc

    # 7. Normalize results and persist snapshots — ALL must succeed
    normalized_offers: list[NormalizedFlightOffer] = []
    now = datetime.now(timezone.utc)

    for offer in raw_offers:
        normalized_offer, snapshot_id, provider_offer_id = normalize_provider_offer(
            offer=offer,
            canonical_origin=orig,
            canonical_dest=dest,
            total_travelers=total_travelers,
        )

        # Snapshot persistence is mandatory: an offer is only returned when its
        # snapshot exists in storage so that the snapshot_id / offer_id is
        # a valid reference for Section 5B selection.
        try:
            # Build PriceBreakdown
            p_price = Decimal(str(normalized_offer.price))
            p_inr = Decimal(str(normalized_offer.price_inr)) if normalized_offer.price_inr is not None else None
            pb = PriceBreakdown(
                base_amount=p_price,
                tax_amount=Decimal("0.00"),
                fee_amount=Decimal("0.00"),
                total_amount=p_price,
                currency=normalized_offer.currency,
                inr_reference_amount=p_inr,
            )

            # Build FlightSegmentModel list
            seg_models: list[FlightSegmentModel] = []
            for seg in normalized_offer.segments:
                dep_dt = _parse_iso_datetime(seg.departure_time) or now
                arr_dt = _parse_iso_datetime(seg.arrival_time) or now
                seg_models.append(
                    FlightSegmentModel(
                        origin_iata=seg.origin,
                        destination_iata=seg.destination,
                        departure_time=dep_dt,
                        arrival_time=arr_dt,
                        marketing_carrier_iata=seg.carrier_code or "UNK",
                        marketing_carrier_name=seg.carrier or "Unknown",
                        flight_number=seg.flight_number or "",
                        duration_minutes=(
                            _duration_minutes(seg.duration, seg.departure_time, seg.arrival_time) or 0
                        ),
                        cabin_class=cabin_class,
                    )
                )

            exp_dt = _parse_iso_datetime(normalized_offer.expires_at) or datetime.fromtimestamp(now.timestamp() + 1800, tz=timezone.utc)

            domain_flight_offer = FlightOffer(
                snapshot_id=snapshot_id,
                provider=provider_name,
                provider_offer_id=provider_offer_id,
                price=pb,
                stops=normalized_offer.stops,
                segments=seg_models,
                passenger_count=total_travelers,
                retrieved_at=now,
                expires_at=exp_dt,
                freshness=FreshnessStatus.FRESH,
                availability=AvailabilityStatus.AVAILABLE,
                candidate_departure_date=dep_date,
                candidate_return_date=ret_date,
            )

            raw_payload = offer.get("raw") if isinstance(offer.get("raw"), dict) else offer
            store.save_snapshot(search_session_id, domain_flight_offer, raw_payload)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Offer snapshot persistence failed (session %s): %s",
                search_session_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Flight search persistence unavailable: one or more offer snapshots could not be saved.",
            ) from exc

        normalized_offers.append(normalized_offer)

    metadata = FlightSearchMetadata(
        trip_id=str(trip["id"]),
        origin=orig,
        destination=dest,
        departure_date=dep_date.isoformat(),
        return_date=ret_date.isoformat() if ret_date else None,
        traveler_count=total_travelers,
        currency=currency,
        cabin_class=cabin_class,
        count=len(normalized_offers),
    )

    return FlightSearchResponse(
        success=True,
        metadata=metadata,
        offers=normalized_offers,
        provider=provider_name,
        availability_state="available",
    )
