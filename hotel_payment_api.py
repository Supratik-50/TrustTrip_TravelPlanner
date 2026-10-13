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
    prebook: dict[str, Any] = Field(default_factory=dict)
    holder: dict[str, Any]
    guests: list[dict[str, Any]]
    trip_id: str
    revision_id: str
    expected_revision_number: int = Field(ge=1)
    payment_idempotency_key: str = Field(min_length=8, max_length=128)
    final_confirmation: bool = False
    snapshot_id: str | None = None

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
    from services.search_session_store import SearchSessionStore
    from services.trip_store import TripStore

    offer = req.offer
    pre = req.prebook

    # Rehydrate authoritative prebook from trip metadata if client lost in-memory state
    trip_store = TripStore()
    trip_rec = trip_store.get_trip(req.trip_id, current_user.id)
    t_meta = {}
    if trip_rec:
        t_meta = trip_rec.get("metadata") or {}
        if isinstance(t_meta, str):
            try:
                t_meta = json.loads(t_meta)
            except Exception:
                t_meta = {}

    if not pre or not pre.get("prebook_id"):
        retained_pre = t_meta.get("hotel_prebook")
        if retained_pre and retained_pre.get("prebook_id"):
            pre = retained_pre

    if not pre or not pre.get("prebook_id"):
        raise HTTPException(status_code=422, detail="Hotel prebook is missing or expired.")

    # Section 10: Reject simulated prebook before creating a real payment order
    if str(pre.get("status", "")).lower() == "simulated":
        raise HTTPException(
            status_code=409,
            detail="Hotel prebook returned a simulated reference. Cannot create a real payment order without an authoritative supplier hold.",
        )

    # HRP-02: Derive authoritative payment amount from server-side snapshot if available
    snap_id = req.snapshot_id or (offer.get("snapshot_id") if isinstance(offer, dict) else None)
    if not snap_id and isinstance(pre, dict):
        snap_id = pre.get("snapshot_id")
    if not snap_id:
        snap_id = t_meta.get("selected_hotel_snapshot_id") or (t_meta.get("selected_hotel") or {}).get("snapshot_id")

    snapshot = None
    if snap_id:
        snap_store = SearchSessionStore()
        snapshot = snap_store.get_snapshot(snap_id, current_user.id)
        if not snapshot:
            raise HTTPException(status_code=404, detail=f"Hotel offer snapshot '{snap_id}' not found or access denied.")

    if snapshot:
        # Enforce server-authoritative snapshot price breakdown
        auth_offer = snapshot.normalized_offer
        auth_inr = auth_offer.price.inr_reference_amount
        if auth_inr is not None:
            amount_inr = float(auth_inr)
        else:
            try:
                converter = CurrencyConverter()
                rate_info = converter.get_rate(str(auth_offer.price.currency), "INR")
                amount_inr = float(auth_offer.price.total_amount) * float(rate_info["rate"])
            except Exception:
                amount_inr = float(auth_offer.price.total_amount) * 85.0
        # Overwrite offer dictionary so client cannot tamper with stored prices
        offer["price_inr"] = round(amount_inr, 2)
        offer["price"] = float(auth_offer.price.total_amount)
        offer["currency"] = str(auth_offer.price.currency)
        offer["snapshot_id"] = str(snapshot.snapshot_id)
    else:
        amount_inr = offer.get("price_inr")
        if amount_inr is None:
            try:
                amount_inr = float(offer["price"]) * float(CurrencyConverter().get_rate(offer["currency"], "INR")["rate"])
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Unable to determine INR payment amount: {exc}")
        if float(amount_inr) <= 0:
            raise HTTPException(status_code=422, detail="Hotel payment amount must be strictly positive.")

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
    if req.trip_id:
        existing_txs = store.get_by_trip(str(req.trip_id), customer_ref=effective_customer_ref)
        for etx in existing_txs:
            if etx.get("payment_idempotency_key") == req.payment_idempotency_key:
                continue
            etx_b = str(etx.get("booking_status") or "").lower()
            etx_p = str(etx.get("payment_status") or "").lower()
            if etx_b == "booked":
                raise HTTPException(status_code=409, detail="Hotel booking has already been confirmed for this trip.")
            if etx_b in {"reconciliation_pending", "reconciliation_in_progress", "booking_in_progress"} or etx_p == "captured":
                raise HTTPException(status_code=409, detail="A payment for this hotel stay has already been captured and is pending verification. Please check confirmation status before initiating another order.")
            if etx_p in {"refund_pending", "refund_failed"}:
                raise HTTPException(status_code=409, detail="A previous payment refund for this hotel stay is currently being processed. New payment cannot be initiated until earlier refund is resolved.")

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

    if str(pending.get("payment_status") or "").lower() == "refunded":
        return {
            "success": False,
            "transaction_id": req.transaction_id,
            "payment_captured": True,
            "refunded": True,
            "requires_refund": False,
            "error": pending.get("error") or "Payment was refunded because hotel booking could not be completed.",
        }

    if str(pending.get("booking_status") or "").lower() == "reconciliation_pending":
        rec_status, rec_booking = HotelService().reconcile(pending.get("offer_json") or req.offer, req.transaction_id)
        if rec_status == "CONFIRMED" and rec_booking:
            store.update(req.transaction_id, payment_status="captured", booking_status="booked",
                         razorpay_payment_id=req.razorpay_payment_id or pending.get("razorpay_payment_id"),
                         booking_json=rec_booking, error=None)
            return {"success": True, "transaction_id": req.transaction_id, "payment_captured": True,
                    "booking": rec_booking, "amount_inr": float(pending["amount_inr"]), "already_booked": False,
                    "reconciled": True, "pdf_available": True}
        elif rec_status == "NOT_FOUND":
            # Confirmed supplier did not create booking -> process refund
            rz = RazorpayService()
            refund_res = None
            try:
                refund_res = rz.refund(
                    payment_id=req.razorpay_payment_id or pending.get("razorpay_payment_id"),
                    amount_rupees=float(pending["amount_inr"]),
                    idempotency_key=f"ref_ht_{req.transaction_id.replace('-', '')[:32]}",
                )
            except Exception:
                pass
            if refund_res and str(refund_res.get("status", "")).lower() in {"processed", "captured", "refunded"}:
                store.update(req.transaction_id, payment_status="refunded", booking_status="booking_failed",
                             booking_json={"refund": refund_res})
                return {"success": False, "transaction_id": req.transaction_id, "payment_captured": True,
                        "refunded": True, "refund_id": refund_res.get("id"), "requires_refund": False,
                        "error": "Supplier confirmed no booking exists. Payment has been refunded."}
        raise HTTPException(status_code=409, detail="Hotel booking verification is still pending supplier confirmation. Please wait or refresh.")

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
        if str(latest.get("booking_status") or "").lower() in {"reconciliation_pending", "reconciliation_in_progress"}:
            claimed_rec = store.claim_reconciliation(req.transaction_id)
            if not claimed_rec:
                raise HTTPException(status_code=409, detail="Hotel booking verification is still pending supplier confirmation. Please wait or refresh.")
            rec_claimed = True
            try:
                rec_status, rec_booking = HotelService().reconcile(latest.get("offer_json") or req.offer, req.transaction_id)
                if rec_status == "CONFIRMED" and rec_booking:
                    store.update(req.transaction_id, payment_status="captured", booking_status="booked",
                                 booking_json=rec_booking, error=None)
                    rec_claimed = False
                    return {"success": True, "transaction_id": req.transaction_id, "payment_captured": True, "booking": rec_booking, "amount_inr": float(latest.get("amount_inr") or 0), "already_booked": False, "reconciled": True, "pdf_available": True}
                elif rec_status == "NOT_FOUND":
                    refund_res = None
                    try:
                        refund_res = rz.refund(
                            payment_id=req.razorpay_payment_id,
                            amount_rupees=float(latest["amount_inr"]),
                            idempotency_key=f"ref_ht_{req.transaction_id.replace('-', '')[:32]}",
                        )
                    except Exception:
                        pass
                    if refund_res and str(refund_res.get("status", "")).lower() in {"processed", "captured", "refunded"}:
                        store.update(req.transaction_id, payment_status="refunded", booking_status="booking_failed",
                                     booking_json={"refund": refund_res})
                        rec_claimed = False
                        return {"success": False, "payment_captured": True, "refunded": True, "requires_refund": False,
                                "transaction_id": req.transaction_id, "error": "Supplier confirmed no booking exists. Payment has been refunded."}
                raise HTTPException(status_code=409, detail="Hotel booking verification is still pending supplier confirmation. Please wait or refresh.")
            finally:
                if rec_claimed:
                    store.release_reconciliation_claim(req.transaction_id)
        if str(latest.get("payment_status") or "").lower() == "refunded":
            return {"success": False, "transaction_id": req.transaction_id, "payment_captured": True, "refunded": True, "requires_refund": False, "error": latest.get("error") or "Payment was refunded because hotel booking could not be completed."}
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
    except Exception as exc:
        # HRP-04: Reconcile before failing or refunding
        rec_status, rec_booking = HotelService().reconcile(req.offer, req.transaction_id)
        if rec_status == "CONFIRMED" and rec_booking:
            booking = rec_booking
            store.update(req.transaction_id, payment_status="captured", booking_status="booked",
                         razorpay_payment_id=req.razorpay_payment_id, booking_json=booking, error=None)
        elif rec_status == "UNCERTAIN":
            # Response lost / connection ambiguous. Maintain recoverable reconciliation state
            store.update(req.transaction_id, payment_status="captured", booking_status="reconciliation_pending",
                         razorpay_payment_id=req.razorpay_payment_id,
                         error=f"Supplier booking response lost, status uncertain: {exc}")
            return {
                "success": False,
                "payment_captured": True,
                "booking_pending": True,
                "reconciliation_pending": True,
                "requires_refund": False,
                "transaction_id": req.transaction_id,
                "error": "Supplier booking status is uncertain after network interruption. Verification is pending supplier confirmation.",
            }
        else:
            # rec_status == "NOT_FOUND": Confirmed no booking upstream -> HRP-03: execute automated refund
            refund_res = None
            refund_err = None
            try:
                refund_res = rz.refund(
                    payment_id=req.razorpay_payment_id,
                    amount_rupees=float(pending["amount_inr"]),
                    idempotency_key=f"ref_ht_{req.transaction_id.replace('-', '')[:32]}",
                )
            except Exception as rz_exc:
                refund_err = str(rz_exc)

            if refund_res and str(refund_res.get("status", "")).lower() in {"processed", "captured", "refunded"}:
                store.update(req.transaction_id, payment_status="refunded", booking_status="booking_failed",
                             razorpay_payment_id=req.razorpay_payment_id,
                             booking_json={"refund": refund_res},
                             error=f"Hotel booking failed: {exc}. Payment refunded: {refund_res.get('id')}")
                return {
                    "success": False,
                    "payment_captured": True,
                    "booking_pending": False,
                    "refunded": True,
                    "refund_id": refund_res.get("id"),
                    "requires_refund": False,
                    "transaction_id": req.transaction_id,
                    "error": f"Payment refunded because hotel booking could not be completed: {exc}",
                }
            else:
                store.update(req.transaction_id,
                             payment_status="refund_failed" if refund_err else "refund_pending",
                             booking_status="booking_failed",
                             razorpay_payment_id=req.razorpay_payment_id,
                             error=f"Hotel booking failed: {exc}. Refund attempt pending: {refund_err or 'unprocessed'}")
                return {
                    "success": False,
                    "payment_captured": True,
                    "booking_pending": False,
                    "refunded": False,
                    "requires_refund": True,
                    "transaction_id": req.transaction_id,
                    "error": f"Payment succeeded but hotel booking could not be completed: {exc}. Refund is pending reconciliation.",
                }

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


