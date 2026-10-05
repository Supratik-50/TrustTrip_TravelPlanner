"""Flight Booking Service for TrustTrip Travel Planner.

Implements Section 5B vertical slice:
1. Select existing persisted flight offer snapshot (validates tenant, trip binding, freshness).
2. Revalidate selected offer against provider abstraction (detects fare changes & inventory withdrawal).
3. Record Human-in-the-loop (HITL) approval (mandatory gate before booking confirmation).
4. Confirm booking via provider abstraction with duplicate confirmation & raw provider ID shielding.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from domain.booking import FlightBookingDestination
from domain.booking_lifecycle import BookingStatus
from domain.flight_booking import (
    ApprovalRequiredError,
    BookingNotFoundError,
    BookingPermissionError,
    DuplicateBookingError,
    FinalConfirmationRequiredError,
    FlightBookingApprovalResponse,
    FlightBookingConfirmationResponse,
    FlightBookingException,
    FlightBookingRevalidationResponse,
    FlightBookingSelectionResponse,
    FlightPassenger,
    OfferStaleError,
    OfferUnavailableError,
    PriceChangedError,
    ProviderBookingError,
    TripMismatchError,
)
from domain.live_offer import AvailabilityStatus, RevalidationStatus
from services.flight_provider import (
    FlightProvider,
    FlightProviderError,
    FlightProviderTimeout,
)
from services.flight_service import FlightService
from services.planner_service import PlannerService
from services.search_session_store import SearchSessionStore
from services.travel_intelligence.duffel_adapter import DuffelFlightAdapter
from services.trip_store import TripStore, resolve_current_revision

logger = logging.getLogger(__name__)


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


def _normalize_reconciled_order(order: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw Duffel order dictionary returned by find_order_by_offer into booking_res format."""
    return {
        "provider": "duffel",
        "order_id": order.get("id"),
        "booking_reference": order.get("booking_reference") or order.get("id"),
        "status": order.get("type") or "confirmed",
        "order_type": order.get("type"),
        "total_amount": order.get("total_amount"),
        "total_currency": order.get("total_currency"),
        "payment_required_by": order.get("payment_required_by"),
        "live_mode": order.get("live_mode", False),
        "documents": order.get("documents") or [],
        "slices": order.get("slices") or [],
        "pnr": order.get("booking_reference"),
        "raw": order,
    }


class FareChangedException(Exception):
    """Raised when revalidation detects price or schedule change during legacy handoff."""

    def __init__(
        self,
        original_snapshot_id: UUID,
        subsequent_snapshot_id: UUID,
        new_price: Decimal,
        currency: str,
        details: str,
    ) -> None:
        self.original_snapshot_id = original_snapshot_id
        self.subsequent_snapshot_id = subsequent_snapshot_id
        self.new_price = new_price
        self.currency = currency
        self.details = details
        super().__init__(f"Flight fare changed to {currency} {new_price}: {details}")


class OfferWithdrawnException(Exception):
    """Raised when supplier withdraws flight offer inventory during legacy handoff."""

    def __init__(self, snapshot_id: UUID, details: str) -> None:
        self.snapshot_id = snapshot_id
        self.details = details
        super().__init__(f"Flight offer withdrawn: {details}")


