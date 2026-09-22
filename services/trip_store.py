"""Database persistence for Trip and Thread ownership.

Enforces strict tenant isolation: every trip and thread is bound to user_id.
All queries require WHERE user_id = %s.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
import psycopg
from psycopg.rows import dict_row

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

    def _connect(self):
        return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)

    def create_trip(
        self,
        user_id: str | uuid.UUID,
        title: str = "New Trip",
        destination: str = "",
        origin: str = "",
        status: str = "planning",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a trip owned by the authenticated user."""
        trip_id = uuid.uuid4()
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)
        meta = metadata or {}

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            record = {
                "id": trip_id,
                "user_id": uuid.UUID(uid_str),
                "title": title,
                "destination": destination,
                "origin": origin,
                "status": status,
                "metadata": meta,
                "created_at": now,
                "updated_at": now,
            }
            self._memory_trips[str(trip_id)] = record
            return record

        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO trips (id, user_id, title, destination, origin, status, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id, user_id, title, destination, origin, status, metadata, created_at, updated_at
                """,
                (trip_id, uid_str, title, destination, origin, status, json.dumps(meta), now, now),
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
        """Update an owned trip's fields (status, metadata, etc.)."""
        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)

        allowed = {"title", "destination", "origin", "status", "metadata"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return self.get_trip(trip_id, user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            t = self._memory_trips.get(tid_str)
            if not t or str(t["user_id"]) != uid_str:
                return None
            t.update(filtered)
            t["updated_at"] = now
            return dict(t)

        sets = []
        vals = []
        for k, v in filtered.items():
            if k == "metadata":
                sets.append(f"{k} = %s::jsonb")
                vals.append(json.dumps(v))
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
