"""Trips and Bookings API Router for Trust Trip MAS.

All operations are ownership-scoped to the authenticated user.
Endpoints require get_current_user dependency; tenant enumeration is blocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from fastapi.responses import Response, JSONResponse

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
from services.payment_receipt_pdf import build_payment_receipt_pdf
from services.hotel_transaction_store import HotelTransactionStore
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService, BookingNotFoundError, BookingPermissionError
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
    record_type: str | None = Query(default=None, description="Optional filter by record_type: 'trip', 'flight', 'hotel', 'all'"),
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Return only the authenticated user's trips and transaction records."""
    user_id_str = str(current_user.id)
    canonical_trips = trip_store.list_trips(current_user.id, limit=50)

    # Wave 3B: Use canonical booking_intents for flight bookings
    read_service = BookingReadService()
    flights = read_service.list_recent_flights_for_user(current_user.id, limit=50)
    # If no flights found in booking_intents, fall back to legacy flight_transactions for backward compatibility
    if not flights:
        flights = _store().list_recent(50, customer_ref=user_id_str)

    try:
        hotels = HotelTransactionStore().list_recent(50, customer_ref=user_id_str)
    except Exception:
        hotels = []
    if not hotels and BookingReadService._IN_MEMORY_HOTEL_BOOKINGS:
        hotels = [
            h for h in BookingReadService._IN_MEMORY_HOTEL_BOOKINGS.values()
            if str(h.get("customer_ref")) == user_id_str
        ]

    # Build lookup of flight and hotel bookings by trip_id to attach to canonical trips
    flights_by_trip: dict[str, list[dict[str, Any]]] = {}
    for f in flights:
        if str(f.get("booking_status") or "").lower() != "booked":
            continue
        tid = str(f.get("trip_id") or "")
        if tid:
            flights_by_trip.setdefault(tid, []).append(f)

    hotels_by_trip: dict[str, list[dict[str, Any]]] = {}
    for h in hotels:
        if str(h.get("booking_status") or "").lower() != "booked":
            continue
        tid = str(h.get("trip_id") or "")
        if tid:
            hotels_by_trip.setdefault(tid, []).append(h)

    normalized = []
    if record_type in (None, "trip", "all"):
        for t in canonical_trips:
            item = dict(t)
            item["record_type"] = "trip"
            trip_id = str(item.get("id") or item.get("trip_id") or "")
            trip_flights = flights_by_trip.get(trip_id, [])
            trip_hotels = hotels_by_trip.get(trip_id, [])
            item["flight_count"] = len(trip_flights)
            item["hotel_count"] = len(trip_hotels)
            item["flights"] = trip_flights
            item["hotels"] = [
                {
                    "id": str(h.get("id")),
                    "booking_reference": (
                        (h.get("booking_json") or {}).get("confirmation_code")
                        or (h.get("booking_json") or {}).get("booking_id")
                        or str(h.get("id"))
                    ),
                    "confirmation_code": (
                        (h.get("booking_json") or {}).get("confirmation_code")
                        or (h.get("booking_json") or {}).get("booking_id")
                        or str(h.get("id"))
                    ),
                    "booking_status": str(h.get("booking_status")),
                    "hotel_name": h.get("hotel_name"),
                    "amount": float(h.get("amount_inr") or 0.0),
                    "currency": str(h.get("currency") or "INR"),
                }
                for h in trip_hotels
            ]
            normalized.append(item)

    if record_type in ("flight", "all"):
        for tx in flights:
            item = dict(tx)
            item["record_type"] = "flight"
            normalized.append(item)

    if record_type in ("hotel", "all"):
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
    return {"success": True, "trips": normalized[:100], "flights": flights, "hotels": hotels}


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
    meta = trip.get("metadata") or {}
    if isinstance(meta, dict):
        if meta.get("selected_hotel"):
            trip["selected_hotel_offer"] = meta["selected_hotel"]
        if meta.get("selected_flight"):
            trip["selected_flight_offer"] = meta["selected_flight"]

    # Wave 3D-A: Expose active booking authorization for current revision if exists
    cur_rev_id = trip.get("current_revision_id")
    if cur_rev_id:
        from services.authorization_store import AuthorizationStore
        auth = AuthorizationStore().get_booking_authorization(trip_id=trip_id, revision_id=cur_rev_id)
        if auth and str(auth.user_id) == str(current_user.id):
            trip["booking_authorization"] = {
                "authorization_id": str(auth.authorization_id),
                "trip_id": str(auth.trip_id),
                "revision_id": str(auth.revision_id),
                "revision_number": int(auth.revision_number),
                "authorized_at": auth.authorized_at.isoformat() if hasattr(auth.authorized_at, "isoformat") else str(auth.authorized_at),
                "metadata": auth.authorization_metadata or {},
            }
            trip["is_booking_authorized"] = True

    return {"success": True, "trip": trip}


