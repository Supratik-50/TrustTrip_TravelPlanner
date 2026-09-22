"""Persistence and Encryption Store for Search Sessions, Offer Snapshots, and Revalidations.

Enforces strict tenant isolation, immutable append-only snapshots, and AES-256-GCM
encryption for raw supplier payloads.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FreshnessStatus,
    HotelOffer,
    OfferSnapshot,
    RevalidationResult,
    SearchSession,
)


def _get_encryption_key() -> bytes:
    """Load and validate the 32-byte AES-256-GCM encryption key."""
    raw_key = os.getenv("PAYLOAD_ENCRYPTION_KEY")
    use_in_memory = os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}

    if not raw_key:
        if use_in_memory:
            # Ephemeral 32-byte key for local development and unit tests
            dev_key = os.environ.setdefault(
                "_DEV_PAYLOAD_ENCRYPTION_KEY",
                base64.b64encode(secrets.token_bytes(32)).decode("utf-8"),
            )
            raw_key = dev_key
        else:
            raise RuntimeError(
                "PAYLOAD_ENCRYPTION_KEY is missing. Production environments require a 32-byte base64 key."
            )

    try:
        key_bytes = base64.b64decode(raw_key)
    except Exception as exc:
        raise RuntimeError("PAYLOAD_ENCRYPTION_KEY is not valid base64.") from exc

    if len(key_bytes) != 32:
        raise RuntimeError(
            f"PAYLOAD_ENCRYPTION_KEY must decode to exactly 32 bytes (got {len(key_bytes)} bytes)."
        )

    return key_bytes


def encrypt_payload(payload: dict[str, Any], key: bytes) -> bytes:
    """Encrypt payload dictionary using AES-256-GCM with a unique 12-byte nonce."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    serialized = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    nonce = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, serialized, None)
    # Binary format: Nonce (12B) + Ciphertext + Tag (16B appended by AESGCM)
    return nonce + ciphertext