@router.get("/by-trip/{trip_id}")
async def get_hotel_payment_by_trip(
    trip_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Recover the latest hotel payment transaction for a trip and current user."""
    store = HotelTransactionStore()
    txs = store.get_by_trip(trip_id, customer_ref=str(current_user.id))
    if not txs:
        return {"has_payment": False, "transaction": None}

    # Prioritize: 1) booked, 2) captured/reconciling, 3) refund_pending/refund_failed, 4) newest
    def _rank(t):
        b = str(t.get("booking_status") or "").lower()
        p = str(t.get("payment_status") or "").lower()
        if b == "booked":
            return 4
        if b in {"reconciliation_pending", "reconciliation_in_progress", "booking_in_progress"} or p == "captured":
            return 3
        if p in {"refund_pending", "refund_failed"}:
            return 2
        return 1

    ranked = sorted(txs, key=_rank, reverse=True)
    latest = ranked[0]
    b_status = str(latest.get("booking_status") or "").lower()
    p_status = str(latest.get("payment_status") or "").lower()
    is_captured = p_status in {"captured", "refund_pending", "refund_failed"} or b_status in {
        "payment_captured", "booking_in_progress", "reconciliation_pending", "reconciliation_in_progress", "booked"
    }
    is_refund_pending = p_status in {"refund_pending", "refund_failed"}
    return {
        "has_payment": True,
        "transaction_id": latest["id"],
        "payment_status": latest.get("payment_status"),
        "booking_status": latest.get("booking_status"),
        "is_captured": is_captured,
        "reconciliation_pending": b_status in {"reconciliation_pending", "reconciliation_in_progress"},
        "refund_pending": is_refund_pending,
        "transaction": {
            "transaction_id": latest["id"],
            "id": latest["id"],
            "payment_status": latest.get("payment_status"),
            "booking_status": latest.get("booking_status"),
            "payment_captured": is_captured,
            "reconciliation_pending": b_status in {"reconciliation_pending", "reconciliation_in_progress"},
            "refund_pending": is_refund_pending,
            "booking": latest.get("booking_json") if b_status == "booked" else None,
            "amount_inr": float(latest.get("amount_inr") or 0),
            "refunded": p_status == "refunded",
            "error": latest.get("error"),
        }
    }


@router.get("/{transaction_id}/status")
async def get_hotel_payment_status(
    transaction_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Retrieve authoritative status of an existing hotel payment transaction."""
    store = HotelTransactionStore()
    tx = store.get(transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Hotel transaction not found.")
    effective_customer_ref = str(current_user.id)
    if tx.get("customer_ref") and tx.get("customer_ref") != effective_customer_ref:
        raise HTTPException(status_code=403, detail="This hotel transaction does not belong to the current customer.")

    b_status = str(tx.get("booking_status") or "").lower()
    p_status = str(tx.get("payment_status") or "").lower()
    is_captured = p_status in {"captured", "refund_pending", "refund_failed"} or b_status in {
        "payment_captured", "booking_in_progress", "reconciliation_pending", "reconciliation_in_progress", "booked"
    }
    is_refund_pending = p_status in {"refund_pending", "refund_failed"}

    return {
        "transaction_id": transaction_id,
        "payment_status": tx.get("payment_status"),
        "booking_status": tx.get("booking_status"),
        "payment_captured": is_captured,
        "reconciliation_pending": b_status in {"reconciliation_pending", "reconciliation_in_progress"},
        "refund_pending": is_refund_pending,
        "booking": tx.get("booking_json") if b_status == "booked" else None,
        "amount_inr": float(tx.get("amount_inr") or 0),
        "refunded": p_status == "refunded",
        "error": tx.get("error"),
        "trip_id": str(tx.get("trip_id")) if tx.get("trip_id") else None,
    }


@router.post("/{transaction_id}/reconcile")
async def reconcile_hotel_payment(
    transaction_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Reconcile captured payment / uncertain supplier booking on original transaction ID with concurrency serialization."""
    store = HotelTransactionStore()
    pending = store.get(transaction_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Hotel transaction not found.")
    effective_customer_ref = str(current_user.id)
    if pending.get("customer_ref") and pending.get("customer_ref") != effective_customer_ref:
        raise HTTPException(status_code=403, detail="This hotel transaction does not belong to the current customer.")

    b_status = str(pending.get("booking_status") or "").lower()
    p_status = str(pending.get("payment_status") or "").lower()

    # Fast path 1: already booked
    if b_status == "booked" and pending.get("booking_json"):
        return {
            "success": True,
            "status": "confirmed",
            "booking_status": "booked",
            "transaction_id": transaction_id,
            "payment_captured": True,
            "booking": pending.get("booking_json"),
            "amount_inr": float(pending["amount_inr"]),
            "already_booked": True,
            "pdf_available": bool(pending.get("pdf_generated_at")),
        }

    # Fast path 2: already refunded
    if p_status == "refunded":
        return {
            "success": False,
            "status": "refunded",
            "booking_status": "booking_failed",
            "transaction_id": transaction_id,
            "payment_captured": True,
            "refunded": True,
            "requires_refund": False,
            "error": pending.get("error") or "Supplier confirmed no booking exists. Payment has been refunded.",
        }

    # Fast path 3: if booking confirmed absent but refund is pending or failed, retry/reconcile refund
    if p_status in {"refund_pending", "refund_failed"} and b_status == "booking_failed":
        rz = RazorpayService()
        refund_res = None
        refund_err = None
        try:
            refund_res = rz.refund(
                payment_id=pending.get("razorpay_payment_id"),
                amount_rupees=float(pending["amount_inr"]),
                idempotency_key=f"ref_ht_{transaction_id.replace('-', '')[:32]}",
            )
        except Exception as rz_exc:
            refund_err = str(rz_exc)

        if refund_res and str(refund_res.get("status", "")).lower() in {"processed", "captured", "refunded"}:
            store.update(
                transaction_id,
                payment_status="refunded",
                booking_status="booking_failed",
                booking_json={"refund": refund_res},
                error=f"Supplier confirmed no booking exists. Payment refunded: {refund_res.get('id')}",
            )
            return {
                "success": False,
                "status": "refunded",
                "booking_status": "booking_failed",
                "transaction_id": transaction_id,
                "payment_captured": True,
                "refunded": True,
                "refund_id": refund_res.get("id"),
                "requires_refund": False,
                "error": "Supplier confirmed no booking exists. Payment has been refunded.",
            }
        else:
            store.update(
                transaction_id,
                payment_status="refund_failed" if refund_err else "refund_pending",
                booking_status="booking_failed",
                error=f"Supplier confirmed no booking exists. Refund pending: {refund_err or 'unprocessed'}",
            )
            return {
                "success": False,
                "status": "refund_failed" if refund_err else "refund_pending",
                "booking_status": "booking_failed",
                "transaction_id": transaction_id,
                "payment_captured": True,
                "refunded": False,
                "requires_refund": True,
                "error": f"Supplier confirmed no booking exists, but refund could not be completed immediately: {refund_err or 'unprocessed'}.",
            }

    # Atomic concurrency guard: serialize reconciliation execution across workers
    claimed_tx = store.claim_reconciliation(transaction_id)
    if not claimed_tx:
        latest = store.get(transaction_id) or pending
        latest_b = str(latest.get("booking_status") or "").lower()
        latest_p = str(latest.get("payment_status") or "").lower()
        if latest_b == "booked" and latest.get("booking_json"):
            return {
                "success": True,
                "status": "confirmed",
                "booking_status": "booked",
                "transaction_id": transaction_id,
                "payment_captured": True,
                "booking": latest.get("booking_json"),
                "amount_inr": float(latest.get("amount_inr") or 0),
                "already_booked": True,
                "pdf_available": bool(latest.get("pdf_generated_at")),
            }
        if latest_p == "refunded":
            return {
                "success": False,
                "status": "refunded",
                "booking_status": "booking_failed",
                "transaction_id": transaction_id,
                "payment_captured": True,
                "refunded": True,
                "requires_refund": False,
                "error": latest.get("error") or "Supplier confirmed no booking exists. Payment has been refunded.",
            }
        return {
            "success": False,
            "status": "reconciliation_in_progress",
            "booking_status": "reconciliation_in_progress",
            "reconciliation_pending": True,
            "payment_captured": True,
            "requires_refund": False,
            "transaction_id": transaction_id,
            "amount_inr": float(latest.get("amount_inr") or 0),
            "error": "Hotel reconciliation is currently in progress. Please retry shortly.",
        }

    pending = claimed_tx
    claim_resolved = False
    try:
        # Reconcile with supplier using persisted offer and transaction_id clientReference
        rec_status, rec_booking = HotelService().reconcile(
            pending.get("offer_json") or {},
            transaction_id,
        )

        if rec_status == "CONFIRMED" and rec_booking:
            store.update(
                transaction_id,
                payment_status="captured",
                booking_status="booked",
                booking_json=rec_booking,
                error=None,
            )
            claim_resolved = True
            pdf_avail = False
            try:
                rec_record = store.get(transaction_id)
                build_hotel_booking_pdf(rec_record)
                from datetime import datetime, timezone
                store.update(transaction_id, pdf_generated_at=datetime.now(timezone.utc))
                pdf_avail = True
            except Exception:
                pass

            return {
                "success": True,
                "status": "confirmed",
                "booking_status": "booked",
                "transaction_id": transaction_id,
                "payment_captured": True,
                "booking": rec_booking,
                "amount_inr": float(pending["amount_inr"]),
                "already_booked": False,
                "reconciled": True,
                "pdf_available": pdf_avail,
            }
        elif rec_status == "NOT_FOUND":
            rz = RazorpayService()
            refund_res = None
            refund_err = None
            try:
                refund_res = rz.refund(
                    payment_id=pending.get("razorpay_payment_id"),
                    amount_rupees=float(pending["amount_inr"]),
                    idempotency_key=f"ref_ht_{transaction_id.replace('-', '')[:32]}",
                )
            except Exception as rz_exc:
                refund_err = str(rz_exc)

            if refund_res and str(refund_res.get("status", "")).lower() in {"processed", "captured", "refunded"}:
                store.update(
                    transaction_id,
                    payment_status="refunded",
                    booking_status="booking_failed",
                    booking_json={"refund": refund_res},
                    error=f"Supplier confirmed no booking exists. Payment refunded: {refund_res.get('id')}",
                )
                claim_resolved = True
                return {
                    "success": False,
                    "status": "refunded",
                    "booking_status": "booking_failed",
                    "transaction_id": transaction_id,
                    "payment_captured": True,
                    "refunded": True,
                    "refund_id": refund_res.get("id"),
                    "requires_refund": False,
                    "error": "Supplier confirmed no booking exists. Payment has been refunded.",
                }
            else:
                store.update(
                    transaction_id,
                    payment_status="refund_failed" if refund_err else "refund_pending",
                    booking_status="booking_failed",
                    error=f"Supplier confirmed no booking exists. Refund pending: {refund_err or 'unprocessed'}",
                )
                claim_resolved = True
                return {
                    "success": False,
                    "status": "refund_failed" if refund_err else "refund_pending",
                    "booking_status": "booking_failed",
                    "transaction_id": transaction_id,
                    "payment_captured": True,
                    "refunded": False,
                    "requires_refund": True,
                    "error": f"Supplier confirmed no booking exists, but refund could not be completed immediately: {refund_err or 'unprocessed'}.",
                }
        else:
            # UNCERTAIN: preserve reconciliation_pending
            store.update(
                transaction_id,
                payment_status="captured",
                booking_status="reconciliation_pending",
                error="Supplier booking status is uncertain after network interruption. Verification is pending supplier confirmation.",
            )
            claim_resolved = True
            return {
                "success": False,
                "status": "reconciliation_pending",
                "booking_status": "reconciliation_pending",
                "reconciliation_pending": True,
                "payment_captured": True,
                "requires_refund": False,
                "transaction_id": transaction_id,
                "amount_inr": float(pending["amount_inr"]),
                "error": "Supplier booking status is uncertain after network interruption. Verification is pending supplier confirmation.",
            }
    finally:
        if not claim_resolved:
            store.release_reconciliation_claim(
                transaction_id,
                error="Reconciliation attempt interrupted. Preserved for recovery.",
            )
