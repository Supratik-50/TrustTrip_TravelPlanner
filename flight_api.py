"""Flight Booking Agent HTTP API.

The API deliberately separates search, selection, final confirmation and
booking. The backend never books merely because an AI model recommended an
offer.
"""
from __future__ import annotations

from typing import Any
import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator

from uuid import uuid4

logger = logging.getLogger(__name__)

from dependencies.auth import get_current_user
from domain.booking import FlightHandoffRequest, FlightBookingDestination
from domain.flight_search import FlightSearchResponse
from domain.live_offer import FlexibleFlightSearchResponse
from domain.search_criteria import FlexibleFlightSearchCriteria
from domain.user import UserResponse
from services.flight_service import FlightService
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.flight_booking_service import FlightBookingService, FareChangedException, OfferWithdrawnException
from domain.flight_booking import (
    FlightBookingSelectionRequest,
    FlightBookingSelectionResponse,
    FlightBookingRevalidationRequest,
    FlightBookingRevalidationResponse,
    FlightBookingApprovalRequest,
    FlightBookingApprovalResponse,
    FlightBookingConfirmationRequest,
    FlightBookingConfirmationResponse,
    FlightBookingException,
    BookingNotFoundError,
    BookingPermissionError,
    TripMismatchError,
    OfferStaleError,
    ApprovalRequiredError,
    FinalConfirmationRequiredError,
    PriceChangedError,
    OfferUnavailableError,
    DuplicateBookingError,
    ProviderBookingError,
)
from services.flight_search_service import search_flights_for_trip
from services.travel_intelligence.orchestrator import TravelIntelligenceOrchestrator
from services.trip_store import TripStore, resolve_current_revision

router = APIRouter(prefix="/api/flights", tags=["flights"])


def _booking_service() -> FlightBookingService:
    return FlightBookingService()



@router.post("/select-booking", response_model=FlightBookingDestination)
async def select_flight_booking(
    req: FlightHandoffRequest,
    current_user: UserResponse = Depends(get_current_user),
):
    """Select a flight offer and obtain its legitimate external booking destination.

    Does not require whole-trip APPROVED status.
    Client submits only snapshot_id.
    Server enforces tenant ownership, revalidation, and returns server-minted destination.
    """
    service = FlightBookingService()
    try:
        dest = await service.select_and_handoff_flight(
            snapshot_id=req.snapshot_id,
            authenticated_user_id=current_user.id,
        )
        return dest
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FareChangedException as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Fare has changed",
                "original_snapshot_id": str(exc.original_snapshot_id),
                "subsequent_snapshot_id": str(exc.subsequent_snapshot_id),
                "new_price": str(exc.new_price),
                "currency": exc.currency,
                "details": exc.details,
            },
        )
    except OfferWithdrawnException:
        raise HTTPException(status_code=410, detail="The selected flight offer is no longer available from the provider.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid booking parameters.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Flight selection handoff failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Flight selection handoff failed due to an internal error.")


@router.post("/search-flexible", response_model=FlexibleFlightSearchResponse)
async def search_flexible_flights(
    req: FlexibleFlightSearchCriteria,
    current_user: UserResponse = Depends(get_current_user),
):
    """Execute flexible live flight search across candidate date windows.

    Guarantees tenant isolation, provider offer ID shielding, and binds directly to authentic trip.
    """
    trip_store = TripStore()
    trip = trip_store.get_trip(req.trip_id, current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    current_revision = resolve_current_revision(trip)
    if current_revision is None:
        raise HTTPException(status_code=400, detail="Trip revision context unavailable.")

    orchestrator = TravelIntelligenceOrchestrator()
    try:
        res = await orchestrator.search_flexible_flights(
            user_id=current_user.id,
            trip_id=req.trip_id,
            criteria=req,
            trip_revision=current_revision,
            global_timeout=35.0,
        )
        return res
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Flexible flight search failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="Flexible flight search encountered a provider or system error. Please retry.",
        )




