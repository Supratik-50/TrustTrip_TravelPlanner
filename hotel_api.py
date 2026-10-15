from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies.auth import get_current_user
from domain.live_offer import (
    AvailabilityStatus,
    FreshnessStatus,
    HotelOffer,
    PriceBreakdown,
)
from domain.user import UserResponse
from services.hotel.hotel_provider import HotelProviderError
from services.hotel.hotel_service import HotelService
from services.search_session_store import SearchSessionStore
from services.trip_store import (
    OfferSelectionError,
    RevisionContextError,
    TripStore,
    resolve_current_revision,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hotels", tags=["hotels"])


def _trip_store() -> TripStore:
    return TripStore()


def _scrub_payload(payload: dict[str, Any]) -> dict[str, Any]:
    import json
    data = json.loads(json.dumps(payload, default=str))
    sensitive = {"x-api-key", "api_key", "secret", "token", "password", "client_secret"}

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: "REDACTED" if k.lower() in sensitive else _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(x) for x in obj]
        return obj

    return _clean(data)


class HotelSearchRequest(BaseModel):
    trip_id: str | UUID | None = None
    destination: str | None = None
    city: str | None = None
    checkin: str | None = None
    checkout: str | None = None
    check_in_date: str | None = None
    check_out_date: str | None = None
    adults: int = Field(default=1, ge=1, le=12)
    guests: int | None = None
    children: int = Field(default=0, ge=0, le=12)
    rooms: int = Field(default=1, ge=1, le=8)
    rooms_count: int | None = None
    budget_inr: float | None = Field(default=None, gt=0)
    cheaper_mode: bool = False
    hotelbeds_destination_code: str | None = None


class HotelSelectRequest(BaseModel):
    trip_id: str | UUID
    snapshot_id: UUID
    room_id: UUID | str | None = None


class HotelPrebookRequest(BaseModel):
    trip_id: str | UUID
    snapshot_id: UUID | str | None = None
    hotel_id: str | None = None
    room_id: str | None = None
    check_in_date: str | None = None
    check_out_date: str | None = None
    guests: int | list[dict[str, Any]] | None = None
    guest_name: str | None = None
    guest_email: str | None = None
    offer: dict[str, Any] | None = None


@router.get("/config")
async def hotel_config():
    return {
        "success": True,
        "providers": {
            "liteapi": bool(os.getenv("LITEAPI_SANDBOX_API_KEY") or os.getenv("LITEAPI_PRODUCTION_API_KEY")),
            "hotelbeds": bool(os.getenv("HOTELBEDS_API_KEY") and os.getenv("HOTELBEDS_SECRET")),
        },
        "liteapi_mode": os.getenv("LITEAPI_MODE", "sandbox"),
        "hotelbeds_mode": os.getenv("HOTELBEDS_MODE", "test"),
        "razorpay_configured": bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET")),
    }


