from __future__ import annotations
import asyncio
import json, os, uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies.auth import get_optional_current_user, get_current_user
from domain.booking import HotelCheckoutSnapshotRequest
from domain.user import UserResponse
from services.hotel.hotel_service import HotelService
from services.hotel.hotel_provider import HotelProviderError
from services.razorpay_service import RazorpayService, RazorpayError
from services.currency_converter import CurrencyConverter, CurrencyConversionError
from services.hotel_transaction_store import HotelTransactionStore
from services.hotel_booking_pdf import build_hotel_booking_pdf
from services.hotel_booking_service import HotelBookingService
from services.flight_booking_service import FareChangedException, OfferWithdrawnException

router=APIRouter(prefix="/api/hotel-payments", tags=["hotel-payments"])


@router.post("/create-order-from-snapshot")
async def create_order_from_snapshot(
    req: HotelCheckoutSnapshotRequest,
    current_user: UserResponse = Depends(get_current_user),
):
    """Initiate hotel checkout via verified snapshot_id with tenant validation and revalidation gate.

    Section 10: trip_id, revision_id, expected_revision_number are required so the
    server can call assert_booking_eligible before payment order creation.
    """
    service = HotelBookingService()
    try:
        res = await service.create_checkout_order_from_snapshot(
            snapshot_id=req.snapshot_id,
            user_id=current_user.id,
            trip_id=req.trip_id,
            revision_id=req.revision_id,
            expected_revision_number=req.expected_revision_number,
            payment_idempotency_key=req.payment_idempotency_key,
            holder=req.holder,
            guests=req.guests,
            customer_ref=req.customer_ref,
            final_confirmation=req.final_confirmation,
        )
        return res
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
    except OfferWithdrawnException as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # Handle BookingEligibilityError from assert_booking_eligible
        try:
            from domain.booking_authorization import BookingEligibilityError
            if isinstance(exc, BookingEligibilityError):
                raise HTTPException(status_code=409, detail={"code": "BOOKING_NOT_ELIGIBLE", "message": str(exc)})
        except ImportError:
            pass
        raise HTTPException(status_code=500, detail=f"Hotel checkout order creation failed: {exc}")


class HotelPaymentOrder(BaseModel):
    offer: dict[str, Any]
    customer_ref: str | None = None
    prebook: dict[str, Any]
    holder: dict[str, Any]
    guests: list[dict[str, Any]]
    trip_id: str
    revision_id: str
    expected_revision_number: int = Field(ge=1)
    payment_idempotency_key: str = Field(min_length=8, max_length=128)
    final_confirmation: bool = False

class HotelVerifyBook(BaseModel):
    transaction_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    offer: dict[str, Any]
    prebook: dict[str, Any]
    holder: dict[str, Any]
    guests: list[dict[str, Any]]
    trip_id: str
    revision_id: str
    expected_revision_number: int = Field(ge=1)
    customer_ref: str | None = None
    final_confirmation: bool = False