class FlightSlice(BaseModel):
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: str

    @field_validator("departure_date")
    @classmethod
    def validate_departure_date(cls, value: str) -> str:
        from datetime import date
        cleaned = value.strip()
        try:
            d = date.fromisoformat(cleaned[:10])
        except Exception:
            raise ValueError("departure_date must be in valid YYYY-MM-DD format.")
        if d < date.today():
            raise ValueError("Flight departure date cannot be in the past for live search.")
        return cleaned[:10]

    @field_validator("origin", "destination")
    @classmethod
    def validate_airport_code(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise ValueError("Airport code must be a 3-letter IATA code.")
        return cleaned



class FlightSearchRequest(BaseModel):
    trip_id: str | None = None
    slices: list[FlightSlice] | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)
    cabin_class: str = "economy"
    max_connections: int = Field(default=2, ge=0, le=4)


class RecheckRequest(BaseModel):
    offer_id: str = Field(min_length=4)


class RefreshOfferRequest(BaseModel):
    offer_id: str = Field(min_length=4)
    search_context: dict[str, Any] | None = None


class Passenger(BaseModel):
    id: str | None = None
    given_name: str = Field(min_length=1, max_length=40)
    family_name: str = Field(min_length=1, max_length=40)
    born_on: str
    title: str = Field(min_length=2, max_length=4)
    gender: str = Field(min_length=1, max_length=1)
    email: str | None = None
    phone_number: str | None = None
    type: str = "adult"

    @field_validator("given_name", "family_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        import re
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("Passenger name cannot be blank.")
        # Duffel accepts letters plus spaces, hyphens and apostrophes.
        if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž'\- ]+", value):
            raise ValueError("Passenger names may contain only letters, spaces, hyphens, and apostrophes.")
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"mr", "mrs", "ms", "miss", "mx", "dr"}:
            raise ValueError("Passenger title must be Mr, Mrs, Ms, Miss, Mx, or Dr.")
        return value

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"m", "f", "x"}:
            raise ValueError("Passenger gender must be M, F, or X.")
        return value


class BookingRequest(BaseModel):
    offer_id: str = Field(min_length=4)
    expected_amount: float | None = None
    passengers: list[Passenger] = Field(min_length=1, max_length=9)
    final_confirmation: bool = False
    allow_price_change: bool = False
    payment: dict[str, Any] | None = None
    payment_mode: str = "auto"


def _service() -> FlightService:
    return FlightService()


@router.get("/config")
async def flight_config():
    """Return safe provider metadata; never expose API credentials."""
    mode = os.getenv("DUFFEL_MODE", "test").strip().lower()
    return {
        "success": True,
        "provider": "duffel",
        "mode": mode,
        "bookable": True,
        "test_mode": mode == "test",
    }


@router.post("/search")
async def search_flights(
    request: FlightSearchRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: FlightService = Depends(_service),
):
    """Execute canonical trip flight search adhering to Section 5A backend contract."""
    if request.trip_id is not None:
        trip_store = TripStore()
        trip = trip_store.get_trip(request.trip_id, current_user.id)
        if not trip:
            # Check whether trip exists for a different user (unauthorized tenant)
            tid_str = str(request.trip_id)
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

        return await search_flights_for_trip(
            trip=trip,
            user_id=current_user.id,
            cabin_class=request.cabin_class,
            max_connections=request.max_connections,
            flight_service=service,
        )

    # Legacy slices flow if slices provided
    if not request.slices:
        raise HTTPException(status_code=400, detail="trip_id is required for flight search.")

    try:
        passengers = [{"type": "adult"}] * request.adults
        passengers += [{"type": "child"}] * request.children
        passengers += [{"type": "infant_without_seat"}] * request.infants
        search_context = {
            "slices": [item.model_dump() for item in request.slices],
            "passengers": passengers,
            "cabin_class": request.cabin_class,
            "max_connections": request.max_connections,
        }
        result = await asyncio.to_thread(service.search, search_context)
        for offer in result.get("offers", []):
            selected_snapshot = dict(offer)
            selected_snapshot.pop("search_context", None)
            offer["search_context"] = {**search_context, "selected_offer": selected_snapshot}
        return {"success": True, **result}
    except FlightProviderError as exc:
        logger.error("Flight provider search error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Flight provider search encountered an error.") from exc