@router.post("/search")
async def search_hotels(
    req: HotelSearchRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Search hotels for canonical trip, normalize results, and persist snapshots."""
    if not req.trip_id or not str(req.trip_id).strip():
        raise HTTPException(
            status_code=400,
            detail="trip_id is required for hotel search.",
        )

    trip = trip_store.get_trip(req.trip_id, current_user.id)
    if not trip:
        # Check for cross-user / unauthorized access
        tid_str = str(req.trip_id)
        unowned = False
        if trip_store.use_in_memory or not os.getenv("DATABASE_URL"):
            unowned = tid_str in trip_store._memory_trips
        else:
            try:
                with trip_store._connect() as conn:
                    row = conn.execute("SELECT user_id FROM trips WHERE id = %s", (tid_str,)).fetchone()
                    unowned = bool(row)
            except Exception:
                unowned = False
        if unowned:
            raise HTTPException(status_code=403, detail="Unauthorized: Trip does not belong to you.")
        raise HTTPException(status_code=404, detail="Trip not found.")

    meta = trip.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = {}
    meta = dict(meta)

    constraints = meta.get("constraints") or {}
    if isinstance(constraints, str):
        try:
            import json
            constraints = json.loads(constraints)
        except Exception:
            constraints = {}
    constraints = dict(constraints)

    # 1. Canonical destination: strictly from trip, never client request fallback
    canonical_dest = (
        trip.get("destination")
        or constraints.get("destination")
        or meta.get("destination")
        or ""
    )
    if not canonical_dest or len(str(canonical_dest).strip()) < 2:
        raise HTTPException(
            status_code=400,
            detail="Missing canonical destination for trip.",
        )
    destination = str(canonical_dest).strip()

    # 2. Canonical check-in date: strictly from trip, never client request fallback
    canonical_checkin = (
        constraints.get("checkin_date")
        or constraints.get("departure_date")
        or constraints.get("start_date")
        or meta.get("checkin_date")
        or meta.get("departure_date")
        or meta.get("start_date")
        or trip.get("start_date")
        or trip.get("departure_date")
    )
    if not canonical_checkin:
        raise HTTPException(
            status_code=400,
            detail="Missing canonical check-in date for trip.",
        )

    # 3. Canonical check-out date: strictly from trip, never client request fallback
    canonical_checkout = (
        constraints.get("checkout_date")
        or constraints.get("return_date")
        or constraints.get("end_date")
        or meta.get("checkout_date")
        or meta.get("return_date")
        or meta.get("end_date")
        or trip.get("end_date")
        or trip.get("return_date")
    )
    if not canonical_checkout:
        raise HTTPException(
            status_code=400,
            detail="Missing canonical check-out date for trip.",
        )

    # 4. Canonical traveler/adult count: strictly from trip, never client request fallback
    canonical_adults = (
        constraints.get("travelers_count")
        or constraints.get("adults")
        or meta.get("travelers_count")
        or meta.get("adults")
        or trip.get("travelers_count")
        or trip.get("adults")
    )
    if canonical_adults is None:
        raise HTTPException(
            status_code=400,
            detail="Missing canonical traveler count for trip.",
        )
    try:
        adults = int(canonical_adults)
        if adults < 1 or adults > 12:
            raise ValueError()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid canonical traveler count for trip (must be between 1 and 12).",
        )

    # 4b. Canonical child count: strictly from trip, default 0 when omitted
    canonical_children = (
        constraints.get("children")
        if constraints.get("children") is not None
        else constraints.get("children_count")
        if constraints.get("children_count") is not None
        else constraints.get("child_count")
        if constraints.get("child_count") is not None
        else meta.get("children")
        if meta.get("children") is not None
        else meta.get("children_count")
        if meta.get("children_count") is not None
        else trip.get("children")
        if trip.get("children") is not None
        else trip.get("children_count")
        if trip.get("children_count") is not None
        else 0
    )
    try:
        children = int(canonical_children)
        if children < 0 or children > 12:
            raise ValueError()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid canonical children count for trip (must be an integer between 0 and 12).",
        )

    # 5. Canonical rooms count: strictly from trip, default 1 when omitted
    canonical_rooms = (
        constraints.get("rooms")
        or constraints.get("rooms_count")
        or meta.get("rooms")
        or meta.get("rooms_count")
        or trip.get("rooms")
        or trip.get("rooms_count")
        or 1
    )
    try:
        rooms = int(canonical_rooms)
        if rooms < 1 or rooms > 8:
            raise ValueError()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid canonical rooms count for trip (must be between 1 and 8).",
        )

    # 6. Canonical budget: strictly from trip, never client request override
    canonical_budget = (
        constraints.get("budget_inr")
        or meta.get("budget_inr")
        or trip.get("budget_inr")
        or trip.get("budget")
    )
    budget_inr = float(canonical_budget) if canonical_budget else None

    try:
        d_in = date.fromisoformat(str(canonical_checkin).split("T")[0])
        d_out = date.fromisoformat(str(canonical_checkout).split("T")[0])
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Dates must be in valid YYYY-MM-DD format.",
        ) from exc

    if d_in >= d_out:
        raise HTTPException(
            status_code=400,
            detail="checkin date must be strictly before checkout date.",
        )

    search_params: dict[str, Any] = {
        "destination": destination,
        "checkin": d_in.isoformat(),
        "checkout": d_out.isoformat(),
        "adults": int(adults),
        "children": int(children),
        "rooms": int(rooms),
        "budget_inr": budget_inr,
    }

    canonical_cheaper = constraints.get("cheaper_mode")
    if canonical_cheaper is None:
        canonical_cheaper = meta.get("cheaper_mode")
    if canonical_cheaper is not None:
        search_params["cheaper_mode"] = bool(canonical_cheaper)

    canonical_hbeds = constraints.get("hotelbeds_destination_code")
    if not canonical_hbeds:
        canonical_hbeds = meta.get("hotelbeds_destination_code")
    if canonical_hbeds:
        search_params["hotelbeds_destination_code"] = str(canonical_hbeds).strip().upper()

    try:
        service = HotelService()
        search_res = service.search(search_params)
    except HotelProviderError as exc:
        logger.warning("Hotel provider search error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Hotel provider search error: {exc}") from exc
    except Exception as exc:
        logger.error("Hotel search failed unexpectedly: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Hotel search encountered an internal error.") from exc

    raw_offers = search_res.get("offers") or []
    # Legitimate empty provider results return cleanly without snapshot persistence overhead
    if not raw_offers:
        return {
            "success": True,
            "offers": [],
            "hotels": [],
            "count": 0,
            "errors": search_res.get("errors", []),
            "providers_used": search_res.get("providers_used", []),
            "availability_state": "empty",
            "destination": destination,
            "checkin": d_in.isoformat(),
            "checkout": d_out.isoformat(),
        }

    # Persistence fail-closed: SearchSessionStore initialization
    try:
        snap_store = SearchSessionStore()
    except Exception as exc:
        logger.error("SearchSessionStore initialization failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Hotel search persistence unavailable: search session store could not be initialized.",
        ) from exc

    # Persistence fail-closed: session creation
    try:
        current_revision = resolve_current_revision(trip)
        if current_revision is None:
            current_revision = 0
        session = snap_store.create_session(
            user_id=current_user.id,
            trip_id=trip["id"],
            thread_id=None,
            search_type="hotels",
            criteria_hash=f"{destination}:{d_in}:{d_out}:{adults}:{children}:{rooms}",
            trip_revision=current_revision,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Hotel search session creation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Hotel search persistence unavailable: search session could not be created.",
        ) from exc

    nights = max(1, (d_out - d_in).days)
    normalized_offers: list[dict[str, Any]] = []

    for raw_o in raw_offers:
        # Finding 3: Truthful mapping — reject/skip malformed offers with missing required facts
        raw_price = raw_o.get("price")
        if raw_price is None:
            logger.warning("Skipping hotel offer: missing required field 'price'")
            continue
        try:
            price_val = float(raw_price)
        except (ValueError, TypeError):
            logger.warning("Skipping hotel offer: invalid price %r", raw_price)
            continue
        if price_val <= 0:
            logger.warning("Skipping hotel offer: non-positive price %r", price_val)
            continue

        raw_name = raw_o.get("hotel_name") or raw_o.get("name")
        if not raw_name or not str(raw_name).strip() or str(raw_name).strip().lower() == "hotel":
            logger.warning("Skipping hotel offer: missing or generic hotel name %r", raw_name)
            continue
        hotel_name = str(raw_name).strip()

        raw_avail = raw_o.get("availability")
        if raw_avail is None and raw_o.get("bookable") is True:
            raw_avail = "available"
        elif raw_avail is None and raw_o.get("status") in ("available", "confirmed"):
            raw_avail = "available"
        if not raw_avail or str(raw_avail).lower() not in ("available", "open"):
            logger.warning("Skipping hotel offer: missing or unavailable status (%s)", raw_avail)
            continue
        availability_str = str(raw_avail).lower()

        # Required field: currency must come from provider data (never default to USD)
        raw_currency = raw_o.get("currency")
        if not raw_currency or not str(raw_currency).strip() or len(str(raw_currency).strip()) != 3:
            logger.warning("Skipping hotel offer: missing or invalid currency %r", raw_currency)
            continue
        currency_val = str(raw_currency).strip().upper()

        # Optional metadata: use actual values or None / truthful empty representation (no invented defaults)
        raw_address = raw_o.get("address")
        address = str(raw_address).strip() if raw_address and str(raw_address).strip() else ""

        raw_stars = raw_o.get("stars") or raw_o.get("star_rating")
        if raw_stars is not None:
            try:
                stars = int(float(raw_stars))
            except (ValueError, TypeError):
                stars = None
        else:
            stars = None

        raw_rating = raw_o.get("rating") or raw_o.get("review_score")
        if raw_rating is not None:
            try:
                rating = round(float(raw_rating), 1)
            except (ValueError, TypeError):
                rating = None
        else:
            rating = None

        raw_reviews = raw_o.get("review_count") or raw_o.get("reviews_count")
        if raw_reviews is not None:
            try:
                review_count = int(raw_reviews)
            except (ValueError, TypeError):
                review_count = None
        else:
            review_count = None

        snapshot_id = uuid4()
        provider_name = str(raw_o.get("provider") or "liteapi")
        provider_offer_id = str(raw_o.get("provider_offer_id") or raw_o.get("id") or snapshot_id)

        price_inr_val = float(raw_o["price_inr"]) if raw_o.get("price_inr") is not None else None
        rate_val = float(raw_o["exchange_rate"]) if raw_o.get("exchange_rate") is not None else None

        # Room name: preserve actual provider value, never manufacture "Standard Room"
        raw_room_name = raw_o.get("room_name") or raw_o.get("room")
        room_name = str(raw_room_name).strip() if raw_room_name and str(raw_room_name).strip() else None

        # Board type: preserve actual provider value, never manufacture "Room Only"
        raw_board = raw_o.get("board_name") or raw_o.get("board_type")
        board_name = str(raw_board).strip() if raw_board and str(raw_board).strip() else None

        # Refundability: preserve actual provider value, never convert missing data into false
        raw_ref = raw_o.get("refundable")
        if raw_ref is None:
            canc = raw_o.get("cancellation")
            if isinstance(canc, dict) and canc.get("refundableTag"):
                tag = str(canc["refundableTag"]).upper()
                if tag == "RFN":
                    refundable = True
                elif tag == "NRF":
                    refundable = False
                else:
                    refundable = None
            else:
                refundable = None
        elif isinstance(raw_ref, bool):
            refundable = raw_ref
        elif str(raw_ref).strip().lower() in ("true", "1", "yes"):
            refundable = True
        elif str(raw_ref).strip().lower() in ("false", "0", "no"):
            refundable = False
        else:
            refundable = None

        if refundable is True:
            cancellation_policy = "Free cancellation"
        elif refundable is False:
            cancellation_policy = "Non-refundable"
        else:
            cancellation_policy = None

        is_rec = bool(raw_o.get("is_recommended", False))
        rec_rank = int(raw_o.get("recommendation_rank", 1))

        # Room records: only create when actual provider room/rate data exists. Never from price alone.
        # Strict Issue 2: NEVER inherit hotel-level total price (price_val) or adult count (adults).
        # Price-only hotel offers must not create fake selectable rooms.
        rooms_list: list[dict[str, Any]] = []
        internal_rooms: list[dict[str, Any]] = []
        if raw_o.get("rooms") and isinstance(raw_o["rooms"], list):
            for idx, r in enumerate(raw_o["rooms"]):
                if isinstance(r, dict):
                    r_name = str(r.get("name") or r.get("room_name") or "").strip()
                    if not r_name:
                        continue

                    # 1. Room price: use actual provider room/rate price only. NEVER inherit hotel price_val.
                    r_price_raw = r.get("total_price") if r.get("total_price") is not None else r.get("price")
                    if r_price_raw is not None:
                        try:
                            r_total_price = float(r_price_raw)
                            r_nightly_price = round(r_total_price / nights, 2)
                        except (ValueError, TypeError):
                            r_total_price = None
                            r_nightly_price = None
                    else:
                        r_total_price = None
                        r_nightly_price = None

                    # 2. Room capacity: use actual provider room capacity only. NEVER inherit adult count (adults).
                    r_cap_raw = r.get("capacity")
                    if r_cap_raw is not None:
                        try:
                            r_capacity = int(r_cap_raw)
                        except (ValueError, TypeError):
                            r_capacity = None
                    else:
                        r_capacity = None

                    # Optional fields: preserve exactly if present, else None
                    r_desc = str(r["description"]).strip() if r.get("description") is not None and str(r["description"]).strip() else None
                    r_beds = str(r["beds"]).strip() if r.get("beds") is not None and str(r["beds"]).strip() else None
                    r_amenities = [str(a).strip() for a in r["amenities"] if a and str(a).strip()] if isinstance(r.get("amenities"), list) else None

                    public_room_id = str(uuid4())
                    provider_room_id = str(r.get("id") or f"room_{snapshot_id}_{idx}")

                    # Public room: internal TrustTrip UUID only. NEVER leak provider_room_id
                    rooms_list.append({
                        "id": public_room_id,
                        "name": r_name,
                        "description": r_desc,
                        "price_per_night": r_nightly_price,
                        "total_price": r_total_price,
                        "currency": currency_val,
                        "capacity": r_capacity,
                        "beds": r_beds,
                        "amenities": r_amenities,
                    })

                    # Internal room record: retained strictly server-side in encrypted snapshot payload
                    internal_rooms.append({
                        "id": public_room_id,
                        "provider_room_id": provider_room_id,
                        "name": r_name,
                        "description": r_desc,
                        "price_per_night": r_nightly_price,
                        "total_price": r_total_price,
                        "currency": currency_val,
                        "capacity": r_capacity,
                        "beds": r_beds,
                        "amenities": r_amenities,
                    })
        elif room_name:
            # Single named room provided at provider level
            # Check if actual room price or capacity was explicitly provided in the room data
            r_price_raw = raw_o.get("room_price") if raw_o.get("room_price") is not None else raw_o.get("room_total_price")
            if r_price_raw is not None:
                try:
                    r_total_price = float(r_price_raw)
                    r_nightly_price = round(r_total_price / nights, 2)
                except (ValueError, TypeError):
                    r_total_price = None
                    r_nightly_price = None
            else:
                r_total_price = None
                r_nightly_price = None

            r_cap_raw = raw_o.get("room_capacity")
            if r_cap_raw is not None:
                try:
                    r_capacity = int(r_cap_raw)
                except (ValueError, TypeError):
                    r_capacity = None
            else:
                r_capacity = None

            r_desc = str(raw_o["room_description"]).strip() if raw_o.get("room_description") is not None and str(raw_o["room_description"]).strip() else None
            r_beds = str(raw_o["room_beds"]).strip() if raw_o.get("room_beds") is not None and str(raw_o["room_beds"]).strip() else None

            public_room_id = str(uuid4())
            provider_room_id = f"room_{snapshot_id}"

            # Public room: internal TrustTrip UUID only. NEVER leak provider_room_id
            rooms_list.append({
                "id": public_room_id,
                "name": room_name,
                "description": r_desc,
                "price_per_night": r_nightly_price,
                "total_price": r_total_price,
                "currency": currency_val,
                "capacity": r_capacity,
                "beds": r_beds,
                "amenities": None,
            })

            # Internal room record: retained strictly server-side in encrypted snapshot payload
            internal_rooms.append({
                "id": public_room_id,
                "provider_room_id": provider_room_id,
                "name": room_name,
                "description": r_desc,
                "price_per_night": r_nightly_price,
                "total_price": r_total_price,
                "currency": currency_val,
                "capacity": r_capacity,
                "beds": r_beds,
                "amenities": None,
            })

        persisted_raw = _scrub_payload(raw_o)
        persisted_raw["_internal_rooms"] = internal_rooms
        persisted_raw["checkin"] = d_in.isoformat()
        persisted_raw["checkout"] = d_out.isoformat()

        try:
            hotel_offer_obj = HotelOffer(
                snapshot_id=snapshot_id,
                provider=provider_name,
                provider_offer_id=provider_offer_id,
                hotel_id=str(raw_o.get("hotel_id") or snapshot_id),
                hotel_name=hotel_name,
                city=destination,
                address=address,
                star_rating=float(stars) if stars is not None else None,
                review_score=rating,
                room_name=room_name,
                board_type=board_name,
                refundable=refundable,
                price=PriceBreakdown(
                    base_amount=Decimal(str(round(price_val, 2))),
                    tax_amount=Decimal("0.00"),
                    fee_amount=Decimal("0.00"),
                    total_amount=Decimal(str(round(price_val, 2))),
                    currency=currency_val,
                    inr_reference_amount=Decimal(str(round(price_inr_val, 2))) if price_inr_val is not None else None,
                    fx_rate_applied=Decimal(str(rate_val)) if rate_val is not None else None,
                ),
                nightly_price=Decimal(str(round(price_val / nights, 2))),
                nights=nights,
                rooms_count=rooms,
                adults_count=adults,
                children_count=children,
                retrieved_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
                freshness=FreshnessStatus.FRESH,
                availability=AvailabilityStatus.AVAILABLE,
                is_recommended=is_rec,
                recommendation_rank=rec_rank,
                checkin=d_in.isoformat(),
                checkout=d_out.isoformat(),
            )
            snap_store.save_snapshot(
                session_id=session.session_id,
                offer=hotel_offer_obj,
                raw_payload=persisted_raw,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Hotel snapshot persistence failed for %s: %s", snapshot_id, exc)
            raise HTTPException(
                status_code=503,
                detail="Hotel search persistence unavailable: one or more offer snapshots could not be saved.",
            ) from exc

        # Extract provider amenities if present (e.g. tags or amenities)
        raw_amenities = raw_o.get("amenities") or raw_o.get("tags") or []
        amenities_list = [str(a).strip() for a in raw_amenities if a and str(a).strip()] if isinstance(raw_amenities, list) else []

        normalized_offer = {
            "id": str(snapshot_id),
            "snapshot_id": str(snapshot_id),
            "hotel_id": str(snapshot_id),
            "name": hotel_name,
            "hotel_name": hotel_name,
            "city": destination,
            "destination": destination,
            "address": address,
            "stars": stars,
            "rating": rating,
            "review_count": review_count,
            "image_url": str(raw_o.get("photo") or raw_o.get("image_url") or ""),
            "cancellation_policy": cancellation_policy,
            "refundable": refundable,
            "total_amount": price_val,
            "price": price_val,
            "currency": currency_val,
            "price_inr": price_inr_val,
            "exchange_rate": rate_val,
            "nightly_price": round(price_val / nights, 2),
            "nights": nights,
            "rooms_count": rooms,
            "adults_count": adults,
            "children_count": children,
            "checkin": d_in.isoformat(),
            "checkout": d_out.isoformat(),
            "check_in_date": d_in.isoformat(),
            "check_out_date": d_out.isoformat(),
            "availability": availability_str,
            "room_name": room_name,
            "board_name": board_name,
            "amenities": amenities_list,
            "rooms": rooms_list,
            "is_recommended": is_rec,
            "recommendation_rank": rec_rank,
            "provider": provider_name,
        }
        # Only append offer after successful persistence
        normalized_offers.append(normalized_offer)

    return {
        "success": True,
        "offers": normalized_offers,
        "hotels": normalized_offers,
        "count": len(normalized_offers),
        "errors": search_res.get("errors", []),
        "providers_used": search_res.get("providers_used", []),
        "availability_state": "available" if normalized_offers else "empty",
    }


@router.post("/select")
async def select_hotel(
    req: HotelSelectRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Select a normalized hotel offer snapshot and bind it to the trip."""
    snap_store = SearchSessionStore()
    from trips_api import OfferSelectionRequest

    selection_req = OfferSelectionRequest(
        offer_type="hotel",
        snapshot_id=req.snapshot_id,
        room_id=req.room_id,
    )
    try:
        updated_trip = trip_store.select_offer_atomic(
            trip_id=req.trip_id,
            user_id=current_user.id,
            req=selection_req,
            snap_store=snap_store,
        )
        return {
            "success": True,
            "trip_id": str(req.trip_id),
            "offer_type": "hotel",
            "snapshot_id": str(req.snapshot_id),
            "trip": updated_trip,
        }
    except OfferSelectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except RevisionContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Hotel selection failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Hotel selection failed due to an internal error.")


@router.post("/prebook")
async def prebook_hotel(
    req: HotelPrebookRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Prebook a hotel offer with mandatory user authentication and tenant trip verification.

    W3-FIND-01: Canonical prebook contract resolving authoritative offer amount and hold ID.
    W3-FIND-08: Authentication and tenant ownership validation (rejects anonymous and cross-tenant access).
    """
    tid_str = str(req.trip_id)
    trip = trip_store.get_trip(trip_id=tid_str, user_id=current_user.id)
    if not trip:
        # Distinguish cross-user (403) from non-existent (404)
        unscoped = None
        if trip_store.use_in_memory or not os.getenv("DATABASE_URL"):
            unscoped = trip_store._memory_trips.get(tid_str)
        else:
            with trip_store._connect() as conn:
                unscoped = conn.execute("SELECT id, user_id FROM trips WHERE id = %s", (tid_str,)).fetchone()
        if unscoped and str(unscoped["user_id"]) != str(current_user.id):
            raise HTTPException(status_code=403, detail="Forbidden: Trip does not belong to you.")
        raise HTTPException(status_code=404, detail="Trip not found.")

    # Authoritative offer resolution
    snap_id = req.snapshot_id
    meta = trip.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if not snap_id:
        snap_id = meta.get("selected_hotel_snapshot_id") or (meta.get("selected_hotel") or {}).get("snapshot_id")
    if not snap_id and req.hotel_id:
        try:
            UUID(str(req.hotel_id))
            snap_id = req.hotel_id
        except ValueError:
            pass

    # Avoid duplicate supplier holds: if trip metadata already holds an active, unexpired prebook, recover and return it
    retained_pre = meta.get("hotel_prebook")
    if retained_pre and isinstance(retained_pre, dict) and retained_pre.get("prebook_id"):
        retained_snap = retained_pre.get("snapshot_id")
        retained_hotel = retained_pre.get("hotel_id")
        matches = False
        if snap_id and retained_snap and str(retained_snap) == str(snap_id):
            matches = True
        elif req.hotel_id and retained_hotel and str(retained_hotel) == str(req.hotel_id):
            matches = True
        elif not snap_id and not req.hotel_id:
            matches = True

        if matches:
            now_utc = datetime.now(timezone.utc)
            exp_str = retained_pre.get("expires_at")
            is_exp = False
            if exp_str:
                try:
                    exp_dt = datetime.fromisoformat(str(exp_str).replace("Z", "+00:00"))
                    if exp_dt <= now_utc:
                        is_exp = True
                except Exception:
                    pass
            if not is_exp:
                return {
                    "success": True,
                    **retained_pre,
                }

    snap_store = SearchSessionStore()
    offer_dict: dict[str, Any] | None = None
    snapshot = None

    if snap_id:
        snapshot = snap_store.get_snapshot(snap_id, current_user.id)
        if not snapshot:
            other_snapshot = None
            if snap_store.use_in_memory:
                rec = snap_store._IN_MEMORY_SNAPSHOTS.get(str(snap_id))
                if rec:
                    other_snapshot = rec
            else:
                with snap_store._connect() as conn:
                    other_snapshot = conn.execute("SELECT id FROM offer_snapshots WHERE id = %s", (str(snap_id),)).fetchone()
            if other_snapshot:
                raise HTTPException(status_code=403, detail="Forbidden: Hotel offer snapshot does not belong to you.")
            raise HTTPException(status_code=404, detail=f"Hotel offer snapshot '{snap_id}' not found.")

        if snapshot.offer_type != "hotel":
            raise HTTPException(status_code=422, detail=f"Snapshot '{snap_id}' is not a hotel offer.")

        if snapshot.availability != AvailabilityStatus.AVAILABLE:
            raise HTTPException(status_code=409, detail="Selected hotel offer is no longer available.")

        now_utc = datetime.now(timezone.utc)
        if snapshot.expires_at and snapshot.expires_at <= now_utc:
            raise HTTPException(status_code=409, detail="Selected hotel offer has expired.")

        hotel_offer: HotelOffer = snapshot.normalized_offer
        offer_dict = {
            "id": hotel_offer.provider_offer_id,
            "provider_offer_id": hotel_offer.provider_offer_id,
            "provider": hotel_offer.provider,
            "hotel_id": hotel_offer.hotel_id,
            "hotel_name": hotel_offer.hotel_name,
            "city": hotel_offer.city,
            "address": hotel_offer.address,
            "room_name": hotel_offer.room_name,
            "board_name": hotel_offer.board_type,
            "refundable": hotel_offer.refundable,
            "price": float(hotel_offer.price.total_amount),
            "currency": str(hotel_offer.price.currency),
            "price_inr": float(hotel_offer.price.inr_reference_amount) if hotel_offer.price.inr_reference_amount else None,
            "nights": hotel_offer.nights,
            "rooms": hotel_offer.rooms_count,
            "adults": hotel_offer.adults_count,
            "children": hotel_offer.children_count,
            "infants": hotel_offer.infants_count,
            "snapshot_id": str(snapshot.snapshot_id),
        }
    elif req.offer and req.offer.get("provider_offer_id") and req.offer.get("provider") in {"liteapi", "hotelbeds"}:
        offer_dict = dict(req.offer)
    else:
        raise HTTPException(
            status_code=422,
            detail="No authoritative hotel offer found. Provide snapshot_id or select a hotel for this trip first.",
        )

    if not offer_dict.get("provider_offer_id") or offer_dict.get("provider") not in {"liteapi", "hotelbeds"}:
        raise HTTPException(status_code=422, detail="Invalid hotel offer: provider or provider_offer_id is missing.")

    try:
        result = HotelService().prebook(offer_dict)
        prebook_data = {
            "prebook_id": result.get("prebook_id"),
            "amount": float(offer_dict.get("price") or 0.0),
            "currency": str(offer_dict.get("currency") or "INR"),
            "status": "prebooked",
            "hotel_name": offer_dict.get("hotel_name", "Hotel"),
            "snapshot_id": str(snapshot.snapshot_id) if snapshot else (str(snap_id) if snap_id else None),
            "hotel_id": offer_dict.get("hotel_id"),
            "room_id": offer_dict.get("room_name"),
            "expires_at": snapshot.expires_at.isoformat() if (snapshot and snapshot.expires_at) else None,
            "prebook": result,
        }

        # Retain authoritative prebook on trip metadata for continuity
        try:
            meta["hotel_prebook"] = prebook_data
            trip_store.update_trip_metadata(tid_str, current_user.id, meta)
        except Exception as exc:
            logger.warning("Could not retain hotel_prebook on trip metadata: %s", exc)

        return {
            "success": True,
            **prebook_data,
        }
    except HotelProviderError as exc:
        msg = str(exc)
        if "no prebook availability" in msg.lower() or "withdrawn" in msg.lower() or "expired" in msg.lower():
            raise HTTPException(
                status_code=409,
                detail="Rate is no longer available from supplier. Please refresh stays and select a valid offer.",
            )
        raise HTTPException(status_code=409, detail=str(exc))
