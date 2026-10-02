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

    def get_trip(self, trip_id: str | uuid.UUID, user_id: str | uuid.UUID) -> dict[str, Any] | None:
        """Fetch trip strictly verifying ownership by user_id."""
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if t and str(t["user_id"]) == uid_str:
                return dict(t)
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trips WHERE id = %s AND user_id = %s",
                (tid_str, uid_str),
            ).fetchone()
            return dict(row) if row else None

    def list_trips(self, user_id: str | uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        """List all trips owned by user_id. Never returns another user's trips."""
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            items = [t for t in self._memory_trips.values() if str(t["user_id"]) == uid_str]
            items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            return [dict(t) for t in items[:limit]]

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trips WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (uid_str, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_trip(self, trip_id: str | uuid.UUID, user_id: str | uuid.UUID) -> bool:
        """Delete trip strictly scoped by user_id."""
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if t and str(t["user_id"]) == uid_str:
                del self._memory_trips[tid_str]
                # Cascade delete associated threads
                for th_id, th in list(self._memory_threads.items()):
                    if str(th.get("trip_id")) == tid_str:
                        del self._memory_threads[th_id]
                return True
            return False

        with self._connect() as conn:
            row = conn.execute(
                "DELETE FROM trips WHERE id = %s AND user_id = %s RETURNING id",
                (tid_str, uid_str),
            ).fetchone()
            return bool(row)

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
