"""Flight Booking Lifecycle HTTP API.

Implements approved endpoints:
- POST /api/bookings/flight/prepare
- POST /api/bookings/flight/{id}/revalidate
- POST /api/bookings/flight/{id}/handoff
- GET  /api/bookings/{id}
- POST /api/bookings/{id}/cancel-intent
- POST /api/bookings/flight/webhook
- GET  /api/bookings/flight/callback

Strictly preserves tenant isolation, mandatory idempotency, revision locking, and structured error responses.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status

from dependencies.auth import get_current_user
from domain.booking_lifecycle import (
    BookingCompletedException,
    BookingExpiredException,
    BookingInProgressException,
    BookingIntent,
    BookingLifecycleException,
    BookingLockedException,
    BookingProviderRateLimited,
    BookingProviderTimeout,
    BookingProviderUnavailable,
    CancelFlightBookingRequest,
    FareChangedException,
    FlightHandoffDestinationResponse,
    HandoffFlightBookingRequest,
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
    PrepareFlightBookingRequest,
    RevalidateFlightBookingRequest,
    StaleRevisionException,
    UnauthorizedCallbackException,
    validate_idempotency_key,
)
from domain.user import UserResponse
from services.flight_booking_lifecycle_service import FlightBookingLifecycleService
from services.flight_provider import FlightProviderRateLimitError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def _service() -> FlightBookingLifecycleService:
    return FlightBookingLifecycleService()


def _handle_booking_exception(exc: Exception) -> None:
    """Map domain exceptions to structured HTTPExceptions with stable codes."""
    if isinstance(exc, IdempotencyKeyRequiredException):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": str(exc)},
        )
    if isinstance(exc, IdempotencyConflictException):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": str(exc)},
        )
    if isinstance(exc, BookingInProgressException):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "BOOKING_IN_PROGRESS", "message": str(exc)},
        )
    if isinstance(exc, BookingLockedException):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "BOOKING_LOCKED", "message": str(exc)},
        )
    if isinstance(exc, OwnershipViolationException):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "OWNERSHIP_VIOLATION", "message": str(exc)},
        )
    if isinstance(exc, FareChangedException):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PRICE_CHANGED",
                "message": str(exc),
                "details": exc.details,
            },
        )
    if isinstance(exc, OfferChangedException):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OFFER_CHANGED", "message": str(exc)},
        )
    if isinstance(exc, OfferDataInvalidException):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "OFFER_DATA_INVALID", "message": str(exc)},
        )
    if isinstance(exc, StaleRevisionException):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "REVISION_STALE", "message": str(exc)},
        )
    if isinstance(exc, (OfferExpiredException, BookingExpiredException)):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "BOOKING_EXPIRED", "message": str(exc)},
        )
    if isinstance(exc, OfferUnavailableException):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "OFFER_UNAVAILABLE", "message": str(exc)},
        )
    if isinstance(exc, InvalidBookingStateTransitionError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_BOOKING_STATE", "message": str(exc)},
        )
    if isinstance(exc, InvalidCallbackException):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CALLBACK", "message": str(exc)},
        )
    if isinstance(exc, UnauthorizedCallbackException):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED_CALLBACK", "message": str(exc)},
        )
    if isinstance(exc, (FlightProviderRateLimitError, BookingProviderRateLimited)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "PROVIDER_RATE_LIMITED", "message": "Upstream flight provider rate limit exceeded."},
        )
    if isinstance(exc, BookingProviderUnavailable):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "PROVIDER_UNAVAILABLE", "message": str(exc)},
        )
    if isinstance(exc, BookingProviderTimeout):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"code": "PROVIDER_TIMEOUT", "message": str(exc)},
        )
    if isinstance(exc, BookingLifecycleException):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        )
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PARAMETERS", "message": str(exc)},
        )
    if isinstance(exc, HTTPException):
        raise exc

    logger.error("Unhandled booking lifecycle exception: %s", exc, exc_info=True)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "INTERNAL_ERROR", "message": "Booking lifecycle operation failed due to an internal error."},
    )


@router.post("/flight/prepare", response_model=BookingIntent, status_code=status.HTTP_201_CREATED)
async def prepare_flight_booking(
    req: PrepareFlightBookingRequest,
    current_user: UserResponse = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Initialize a new BookingIntent bound to trip, tenant, snapshot, and revision."""
    service = _service()
    try:
        validated_key = validate_idempotency_key(idempotency_key)
        intent = await service.prepare_booking_intent(
            user_id=current_user.id,
            trip_id=req.trip_id,
            snapshot_id=req.snapshot_id,
            passengers=req.passengers,
            booking_mode=req.booking_mode,
            idempotency_key=validated_key,
        )
        return intent
    except Exception as exc:
        _handle_booking_exception(exc)


