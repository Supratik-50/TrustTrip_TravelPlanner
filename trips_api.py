"""Trips and Bookings API Router for Trust Trip MAS.

All operations are ownership-scoped to the authenticated user.
Endpoints require get_current_user dependency; tenant enumeration is blocked.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import Response

logger = logging.getLogger(__name__)

from dependencies.auth import get_current_user
from domain.user import UserResponse
from domain.change_set import compute_change_set, calculate_replanning_plan
from services.transaction_store import TransactionStore
from services.flight_service import FlightService
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.razorpay_service import RazorpayService, RazorpayError
from services.ticket_service import build_booking_pdf
from services.hotel_booking_pdf import build_hotel_booking_pdf
from services.hotel_transaction_store import HotelTransactionStore
from services.trip_store import (
    TripStore,
    OfferSelectionError,
    RevisionContextError,
    resolve_current_revision,
)
from services.search_session_store import SearchSessionStore
from services.evidence_store import EvidenceRevisionStore
from domain.evidence import UsageState
from domain.live_offer import AvailabilityStatus, FreshnessStatus

router = APIRouter(prefix="/api/trips", tags=["trips"])



class TripCreateRequest(BaseModel):
    title: str = Field(default="New Trip", max_length=200)
    destination: str = Field(default="", max_length=120)
    origin: str = Field(default="", max_length=120)
    status: str = Field(default="planning", max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadCreateRequest(BaseModel):
    thread_id: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelQuote(BaseModel):
    transaction_id: str
    final_confirmation: bool = False


class CancelConfirm(BaseModel):
    transaction_id: str
    cancellation_id: str
    final_confirmation: bool = False


def _store() -> TransactionStore:
    return TransactionStore()


def _trip_store() -> TripStore:
    return TripStore()


@router.get(
    "",
    summary="List all trips and booking transactions owned by the authenticated user",
)
async def list_trips(
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Return only the authenticated user's trips and transaction records."""
    user_id_str = str(current_user.id)
    canonical_trips = trip_store.list_trips(current_user.id, limit=50)
    flights = _store().list_recent(50, customer_ref=user_id_str)
    hotels = HotelTransactionStore().list_recent(50, customer_ref=user_id_str)

    normalized = []
    for t in canonical_trips:
        item = dict(t)
        item["record_type"] = "trip"
        normalized.append(item)
    for tx in flights:
        item = dict(tx)
        item["record_type"] = "flight"
        normalized.append(item)
    for tx in hotels:
        item = dict(tx)
        item["record_type"] = "hotel"
        item["booking_reference"] = (
            (item.get("booking_json") or {}).get("confirmation_code")
            or (item.get("booking_json") or {}).get("booking_id")
        )
        item["amount"] = item.get("amount_inr")
        item["currency"] = "INR"
        normalized.append(item)

    normalized.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return {"success": True, "trips": normalized[:100]}


