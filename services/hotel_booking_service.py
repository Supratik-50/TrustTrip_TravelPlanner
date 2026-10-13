"""Hotel Booking Integration Service bridging snapshot authorization into existing HotelService and HotelTransactionStore."""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from uuid import UUID

from domain.live_offer import HotelOffer, RevalidationStatus
from services.currency_converter import CurrencyConverter
from services.flight_booking_service import FareChangedException, OfferWithdrawnException
from services.hotel.hotel_service import HotelService
from services.hotel_transaction_store import HotelTransactionStore
from services.planner_service import PlannerService
from services.razorpay_service import RazorpayService
from services.search_session_store import SearchSessionStore


class HotelBookingService:
    """Bridges Phase 4 snapshot security and revalidation into the existing TrustTrip hotel checkout pipeline."""

    def __init__(
        self,
        store: SearchSessionStore | None = None,
        planner: PlannerService | None = None,
        hotel_service: HotelService | None = None,
        tx_store: HotelTransactionStore | None = None,
        razorpay_service: RazorpayService | None = None,
    ) -> None:
        self.store = store or SearchSessionStore()
        self.planner = planner or PlannerService()
        self.hotel_service = hotel_service or HotelService()
        self.tx_store = tx_store or HotelTransactionStore()
        self.razorpay = razorpay_service or RazorpayService()

    async def create_checkout_order_from_snapshot(
        self,
        snapshot_id: UUID,
        user_id: UUID | str,
        holder: dict[str, Any],
        guests: list[dict[str, Any]],
        trip_id: UUID | str,
        revision_id: UUID | str,
        expected_revision_number: int,
        payment_idempotency_key: str,
        customer_ref: str | None = None,
        final_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Validate snapshot ownership, revalidate rates, and initiate existing TrustTrip hotel checkout order.

        Section 10: exact revision identity and an idempotency reservation are
        required before creating a payment order.
        """
        if not final_confirmation:
            raise ValueError("Final user confirmation is required before hotel payment order creation.")

        # 0. Section 10 — Section 9 eligibility gate (must run before any irreversible action)
        from services.authorization_store import AuthorizationStore
        AuthorizationStore().assert_booking_eligible(
            trip_id=trip_id,
            user_id=user_id,
            revision_id=revision_id,
            expected_revision_number=expected_revision_number,
        )

        # 1. Tenant ownership validation
        snapshot = self.store.get_snapshot(snapshot_id, user_id)
        if not snapshot:
            raise PermissionError(
                f"Access denied: Snapshot '{snapshot_id}' does not exist or does not belong to the user."
            )

        if snapshot.offer_type != "hotel":
            raise ValueError(f"Snapshot '{snapshot_id}' is not a hotel offer.")

        # 2. Rate revalidation gate
        result = await self.planner.revalidate_offer(snapshot_id, user_id)
        if result.status in {RevalidationStatus.PRICE_CHANGED, RevalidationStatus.SCHEDULE_CHANGED}:
            subsequent_id = result.subsequent_snapshot_id or snapshot_id
            raise FareChangedException(
                original_snapshot_id=snapshot_id,
                subsequent_snapshot_id=subsequent_id,
                new_price=result.current_price,
                currency=result.currency,
                details=result.details,
            )
        if result.status == RevalidationStatus.OFFER_WITHDRAWN:
            raise OfferWithdrawnException(snapshot_id=snapshot_id, details=result.details)

        # 3. Format offer for existing HotelService and HotelTransactionStore
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
            "currency": hotel_offer.price.currency,
            "price_inr": float(hotel_offer.price.inr_reference_amount) if hotel_offer.price.inr_reference_amount else None,
            "nights": hotel_offer.nights,
            "rooms": hotel_offer.rooms_count,
            "adults": hotel_offer.adults_count,
            "children": hotel_offer.children_count,
            "infants": hotel_offer.infants_count,
            "snapshot_id": str(snapshot_id),
        }

        # Calculate INR amount for payment
        amount_inr = offer_dict.get("price_inr")
        if amount_inr is None:
            try:
                converter = CurrencyConverter()
                rate_info = converter.get_rate(offer_dict["currency"], "INR")
                amount_inr = round(float(offer_dict["price"]) * float(rate_info["rate"]), 2)
            except Exception:
                amount_inr = round(float(offer_dict["price"]) * 85.0, 2)

        # 4. Perform prebook via existing HotelService
        # Section 10: A simulated/fallback prebook MUST NOT proceed to payment order creation.
        prebook_data = {}
        try:
            prebook_res = self.hotel_service.prebook(offer_dict)
            prebook_data = prebook_res if isinstance(prebook_res, dict) else {}
        except Exception:
            prebook_data = {}

        # Reject simulated prebook: a UUID-only prebook without a real supplier reference
        # cannot be used as a valid supplier hold. Fail closed rather than continue.
        if not prebook_data.get("prebook_id"):
            raise ValueError(
                "Hotel prebook did not return a valid supplier hold reference. "
                "Cannot create payment order without an authoritative prebook."
            )
        if str(prebook_data.get("status", "")).lower() == "simulated":
            raise ValueError(
                "Hotel prebook returned a simulated reference. "
                "A simulated prebook cannot be used for real payment order creation."
            )

        # 5. Check for outstanding captured / pending / refunding transactions on this trip
        effective_customer_ref = str(user_id) if user_id else (customer_ref or "guest")
        if trip_id:
            existing_txs = self.tx_store.get_by_trip(str(trip_id), customer_ref=effective_customer_ref)
            for etx in existing_txs:
                if etx.get("payment_idempotency_key") == payment_idempotency_key:
                    continue
                etx_b = str(etx.get("booking_status") or "").lower()
                etx_p = str(etx.get("payment_status") or "").lower()
                if etx_b == "booked":
                    raise ValueError("Hotel booking has already been confirmed for this trip.")
                if etx_b in {"reconciliation_pending", "reconciliation_in_progress", "booking_in_progress"} or etx_p == "captured":
                    raise ValueError("A payment for this hotel stay has already been captured and is pending verification. Please check confirmation status before initiating another order.")
                if etx_p in {"refund_pending", "refund_failed"}:
                    raise ValueError("A previous payment refund for this hotel stay is currently being processed. New payment cannot be initiated until earlier refund is resolved.")

        # Create transaction and payment order
        tx_id = str(uuid.uuid4())

        order_id = None
        key_id = "test_key"
        reservation = self.tx_store.reserve_payment_order(
            id=tx_id,
            customer_ref=effective_customer_ref,
            provider=offer_dict["provider"],
            hotel_name=offer_dict["hotel_name"],
            offer=offer_dict,
            prebook=prebook_data,
            holder=holder,
            guests=guests,
            amount_inr=float(amount_inr),
            trip_id=str(trip_id),
            revision_id=str(revision_id),
            revision_number=expected_revision_number,
            payment_idempotency_key=payment_idempotency_key,
        )
        if reservation["id"] != tx_id:
            for _ in range(100):
                if reservation.get("razorpay_order_id"):
                    return {"success": True, "idempotent_replay": True,
                            "transaction_id": reservation["id"],
                            "razorpay_order_id": reservation["razorpay_order_id"]}
                await asyncio.sleep(0.05)
                reservation = self.tx_store.get_by_payment_idempotency_key(payment_idempotency_key) or reservation
            if reservation.get("booking_status") == "payment_order_failed":
                raise ValueError("The prior hotel payment-order request failed; use a new idempotency key after reviewing its state.")
            if reservation.get("razorpay_order_id"):
                return {"success": True, "idempotent_replay": True,
                        "transaction_id": reservation["id"],
                        "razorpay_order_id": reservation["razorpay_order_id"]}
            raise ValueError("A hotel payment order with this idempotency key is still being created.")

        if not self.razorpay.configured:
            self.tx_store.update(tx_id, payment_status="order_failed", booking_status="payment_order_failed", error="Razorpay is not configured.")
            raise ValueError("Razorpay is not configured; no payment order was created.")
        try:
            order = self.razorpay.create_order(
                float(amount_inr), f"hotel_{tx_id[:32]}",
                {"transaction_id": tx_id, "provider": offer_dict["provider"], "hotel": offer_dict["hotel_name"][:120]},
            )
            order_id = order.get("id")
            key_id = self.razorpay.key_id
        except Exception as exc:
            self.tx_store.update(tx_id, payment_status="order_failed", booking_status="payment_order_failed", error="Razorpay order creation failed.")
            raise ValueError("Razorpay payment order creation failed; no payment order was returned.") from exc

        self.tx_store.update(tx_id, razorpay_order_id=order_id)

        return {
            "success": True,
            "transaction_id": tx_id,
            "razorpay_order_id": order_id,
            "amount_inr": float(amount_inr),
            "currency": "INR",
            "key_id": key_id,
            "provider": offer_dict["provider"],
            "hotel_name": offer_dict["hotel_name"],
            "snapshot_id": str(snapshot_id),
        }