@router.get(
    "/{trip_id}/bookings",
    summary="Get authoritative booking status (flight & hotel) for an owned trip",
)
async def get_trip_bookings(
    trip_id: str,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Return authoritative flight and hotel booking state for a trip owned by current user."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        # Check whether trip exists under another user to distinguish 403 vs 404
        all_trips = trip_store._memory_trips if (trip_store.use_in_memory or not os.getenv("DATABASE_URL")) else None
        exists_for_other = False
        if all_trips is not None:
            exists_for_other = str(trip_id) in all_trips
        else:
            try:
                with trip_store._connect() as conn:
                    row = conn.execute("SELECT id FROM trips WHERE id = %s", (str(trip_id),)).fetchone()
                    exists_for_other = bool(row)
            except Exception:
                pass
        if exists_for_other:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this trip.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found.",
        )
    read_service = BookingReadService()
    bookings = read_service.get_trip_bookings(trip_id=trip_id, user_id=current_user.id)
    return {
        "success": True,
        **bookings,
    }


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
    # Persist thread_id in trip metadata
    try:
        meta = trip.get("metadata") or {}
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        meta = dict(meta)
        meta["thread_id"] = str(thread["id"])
        trip_store.update_trip(trip_id, current_user.id, metadata=meta)
    except Exception:
        pass
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
    "/{transaction_id}/receipt.pdf",
    summary="Download payment receipt PDF for an owned transaction",
)
async def payment_receipt_pdf(
    transaction_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    user_id_str = str(current_user.id)
    receipt_data: dict[str, Any] = {}

    # 1. Attempt lookup in HotelTransactionStore
    hotel_record = HotelTransactionStore().get(transaction_id)
    if hotel_record:
        if hotel_record.get("customer_ref") and hotel_record.get("customer_ref") != user_id_str:
            raise HTTPException(
                status_code=403,
                detail="Access denied to this payment receipt.",
            )
        offer = hotel_record.get("offer_json") or {}
        if isinstance(offer, str):
            try:
                offer = json.loads(offer)
            except Exception:
                offer = {}
        booking = hotel_record.get("booking_json") or {}
        if isinstance(booking, str):
            try:
                booking = json.loads(booking)
            except Exception:
                booking = {}
        holder = hotel_record.get("holder_json") or {}
        if isinstance(holder, str):
            try:
                holder = json.loads(holder)
            except Exception:
                holder = {}

        lead_name = " ".join(x for x in [holder.get("firstName"), holder.get("lastName")] if x) or holder.get("name") or "—"
        conf_code = booking.get("confirmation_code") or booking.get("booking_id")

        receipt_data = {
            "id": transaction_id,
            "merchant": "TrustTrip Travel Planner",
            "service_type": "Hotel Reservation",
            "service_name": hotel_record.get("hotel_name") or offer.get("hotel_name") or "Hotel Stay",
            "payment_status": hotel_record.get("payment_status") or "pending",
            "booking_status": hotel_record.get("booking_status") or "pending",
            "amount_inr": float(hotel_record.get("amount_inr") or 0.0),
            "currency": str(hotel_record.get("currency") or "INR"),
            "razorpay_order_id": hotel_record.get("razorpay_order_id"),
            "razorpay_payment_id": hotel_record.get("razorpay_payment_id"),
            "confirmation_code": conf_code,
            "customer_name": lead_name,
            "customer_email": holder.get("email"),
            "created_at": hotel_record.get("created_at"),
        }

    # 2. Attempt lookup in FlightBookingService booking_intents
    if not receipt_data:
        flight_booking_service = FlightBookingService()
        intent = None
        try:
            intent = flight_booking_service._get_intent(transaction_id, current_user.id)
        except BookingPermissionError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this payment receipt.",
            )
        except BookingNotFoundError:
            intent = None
        except Exception as exc:
            logger.warning("Could not query booking_intents for receipt %s: %s", transaction_id, exc)
            intent = None

        if intent:
            meta = intent.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            passengers = meta.get("passengers", [])
            lead_name = "—"
            if passengers:
                lead = passengers[0]
                lead_name = " ".join(x for x in [lead.get("given_name"), lead.get("family_name")] if x) or lead.get("name") or "—"

            pnr = intent.get("provider_reference") or meta.get("booking_reference") or meta.get("pnr")
            status_str = str(intent.get("status") or "").upper()
            pay_status = "captured" if status_str == "COMPLETED" else "pending"

            receipt_data = {
                "id": str(intent["id"]),
                "merchant": "TrustTrip Travel Planner",
                "service_type": "Flight Reservation",
                "service_name": f"Flight Booking ({intent.get('provider') or 'Duffel'})",
                "payment_status": pay_status,
                "booking_status": status_str,
                "amount_inr": float(intent.get("expected_amount") or 0.0),
                "currency": str(intent.get("currency") or "INR"),
                "razorpay_order_id": meta.get("razorpay_order_id") or "—",
                "razorpay_payment_id": meta.get("razorpay_payment_id") or "—",
                "confirmation_code": str(pnr) if pnr else None,
                "customer_name": lead_name,
                "customer_email": meta.get("contact_email") or current_user.email,
                "created_at": intent.get("created_at"),
            }

    # 3. Attempt lookup in legacy flight_transactions
    if not receipt_data:
        tx_rec = _store().get(transaction_id)
        if tx_rec:
            if tx_rec.get("customer_ref") and tx_rec.get("customer_ref") != user_id_str:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied to this payment receipt.",
                )
            receipt_data = {
                "id": str(tx_rec["id"]),
                "merchant": "TrustTrip Travel Planner",
                "service_type": "Flight Reservation",
                "service_name": "Flight Booking",
                "payment_status": tx_rec.get("payment_status") or "captured",
                "booking_status": tx_rec.get("booking_status") or "booked",
                "amount_inr": float(tx_rec.get("amount") or 0.0),
                "currency": str(tx_rec.get("currency") or "INR"),
                "razorpay_order_id": tx_rec.get("razorpay_order_id"),
                "razorpay_payment_id": tx_rec.get("razorpay_payment_id"),
                "confirmation_code": tx_rec.get("booking_reference") or tx_rec.get("duffel_order_id"),
                "customer_name": "—",
                "customer_email": current_user.email,
                "created_at": tx_rec.get("created_at"),
            }

    if not receipt_data:
        raise HTTPException(
            status_code=404,
            detail="Payment transaction record not found.",
        )

    try:
        pdf = await asyncio.to_thread(build_payment_receipt_pdf, receipt_data)
    except Exception as exc:
        logger.error("Failed to generate payment receipt PDF: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not generate payment receipt PDF.")

    filename = f"trust-trip-receipt-{transaction_id[:8]}.pdf"
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
    order: dict[str, Any] = {}
    tx: dict[str, Any] = {}

    # 1. Attempt lookup in canonical booking_intents (production Duffel path)
    flight_booking_service = FlightBookingService()
    intent = None
    try:
        intent = flight_booking_service._get_intent(transaction_id, current_user.id)
    except BookingPermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this ticket.",
        )
    except BookingNotFoundError:
        intent = None
    except Exception as exc:
        logger.warning("Could not query booking_intents for ticket %s: %s", transaction_id, exc)
        intent = None

    if intent:
        status_str = str(intent.get("status") or "").upper()
        if status_str != "COMPLETED":
            raise HTTPException(
                status_code=404,
                detail="A completed flight booking owned by you is required before downloading the ticket.",
            )
        meta = intent.get("metadata") or {}
        pnr = intent.get("provider_reference") or meta.get("booking_reference") or meta.get("pnr")
        duffel_order_id = meta.get("order_id") or str(pnr)

        order = {
            "id": duffel_order_id,
            "booking_reference": str(pnr) if pnr else None,
            "documents": meta.get("documents", []),
            "slices": meta.get("slices", []),
            "passengers": meta.get("passengers", []),
            "total_amount": float(intent.get("expected_amount") or 0.0),
            "total_currency": str(intent.get("currency") or "INR"),
            "created_at": meta.get("completed_at") or intent.get("created_at"),
            "live_mode": False,
        }
        tx = {
            "id": str(intent["id"]),
            "customer_ref": user_id_str,
            "booking_reference": str(pnr) if pnr else None,
            "duffel_order_id": duffel_order_id,
            "booking_status": "booked",
            "amount": float(intent.get("expected_amount") or 0.0),
            "currency": str(intent.get("currency") or "INR"),
            "passenger_json": json.dumps(meta.get("passengers", [])),
        }
        # Attempt upstream refresh from Duffel if duffel_order_id is available
        if duffel_order_id and duffel_order_id.startswith("ord_"):
            try:
                live_order = await asyncio.to_thread(FlightService().get_order, duffel_order_id)
                if live_order and isinstance(live_order, dict):
                    order.update(live_order)
            except Exception as exc:
                logger.info("Using persisted booking metadata for ticket PDF (upstream Duffel refresh skipped: %s)", exc)

    # 2. Fall back to legacy TransactionStore (flight_transactions)
    if not tx:
        tx_rec = _store().get(transaction_id)
        if tx_rec and tx_rec.get("customer_ref") == user_id_str and tx_rec.get("booking_status") == "booked" and tx_rec.get("duffel_order_id"):
            tx = tx_rec
            try:
                order = await asyncio.to_thread(FlightService().get_order, tx["duffel_order_id"])
            except FlightProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not tx:
        raise HTTPException(
            status_code=404,
            detail="A completed flight booking owned by you is required before downloading the ticket.",
        )

    try:
        pdf = await asyncio.to_thread(build_booking_pdf, order, tx)
        filename = f"Trust-Trip-MAS-{tx.get('booking_reference') or tx.get('duffel_order_id') or 'flight'}.pdf"
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Failed to build booking PDF: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not generate flight ticket PDF.")


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
    trip_store: TripStore = Depends(_trip_store),
):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True}
    user_id_str = str(current_user.id)

    # 1. Try canonical booking_intent first
    fb_service = FlightBookingService()
    intent = None
    try:
        intent = fb_service._get_intent(request.transaction_id, current_user.id)
    except (BookingNotFoundError, BookingPermissionError, ValueError, Exception):
        intent = None

    if intent:
        if str(intent.get("user_id")) != user_id_str:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Booking intent does not belong to you.",
            )
        trip_id = intent.get("trip_id")
        if trip_id:
            trip = trip_store.get_trip(trip_id, current_user.id)
            if not trip:
                raise HTTPException(
                    status_code=404,
                    detail="Parent trip not found or does not belong to you.",
                )

        status_str = str(intent.get("status") or "").upper()
        meta = intent.get("metadata") or {}
        if status_str == "CANCELLED" or meta.get("status") == "CANCELLED" or meta.get("cancellation"):
            raise HTTPException(
                status_code=409,
                detail="Booking is already cancelled.",
            )
        if status_str != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail=f"Only completed bookings can be cancelled (current status: {status_str}).",
            )

        order_id = meta.get("order_id") or intent.get("provider_reference")
        if not order_id:
            raise HTTPException(
                status_code=400,
                detail="No provider order found for this booking intent.",
            )
        try:
            quote = await asyncio.to_thread(FlightService().cancellation_quote, order_id)
            return {
                "success": True,
                "transaction_id": request.transaction_id,
                "booking_id": request.transaction_id,
                "quote": quote,
            }
        except FlightProviderTimeout as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except FlightProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 2. Legacy Section 10 fallback
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
    trip_store: TripStore = Depends(_trip_store),
):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True}
    user_id_str = str(current_user.id)

    # 1. Try canonical booking_intent first
    fb_service = FlightBookingService()
    intent = None
    try:
        intent = fb_service._get_intent(request.transaction_id, current_user.id)
    except (BookingNotFoundError, BookingPermissionError, ValueError, Exception):
        intent = None

    if intent:
        if str(intent.get("user_id")) != user_id_str:
            raise HTTPException(
                status_code=403,
                detail="Access denied: Booking intent does not belong to you.",
            )
        trip_id = intent.get("trip_id")
        if trip_id:
            trip = trip_store.get_trip(trip_id, current_user.id)
            if not trip:
                raise HTTPException(
                    status_code=404,
                    detail="Parent trip not found or does not belong to you.",
                )

        status_str = str(intent.get("status") or "").upper()
        meta = intent.get("metadata") or {}
        if status_str == "CANCELLED" or meta.get("status") == "CANCELLED" or meta.get("cancellation"):
            raise HTTPException(
                status_code=409,
                detail="Booking is already cancelled.",
            )
        if status_str != "COMPLETED":
            raise HTTPException(
                status_code=409,
                detail=f"Only completed bookings can be cancelled (current status: {status_str}).",
            )

        try:
            result = await asyncio.to_thread(FlightService().confirm_cancellation, request.cancellation_id)
        except FlightProviderTimeout as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except FlightProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        # Provider cancellation succeeded; update local state
        now_iso = datetime.now(timezone.utc).isoformat()
        meta["cancellation"] = result
        meta["cancelled_at"] = now_iso
        meta["status"] = "CANCELLED"
        intent["status"] = "CANCELLED"
        intent["metadata"] = meta

        refund = None
        payment_id = meta.get("razorpay_payment_id")
        refund_amount = result.get("refund_amount")
        if payment_id and refund_amount is not None:
            try:
                refund_currency = str(result.get("refund_currency") or result.get("currency") or "INR").upper()
                customer_amount = float(intent.get("expected_amount") or 0)
                if refund_currency == "INR":
                    customer_refund_amount = float(refund_amount)
                else:
                    stored_rate = meta.get("fx_rate")
                    if stored_rate is not None:
                        customer_refund_amount = float(refund_amount) * float(stored_rate)
                    else:
                        customer_refund_amount = float(refund_amount)
                customer_refund_amount = min(customer_refund_amount, customer_amount)
                if customer_refund_amount > 0:
                    refund = await asyncio.to_thread(
                        RazorpayService().refund,
                        payment_id,
                        customer_refund_amount,
                        f"cancel-refund-{request.transaction_id}",
                    )
            except Exception as exc:
                logger.warning("Refund processing for canonical booking %s failed: %s", request.transaction_id, exc)

        try:
            fb_service._save_intent(intent)
        except Exception as exc:
            if "booking_status_enum" in str(exc):
                intent["status"] = "ABANDONED"
                fb_service._save_intent(intent)
            else:
                logger.error("Failed to save cancelled intent: %s", exc)

        return {
            "success": True,
            "status": "cancelled",
            "cancellation": result,
            "refund": refund,
            "transaction_id": request.transaction_id,
            "booking_id": request.transaction_id,
        }

    # 2. Legacy Section 10 fallback
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