def decrypt_payload(encrypted_blob: bytes, key: bytes) -> dict[str, Any]:
    """Decrypt payload using AES-256-GCM and verify authentication tag."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(encrypted_blob) < 28:  # 12B nonce + 16B tag minimum
        raise ValueError("Encrypted blob is corrupted or too short.")

    nonce = encrypted_blob[:12]
    ciphertext = encrypted_blob[12:]
    aesgcm = AESGCM(key)
    decrypted = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(decrypted.decode("utf-8"))


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for travel intelligence persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


REQUIRED_TRAVEL_INTELLIGENCE_TABLES = ("search_sessions", "offer_snapshots", "revalidation_history")


def validate_travel_intelligence_schema() -> None:
    """Validate that required travel intelligence tables exist in PostgreSQL.

    Fails closed with RuntimeError if tables are missing (unmigrated database).
    Executes ZERO DDL statements. Schema creation/modification must be performed
    exclusively via versioned migrations (e.g. migrations/004_travel_intelligence.sql).
    """
    if os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}:
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL must be configured.")

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (list(REQUIRED_TRAVEL_INTELLIGENCE_TABLES),),
            )
            found = {row[0] for row in cur.fetchall()}
            missing = set(REQUIRED_TRAVEL_INTELLIGENCE_TABLES) - found
            if missing:
                raise RuntimeError(
                    f"Travel intelligence database tables missing: {sorted(missing)}. "
                    f"Run migration 'migrations/004_travel_intelligence.sql' before starting the application."
                )


class SearchSessionStore:
    """Thread-safe, tenant-isolated repository for search sessions and immutable offer snapshots."""

    _IN_MEMORY_SESSIONS: dict[str, dict[str, Any]] = {}
    _IN_MEMORY_SNAPSHOTS: dict[str, dict[str, Any]] = {}
    _IN_MEMORY_REVALIDATIONS: dict[str, dict[str, Any]] = {}

    def __init__(self) -> None:
        self.use_in_memory = os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}
        self._key = _get_encryption_key()

        if not self.use_in_memory:
            if not os.getenv("DATABASE_URL"):
                raise RuntimeError("DATABASE_URL must be configured in production.")
            # Zero DDL executed at startup or runtime; schema is validated via validate_travel_intelligence_schema()

    def _connect(self):
        return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)

    def get_active_session_by_hash(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        criteria_hash: str,
    ) -> SearchSession | None:
        """Find an active, non-expired search session for the given user, trip, and query hash."""
        uid_str = str(user_id)
        tid_str = str(trip_id)
        now = datetime.now(timezone.utc)

        if self.use_in_memory:
            for s in self._IN_MEMORY_SESSIONS.values():
                if (
                    str(s["user_id"]) == uid_str
                    and str(s["trip_id"]) == tid_str
                    and s["criteria_hash"] == criteria_hash
                    and s["status"] == "active"
                    and s["session_reuse_expires_at"] > now
                ):
                    return SearchSession.model_validate(s)
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id AS session_id, user_id, trip_id, thread_id, trip_revision,
                       search_type, criteria_hash, status, created_at, session_reuse_expires_at
                FROM search_sessions
                WHERE user_id = %s AND trip_id = %s AND criteria_hash = %s
                  AND status = 'active' AND session_reuse_expires_at > %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (uid_str, tid_str, criteria_hash, now),
            ).fetchone()
            return SearchSession.model_validate(dict(row)) if row else None

    def create_session(
        self,
        user_id: UUID | str,
        trip_id: UUID | str,
        thread_id: str | None,
        search_type: str,
        criteria_hash: str,
        trip_revision: int = 0,
        reuse_ttl_seconds: int = 900,  # 15 minutes default
    ) -> SearchSession:
        """Create a new search session bound to user_id and trip_id."""
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now.timestamp() + reuse_ttl_seconds, tz=timezone.utc)

        session_data = {
            "session_id": session_id,
            "user_id": UUID(str(user_id)),
            "trip_id": UUID(str(trip_id)),
            "thread_id": thread_id,
            "trip_revision": trip_revision,
            "search_type": search_type,
            "criteria_hash": criteria_hash,
            "status": "active",
            "created_at": now,
            "session_reuse_expires_at": expires_at,
        }

        if self.use_in_memory:
            self._IN_MEMORY_SESSIONS[str(session_id)] = session_data
            return SearchSession.model_validate(session_data)

        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO search_sessions (
                    id, user_id, trip_id, thread_id, trip_revision,
                    search_type, criteria_hash, status, created_at, session_reuse_expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id AS session_id, user_id, trip_id, thread_id, trip_revision,
                          search_type, criteria_hash, status, created_at, session_reuse_expires_at
                """,
                (
                    session_id,
                    str(user_id),
                    str(trip_id),
                    thread_id,
                    trip_revision,
                    search_type,
                    criteria_hash,
                    "active",
                    now,
                    expires_at,
                ),
            ).fetchone()
            return SearchSession.model_validate(dict(row))

    def save_snapshot(
        self,
        session_id: UUID | str,
        offer: FlightOffer | HotelOffer,
        raw_payload: dict[str, Any],
        predecessor_snapshot_id: UUID | str | None = None,
    ) -> OfferSnapshot:
        """Persist an immutable offer snapshot with encrypted raw supplier payload."""
        snapshot_id = offer.snapshot_id
        offer_type = "flight" if isinstance(offer, FlightOffer) else "hotel"
        encrypted_raw = encrypt_payload(raw_payload, self._key)
        now = datetime.now(timezone.utc)

        snapshot_record = {
            "snapshot_id": snapshot_id,
            "session_id": UUID(str(session_id)),
            "provider": offer.provider,
            "provider_offer_id": offer.provider_offer_id,
            "offer_type": offer_type,
            "price_total": offer.price.total_amount,
            "currency": offer.price.currency,
            "normalized_offer": offer,
            "freshness": offer.freshness,
            "availability": offer.availability,
            "retrieved_at": offer.retrieved_at,
            "expires_at": offer.expires_at,
            "predecessor_snapshot_id": UUID(str(predecessor_snapshot_id)) if predecessor_snapshot_id else None,
        }

        if self.use_in_memory:
            mem_copy = dict(snapshot_record)
            mem_copy["raw_payload_encrypted"] = encrypted_raw
            self._IN_MEMORY_SNAPSHOTS[str(snapshot_id)] = mem_copy
            return OfferSnapshot.model_validate(snapshot_record)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO offer_snapshots (
                    id, session_id, provider, provider_offer_id, offer_type,
                    price_total, currency, normalized_offer, raw_payload_encrypted,
                    freshness, availability, retrieved_at, expires_at, predecessor_snapshot_id
                )
                VALUES (%s, %s, %s, %s, %s::offer_type_enum, %s, %s, %s::jsonb, %s,
                        %s::freshness_status_enum, %s::availability_status_enum, %s, %s, %s)
                """,
                (
                    snapshot_id,
                    str(session_id),
                    offer.provider,
                    offer.provider_offer_id,
                    offer_type,
                    offer.price.total_amount,
                    offer.price.currency,
                    offer.model_dump_json(),
                    encrypted_raw,
                    offer.freshness.value,
                    offer.availability.value,
                    offer.retrieved_at,
                    offer.expires_at,
                    str(predecessor_snapshot_id) if predecessor_snapshot_id else None,
                ),
            )
            return OfferSnapshot.model_validate(snapshot_record)

    def get_snapshot(
        self,
        snapshot_id: UUID | str,
        user_id: UUID | str,
    ) -> OfferSnapshot | None:
        """Fetch snapshot enforcing that it belongs to the authenticated user via its session."""
        sid_str = str(snapshot_id)
        uid_str = str(user_id)

        if self.use_in_memory:
            record = self._IN_MEMORY_SNAPSHOTS.get(sid_str)
            if not record:
                return None
            session = self._IN_MEMORY_SESSIONS.get(str(record["session_id"]))
            if not session or str(session["user_id"]) != uid_str:
                return None
            clean_record = {k: v for k, v in record.items() if k != "raw_payload_encrypted"}
            return OfferSnapshot.model_validate(clean_record)

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.id AS snapshot_id, s.session_id, s.provider, s.provider_offer_id,
                       s.offer_type, s.price_total, s.currency, s.normalized_offer,
                       s.freshness, s.availability, s.retrieved_at, s.expires_at,
                       s.predecessor_snapshot_id
                FROM offer_snapshots s
                JOIN search_sessions ss ON s.session_id = ss.id
                WHERE s.id = %s AND ss.user_id = %s
                """,
                (sid_str, uid_str),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            offer_dict = data["normalized_offer"]
            if data["offer_type"] == "flight":
                data["normalized_offer"] = FlightOffer.model_validate(offer_dict)
            else:
                data["normalized_offer"] = HotelOffer.model_validate(offer_dict)
            return OfferSnapshot.model_validate(data)

    def get_raw_payload(
        self,
        snapshot_id: UUID | str,
        user_id: UUID | str,
    ) -> dict[str, Any] | None:
        """Privileged backend retrieval of decrypted raw supplier payload."""
        sid_str = str(snapshot_id)
        uid_str = str(user_id)

        if self.use_in_memory:
            record = self._IN_MEMORY_SNAPSHOTS.get(sid_str)
            if not record:
                return None
            session = self._IN_MEMORY_SESSIONS.get(str(record["session_id"]))
            if not session or str(session["user_id"]) != uid_str:
                return None
            blob = record.get("raw_payload_encrypted")
            return decrypt_payload(blob, self._key) if blob else None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.raw_payload_encrypted
                FROM offer_snapshots s
                JOIN search_sessions ss ON s.session_id = ss.id
                WHERE s.id = %s AND ss.user_id = %s
                """,
                (sid_str, uid_str),
            ).fetchone()
            if not row or not row["raw_payload_encrypted"]:
                return None
            blob = bytes(row["raw_payload_encrypted"])
            return decrypt_payload(blob, self._key)

    def record_revalidation(
        self,
        original_snapshot_id: UUID | str,
        result: RevalidationResult,
        successor_snapshot: OfferSnapshot | None = None,
    ) -> None:
        """Record immutable revalidation audit log and link successor snapshot if created."""
        rev_id = result.revalidation_id
        orig_id = UUID(str(original_snapshot_id))
        sub_id = successor_snapshot.snapshot_id if successor_snapshot else result.subsequent_snapshot_id

        data = {
            "revalidation_id": rev_id,
            "original_snapshot_id": orig_id,
            "subsequent_snapshot_id": sub_id,
            "status": result.status.value,
            "verified_at": result.verified_at,
            "price_changed": result.price_changed,
            "original_price": result.original_price,
            "current_price": result.current_price,
            "currency": result.currency,
            "schedule_changed": result.schedule_changed,
            "details": result.details,
        }

        if self.use_in_memory:
            self._IN_MEMORY_REVALIDATIONS[str(rev_id)] = data
            return

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO revalidation_history (
                    id, original_snapshot_id, subsequent_snapshot_id, status,
                    verified_at, price_changed, original_price, current_price,
                    currency, schedule_changed, details
                )
                VALUES (%s, %s, %s, %s::revalidation_status_enum, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rev_id,
                    orig_id,
                    sub_id,
                    result.status.value,
                    result.verified_at,
                    result.price_changed,
                    result.original_price,
                    result.current_price,
                    result.currency,
                    result.schedule_changed,
                    result.details,
                ),
            )