@router.post("/recheck")
async def recheck_flight(request: RecheckRequest):
    try:
        offer = await asyncio.to_thread(_service().recheck, request.offer_id)
        return {"success": True, "offer": offer}
    except FlightProviderError as exc:
        logger.error("Flight provider recheck error: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Flight provider recheck encountered an error.") from exc


@router.post("/refresh-selected-offer")
async def refresh_selected_offer(request: RefreshOfferRequest):
    """Create a fresh Duffel offer request for every booking attempt.

    This is required because Duffel allows only one offer to be booked from
    each offer request. It makes repeated purchases from the same search safe.
    """
    try:
        provider = _service().provider
        if not hasattr(provider, "refresh_bookable_offer"):
            raise FlightProviderError("The configured flight provider does not support fresh booking offers.")
        offer = await asyncio.to_thread(provider.refresh_bookable_offer, request.offer_id, request.search_context)
        return {"success": True, "offer": offer, "fresh_offer": True}
    except FlightProviderError as exc:
        logger.error("Flight provider refresh offer error: %s", exc, exc_info=True)
        raise HTTPException(status_code=409, detail="Flight provider could not refresh bookable offer.") from exc


@router.post("/book")
async def book_flight(request: BookingRequest):
    if not request.final_confirmation:
        return {
            "success": False,
            "requires_final_confirmation": True,
            "error": "Final user confirmation is required before booking.",
        }

    try:
        passengers = [p.model_dump(exclude_none=True) for p in request.passengers]
        result = await asyncio.to_thread(
            _service().book_after_recheck,
            offer_id=request.offer_id,
            expected_amount=request.expected_amount,
            passengers=passengers,
            payment=request.payment,
            allow_price_change=request.allow_price_change,
            payment_mode=request.payment_mode,
        )
        return {"success": True, **result}
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class HoldPaymentRequest(BaseModel):
    order_id: str = Field(min_length=4)
    final_confirmation: bool = False


@router.post("/pay")
async def pay_hold_order(request: HoldPaymentRequest):
    """Complete a previously-created hold order in Duffel test mode.

    This endpoint uses Duffel's unlimited test balance, so no real card data
    is collected. It exists specifically for a safe end-to-end demonstration.
    """
    if not request.final_confirmation:
        return {
            "success": False,
            "requires_final_confirmation": True,
            "error": "Final user confirmation is required before payment.",
        }
    try:
        result = await asyncio.to_thread(
            _service().pay_hold_with_sandbox_balance, request.order_id
        )
        return {"success": True, **result}
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ============================================================================
# SECTION 5B FLIGHT BOOKING VERTICAL SLICE
# ============================================================================

@router.post("/booking/select", response_model=FlightBookingSelectionResponse, status_code=201)
@router.post("/select", response_model=FlightBookingSelectionResponse, status_code=201)
async def select_flight_offer(
    req: FlightBookingSelectionRequest,
    current_user: UserResponse = Depends(get_current_user),
    service: FlightBookingService = Depends(_booking_service),
):
    """Select an existing persisted flight offer and initialize a booking intent."""
    try:
        return await service.select_offer(
            user_id=current_user.id,
            trip_id=req.trip_id,
            offer_id=req.offer_id,
            passengers=req.passengers,
        )
    except BookingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TripMismatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OfferStaleError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Flight booking selection failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Flight selection failed due to an internal error.")