@router.post("/create-order")
async def create_order(
    req: HotelPaymentOrder,
    current_user: UserResponse = Depends(get_current_user),
):
    if not req.final_confirmation:
        raise HTTPException(status_code=409, detail="Final user confirmation is required before hotel payment.")
    offer=req.offer; pre=req.prebook
    if not pre.get("prebook_id"):
        raise HTTPException(status_code=422, detail="Hotel prebook is missing or expired.")
    # Section 10: Reject simulated prebook before creating a real payment order
    if str(pre.get("status", "")).lower() == "simulated":
        raise HTTPException(
            status_code=409,
            detail="Hotel prebook returned a simulated reference. Cannot create a real payment order without an authoritative supplier hold.",
        )
    amount_inr=offer.get("price_inr")
    if amount_inr is None:
        try:
            amount_inr=float(offer["price"])*float(CurrencyConverter().get_rate(offer["currency"],"INR")["rate"])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Unable to determine INR payment amount: {exc}")
    from services.authorization_store import AuthorizationStore
    try:
        AuthorizationStore().assert_booking_eligible(
            trip_id=req.trip_id, user_id=current_user.id,
            revision_id=req.revision_id,
            expected_revision_number=req.expected_revision_number,
        )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=409, detail={"code": "BOOKING_NOT_ELIGIBLE", "message": str(exc)}) from exc
    rz=RazorpayService()
    if not rz.configured:
        raise HTTPException(status_code=503, detail="Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.")
    effective_customer_ref = str(current_user.id)
    store = HotelTransactionStore()
    tx_id = str(uuid.uuid4())
    try:
        reservation = store.reserve_payment_order(
            id=tx_id, customer_ref=effective_customer_ref, provider=str(offer.get("provider")),
            hotel_name=str(offer.get("hotel_name") or "Hotel"), offer=offer, prebook=pre,
            holder=req.holder, guests=req.guests, amount_inr=round(float(amount_inr), 2),
            trip_id=req.trip_id, revision_id=req.revision_id,
            revision_number=req.expected_revision_number,
            payment_idempotency_key=req.payment_idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if str(reservation["id"]) != tx_id:
        for _ in range(100):
            if reservation.get("razorpay_order_id"):
                return {"success": True, "idempotent_replay": True, "transaction_id": reservation["id"],
                        "razorpay_order_id": reservation["razorpay_order_id"], "amount_inr": float(reservation["amount_inr"]),
                        "currency": "INR", "key_id": rz.key_id}
            await asyncio.sleep(0.05)
            reservation = store.get_by_payment_idempotency_key(req.payment_idempotency_key) or reservation
        if reservation.get("booking_status") == "payment_order_failed":
            raise HTTPException(status_code=409, detail="The prior hotel payment-order request failed; use a new idempotency key after reviewing its state.")
        if reservation.get("razorpay_order_id"):
            return {"success": True, "idempotent_replay": True, "transaction_id": reservation["id"],
                    "razorpay_order_id": reservation["razorpay_order_id"], "amount_inr": float(reservation["amount_inr"]),
                    "currency": "INR", "key_id": rz.key_id}
        raise HTTPException(status_code=409, detail="A hotel payment order with this idempotency key is still being created.")
    try:
        order=rz.create_order(float(amount_inr), f"hotel_{tx_id[:32]}", {"transaction_id":tx_id,"provider":str(offer.get("provider")),"hotel":str(offer.get("hotel_name","Hotel"))[:120]})
    except RazorpayError as exc:
        store.update(tx_id, payment_status="order_failed", booking_status="payment_order_failed", error="Razorpay order creation failed.")
        raise HTTPException(status_code=502, detail=str(exc))
    store.update(tx_id, razorpay_order_id=order.get("id"))
    return {"success":True,"transaction_id":tx_id,"razorpay_order_id":order.get("id"),"amount_inr":round(float(amount_inr),2),"currency":"INR","key_id":rz.key_id,"provider":offer.get("provider"),"hotel_name":offer.get("hotel_name")}

@router.post("/verify-and-book")
async def verify_and_book(
    req: HotelVerifyBook,
    current_user: UserResponse = Depends(get_current_user),
):
    if not req.final_confirmation:
        raise HTTPException(status_code=409, detail="Final user confirmation is required before hotel booking.")
    store = HotelTransactionStore()
    pending = store.get(req.transaction_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Hotel payment transaction not found. Start checkout again.")
    effective_customer_ref = str(current_user.id) if current_user else req.customer_ref
    if pending.get("customer_ref"):
        if not effective_customer_ref or effective_customer_ref != pending.get("customer_ref"):
            raise HTTPException(status_code=403, detail="This hotel transaction does not belong to the current customer.")
    if pending.get("razorpay_order_id") != req.razorpay_order_id:
        raise HTTPException(status_code=409, detail="Razorpay order does not match the hotel transaction.")

    if str(pending.get("booking_status") or "").lower() == "booked" and pending.get("booking_json"):
        return {"success": True, "transaction_id": req.transaction_id, "payment_captured": True, "booking": pending.get("booking_json"), "amount_inr": float(pending["amount_inr"]), "already_booked": True, "pdf_available": True}

    if str(pending.get("booking_status") or "").lower() == "booking_in_progress":
        raise HTTPException(status_code=409, detail="Hotel booking is already in progress for this transaction. Please wait or refresh.")

    rz = RazorpayService()
    try:
        if not rz.verify_checkout_signature(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid Razorpay payment signature.")
        payment = rz.fetch_payment(req.razorpay_payment_id)
        if payment.get("order_id") != pending.get("razorpay_order_id"):
            raise HTTPException(status_code=400, detail="Payment is linked to a different Razorpay order.")
        if str(payment.get("currency") or "").upper() != "INR":
            raise HTTPException(status_code=400, detail="The captured hotel payment is not in INR.")
        if int(payment.get("amount") or 0) != int(round(float(pending["amount_inr"]) * 100)):
            raise HTTPException(status_code=400, detail="Captured payment amount does not match the stored hotel order.")
        if str(payment.get("status", "")).lower() != "captured":
            raise HTTPException(status_code=409, detail="Razorpay payment is not captured yet.")
    except RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Persist the verified capture before the last eligibility check. If revision
    # eligibility changed while the user was in checkout, retain payment state for
    # review and block supplier fulfillment.
    store.update(req.transaction_id, payment_status="captured", booking_status="payment_captured",
                 razorpay_payment_id=req.razorpay_payment_id)

    # Atomic compare-and-set: claim transaction for supplier booking
    claimed = store.claim_booking(req.transaction_id, req.razorpay_payment_id)
    if not claimed:
        latest = store.get(req.transaction_id) or {}
        if str(latest.get("booking_status") or "").lower() == "booked":
            return {"success": True, "transaction_id": req.transaction_id, "payment_captured": True, "booking": latest.get("booking_json"), "amount_inr": float(latest.get("amount_inr") or 0), "already_booked": True, "pdf_available": True}
        raise HTTPException(status_code=409, detail="Hotel booking is already in progress or already processed.")

    try:
        from services.authorization_store import AuthorizationStore
        from domain.booking_authorization import BookingEligibilityError
        AuthorizationStore().assert_booking_eligible(
            trip_id=pending["trip_id"], user_id=current_user.id,
            revision_id=pending["revision_id"],
            expected_revision_number=int(pending["revision_number"]),
        )
    except Exception as exc:
        from domain.booking_authorization import BookingEligibilityError
        if isinstance(exc, BookingEligibilityError):
            store.update(req.transaction_id, payment_status="captured",
                         booking_status="stale_revision_review",
                         error=f"Eligibility check failed immediately before fulfillment: {exc}")
            return {"success": False, "payment_captured": True, "requires_refund": True,
                    "transaction_id": req.transaction_id,
                    "error": "Trip revision changed after payment capture. Booking requires manual review."}
        raise

    try:
        from services.authorization_store import AuthorizationStore
        AuthorizationStore().assert_booking_eligible(
            trip_id=req.trip_id, user_id=current_user.id,
            revision_id=req.revision_id,
            expected_revision_number=req.expected_revision_number,
        )
    except Exception as exc:
        from domain.booking_authorization import BookingEligibilityError
        if isinstance(exc, BookingEligibilityError):
            store.update(req.transaction_id, payment_status="captured",
                         booking_status="stale_revision_review",
                         error=f"Eligibility check failed immediately before fulfillment: {exc}")
            return {
                "success": False,
                "payment_captured": True,
                "requires_refund": True,
                "transaction_id": req.transaction_id,
                "error": "Trip revision changed after payment capture. Booking requires manual review.",
            }
        raise
    try:
        booking = HotelService().book(req.offer, req.prebook, req.holder, req.guests, req.transaction_id)
        store.update(req.transaction_id, payment_status="captured", booking_status="booked", razorpay_payment_id=req.razorpay_payment_id, booking_json=booking, error=None)
    except HotelProviderError as exc:
        store.update(req.transaction_id, payment_status="captured", booking_status="booking_failed", razorpay_payment_id=req.razorpay_payment_id, error=str(exc))
        return {"success": False, "payment_captured": True, "booking_pending": False, "requires_refund": True, "transaction_id": req.transaction_id, "error": f"Payment succeeded but hotel booking failed: {exc}"}
    except Exception:
        store.update(req.transaction_id, payment_status="captured", booking_status="booking_failed", razorpay_payment_id=req.razorpay_payment_id, error="Hotel booking failed after payment capture; reconciliation/refund is required.")
        return {"success": False, "payment_captured": True, "booking_pending": False, "requires_refund": True, "transaction_id": req.transaction_id, "error": "Payment succeeded but the hotel booking could not be completed. Reconciliation/refund is required."}

    # Separate PDF generation lifecycle: failure must NOT invalidate confirmed booking
    pdf_available = False
    try:
        record = store.get(req.transaction_id)
        build_hotel_booking_pdf(record)
        from datetime import datetime, timezone
        store.update(req.transaction_id, pdf_generated_at=datetime.now(timezone.utc))
        pdf_available = True
    except Exception as pdf_exc:
        import logging
        logging.getLogger(__name__).warning("Hotel booking confirmation PDF generation failed for transaction %s: %s", req.transaction_id, pdf_exc)

    return {
        "success": True,
        "transaction_id": req.transaction_id,
        "payment_captured": True,
        "booking": booking,
        "amount_inr": float(pending["amount_inr"]),
        "pdf_available": pdf_available,
    }
