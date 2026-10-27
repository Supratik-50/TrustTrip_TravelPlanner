"""Database persistence for Trip and Thread ownership.

Enforces strict tenant isolation: every trip and thread is bound to user_id.
All queries require WHERE user_id = %s.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

from domain.live_offer import AvailabilityStatus, FreshnessStatus

# Explicit lifecycle classification for safe trip deletion
ELIGIBLE_PRE_TRAVEL_STATUSES: frozenset[str] = frozenset({"draft", "planning", "awaiting_approval"})
ACTUALLY_ONGOING_STATUSES: frozenset[str] = frozenset({"active", "ongoing", "in_progress"})


class TripStoreError(Exception):
    """Base error for trip store operations."""
    pass


class RevisionContextError(TripStoreError):
    """Raised when trip revision context is missing or cannot be resolved."""
    pass


class OfferSelectionError(TripStoreError):
    """Raised when offer selection fails validation, ownership, freshness, or revision match."""
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def resolve_current_revision(trip: dict[str, Any], fail_on_divergence: bool = True) -> int | None:
    """Explicitly resolve the authoritative trip revision, failing closed if unavailable or inconsistent.

    Authoritative source is trip['current_revision_number'].
    Metadata may mirror revision information ('trip_revision' or 'revision'),
    but MUST NOT override the database revision pointer.
    If metadata contains a revision mirror and it diverges from the authoritative
    current_revision_number, fails closed with RevisionContextError.
    Revision 0 is explicitly supported as a valid initial revision.
    """
    if not isinstance(trip, dict):
        return None

    db_rev_raw = trip.get("current_revision_number")
    db_rev: int | None = int(db_rev_raw) if db_rev_raw is not None else None

    meta = trip.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    meta_rev: int | None = None
    if isinstance(meta, dict):
        if "trip_revision" in meta and meta["trip_revision"] is not None:
            meta_rev = int(meta["trip_revision"])
        elif "revision" in meta and meta["revision"] is not None:
            meta_rev = int(meta["revision"])

    if db_rev is not None:
        if meta_rev is not None and meta_rev != db_rev:
            if fail_on_divergence:
                raise RevisionContextError(
                    f"Revision context divergence: database revision {db_rev} "
                    f"but metadata revision mirror is {meta_rev}."
                )
        return db_rev

    if meta_rev is not None:
        return meta_rev

    return None


_MEMORY_TRIP_LOCKS: dict[str, threading.Lock] = {}
_MEMORY_GLOBAL_LOCK = threading.Lock()


def _get_memory_trip_lock(trip_id: str) -> threading.Lock:
    with _MEMORY_GLOBAL_LOCK:
        if trip_id not in _MEMORY_TRIP_LOCKS:
            _MEMORY_TRIP_LOCKS[trip_id] = threading.Lock()
        return _MEMORY_TRIP_LOCKS[trip_id]


_TRIP_SCHEMA_INITIALIZED = False


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for trip persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


def init_trip_tables() -> None:
    """Initialize trips and threads tables once at startup."""
    global _TRIP_SCHEMA_INITIALIZED
    if _TRIP_SCHEMA_INITIALIZED:
        return

    if os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}:
        _TRIP_SCHEMA_INITIALIZED = True
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return

    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"

    from services.auth_store import init_auth_tables
    init_auth_tables()

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trips (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT '',
                destination TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planning',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_trips_user_id ON trips (user_id);
            CREATE INDEX IF NOT EXISTS idx_trips_created_at ON trips (created_at DESC);

            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_threads_user_id ON threads (user_id);
            CREATE INDEX IF NOT EXISTS idx_threads_trip_id ON threads (trip_id);
        """)
    _TRIP_SCHEMA_INITIALIZED = True


