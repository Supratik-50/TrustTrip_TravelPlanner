"""Canonical booking read and aggregation service for TrustTrip.

Wave 3B Read Abstraction:
- Strictly read-only: does not execute bookings, payments, or state transitions.
- Aggregates canonical flight bookings from `booking_intents`.
- Aggregates canonical hotel bookings from `trust_trip_hotel_transactions`.
- Enforces strict tenant and trip isolation (authenticated user + trip ID).
- Preserves component-level booking status semantics.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from services.flight_booking_service import FlightBookingService, _dsn
from services.hotel_transaction_store import HotelTransactionStore

logger = logging.getLogger(__name__)


class BookingReadService:
    """Read-only service to query and aggregate flight and hotel bookings."""

    _IN_MEMORY_HOTEL_BOOKINGS: dict[str, dict[str, Any]] = {}

    @classmethod
    def save_in_memory_hotel(cls, data: dict[str, Any]) -> None:
        cls._IN_MEMORY_HOTEL_BOOKINGS[str(data["id"])] = data

    @classmethod
    def clear_in_memory(cls) -> None:
        cls._IN_MEMORY_HOTEL_BOOKINGS.clear()

    def __init__(self, hotel_store: HotelTransactionStore | None = None) -> None:
        self.use_in_memory = (
            os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}
            or not os.getenv("DATABASE_URL")
        )
        self._hotel_store = hotel_store

    def _get_hotel_store(self) -> HotelTransactionStore:
        if self._hotel_store is None:
            self._hotel_store = HotelTransactionStore()
        return self._hotel_store

    def get_trip_bookings(self, trip_id: UUID | str, user_id: UUID | str) -> dict[str, Any]:
        """Aggregate authoritative booking information for a trip, strictly enforcing tenant isolation."""
        tid_str = str(trip_id)
        uid_str = str(user_id)

        flight_data = self._get_flight_booking_for_trip(tid_str, uid_str)
        hotel_data = self._get_hotel_booking_for_trip(tid_str, uid_str)

        return {
            "trip_id": tid_str,
            "flight": flight_data,
            "hotel": hotel_data,
        }

    def _get_flight_booking_for_trip(self, trip_id: str, user_id: str) -> dict[str, Any] | None:
        intents: list[dict[str, Any]] = []
        if self.use_in_memory:
            for rec in FlightBookingService._IN_MEMORY_BOOKINGS.values():
                if str(rec.get("trip_id")) == trip_id and str(rec.get("user_id")) == user_id:
                    intents.append(rec)
            intents.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        else:
            try:
                with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
                    rows = conn.execute(
                        "SELECT * FROM booking_intents WHERE trip_id = %s AND user_id = %s ORDER BY updated_at DESC, created_at DESC",
                        (trip_id, user_id),
                    ).fetchall()
                    for r in rows:
                        item = dict(r)
                        if isinstance(item.get("metadata"), str):
                            try:
                                item["metadata"] = json.loads(item["metadata"])
                            except Exception:
                                item["metadata"] = {}
                        intents.append(item)
            except Exception as exc:
                logger.warning("Failed to query booking_intents for trip %s: %s", trip_id, exc)

        if not intents:
            return None

        completed_intents = [
            i for i in intents
            if str(i.get("status") or "").upper() == "COMPLETED"
            and not (
                (i.get("metadata") or {}).get("status") == "CANCELLED"
                or (i.get("metadata") or {}).get("cancellation")
                or (i.get("metadata") or {}).get("cancelled_at")
            )
        ]
        target = completed_intents[0] if completed_intents else intents[0]

        meta = target.get("metadata") or {}
        if (
            meta.get("status") == "CANCELLED"
            or meta.get("cancellation")
            or meta.get("cancelled_at")
        ):
            status = "CANCELLED"
        else:
            status = str(target.get("status") or "").upper()

        is_booked = (status == "COMPLETED")
        is_in_progress = (status in {"HANDED_OFF", "REVALIDATING", "READY_FOR_BOOKING"})

        booking_ref = target.get("provider_reference") or meta.get("booking_reference")
        order_id = meta.get("order_id")

        return {
            "is_booked": is_booked,
            "status": status.lower(),
            "in_progress": is_in_progress,
            "booking_id": str(target["id"]),
            "booking_reference": str(booking_ref) if booking_ref else None,
            "pnr": meta.get("pnr"),
            "order_id": str(order_id) if order_id else None,
            "provider": str(target.get("provider") or "duffel"),
            "amount": float(target.get("expected_amount") or 0.0),
            "currency": str(target.get("currency") or "INR"),
            "passengers": meta.get("passengers", []),
            "documents": meta.get("documents", []),
            "slices": meta.get("slices", []),
            "completed_at": meta.get("completed_at"),
            "created_at": target.get("created_at").isoformat() if hasattr(target.get("created_at"), "isoformat") else str(target.get("created_at") or ""),
        }

    def _get_hotel_booking_for_trip(self, trip_id: str, user_id: str) -> dict[str, Any] | None:
        transactions: list[dict[str, Any]] = []
        if self.use_in_memory:
            for rec in self._IN_MEMORY_HOTEL_BOOKINGS.values():
                if str(rec.get("trip_id")) == trip_id and str(rec.get("customer_ref")) == user_id:
                    transactions.append(rec)
        else:
            try:
                store = self._get_hotel_store()
                rows = store.get_by_trip(trip_id, customer_ref=user_id)
                transactions = [dict(r) for r in rows]
            except Exception as exc:
                logger.warning("Failed to fetch hotel transactions for trip %s: %s", trip_id, exc)
                for rec in self._IN_MEMORY_HOTEL_BOOKINGS.values():
                    if str(rec.get("trip_id")) == trip_id and str(rec.get("customer_ref")) == user_id:
                        transactions.append(rec)

        if not transactions:
            return None

        # Prioritize booked transaction if one exists, otherwise most recent
        booked_txs = [t for t in transactions if str(t.get("booking_status") or "").lower() == "booked"]
        target = booked_txs[0] if booked_txs else transactions[0]

        booking_status = str(target.get("booking_status") or "").lower()
        payment_status = str(target.get("payment_status") or "").lower()
        is_booked = (booking_status == "booked")
        is_in_progress = (booking_status in {"booking_in_progress", "awaiting_payment"})

        booking_json = target.get("booking_json") or {}
        if isinstance(booking_json, str):
            try:
                booking_json = json.loads(booking_json)
            except Exception:
                booking_json = {}

        offer_json = target.get("offer_json") or {}
        if isinstance(offer_json, str):
            try:
                offer_json = json.loads(offer_json)
            except Exception:
                offer_json = {}

        holder_json = target.get("holder_json") or {}
        if isinstance(holder_json, str):
            try:
                holder_json = json.loads(holder_json)
            except Exception:
                holder_json = {}

        guests_json = target.get("guests_json") or []
        if isinstance(guests_json, str):
            try:
                guests_json = json.loads(guests_json)
            except Exception:
                guests_json = []

        confirmation_code = (
            booking_json.get("confirmation_code")
            or booking_json.get("booking_id")
            or (target.get("id") if is_booked else None)
        )

        return {
            "is_booked": is_booked,
            "status": booking_status,
            "payment_status": payment_status,
            "in_progress": is_in_progress,
            "transaction_id": str(target["id"]),
            "confirmation_code": str(confirmation_code) if confirmation_code else None,
            "provider": str(target.get("provider") or "liteapi"),
            "hotel_name": target.get("hotel_name") or offer_json.get("hotel_name"),
            "room_name": offer_json.get("room_name"),
            "check_in": offer_json.get("checkin") or offer_json.get("check_in"),
            "check_out": offer_json.get("checkout") or offer_json.get("check_out"),
            "amount_inr": float(target.get("amount_inr") or 0.0),
            "currency": str(target.get("currency") or "INR"),
            "holder": holder_json,
            "guests": guests_json,
            "created_at": target.get("created_at").isoformat() if hasattr(target.get("created_at"), "isoformat") else str(target.get("created_at") or ""),
        }

    def list_recent_flights_for_user(self, user_id: UUID | str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent completed or active flight booking records for user to power My Trips."""
        uid_str = str(user_id)
        results: list[dict[str, Any]] = []
        if self.use_in_memory:
            for rec in FlightBookingService._IN_MEMORY_BOOKINGS.values():
                if str(rec.get("user_id")) == uid_str and str(rec.get("status") or "").upper() != "FAILED":
                    results.append(rec)
            results.sort(key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True)
        else:
            try:
                with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
                    rows = conn.execute(
                        "SELECT * FROM booking_intents WHERE user_id = %s AND UPPER(status::text) != 'FAILED' ORDER BY updated_at DESC, created_at DESC LIMIT %s",
                        (uid_str, limit),
                    ).fetchall()
                    for r in rows:
                        item = dict(r)
                        if isinstance(item.get("metadata"), str):
                            try:
                                item["metadata"] = json.loads(item["metadata"])
                            except Exception:
                                item["metadata"] = {}
                        results.append(item)
            except Exception as exc:
                logger.warning("Failed to query booking_intents for user %s: %s", uid_str, exc)

        normalized: list[dict[str, Any]] = []
        for r in results[:limit]:
            meta = r.get("metadata") or {}
            if (
                meta.get("status") == "CANCELLED"
                or meta.get("cancellation")
                or meta.get("cancelled_at")
            ):
                status_str = "cancelled"
            else:
                status_str = str(r.get("status") or "").lower()

            # Wave 3D-A: Exclude failed flight intents from active My Trips display
            if status_str in {"failed"}:
                continue

            booking_ref = r.get("provider_reference") or meta.get("booking_reference")
            normalized.append({
                "id": str(r["id"]),
                "trip_id": str(r["trip_id"]),
                "record_type": "flight",
                "customer_ref": uid_str,
                "provider": str(r.get("provider") or "duffel"),
                "booking_reference": str(booking_ref) if booking_ref else None,
                "pnr": meta.get("pnr"),
                "duffel_order_id": meta.get("order_id"),
                "booking_status": "booked" if status_str == "completed" else status_str,
                "amount": float(r.get("expected_amount") or 0.0),
                "currency": str(r.get("currency") or "INR"),
                "passenger_json": json.dumps(meta.get("passengers", [])),
                "metadata": meta,
                "created_at": r.get("created_at").isoformat() if hasattr(r.get("created_at"), "isoformat") else str(r.get("created_at") or ""),
            })
        return normalized