class CancelHotelRequest(BaseModel):
    transaction_id: str
    trip_id: str | None = None
    reason: str = "User requested cancellation"
    contact_email: str | None = None


@router.post(
    "/cancel/hotel",
    summary="Submit hotel booking cancellation request for concierge review",
)
async def cancel_hotel_booking(
    request: CancelHotelRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    user_id_str = str(current_user.id)
    hotel_tx = None
    try:
        hotel_tx = HotelTransactionStore().get(request.transaction_id)
    except Exception:
        hotel_tx = None
    if not hotel_tx and BookingReadService._IN_MEMORY_HOTEL_BOOKINGS:
        hotel_tx = BookingReadService._IN_MEMORY_HOTEL_BOOKINGS.get(request.transaction_id)

    if not hotel_tx:
        raise HTTPException(
            status_code=404,
            detail="Hotel booking transaction not found.",
        )

    # Ownership check
    tx_cust = str(hotel_tx.get("customer_ref") or "")
    if tx_cust != user_id_str:
        raise HTTPException(
            status_code=403,
            detail="Access denied: Hotel booking does not belong to you.",
        )

    # Trip ownership check
    tx_trip_id = str(hotel_tx.get("trip_id") or request.trip_id or "")
    if tx_trip_id:
        trip = trip_store.get_trip(tx_trip_id, current_user.id)
        if not trip:
            raise HTTPException(
                status_code=404,
                detail="Parent trip not found or does not belong to you.",
            )

    current_status = str(hotel_tx.get("booking_status") or "").lower()
    if current_status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Hotel booking is already cancelled.",
        )
    if current_status == "cancellation_requested":
        return {
            "success": True,
            "status": "cancellation_requested",
            "provider_cancelled": False,
            "message": "A cancellation request is already pending concierge review for this hotel booking.",
            "transaction_id": request.transaction_id,
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    cancellation_request = {
        "requested_at": now_iso,
        "reason": request.reason,
        "contact_email": request.contact_email or current_user.email,
        "status": "cancellation_requested",
    }

    # Update in DB
    try:
        raw_bjson = hotel_tx.get("booking_json") or {}
        if isinstance(raw_bjson, str):
            raw_bjson = json.loads(raw_bjson)
        raw_bjson["cancellation_request"] = cancellation_request
        HotelTransactionStore().update(
            request.transaction_id,
            booking_status="cancellation_requested",
            booking_json=raw_bjson,
        )
    except Exception as exc:
        logger.warning("Failed to update HotelTransactionStore: %s", exc)

    # Update in memory
    if request.transaction_id in BookingReadService._IN_MEMORY_HOTEL_BOOKINGS:
        mem_rec = BookingReadService._IN_MEMORY_HOTEL_BOOKINGS[request.transaction_id]
        mem_rec["booking_status"] = "cancellation_requested"
        bjson = mem_rec.get("booking_json") or {}
        if isinstance(bjson, str):
            bjson = json.loads(bjson)
        bjson["cancellation_request"] = cancellation_request
        mem_rec["booking_json"] = bjson

    return {
        "success": True,
        "status": "cancellation_requested",
        "provider_cancelled": False,
        "message": "Your hotel cancellation request has been submitted for concierge review. Our support team will process this with the property.",
        "transaction_id": request.transaction_id,
        "submitted_at": now_iso,
    }


class TripCancelRequest(BaseModel):
    booking_type: str = Field(default="all", pattern="^(all|flight|hotel)$")
    reason: str = "User requested cancellation"
    contact_email: str | None = None
    final_confirmation: bool = True


@router.post(
    "/{trip_id}/cancel",
    summary="Request cancellation for trip bookings (flight, hotel, or all)",
)
async def cancel_trip_bookings(
    trip_id: str,
    request: TripCancelRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    user_id_str = str(current_user.id)
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(
            status_code=404,
            detail="Trip not found or does not belong to you.",
        )

    read_service = BookingReadService()
    bookings = read_service.get_trip_bookings(trip_id=trip_id, user_id=current_user.id)
    flight_booking = bookings.get("flight")
    hotel_booking = bookings.get("hotel")

    results: dict[str, Any] = {
        "success": True,
        "trip_id": trip_id,
        "scope": request.booking_type,
        "components": {},
    }

    # 1. Flight cancellation
    if request.booking_type in ("all", "flight"):
        if flight_booking and flight_booking.get("is_booked"):
            flight_id = flight_booking.get("booking_id")
            order_id = flight_booking.get("order_id")
            if order_id:
                try:
                    quote = await asyncio.to_thread(FlightService().cancellation_quote, order_id)
                    cancel_res = await asyncio.to_thread(FlightService().confirm_cancellation, quote["id"])

                    fb_service = FlightBookingService()
                    intent = fb_service._get_intent(flight_id, current_user.id)
                    meta = intent.get("metadata") or {}
                    meta["cancellation"] = cancel_res
                    meta["cancelled_at"] = datetime.now(timezone.utc).isoformat()
                    meta["status"] = "CANCELLED"
                    intent["status"] = "CANCELLED"
                    intent["metadata"] = meta

                    refund = None
                    if meta.get("razorpay_payment_id") and cancel_res.get("refund_amount") is not None:
                        try:
                            refund = await asyncio.to_thread(
                                RazorpayService().refund,
                                meta["razorpay_payment_id"],
                                float(cancel_res["refund_amount"]),
                                f"cancel-refund-{flight_id}",
                            )
                        except Exception:
                            pass
                    try:
                        fb_service._save_intent(intent)
                    except Exception:
                        intent["status"] = "ABANDONED"
                        fb_service._save_intent(intent)

                    results["components"]["flight"] = {
                        "status": "cancelled",
                        "provider_cancelled": True,
                        "cancellation": cancel_res,
                        "refund": refund,
                    }
                except Exception as exc:
                    results["components"]["flight"] = {
                        "status": "failed",
                        "error": str(exc),
                    }
            else:
                results["components"]["flight"] = {
                    "status": "not_applicable",
                    "message": "No active provider order ID for flight.",
                }
        else:
            results["components"]["flight"] = {
                "status": "none_found",
                "message": "No active confirmed flight booking to cancel.",
            }

    # 2. Hotel cancellation
    if request.booking_type in ("all", "hotel"):
        if hotel_booking and hotel_booking.get("is_booked"):
            hotel_id = hotel_booking.get("booking_id")
            now_iso = datetime.now(timezone.utc).isoformat()
            cancellation_req = {
                "requested_at": now_iso,
                "reason": request.reason,
                "contact_email": request.contact_email or current_user.email,
                "status": "cancellation_requested",
            }
            try:
                raw_bjson = hotel_booking.get("booking_json") or {}
                if isinstance(raw_bjson, str):
                    raw_bjson = json.loads(raw_bjson)
                raw_bjson["cancellation_request"] = cancellation_req
                HotelTransactionStore().update(
                    hotel_id,
                    booking_status="cancellation_requested",
                    booking_json=raw_bjson,
                )
            except Exception:
                pass
            if hotel_id in BookingReadService._IN_MEMORY_HOTEL_BOOKINGS:
                BookingReadService._IN_MEMORY_HOTEL_BOOKINGS[hotel_id]["booking_status"] = "cancellation_requested"

            results["components"]["hotel"] = {
                "status": "cancellation_requested",
                "provider_cancelled": False,
                "message": "Hotel cancellation request submitted for concierge review.",
            }
        else:
            results["components"]["hotel"] = {
                "status": "none_found",
                "message": "No active confirmed hotel booking to cancel.",
            }

    # Summary message & truthful status
    flight_res = results["components"].get("flight", {})
    hotel_res = results["components"].get("hotel", {})

    if request.booking_type == "flight":
        if flight_res.get("status") == "cancelled":
            results["message"] = "Flight booking has been successfully cancelled with the carrier."
            results["status"] = "cancelled"
        else:
            results["message"] = flight_res.get("error") or flight_res.get("message") or "Flight cancellation failed."
            results["status"] = "failed"
    elif request.booking_type == "hotel":
        if hotel_res.get("status") == "cancellation_requested":
            results["message"] = "Hotel cancellation request submitted for concierge review."
            results["status"] = "cancellation_requested"
        else:
            results["message"] = hotel_res.get("message") or "No hotel booking found to cancel."
            results["status"] = "none_found"
    else:  # all
        if flight_res.get("status") == "cancelled" and hotel_res.get("status") == "cancellation_requested":
            results["message"] = "Flight cancelled with carrier; hotel cancellation submitted for concierge review."
            results["status"] = "partially_cancelled"
        elif flight_res.get("status") == "cancelled":
            results["message"] = "Flight cancelled with carrier."
            results["status"] = "partially_cancelled"
        elif hotel_res.get("status") == "cancellation_requested":
            results["message"] = "Hotel cancellation request submitted for concierge review."
            results["status"] = "cancellation_requested"
        else:
            results["message"] = "No confirmed bookings were eligible for cancellation."
            results["status"] = "none_found"

    return results


class OfferSelectionRequest(BaseModel):
    offer_type: str = Field(pattern="^(flight|hotel)$")
    snapshot_id: UUID
    offer_id: str | None = None
    room_id: UUID | str | None = None
    metadata: dict[str, Any] | None = None


class TripRevisionRequest(BaseModel):
    feedback: str = ""
    updated_constraints: dict[str, Any] = Field(default_factory=dict)
    expected_base_revision: int | None = Field(default=None, ge=0)
    expected_base_revision_id: UUID | str | None = None
    changes: dict[str, Any] | None = None


class GenerateRevisionPlanRequest(BaseModel):
    message: str | None = None
    query: str | None = None


class TripApprovalRequest(BaseModel):
    revision_id: UUID = Field(..., description="Exact revision ID to approve")
    expected_revision_number: int = Field(..., ge=1, description="Expected monotonically increasing revision number")
    notes: str | None = None


class TripRejectionRequest(BaseModel):
    expected_revision_number: int = Field(..., ge=1, description="Expected monotonically increasing revision number")
    reason: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] | None = None


class BookingApprovalRequest(BaseModel):
    revision_id: UUID = Field(..., description="Exact revision ID to authorize for booking")
    expected_revision_number: int = Field(..., ge=1, description="Expected monotonically increasing revision number")
    notes: str | None = None


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
        effective_constraints = req.updated_constraints
        if not effective_constraints and req.changes:
            effective_constraints = req.changes

        return trip_store.create_revision_atomic(
            trip_id=trip_id,
            user_id=current_user.id,
            feedback=req.feedback,
            updated_constraints=effective_constraints,
            expected_base_revision=req.expected_base_revision,
            expected_base_revision_id=req.expected_base_revision_id,
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


@router.post("/{trip_id}/revisions/{revision_id}/plan")
async def generate_revision_plan(
    trip_id: str,
    revision_id: str,
    req: GenerateRevisionPlanRequest | None = None,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Synthesize a multi-day itinerary for an active PROPOSED revision without incrementing revision_number."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    current_rev_id = trip.get("current_revision_id")
    if not current_rev_id or str(current_rev_id) != str(revision_id):
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Supplied revision {revision_id} does not match active trip revision ({current_rev_id}).",
        )

    status = trip.get("status")
    if status not in ("planning", "awaiting_approval"):
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Cannot generate plan for trip in '{status}' status.",
        )

    ev_store = EvidenceRevisionStore()
    target_rev = ev_store.get_revision(revision_id=str(revision_id), user_id=current_user.id)
    if not target_rev or str(target_rev.trip_id) != str(trip_id):
        raise HTTPException(status_code=404, detail="Revision not found or does not belong to this trip.")

    if ev_store.get_approval_for_revision(trip_id, str(revision_id)):
        raise HTTPException(status_code=409, detail="Conflict: Revision is already approved.")
    if ev_store.get_rejection_for_revision(trip_id, str(revision_id)):
        raise HTTPException(status_code=409, detail="Conflict: Revision is already rejected.")

    user_message = ((req and (req.message or req.query)) or "").strip()
    if not user_message:
        user_message = "Synthesize multi-day itinerary matching the updated travel constraints and dates."

    from services.planner_service import PlannerService
    try:
        result = PlannerService().plan_travel(
            user_input=user_message,
            thread_id=trip.get("thread_id") or (trip.get("metadata") or {}).get("thread_id"),
            authenticated_user_id=str(current_user.id),
            trip_id=str(trip_id),
        )
    except Exception as exc:
        err_msg = str(exc)
        if "429" in err_msg or "rate limit" in err_msg.lower():
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": err_msg,
                    "retryable": False,
                    "status_code": 429,
                },
            )
        if "503" in err_msg or "unavailable" in err_msg.lower():
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": err_msg,
                    "retryable": True,
                    "status_code": 503,
                },
                headers={"Retry-After": "5"},
            )
        logger.error("PlannerService invocation failed for revision %s: %s", revision_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to synthesize itinerary due to an internal error.")

    planner_success = result.get("success")
    if planner_success is None:
        if result.get("guardrail_allowed") is False or result.get("planning_blocked") is True or result.get("error"):
            planner_success = False
        else:
            planner_success = True

    if not planner_success:
        err_msg = result.get("error") or result.get("guardrail_reason") or "Travel planning failed."
        outage_code = result.get("status_code") or result.get("outage_status_code")
        if outage_code in (429, 503):
            headers = {"Retry-After": "5"} if outage_code == 503 else None
            return JSONResponse(
                status_code=outage_code,
                content={
                    "success": False,
                    "error": err_msg,
                    "retryable": outage_code == 503,
                    "status_code": outage_code,
                },
                headers=headers,
            )
        raise HTTPException(status_code=outage_code or 400, detail=err_msg)

    plan_json = result.get("plan_json")
    raw_plan = str(result.get("answer") or result.get("itinerary") or "")
    if not plan_json and not raw_plan:
        raise HTTPException(status_code=500, detail="Planner did not return an itinerary.")

    attached = trip_store.attach_plan_to_revision(
        trip_id=trip_id,
        user_id=current_user.id,
        revision_id=str(revision_id),
        plan_json=plan_json,
        raw_plan=raw_plan,
    )

    return {
        "success": True,
        "trip_id": str(trip_id),
        "revision_id": str(revision_id),
        "revision_number": int(trip.get("current_revision_number") or 1),
        "status": "awaiting_approval",
        "proposal_status": "PROPOSED",
        "plan_json": plan_json,
        "itinerary": raw_plan,
        "trip": attached["trip"],
    }


@router.post("/{trip_id}/approve")
async def approve_trip(
    trip_id: str,
    req: TripApprovalRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Explicitly approve an exact proposed plan revision under parent trip row lock."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    ev_store = EvidenceRevisionStore()
    target_rev = ev_store.get_revision(revision_id=str(req.revision_id), user_id=current_user.id)
    if not target_rev or str(target_rev.trip_id) != str(trip_id):
        raise HTTPException(status_code=404, detail="Revision not found or does not belong to this trip.")

    current_rev_id = trip.get("current_revision_id")
    if not current_rev_id or str(req.revision_id) != str(current_rev_id):
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Cannot approve revision {req.revision_id}: not current revision ({current_rev_id}).",
        )

    current_num = int(trip.get("current_revision_number") or 0)
    if current_num != req.expected_revision_number or target_rev.revision_number != req.expected_revision_number:
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Expected revision number {req.expected_revision_number} but current is {current_num}.",
        )

    meta = trip.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    meta = dict(meta)

    # 1. Check HITL status precedence (rejections and duplicate approvals return 409)
    rejection = ev_store.get_rejection_for_revision(trip_id, str(req.revision_id))
    if rejection:
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Revision {req.revision_id} is already rejected; cannot approve.",
        )
    existing_approval = ev_store.get_approval_for_revision(trip_id, str(req.revision_id))
    if existing_approval:
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Revision {req.revision_id} is already approved.",
        )

    # 2. Validate that an itinerary corresponds to authoritative constraints/duration
    constraints = meta.get("constraints") or {}
    plan = meta.get("plan") or {}
    raw_plan = meta.get("raw_plan") or ""

    ps = target_rev.plan_snapshot if isinstance(target_rev.plan_snapshot, dict) else {}

    const_start = str(constraints.get("start_date") or constraints.get("departure_date") or meta.get("start_date") or "")[:10]
    const_end = str(constraints.get("end_date") or constraints.get("return_date") or meta.get("end_date") or "")[:10]

    # Resolve active itinerary plan:
    # FIRST prefer trips.metadata.plan when it exists, contains a non-empty itinerary, and matches active revision constraints
    def _is_valid_working_plan(p: Any) -> bool:
        if not isinstance(p, dict) or not p:
            return False
        p_json = p.get("plan_json") if isinstance(p.get("plan_json"), dict) else p
        itin = (
            p_json.get("itinerary")
            or p_json.get("daily_plan")
            or p.get("itinerary")
            or p.get("daily_plan")
            or []
        )
        if not itin:
            return False
        dep = str(p_json.get("departure_date") or p.get("departure_date") or "")[:10]
        ret = str(p_json.get("return_date") or p.get("return_date") or "")[:10]
        if const_start and dep and dep != const_start:
            return False
        if const_end and ret and ret != const_end:
            return False
        return True

    if _is_valid_working_plan(plan):
        itinerary_plan = plan
    else:
        # Otherwise fallback to immutable revision snapshot hierarchy
        ps_meta_plan = (ps.get("metadata") or {}).get("plan") if isinstance(ps.get("metadata"), dict) else None
        itinerary_plan = (
            (ps.get("plan_json") if isinstance(ps.get("plan_json"), dict) else None)
            or (ps.get("plan") if isinstance(ps.get("plan"), dict) else None)
            or (ps_meta_plan if _is_valid_working_plan(ps_meta_plan) else None)
            or (plan if isinstance(plan, dict) and plan else None)
            or ps
        )
    if not isinstance(itinerary_plan, dict):
        itinerary_plan = {}

    plan_start = str(
        itinerary_plan.get("departure_date")
        or (itinerary_plan.get("plan_json") or {}).get("departure_date")
        or ""
    )[:10]
    plan_end = str(
        itinerary_plan.get("return_date")
        or (itinerary_plan.get("plan_json") or {}).get("return_date")
        or ""
    )[:10]

    if (const_start or const_end) and (itinerary_plan or raw_plan):
        if const_start and plan_start and const_start != plan_start:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve revision: Plan departure date ({plan_start}) does not match updated trip constraints ({const_start}). Please generate a revised plan before approving.",
            )
        if const_end and plan_end and const_end != plan_end:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve revision: Plan return date ({plan_end}) does not match updated trip constraints ({const_end}). Please generate a revised plan before approving.",
            )
        if target_rev.parent_revision_id and const_start and const_end:
            try:
                from datetime import date
                d1 = date.fromisoformat(const_start)
                d2 = date.fromisoformat(const_end)
                req_days = (d2 - d1).days
                itinerary = (
                    (itinerary_plan.get("plan_json") or {}).get("itinerary")
                    or (itinerary_plan.get("plan_json") or {}).get("daily_plan")
                    or itinerary_plan.get("itinerary")
                    or itinerary_plan.get("daily_plan")
                    or []
                )
                if itinerary and len(itinerary) not in (req_days, req_days + 1):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot approve revision: Plan duration ({len(itinerary)} days) does not match updated trip duration ({req_days} or {req_days + 1} days). Please generate a revised plan before approving.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

    now_iso = datetime.now(timezone.utc).isoformat()
    meta["approved_at"] = now_iso

    # Execute approval transaction under parent trip lock
    try:
        approval = ev_store.approve_revision_transaction(
            trip_id=trip_id,
            user_id=current_user.id,
            revision_id=str(req.revision_id),
            expected_revision=req.expected_revision_number,
            metadata={"approved_at": now_iso, "notes": req.notes},
        )
    except HTTPException as exc:
        if exc.status_code == 400 and "not current revision" in str(exc.detail):
            raise HTTPException(status_code=409, detail=f"Conflict: {exc.detail}")
        raise
    except Exception as exc:
        logger.error("Failed to approve trip revision: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Trip approval failed due to an internal error.")

    updated_trip = trip_store.update_trip(
        trip_id,
        current_user.id,
        metadata=meta,
    )

    return {
        "success": True,
        "trip_id": trip_id,
        "revision_id": str(req.revision_id),
        "status": "approved",
        "review_status": "APPROVED",
        "approval_id": str(approval.approval_id),
        "trip": updated_trip,
    }