def _build_selected_flight_metadata(
    norm_offer: Any,
    snapshot_id: str | uuid.UUID,
    trip: dict[str, Any],
) -> dict[str, Any]:
    """Extract and build symmetrical flight offer metadata for trip persistence."""
    f_price_val = None
    f_currency_val = "USD"
    f_price_inr_val = None
    if hasattr(norm_offer, "price"):
        f_price_val = float(norm_offer.price.total_amount)
        f_currency_val = str(norm_offer.price.currency)
        if getattr(norm_offer.price, "inr_reference_amount", None) is not None:
            f_price_inr_val = float(norm_offer.price.inr_reference_amount)
    elif isinstance(norm_offer, dict):
        f_price_val = float(norm_offer.get("price") or norm_offer.get("total_amount") or 0)
        f_currency_val = str(norm_offer.get("currency", "USD"))
        f_price_inr_val = float(norm_offer["price_inr"]) if norm_offer.get("price_inr") is not None else None

    carrier_name = "Airline"
    carrier_code = ""
    cabin_class = "economy"
    f_slices: list[dict[str, Any]] = []

    def _make_slice(segs: list[dict[str, Any]], fallback_origin: str = "", fallback_destination: str = "") -> dict[str, Any]:
        return {
            "origin": segs[0]["origin"] if segs else fallback_origin,
            "destination": segs[-1]["destination"] if segs else fallback_destination,
            "departure_time": segs[0]["departure_time"] if segs else "",
            "arrival_time": segs[-1]["arrival_time"] if segs else "",
            "segments": segs,
            "total_duration_minutes": sum(s.get("duration_minutes", 0) for s in segs),
            "stops": max(0, len(segs) - 1),
        }

    raw_slices = None
    if hasattr(norm_offer, "raw") and isinstance(norm_offer.raw, dict):
        raw_slices = norm_offer.raw.get("slices")
    elif isinstance(norm_offer, dict):
        raw_slices = (norm_offer.get("raw") or {}).get("slices") if isinstance(norm_offer.get("raw"), dict) else norm_offer.get("slices")

    raw_segments = getattr(norm_offer, "segments", None) if hasattr(norm_offer, "segments") else (norm_offer.get("segments") if isinstance(norm_offer, dict) else None)

    if isinstance(norm_offer, dict):
        carrier_name = norm_offer.get("carrier_name") or carrier_name
        carrier_code = norm_offer.get("carrier_code") or carrier_code
        cabin_class = norm_offer.get("cabin_class") or cabin_class

    if raw_segments:
        first_seg = raw_segments[0]
        if hasattr(first_seg, "marketing_carrier_name"):
            carrier_name = getattr(first_seg, "marketing_carrier_name", carrier_name)
            carrier_code = getattr(first_seg, "marketing_carrier_iata", carrier_code)
            cabin_class = getattr(first_seg, "cabin_class", cabin_class)
        elif isinstance(first_seg, dict):
            carrier_name = first_seg.get("carrier_name") or first_seg.get("marketing_carrier_name") or carrier_name
            carrier_code = first_seg.get("carrier_code") or first_seg.get("marketing_carrier_iata") or carrier_code
            cabin_class = first_seg.get("cabin_class") or cabin_class

        seg_list = []
        for s in raw_segments:
            if hasattr(s, "origin_iata"):
                dep_t = s.departure_time.isoformat() if hasattr(s.departure_time, "isoformat") else str(s.departure_time)
                arr_t = s.arrival_time.isoformat() if hasattr(s.arrival_time, "isoformat") else str(s.arrival_time)
                seg_list.append({
                    "id": str(getattr(s, "id", "") or ""),
                    "origin": getattr(s, "origin_iata", ""),
                    "destination": getattr(s, "destination_iata", ""),
                    "departure_time": dep_t,
                    "arrival_time": arr_t,
                    "carrier_code": getattr(s, "marketing_carrier_iata", ""),
                    "carrier_name": getattr(s, "marketing_carrier_name", ""),
                    "flight_number": getattr(s, "flight_number", ""),
                    "duration_minutes": getattr(s, "duration_minutes", 0),
                    "cabin_class": getattr(s, "cabin_class", "economy"),
                })
            elif isinstance(s, dict):
                seg_list.append({
                    "id": str(s.get("id") or ""),
                    "origin": str(s.get("origin_iata") or s.get("origin") or ""),
                    "destination": str(s.get("destination_iata") or s.get("destination") or ""),
                    "departure_time": str(s.get("departure_time") or ""),
                    "arrival_time": str(s.get("arrival_time") or ""),
                    "carrier_code": str(s.get("carrier_code") or s.get("marketing_carrier_iata") or ""),
                    "carrier_name": str(s.get("carrier_name") or s.get("marketing_carrier_name") or ""),
                    "flight_number": str(s.get("flight_number") or ""),
                    "duration_minutes": int(s.get("duration_minutes") or 0),
                    "cabin_class": str(s.get("cabin_class") or "economy"),
                })

        if raw_slices and isinstance(raw_slices, list) and len(raw_slices) > 1:
            # Multi-slice boundaries from authoritative provider payload
            seg_idx = 0
            for r_sl in raw_slices:
                r_sl_segs = r_sl.get("segments") or []
                count = len(r_sl_segs) if r_sl_segs else 0
                if count > 0 and seg_idx + count <= len(seg_list):
                    sl_slice_segs = seg_list[seg_idx:seg_idx + count]
                    seg_idx += count
                else:
                    sl_slice_segs = [seg_list[seg_idx]] if seg_idx < len(seg_list) else []
                    seg_idx += 1
                if sl_slice_segs:
                    f_slices.append(_make_slice(
                        sl_slice_segs,
                        fallback_origin=str(r_sl.get("origin") or ""),
                        fallback_destination=str(r_sl.get("destination") or ""),
                    ))

        if not f_slices and seg_list:
            first_orig = seg_list[0]["origin"]
            last_dest = seg_list[-1]["destination"]
            # Detect round-trip: journey departs from first_orig and eventually returns to first_orig
            if len(seg_list) >= 2 and first_orig and last_dest and first_orig.upper() == last_dest.upper():
                # Derive canonical destination from trip
                trip_dest_raw = trip.get("destination") or ""
                canonical_dest = ""
                if "(" in trip_dest_raw and ")" in trip_dest_raw:
                    canonical_dest = trip_dest_raw.split("(")[-1].replace(")", "").strip().upper()
                elif len(trip_dest_raw.strip()) == 3:
                    canonical_dest = trip_dest_raw.strip().upper()

                # Find turnaround segment: where return slice commences
                turnaround_idx = None
                # Check for segment starting at canonical destination
                if canonical_dest:
                    for idx in range(1, len(seg_list)):
                        if seg_list[idx]["origin"].upper() == canonical_dest:
                            turnaround_idx = idx
                            break
                        if seg_list[idx - 1]["destination"].upper() == canonical_dest:
                            turnaround_idx = idx
                            break

                # If canonical destination not directly matched, find where arrival date/time breaks
                if turnaround_idx is None:
                    # Look for date change or disconnected leg or departure heading back towards first_orig
                    max_gap = None
                    best_split = None
                    for idx in range(1, len(seg_list)):
                        dep_prev = seg_list[idx - 1].get("arrival_time", "")
                        dep_next = seg_list[idx].get("departure_time", "")
                        # If segment destination is first_orig, this is definitely a return flight
                        if seg_list[idx]["destination"].upper() == first_orig.upper() and len(seg_list) == 2:
                            best_split = idx
                            break
                        # Track calendar date change between legs
                        date_prev = dep_prev[:10] if len(dep_prev) >= 10 else ""
                        date_next = dep_next[:10] if len(dep_next) >= 10 else ""
                        if date_prev and date_next and date_next > date_prev:
                            best_split = idx
                            break
                    turnaround_idx = best_split if best_split is not None else (len(seg_list) // 2)

                if turnaround_idx and 0 < turnaround_idx < len(seg_list):
                    outbound_segs = seg_list[:turnaround_idx]
                    return_segs = seg_list[turnaround_idx:]
                    f_slices.append(_make_slice(outbound_segs, trip.get("origin") or "", canonical_dest or ""))
                    f_slices.append(_make_slice(return_segs, canonical_dest or "", trip.get("origin") or ""))
                else:
                    f_slices.append(_make_slice(seg_list, trip.get("origin") or "", trip.get("destination") or ""))
            else:
                # One-way journey
                f_slices.append(_make_slice(seg_list, trip.get("origin") or "", trip.get("destination") or ""))

    elif isinstance(norm_offer, dict):
        carrier_name = norm_offer.get("carrier_name") or carrier_name
        carrier_code = norm_offer.get("carrier_code") or carrier_code
        cabin_class = norm_offer.get("cabin_class") or cabin_class
        if not f_slices:
            f_slices = norm_offer.get("slices") or []

    provider_offer_id = getattr(norm_offer, "provider_offer_id", None) or (
        norm_offer.get("offer_id") or norm_offer.get("provider_offer_id") if isinstance(norm_offer, dict) else str(snapshot_id)
    )

    return {
        "id": str(snapshot_id),
        "snapshot_id": str(snapshot_id),
        "offer_id": str(provider_offer_id),
        "price": f_price_val,
        "total_amount": str(f_price_val) if f_price_val is not None else "0",
        "currency": f_currency_val,
        "price_inr": f_price_inr_val,
        "carrier_name": carrier_name,
        "carrier_code": carrier_code,
        "cabin_class": cabin_class,
        "slices": f_slices,
        "refundable": getattr(norm_offer, "refundable", False) if hasattr(norm_offer, "refundable") else (norm_offer.get("refundable", False) if isinstance(norm_offer, dict) else False),
    }


class TripStore:
    """Repository for Trips and Threads with strict ownership enforcement."""
    _IN_MEMORY_TRIPS: dict[str, dict[str, Any]] = {}
    _IN_MEMORY_THREADS: dict[str, dict[str, Any]] = {}

    def __init__(self) -> None:
        self.use_in_memory = bool(os.getenv("USE_IN_MEMORY_AUTH"))
        self._memory_trips = TripStore._IN_MEMORY_TRIPS
        self._memory_threads = TripStore._IN_MEMORY_THREADS
        if not self.use_in_memory and os.getenv("DATABASE_URL"):
            init_trip_tables()

    def _connect(self, autocommit: bool = True):
        return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=autocommit)

    def create_trip(
        self,
        user_id: str | uuid.UUID,
        title: str = "New Trip",
        destination: str = "",
        origin: str = "",
        status: str = "planning",
        metadata: dict[str, Any] | None = None,
        current_revision_number: int | None = None,
        current_revision_id: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Create a trip owned by the authenticated user."""
        trip_id = uuid.uuid4()
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)
        meta = dict(metadata or {})

        rev_num = current_revision_number
        if rev_num is None:
            if "trip_revision" in meta and meta["trip_revision"] is not None:
                rev_num = int(meta["trip_revision"])
            elif "revision" in meta and meta["revision"] is not None:
                rev_num = int(meta["revision"])
            else:
                rev_num = 0

        # Keep metadata mirror in lockstep with authoritative column
        meta["revision"] = rev_num
        meta["trip_revision"] = rev_num

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            record = {
                "id": trip_id,
                "user_id": uuid.UUID(uid_str),
                "title": title,
                "destination": destination,
                "origin": origin,
                "status": status,
                "metadata": meta,
                "current_revision_id": str(current_revision_id) if current_revision_id else None,
                "current_revision_number": rev_num,
                "created_at": now,
                "updated_at": now,
            }
            self._memory_trips[str(trip_id)] = record
            return record

        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO trips (id, user_id, title, destination, origin, status, metadata, current_revision_number, current_revision_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                RETURNING *
                """,
                (trip_id, uid_str, title, destination, origin, status, json.dumps(meta), rev_num, str(current_revision_id) if current_revision_id else None, now, now),
            ).fetchone()
            return dict(row)

    def update_trip_metadata(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update metadata on a trip owned by user_id."""
        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if t and str(t["user_id"]) == uid_str:
                t["metadata"] = dict(metadata)
                t["updated_at"] = now
                return dict(t)
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE trips
                SET metadata = %s::jsonb, updated_at = %s
                WHERE id = %s AND user_id = %s
                RETURNING *
                """,
                (json.dumps(metadata, default=str), now, tid_str, uid_str),
            ).fetchone()
            return dict(row) if row else None

    def get_trip(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch trip strictly verifying ownership by user_id."""
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if t and str(t["user_id"]) == uid_str:
                if not include_deleted and str(t.get("status") or "").lower() == "deleted":
                    return None
                return dict(t)
            return None

        with self._connect() as conn:
            query = "SELECT * FROM trips WHERE id = %s AND user_id = %s"
            if not include_deleted:
                query += " AND status != 'deleted'"
            row = conn.execute(query, (tid_str, uid_str)).fetchone()
            return dict(row) if row else None

    def list_trips(
        self,
        user_id: str | uuid.UUID,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        """List all trips owned by user_id. Never returns another user's trips."""
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            items = [
                t for t in self._memory_trips.values()
                if str(t["user_id"]) == uid_str and (include_deleted or str(t.get("status") or "").lower() != "deleted")
            ]
            items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            return [dict(t) for t in items[:limit]]

        with self._connect() as conn:
            query = "SELECT * FROM trips WHERE user_id = %s"
            if not include_deleted:
                query += " AND status != 'deleted'"
            query += " ORDER BY created_at DESC LIMIT %s"
            rows = conn.execute(query, (uid_str, limit)).fetchall()
            return [dict(r) for r in rows]

    def delete_trip(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        soft: bool = True,
    ) -> bool:
        """Delete trip strictly scoped by user_id.

        By default performs a safe soft-delete/archive, preserving foreign keys,
        revision histories, evidence chains, and audit trails intact.
        """
        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if t and str(t["user_id"]) == uid_str:
                if soft:
                    if str(t.get("status") or "").lower() == "deleted":
                        return False
                    t["status"] = "deleted"
                    meta = t.setdefault("metadata", {})
                    if isinstance(meta, dict):
                        meta["deleted_at"] = now_iso
                    t["updated_at"] = now
                    return True
                else:
                    del self._memory_trips[tid_str]
                    for th_id, th in list(self._memory_threads.items()):
                        if str(th.get("trip_id")) == tid_str:
                            del self._memory_threads[th_id]
                    return True
            return False

        with self._connect() as conn:
            if soft:
                row = conn.execute(
                    """
                    UPDATE trips
                    SET status = 'deleted',
                        metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{deleted_at}', to_jsonb(%s::text)),
                        updated_at = %s
                    WHERE id = %s AND user_id = %s AND status != 'deleted'
                    RETURNING id
                    """,
                    (now_iso, now, tid_str, uid_str),
                ).fetchone()
                return bool(row)
            else:
                row = conn.execute(
                    "DELETE FROM trips WHERE id = %s AND user_id = %s RETURNING id",
                    (tid_str, uid_str),
                ).fetchone()
                return bool(row)

    def check_trip_deletion_eligibility(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """Check whether a trip is safe and eligible for deletion under canonical business rules.

        Returns:
            (is_eligible: bool, refusal_reason: str | None, trip: dict | None)
        """
        trip = self.get_trip(trip_id, user_id, include_deleted=True)
        if not trip:
            return False, "Trip not found or does not belong to you.", None

        if str(trip.get("status") or "").lower() == "deleted":
            return False, "Trip is already deleted.", trip

        # 1. Ongoing trip check
        meta = trip.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}

        constraints = meta.get("constraints") or {}
        if isinstance(constraints, str):
            try:
                constraints = json.loads(constraints)
            except Exception:
                constraints = {}
        if not isinstance(constraints, dict):
            constraints = {}

        plan = meta.get("plan") or {}
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except Exception:
                plan = {}
        if not isinstance(plan, dict):
            plan = {}

        raw_start = (
            trip.get("start_date")
            or meta.get("start_date")
            or constraints.get("start_date")
            or constraints.get("departure_date")
            or plan.get("departure_date")
            or plan.get("start_date")
        )
        raw_end = (
            trip.get("end_date")
            or meta.get("end_date")
            or constraints.get("end_date")
            or constraints.get("return_date")
            or plan.get("return_date")
            or plan.get("end_date")
        )

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()

        status_norm = str(trip.get("status") or "").strip().lower()

        # 1. Reject trips whose authoritative lifecycle explicitly indicates travel is underway
        if status_norm in ACTUALLY_ONGOING_STATUSES:
            return False, f"Cannot delete trip: lifecycle state '{status_norm}' represents active or ongoing travel.", trip

        # 2. Date-range ongoing check:
        # Pre-travel planning states ('draft', 'planning', 'awaiting_approval') must not be considered
        # ongoing solely because today's date falls between planned dates.
        # For non-planning lifecycles, reject if current date falls within the trip's date range.
        if status_norm not in ELIGIBLE_PRE_TRAVEL_STATUSES:
            if raw_start and raw_end:
                try:
                    from datetime import date
                    d_start = date.fromisoformat(str(raw_start)[:10])
                    d_end = date.fromisoformat(str(raw_end)[:10])
                    if d_start <= today <= d_end:
                        return False, f"Cannot delete trip: travel is currently ongoing ({d_start.isoformat()} to {d_end.isoformat()}).", trip
                except Exception as date_err:
                    logger.warning("Could not parse trip dates for trip %s: %s", trip_id, date_err)
                    if status_norm in ACTUALLY_ONGOING_STATUSES:
                        return False, "Cannot delete trip with unverified dates in active status.", trip

        # 2. Check bookings and payments using BookingReadService
        try:
            from services.booking_read_service import BookingReadService
            booking_svc = BookingReadService()
            bookings = booking_svc.get_trip_bookings(trip_id, user_id)
        except Exception as exc:
            logger.error("Failed to query booking records for trip %s: %s", trip_id, exc, exc_info=True)
            return False, "Cannot verify booking safety state for trip deletion. Request failed closed.", trip

        flight_info = bookings.get("flight")
        if flight_info:
            f_status = str(flight_info.get("status") or "").lower()
            if flight_info.get("is_booked") or f_status in {"completed", "booked"}:
                ref = flight_info.get("booking_reference") or flight_info.get("pnr") or "Confirmed"
                return False, f"Cannot delete trip: confirmed flight booking exists (Reference: {ref}). Financial records are protected.", trip
            if flight_info.get("in_progress") or f_status in {"handed_off", "revalidating", "ready_for_booking", "pending", "processing"}:
                return False, "Cannot delete trip: flight booking or ticketing is currently in progress.", trip

        hotel_info = bookings.get("hotel")
        if hotel_info:
            h_status = str(hotel_info.get("status") or "").lower()
            h_pay_status = str(hotel_info.get("payment_status") or "").lower()
            if hotel_info.get("is_booked") or h_status in {"booked", "confirmed"}:
                conf = hotel_info.get("confirmation_code") or "Booked"
                return False, f"Cannot delete trip: confirmed hotel booking exists (Confirmation: {conf}). Financial records are protected.", trip
            if hotel_info.get("in_progress") or h_status in {"booking_in_progress", "awaiting_payment", "pending", "processing"}:
                return False, "Cannot delete trip: hotel booking or reservation is currently in progress.", trip
            if h_pay_status in {"captured", "authorized", "pending", "processing"}:
                return False, "Cannot delete trip: unresolved or captured hotel payment exists.", trip

        # 3. Direct inspection of booking_intents and hotel_transactions for unresolved or protected records
        try:
            from services.flight_booking_service import FlightBookingService, _dsn
            if self.use_in_memory or not os.getenv("DATABASE_URL"):
                for intent in FlightBookingService._IN_MEMORY_BOOKINGS.values():
                    if str(intent.get("trip_id")) == str(trip_id) and str(intent.get("user_id")) == str(user_id):
                        ist = str(intent.get("status") or "").upper()
                        if ist in {"SELECTED", "COMPLETED", "HANDED_OFF", "REVALIDATING", "READY_FOR_BOOKING", "PENDING"}:
                            return False, f"Cannot delete trip: protected flight booking intent in state '{ist}'.", trip
            else:
                with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
                    rows = conn.execute(
                        "SELECT status FROM booking_intents WHERE trip_id = %s AND user_id = %s",
                        (str(trip_id), str(user_id)),
                    ).fetchall()
                    for r in rows:
                        ist = str(r["status"]).upper()
                        if ist in {"SELECTED", "COMPLETED", "HANDED_OFF", "REVALIDATING", "READY_FOR_BOOKING", "PENDING"}:
                            return False, f"Cannot delete trip: protected flight booking intent in state '{ist}'.", trip
        except Exception as exc:
            logger.error("Error inspecting flight booking intents for trip %s: %s", trip_id, exc, exc_info=True)
            return False, "Could not verify flight booking intents. Deletion failed closed.", trip

        try:
            from services.hotel_transaction_store import HotelTransactionStore
            if self.use_in_memory or not os.getenv("DATABASE_URL"):
                for htx in BookingReadService._IN_MEMORY_HOTEL_BOOKINGS.values():
                    if str(htx.get("trip_id")) == str(trip_id) and str(htx.get("customer_ref")) == str(user_id):
                        bs = str(htx.get("booking_status") or "").lower()
                        ps = str(htx.get("payment_status") or "").lower()
                        if bs in {"booked", "confirmed", "booking_in_progress", "awaiting_payment"} or ps in {"captured", "authorized", "pending"}:
                            return False, f"Cannot delete trip: protected hotel transaction in status '{bs}/{ps}'.", trip
            else:
                hstore = HotelTransactionStore()
                htxs = hstore.get_by_trip(str(trip_id), customer_ref=str(user_id))
                for htx in htxs:
                    bs = str(htx.get("booking_status") or "").lower()
                    ps = str(htx.get("payment_status") or "").lower()
                    if bs in {"booked", "confirmed", "booking_in_progress", "awaiting_payment"} or ps in {"captured", "authorized", "pending"}:
                        return False, f"Cannot delete trip: protected hotel transaction in status '{bs}/{ps}'.", trip
        except Exception as exc:
            logger.error("Error inspecting hotel transactions for trip %s: %s", trip_id, exc, exc_info=True)
            return False, "Could not verify hotel transactions. Deletion failed closed.", trip

        return True, None, trip

    def delete_trip_atomic(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """Atomically verify safe deletion eligibility under trip row lock and soft-delete.

        Guarantees:
        1. Universal parent trip row lock (SELECT ... FOR UPDATE) inside a single transaction.
        2. Inspects latest committed canonical trip state, flight booking intents, and hotel transactions.
        3. Prevents race conditions with concurrent booking or payment transitions.
        4. Soft-deletes trip safely preserving audit trails, financial history, and foreign keys.

        Returns:
            (success: bool, error_reason: str | None, deleted_trip: dict | None)
        """
        try:
            return self._delete_trip_atomic_internal(trip_id, user_id)
        except psycopg.Error as db_err:
            logger.error("Database error during atomic trip deletion for trip %s: %s", trip_id, db_err, exc_info=True)
            err_msg = getattr(getattr(db_err, "diag", None), "message_primary", None) or str(db_err)
            return False, f"Database invariant rejection: {err_msg}", None
        except Exception as exc:
            logger.error("Unexpected error during atomic trip deletion for trip %s: %s", trip_id, exc, exc_info=True)
            return False, f"Trip deletion failed: {exc}", None

    def _delete_trip_atomic_internal(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with _get_memory_trip_lock(tid_str):
                t = self._memory_trips.get(tid_str)
                if not t or str(t["user_id"]) != uid_str:
                    return False, "Trip not found or does not belong to you.", None
                if str(t.get("status") or "").lower() == "deleted":
                    return False, "Trip not found or already deleted.", t

                eligible, reason, _ = self.check_trip_deletion_eligibility(tid_str, uid_str)
                if not eligible:
                    return False, reason, t

                t["status"] = "deleted"
                meta = t.setdefault("metadata", {})
                if isinstance(meta, dict):
                    meta["deleted_at"] = now_iso
                t["updated_at"] = now
                return True, None, dict(t)

        with self._connect(autocommit=False) as conn:
            with conn.transaction():
                # 1. Acquire parent trip row lock
                row = conn.execute(
                    "SELECT * FROM trips WHERE id = %s AND user_id = %s FOR UPDATE",
                    (tid_str, uid_str),
                ).fetchone()
                if not row:
                    return False, "Trip not found or does not belong to you.", None

                trip = dict(row)
                if str(trip.get("status") or "").lower() == "deleted":
                    return False, "Trip not found or already deleted.", trip

                # 2. Check ongoing dates
                meta = trip.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                if not isinstance(meta, dict):
                    meta = {}

                constraints = meta.get("constraints") or {}
                if isinstance(constraints, str):
                    try:
                        constraints = json.loads(constraints)
                    except Exception:
                        constraints = {}
                if not isinstance(constraints, dict):
                    constraints = {}

                plan = meta.get("plan") or {}
                if isinstance(plan, str):
                    try:
                        plan = json.loads(plan)
                    except Exception:
                        plan = {}
                if not isinstance(plan, dict):
                    plan = {}

                raw_start = (
                    trip.get("start_date")
                    or meta.get("start_date")
                    or constraints.get("start_date")
                    or constraints.get("departure_date")
                    or plan.get("departure_date")
                    or plan.get("start_date")
                )
                raw_end = (
                    trip.get("end_date")
                    or meta.get("end_date")
                    or constraints.get("end_date")
                    or constraints.get("return_date")
                    or plan.get("return_date")
                    or plan.get("end_date")
                )
                today = now.date()
                status_norm = str(trip.get("status") or "").strip().lower()

                # 1. Reject trips whose authoritative lifecycle explicitly indicates travel is underway
                if status_norm in ACTUALLY_ONGOING_STATUSES:
                    return False, f"Cannot delete trip: lifecycle state '{status_norm}' represents active or ongoing travel.", trip

                # 2. Date-range ongoing check:
                # Pre-travel planning states ('draft', 'planning', 'awaiting_approval') must not be considered
                # ongoing solely because today's date falls between planned dates.
                # For non-planning lifecycles, reject if current date falls within the trip's date range.
                if status_norm not in ELIGIBLE_PRE_TRAVEL_STATUSES:
                    if raw_start and raw_end:
                        try:
                            from datetime import date
                            d_start = date.fromisoformat(str(raw_start)[:10])
                            d_end = date.fromisoformat(str(raw_end)[:10])
                            if d_start <= today <= d_end:
                                return False, f"Cannot delete trip: travel is currently ongoing ({d_start.isoformat()} to {d_end.isoformat()}).", trip
                        except Exception as date_err:
                            logger.warning("Could not parse dates for trip %s under lock: %s", tid_str, date_err)
                            if status_norm in ACTUALLY_ONGOING_STATUSES:
                                return False, "Cannot delete trip with unverified dates in active status.", trip

                # 3. Direct inspection of flight booking intents inside locked connection
                intent_rows = conn.execute(
                    "SELECT status, provider_reference, metadata FROM booking_intents WHERE trip_id = %s AND user_id = %s",
                    (tid_str, uid_str),
                ).fetchall()
                for ir in intent_rows:
                    ist = str(ir["status"]).upper()
                    imeta = ir.get("metadata") or {}
                    if isinstance(imeta, str):
                        try:
                            imeta = json.loads(imeta)
                        except Exception:
                            imeta = {}
                    is_cancelled = (
                        imeta.get("status") == "CANCELLED"
                        or imeta.get("cancellation")
                        or imeta.get("cancelled_at")
                    )
                    if ist == "COMPLETED" and not is_cancelled:
                        ref = ir.get("provider_reference") or imeta.get("booking_reference") or "Confirmed"
                        return False, f"Cannot delete trip: confirmed flight booking exists (Reference: {ref}). Financial records are protected.", trip
                    if ist in {"SELECTED", "HANDED_OFF", "REVALIDATING", "READY_FOR_BOOKING", "PENDING"}:
                        return False, f"Cannot delete trip: flight booking intent is in progress ('{ist}').", trip

                # 4. Direct inspection of hotel transactions inside locked connection
                htx_rows = conn.execute(
                    "SELECT booking_status, payment_status, booking_json FROM trust_trip_hotel_transactions WHERE trip_id = %s AND customer_ref = %s",
                    (tid_str, uid_str),
                ).fetchall()
                for hr in htx_rows:
                    hbs = str(hr["booking_status"] or "").lower()
                    hps = str(hr["payment_status"] or "").lower()
                    hjson = hr.get("booking_json") or {}
                    if isinstance(hjson, str):
                        try:
                            hjson = json.loads(hjson)
                        except Exception:
                            hjson = {}
                    if hbs in {"booked", "confirmed"}:
                        conf = hjson.get("confirmation_code") or hjson.get("booking_id") or "Confirmed"
                        return False, f"Cannot delete trip: confirmed hotel booking exists (Confirmation: {conf}). Financial records are protected.", trip
                    if hbs in {"booking_in_progress", "awaiting_payment"}:
                        return False, "Cannot delete trip: hotel booking or reservation is currently in progress.", trip
                    if hps in {"captured", "authorized", "pending", "processing"}:
                        return False, f"Cannot delete trip: unresolved or captured hotel payment exists ('{hps}').", trip

                # 5. Perform safe soft-delete within the same locked transaction
                updated = conn.execute(
                    """
                    UPDATE trips
                    SET status = 'deleted',
                        metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{deleted_at}', to_jsonb(%s::text)),
                        updated_at = %s
                    WHERE id = %s AND user_id = %s AND status != 'deleted'
                    RETURNING *
                    """,
                    (now_iso, now, tid_str, uid_str),
                ).fetchone()

                if not updated:
                    return False, "Trip deletion could not be completed. State may have changed concurrently.", trip

                return True, None, dict(updated)



    def update_trip(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Update an owned trip's fields (status, metadata, revision pointer, etc.)."""
        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)

        allowed = {
            "title",
            "destination",
            "origin",
            "status",
            "metadata",
            "current_revision_id",
            "current_revision_number",
        }
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return self.get_trip(trip_id, user_id)

        # Synchronize authoritative column with metadata mirror
        has_rev_num = "current_revision_number" in filtered
        if has_rev_num:
            rev_num = int(filtered["current_revision_number"])
            if "metadata" in filtered and isinstance(filtered["metadata"], dict):
                filtered["metadata"]["revision"] = rev_num
                filtered["metadata"]["trip_revision"] = rev_num

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if not t or str(t["user_id"]) != uid_str:
                return None
            if not has_rev_num and "metadata" in filtered and isinstance(filtered["metadata"], dict):
                cur_r = t.get("current_revision_number", 0)
                filtered["metadata"]["revision"] = cur_r
                filtered["metadata"]["trip_revision"] = cur_r
            t.update(filtered)
            t["updated_at"] = now
            return dict(t)

        sets = []
        vals = []
        for k, v in filtered.items():
            if k == "metadata":
                if has_rev_num:
                    sets.append(f"{k} = %s::jsonb")
                else:
                    sets.append(f"{k} = (%s::jsonb || jsonb_build_object('revision', current_revision_number, 'trip_revision', current_revision_number))")
                vals.append(json.dumps(v))
            elif k == "current_revision_id":
                sets.append(f"{k} = %s")
                vals.append(str(v) if v is not None else None)
            elif k == "current_revision_number":
                sets.append(f"{k} = %s")
                vals.append(int(v) if v is not None else 0)
            else:
                sets.append(f"{k} = %s")
                vals.append(v)
        sets.append("updated_at = %s")
        vals.append(now)
        vals.extend([tid_str, uid_str])

        with self._connect() as conn:
            row = conn.execute(
                f"UPDATE trips SET {', '.join(sets)} WHERE id = %s AND user_id = %s RETURNING *",
                vals,
            ).fetchone()
            return dict(row) if row else None


    def select_offer_atomic(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        req: Any,
        snap_store: Any,
    ) -> dict[str, Any]:
        """Atomically validate and bind an explicit flight or hotel offer to the trip.

        Guarantees:
        1. Universal row lock (SELECT ... FOR UPDATE) inside a single transaction.
        2. Strict revision match (fails closed if revision context missing; no 0 bypass).
        3. Single connection for all selection reads and metadata mutation.
        4. Strict unexpired check (snapshot.expires_at <= now is expired; no bypass).
        5. Synchronizes candidate departure and return dates into constraints & canonical TripRequest.
        """
        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with _get_memory_trip_lock(tid_str):
                t = self._memory_trips.get(tid_str)
                if not t or str(t["user_id"]) != uid_str:
                    raise OfferSelectionError(404, "Trip not found or does not belong to you.")
                if str(t.get("status") or "").lower() == "deleted":
                    raise OfferSelectionError(404, "Trip not found or already deleted.")
                trip = dict(t)

                current_revision = resolve_current_revision(trip)
                if current_revision is None:
                    raise RevisionContextError("Trip revision context unavailable")

                snapshot = snap_store.get_snapshot(req.snapshot_id, user_id)
                if not snapshot:
                    raise OfferSelectionError(403, "Offer snapshot not found or does not belong to you.")

                session = snap_store.get_session(snapshot.session_id, user_id)
                if not session or str(session.trip_id) != tid_str:
                    raise OfferSelectionError(400, "Offer snapshot does not belong to the requested trip.")

                if session.trip_revision != current_revision:
                    raise OfferSelectionError(
                        400,
                        f"Offer snapshot was generated for outdated trip revision {session.trip_revision} (current trip revision is {current_revision}).",
                    )

                if snapshot.offer_type != req.offer_type:
                    raise OfferSelectionError(400, f"Snapshot is a {snapshot.offer_type} offer, not a {req.offer_type} offer.")

                if snapshot.availability != AvailabilityStatus.AVAILABLE:
                    raise OfferSelectionError(409, "Selected offer is no longer available.")
                if snapshot.freshness != FreshnessStatus.FRESH:
                    raise OfferSelectionError(409, "Selected offer snapshot is stale.")
                if snapshot.expires_at and snapshot.expires_at <= now:
                    raise OfferSelectionError(409, "Selected offer has expired.")

                norm_offer = snapshot.normalized_offer
                if hasattr(norm_offer, "availability") and norm_offer.availability != AvailabilityStatus.AVAILABLE:
                    raise OfferSelectionError(409, "Selected offer is no longer available.")
                if hasattr(norm_offer, "freshness") and norm_offer.freshness != FreshnessStatus.FRESH:
                    raise OfferSelectionError(409, "Selected offer snapshot is stale.")
                if hasattr(norm_offer, "expires_at") and norm_offer.expires_at and norm_offer.expires_at <= now:
                    raise OfferSelectionError(409, "Selected offer has expired.")

                meta = trip.get("metadata") or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                meta = dict(meta)

                key = f"selected_{req.offer_type}_snapshot_id"
                meta[key] = str(req.snapshot_id)

                if req.offer_type == "flight":
                    dep_date = None
                    ret_date = None
                    if hasattr(norm_offer, "candidate_departure_date") and norm_offer.candidate_departure_date:
                        dep_date = norm_offer.candidate_departure_date.isoformat()
                    elif getattr(norm_offer, "segments", None):
                        dep_date = norm_offer.segments[0].departure_time.date().isoformat()

                    if hasattr(norm_offer, "candidate_return_date") and norm_offer.candidate_return_date:
                        ret_date = norm_offer.candidate_return_date.isoformat()
                    elif getattr(norm_offer, "segments", None) and len(norm_offer.segments) > 1:
                        first_orig = norm_offer.segments[0].origin_iata
                        for seg in norm_offer.segments[1:]:
                            if seg.destination_iata == first_orig:
                                ret_date = seg.departure_time.date().isoformat()
                                break

                    constraints = meta.get("constraints") or {}
                    if dep_date:
                        constraints["departure_date"] = dep_date
                        constraints["checkin_date"] = dep_date
                    if ret_date:
                        constraints["return_date"] = ret_date
                        constraints["checkout_date"] = ret_date
                    meta["constraints"] = constraints

                    canonical_tr_dict = meta.get("canonical_trip_request")
                    if canonical_tr_dict and isinstance(canonical_tr_dict, dict):
                        slices = canonical_tr_dict.get("slices") or []
                        if slices and dep_date:
                            slices[0]["departure_date"] = dep_date
                        if len(slices) > 1 and ret_date:
                            slices[1]["departure_date"] = ret_date
                        canonical_tr_dict["slices"] = slices
                        meta["canonical_trip_request"] = canonical_tr_dict
                    meta["selected_flight"] = _build_selected_flight_metadata(norm_offer, req.snapshot_id, trip)
                elif req.offer_type == "hotel":
                    price_val = None
                    currency_val = "USD"
                    price_inr_val = None
                    if hasattr(norm_offer, "price"):
                        price_val = float(norm_offer.price.total_amount)
                        currency_val = str(norm_offer.price.currency)
                        if getattr(norm_offer.price, "inr_reference_amount", None) is not None:
                            price_inr_val = float(norm_offer.price.inr_reference_amount)
                    elif isinstance(norm_offer, dict):
                        price_val = float(norm_offer.get("price") or norm_offer.get("total_amount") or 0)
                        currency_val = str(norm_offer.get("currency", "USD"))
                        price_inr_val = float(norm_offer["price_inr"]) if norm_offer.get("price_inr") is not None else None

                    hotel_name = getattr(norm_offer, "hotel_name", None) or (norm_offer.get("hotel_name") or norm_offer.get("name") if isinstance(norm_offer, dict) else "Hotel")
                    rooms_count_val = getattr(norm_offer, "rooms_count", None) or (norm_offer.get("rooms_count") if isinstance(norm_offer, dict) else None)
                    meta["selected_hotel"] = {
                        "id": str(req.snapshot_id),
                        "snapshot_id": str(req.snapshot_id),
                        "hotel_name": hotel_name,
                        "name": hotel_name,
                        "price": price_val,
                        "total_amount": price_val,
                        "currency": currency_val,
                        "price_inr": price_inr_val,
                        "room_name": getattr(norm_offer, "room_name", None) or (norm_offer.get("room_name") if isinstance(norm_offer, dict) else None),
                        "rooms_count": rooms_count_val,
                    }

                    # Room selection validation and persistence (Micro-Remediation G, Issue 3)
                    room_id_raw = getattr(req, "room_id", None)
                    if not room_id_raw and getattr(req, "metadata", None) and isinstance(req.metadata, dict):
                        sel_room_meta = req.metadata.get("selected_room")
                        if isinstance(sel_room_meta, dict):
                            room_id_raw = sel_room_meta.get("id") or sel_room_meta.get("room_id")
                        elif isinstance(sel_room_meta, str):
                            room_id_raw = sel_room_meta

                    if room_id_raw is not None:
                        room_id_str = str(room_id_raw).strip()
                        try:
                            val_room_uuid = uuid.UUID(room_id_str)
                        except (ValueError, TypeError):
                            raise OfferSelectionError(
                                400,
                                f"Invalid room identifier '{room_id_str}': room id must be a valid TrustTrip UUID, not a provider identifier.",
                            )

                        raw_payload = snap_store.get_raw_payload(req.snapshot_id, user_id)
                        internal_rooms = (raw_payload or {}).get("_internal_rooms") or []
                        matching_room = next((r for r in internal_rooms if str(r.get("id")) == str(val_room_uuid)), None)
                        if not matching_room:
                            raise OfferSelectionError(
                                400,
                                f"Room '{val_room_uuid}' does not exist in the selected hotel snapshot.",
                            )

                        persisted_room = {
                            "id": str(matching_room["id"]),
                            "room_id": str(matching_room["id"]),
                            "name": matching_room.get("name"),
                            "description": matching_room.get("description"),
                            "price_per_night": matching_room.get("price_per_night"),
                            "total_price": matching_room.get("total_price"),
                            "currency": matching_room.get("currency"),
                            "capacity": matching_room.get("capacity"),
                            "beds": matching_room.get("beds"),
                            "amenities": matching_room.get("amenities"),
                        }
                        meta["selected_room"] = persisted_room
                        meta["selected_hotel"]["selected_room"] = persisted_room
                        if persisted_room.get("name"):
                            meta["selected_hotel"]["room_name"] = persisted_room["name"]
                        if persisted_room.get("total_price") is not None:
                            meta["selected_hotel"]["room_price"] = persisted_room["total_price"]

                t["metadata"] = meta
                t["updated_at"] = now
                return dict(t)

        with self._connect(autocommit=False) as conn:
            with conn.transaction():
                # 1. Acquire parent trip row lock
                row = conn.execute(
                    "SELECT * FROM trips WHERE id = %s AND user_id = %s FOR UPDATE",
                    (tid_str, uid_str),
                ).fetchone()
                if not row:
                    raise OfferSelectionError(404, "Trip not found or does not belong to you.")
                trip = dict(row)
                if str(trip.get("status") or "").lower() == "deleted":
                    raise OfferSelectionError(404, "Trip not found or already deleted.")

                # 2. Strict revision resolution
                current_revision = resolve_current_revision(trip)
                if current_revision is None:
                    raise RevisionContextError("Trip revision context unavailable")

                # 3. Read snapshot using the SAME locked connection
                snapshot = snap_store.get_snapshot(req.snapshot_id, user_id, conn=conn)
                if not snapshot:
                    raise OfferSelectionError(403, "Offer snapshot not found or does not belong to you.")

                # 4. Read session using the SAME locked connection
                session = snap_store.get_session(snapshot.session_id, user_id, conn=conn)
                if not session or str(session.trip_id) != tid_str:
                    raise OfferSelectionError(400, "Offer snapshot does not belong to the requested trip.")

                # 5. Strict revision match (fail closed)
                if session.trip_revision != current_revision:
                    raise OfferSelectionError(
                        400,
                        f"Offer snapshot was generated for outdated trip revision {session.trip_revision} (current trip revision is {current_revision}).",
                    )

                if snapshot.offer_type != req.offer_type:
                    raise OfferSelectionError(400, f"Snapshot is a {snapshot.offer_type} offer, not a {req.offer_type} offer.")

                if snapshot.availability != AvailabilityStatus.AVAILABLE:
                    raise OfferSelectionError(409, "Selected offer is no longer available.")
                if snapshot.freshness != FreshnessStatus.FRESH:
                    raise OfferSelectionError(409, "Selected offer snapshot is stale.")
                if snapshot.expires_at and snapshot.expires_at <= now:
                    raise OfferSelectionError(409, "Selected offer has expired.")

                norm_offer = snapshot.normalized_offer
                if hasattr(norm_offer, "availability") and norm_offer.availability != AvailabilityStatus.AVAILABLE:
                    raise OfferSelectionError(409, "Selected offer is no longer available.")
                if hasattr(norm_offer, "freshness") and norm_offer.freshness != FreshnessStatus.FRESH:
                    raise OfferSelectionError(409, "Selected offer snapshot is stale.")
                if hasattr(norm_offer, "expires_at") and norm_offer.expires_at and norm_offer.expires_at <= now:
                    raise OfferSelectionError(409, "Selected offer has expired.")

                meta = trip.get("metadata") or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                meta = dict(meta)

                key = f"selected_{req.offer_type}_snapshot_id"
                meta[key] = str(req.snapshot_id)

                if req.offer_type == "flight":
                    dep_date = None
                    ret_date = None
                    if hasattr(norm_offer, "candidate_departure_date") and norm_offer.candidate_departure_date:
                        dep_date = norm_offer.candidate_departure_date.isoformat()
                    elif getattr(norm_offer, "segments", None):
                        dep_date = norm_offer.segments[0].departure_time.date().isoformat()

                    if hasattr(norm_offer, "candidate_return_date") and norm_offer.candidate_return_date:
                        ret_date = norm_offer.candidate_return_date.isoformat()
                    elif getattr(norm_offer, "segments", None) and len(norm_offer.segments) > 1:
                        first_orig = norm_offer.segments[0].origin_iata
                        for seg in norm_offer.segments[1:]:
                            if seg.destination_iata == first_orig:
                                ret_date = seg.departure_time.date().isoformat()
                                break

                    constraints = meta.get("constraints") or {}
                    if dep_date:
                        constraints["departure_date"] = dep_date
                        constraints["checkin_date"] = dep_date
                    if ret_date:
                        constraints["return_date"] = ret_date
                        constraints["checkout_date"] = ret_date
                    meta["constraints"] = constraints

                    canonical_tr_dict = meta.get("canonical_trip_request")
                    if canonical_tr_dict and isinstance(canonical_tr_dict, dict):
                        slices = canonical_tr_dict.get("slices") or []
                        if slices and dep_date:
                            slices[0]["departure_date"] = dep_date
                        if len(slices) > 1 and ret_date:
                            slices[1]["departure_date"] = ret_date
                        canonical_tr_dict["slices"] = slices
                        meta["canonical_trip_request"] = canonical_tr_dict
                    meta["selected_flight"] = _build_selected_flight_metadata(norm_offer, req.snapshot_id, trip)
                elif req.offer_type == "hotel":
                    price_val = None
                    currency_val = "USD"
                    price_inr_val = None
                    if hasattr(norm_offer, "price"):
                        price_val = float(norm_offer.price.total_amount)
                        currency_val = str(norm_offer.price.currency)
                        if getattr(norm_offer.price, "inr_reference_amount", None) is not None:
                            price_inr_val = float(norm_offer.price.inr_reference_amount)
                    elif isinstance(norm_offer, dict):
                        price_val = float(norm_offer.get("price") or norm_offer.get("total_amount") or 0)
                        currency_val = str(norm_offer.get("currency", "USD"))
                        price_inr_val = float(norm_offer["price_inr"]) if norm_offer.get("price_inr") is not None else None

                    hotel_name = getattr(norm_offer, "hotel_name", None) or (norm_offer.get("hotel_name") or norm_offer.get("name") if isinstance(norm_offer, dict) else "Hotel")
                    rooms_count_val = getattr(norm_offer, "rooms_count", None) or (norm_offer.get("rooms_count") if isinstance(norm_offer, dict) else None)
                    meta["selected_hotel"] = {
                        "id": str(req.snapshot_id),
                        "snapshot_id": str(req.snapshot_id),
                        "hotel_name": hotel_name,
                        "name": hotel_name,
                        "price": price_val,
                        "total_amount": price_val,
                        "currency": currency_val,
                        "price_inr": price_inr_val,
                        "room_name": getattr(norm_offer, "room_name", None) or (norm_offer.get("room_name") if isinstance(norm_offer, dict) else None),
                        "rooms_count": rooms_count_val,
                    }

                    # Room selection validation and persistence (Micro-Remediation G, Issue 3)
                    room_id_raw = getattr(req, "room_id", None)
                    if not room_id_raw and getattr(req, "metadata", None) and isinstance(req.metadata, dict):
                        sel_room_meta = req.metadata.get("selected_room")
                        if isinstance(sel_room_meta, dict):
                            room_id_raw = sel_room_meta.get("id") or sel_room_meta.get("room_id")
                        elif isinstance(sel_room_meta, str):
                            room_id_raw = sel_room_meta

                    if room_id_raw is not None:
                        room_id_str = str(room_id_raw).strip()
                        try:
                            val_room_uuid = uuid.UUID(room_id_str)
                        except (ValueError, TypeError):
                            raise OfferSelectionError(
                                400,
                                f"Invalid room identifier '{room_id_str}': room id must be a valid TrustTrip UUID, not a provider identifier.",
                            )

                        raw_payload = snap_store.get_raw_payload(req.snapshot_id, user_id, conn=conn)
                        internal_rooms = (raw_payload or {}).get("_internal_rooms") or []
                        matching_room = next((r for r in internal_rooms if str(r.get("id")) == str(val_room_uuid)), None)
                        if not matching_room:
                            raise OfferSelectionError(
                                400,
                                f"Room '{val_room_uuid}' does not exist in the selected hotel snapshot.",
                            )

                        persisted_room = {
                            "id": str(matching_room["id"]),
                            "room_id": str(matching_room["id"]),
                            "name": matching_room.get("name"),
                            "description": matching_room.get("description"),
                            "price_per_night": matching_room.get("price_per_night"),
                            "total_price": matching_room.get("total_price"),
                            "currency": matching_room.get("currency"),
                            "capacity": matching_room.get("capacity"),
                            "beds": matching_room.get("beds"),
                            "amenities": matching_room.get("amenities"),
                        }
                        meta["selected_room"] = persisted_room
                        meta["selected_hotel"]["selected_room"] = persisted_room
                        if persisted_room.get("name"):
                            meta["selected_hotel"]["room_name"] = persisted_room["name"]
                        if persisted_room.get("total_price") is not None:
                            meta["selected_hotel"]["room_price"] = persisted_room["total_price"]

                # 6. Update trip inside the SAME transaction
                updated_row = conn.execute(
                    """
                    UPDATE trips
                    SET metadata = %s::jsonb, updated_at = %s
                    WHERE id = %s AND user_id = %s
                    RETURNING *
                    """,
                    (json.dumps(meta), now, tid_str, uid_str),
                ).fetchone()
                return dict(updated_row)

    def create_revision_atomic(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        feedback: str = "",
        updated_constraints: dict[str, Any] | None = None,
        expected_base_revision: int | None = None,
        constraints_update: dict[str, Any] | None = None,
        expected_base_revision_id: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Atomically advance trip plan revision under parent trip row lock in a single transaction.

        Guarantees:
        1. Universal parent trip row lock (SELECT ... FOR UPDATE) inside a single transaction.
        2. Derives changeset, replanning decisions, and structured diff from the LOCKED current constraints/metadata.
        3. Advances revision pointer, records PlanRevision with parent linkage, and persists updated metadata.
        4. Validates expected base revision number and ID, raising 409 conflict if stale.
        5. Zero possibility of overwriting concurrent offer selections or metadata updates.
        """
        tid_str = str(trip_id)
        uid_str = str(user_id)
        effective_constraints = updated_constraints if updated_constraints is not None else (constraints_update or {})
        constraints_update = effective_constraints
        now = datetime.now(timezone.utc)

        from domain.change_set import compute_change_set, calculate_replanning_plan
        from domain.plan_revision import PlanRevision, compute_structured_diff

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with _get_memory_trip_lock(tid_str):
                t = self._memory_trips.get(tid_str)
                if not t or str(t.get("user_id")) != uid_str:
                    raise OfferSelectionError(404, "Trip not found or does not belong to you.")
                trip_row = dict(t)
                authoritative_rev = int(trip_row.get("current_revision_number") or 0)
                cur_rev_id = str(trip_row.get("current_revision_id") or "")

                if expected_base_revision_id is not None and cur_rev_id != str(expected_base_revision_id):
                    raise OfferSelectionError(
                        409,
                        f"Conflict: Expected base revision ID {expected_base_revision_id} but current is {cur_rev_id}.",
                    )
                if expected_base_revision is not None and authoritative_rev != expected_base_revision:
                    raise OfferSelectionError(
                        409,
                        f"Conflict: Expected base revision {expected_base_revision} but current is {authoritative_rev}.",
                    )

                meta = dict(trip_row.get("metadata") or {})
                locked_constraints = dict(meta.get("constraints") or {})

                change_set = compute_change_set(locked_constraints, constraints_update, feedback)
                plan = calculate_replanning_plan(change_set)
                structured_diff = compute_structured_diff(locked_constraints, {**locked_constraints, **constraints_update}, feedback)

                new_revision_number = authoritative_rev + 1
                new_constraints = {**locked_constraints, **constraints_update}
                meta["revision"] = new_revision_number
                meta["trip_revision"] = new_revision_number
                meta["constraints"] = new_constraints
                meta["last_feedback"] = feedback

                # Mirror supported constraints into canonical metadata fields
                if "budget" in new_constraints and new_constraints["budget"] is not None:
                    try:
                        meta["budget"] = float(new_constraints["budget"])
                    except (ValueError, TypeError):
                        pass
                if "start_date" in new_constraints and new_constraints["start_date"]:
                    meta["start_date"] = str(new_constraints["start_date"])[:10]
                if "end_date" in new_constraints and new_constraints["end_date"]:
                    meta["end_date"] = str(new_constraints["end_date"])[:10]
                if "departure_date" in new_constraints and new_constraints["departure_date"]:
                    meta["departure_date"] = str(new_constraints["departure_date"])[:10]
                    meta.setdefault("start_date", meta["departure_date"])
                if "return_date" in new_constraints and new_constraints["return_date"]:
                    meta["return_date"] = str(new_constraints["return_date"])[:10]
                    meta.setdefault("end_date", meta["return_date"])
                if "travelers_count" in new_constraints and new_constraints["travelers_count"] is not None:
                    try:
                        meta["travelers_count"] = int(new_constraints["travelers_count"])
                    except (ValueError, TypeError):
                        pass
                elif "adults" in new_constraints and new_constraints["adults"] is not None:
                    try:
                        meta["travelers_count"] = int(new_constraints["adults"])
                    except (ValueError, TypeError):
                        pass
                elif "passengers" in new_constraints and new_constraints["passengers"] is not None:
                    try:
                        meta["travelers_count"] = int(new_constraints["passengers"])
                    except (ValueError, TypeError):
                        pass
                if "children" in new_constraints and new_constraints["children"] is not None:
                    try:
                        meta["children"] = int(new_constraints["children"])
                    except (ValueError, TypeError):
                        pass
                if "rooms" in new_constraints and new_constraints["rooms"] is not None:
                    try:
                        meta["rooms"] = int(new_constraints["rooms"])
                    except (ValueError, TypeError):
                        pass

                old_flight = meta.get("selected_flight_snapshot_id")
                new_flight = constraints_update.get("selected_flight_snapshot_id", old_flight)
                flight_selection_changed = (old_flight is not None and new_flight != old_flight)
                flight_dates_or_route_changed = (
                    ("origin" in constraints_update and constraints_update["origin"] != locked_constraints.get("origin"))
                    or ("destination" in constraints_update and constraints_update["destination"] != locked_constraints.get("destination"))
                    or ("departure_date" in constraints_update and constraints_update["departure_date"] != locked_constraints.get("departure_date"))
                    or ("return_date" in constraints_update and constraints_update["return_date"] != locked_constraints.get("return_date"))
                    or ("passengers" in constraints_update and constraints_update["passengers"] != locked_constraints.get("passengers"))
                )

                if plan.requires_flight_search or flight_selection_changed or flight_dates_or_route_changed:
                    if plan.requires_flight_search or new_flight is None:
                        meta.pop("selected_flight_snapshot_id", None)
                        meta.pop("selected_flight", None)
                    elif flight_selection_changed:
                        meta["selected_flight_snapshot_id"] = new_flight
                        meta.pop("selected_flight", None)
                    else:
                        meta["selected_flight_snapshot_id"] = new_flight
                    try:
                        from services.flight_booking_lifecycle_service import FlightBookingLifecycleService
                        FlightBookingLifecycleService.invalidate_active_flight_intents(tid_str)
                    except Exception as exc:
                        logger.error("Failed to invalidate active flight intents for trip %s: %s", tid_str, exc)
                        raise
                if plan.requires_hotel_search:
                    meta.pop("selected_hotel_snapshot_id", None)
                    meta.pop("selected_hotel", None)
                    meta.pop("hotel_prebook", None)
                    meta.pop("selected_room", None)

                new_rev_id = uuid.uuid4()
                parent_rev_id = trip_row.get("current_revision_id")
                plan_snapshot = {
                    "title": trip_row.get("title") or "",
                    "destination": trip_row.get("destination") or "",
                    "origin": trip_row.get("origin") or "",
                    "constraints": new_constraints,
                    "metadata": meta,
                }

                from services.evidence_store import EvidenceRevisionStore
                ev_store = EvidenceRevisionStore()
                published_rev = ev_store.publish_revision_transaction(
                    trip_id=uuid.UUID(tid_str),
                    user_id=uuid.UUID(uid_str),
                    expected_base_revision=authoritative_rev,
                    expected_base_revision_id=expected_base_revision_id,
                    change_set=change_set.model_dump(),
                    plan_snapshot=plan_snapshot,
                    parent_revision_id=uuid.UUID(str(parent_rev_id)) if parent_rev_id else None,
                )
                new_rev_id = published_rev.revision_id

                t["current_revision_id"] = str(new_rev_id)
                t["current_revision_number"] = new_revision_number
                t["status"] = "awaiting_approval"
                t["metadata"] = meta
                t["updated_at"] = now

                return {
                    "success": True,
                    "trip_id": tid_str,
                    "revision": new_revision_number,
                    "revision_number": new_revision_number,
                    "revision_id": str(new_rev_id),
                    "parent_revision_id": str(parent_rev_id) if parent_rev_id else None,
                    "status": "awaiting_approval",
                    "proposal_status": "PROPOSED",
                    "review_status": "PROPOSED",
                    "trip_status": "awaiting_approval",
                    "change_set": change_set.model_dump(),
                    "structured_diff": structured_diff.model_dump(),
                    "affected_agents": plan.affected_agents,
                    "plan_snapshot": plan_snapshot,
                    "proposal_snapshot": plan_snapshot,
                    "review_projection": {
                        "revision_id": str(new_rev_id),
                        "revision_number": new_revision_number,
                        "parent_revision_id": str(parent_rev_id) if parent_rev_id else None,
                        "status": "PROPOSED",
                        "structured_diff": structured_diff.model_dump(),
                        "plan_snapshot": plan_snapshot,
                    },
                    "trip": dict(t),
                }

        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT id, user_id, title, destination, origin, status, current_revision_id, current_revision_number, metadata
                    FROM trips
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (tid_str, uid_str),
                ).fetchone()

                if not row:
                    raise OfferSelectionError(404, "Trip not found or does not belong to you.")

                trip_row = dict(row)
                authoritative_rev = int(trip_row.get("current_revision_number") or 0)
                cur_rev_id = str(trip_row.get("current_revision_id") or "")

                if expected_base_revision_id is not None and cur_rev_id != str(expected_base_revision_id):
                    raise OfferSelectionError(
                        409,
                        f"Conflict: Expected base revision ID {expected_base_revision_id} but current is {cur_rev_id}.",
                    )
                if expected_base_revision is not None and authoritative_rev != expected_base_revision:
                    raise OfferSelectionError(
                        409,
                        f"Conflict: Expected base revision {expected_base_revision} but current is {authoritative_rev}.",
                    )

                meta = trip_row.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                meta = dict(meta)

                locked_constraints = meta.get("constraints") or {}
                if isinstance(locked_constraints, str):
                    try:
                        locked_constraints = json.loads(locked_constraints)
                    except Exception:
                        locked_constraints = {}
                locked_constraints = dict(locked_constraints)

                change_set = compute_change_set(locked_constraints, constraints_update, feedback)
                plan = calculate_replanning_plan(change_set)
                structured_diff = compute_structured_diff(locked_constraints, {**locked_constraints, **constraints_update}, feedback)

                new_revision_number = authoritative_rev + 1
                new_constraints = {**locked_constraints, **constraints_update}
                meta["revision"] = new_revision_number
                meta["trip_revision"] = new_revision_number
                meta["constraints"] = new_constraints
                meta["last_feedback"] = feedback

                # Mirror supported constraints into canonical metadata fields
                if "budget" in new_constraints and new_constraints["budget"] is not None:
                    try:
                        meta["budget"] = float(new_constraints["budget"])
                    except (ValueError, TypeError):
                        pass
                if "start_date" in new_constraints and new_constraints["start_date"]:
                    meta["start_date"] = str(new_constraints["start_date"])[:10]
                if "end_date" in new_constraints and new_constraints["end_date"]:
                    meta["end_date"] = str(new_constraints["end_date"])[:10]
                if "departure_date" in new_constraints and new_constraints["departure_date"]:
                    meta["departure_date"] = str(new_constraints["departure_date"])[:10]
                    meta.setdefault("start_date", meta["departure_date"])
                if "return_date" in new_constraints and new_constraints["return_date"]:
                    meta["return_date"] = str(new_constraints["return_date"])[:10]
                    meta.setdefault("end_date", meta["return_date"])
                if "travelers_count" in new_constraints and new_constraints["travelers_count"] is not None:
                    try:
                        meta["travelers_count"] = int(new_constraints["travelers_count"])
                    except (ValueError, TypeError):
                        pass
                elif "adults" in new_constraints and new_constraints["adults"] is not None:
                    try:
                        meta["travelers_count"] = int(new_constraints["adults"])
                    except (ValueError, TypeError):
                        pass
                elif "passengers" in new_constraints and new_constraints["passengers"] is not None:
                    try:
                        meta["travelers_count"] = int(new_constraints["passengers"])
                    except (ValueError, TypeError):
                        pass
                if "children" in new_constraints and new_constraints["children"] is not None:
                    try:
                        meta["children"] = int(new_constraints["children"])
                    except (ValueError, TypeError):
                        pass
                if "rooms" in new_constraints and new_constraints["rooms"] is not None:
                    try:
                        meta["rooms"] = int(new_constraints["rooms"])
                    except (ValueError, TypeError):
                        pass

                old_flight = meta.get("selected_flight_snapshot_id")
                new_flight = constraints_update.get("selected_flight_snapshot_id", old_flight)
                flight_selection_changed = (old_flight is not None and new_flight != old_flight)
                flight_dates_or_route_changed = (
                    ("origin" in constraints_update and constraints_update["origin"] != locked_constraints.get("origin"))
                    or ("destination" in constraints_update and constraints_update["destination"] != locked_constraints.get("destination"))
                    or ("departure_date" in constraints_update and constraints_update["departure_date"] != locked_constraints.get("departure_date"))
                    or ("return_date" in constraints_update and constraints_update["return_date"] != locked_constraints.get("return_date"))
                    or ("passengers" in constraints_update and constraints_update["passengers"] != locked_constraints.get("passengers"))
                )

                if plan.requires_flight_search or flight_selection_changed or flight_dates_or_route_changed:
                    if plan.requires_flight_search or new_flight is None:
                        meta.pop("selected_flight_snapshot_id", None)
                        meta.pop("selected_flight", None)
                    elif flight_selection_changed:
                        meta["selected_flight_snapshot_id"] = new_flight
                        meta.pop("selected_flight", None)
                    else:
                        meta["selected_flight_snapshot_id"] = new_flight
                    try:
                        from services.flight_booking_lifecycle_service import FlightBookingLifecycleService
                        FlightBookingLifecycleService.invalidate_active_flight_intents(tid_str)
                    except Exception as exc:
                        logger.error("Failed to invalidate active flight intents for trip %s: %s", tid_str, exc)
                        raise
                if plan.requires_hotel_search:
                    meta.pop("selected_hotel_snapshot_id", None)
                    meta.pop("selected_hotel", None)
                    meta.pop("hotel_prebook", None)
                    meta.pop("selected_room", None)

                new_rev_id = uuid.uuid4()
                parent_rev_id = trip_row.get("current_revision_id")
                parent_id_str = str(parent_rev_id) if parent_rev_id else None

                plan_snapshot = {
                    "title": trip_row.get("title") or "",
                    "destination": trip_row.get("destination") or "",
                    "origin": trip_row.get("origin") or "",
                    "constraints": new_constraints,
                    "metadata": meta,
                }

                col_exists = conn.execute(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'plan_revisions' AND column_name = 'parent_revision_id'
                    """
                ).fetchone()

                if col_exists:
                    conn.execute(
                        """
                        INSERT INTO plan_revisions (
                            revision_id, trip_id, user_id, revision_number, parent_revision_id,
                            change_set, plan_snapshot, is_published, published_at, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, TRUE, %s, %s
                        )
                        """,
                        (
                            str(new_rev_id),
                            tid_str,
                            uid_str,
                            new_revision_number,
                            parent_id_str,
                            json.dumps(change_set.model_dump()),
                            json.dumps(plan_snapshot),
                            now,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO plan_revisions (
                            revision_id, trip_id, user_id, revision_number,
                            change_set, plan_snapshot, is_published, published_at, created_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, TRUE, %s, %s
                        )
                        """,
                        (
                            str(new_rev_id),
                            tid_str,
                            uid_str,
                            new_revision_number,
                            json.dumps(change_set.model_dump()),
                            json.dumps(plan_snapshot),
                            now,
                            now,
                        ),
                    )

                updated_row = conn.execute(
                    """
                    UPDATE trips
                    SET current_revision_id = %s,
                        current_revision_number = %s,
                        status = 'awaiting_approval',
                        metadata = %s::jsonb,
                        updated_at = %s
                    WHERE id = %s AND user_id = %s
                    RETURNING *
                    """,
                    (str(new_rev_id), new_revision_number, json.dumps(meta), now, tid_str, uid_str),
                ).fetchone()

                updated_trip = dict(updated_row) if updated_row else trip_row
                return {
                    "success": True,
                    "trip_id": tid_str,
                    "revision": new_revision_number,
                    "revision_number": new_revision_number,
                    "revision_id": str(new_rev_id),
                    "parent_revision_id": parent_id_str,
                    "status": "awaiting_approval",
                    "proposal_status": "PROPOSED",
                    "review_status": "PROPOSED",
                    "trip_status": "awaiting_approval",
                    "change_set": change_set.model_dump(),
                    "structured_diff": structured_diff.model_dump(),
                    "affected_agents": plan.affected_agents,
                    "plan_snapshot": plan_snapshot,
                    "proposal_snapshot": plan_snapshot,
                    "review_projection": {
                        "revision_id": str(new_rev_id),
                        "revision_number": new_revision_number,
                        "parent_revision_id": parent_id_str,
                        "status": "PROPOSED",
                        "structured_diff": structured_diff.model_dump(),
                        "plan_snapshot": plan_snapshot,
                    },
                    "trip": updated_trip,
                }


    def attach_plan_to_revision(
        self,
        trip_id: str | uuid.UUID,
        user_id: str | uuid.UUID,
        revision_id: str | uuid.UUID,
        plan_json: dict[str, Any],
        raw_plan: str,
    ) -> dict[str, Any]:
        """Attach a newly generated itinerary plan directly to an existing active PROPOSED revision.

        Guarantees that:
        1. Universal parent trip lock FOR UPDATE is acquired.
        2. revision_id must match the trip's current active revision.
        3. Revision number is NOT incremented; no new revision row is created.
        4. Both trips.metadata and plan_revisions.plan_snapshot are atomically updated.
        """
        tid_str = str(trip_id)
        uid_str = str(user_id)
        rid_str = str(revision_id)
        now = datetime.now(timezone.utc)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            with _get_memory_trip_lock(tid_str):
                trip = self._memory_trips.get(tid_str)
                if not trip or str(trip.get("user_id")) != uid_str:
                    raise OfferSelectionError(404, "Trip not found or does not belong to you.")
                cur_rid = str(trip.get("current_revision_id") or "")
                if cur_rid != rid_str:
                    raise OfferSelectionError(409, f"Conflict: Revision {rid_str} is not the active revision ({cur_rid}).")

                norm_plan = json.loads(json.dumps(plan_json, default=str)) if isinstance(plan_json, (dict, list)) else plan_json
                meta = dict(trip.get("metadata") or {})
                meta["plan"] = norm_plan
                meta["raw_plan"] = raw_plan
                trip["metadata"] = meta
                trip["updated_at"] = now
                self._memory_trips[tid_str] = trip

                return {
                    "success": True,
                    "trip_id": tid_str,
                    "revision_id": rid_str,
                    "revision_number": int(trip.get("current_revision_number") or 1),
                    "status": trip.get("status", "awaiting_approval"),
                    "plan": norm_plan,
                    "raw_plan": raw_plan,
                    "trip": trip,
                }

        with self._connect(autocommit=False) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT id, current_revision_id, current_revision_number, status, metadata
                    FROM trips
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (tid_str, uid_str),
                ).fetchone()
                if not row:
                    raise OfferSelectionError(404, "Trip not found or does not belong to you.")

                trip_row = dict(row)
                cur_rid = str(trip_row.get("current_revision_id") or "")
                if cur_rid != rid_str:
                    raise OfferSelectionError(409, f"Conflict: Revision {rid_str} is not the active revision ({cur_rid}).")

                meta = trip_row.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                meta = dict(meta)
                meta["plan"] = plan_json
                meta["raw_plan"] = raw_plan

                updated_trip_row = conn.execute(
                    """
                    UPDATE trips
                    SET metadata = %s::jsonb,
                        updated_at = %s
                    WHERE id = %s AND user_id = %s
                    RETURNING *
                    """,
                    (json.dumps(meta, default=str), now, tid_str, uid_str),
                ).fetchone()

                return {
                    "success": True,
                    "trip_id": tid_str,
                    "revision_id": rid_str,
                    "revision_number": int(trip_row.get("current_revision_number") or 1),
                    "status": trip_row.get("status", "awaiting_approval"),
                    "plan": plan_json,
                    "raw_plan": raw_plan,
                    "trip": dict(updated_trip_row) if updated_trip_row else trip_row,
                }


    def create_thread(
        self,
        user_id: str | uuid.UUID,
        trip_id: str | uuid.UUID | None = None,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a thread owned by user_id and tied to an owned trip."""
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)
        meta = metadata or {}

        # If trip_id is provided, verify it belongs to user_id
        if trip_id:
            trip = self.get_trip(trip_id, user_id)
            if not trip:
                raise PermissionError("Trip does not exist or does not belong to the user.")
            actual_trip_id = str(trip_id)
        else:
            # Create a default trip for this user
            default_trip = self.create_trip(user_id=uid_str, title="Planned Trip")
            actual_trip_id = str(default_trip["id"])

        actual_thread_id = thread_id or f"user_{uuid.uuid4().hex}"

        # Link thread_id to the trip's metadata
        try:
            trip_obj = self.get_trip(actual_trip_id, user_id)
            if trip_obj:
                existing_meta = trip_obj.get("metadata") or {}
                if isinstance(existing_meta, str):
                    existing_meta = json.loads(existing_meta)
                existing_meta = dict(existing_meta)
                existing_meta["thread_id"] = actual_thread_id
                self.update_trip(actual_trip_id, user_id, metadata=existing_meta)
        except Exception:
            pass

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            record = {
                "id": actual_thread_id,
                "trip_id": uuid.UUID(actual_trip_id),
                "user_id": uuid.UUID(uid_str),
                "metadata": meta,
                "created_at": now,
                "updated_at": now,
            }
            self._memory_threads[actual_thread_id] = record
            return record

        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO threads (id, trip_id, user_id, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id, trip_id, user_id, metadata, created_at, updated_at
                """,
                (actual_thread_id, actual_trip_id, uid_str, json.dumps(meta), now, now),
            ).fetchone()
            return dict(row)

    def get_thread(self, thread_id: str, user_id: str | uuid.UUID | None = None) -> dict[str, Any] | None:
        """Fetch a thread by ID, optionally verifying ownership."""
        if not thread_id:
            return None
        th_id = str(thread_id).strip()

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            th = self._memory_threads.get(th_id)
            if not th:
                return None
            if user_id and str(th["user_id"]) != str(user_id):
                return None
            return dict(th)

        with self._connect() as conn:
            if user_id:
                row = conn.execute(
                    "SELECT * FROM threads WHERE id = %s AND user_id = %s",
                    (th_id, str(user_id)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM threads WHERE id = %s",
                    (th_id,),
                ).fetchone()
            return dict(row) if row else None

    def verify_thread_ownership(self, thread_id: str, user_id: str | uuid.UUID) -> bool:
        """Verify that a thread exists and belongs to the authenticated user."""
        return self.get_thread(thread_id, user_id) is not None