@router.post("/flight/{id}/revalidate", response_model=OfferRevalidationResult)
async def revalidate_flight_booking(
    id: UUID,
    req: RevalidateFlightBookingRequest | None = Body(default=None),
    current_user: UserResponse = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Revalidate live supplier offer before checkout handoff."""
    service = _service()
    accepted_amount = req.accepted_amount if req else None
    try:
        validated_key = validate_idempotency_key(idempotency_key)
        result = await service.revalidate_booking_intent(
            user_id=current_user.id,
            intent_id=id,
            accepted_amount=accepted_amount,
            idempotency_key=validated_key,
        )
        return result
    except Exception as exc:
        _handle_booking_exception(exc)


@router.post("/flight/{id}/handoff", response_model=FlightHandoffDestinationResponse)
async def handoff_flight_booking(
    id: UUID,
    req: HandoffFlightBookingRequest | None = Body(default=None),
    current_user: UserResponse = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Generate verified external checkout destination with fresh pre-handoff revalidation."""
    service = _service()
    booking_mode = req.booking_mode if req else None
    accepted_amount = req.accepted_amount if req else None
    payment = req.payment if req else None
    try:
        validated_key = validate_idempotency_key(idempotency_key)
        dest = await service.handoff_booking(
            user_id=current_user.id,
            intent_id=id,
            booking_mode=booking_mode,
            accepted_amount=accepted_amount,
            payment=payment,
            idempotency_key=validated_key,
        )
        return dest
    except Exception as exc:
        _handle_booking_exception(exc)


@router.get("/{id}", response_model=BookingIntent)
async def get_booking_intent(
    id: UUID,
    current_user: UserResponse = Depends(get_current_user),
):
    """Query authoritative booking lifecycle status."""
    service = _service()
    try:
        intent = await service.get_booking_intent(
            user_id=current_user.id,
            intent_id=id,
        )
        return intent
    except Exception as exc:
        _handle_booking_exception(exc)


@router.post("/{id}/cancel-intent", response_model=BookingIntent)
async def cancel_booking_intent(
    id: UUID,
    req: CancelFlightBookingRequest | None = Body(default=None),
    current_user: UserResponse = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Explicitly cancel/abandon an uncompleted booking intent."""
    service = _service()
    reason = req.reason if req else "User requested cancellation"
    try:
        validated_key = validate_idempotency_key(idempotency_key)
        intent = await service.cancel_booking_intent(
            user_id=current_user.id,
            intent_id=id,
            reason=reason,
            idempotency_key=validated_key,
        )
        return intent
    except Exception as exc:
        _handle_booking_exception(exc)


@router.post("/flight/webhook")
async def provider_webhook(
    request: Request,
    x_duffel_signature: str | None = Header(default=None, alias="X-Duffel-Signature"),
    duffel_signature: str | None = Header(default=None, alias="Duffel-Signature"),
):
    """Authenticated Duffel webhook for production lifecycle completion."""
    sig = x_duffel_signature or duffel_signature
    raw_body = await request.body()
    service = _service()
    try:
        res = await service.process_provider_callback(raw_body, signature_header=sig)
        return res
    except Exception as exc:
        _handle_booking_exception(exc)


@router.get("/flight/callback")
async def browser_redirect_callback(
    intent_id: UUID | None = None,
    status: str | None = None,
):
    """Browser return redirect endpoint.
    
    CRITICAL SECURITY INVARIANT (Blocker 8):
    Browser redirect alone MUST NEVER mark a booking as COMPLETED.
    """
    return {
        "status": "pending_provider_webhook",
        "intent_id": str(intent_id) if intent_id else None,
        "message": "Browser redirect received; awaiting authenticated asynchronous provider webhook confirmation.",
    }