@router.post("/booking/{booking_id}/revalidate", response_model=FlightBookingRevalidationResponse)
@router.post("/booking/revalidate", response_model=FlightBookingRevalidationResponse)
async def revalidate_flight_booking(
    booking_id: str | None = None,
    req: FlightBookingRevalidationRequest | None = None,
    current_user: UserResponse = Depends(get_current_user),
    service: FlightBookingService = Depends(_booking_service),
):
    """Revalidate live provider price and availability before human approval and booking."""
    target_id = booking_id or (req.booking_id if req else None)
    if not target_id:
        raise HTTPException(status_code=400, detail="booking_id is required for revalidation.")
    accepted_price = req.accepted_price if req else None
    try:
        return await service.revalidate_offer(
            user_id=current_user.id,
            booking_id=target_id,
            accepted_price=accepted_price,
        )
    except BookingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PriceChangedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Fare has changed",
                "message": exc.message,
                "original_price": exc.details.get("original_price"),
                "new_price": exc.details.get("new_price"),
                "currency": exc.details.get("currency"),
            },
        )
    except OfferUnavailableError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except FlightBookingException as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Flight booking revalidation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Revalidation failed due to an internal error.")


@router.post("/booking/{booking_id}/approve", response_model=FlightBookingApprovalResponse)
@router.post("/booking/approve", response_model=FlightBookingApprovalResponse)
async def approve_flight_booking(
    booking_id: str | None = None,
    req: FlightBookingApprovalRequest | None = None,
    current_user: UserResponse = Depends(get_current_user),
    service: FlightBookingService = Depends(_booking_service),
):
    """Record explicit Human-in-the-loop (HITL) approval for a revalidated booking intent."""
    target_id = booking_id or (req.booking_id if req else None)
    if not target_id:
        raise HTTPException(status_code=400, detail="booking_id is required for approval.")
    approved = req.approved if req else True
    notes = req.notes if req else None
    try:
        return await service.record_approval(
            user_id=current_user.id,
            booking_id=target_id,
            approved=approved,
            notes=notes,
        )
    except BookingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FlightBookingException as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Flight booking approval failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Approval recording failed due to an internal error.")


@router.post("/booking/{booking_id}/confirm", response_model=FlightBookingConfirmationResponse)
@router.post("/booking/confirm", response_model=FlightBookingConfirmationResponse)
async def confirm_flight_booking(
    booking_id: str | None = None,
    req: FlightBookingConfirmationRequest | None = None,
    current_user: UserResponse = Depends(get_current_user),
    service: FlightBookingService = Depends(_booking_service),
):
    """Confirm booking with provider after revalidation and human approval."""
    target_id = booking_id or (req.booking_id if req else None)
    if not target_id:
        raise HTTPException(status_code=400, detail="booking_id is required for confirmation.")
    final_confirmation = req.final_confirmation if req else False
    payment = req.payment if req else None
    payment_mode = req.payment_mode if req else "auto"
    try:
        return await service.confirm_booking(
            user_id=current_user.id,
            booking_id=target_id,
            final_confirmation=final_confirmation,
            payment=payment,
            payment_mode=payment_mode,
        )
    except BookingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DuplicateBookingError as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "duplicate_attempt": True})
    except ApprovalRequiredError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "approval_required": True})
    except FinalConfirmationRequiredError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc), "requires_final_confirmation": True})
    except PriceChangedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Fare has changed",
                "message": exc.message,
                "original_price": exc.details.get("original_price"),
                "new_price": exc.details.get("new_price"),
                "currency": exc.details.get("currency"),
            },
        )
    except OfferUnavailableError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    except ProviderBookingError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except FlightBookingException as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        logger.error("Flight booking confirmation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Confirmation failed due to an internal error.")


@router.get("/booking/{booking_id}")
async def get_flight_booking(
    booking_id: str,
    current_user: UserResponse = Depends(get_current_user),
    service: FlightBookingService = Depends(_booking_service),
):
    """Query authoritative flight booking status."""
    try:
        return await service.get_booking(user_id=current_user.id, booking_id=booking_id)
    except BookingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Get flight booking failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve booking.")