class FlightBookingService:
    """Orchestrates flight selection, tenant authorization, revalidation gate, and provider booking."""

    # In-memory storage for unit tests / test environment
    _IN_MEMORY_BOOKINGS: dict[str, dict[str, Any]] = {}
    _IN_MEMORY_TRANSITIONS: list[dict[str, Any]] = []

    def __init__(
        self,
        store: SearchSessionStore | None = None,
        trip_store: TripStore | None = None,
        flight_service: FlightService | None = None,
        planner: PlannerService | None = None,
        duffel_adapter: DuffelFlightAdapter | None = None,
    ) -> None:
        self.use_in_memory = (
            os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}
            or not os.getenv("DATABASE_URL")
        )
        self.store = store or SearchSessionStore()
        self.trip_store = trip_store or TripStore()
        self.flight_service = flight_service or FlightService()
        self.planner = planner or PlannerService()
        self.duffel_adapter = duffel_adapter or DuffelFlightAdapter()

    # ========================================================================
    # INTERNAL PERSISTENCE & INTENT HELPERS
    # ========================================================================

    def _get_intent(self, booking_id: UUID | str, user_id: UUID | str) -> dict[str, Any]:
        """Fetch booking intent enforcing strict tenant isolation."""
        bid_str = str(booking_id)
        uid_str = str(user_id)

        if self.use_in_memory:
            record = self._IN_MEMORY_BOOKINGS.get(bid_str)
            if not record:
                raise BookingNotFoundError(f"Booking intent '{bid_str}' not found.")
            if str(record.get("user_id")) != uid_str:
                raise BookingPermissionError(f"Access denied: Booking intent '{bid_str}' does not belong to you.")
            return record

        with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT * FROM booking_intents WHERE id = %s",
                (bid_str,),
            ).fetchone()
            if not row:
                raise BookingNotFoundError(f"Booking intent '{bid_str}' not found.")
            if str(row["user_id"]) != uid_str:
                raise BookingPermissionError(f"Access denied: Booking intent '{bid_str}' does not belong to you.")
            data = dict(row)
            if isinstance(data.get("metadata"), str):
                data["metadata"] = json.loads(data["metadata"])
            return data

    def _save_intent(self, intent_data: dict[str, Any]) -> None:
        """Update booking intent status and metadata."""
        bid_str = str(intent_data["id"])
        now = datetime.now(timezone.utc)
        intent_data["updated_at"] = now

        if self.use_in_memory:
            self._IN_MEMORY_BOOKINGS[bid_str] = intent_data
            return

        with psycopg.connect(_dsn(), autocommit=True) as conn:
            meta_json = json.dumps(intent_data.get("metadata") or {})
            conn.execute(
                """
                UPDATE booking_intents
                SET status = %s::booking_status_enum,
                    provider_reference = %s,
                    metadata = %s::jsonb,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    intent_data["status"],
                    intent_data.get("provider_reference"),
                    meta_json,
                    now,
                    bid_str,
                ),
            )

    # ========================================================================
    # 1. SELECT EXISTING OFFER
    # ========================================================================

    async def select_offer(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        offer_id: UUID | str,
        passengers: list[dict[str, Any] | FlightPassenger],
    ) -> FlightBookingSelectionResponse:
        """Select an existing persisted flight offer and initialize a booking intent."""
        uid_str = str(user_id)
        tid_str = str(trip_id)
        oid_str = str(offer_id)

        try:
            offer_uuid = UUID(oid_str)
        except (ValueError, TypeError):
            raise BookingNotFoundError(f"Invalid offer ID format '{oid_str}'.")

        # 1. Validate Trip ownership
        trip = self.trip_store.get_trip(tid_str, uid_str)
        if not trip:
            # Check if trip exists for another user to distinguish 403 vs 404
            unowned = False
            if self.use_in_memory:
                unowned = tid_str in self.trip_store._memory_trips
            else:
                try:
                    with psycopg.connect(_dsn()) as conn:
                        row = conn.execute("SELECT user_id FROM trips WHERE id = %s", (tid_str,)).fetchone()
                        unowned = bool(row)
                except Exception:
                    unowned = False
            if unowned:
                raise BookingPermissionError(f"Access denied: Trip '{tid_str}' does not belong to you.")
            raise BookingNotFoundError(f"Trip '{tid_str}' not found.")

        # 2. Validate Snapshot existence and ownership
        snapshot = self.store.get_snapshot(offer_uuid, uid_str)
        if not snapshot:
            # Distinguish snapshot unowned vs not existing
            exists_unowned = False
            if self.use_in_memory:
                exists_unowned = oid_str in self.store._IN_MEMORY_SNAPSHOTS
            else:
                try:
                    with psycopg.connect(_dsn()) as conn:
                        row = conn.execute("SELECT id FROM offer_snapshots WHERE id = %s", (oid_str,)).fetchone()
                        exists_unowned = bool(row)
                except Exception:
                    exists_unowned = False
            if exists_unowned:
                raise BookingPermissionError(f"Access denied: Offer snapshot '{oid_str}' does not belong to you.")
            raise BookingNotFoundError(f"Offer snapshot '{oid_str}' not found.")

        # 3. Check offer type
        if snapshot.offer_type != "flight":
            raise TripMismatchError(f"Snapshot '{oid_str}' is a '{snapshot.offer_type}' offer, not a flight offer.")

        # 4. Check trip binding (session trip must match current trip)
        session = self.store.get_session(snapshot.session_id, uid_str)
        if not session or str(session.trip_id) != tid_str:
            session_trip = session.trip_id if session else "unknown"
            raise TripMismatchError(
                f"Offer ownership/trip mismatch: Offer snapshot '{oid_str}' was generated for trip '{session_trip}', not current trip '{tid_str}'."
            )

        # 5. Check staleness / expiration
        now = datetime.now(timezone.utc)
        if snapshot.expires_at and snapshot.expires_at < now:
            raise OfferStaleError(f"The selected flight offer '{oid_str}' has expired.")
        if snapshot.availability == AvailabilityStatus.UNAVAILABLE:
            raise OfferStaleError(f"The selected flight offer '{oid_str}' is no longer available.")

        # 6. Validate passengers
        if not passengers:
            raise ValueError("At least one passenger is required for flight booking.")
        if len(passengers) > 9:
            raise ValueError("Maximum 9 passengers permitted per booking.")

        normalized_passengers = []
        for p in passengers:
            if isinstance(p, FlightPassenger):
                normalized_passengers.append(p.model_dump())
            elif isinstance(p, dict):
                fp = FlightPassenger.model_validate(p)
                normalized_passengers.append(fp.model_dump())
            else:
                raise ValueError("Invalid passenger data.")

        # Validate passenger count against trip constraints if set
        meta = trip.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        constraints = meta.get("constraints") or {}
        expected_travelers = constraints.get("travelers_count") or meta.get("travelers_count") or trip.get("travelers_count")
        if expected_travelers is not None:
            try:
                exp_count = int(expected_travelers)
                if exp_count > 0 and len(normalized_passengers) != exp_count:
                    raise ValueError(
                        f"Passenger count mismatch: trip requires {exp_count} passenger(s), "
                        f"but {len(normalized_passengers)} passenger(s) were supplied."
                    )
            except (TypeError, ValueError) as exc:
                if "Passenger count mismatch" in str(exc):
                    raise

        # 7. Create booking intent
        booking_id = uuid4()
        idempotency_key = f"book_{booking_id}"
        pass_hash = hashlib.sha256(json.dumps(normalized_passengers, sort_keys=True).encode("utf-8")).hexdigest()
        price_total = Decimal(str(snapshot.price_total))
        currency = str(snapshot.currency)[:3].upper()

        intent_data = {
            "id": booking_id,
            "user_id": UUID(uid_str),
            "trip_id": UUID(tid_str),
            "selected_offer_snapshot_id": offer_uuid,
            "current_revalidation_snapshot_id": offer_uuid,
            "status": BookingStatus.SELECTED.value,
            "expected_amount": price_total,
            "currency": currency,
            "idempotency_key": idempotency_key,
            "passenger_hash": pass_hash,
            "provider_reference": None,
            "metadata": {
                "approved": False,
                "approved_at": None,
                "passengers": normalized_passengers,
                "passenger_count": len(normalized_passengers),
                "offer_id": oid_str,
                "trip_id": tid_str,
                "created_at": now.isoformat(),
            },
            "created_at": now,
            "updated_at": now,
            "expires_at": snapshot.expires_at or (now + timedelta(minutes=30)),
        }

        # 8. Persist
        if self.use_in_memory:
            self._IN_MEMORY_BOOKINGS[str(booking_id)] = intent_data
        else:
            with psycopg.connect(_dsn(), autocommit=True) as conn:
                # Ensure plan revision row exists for foreign key
                current_rev_num = resolve_current_revision(trip) or 1
                rev_row = conn.execute(
                    "SELECT revision_id FROM plan_revisions WHERE trip_id = %s ORDER BY revision_number DESC LIMIT 1",
                    (tid_str,),
                ).fetchone()
                if rev_row:
                    revision_id = rev_row[0]
                else:
                    revision_id = uuid4()
                    conn.execute(
                        """
                        INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot, is_published, published_at, created_at)
                        VALUES (%s, %s, %s, %s, '{}'::jsonb, '{}'::jsonb, true, now(), now())
                        ON CONFLICT DO NOTHING
                        """,
                        (revision_id, tid_str, uid_str, current_rev_num),
                    )

                conn.execute(
                    """
                    INSERT INTO booking_intents (
                        id, user_id, trip_id, revision_id, selected_offer_snapshot_id,
                        current_revalidation_snapshot_id, provider, booking_mode, status,
                        idempotency_key, passenger_hash, expected_amount, currency,
                        metadata, created_at, updated_at, expires_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, 'duffel'::booking_provider_enum, 'duffel_api_order'::booking_mode_enum,
                        %s::booking_status_enum, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                    )
                    """,
                    (
                        booking_id,
                        uid_str,
                        tid_str,
                        str(revision_id),
                        oid_str,
                        oid_str,
                        BookingStatus.SELECTED.value,
                        idempotency_key,
                        pass_hash,
                        price_total,
                        currency,
                        json.dumps(intent_data["metadata"]),
                        now,
                        now,
                        intent_data["expires_at"],
                    ),
                )

        # Add inr price if available from snapshot
        price_inr = None
        if hasattr(snapshot.normalized_offer, "price_inr"):
            price_inr = snapshot.normalized_offer.price_inr

        return FlightBookingSelectionResponse(
            success=True,
            booking_id=str(booking_id),
            trip_id=tid_str,
            offer_id=oid_str,
            status=BookingStatus.SELECTED.value,
            price=float(price_total),
            currency=currency,
            price_inr=float(price_inr) if price_inr is not None else None,
            passenger_count=len(normalized_passengers),
            human_approval_required=True,
            approved=False,
            message="Flight offer selected. Revalidation and human approval are required before booking.",
        )

    # ========================================================================
    # 2. REVALIDATE SELECTED OFFER
    # ========================================================================

    async def revalidate_offer(
        self,
        user_id: UUID | str,
        booking_id: UUID | str,
        accepted_price: float | None = None,
    ) -> FlightBookingRevalidationResponse:
        """Revalidate price and availability against the upstream provider abstraction."""
        intent = self._get_intent(booking_id, user_id)
        current_status = intent["status"]

        if current_status in {
            BookingStatus.COMPLETED.value,
            BookingStatus.FAILED.value,
            BookingStatus.EXPIRED.value,
            BookingStatus.UNAVAILABLE.value,
            BookingStatus.HANDED_OFF.value,
        }:
            raise FlightBookingException(
                f"Cannot revalidate booking intent in terminal or in-progress state '{current_status}'.",
                status_code=400,
            )

        # Lifecycle transition: PRICE_CHANGED -> SELECTED -> REVALIDATING
        if current_status == BookingStatus.PRICE_CHANGED.value:
            intent["status"] = BookingStatus.SELECTED.value
            if accepted_price is not None:
                intent["expected_amount"] = Decimal(str(accepted_price))
            elif intent.get("metadata", {}).get("updated_price") is not None:
                intent["expected_amount"] = Decimal(str(intent["metadata"]["updated_price"]))
            self._save_intent(intent)

        # Transition status to REVALIDATING
        intent["status"] = BookingStatus.REVALIDATING.value
        self._save_intent(intent)

        # Retrieve persisted snapshot to obtain internal provider_offer_id
        snapshot_id = intent["selected_offer_snapshot_id"]
        snapshot = self.store.get_snapshot(snapshot_id, user_id)
        if not snapshot:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError(f"Underlying snapshot '{snapshot_id}' not found.")

        raw_provider_offer_id = snapshot.provider_offer_id
        expected_amount = float(intent["expected_amount"])
        currency = str(intent["currency"]).upper()

        # Check staleness / expiration
        now = datetime.now(timezone.utc)
        expires_at = intent.get("expires_at")
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except Exception:
                expires_at = None
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        snapshot_expires_at = snapshot.expires_at
        if snapshot_expires_at and snapshot_expires_at.tzinfo is None:
            snapshot_expires_at = snapshot_expires_at.replace(tzinfo=timezone.utc)

        if (expires_at and expires_at < now) or (snapshot_expires_at and snapshot_expires_at < now):
            intent["status"] = BookingStatus.EXPIRED.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferStaleError("The flight offer snapshot or intent has expired.")

        if snapshot.availability == AvailabilityStatus.UNAVAILABLE:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError("The underlying flight offer snapshot is marked unavailable.")

        # Query provider abstraction
        try:
            provider_offer = await asyncio.to_thread(self.flight_service.recheck, raw_provider_offer_id)
        except FlightProviderTimeout as exc:
            raise FlightProviderTimeout(f"Upstream provider timed out: {exc}")
        except FlightProviderError as exc:
            logger.warning("Provider recheck error for offer: %s", exc)
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError(f"Offer is no longer available from provider: {exc}")
        except Exception as exc:
            logger.error("Unexpected provider error during recheck: %s", exc, exc_info=True)
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError("Flight provider is unavailable.")

        if not provider_offer or provider_offer.get("available") is False:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError("The selected flight offer is no longer available.")

        # Check price and currency
        current_amount_val = provider_offer.get("price")
        if current_amount_val is None:
            current_amount_val = provider_offer.get("total_amount")
        if current_amount_val is None:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError("Provider offer has no current price.")

        current_amount = float(current_amount_val)
        current_curr = str(provider_offer.get("currency") or currency).upper()

        price_diff = abs(current_amount - expected_amount) > 0.01
        curr_diff = current_curr != currency

        if price_diff or curr_diff:
            intent["status"] = BookingStatus.PRICE_CHANGED.value
            intent["metadata"]["updated_price"] = current_amount
            intent["metadata"]["updated_currency"] = current_curr
            # Invalidate previous human approval on price change
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise PriceChangedError(
                original_price=expected_amount,
                new_price=current_amount,
                currency=current_curr,
                message=f"Fare has changed from {currency} {expected_amount:.2f} to {current_curr} {current_amount:.2f}.",
            )

        # Offer valid and unchanged
        intent["status"] = BookingStatus.READY_FOR_BOOKING.value
        intent["metadata"]["last_revalidated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_intent(intent)

        approved = bool(intent.get("metadata", {}).get("approved", False))
        return FlightBookingRevalidationResponse(
            success=True,
            booking_id=str(intent["id"]),
            status=BookingStatus.READY_FOR_BOOKING.value,
            is_valid=True,
            price_changed=False,
            original_price=expected_amount,
            current_price=current_amount,
            currency=currency,
            human_approval_required=True,
            approved=approved,
            message="Offer successfully revalidated and ready for human approval.",
        )

    # ========================================================================
    # 3. RECORD HUMAN APPROVAL (HITL)
    # ========================================================================

    async def record_approval(
        self,
        user_id: UUID | str,
        booking_id: UUID | str,
        approved: bool = True,
        notes: str | None = None,
    ) -> FlightBookingApprovalResponse:
        """Record explicit human approval for a revalidated booking intent."""
        intent = self._get_intent(booking_id, user_id)
        current_status = intent["status"]

        if current_status != BookingStatus.READY_FOR_BOOKING.value:
            raise FlightBookingException(
                f"Booking must be revalidated (READY_FOR_BOOKING) before human approval can be recorded. Current status is '{current_status}'.",
                status_code=400,
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        intent["metadata"]["approved"] = approved
        intent["metadata"]["approved_at"] = now_iso
        intent["metadata"]["approved_price"] = float(intent["expected_amount"])
        if notes:
            intent["metadata"]["approval_notes"] = notes

        self._save_intent(intent)

        return FlightBookingApprovalResponse(
            success=True,
            booking_id=str(intent["id"]),
            status=intent["status"],
            approved=approved,
            approved_at=now_iso,
            message="Human approval recorded successfully.",
        )

    # ========================================================================
    # 4. CONFIRM BOOKING
    # ========================================================================

    async def confirm_booking(
        self,
        user_id: UUID | str,
        booking_id: UUID | str,
        final_confirmation: bool = False,
        payment: dict[str, Any] | None = None,
        payment_mode: str = "auto",
    ) -> FlightBookingConfirmationResponse:
        """Confirm flight booking after revalidation and human approval."""
        intent = self._get_intent(booking_id, user_id)
        current_status = intent["status"]

        # 1. State guards & duplicate / in-progress confirmation protection
        if current_status == BookingStatus.HANDED_OFF.value:
            # Check if an existing provider order was already created on a prior attempt
            snapshot_id = intent.get("selected_offer_snapshot_id")
            snapshot = self.store.get_snapshot(snapshot_id, user_id) if snapshot_id else None
            raw_provider_offer_id = snapshot.provider_offer_id if snapshot else None
            if raw_provider_offer_id and hasattr(self.flight_service, "find_order_by_offer"):
                try:
                    existing_order = await asyncio.to_thread(
                        self.flight_service.find_order_by_offer, raw_provider_offer_id
                    )
                    if existing_order and (existing_order.get("id") or existing_order.get("booking_reference")):
                        logger.info(
                            "Found existing provider order %s for HANDED_OFF intent %s; reconciling.",
                            existing_order.get("id"), booking_id
                        )
                        booking_res = _normalize_reconciled_order(existing_order)
                        return self._finalize_completed_booking(
                            intent=intent,
                            snapshot=snapshot,
                            booking_res=booking_res,
                            expected_amount=float(intent["expected_amount"]),
                            currency=str(intent["currency"]).upper(),
                            passengers=intent.get("metadata", {}).get("passengers", []),
                        )
                except Exception as rec_err:
                    logger.warning("Reconciliation query failed for HANDED_OFF intent %s: %s", booking_id, rec_err)
            raise DuplicateBookingError(
                "Duplicate confirmation blocked: Booking confirmation is currently in progress (HANDED_OFF)."
            )

        if current_status == BookingStatus.COMPLETED.value:
            raise DuplicateBookingError(
                "Duplicate confirmation blocked: Booking has already been completed."
            )

        if current_status in {BookingStatus.FAILED.value, BookingStatus.EXPIRED.value, BookingStatus.UNAVAILABLE.value}:
            raise FlightBookingException(
                f"Cannot confirm booking in terminal state '{current_status}'.",
                status_code=400,
            )

        if current_status == BookingStatus.PRICE_CHANGED.value:
            raise FlightBookingException(
                "Booking cannot be confirmed while fare has changed. Revalidation and fresh human approval are required.",
                status_code=400,
            )

        if current_status != BookingStatus.READY_FOR_BOOKING.value:
            raise FlightBookingException(
                f"Booking must be revalidated and approved before confirmation. Current status is '{current_status}'.",
                status_code=400,
            )

        # 2. Human approval check (HITL)
        approved = bool(intent.get("metadata", {}).get("approved", False))
        if not approved:
            raise ApprovalRequiredError("Booking blocked: Human approval is mandatory before booking.")

        # 3. Final confirmation check
        if not final_confirmation:
            raise FinalConfirmationRequiredError("Final user confirmation is required before booking.")

        # 4. Trip ownership and relationship validation
        trip = self.trip_store.get_trip(str(intent["trip_id"]), str(user_id))
        if not trip:
            raise BookingNotFoundError(f"Trip '{intent['trip_id']}' not found.")

        # 5. Immediate pre-booking revalidation & staleness verification
        snapshot_id = intent["selected_offer_snapshot_id"]
        snapshot = self.store.get_snapshot(snapshot_id, user_id)
        if not snapshot:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError(f"Snapshot '{snapshot_id}' not found.")

        # Check staleness / expiration of intent and snapshot
        now = datetime.now(timezone.utc)
        expires_at = intent.get("expires_at")
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except Exception:
                expires_at = None
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        snapshot_expires_at = snapshot.expires_at
        if snapshot_expires_at and snapshot_expires_at.tzinfo is None:
            snapshot_expires_at = snapshot_expires_at.replace(tzinfo=timezone.utc)

        if (expires_at and expires_at < now) or (snapshot_expires_at and snapshot_expires_at < now):
            intent["status"] = BookingStatus.EXPIRED.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferStaleError(f"The flight booking intent or underlying offer snapshot has expired.")

        if snapshot.availability == AvailabilityStatus.UNAVAILABLE:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError(f"The underlying flight offer snapshot is marked unavailable.")

        raw_provider_offer_id = snapshot.provider_offer_id
        expected_amount = float(intent["expected_amount"])
        currency = str(intent["currency"]).upper()

        # Enforce approved price binding consistency
        approved_price = intent.get("metadata", {}).get("approved_price")
        if approved_price is not None and abs(float(approved_price) - expected_amount) > 0.01:
            intent["status"] = BookingStatus.PRICE_CHANGED.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise PriceChangedError(
                original_price=float(approved_price),
                new_price=expected_amount,
                currency=currency,
                message=f"Fare has changed since approval from {approved_price} to {expected_amount}. Fresh approval required.",
            )

        try:
            latest_offer = await asyncio.to_thread(self.flight_service.recheck, raw_provider_offer_id)
        except Exception as exc:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError(f"Provider recheck failed immediately prior to booking: {exc}")

        current_amount_val = latest_offer.get("price") or latest_offer.get("total_amount")
        if current_amount_val is None or latest_offer.get("available") is False:
            intent["status"] = BookingStatus.UNAVAILABLE.value
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise OfferUnavailableError("Flight offer is no longer available immediately prior to booking.")

        current_amount = float(current_amount_val)
        if abs(current_amount - expected_amount) > 0.01:
            intent["status"] = BookingStatus.PRICE_CHANGED.value
            intent["metadata"]["updated_price"] = current_amount
            intent["metadata"]["approved"] = False
            intent["metadata"]["approved_at"] = None
            self._save_intent(intent)
            raise PriceChangedError(
                original_price=expected_amount,
                new_price=current_amount,
                currency=currency,
                message=f"Fare changed to {currency} {current_amount:.2f} immediately prior to booking.",
            )

        # 5. Transition to HANDED_OFF before provider booking call
        intent["status"] = BookingStatus.HANDED_OFF.value
        self._save_intent(intent)

        # 6. Execute booking call via provider abstraction
        passengers = intent.get("metadata", {}).get("passengers", [])
        booking_res = None
        try:
            booking_res = await asyncio.to_thread(
                self.flight_service.provider.book,
                raw_provider_offer_id,
                passengers,
                payment=payment,
                payment_mode=payment_mode,
            )
        except Exception as exc:
            # Ambiguous provider exception: Duffel order creation is non-transactional
            # with respect to network drops/timeouts/500s. Attempt to locate an existing order
            # before marking intent as failed.
            recovered_order = None
            rec_lookup_failed = False
            if hasattr(self.flight_service, "find_order_by_offer"):
                try:
                    recovered_order = await asyncio.to_thread(
                        self.flight_service.find_order_by_offer, raw_provider_offer_id
                    )
                except Exception as rec_err:
                    rec_lookup_failed = True
                    logger.warning(
                        "Reconciliation lookup failed for offer %s after error %s: %s",
                        raw_provider_offer_id, exc, rec_err
                    )

            if recovered_order and (recovered_order.get("id") or recovered_order.get("booking_reference")):
                logger.info(
                    "Reconciled existing provider order %s for intent %s after booking exception: %s",
                    recovered_order.get("id"), booking_id, exc
                )
                booking_res = _normalize_reconciled_order(recovered_order)
            elif rec_lookup_failed:
                # Both provider booking and reconciliation lookup failed; state is strictly ambiguous.
                # Keep intent in HANDED_OFF to block duplicate booking until status can be verified.
                intent["metadata"]["failure_reason"] = f"Ambiguous provider response: {exc}. Reconciliation lookup also failed."
                self._save_intent(intent)
                raise ProviderBookingError(
                    f"Flight provider response was ambiguous ({exc}) and status could not be verified. "
                    "Booking remains in progress. Please check booking status before retrying."
                ) from exc
            else:
                # Reconciliation lookup succeeded and confirmed NO provider order was created.
                # Safe to mark the intent as FAILED.
                intent["status"] = BookingStatus.FAILED.value
                intent["metadata"]["failure_reason"] = f"{type(exc).__name__}: {exc}"
                self._save_intent(intent)
                if isinstance(exc, FlightProviderTimeout):
                    raise ProviderBookingError(f"Flight provider timed out during booking: {exc}") from exc
                elif isinstance(exc, FlightProviderError):
                    raise ProviderBookingError(f"Flight provider booking failed: {exc}") from exc
                else:
                    raise ProviderBookingError(f"Booking execution failed: {exc}") from exc

        # 7. Transition to COMPLETED
        return self._finalize_completed_booking(
            intent=intent,
            snapshot=snapshot,
            booking_res=booking_res,
            expected_amount=expected_amount,
            currency=currency,
            passengers=passengers,
        )

    def _finalize_completed_booking(
        self,
        intent: dict[str, Any],
        snapshot: Any,
        booking_res: dict[str, Any],
        expected_amount: float,
        currency: str,
        passengers: list[dict[str, Any]],
    ) -> FlightBookingConfirmationResponse:
        booking_ref = (
            booking_res.get("booking_reference")
            or booking_res.get("reference")
            or booking_res.get("id")
            or "CONFIRMED"
        )
        order_id = booking_res.get("order_id") or booking_res.get("id")
        raw_res = booking_res.get("raw") if isinstance(booking_res.get("raw"), dict) else {}

        # Extract documents and e-ticket numbers safely
        documents = booking_res.get("documents") or raw_res.get("documents") or []
        eticket_val = booking_res.get("e_ticket")
        if not eticket_val and documents:
            for doc in documents:
                for key in ("ticket_number", "document_number", "number", "unique_identifier"):
                    val = doc.get(key)
                    if val:
                        eticket_val = str(val)
                        break
                if eticket_val:
                    break

        pnr_val = booking_res.get("pnr")

        # Extract slices / itinerary for persistent ticket generation without Duffel dependency
        slices = booking_res.get("slices") or raw_res.get("slices") or []
        if not slices and hasattr(snapshot, "normalized_offer") and snapshot.normalized_offer:
            norm_offer = snapshot.normalized_offer
            if hasattr(norm_offer, "segments") and norm_offer.segments:
                seg_list = []
                for s in norm_offer.segments:
                    s_dict = s.model_dump() if hasattr(s, "model_dump") else (s if isinstance(s, dict) else dict(s))
                    seg_list.append({
                        "origin": {"iata_code": s_dict.get("origin_iata"), "name": s_dict.get("origin_iata")},
                        "destination": {"iata_code": s_dict.get("destination_iata"), "name": s_dict.get("destination_iata")},
                        "origin_iata": s_dict.get("origin_iata"),
                        "destination_iata": s_dict.get("destination_iata"),
                        "operating_carrier": {"name": s_dict.get("operating_carrier_name") or s_dict.get("marketing_carrier_name") or "Airline"},
                        "marketing_carrier": {"name": s_dict.get("marketing_carrier_name") or "Airline"},
                        "marketing_carrier_name": s_dict.get("marketing_carrier_name") or "Airline",
                        "marketing_carrier_flight_number": s_dict.get("flight_number") or "",
                        "flight_number": s_dict.get("flight_number") or "",
                        "departing_at": s_dict.get("departure_time").isoformat() if hasattr(s_dict.get("departure_time"), "isoformat") else str(s_dict.get("departure_time") or ""),
                        "arriving_at": s_dict.get("arrival_time").isoformat() if hasattr(s_dict.get("arrival_time"), "isoformat") else str(s_dict.get("arrival_time") or ""),
                        "departure_time": str(s_dict.get("departure_time") or ""),
                        "arrival_time": str(s_dict.get("arrival_time") or ""),
                    })
                slices = [{"segments": seg_list}]

        completed_intent = copy.deepcopy(intent)
        completed_intent["status"] = BookingStatus.COMPLETED.value
        completed_intent["provider_reference"] = str(booking_ref)
        if "metadata" not in completed_intent or not isinstance(completed_intent["metadata"], dict):
            completed_intent["metadata"] = {}
        completed_intent["metadata"]["booking_reference"] = str(booking_ref)
        completed_intent["metadata"]["order_id"] = str(order_id) if order_id else None
        completed_intent["metadata"]["pnr"] = str(pnr_val) if pnr_val else None
        completed_intent["metadata"]["e_ticket"] = str(eticket_val) if eticket_val else None
        completed_intent["metadata"]["documents"] = documents
        completed_intent["metadata"]["slices"] = slices
        completed_intent["metadata"]["passengers"] = passengers or completed_intent["metadata"].get("passengers", [])
        completed_intent["metadata"]["completed_at"] = datetime.now(timezone.utc).isoformat()

        try:
            self._save_intent(completed_intent)
            intent.update(completed_intent)
        except Exception as exc:
            logger.error(
                "Provider booking succeeded (%s), but local persistence of COMPLETED state failed for intent %s: %s",
                booking_ref, intent["id"], exc, exc_info=True
            )
            # Do NOT claim COMPLETED unless local persistence succeeds
            # The booking remains in HANDED_OFF in persistent store to prevent re-booking
            raise FlightBookingException(
                f"Provider booking succeeded ({booking_ref}), but local persistence of completion failed. Booking remains in HANDED_OFF state for safety.",
                status_code=500,
            )

        return FlightBookingConfirmationResponse(
            success=True,
            booked=True,
            booking_id=str(intent["id"]),
            status=BookingStatus.COMPLETED.value,
            booking_reference=str(booking_ref),
            order_id=str(order_id) if order_id else None,
            price=expected_amount,
            currency=currency,
            pnr=str(pnr_val) if pnr_val else None,
            e_ticket=str(eticket_val) if eticket_val else None,
            message="Flight booking confirmed successfully.",
        )

    # ========================================================================
    # 5. GET BOOKING STATUS
    # ========================================================================

    async def get_booking(self, user_id: UUID | str, booking_id: UUID | str) -> dict[str, Any]:
        """Query booking status strictly shielding raw provider IDs."""
        intent = self._get_intent(booking_id, user_id)
        if intent.get("status") == BookingStatus.HANDED_OFF.value:
            snapshot_id = intent.get("selected_offer_snapshot_id")
            snapshot = self.store.get_snapshot(snapshot_id, user_id) if snapshot_id else None
            raw_provider_offer_id = snapshot.provider_offer_id if snapshot else None
            if raw_provider_offer_id and hasattr(self.flight_service, "find_order_by_offer"):
                try:
                    existing_order = await asyncio.to_thread(
                        self.flight_service.find_order_by_offer, raw_provider_offer_id
                    )
                    if existing_order and (existing_order.get("id") or existing_order.get("booking_reference")):
                        booking_res = _normalize_reconciled_order(existing_order)
                        self._finalize_completed_booking(
                            intent=intent,
                            snapshot=snapshot,
                            booking_res=booking_res,
                            expected_amount=float(intent["expected_amount"]),
                            currency=str(intent["currency"]).upper(),
                            passengers=intent.get("metadata", {}).get("passengers", []),
                        )
                        intent = self._get_intent(booking_id, user_id)
                except Exception as rec_err:
                    logger.warning("Reconciliation lookup in get_booking failed for intent %s: %s", booking_id, rec_err)

        meta = intent.get("metadata") or {}
        booking_ref = intent.get("provider_reference") or meta.get("booking_reference")
        return {
            "booking_id": str(intent["id"]),
            "trip_id": str(intent["trip_id"]),
            "offer_id": str(intent["selected_offer_snapshot_id"]),
            "status": intent["status"],
            "expected_amount": float(intent["expected_amount"]),
            "currency": intent["currency"],
            "approved": bool(meta.get("approved", False)),
            "approved_at": meta.get("approved_at"),
            "passenger_count": meta.get("passenger_count", len(meta.get("passengers", [])) or 1),
            "booking_reference": str(booking_ref) if booking_ref else None,
            "order_id": meta.get("order_id"),
            "pnr": meta.get("pnr"),
            "e_ticket": meta.get("e_ticket"),
            "documents": meta.get("documents", []),
            "slices": meta.get("slices", []),
            "passengers": meta.get("passengers", []),
            "created_at": (
                intent.get("created_at").isoformat()
                if hasattr(intent.get("created_at"), "isoformat")
                else str(intent.get("created_at") or "")
            ),
        }

    # ========================================================================
    # LEGACY HANDOFF SUPPORT
    # ========================================================================

    async def select_and_handoff_flight(
        self,
        snapshot_id: UUID,
        authenticated_user_id: UUID | str,
    ) -> FlightBookingDestination:
        """Legacy handoff retained for backwards compatibility."""
        snapshot = self.store.get_snapshot(snapshot_id, authenticated_user_id)
        if not snapshot:
            raise PermissionError(
                f"Access denied: Snapshot '{snapshot_id}' does not exist or does not belong to the user."
            )

        if snapshot.offer_type != "flight":
            raise ValueError(f"Snapshot '{snapshot_id}' is not a flight offer.")

        result = await self.planner.revalidate_offer(snapshot_id, authenticated_user_id)
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

        raw_payload = self.store.get_raw_payload(snapshot_id, authenticated_user_id)
        return self.duffel_adapter.resolve_booking_destination(
            snapshot_id=snapshot.snapshot_id,
            offer=snapshot.normalized_offer,
            raw_payload=raw_payload,
        )