@router.post(
    "",
    status_code=201,
    summary="Create a new canonical trip owned by the authenticated user",
)
async def create_trip(
    request: TripCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    trip = trip_store.create_trip(
        user_id=current_user.id,
        title=request.title,
        destination=request.destination,
        origin=request.origin,
        status=request.status,
        metadata=request.metadata,
    )
    return {"success": True, "trip": trip}


@router.get(
    "/{trip_id}",
    summary="Get details of a specific trip owned by the authenticated user",
)
async def get_trip(
    trip_id: str,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(
            status_code=404,
            detail="Trip not found or does not belong to you.",
        )
    return {"success": True, "trip": trip}


@router.delete(
    "/{transaction_id}",
    summary="Delete a trip or booking record owned by the authenticated user",
)
async def delete_trip_record(
    transaction_id: str,
    record_type: str = Query(default="trip", pattern="^(trip|flight|hotel)$"),
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Delete a trip or transaction record strictly verifying user ownership."""
    user_id_str = str(current_user.id)
    deleted = False

    if record_type == "trip":
        deleted = trip_store.delete_trip(transaction_id, current_user.id)
    elif record_type == "hotel":
        deleted = HotelTransactionStore().delete(transaction_id, customer_ref=user_id_str)
    elif record_type == "flight":
        deleted = _store().delete(transaction_id, customer_ref=user_id_str)

    if not deleted:
        # Check if record_type was default 'trip' but it was a transaction or vice versa
        if record_type == "trip":
            deleted = _store().delete(transaction_id, customer_ref=user_id_str) or \
                      HotelTransactionStore().delete(transaction_id, customer_ref=user_id_str)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Trip record not found or it does not belong to you.",
        )
    return {"success": True, "deleted": transaction_id, "record_type": record_type}


@router.post(
    "/{trip_id}/threads",
    status_code=201,
    summary="Create a new conversation thread bound to an owned trip",
)
async def create_trip_thread(
    trip_id: str,
    request: ThreadCreateRequest = ThreadCreateRequest(),
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(
            status_code=404,
            detail="Trip not found or does not belong to you.",
        )
    thread = trip_store.create_thread(
        user_id=current_user.id,
        trip_id=trip_id,
        thread_id=request.thread_id,
        metadata=request.metadata,
    )
    return {"success": True, "thread": thread}


@router.get(
    "/{transaction_id}/hotel.pdf",
    summary="Download hotel confirmation PDF for an owned transaction",
)
async def hotel_booking_pdf(
    transaction_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    user_id_str = str(current_user.id)
    record = HotelTransactionStore().get(transaction_id)
    if not record or record.get("customer_ref") != user_id_str:
        raise HTTPException(
            status_code=404,
            detail="Hotel booking record not found.",
        )
    if str(record.get("booking_status") or "").lower() != "booked" or not record.get("booking_json"):
        raise HTTPException(
            status_code=409,
            detail="A hotel booking confirmation PDF is available only after a successful booking.",
        )
    try:
        pdf = build_hotel_booking_pdf(record)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate hotel booking PDF: {exc}",
        )
    filename = "trust-trip-hotel-booking.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{transaction_id}/ticket.pdf",
    summary="Download flight ticket PDF for an owned transaction",
)
async def download_ticket(
    transaction_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    user_id_str = str(current_user.id)
    tx = _store().get(transaction_id)
    if not tx or tx.get("customer_ref") != user_id_str or tx.get("booking_status") != "booked" or not tx.get("duffel_order_id"):
        raise HTTPException(
            status_code=404,
            detail="A completed flight booking owned by you is required before downloading the ticket.",
        )
    try:
        order = await asyncio.to_thread(FlightService().get_order, tx["duffel_order_id"])
        pdf = await asyncio.to_thread(build_booking_pdf, order, tx)
        filename = f"Trust-Trip-MAS-{tx.get('booking_reference') or tx['duffel_order_id']}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/{transaction_id}/refresh",
    summary="Refresh status of an owned supplier order",
)
async def refresh_booking(
    transaction_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    user_id_str = str(current_user.id)
    store = _store()
    tx = store.get(transaction_id)
    if not tx or tx.get("customer_ref") != user_id_str:
        raise HTTPException(
            status_code=404,
            detail="Booking transaction not found or does not belong to you.",
        )
    if tx.get("booking_status") == "booked":
        return {
            "success": True,
            "status": "booked",
            "booking_reference": tx.get("booking_reference"),
            "duffel_order_id": tx.get("duffel_order_id"),
        }
    order_id = tx.get("duffel_order_id")
    if not order_id:
        return {
            "success": True,
            "status": tx.get("booking_status"),
            "message": "Supplier order has not been assigned yet.",
        }
    try:
        order = await asyncio.to_thread(FlightService().get_order, order_id)
        pnr = order.get("booking_reference")
        if pnr:
            store.update(
                transaction_id,
                booking_reference=pnr,
                booking_status="booked",
                payment_status="captured",
            )
            store.update_metadata(
                transaction_id,
                supplier_documents=order.get("documents") or [],
                supplier_live_mode=bool(order.get("live_mode")),
            )
            return {
                "success": True,
                "status": "booked",
                "booking_reference": pnr,
                "duffel_order_id": order_id,
                "order": order,
            }
        return {
            "success": True,
            "status": "provider_pending",
            "duffel_order_id": order_id,
            "message": "Supplier order exists; airline PNR is not available yet.",
        }
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/cancel/quote",
    summary="Get cancellation quote for an owned booking transaction",
)
async def cancellation_quote(
    request: CancelQuote,
    current_user: UserResponse = Depends(get_current_user),
):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True}
    user_id_str = str(current_user.id)
    tx = _store().get(request.transaction_id)
    if not tx or tx.get("customer_ref") != user_id_str or not tx.get("duffel_order_id"):
        raise HTTPException(
            status_code=404,
            detail="Booking transaction not found or does not belong to you.",
        )
    try:
        quote = await asyncio.to_thread(FlightService().cancellation_quote, tx["duffel_order_id"])
        return {"success": True, "transaction_id": request.transaction_id, "quote": quote}
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/cancel/confirm",
    summary="Confirm cancellation for an owned booking transaction",
)
async def cancellation_confirm(
    request: CancelConfirm,
    current_user: UserResponse = Depends(get_current_user),
):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True}
    user_id_str = str(current_user.id)
    tx = _store().get(request.transaction_id)
    if not tx or tx.get("customer_ref") != user_id_str:
        raise HTTPException(
            status_code=404,
            detail="Booking transaction not found or does not belong to you.",
        )
    try:
        result = await asyncio.to_thread(FlightService().confirm_cancellation, request.cancellation_id)
        refund = None
        payment_id = tx.get("razorpay_payment_id")
        refund_amount = result.get("refund_amount")
        if payment_id and refund_amount is not None:
            try:
                refund_currency = str(result.get("refund_currency") or result.get("currency") or "INR").upper()
                customer_amount = float(tx.get("amount") or 0)
                if refund_currency == "INR":
                    customer_refund_amount = float(refund_amount)
                else:
                    provider_currency = str(tx.get("provider_currency") or "").upper()
                    stored_rate = tx.get("fx_rate")
                    if refund_currency != provider_currency or stored_rate is None:
                        _store().update(request.transaction_id, booking_status="cancelled", refund_status="manual_review")
                        return {
                            "success": True,
                            "cancellation": result,
                            "refund": None,
                            "refund_error": "Airline refund currency could not be converted using the locked transaction FX rate; manual refund review is required.",
                            "transaction_id": request.transaction_id,
                        }
                    customer_refund_amount = float(refund_amount) * float(stored_rate)
                customer_refund_amount = min(customer_refund_amount, customer_amount)
                if customer_refund_amount <= 0:
                    _store().update(request.transaction_id, booking_status="cancelled", refund_status="not_applicable")
                    return {"success": True, "cancellation": result, "refund": None, "transaction_id": request.transaction_id}
                refund = await asyncio.to_thread(
                    RazorpayService().refund,
                    payment_id,
                    customer_refund_amount,
                    f"cancel-refund-{request.transaction_id}",
                )
            except RazorpayError as exc:
                _store().update(request.transaction_id, booking_status="cancelled", refund_status="manual_review")
                return {
                    "success": True,
                    "cancellation": result,
                    "refund": None,
                    "refund_error": str(exc),
                    "transaction_id": request.transaction_id,
                }
        _store().update(
            request.transaction_id,
            booking_status="cancelled",
            refund_status=(refund or {}).get("status", "not_applicable"),
        )
        return {"success": True, "cancellation": result, "refund": refund, "transaction_id": request.transaction_id}
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class OfferSelectionRequest(BaseModel):
    offer_type: str = Field(pattern="^(flight|hotel)$")
    snapshot_id: UUID


class TripRevisionRequest(BaseModel):
    feedback: str = ""
    updated_constraints: dict[str, Any] = Field(default_factory=dict)
    expected_base_revision: int | None = None


@router.post("/{trip_id}/select-offer")
async def select_trip_offer(
    trip_id: str,
    req: OfferSelectionRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Bind an explicit flight or hotel offer to the trip.

    Validates tenant ownership of both trip and snapshot atomically under parent trip row lock.
    Does NOT increment trip revision and does NOT invalidate other plan evidence.
    """
    snap_store = SearchSessionStore()
    try:
        updated_trip = trip_store.select_offer_atomic(
            trip_id=trip_id,
            user_id=current_user.id,
            req=req,
            snap_store=snap_store,
        )
        return {
            "success": True,
            "trip_id": trip_id,
            "offer_type": req.offer_type,
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
        logger.error("Atomic offer selection failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Offer selection failed due to an internal error.")


@router.post("/{trip_id}/revisions")
async def create_trip_revision(
    trip_id: str,
    req: TripRevisionRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Submit human feedback causing a TripChangeSet, advancing revision, and updating status to awaiting_approval.

    Executed atomically under parent trip row lock inside a single transaction.
    """
    try:
        return trip_store.create_revision_atomic(
            trip_id=trip_id,
            user_id=current_user.id,
            feedback=req.feedback,
            updated_constraints=req.updated_constraints,
            expected_base_revision=req.expected_base_revision,
        )
    except OfferSelectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except RevisionContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Atomic trip revision creation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Trip revision failed due to an internal error.")


@router.post("/{trip_id}/approve")
async def approve_trip(
    trip_id: str,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Explicitly approve a planned trip, establishing PlanApproval record."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    ev_store = EvidenceRevisionStore()
    current_rev_id = trip.get("current_revision_id")

    # If current_revision_id is not set, publish baseline revision 1 first
    if not current_rev_id:
        meta = trip.get("metadata") or {}
        plan_snapshot = {
            "title": trip.get("title"),
            "destination": trip.get("destination"),
            "origin": trip.get("origin"),
            "constraints": meta.get("constraints") or {},
            "metadata": meta,
        }
        published_rev = ev_store.publish_revision_transaction(
            trip_id=trip_id,
            user_id=current_user.id,
            expected_base_revision=0,
            change_set={"initial": True},
            plan_snapshot=plan_snapshot,
        )
        current_rev_id = str(published_rev.revision_id)

    meta = trip.get("metadata") or {}
    now_iso = datetime.now(timezone.utc).isoformat()
    meta["approved_at"] = now_iso

    # Execute approval transaction under parent trip lock
    approval = ev_store.approve_revision_transaction(
        trip_id=trip_id,
        user_id=current_user.id,
        revision_id=current_rev_id,
        metadata={"approved_at": now_iso},
    )

    updated_trip = trip_store.update_trip(
        trip_id,
        current_user.id,
        metadata=meta,
    )

    return {
        "success": True,
        "trip_id": trip_id,
        "revision_id": str(current_rev_id),
        "status": "approved",
        "approval_id": str(approval.approval_id),
        "trip": updated_trip,
    }


@router.post("/{trip_id}/revoke-approval")
async def revoke_trip_approval(
    trip_id: str,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Explicitly revoke approval for the trip, demoting status to awaiting_approval."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    ev_store = EvidenceRevisionStore()
    revoked = ev_store.revoke_approval_transaction(trip_id=trip_id, user_id=current_user.id)

    updated_trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    return {
        "success": True,
        "trip_id": trip_id,
        "revoked": revoked,
        "status": (updated_trip or {}).get("status", "awaiting_approval"),
        "trip": updated_trip,
    }


@router.get("/{trip_id}/revisions")
async def list_trip_revisions(
    trip_id: str,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """List all immutable plan revisions for an owned trip."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    ev_store = EvidenceRevisionStore()
    revisions = ev_store.list_revisions_for_trip(trip_id=trip_id, user_id=current_user.id)
    return {
        "success": True,
        "trip_id": trip_id,
        "current_revision_id": str(trip.get("current_revision_id") or ""),
        "current_revision_number": int(trip.get("current_revision_number") or 0),
        "revisions": [r.model_dump() for r in revisions],
    }
