"""Secure Razorpay payment lifecycle for Trust Trip MAS.

The server creates the Razorpay order from a freshly rechecked Duffel offer.
The browser only receives the public Razorpay key and order ID. After Checkout,
the server verifies the Razorpay signature and captured amount before fulfilling
the flight order. Raw card/UPI data never reaches this FastAPI application.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dependencies.auth import get_current_user, get_optional_current_user
from domain.user import UserResponse
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.currency_converter import CurrencyConversionError, CurrencyConverter
from services.flight_service import FlightService
from services.razorpay_service import RazorpayError, RazorpayService
from services.transaction_store import TransactionStore

router = APIRouter(prefix="/api/payments", tags=["payments"])


class PaymentOrderRequest(BaseModel):
    offer_id: str = Field(min_length=4)
    expected_amount: float = Field(gt=0)
    currency: str = "INR"
    passengers: list[dict] = Field(min_length=1, max_length=9)
    customer_ref: str | None = Field(default=None, max_length=120)
    final_confirmation: bool = False




class PresentationBookRequest(BaseModel):
    """Razorpay-verified presentation booking request.

    The presentation endpoint is intentionally named for backwards compatibility
    with the existing UI, but it no longer bypasses Razorpay. It verifies the
    captured TEST payment attached to the transaction and then uses the same
    supplier-fulfilment path as the normal booking flow.
    """
    transaction_id: str = Field(min_length=8)
    razorpay_order_id: str = Field(min_length=4)
    razorpay_payment_id: str = Field(min_length=4)
    razorpay_signature: str = Field(min_length=16)
    final_confirmation: bool = False


def _presentation_enabled() -> bool:
    return os.getenv("PRESENTATION_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _validate_passengers(passengers: list[dict]) -> None:
    allowed_titles = {"mr", "mrs", "ms", "miss", "mx", "dr"}
    allowed_genders = {"m", "f", "x"}
    if not passengers:
        raise HTTPException(status_code=422, detail="At least one passenger is required.")
    for i, p in enumerate(passengers, 1):
        given = str(p.get("given_name") or "").strip()
        family = str(p.get("family_name") or "").strip()
        title = str(p.get("title") or "").strip().lower()
        gender = str(p.get("gender") or "").strip().lower()
        if not given or len(given) > 40:
            raise HTTPException(status_code=422, detail=f"Passenger {i}: given_name is required and must be at most 40 characters.")
        if not family or len(family) > 40:
            raise HTTPException(status_code=422, detail=f"Passenger {i}: family_name is required and must be at most 40 characters.")
        if title not in allowed_titles:
            raise HTTPException(status_code=422, detail=f"Passenger {i}: a valid title is required (Mr/Mrs/Ms/Miss/Mx/Dr).")
        if gender not in allowed_genders:
            raise HTTPException(status_code=422, detail=f"Passenger {i}: a valid gender is required.")
        if not p.get("born_on"):
            raise HTTPException(status_code=422, detail=f"Passenger {i}: date of birth is required.")
        phone = str(p.get("phone_number") or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if phone and phone.startswith("00"):
            phone = "+" + phone[2:]
        if phone and phone.isdigit() and len(phone) == 10:
            phone = "+91" + phone
        if phone and (not phone.startswith("+") or not phone[1:].isdigit() or not (8 <= len(phone[1:]) <= 15)):
            raise HTTPException(status_code=422, detail=f"Passenger {i}: phone number must be international format, e.g. +919876543210.")
        p["phone_number"] = phone or None
        p["given_name"] = given
        p["family_name"] = family
        p["title"] = title
        p["gender"] = gender

class PaymentVerifyRequest(BaseModel):
    transaction_id: str = Field(min_length=8)
    razorpay_order_id: str = Field(min_length=4)
    razorpay_payment_id: str = Field(min_length=4)
    razorpay_signature: str = Field(min_length=16)
    final_confirmation: bool = False


def _store() -> TransactionStore:
    return TransactionStore()


def _razorpay() -> RazorpayService:
    return RazorpayService()


def _integration_mode() -> str:
    """Derive the payment/supplier environment and reject mixed live/test keys."""
    explicit = os.getenv("INTEGRATION_MODE", "auto").strip().lower()
    rp_live = str(os.getenv("RAZORPAY_KEY_ID") or "").startswith("rzp_live_")
    rp_test = str(os.getenv("RAZORPAY_KEY_ID") or "").startswith("rzp_test_")
    duffel_mode = os.getenv("DUFFEL_MODE", "test").strip().lower()
    duffel_token = os.getenv("DUFFEL_TEST_API_TOKEN") if duffel_mode == "test" else os.getenv("DUFFEL_LIVE_API_TOKEN")
    duffel_test = str(duffel_token or "").startswith("duffel_test_")
    duffel_live = str(duffel_token or "").startswith("duffel_live_")
    if explicit in {"live", "production"}:
        mode = "live"
    elif explicit in {"test", "sandbox"}:
        mode = "test"
    else:
        mode = "live" if rp_live or duffel_live else "test"
    if mode == "live" and (rp_test or duffel_test):
        raise HTTPException(status_code=503, detail="Live integration requires Razorpay Live keys and a Duffel Live access token. Mixed test/live credentials are blocked.")
    if mode == "test" and (rp_live or duffel_live):
        raise HTTPException(status_code=503, detail="Test integration requires Razorpay Test keys and a Duffel test access token. Mixed test/live credentials are blocked.")
    if mode == "live" and os.getenv("PRESENTATION_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=503, detail="PRESENTATION_MODE must be false for live payments and live flight bookings.")
    return mode


def _validate_and_convert_offer(offer: dict, expected_amount: float, expected_currency: str) -> dict:
    provider_currency = str(offer.get("currency") or "").upper()
    requested_currency = str(expected_currency or "").upper()
    if not provider_currency:
        raise HTTPException(status_code=422, detail="The live flight offer has no currency.")
    if provider_currency != requested_currency:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The live flight currency changed from {requested_currency or 'unknown'} "
                f"to {provider_currency}. Please review the current fare before payment."
            ),
        )
    current = float(offer.get("price"))
    if abs(current - float(expected_amount)) > 0.01:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The live flight fare changed from {provider_currency} {float(expected_amount):.2f} "
                f"to {provider_currency} {current:.2f}. Please review and confirm the new fare before payment."
            ),
        )

    try:
        conversion = CurrencyConverter().convert(current, provider_currency, "INR")
    except CurrencyConversionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "provider_amount": current,
        "provider_currency": provider_currency,
        "customer_amount": float(conversion["target_amount"]),
        "customer_currency": "INR",
        "fx_rate": float(conversion["rate"]),
        "fx_rate_date": conversion["rate_date"],
        "fx_source": conversion["source"],
    }


@router.get("/config")
async def payment_config():
    rp = _razorpay()
    try:
        mode = _integration_mode()
        mode_error = None
    except HTTPException as exc:
        mode = "invalid"
        mode_error = exc.detail
    return {
        "enabled": rp.configured and mode != "invalid",
        "key_id": rp.key_id if rp.configured else None,
        "currency": "INR",
        "provider": "razorpay",
        "conversion": {"source": "Frankfurter latest reference rate", "target_currency": "INR"},
        "integration_mode": mode,
        "webhook_required": mode == "live",
        "webhook_configured": bool(rp.webhook_secret),
        "presentation_mode": os.getenv("PRESENTATION_MODE", "false").strip().lower() in {"1", "true", "yes", "on"},
        "message": mode_error or ("Razorpay Checkout is ready." if rp.configured else "Razorpay is not configured."),
    }


@router.post("/create-order")
async def create_payment_order(
    request: PaymentOrderRequest,
    current_user: UserResponse | None = Depends(get_optional_current_user),
):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True, "error": "Final user confirmation is required before payment."}

    rp = _razorpay()
    _integration_mode()
    if not rp.configured:
        raise HTTPException(status_code=503, detail="Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env.")

    try:
        # Never trust the amount supplied by the browser. Recheck the provider first.
        offer = await asyncio.to_thread(FlightService().recheck, request.offer_id)
        conversion = await asyncio.to_thread(
            _validate_and_convert_offer, offer, request.expected_amount, request.currency
        )
        amount = conversion["customer_amount"]

        _validate_passengers(request.passengers)
        offer_passenger_count = len(offer.get("passengers") or [])
        if offer_passenger_count and offer_passenger_count != len(request.passengers):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"This flight requires {offer_passenger_count} traveller(s), "
                    f"but {len(request.passengers)} traveller(s) were supplied. "
                    "Please complete every traveller's information before payment."
                ),
            )

        receipt = f"tt_{uuid.uuid4().hex[:30]}"
        razor_order = await asyncio.to_thread(
            rp.create_order,
            amount,
            receipt,
            {
                "offer_id": request.offer_id,
                "app": "Trust Trip MAS",
                "provider_currency": conversion["provider_currency"],
            },
        )
        if not current_user and request.customer_ref:
            from services.auth_store import UserStore
            if UserStore().get_by_id(request.customer_ref):
                raise HTTPException(
                    status_code=403,
                    detail="Cannot assign payment order to a registered user account without authenticating.",
                )
        effective_customer_ref = str(current_user.id) if current_user else request.customer_ref
        tx_id = _store().create(
            customer_ref=effective_customer_ref,
            offer_id=request.offer_id,
            razorpay_order_id=razor_order["id"],
            amount=amount,
            currency="INR",
            provider_amount=conversion["provider_amount"],
            provider_currency=conversion["provider_currency"],
            fx_rate=conversion["fx_rate"],
            fx_rate_date=conversion["fx_rate_date"],
            fx_source=conversion["fx_source"],
            payment_status="created",
            booking_status="awaiting_payment",
            passengers=request.passengers,
            metadata={"receipt": receipt, "fare_rechecked_at_creation": True, "customer_final_confirmation": True},
        )
        return {
            "success": True,
            "transaction_id": tx_id,
            "key_id": rp.key_id,
            "order_id": razor_order["id"],
            "amount": razor_order["amount"],
            "currency": razor_order["currency"],
            "offer": offer,
            "pricing": {
                "provider_amount": conversion["provider_amount"],
                "provider_currency": conversion["provider_currency"],
                "customer_amount": conversion["customer_amount"],
                "customer_currency": conversion["customer_currency"],
                "fx_rate": conversion["fx_rate"],
                "fx_rate_date": conversion["fx_rate_date"],
                "fx_source": conversion["fx_source"],
            },
        }
    except HTTPException:
        raise
    except (RazorpayError, FlightProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        # Never leak an HTML/plain-text 500 into the frontend. Return a JSON
        # error so the UI can always display a useful booking failure message.
        raise HTTPException(
            status_code=500,
            detail=f"Booking failed unexpectedly: {type(exc).__name__}: {exc}"
        ) from exc



@router.post("/presentation-book")
async def presentation_book(
    request: PresentationBookRequest,
    current_user: UserResponse | None = Depends(get_optional_current_user),
):
    """Verify Razorpay TEST payment and immediately fulfil the flight booking.

    This keeps the existing presentation endpoint/logging contract while using
    the same idempotent transaction as the normal Razorpay flow. No synthetic
    PNR and no payment bypass are allowed.
    """
    if not _presentation_enabled():
        raise HTTPException(status_code=403, detail="Presentation booking is disabled. Set PRESENTATION_MODE=true for the test presentation only.")
    mode = _integration_mode()
    if mode != "test":
        raise HTTPException(status_code=403, detail="Presentation booking is available only with Test/Sandbox integration.")
    if not request.final_confirmation:
        raise HTTPException(status_code=400, detail="Final customer confirmation is required.")

    store = _store()
    rp = _razorpay()
    tx = store.get(request.transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Payment transaction not found.")
    if tx.get("customer_ref"):
        from services.auth_store import UserStore
        is_user_tx = bool(UserStore().get_by_id(tx["customer_ref"]))
        if is_user_tx or current_user:
            if not current_user or str(current_user.id) != tx["customer_ref"]:
                raise HTTPException(status_code=403, detail="This payment transaction does not belong to you.")
    if tx.get("razorpay_order_id") != request.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Payment order does not match this transaction.")
    if tx.get("booking_status") == "booked":
        return {
            "success": True, "payment_captured": True, "already_booked": True,
            "presentation_mode": True, "transaction_id": request.transaction_id,
            "booking": {"order_id": tx.get("duffel_order_id"), "booking_reference": tx.get("booking_reference")},
        }

    try:
        if not rp.verify_checkout_signature(request.razorpay_order_id, request.razorpay_payment_id, request.razorpay_signature):
            raise HTTPException(status_code=400, detail="Payment signature verification failed.")
        payment = await asyncio.to_thread(rp.fetch_payment, request.razorpay_payment_id)
        if payment.get("order_id") != tx.get("razorpay_order_id"):
            raise HTTPException(status_code=400, detail="Payment is linked to a different Razorpay order.")
        if payment.get("status") != "captured":
            raise HTTPException(status_code=402, detail=f"Payment is not captured; current status is {payment.get('status')}.")
        if str(payment.get("currency") or "").upper() != "INR":
            raise HTTPException(status_code=400, detail="The captured Razorpay payment is not in INR.")
        expected_paise = int(round(float(tx["amount"]) * 100))
        if int(payment.get("amount") or 0) != expected_paise:
            raise HTTPException(status_code=400, detail="Captured payment amount does not match the server-side booking amount.")

        store.update(request.transaction_id, razorpay_payment_id=request.razorpay_payment_id,
                     payment_status="captured", booking_status="payment_captured")
        result = await _fulfil_paid_transaction(request.transaction_id)
        result["presentation_mode"] = True
        return result
    except HTTPException:
        raise
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (RazorpayError, FlightProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Presentation booking failed: {type(exc).__name__}: {exc}") from exc

def _safe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value or {}


def _booking_is_confirmed(booking: dict) -> bool:
    """True only when Duffel returned a usable airline booking reference."""
    return bool(booking.get("booking_reference"))


async def _fulfil_paid_transaction(transaction_id: str) -> dict:
    """Idempotently convert a captured Razorpay payment into a Duffel booking.

    This is shared by the Checkout callback and Razorpay's webhook. The DB
    claim is the single-flight lock, so receiving both signals cannot create
    two airline orders.
    """
    store = _store()
    tx = store.get(transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Payment transaction not found.")
    if tx.get("booking_status") == "booked":
        return {"success": True, "payment_captured": True, "already_booked": True,
                "transaction_id": transaction_id,
                "booking": {"order_id": tx.get("duffel_order_id"), "booking_reference": tx.get("booking_reference")}}
    metadata = _safe_json(tx.get("metadata"))
    if not metadata.get("customer_final_confirmation"):
        raise HTTPException(status_code=409, detail="Final customer confirmation was not recorded for this transaction.")
    if tx.get("payment_status") != "captured":
        raise HTTPException(status_code=409, detail="The Razorpay payment is not captured yet.")

    if not store.claim_booking(transaction_id):
        latest = store.get(transaction_id) or {}
        if latest.get("booking_status") == "booked":
            return {"success": True, "payment_captured": True, "already_booked": True,
                    "transaction_id": transaction_id,
                    "booking": {"order_id": latest.get("duffel_order_id"), "booking_reference": latest.get("booking_reference")}}
        if latest.get("booking_status") in {"booking_pending", "provider_pending"}:
            return {"success": False, "payment_captured": True, "booking_pending": True,
                    "transaction_id": transaction_id, "message": "Supplier booking is still being confirmed. Refresh My Trips shortly."}
        raise HTTPException(status_code=409, detail="This payment is already being processed or requires refund/review. Do not retry automatically.")

    passengers = _safe_json(tx.get("passenger_json"))
    service = FlightService()
    try:
        result = await asyncio.to_thread(
            service.book_after_recheck,
            offer_id=tx["offer_id"],
            expected_amount=float(tx.get("provider_amount") or 0),
            passengers=passengers,
            expected_currency=str(tx.get("provider_currency") or "USD"),
            payment=None,
            allow_price_change=False,
            payment_mode="duffel_balance",
        )
        booking = result.get("booking") or {}
        if not result.get("booked"):
            store.update(transaction_id, booking_status="booking_failed")
            raise HTTPException(status_code=409, detail="The supplier fare changed after payment capture. Refund/review is required; the system will not create a second order.")

        order_id = booking.get("order_id")
        # For asynchronous Duffel order creation, poll the supplier order. This
        # is used both in test mode (where no webhook is configured) and as a
        # short fast path in live mode before the webhook is relied upon.
        if order_id and not _booking_is_confirmed(booking):
            store.update(transaction_id, duffel_order_id=order_id, booking_status="provider_pending")
            wait_seconds = max(1, int(os.getenv("DUFFEL_BOOKING_POLL_SECONDS", "45")))
            interval = max(1, int(os.getenv("DUFFEL_BOOKING_POLL_INTERVAL_SECONDS", "3")))
            import time
            deadline = time.monotonic() + wait_seconds
            latest_order = {}
            while time.monotonic() < deadline:
                latest_order = await asyncio.to_thread(service.get_order, order_id)
                if latest_order.get("booking_reference"):
                    booking["booking_reference"] = latest_order.get("booking_reference")
                    booking["raw"] = latest_order
                    booking["documents"] = latest_order.get("documents") or []
                    break
                await asyncio.sleep(interval)
            if not booking.get("booking_reference"):
                # The order exists; do not call create-order again. Duffel's
                # webhook can complete this transaction later.
                store.update(transaction_id, booking_status="provider_pending")
                return {"success": False, "payment_captured": True, "booking_pending": True,
                        "transaction_id": transaction_id, "booking": booking,
                        "message": "Payment succeeded and the supplier order exists, but the airline has not returned the PNR yet. No duplicate booking will be attempted."}

        store.update(transaction_id, duffel_order_id=booking.get("order_id"),
                     booking_reference=booking.get("booking_reference"),
                     payment_status="captured", booking_status="booked")
        store.update_metadata(transaction_id,
                              supplier_live_mode=bool((booking.get("raw") or {}).get("live_mode")),
                              supplier_documents=booking.get("documents") or [])
        return {"success": True, "payment_captured": True, "booking": booking, "transaction_id": transaction_id}
    except FlightProviderTimeout:
        store.update(transaction_id, booking_status="booking_ambiguous")
        raise
    except HTTPException:
        raise
    except FlightProviderError as exc:
        # Duffel documents that an HTTP 500 during order creation can be
        # ambiguous: the airline order may actually have been created. Never
        # blindly retry in that case. Try to locate the existing order by the
        # offer ID before marking the transaction for manual review.
        if "HTTP 500" in str(exc):
            try:
                existing = await asyncio.to_thread(service.find_order_by_offer, tx["offer_id"])
                if existing and existing.get("id"):
                    store.update(transaction_id, duffel_order_id=existing.get("id"),
                                 booking_reference=existing.get("booking_reference"),
                                 booking_status="booked" if existing.get("booking_reference") else "provider_pending",
                                 payment_status="captured")
                    store.update_metadata(transaction_id, supplier_live_mode=bool(existing.get("live_mode")), supplier_documents=existing.get("documents") or [])
                    if existing.get("booking_reference"):
                        return {"success": True, "payment_captured": True, "booking": {"order_id": existing.get("id"), "booking_reference": existing.get("booking_reference"), "raw": existing, "documents": existing.get("documents") or []}, "transaction_id": transaction_id, "recovered_after_ambiguous_response": True}
                    return {"success": False, "payment_captured": True, "booking_pending": True, "transaction_id": transaction_id, "booking": {"order_id": existing.get("id"), "raw": existing}, "message": "Supplier order was located after an ambiguous response. The airline PNR is still being confirmed; no duplicate order will be created."}
            except Exception:
                pass
            store.update(transaction_id, booking_status="booking_ambiguous")
            raise
        store.update(transaction_id, booking_status="booking_failed")
        raise


@router.post("/verify-and-book")
async def verify_and_book(
    request: PaymentVerifyRequest,
    current_user: UserResponse | None = Depends(get_optional_current_user),
):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True, "error": "Final user confirmation is required."}

    store = _store()
    rp = _razorpay()
    tx = store.get(request.transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Payment transaction not found.")
    if tx.get("customer_ref"):
        from services.auth_store import UserStore
        is_user_tx = bool(UserStore().get_by_id(tx["customer_ref"]))
        if is_user_tx or current_user:
            if not current_user or str(current_user.id) != tx["customer_ref"]:
                raise HTTPException(status_code=403, detail="This payment transaction does not belong to you.")
    if tx.get("razorpay_order_id") != request.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Payment order does not match this transaction.")

    if tx.get("booking_status") == "booked":
        return {"success": True, "payment_captured": True, "already_booked": True,
                "transaction_id": request.transaction_id,
                "booking": {"order_id": tx.get("duffel_order_id"), "booking_reference": tx.get("booking_reference")}}

    try:
        if not rp.verify_checkout_signature(request.razorpay_order_id, request.razorpay_payment_id, request.razorpay_signature):
            raise HTTPException(status_code=400, detail="Payment signature verification failed.")
        payment = await asyncio.to_thread(rp.fetch_payment, request.razorpay_payment_id)
        if payment.get("order_id") != tx.get("razorpay_order_id"):
            raise HTTPException(status_code=400, detail="Payment is linked to a different Razorpay order.")
        if payment.get("status") != "captured":
            raise HTTPException(status_code=402, detail=f"Payment is not captured; current status is {payment.get('status')}.")
        if str(payment.get("currency") or "").upper() != "INR":
            raise HTTPException(status_code=400, detail="The captured Razorpay payment is not in INR.")
        expected_paise = int(round(float(tx["amount"]) * 100))
        if int(payment.get("amount") or 0) != expected_paise:
            raise HTTPException(status_code=400, detail="Captured payment amount does not match the server-side booking amount.")
        store.update(request.transaction_id, razorpay_payment_id=request.razorpay_payment_id,
                     payment_status="captured", booking_status="payment_captured")
        result = await _fulfil_paid_transaction(request.transaction_id)
        if result.get("booking_pending"):
            # HTTP 202 lets the UI distinguish a supplier confirmation wait from
            # a payment failure; My Trips remains the source of truth.
            return result
        return result
    except HTTPException:
        raise
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (RazorpayError, FlightProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/refund")
async def refund_payment(
    transaction_id: str,
    final_confirmation: bool = False,
    current_user: UserResponse = Depends(get_current_user),
):
    if not final_confirmation:
        return {"success": False, "requires_final_confirmation": True, "error": "Final refund confirmation is required."}
    store = _store()
    tx = store.get(transaction_id)
    if not tx or not tx.get("razorpay_payment_id"):
        raise HTTPException(status_code=404, detail="Captured payment not found for this transaction.")
    if tx.get("customer_ref") != str(current_user.id):
        raise HTTPException(status_code=403, detail="Captured payment transaction does not belong to you.")
    if tx.get("refund_status") in {"processed", "processed_partial"}:
        return {"success": True, "already_refunded": True, "transaction_id": transaction_id}
    try:
        refund = await asyncio.to_thread(
            _razorpay().refund,
            tx["razorpay_payment_id"],
            None,
            f"refund-{transaction_id}",
        )
        store.update(transaction_id, refund_status=refund.get("status", "created"))
        return {"success": True, "refund": refund, "transaction_id": transaction_id}
    except RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/webhook")
async def razorpay_webhook(request: Request, background_tasks: BackgroundTasks):
    """Razorpay payment webhook with idempotent supplier fulfilment.

    Live deployments should configure this endpoint in Razorpay Dashboard.
    Test mode does not require the webhook for the synchronous Checkout flow:
    verify-and-book fetches the captured payment directly and fulfils the order.
    If a test webhook is configured, duplicate delivery is harmless because the
    same DB booking claim protects supplier fulfilment.
    """
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    event_id = request.headers.get("x-razorpay-event-id", "")
    rp = _razorpay()
    try:
        if not rp.verify_webhook(raw, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature.")
        payload = json.loads(raw.decode("utf-8"))
        event = payload.get("event", "")
        entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        order_id = entity.get("order_id")
        if not order_id:
            return {"success": True, "event": event, "ignored": True}
        tx = _store().get_by_razorpay_order(order_id)
        if not tx:
            return {"success": True, "event": event, "ignored": True}

        if event == "payment.captured":
            expected_paise = int(round(float(tx.get("amount") or 0) * 100))
            if str(entity.get("currency") or "").upper() != "INR" or int(entity.get("amount") or 0) != expected_paise:
                _store().update(tx["id"], payment_status="webhook_amount_mismatch", booking_status="manual_review")
                raise HTTPException(status_code=400, detail="Webhook payment amount/currency does not match the stored transaction.")
            _store().update(tx["id"], razorpay_payment_id=entity.get("id"), payment_status="captured", booking_status="payment_captured")
            _store().update_metadata(tx["id"], last_razorpay_event_id=event_id or None, last_razorpay_event=event)
            # Execute the exact same idempotent fulfilment path as Checkout, but
            # in the background so Razorpay gets a fast 2xx acknowledgement.
            background_tasks.add_task(_fulfil_paid_transaction, tx["id"])
        elif event == "payment.failed":
            _store().update(tx["id"], payment_status="failed", booking_status="payment_failed")
            _store().update_metadata(tx["id"], last_razorpay_event_id=event_id or None, last_razorpay_event=event)
        elif event in {"order.paid", "payment.authorized"}:
            _store().update_metadata(tx["id"], last_razorpay_event_id=event_id or None, last_razorpay_event=event)
        return {"success": True, "event": event, "transaction_id": tx["id"]}
    except HTTPException:
        raise
    except (RazorpayError, ValueError, json.JSONDecodeError, FlightProviderError, FlightProviderTimeout) as exc:
        raise HTTPException(status_code=400 if not isinstance(exc, FlightProviderTimeout) else 504, detail=str(exc)) from exc

