"""Travel Intelligence Orchestrator.

Orchestrates concurrent flight and hotel searches, unified snapshot identity assignment,
session criteria deduplication, and partial failure preservation.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

from domain.evidence import (
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceType,
    FreshnessKind,
)
from domain.live_offer import (
    AvailabilityStatus,
    CandidateDateSummary,
    FlexibleFlightSearchResponse,
    FlightOffer,
    FlightOfferView,
    FreshnessStatus,
    HotelOffer,
    OfferSnapshot,
    ProviderLimitationWarning,
    SearchSession,
)
from domain.search_criteria import (
    FlexibleFlightSearchCriteria,
    FlightSearchCriteria,
    FlightSliceCriteria,
    HotelSearchCriteria,
    compute_criteria_hash,
)
from services.evidence_policy import build_applicability_key
from services.evidence_store import EvidenceRevisionStore
from services.flight_provider import (
    FlightProviderError,
    FlightProviderRateLimitError,
    FlightProviderTimeout,
)
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
from services.travel_intelligence.liteapi_adapter import LiteAPIHotelAdapter
from services.travel_intelligence.ports import (
    FlightSearchProviderPort,
    HotelSearchProviderPort,
)
from services.travel_planner import candidate_dates


class TravelIntelligenceOrchestrator:
    """Application orchestrator for real-time travel intelligence."""

    def __init__(
        self,
        flight_adapter: FlightSearchProviderPort | None = None,
        hotel_adapter: HotelSearchProviderPort | None = None,
        session_store: SearchSessionStore | None = None,
    ) -> None:
        self.flight_adapter = flight_adapter or DuffelFlightAdapter()
        self.hotel_adapter = hotel_adapter or LiteAPIHotelAdapter()
        self.session_store = session_store or SearchSessionStore()

    def search_flights_sync(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: FlightSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[FlightOffer], list[str]]:
        """Synchronous execution of flight search with snapshot persistence."""
        criteria_hash = compute_criteria_hash(criteria)
        errors: list[str] = []

        session = self.session_store.create_session(
            user_id=user_id,
            trip_id=trip_id,
            thread_id=thread_id,
            search_type="flights",
            criteria_hash=criteria_hash,
            trip_revision=trip_revision,
        )

        try:
            raw_results = self.flight_adapter._sync_search(criteria) if hasattr(self.flight_adapter, "_sync_search") else []
        except Exception as exc:
            return [], [f"Flight search provider error: {exc}"]

        offers: list[FlightOffer] = []
        for idx, (offer, sanitized_raw) in enumerate(raw_results):
            authoritative_id = uuid4()
            unified_offer = offer.model_copy(
                update={
                    "snapshot_id": authoritative_id,
                    "is_recommended": idx == 0,
                    "recommendation_rank": idx + 1,
                }
            )
            self.session_store.save_snapshot(session.session_id, unified_offer, sanitized_raw)
            offers.append(unified_offer)

        return offers, errors

    async def search_flights(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: FlightSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[FlightOffer], list[str]]:
        """Async execution of flight search with timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.search_flights_sync,
                    user_id,
                    trip_id,
                    criteria,
                    thread_id,
                    trip_revision,
                    force_refresh,
                ),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            return [], ["Flight search timed out after 25 seconds."]

    def search_hotels_sync(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: HotelSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[HotelOffer], list[str]]:
        """Synchronous execution of hotel search with snapshot persistence."""
        criteria_hash = compute_criteria_hash(criteria)
        errors: list[str] = []

        session = self.session_store.create_session(
            user_id=user_id,
            trip_id=trip_id,
            thread_id=thread_id,
            search_type="hotels",
            criteria_hash=criteria_hash,
            trip_revision=trip_revision,
        )

        try:
            raw_results = self.hotel_adapter._sync_search(criteria) if hasattr(self.hotel_adapter, "_sync_search") else []
        except Exception as exc:
            return [], [f"Hotel search provider error: {exc}"]

        offers: list[HotelOffer] = []
        for idx, (offer, sanitized_raw) in enumerate(raw_results):
            authoritative_id = uuid4()
            unified_offer = offer.model_copy(
                update={
                    "snapshot_id": authoritative_id,
                    "is_recommended": idx == 0,
                    "recommendation_rank": idx + 1,
                }
            )
            self.session_store.save_snapshot(session.session_id, unified_offer, sanitized_raw)
            offers.append(unified_offer)

        return offers, errors

    async def search_hotels(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: HotelSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> tuple[list[HotelOffer], list[str]]:
        """Async execution of hotel search with timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.search_hotels_sync,
                    user_id,
                    trip_id,
                    criteria,
                    thread_id,
                    trip_revision,
                    force_refresh,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            return [], ["Hotel search timed out after 20 seconds."]

    async def search_all(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        flight_criteria: FlightSearchCriteria | None,
        hotel_criteria: HotelSearchCriteria | None,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Execute flight and hotel searches concurrently using asyncio.gather."""
        tasks = []
        if flight_criteria:
            tasks.append(
                self.search_flights(
                    user_id=user_id,
                    trip_id=trip_id,
                    criteria=flight_criteria,
                    thread_id=thread_id,
                    trip_revision=trip_revision,
                    force_refresh=force_refresh,
                )
            )
        else:
            tasks.append(asyncio.sleep(0, result=([], [])))

        if hotel_criteria:
            tasks.append(
                self.search_hotels(
                    user_id=user_id,
                    trip_id=trip_id,
                    criteria=hotel_criteria,
                    thread_id=thread_id,
                    trip_revision=trip_revision,
                    force_refresh=force_refresh,
                )
            )
        else:
            tasks.append(asyncio.sleep(0, result=([], [])))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        flight_res = results[0] if not isinstance(results[0], Exception) else ([], [str(results[0])])
        hotel_res = results[1] if not isinstance(results[1], Exception) else ([], [str(results[1])])

        flight_offers, flight_errors = flight_res
        hotel_offers, hotel_errors = hotel_res

        return {
            "flight_offers": flight_offers,
            "flight_errors": flight_errors,
            "hotel_offers": hotel_offers,
            "hotel_errors": hotel_errors,
            "partial_success": bool(flight_offers and hotel_errors or hotel_offers and flight_errors),
        }

    def _build_flexible_response(
        self,
        session_id: UUID,
        snapshots: list[OfferSnapshot],
        candidate_window_list: list[tuple[str, str | None]],
        currency: str = "USD",
        errors: list[str] | None = None,
    ) -> FlexibleFlightSearchResponse:
        """Group offers by candidate date, deduplicate, rank, and project to public views."""
        offers_by_dep: dict[str, list[FlightOffer]] = {}
        for snap in snapshots:
            if snap.offer_type != "flight":
                continue
            offer = snap.normalized_offer
            if not isinstance(offer, FlightOffer):
                continue

            dep_str = None
            if offer.candidate_departure_date:
                dep_str = offer.candidate_departure_date.isoformat()
            elif offer.segments:
                dep_str = offer.segments[0].departure_time.date().isoformat()

            if dep_str:
                offers_by_dep.setdefault(dep_str, []).append(offer)

        candidate_summaries: list[CandidateDateSummary] = []
        views_by_date: dict[str, list[FlightOfferView]] = {}
        all_views: list[FlightOfferView] = []

        for dep_str, ret_str in candidate_window_list:
            dep_date = date.fromisoformat(dep_str)
            ret_date = date.fromisoformat(ret_str) if ret_str else None
            cand_offers = offers_by_dep.get(dep_str, [])

            # Deduplicate offers within this candidate date by flight signature
            seen_signatures = set()
            deduped_offers: list[FlightOffer] = []
            sorted_by_price = sorted(cand_offers, key=lambda o: (o.price.total_amount, o.stops))
            for o in sorted_by_price:
                sig = tuple(
                    (s.marketing_carrier_iata, s.flight_number, s.departure_time.isoformat(), s.arrival_time.isoformat())
                    for s in o.segments
                )
                if sig not in seen_signatures:
                    seen_signatures.add(sig)
                    deduped_offers.append(o)

            # Assign deterministic rank within this candidate date
            for idx, o in enumerate(deduped_offers):
                deduped_offers[idx] = o.model_copy(
                    update={
                        "is_recommended": idx == 0,
                        "recommendation_rank": idx + 1,
                    }
                )

            lowest_price = deduped_offers[0].price.total_amount if deduped_offers else None
            status = "ok" if deduped_offers else "no_offers"

            candidate_summaries.append(
                CandidateDateSummary(
                    departure_date=dep_date,
                    return_date=ret_date,
                    offer_count=len(deduped_offers),
                    lowest_price=lowest_price,
                    currency=currency,
                    status=status,
                    error_message=None,
                )
            )

            views = [o.to_client_view() for o in deduped_offers]
            views_by_date[dep_str] = views
            all_views.extend(views)

        cheapest_view = min(all_views, key=lambda v: v.price.total_amount) if all_views else None
        recommended_view = None
        if all_views:
            rank_1_views = [v for v in all_views if v.recommendation_rank == 1]
            recommended_view = min(rank_1_views, key=lambda v: v.price.total_amount) if rank_1_views else all_views[0]

        has_any_offers = bool(all_views)
        has_errors = bool(errors)
        success = has_any_offers or not has_errors

        return FlexibleFlightSearchResponse(
            success=success,
            session_id=session_id,
            candidate_dates=candidate_summaries,
            offers_by_date=views_by_date,
            cheapest_offer=cheapest_view,
            recommended_offer=recommended_view,
            errors=errors or [],
        )

    def _validate_reusable_cached_snapshots(
        self,
        session: SearchSession,
        snapshots: list[OfferSnapshot],
        user_id: UUID | str,
        trip_id: UUID | str,
        trip_revision: int,
        criteria: FlexibleFlightSearchCriteria,
        candidate_window_list: list[tuple[str, str | None]],
    ) -> list[OfferSnapshot] | None:
        """Verify cached session and snapshots satisfy ALL cache-reuse invariants:

        1. session itself is active
        2. session has not exceeded cache-reuse TTL
        3. session trip_revision matches requested trip_revision
        4. evidence record linked to criteria applicability key is fresh and not invalidated
        5. all returned snapshots are AVAILABLE (not UNAVAILABLE or SOLD_OUT)
        6. all returned snapshots are FRESH (not STALE or EXPIRED)
        7. all returned snapshots are unexpired (expires_at > now where known)
        8. candidate dates are adequately covered
        """
        now = datetime.now(timezone.utc)
        if session.status != "active":
            return None
        if session.session_reuse_expires_at <= now:
            return None
        if session.trip_revision != trip_revision:
            return None

        # Verify evidence record validity for this revision
        ev_store = EvidenceRevisionStore()
        app_key = build_applicability_key(
            EvidenceType.FLIGHT,
            {
                "origin": criteria.origin_iata,
                "destination": criteria.destination_iata,
                "departure_date": criteria.departure_date.isoformat(),
                "return_date": criteria.return_date.isoformat() if criteria.return_date else "",
                "adults": criteria.adults,
                "children": criteria.children,
                "infants": criteria.infants,
                "cabin_class": criteria.cabin_class,
                "max_connections": criteria.max_connections,
                "currency": criteria.currency,
                "flexibility_mode": criteria.flexibility.mode.value,
                "flexibility_days": criteria.flexibility.flexibility_days,
                "duration_days": criteria.duration_days,
                "earliest_departure": criteria.flexibility.earliest_departure.isoformat() if criteria.flexibility.earliest_departure else "",
                "latest_departure": criteria.flexibility.latest_departure.isoformat() if criteria.flexibility.latest_departure else "",
                "candidate_dates": [d for d, _ in candidate_window_list],
            },
        )
        ev_rec = ev_store.find_reusable_evidence(trip_id, user_id, EvidenceType.FLIGHT, app_key, now=now)
        if not ev_rec:
            # Evidence was invalidated or not fresh
            return None

        usable_snaps: list[OfferSnapshot] = []
        for snap in snapshots:
            if snap.availability != AvailabilityStatus.AVAILABLE:
                continue
            if snap.freshness != FreshnessStatus.FRESH:
                continue
            if snap.expires_at and snap.expires_at <= now:
                continue
            norm = snap.normalized_offer
            if hasattr(norm, "availability") and norm.availability != AvailabilityStatus.AVAILABLE:
                continue
            if hasattr(norm, "freshness") and norm.freshness != FreshnessStatus.FRESH:
                continue
            if hasattr(norm, "expires_at") and norm.expires_at and norm.expires_at <= now:
                continue
            usable_snaps.append(snap)

        if not usable_snaps:
            return None

        # Check candidate date coverage
        cached_dates = set()
        for s in usable_snaps:
            norm = s.normalized_offer
            if hasattr(norm, "candidate_departure_date") and norm.candidate_departure_date:
                cached_dates.add(norm.candidate_departure_date.isoformat())
            elif getattr(norm, "segments", None):
                cached_dates.add(norm.segments[0].departure_time.date().isoformat())

        req_dates = set(d for d, _ in candidate_window_list)
        if not req_dates.issubset(cached_dates) or cached_dates != req_dates:
            return None

        return usable_snaps

    async def search_flexible_flights(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria: FlexibleFlightSearchCriteria,
        thread_id: str | None = None,
        trip_revision: int = 0,
        force_refresh: bool = False,
        global_timeout: float = 35.0,
    ) -> FlexibleFlightSearchResponse:
        """Execute flexible live flight search across candidate dates with bounded concurrency.

        Guarantees:
        1. Strict global deadline - retries consume remaining budget; no orphan background tasks.
        2. Bounded concurrency <= 3 simultaneous outgoing supplier requests.
        3. Double-checked cache reuse with advisory lock for (user_id, trip_id, criteria_hash).
        4. Partial failure tolerance - successful candidate dates are retained.
        5. Immutable OfferSnapshot and EvidenceRecord persistence.
        6. Internal provider_offer_id strictly shielded from views.
        """
        # 1. Candidate Date Generation
        constraints = {
            "departure_date": criteria.departure_date.isoformat(),
            "return_date": criteria.return_date.isoformat() if criteria.return_date else None,
            "duration_days": criteria.duration_days,
            "flexibility_mode": criteria.flexibility.mode.value,
            "flexibility_days": criteria.flexibility.flexibility_days,
            "earliest_departure": criteria.flexibility.earliest_departure.isoformat() if criteria.flexibility.earliest_departure else None,
            "latest_departure": criteria.flexibility.latest_departure.isoformat() if criteria.flexibility.latest_departure else None,
        }
        candidate_window_list = candidate_dates(constraints, limit=7)
        if not candidate_window_list:
            return FlexibleFlightSearchResponse(
                success=False,
                session_id=uuid4(),
                candidate_dates=[],
                offers_by_date={},
                errors=["No valid candidate travel dates could be generated."],
            )

        criteria_hash = compute_criteria_hash(criteria)

        # 2. Fast-path cache check before locking (strictly verifying revision, freshness, and usability)
        if not force_refresh:
            active_session = self.session_store.get_active_session_by_hash(
                user_id, trip_id, criteria_hash, trip_revision=trip_revision
            )
            if active_session:
                cached_snaps = self.session_store.get_snapshots_for_session(active_session.session_id, user_id)
                valid_snaps = self._validate_reusable_cached_snapshots(
                    active_session, cached_snaps, user_id, trip_id, trip_revision, criteria, candidate_window_list
                )
                if valid_snaps:
                    return self._build_flexible_response(
                        active_session.session_id, valid_snaps, candidate_window_list, currency=criteria.currency
                    )

        # 3. Enter cross-worker search lock to prevent duplicate concurrent searches
        async with self.session_store.search_lock(user_id, trip_id, criteria_hash):
            # Double-check inside lock
            if not force_refresh:
                active_session = self.session_store.get_active_session_by_hash(
                    user_id, trip_id, criteria_hash, trip_revision=trip_revision
                )
                if active_session:
                    cached_snaps = self.session_store.get_snapshots_for_session(active_session.session_id, user_id)
                    valid_snaps = self._validate_reusable_cached_snapshots(
                        active_session, cached_snaps, user_id, trip_id, trip_revision, criteria, candidate_window_list
                    )
                    if valid_snaps:
                        return self._build_flexible_response(
                            active_session.session_id, valid_snaps, candidate_window_list, currency=criteria.currency
                        )

            # 4. Global deadline and bounded concurrency pool
            loop = asyncio.get_running_loop()
            deadline = loop.time() + global_timeout
            cancel_event = asyncio.Event()
            semaphore = asyncio.Semaphore(3)
            errors: list[str] = []

            async def _worker(dep_str: str, ret_str: str | None) -> tuple[str, str | None, list[tuple[FlightOffer, dict[str, Any]]], str | None]:
                async with semaphore:
                    if cancel_event.is_set():
                        return (dep_str, ret_str, [], "Search cancelled.")
                    now = loop.time()
                    remaining = deadline - now
                    if remaining <= 0.1:
                        return (dep_str, ret_str, [], "Global search deadline exceeded before dispatch.")

                    slices = [
                        FlightSliceCriteria(
                            origin_iata=criteria.origin_iata.upper(),
                            destination_iata=criteria.destination_iata.upper(),
                            departure_date=date.fromisoformat(dep_str),
                        )
                    ]
                    if ret_str:
                        slices.append(
                            FlightSliceCriteria(
                                origin_iata=criteria.destination_iata.upper(),
                                destination_iata=criteria.origin_iata.upper(),
                                departure_date=date.fromisoformat(ret_str),
                            )
                        )
                    cand_criteria = FlightSearchCriteria(
                        slices=slices,
                        adults=criteria.adults,
                        children=criteria.children,
                        infants=criteria.infants,
                        cabin_class=criteria.cabin_class,
                        max_connections=criteria.max_connections,
                        currency=criteria.currency,
                    )

                    attempt = 0
                    while not cancel_event.is_set():
                        attempt += 1
                        now = loop.time()
                        rem = deadline - now
                        if rem <= 0.1:
                            return (dep_str, ret_str, [], "Global search deadline exceeded.")

                        call_timeout = min(15.0, rem)
                        try:
                            if hasattr(self.flight_adapter, "search_flights"):
                                search_fn = self.flight_adapter.search_flights
                                try:
                                    sig = inspect.signature(search_fn)
                                    accepts_timeout = "call_timeout" in sig.parameters or any(
                                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                                    )
                                except Exception:
                                    accepts_timeout = True

                                if accepts_timeout:
                                    coro_or_res = search_fn(cand_criteria, call_timeout=call_timeout)
                                else:
                                    coro_or_res = search_fn(cand_criteria)

                                if asyncio.iscoroutine(coro_or_res):
                                    raw_results = await asyncio.wait_for(coro_or_res, timeout=call_timeout)
                                else:
                                    raw_results = coro_or_res
                            else:
                                raw_results = await asyncio.to_thread(self.flight_adapter._sync_search, cand_criteria)
                            return (dep_str, ret_str, raw_results, None)
                        except (asyncio.CancelledError, KeyboardInterrupt):
                            cancel_event.set()
                            raise
                        except FlightProviderRateLimitError as exc:
                            logger.warning("Provider rate limit for date %s: %s", dep_str, exc)
                            if cancel_event.is_set():
                                return (dep_str, ret_str, [], "Cancelled.")
                            rem_after = deadline - loop.time()
                            if attempt <= 2 and rem_after > 1.0:
                                backoff = min(rem_after - 0.2, 0.3 * (2 ** attempt) + random.uniform(0.05, 0.15))
                                if backoff > 0:
                                    try:
                                        await asyncio.wait_for(cancel_event.wait(), timeout=backoff)
                                        return (dep_str, ret_str, [], "Cancelled during retry backoff.")
                                    except asyncio.TimeoutError:
                                        continue
                            return (dep_str, ret_str, [], "Flight provider rate limit exceeded (HTTP 429).")
                        except (FlightProviderTimeout, asyncio.TimeoutError) as exc:
                            logger.warning("Provider timeout for date %s: %s", dep_str, exc)
                            if cancel_event.is_set():
                                return (dep_str, ret_str, [], "Cancelled.")
                            rem_after = deadline - loop.time()
                            if attempt <= 1 and rem_after > 2.0:
                                try:
                                    await asyncio.wait_for(cancel_event.wait(), timeout=0.2)
                                    return (dep_str, ret_str, [], "Cancelled during retry wait.")
                                except asyncio.TimeoutError:
                                    continue
                            return (dep_str, ret_str, [], "Timeout contacting flight provider.")
                        except Exception as exc:
                            logger.error("Flight provider error for date %s: %s", dep_str, exc, exc_info=True)
                            return (dep_str, ret_str, [], "Flight provider encountered an error.")
                    return (dep_str, ret_str, [], "Search aborted.")

            tasks = [asyncio.create_task(_worker(d, r)) for d, r in candidate_window_list]
            rem_global = max(0.1, deadline - loop.time())
            try:
                raw_worker_results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=rem_global,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                cancel_event.set()
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                errors.append("Global search deadline exceeded.")
                raw_worker_results = []

            # 5. Process candidate worker outputs
            all_saved_offers: list[FlightOffer] = []
            candidate_errors: dict[str, str] = {}

            session = self.session_store.create_session(
                user_id=user_id,
                trip_id=trip_id,
                thread_id=thread_id,
                search_type="flights",
                criteria_hash=criteria_hash,
                trip_revision=trip_revision,
            )

            saved_snapshots: list[OfferSnapshot] = []

            for res in raw_worker_results:
                if isinstance(res, BaseException):
                    errors.append(str(res))
                    continue
                dep_str, ret_str, raw_offers, err = res
                if err:
                    candidate_errors[dep_str] = err
                    errors.append(f"Date {dep_str}: {err}")
                else:
                    dep_date_val = date.fromisoformat(dep_str)
                    ret_date_val = date.fromisoformat(ret_str) if ret_str else None
                    for offer, sanitized_raw in raw_offers:
                        authoritative_id = uuid4()
                        unified = offer.model_copy(
                            update={
                                "snapshot_id": authoritative_id,
                                "candidate_departure_date": dep_date_val,
                                "candidate_return_date": ret_date_val,
                            }
                        )
                        snap = self.session_store.save_snapshot(session.session_id, unified, sanitized_raw)
                        saved_snapshots.append(snap)
                        all_saved_offers.append(unified)

            # 6. Check for total failure
            if not all_saved_offers and candidate_errors and len(candidate_errors) == len(candidate_window_list):
                return FlexibleFlightSearchResponse(
                    success=False,
                    session_id=session.session_id,
                    candidate_dates=[
                        CandidateDateSummary(
                            departure_date=date.fromisoformat(d),
                            return_date=date.fromisoformat(r) if r else None,
                            offer_count=0,
                            lowest_price=None,
                            currency=criteria.currency,
                            status="provider_error",
                            error_message=candidate_errors.get(d),
                        )
                        for d, r in candidate_window_list
                    ],
                    offers_by_date={},
                    cheapest_offer=None,
                    recommended_offer=None,
                    errors=errors,
                )

            # 7. Record Phase 6 EvidenceRecord and EvidenceProvenance
            ev_store = EvidenceRevisionStore()
            applicability_key = build_applicability_key(
                EvidenceType.FLIGHT,
                {
                    "origin": criteria.origin_iata,
                    "destination": criteria.destination_iata,
                    "departure_date": criteria.departure_date.isoformat(),
                    "return_date": criteria.return_date.isoformat() if criteria.return_date else "",
                    "adults": criteria.adults,
                    "children": criteria.children,
                    "infants": criteria.infants,
                    "cabin_class": criteria.cabin_class,
                    "max_connections": criteria.max_connections,
                    "currency": criteria.currency,
                    "flexibility_mode": criteria.flexibility.mode.value,
                    "flexibility_days": criteria.flexibility.flexibility_days,
                    "duration_days": criteria.duration_days,
                    "earliest_departure": criteria.flexibility.earliest_departure.isoformat() if criteria.flexibility.earliest_departure else "",
                    "latest_departure": criteria.flexibility.latest_departure.isoformat() if criteria.flexibility.latest_departure else "",
                    "candidate_dates": [d for d, _ in candidate_window_list],
                },
            )

            cheapest_offer = min(all_saved_offers, key=lambda o: o.price.total_amount) if all_saved_offers else None
            top_snapshot_id = cheapest_offer.snapshot_id if cheapest_offer else None

            rec = EvidenceRecord(
                trip_id=UUID(str(trip_id)),
                user_id=UUID(str(user_id)),
                evidence_type=EvidenceType.FLIGHT,
                applicability_key=applicability_key,
                freshness_kind=FreshnessKind.LIVE_SUPPLIER,
                freshness_ttl_seconds=1800,
                source_agent="flight_agent",
                payload={
                    "session_id": str(session.session_id),
                    "candidate_dates_count": len(candidate_window_list),
                    "total_offers": len(all_saved_offers),
                    "cheapest_price": float(cheapest_offer.price.total_amount) if cheapest_offer else None,
                    "currency": criteria.currency,
                },
            )
            prov = EvidenceProvenance(
                evidence_id=rec.evidence_id,
                trip_id=UUID(str(trip_id)),
                user_id=UUID(str(user_id)),
                supplier_session_id=session.session_id,
                supplier_snapshot_id=top_snapshot_id,
            )
            ev_store.create_evidence(rec, prov)

            # 8. Build client response
            response = self._build_flexible_response(
                session.session_id,
                saved_snapshots,
                candidate_window_list,
                currency=criteria.currency,
                errors=errors,
            )
            for cs in response.candidate_dates:
                d_str = cs.departure_date.isoformat()
                if d_str in candidate_errors and cs.offer_count == 0:
                    object.__setattr__(cs, "status", "provider_error")
                    object.__setattr__(cs, "error_message", candidate_errors[d_str])

            return response
