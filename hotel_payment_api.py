from __future__ import annotations
import json, os, uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dependencies.auth import get_optional_current_user
from domain.user import UserResponse
from services.hotel.hotel_service import HotelService
from services.hotel.hotel_provider import HotelProviderError
from services.razorpay_service import RazorpayService, RazorpayError
from services.currency_converter import CurrencyConverter, CurrencyConversionError
from services.hotel_transaction_store import HotelTransactionStore
from services.hotel_booking_pdf import build_hotel_booking_pdf

router=APIRouter(prefix="/api/hotel-payments", tags=["hotel-payments"])

class HotelPaymentOrder(BaseModel):
    offer: dict[str, Any]
    customer_ref: str | None = None
    prebook: dict[str, Any]
    holder: dict[str, Any]
    guests: list[dict[str, Any]]
    customer_ref: str | None = None
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
    customer_ref: str | None = None
    final_confirmation: bool = False


@router.post("/create-order")
async def create_order(
    req: HotelPaymentOrder,
    current_user: UserResponse | None = Depends(get_optional_current_user),
):
    if not req.final_confirmation:
        raise HTTPException(status_code=409, detail="Final user confirmation is required before hotel payment.")
    offer=req.offer; pre=req.prebook
    if not pre.get("prebook_id"):
        raise HTTPException(status_code=422, detail="Hotel prebook is missing or expired.")
    amount_inr=offer.get("price_inr")
    if amount_inr is None:
        try:
            amount_inr=float(offer["price"])*float(CurrencyConverter().get_rate(offer["currency"],"INR")["rate"])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Unable to determine INR payment amount: {exc}")
    rz=RazorpayService()
    if not rz.configured:
        raise HTTPException(status_code=503, detail="Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.")
    tx_id=str(uuid.uuid4())
    try:
        order=rz.create_order(float(amount_inr), f"hotel_{tx_id[:32]}", {"transaction_id":tx_id,"provider":str(offer.get("provider")),"hotel":str(offer.get("hotel_name","Hotel"))[:120]})
    except RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not current_user and req.customer_ref:
        from services.auth_store import UserStore
        if UserStore().get_by_id(req.customer_ref):
            raise HTTPException(
                status_code=403,
                detail="Cannot assign hotel payment order to a registered user account without authenticating.",
            )
    effective_customer_ref = str(current_user.id) if current_user else req.customer_ref
    HotelTransactionStore().create(id=tx_id, customer_ref=effective_customer_ref, provider=str(offer.get("provider")), hotel_name=str(offer.get("hotel_name") or "Hotel"), offer=offer, prebook=pre, holder=req.holder, guests=req.guests, amount_inr=round(float(amount_inr),2), razorpay_order_id=order.get("id"))
    return {"success":True,"transaction_id":tx_id,"razorpay_order_id":order.get("id"),"amount_inr":round(float(amount_inr),2),"currency":"INR","key_id":rz.key_id,"provider":offer.get("provider"),"hotel_name":offer.get("hotel_name")}

@router.post("/verify-and-book")
async def verify_and_book(
    req: HotelVerifyBook,
    current_user: UserResponse | None = Depends(get_optional_current_user),
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

    rz = RazorpayService()
    try:
        if not rz.verify_checkout_signature(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid Razorpay payment signature.")
        payment = rz.fetch_payment(req.razorpay_payment_id)
        if str(payment.get("status", "")).lower() != "captured":
            raise HTTPException(status_code=409, detail="Razorpay payment is not captured yet.")
    except RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    store.update(req.transaction_id, payment_status="captured", razorpay_payment_id=req.razorpay_payment_id, booking_status="booking_in_progress")
    try:
        booking = HotelService().book(req.offer, req.prebook, req.holder, req.guests, req.transaction_id)
        store.update(req.transaction_id, payment_status="captured", booking_status="booked", razorpay_payment_id=req.razorpay_payment_id, booking_json=booking, error=None)
        record = store.get(req.transaction_id)
        build_hotel_booking_pdf(record)
        from datetime import datetime, timezone
        store.update(req.transaction_id, pdf_generated_at=datetime.now(timezone.utc))
        return {"success": True, "transaction_id": req.transaction_id, "payment_captured": True, "booking": booking, "amount_inr": float(pending["amount_inr"]), "pdf_available": True}
    except HotelProviderError as exc:
        store.update(req.transaction_id, payment_status="captured", booking_status="booking_failed", razorpay_payment_id=req.razorpay_payment_id, error=str(exc))
        return {"success": False, "payment_captured": True, "booking_pending": False, "requires_refund": True, "transaction_id": req.transaction_id, "error": f"Payment succeeded but hotel booking failed: {exc}"}
    except Exception:
        store.update(req.transaction_id, payment_status="captured", booking_status="booking_failed", razorpay_payment_id=req.razorpay_payment_id, error="Hotel booking failed after payment capture; reconciliation/refund is required.")
        return {"success": False, "payment_captured": True, "booking_pending": False, "requires_refund": True, "transaction_id": req.transaction_id, "error": "Payment succeeded but the hotel booking could not be completed. Reconciliation/refund is required."}

