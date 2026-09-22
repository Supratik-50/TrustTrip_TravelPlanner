from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import Response

from services.transaction_store import TransactionStore
from services.flight_service import FlightService
from services.flight_provider import FlightProviderError, FlightProviderTimeout
from services.razorpay_service import RazorpayService, RazorpayError
from services.ticket_service import build_booking_pdf
from services.hotel_transaction_store import HotelTransactionStore
from services.hotel_booking_pdf import build_hotel_booking_pdf

router = APIRouter(prefix="/api/trips", tags=["trips"])


@router.get("")
async def list_trips(customer_ref: str | None = Query(default=None, max_length=120)):
    """Return the user's flight and hotel transaction records in one feed."""
    flights = _store().list_recent(50, customer_ref=customer_ref)
    hotels = HotelTransactionStore().list_recent(50, customer_ref=customer_ref)

    normalized = []
    for tx in flights:
        item = dict(tx)
        item["record_type"] = "flight"
        normalized.append(item)
    for tx in hotels:
        item = dict(tx)
        item["record_type"] = "hotel"
        item["booking_reference"] = (item.get("booking_json") or {}).get("confirmation_code") or (item.get("booking_json") or {}).get("booking_id")
        item["amount"] = item.get("amount_inr")
        item["currency"] = "INR"
        normalized.append(item)

    normalized.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return {"success": True, "trips": normalized[:100]}




@router.get("/{transaction_id}/hotel.pdf")
async def hotel_booking_pdf(transaction_id: str, customer_ref: str | None = Query(default=None, max_length=120)):
    if not customer_ref:
        raise HTTPException(status_code=400, detail="customer_ref is required to download a hotel booking PDF.")
    record = HotelTransactionStore().get(transaction_id)
    if not record or record.get("customer_ref") != customer_ref:
        raise HTTPException(status_code=404, detail="Hotel booking record not found.")
    if str(record.get("booking_status") or "").lower() != "booked" or not record.get("booking_json"):
        raise HTTPException(status_code=409, detail="A hotel booking confirmation PDF is available only after a successful booking.")
    try:
        pdf = build_hotel_booking_pdf(record)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not generate hotel booking PDF: {exc}")
    filename = "trust-trip-hotel-booking.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.delete("/{transaction_id}")
async def delete_trip_record(
    transaction_id: str,
    record_type: str = Query(default="flight", pattern="^(flight|hotel)$"),
    customer_ref: str | None = Query(default=None, max_length=120),
):
    """Delete only the local My Trips record. This does not cancel an external booking."""
    if not customer_ref:
        raise HTTPException(status_code=400, detail="customer_ref is required to delete a trip record.")

    if record_type == "hotel":
        deleted = HotelTransactionStore().delete(transaction_id, customer_ref=customer_ref)
    else:
        deleted = _store().delete(transaction_id, customer_ref=customer_ref)

    if not deleted:
        raise HTTPException(status_code=404, detail="Trip record not found or it does not belong to this customer.")
    return {"success": True, "deleted": transaction_id, "record_type": record_type}


def _store():
    return TransactionStore()


@router.post("/{transaction_id}/refresh")
async def refresh_booking(transaction_id: str, customer_ref: str | None = Query(default=None, max_length=120)):
    """Refresh a supplier-pending order without creating a second booking."""
    store = _store()
    tx = store.get(transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Booking transaction not found.")
    if customer_ref and tx.get("customer_ref") and customer_ref != tx.get("customer_ref"):
        raise HTTPException(status_code=403, detail="This booking does not belong to the requested customer.")
    if tx.get("booking_status") == "booked":
        return {"success": True, "status": "booked", "booking_reference": tx.get("booking_reference"), "duffel_order_id": tx.get("duffel_order_id")}
    order_id = tx.get("duffel_order_id")
    if not order_id:
        return {"success": True, "status": tx.get("booking_status"), "message": "Supplier order has not been assigned yet."}
    try:
        order = await asyncio.to_thread(FlightService().get_order, order_id)
        pnr = order.get("booking_reference")
        if pnr:
            store.update(transaction_id, booking_reference=pnr, booking_status="booked", payment_status="captured")
            store.update_metadata(transaction_id, supplier_documents=order.get("documents") or [], supplier_live_mode=bool(order.get("live_mode")))
            return {"success": True, "status": "booked", "booking_reference": pnr, "duffel_order_id": order_id, "order": order}
        return {"success": True, "status": "provider_pending", "duffel_order_id": order_id, "message": "Supplier order exists; airline PNR is not available yet."}
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@router.get("/{transaction_id}/ticket.pdf")
async def download_ticket(transaction_id: str, customer_ref: str | None = Query(default=None, max_length=120)):
    """Download a PDF confirmation built from the actual Duffel order."""
    tx = _store().get(transaction_id)
    if not tx or tx.get("booking_status") != "booked" or not tx.get("duffel_order_id"):
        raise HTTPException(status_code=404, detail="A completed flight booking is required before downloading the ticket.")
    if customer_ref and tx.get("customer_ref") and customer_ref != tx.get("customer_ref"):
        raise HTTPException(status_code=403, detail="This booking does not belong to the requested customer.")
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



class CancelQuote(BaseModel):
    transaction_id: str
    final_confirmation: bool = False


class CancelConfirm(BaseModel):
    transaction_id: str
    cancellation_id: str
    final_confirmation: bool = False


@router.post("/cancel/quote")
async def cancellation_quote(request: CancelQuote):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True}
    tx = _store().get(request.transaction_id)
    if not tx or not tx.get("duffel_order_id"):
        raise HTTPException(status_code=404, detail="Booking transaction not found.")
    try:
        quote = await asyncio.to_thread(FlightService().cancellation_quote, tx["duffel_order_id"])
        return {"success": True, "transaction_id": request.transaction_id, "quote": quote}
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/cancel/confirm")
async def cancellation_confirm(request: CancelConfirm):
    if not request.final_confirmation:
        return {"success": False, "requires_final_confirmation": True}
    tx = _store().get(request.transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Booking transaction not found.")
    try:
        result = await asyncio.to_thread(FlightService().confirm_cancellation, request.cancellation_id)
        refund = None
        # Duffel returns the airline refund amount; when the customer originally
        # paid through Razorpay, mirror that refund to the customer payment method.
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
                        return {"success": True, "cancellation": result, "refund": None, "refund_error": "Airline refund currency could not be converted using the locked transaction FX rate; manual refund review is required.", "transaction_id": request.transaction_id}
                    customer_refund_amount = float(refund_amount) * float(stored_rate)
                customer_refund_amount = min(customer_refund_amount, customer_amount)
                if customer_refund_amount <= 0:
                    _store().update(request.transaction_id, booking_status="cancelled", refund_status="not_applicable")
                    return {"success": True, "cancellation": result, "refund": None, "transaction_id": request.transaction_id}
                refund = await asyncio.to_thread(RazorpayService().refund, payment_id, customer_refund_amount, f"cancel-refund-{request.transaction_id}")
            except RazorpayError as exc:
                _store().update(request.transaction_id, booking_status="cancelled", refund_status="manual_review")
                return {"success": True, "cancellation": result, "refund": None, "refund_error": str(exc), "transaction_id": request.transaction_id}
        _store().update(request.transaction_id, booking_status="cancelled", refund_status=(refund or {}).get("status", "not_applicable"))
        return {"success": True, "cancellation": result, "refund": refund, "transaction_id": request.transaction_id}
    except FlightProviderTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except FlightProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