@router.post("/{trip_id}/revisions/{revision_id}/reject")
async def reject_trip_revision(
    trip_id: str,
    revision_id: str,
    req: TripRejectionRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Explicitly reject an exact proposed trip revision under parent trip row lock."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    # Verify revision belongs to this trip
    ev_store = EvidenceRevisionStore()
    rev = ev_store.get_revision(revision_id=revision_id, user_id=current_user.id)
    if not rev or str(rev.trip_id) != str(trip_id):
        raise HTTPException(status_code=404, detail="Revision not found or does not belong to this trip.")

    current_rev_id = trip.get("current_revision_id")
    if not current_rev_id or str(revision_id) != str(current_rev_id):
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Cannot reject revision {revision_id}: not current revision ({current_rev_id}).",
        )

    current_num = int(trip.get("current_revision_number") or 0)
    if current_num != req.expected_revision_number or rev.revision_number != req.expected_revision_number:
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: Expected revision number {req.expected_revision_number} but current is {current_num}.",
        )

    try:
        rejection = ev_store.reject_revision_transaction(
            trip_id=trip_id,
            user_id=current_user.id,
            revision_id=revision_id,
            expected_revision=req.expected_revision_number,
            reason=req.reason,
            metadata=req.metadata,
        )
        updated_trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
        return {
            "success": True,
            "trip_id": trip_id,
            "revision_id": str(revision_id),
            "revision_number": rev.revision_number,
            "status": "REJECTED",
            "trip_status": "rejected",
            "rejection_id": str(rejection.rejection_id),
            "reason": rejection.reason,
            "trip": updated_trip,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to reject trip revision: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Trip revision rejection failed due to an internal error.")


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
    """List all immutable plan revisions for an owned trip with derived review status and diffs."""
    trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

    ev_store = EvidenceRevisionStore()
    revisions = ev_store.list_revisions_for_trip(trip_id=trip_id, user_id=current_user.id)

    from domain.plan_revision import compute_structured_diff

    enriched = []
    # Build dictionary of parent snapshots for computing historical diffs
    revs_by_id = {str(r.revision_id): r for r in revisions}

    for r in revisions:
        r_dict = r.model_dump()
        r_dict["revision_id"] = str(r.revision_id)
        r_dict["trip_id"] = str(r.trip_id)
        r_dict["user_id"] = str(r.user_id)
        r_dict["parent_revision_id"] = str(r.parent_revision_id) if r.parent_revision_id else None
        r_dict["version"] = r.revision_number
        r_dict["parent_id"] = r_dict["parent_revision_id"]

        approval = ev_store.get_approval_for_revision(trip_id, r.revision_id)
        rejection = ev_store.get_rejection_for_revision(trip_id, r.revision_id)

        status = "PROPOSED"
        decision_metadata: dict[str, Any] = {}
        if approval:
            status = "APPROVED"
            decision_metadata = {
                "decision": "APPROVED",
                "approved_at": approval.approved_at.isoformat() if hasattr(approval.approved_at, "isoformat") else str(approval.approved_at),
                "approved_by_user_id": str(approval.approved_by_user_id),
                **approval.approval_metadata,
            }
        elif rejection:
            status = "REJECTED"
            decision_metadata = {
                "decision": "REJECTED",
                "rejected_at": rejection.rejected_at.isoformat() if hasattr(rejection.rejected_at, "isoformat") else str(rejection.rejected_at),
                "rejected_by_user_id": str(rejection.rejected_by_user_id),
                "reason": rejection.reason,
                **rejection.rejection_metadata,
            }

        r_dict["status"] = status
        r_dict["is_approved"] = (status == "APPROVED")
        r_dict["is_rejected"] = (status == "REJECTED")
        r_dict["decision_metadata"] = decision_metadata

        parent_snapshot: dict[str, Any] = {}
        if r.parent_revision_id and str(r.parent_revision_id) in revs_by_id:
            parent_rev = revs_by_id[str(r.parent_revision_id)]
            parent_snapshot = parent_rev.plan_snapshot or {}

        curr_snapshot = r.plan_snapshot or {}
        curr_constraints = curr_snapshot.get("constraints") or {}
        parent_constraints = parent_snapshot.get("constraints") or {}
        notes = (r.change_set or {}).get("freeform_notes") or ""

        diff = compute_structured_diff(parent_constraints, curr_constraints, notes)
        r_dict["structured_diff"] = diff.model_dump()
        r_dict["structured_changes"] = diff.model_dump()

        # Section 9: Booking authorization enrichment
        from services.authorization_store import AuthorizationStore
        auth_store = AuthorizationStore()
        auth = auth_store.get_booking_authorization(trip_id, r.revision_id)
        r_dict["is_booking_authorized"] = (auth is not None)
        if auth:
            r_dict["booking_authorization"] = {
                "authorization_id": str(auth.authorization_id),
                "authorized_at": auth.authorized_at.isoformat() if hasattr(auth.authorized_at, "isoformat") else str(auth.authorized_at),
                "revision_number": auth.revision_number,
                "metadata": auth.authorization_metadata,
            }
        else:
            r_dict["booking_authorization"] = None

        enriched.append(r_dict)

    from services.authorization_store import AuthorizationStore
    auth_store = AuthorizationStore()
    cur_rid = str(trip.get("current_revision_id") or "")
    cur_auth = auth_store.get_booking_authorization(trip_id, cur_rid) if cur_rid else None

    return {
        "success": True,
        "trip_id": trip_id,
        "current_revision_id": cur_rid,
        "current_revision_number": int(trip.get("current_revision_number") or 0),
        "is_booking_authorized": (cur_auth is not None),
        "booking_authorization": {
            "authorization_id": str(cur_auth.authorization_id),
            "authorized_at": cur_auth.authorized_at.isoformat() if hasattr(cur_auth.authorized_at, "isoformat") else str(cur_auth.authorized_at),
            "revision_number": cur_auth.revision_number,
            "metadata": cur_auth.authorization_metadata,
        } if cur_auth else None,
        "revisions": enriched,
    }


@router.post("/{trip_id}/booking-approval")
async def approve_booking(
    trip_id: str,
    req: BookingApprovalRequest,
    current_user: UserResponse = Depends(get_current_user),
    trip_store: TripStore = Depends(_trip_store),
):
    """Explicitly authorize an approved trip revision for booking under parent trip row lock."""
    from services.authorization_store import AuthorizationStore
    auth_store = AuthorizationStore()

    try:
        authorization = auth_store.create_booking_authorization_atomic(
            trip_id=trip_id,
            user_id=current_user.id,
            revision_id=str(req.revision_id),
            expected_revision_number=req.expected_revision_number,
            metadata={"notes": req.notes} if req.notes else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to authorize trip revision for booking: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Booking authorization failed due to an internal error.")

    updated_trip = trip_store.get_trip(trip_id=trip_id, user_id=current_user.id)
    return {
        "success": True,
        "trip_id": trip_id,
        "revision_id": str(req.revision_id),
        "revision_number": authorization.revision_number,
        "authorization_id": str(authorization.authorization_id),
        "authorized_at": authorization.authorized_at.isoformat() if hasattr(authorization.authorized_at, "isoformat") else str(authorization.authorized_at),
        "is_authorized": True,
        "trip": updated_trip,
    }


@router.get("/{trip_id}/booking-eligibility")
async def get_trip_booking_eligibility(
    trip_id: str,
    revision_id: str | None = None,
    expected_revision_number: int | None = None,
    current_user: UserResponse = Depends(get_current_user),
):
    """Get runtime derived booking eligibility for a trip/revision."""
    from services.authorization_store import AuthorizationStore
    auth_store = AuthorizationStore()
    result = auth_store.check_booking_eligibility(
        trip_id=trip_id,
        user_id=current_user.id,
        revision_id=revision_id,
        expected_revision_number=expected_revision_number,
    )
    return {
        "success": True,
        "eligibility": result.model_dump(),
    }
