from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from domain.booking_authorization import (
    BookingAuthorization,
    BookingEligibilityError,
    BookingEligibilityResult,
)

logger = logging.getLogger(__name__)


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


def extract_canonical_booking_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Extract and strictly normalize canonical booking fields.

    Presence is tracked separately from value so malformed represented fields
    cannot become ``None`` and silently bypass consistency checks.
    """
    malformed: list[str] = []
    if not isinstance(data, dict):
        return {"_malformed": ["booking state must be an object"]}

    meta = data.get("metadata") if "metadata" in data else {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError, json.JSONDecodeError):
            malformed.append("metadata")
            meta = {}
    elif not isinstance(meta, dict):
        malformed.append("metadata")
        meta = {}

    constraints = data.get("constraints") if "constraints" in data else meta.get("constraints", {})
    if isinstance(constraints, str):
        try:
            constraints = json.loads(constraints)
        except (TypeError, ValueError, json.JSONDecodeError):
            malformed.append("constraints")
            constraints = {}
    elif not isinstance(constraints, dict):
        malformed.append("constraints")
        constraints = {}

    def _get_val(*keys: str) -> tuple[bool, Any]:
        for k in keys:
            if k in data:
                return True, data[k]
            if k in constraints:
                return True, constraints[k]
            if k in meta:
                return True, meta[k]
        return False, None

    def _normalize_text(name: str, value: Any) -> str | None:
        if value is None or not isinstance(value, (str, int)):
            if value is not None:
                malformed.append(name)
            return None
        normalized = str(value).strip()
        if not normalized:
            malformed.append(name)
            return None
        return normalized

    def _normalize_non_negative_int(name: str, present: bool, value: Any) -> int | None:
        if not present:
            return None
        if isinstance(value, bool):
            malformed.append(name)
            return None
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            parsed = int(value.strip())
        else:
            malformed.append(name)
            return None
        if parsed < 0:
            malformed.append(name)
            return None
        return parsed

    def _normalize_positive_int(name: str, present: bool, value: Any) -> int | None:
        parsed = _normalize_non_negative_int(name, present, value)
        if present and parsed is not None and parsed < 1:
            malformed.append(name)
            return None
        return parsed

    def _normalize_room(value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            malformed.append("rooms")
            return None
        if isinstance(value, int):
            if value < 1:
                malformed.append("rooms")
                return None
            return value
        if isinstance(value, str) and re.fullmatch(r"[+]?\d+", value.strip()):
            parsed = int(value.strip())
            if parsed < 1:
                malformed.append("rooms")
                return None
            return parsed
        if not isinstance(value, dict):
            malformed.append("rooms")
            return None
        try:
            return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
        except (TypeError, ValueError):
            malformed.append("rooms")
            return None

    origin_present, origin = _get_val("origin", "origin_iata", "departure_airport")
    destination_present, destination = _get_val("destination", "destination_iata", "arrival_airport")
    departure_present, departure_date = _get_val("departure_date", "start_date")
    return_present, return_date = _get_val("return_date", "end_date")
    passengers_present, passengers = _get_val("passengers", "adults", "travelers_count", "travelers")
    children_present, children = _get_val("children", "children_count", "child_count")
    infants_present, infants = _get_val("infants", "infant_count")
    room_present, rooms = _get_val("selected_room", "room_occupancy", "rooms", "rooms_count")
    flight_present, flight_snap = _get_val("selected_flight_snapshot_id")
    hotel_present, hotel_snap = _get_val("selected_hotel_snapshot_id")

    passengers_int = _normalize_positive_int("passengers", passengers_present, passengers)
    children_int = _normalize_non_negative_int("children", children_present, children)
    infants_int = _normalize_non_negative_int("infants", infants_present, infants)
    room_value = _normalize_room(rooms) if room_present else None

    return {
        "origin": _normalize_text("origin", origin).upper() if origin_present and origin is not None else None,
        "destination": _normalize_text("destination", destination).upper() if destination_present and destination is not None else None,
        "departure_date": _normalize_text("departure_date", departure_date)[:10] if departure_present and departure_date is not None else None,
        "return_date": _normalize_text("return_date", return_date)[:10] if return_present and return_date is not None else None,
        "passengers": passengers_int,
        "children": children_int,
        "infants": infants_int,
        "rooms": room_value,
        "selected_flight_snapshot_id": _normalize_text("selected_flight_snapshot_id", flight_snap) if flight_present else None,
        "selected_hotel_snapshot_id": _normalize_text("selected_hotel_snapshot_id", hotel_snap) if hotel_present else None,
        "_present": {
            "origin": origin_present,
            "destination": destination_present,
            "departure_date": departure_present,
            "return_date": return_present,
            "passengers": passengers_present,
            "children": children_present,
            "infants": infants_present,
            "rooms": room_present,
            "selected_flight_snapshot_id": flight_present,
            "selected_hotel_snapshot_id": hotel_present,
        },
        "_malformed": malformed,
    }


def verify_canonical_booking_state_consistency(
    trip: dict[str, Any],
    revision_snapshot: dict[str, Any],
) -> tuple[bool, str | None]:
    """Strictly verify canonical booking state consistency between current trip and accepted revision snapshot.

    Enforces Section 9 Invariants:
    1. Every required booking-relevant field (origin, destination, departure_date, return_date, passengers)
       MUST be present on both trip and accepted revision snapshot. If missing from either -> fail closed.
    2. Explicit type-consistent equality: values must match exactly.
    3. Any represented optional occupancy or offer snapshot fields (children, infants, rooms, selected_flight_snapshot_id,
       selected_hotel_snapshot_id) must also match exactly.
    """
    trip_fields = extract_canonical_booking_fields(trip)
    snap_fields = extract_canonical_booking_fields(revision_snapshot)

    for source, fields in (("canonical trip state", trip_fields), ("accepted revision snapshot", snap_fields)):
        if fields["_malformed"]:
            return False, f"Malformed booking-relevant field(s) in {source}: {', '.join(fields['_malformed'])}."

    # 1. Required booking fields check (fail closed if missing from either)
    required_fields = ["origin", "destination", "departure_date", "return_date", "passengers"]
    for f in required_fields:
        t_val = trip_fields.get(f)
        s_val = snap_fields.get(f)
        if t_val is None:
            return False, f"Required booking-relevant field '{f}' is missing from canonical trip state."
        if s_val is None:
            return False, f"Required booking-relevant field '{f}' is missing from accepted revision snapshot."
        if t_val != s_val:
            return False, f"Canonical field mismatch for '{f}': trip has '{t_val}', revision snapshot has '{s_val}'."

    # 2. Represented optional/occupancy fields check (fail closed if mismatch)
    optional_fields = ["children", "infants", "selected_flight_snapshot_id", "selected_hotel_snapshot_id"]
    for f in optional_fields:
        if trip_fields["_present"][f] != snap_fields["_present"][f]:
            return False, f"Canonical field presence mismatch for '{f}'."
        t_val = trip_fields.get(f)
        s_val = snap_fields.get(f)
        if t_val is not None or s_val is not None:
            if t_val != s_val:
                return False, f"Canonical field mismatch for '{f}': trip has '{t_val}', revision snapshot has '{s_val}'."

    # 3. Rooms / occupancy comparison
    if trip_fields["_present"]["rooms"] != snap_fields["_present"]["rooms"]:
        return False, "Canonical field presence mismatch for 'rooms'."
    if trip_fields.get("rooms") != snap_fields.get("rooms"):
        return False, "Canonical room/occupancy structure mismatch."

    return True, None


class AuthorizationStore:
    """Store managing immutable explicit BookingAuthorizations and runtime booking eligibility derivation."""

    _IN_MEMORY_AUTHORIZATIONS: dict[str, BookingAuthorization] = {}

    def __init__(self, use_in_memory: bool | None = None) -> None:
        if use_in_memory is None:
            self.use_in_memory = os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}
        else:
            self.use_in_memory = use_in_memory

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(_dsn(), row_factory=dict_row)

    def get_booking_authorization(
        self,
        trip_id: UUID | str,
        revision_id: UUID | str,
    ) -> BookingAuthorization | None:
        """Fetch exact booking authorization by trip_id and revision_id."""
        tid_str = str(trip_id)
        rid_str = str(revision_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            for auth in self._IN_MEMORY_AUTHORIZATIONS.values():
                if str(auth.trip_id) == tid_str and str(auth.revision_id) == rid_str:
                    return auth
            return None

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT authorization_id, trip_id, revision_id, user_id, revision_number,
                       authorized_at, authorization_metadata
                FROM booking_authorizations
                WHERE trip_id = %s AND revision_id = %s
                """,
                (tid_str, rid_str),
            ).fetchone()
            if not row:
                return None
            return BookingAuthorization(
                authorization_id=row["authorization_id"],
                trip_id=row["trip_id"],
                revision_id=row["revision_id"],
                user_id=row["user_id"],
                revision_number=row["revision_number"],
                authorized_at=row["authorized_at"],
                authorization_metadata=row["authorization_metadata"] or {},
            )

    get_authorization = get_booking_authorization

    def list_authorizations_for_trip(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
    ) -> list[BookingAuthorization]:
        """List all historical booking authorizations for a trip."""
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            return [
                auth for auth in self._IN_MEMORY_AUTHORIZATIONS.values()
                if str(auth.trip_id) == tid_str and str(auth.user_id) == uid_str
            ]

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT authorization_id, trip_id, revision_id, user_id, revision_number,
                       authorized_at, authorization_metadata
                FROM booking_authorizations
                WHERE trip_id = %s AND user_id = %s
                ORDER BY revision_number ASC
                """,
                (tid_str, uid_str),
            ).fetchall()
            return [
                BookingAuthorization(
                    authorization_id=r["authorization_id"],
                    trip_id=r["trip_id"],
                    revision_id=r["revision_id"],
                    user_id=r["user_id"],
                    revision_number=r["revision_number"],
                    authorized_at=r["authorized_at"],
                    authorization_metadata=r["authorization_metadata"] or {},
                )
                for r in rows
            ]

    def check_revision_accepted(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        revision_id: UUID | str,
        expected_revision_number: int,
    ) -> bool:
        """Check if revision has valid Section 8 plan acceptance without asserting booking authorization."""
        from services.evidence_store import EvidenceRevisionStore
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)
        rid_str = str(revision_id)

        trip = TripStore().get_trip(trip_id, user_id)
        if not trip:
            return False

        ev_store = EvidenceRevisionStore()
        rev = ev_store.get_revision(revision_id, user_id)
        if not rev or str(rev.trip_id) != tid_str or str(rev.user_id) != uid_str:
            return False

        if str(trip.get("current_revision_id") or "") != rid_str:
            return False

        if int(trip.get("current_revision_number") or 0) != expected_revision_number:
            return False

        if trip.get("status") != "approved":
            return False

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            is_approved = any(
                str(a.trip_id) == tid_str and str(a.revision_id) == rid_str and str(a.approved_by_user_id) == uid_str
                for a in ev_store._IN_MEMORY_APPROVALS.values()
            )
            is_rejected = any(
                str(r.trip_id) == tid_str and str(r.revision_id) == rid_str
                for r in ev_store._IN_MEMORY_REJECTIONS.values()
            )
            return is_approved and not is_rejected

        with self._connect() as conn:
            rej = conn.execute(
                "SELECT 1 FROM plan_revision_rejections WHERE trip_id = %s AND revision_id = %s",
                (tid_str, rid_str),
            ).fetchone()
            if rej:
                return False
            appr = conn.execute(
                "SELECT 1 FROM plan_approvals WHERE trip_id = %s AND revision_id = %s AND approved_by_user_id = %s",
                (tid_str, rid_str, uid_str),
            ).fetchone()
            return appr is not None

    def assert_booking_eligible(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        revision_id: UUID | str,
        expected_revision_number: int,
    ) -> BookingEligibilityResult:
        """Authoritative booking eligibility guard.

        Strictly evaluates ALL 10 booking conditions:
        1. Trip exists and belongs to user
        2. Revision exists and belongs to trip/user
        3. Revision is active/current revision
        4. Revision number matches current and expected revision number
        5. Revision is published
        6. Revision is NOT rejected
        7. Revision has Section 8 explicit plan approval
        8. Trip status is 'approved'
        9. Complete canonical booking state consistency (origin, destination, departure, return, passengers, etc.)
        10. Revision has Section 9 explicit booking authorization.
            NO FLAG OR PARAMETER CAN BYPASS THIS REQUIREMENT.

        Raises BookingEligibilityError on any violation.
        """
        from services.evidence_store import EvidenceRevisionStore
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)
        rid_str = str(revision_id)
        reasons: list[str] = []

        # 1. Trip exists and belongs to user
        trip_store = TripStore()
        trip = trip_store.get_trip(trip_id, user_id)
        if not trip:
            raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

        # 2. Revision exists and belongs to trip/user
        ev_store = EvidenceRevisionStore()
        rev = ev_store.get_revision(revision_id, user_id)
        if not rev or str(rev.trip_id) != tid_str or str(rev.user_id) != uid_str:
            raise HTTPException(status_code=404, detail="Revision not found or does not belong to this trip.")

        # 3. Revision is active/current
        cur_rid = str(trip.get("current_revision_id") or "")
        if cur_rid != rid_str:
            msg = f"Revision {rid_str} is not the current active revision ({cur_rid})."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 4. Revision number matches expected
        cur_num = int(trip.get("current_revision_number") or 0)
        if cur_num != expected_revision_number or rev.revision_number != expected_revision_number:
            msg = f"Revision number mismatch: expected {expected_revision_number} but current is {cur_num} (rev {rev.revision_number})."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 5. Revision is published
        if not rev.is_published:
            msg = f"Revision {rid_str} is not published."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 6. Revision is NOT rejected
        is_rejected = False
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            for r in ev_store._IN_MEMORY_REJECTIONS.values():
                if str(r.trip_id) == tid_str and str(r.revision_id) == rid_str:
                    is_rejected = True
                    break
        else:
            with self._connect() as conn:
                rej_row = conn.execute(
                    "SELECT 1 FROM plan_revision_rejections WHERE trip_id = %s AND revision_id = %s",
                    (tid_str, rid_str),
                ).fetchone()
                is_rejected = rej_row is not None

        if is_rejected:
            msg = f"Revision {rid_str} is rejected and cannot be booked."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 7. Revision HAS explicit plan acceptance (approved)
        is_approved = False
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            for a in ev_store._IN_MEMORY_APPROVALS.values():
                if str(a.trip_id) == tid_str and str(a.revision_id) == rid_str and str(a.approved_by_user_id) == uid_str:
                    is_approved = True
                    break
        else:
            with self._connect() as conn:
                appr_row = conn.execute(
                    "SELECT 1 FROM plan_approvals WHERE trip_id = %s AND revision_id = %s AND approved_by_user_id = %s",
                    (tid_str, rid_str, uid_str),
                ).fetchone()
                is_approved = appr_row is not None

        if not is_approved:
            msg = f"Revision {rid_str} has not received explicit plan approval."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 8. Trip status is 'approved'
        if trip.get("status") != "approved":
            msg = f"Trip status is '{trip.get('status')}', expected 'approved'."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 9. Complete canonical booking state consistency
        is_consistent, drift_reason = verify_canonical_booking_state_consistency(trip, rev.plan_snapshot)
        if not is_consistent:
            msg = f"Canonical state drift detected: {drift_reason}."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        # 10. Booking authorization record check (MANDATORY — CANNOT BE BYPASSED)
        auth = self.get_booking_authorization(trip_id, revision_id)
        if not auth:
            msg = f"Revision {rid_str} has not been explicitly authorized for booking (Section 9 authorization required)."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)
        if (
            str(auth.trip_id) != tid_str
            or str(auth.revision_id) != rid_str
            or str(auth.user_id) != uid_str
            or int(auth.revision_number) != cur_num
            or int(auth.revision_number) != expected_revision_number
            or int(rev.revision_number) != cur_num
        ):
            msg = f"Booking authorization binding mismatch for revision {rid_str}."
            reasons.append(msg)
            raise BookingEligibilityError(msg, reasons)

        return BookingEligibilityResult(
            is_eligible=True,
            trip_id=UUID(tid_str),
            revision_id=UUID(rid_str),
            revision_number=cur_num,
            is_approved=True,
            is_rejected=False,
            is_authorized=True,
            authorized_at=auth.authorized_at,
            reasons=[],
        )

    def check_booking_eligibility(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        revision_id: UUID | str | None = None,
        expected_revision_number: int | None = None,
    ) -> BookingEligibilityResult:
        """Safe non-raising derivation of booking eligibility (always checks both approval and authorization)."""
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)
        trip = TripStore().get_trip(trip_id, user_id)
        if not trip:
            return BookingEligibilityResult(
                is_eligible=False,
                trip_id=UUID(tid_str),
                reasons=["Trip not found or does not belong to you."],
            )

        target_rid = str(revision_id) if revision_id else str(trip.get("current_revision_id") or "")
        target_num = expected_revision_number if expected_revision_number is not None else int(trip.get("current_revision_number") or 0)

        if not target_rid:
            return BookingEligibilityResult(
                is_eligible=False,
                trip_id=UUID(tid_str),
                reasons=["Trip has no current revision."],
            )

        try:
            return self.assert_booking_eligible(
                trip_id=trip_id,
                user_id=user_id,
                revision_id=target_rid,
                expected_revision_number=target_num,
            )
        except BookingEligibilityError as err:
            auth = self.get_booking_authorization(trip_id, target_rid)
            return BookingEligibilityResult(
                is_eligible=False,
                trip_id=UUID(tid_str),
                revision_id=UUID(target_rid),
                revision_number=target_num,
                is_approved=trip.get("status") == "approved",
                is_authorized=auth is not None,
                authorized_at=auth.authorized_at if auth else None,
                reasons=err.reasons,
            )
        except HTTPException as exc:
            return BookingEligibilityResult(
                is_eligible=False,
                trip_id=UUID(tid_str),
                revision_id=UUID(target_rid),
                revision_number=target_num,
                reasons=[str(exc.detail)],
            )

    def create_booking_authorization_atomic(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        revision_id: UUID | str,
        expected_revision_number: int,
        metadata: dict[str, Any] | None = None,
    ) -> BookingAuthorization:
        """Atomically create immutable BookingAuthorization under parent trip row lock with full eligibility checks."""
        from services.evidence_store import EvidenceRevisionStore
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)
        rid_str = str(revision_id)
        meta = metadata or {}
        now = datetime.now(timezone.utc)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            trip = TripStore().get_trip(trip_id, user_id)
            if not trip:
                raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

            ev_store = EvidenceRevisionStore()
            rev = ev_store.get_revision(revision_id, user_id)
            if not rev or str(rev.trip_id) != tid_str or str(rev.user_id) != uid_str:
                raise HTTPException(status_code=404, detail="Revision not found or does not belong to this trip.")

            cur_rid = str(trip.get("current_revision_id") or "")
            if cur_rid != rid_str:
                raise HTTPException(
                    status_code=409,
                    detail=f"Conflict: Cannot authorize revision {rid_str}: not current revision ({cur_rid}).",
                )

            cur_num = int(trip.get("current_revision_number") or 0)
            if cur_num != expected_revision_number or rev.revision_number != expected_revision_number:
                raise HTTPException(
                    status_code=409,
                    detail=f"Conflict: Expected revision number {expected_revision_number} but current is {cur_num}.",
                )

            # Check rejection
            for r in ev_store._IN_MEMORY_REJECTIONS.values():
                if str(r.trip_id) == tid_str and str(r.revision_id) == rid_str:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Revision {rid_str} is rejected; cannot authorize for booking.",
                    )

            # Check approval prerequisite
            is_approved = any(
                str(a.trip_id) == tid_str and str(a.revision_id) == rid_str and str(a.approved_by_user_id) == uid_str
                for a in ev_store._IN_MEMORY_APPROVALS.values()
            )
            if not is_approved or trip.get("status") != "approved":
                raise HTTPException(
                    status_code=409,
                    detail=f"Conflict: Revision {rid_str} must have explicit plan approval before booking authorization.",
                )

            # Complete canonical booking state consistency (centralized helper)
            is_consistent, drift_reason = verify_canonical_booking_state_consistency(trip, rev.plan_snapshot)
            if not is_consistent:
                raise HTTPException(
                    status_code=409,
                    detail=f"Conflict: Canonical state drift: {drift_reason}.",
                )

            # Check duplicate / existing authorization (idempotent return per ADR-009 Section F)
            for auth in self._IN_MEMORY_AUTHORIZATIONS.values():
                if str(auth.trip_id) == tid_str and str(auth.revision_id) == rid_str:
                    if (
                        str(auth.user_id) != uid_str
                        or int(auth.revision_number) != cur_num
                        or int(auth.revision_number) != expected_revision_number
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=f"Conflict: Existing authorization binding does not match revision {rid_str}.",
                        )
                    return auth

            auth_id = uuid4()
            auth = BookingAuthorization(
                authorization_id=auth_id,
                trip_id=UUID(tid_str),
                revision_id=UUID(rid_str),
                user_id=UUID(uid_str),
                revision_number=cur_num,
                authorized_at=now,
                authorization_metadata=meta,
            )
            self._IN_MEMORY_AUTHORIZATIONS[str(auth_id)] = auth
            return auth

        with self._connect() as conn:
            with conn.transaction():
                # 1. Acquire trip row lock FOR UPDATE
                trip_row = conn.execute(
                    """
                    SELECT *
                    FROM trips
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (tid_str, uid_str),
                ).fetchone()

                if not trip_row:
                    raise HTTPException(status_code=404, detail="Trip not found or does not belong to you.")

                cur_rid = str(trip_row["current_revision_id"] or "")
                if cur_rid != rid_str:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Cannot authorize revision {rid_str}: not current revision ({cur_rid}).",
                    )

                cur_num = int(trip_row["current_revision_number"] or 0)
                if cur_num != expected_revision_number:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Expected revision number {expected_revision_number} but current is {cur_num}.",
                    )

                # 2. Check revision exists and matches
                rev_row = conn.execute(
                    """
                    SELECT revision_id, trip_id, user_id, revision_number, is_published, plan_snapshot
                    FROM plan_revisions
                    WHERE revision_id = %s AND trip_id = %s AND user_id = %s
                    """,
                    (rid_str, tid_str, uid_str),
                ).fetchone()

                if not rev_row:
                    raise HTTPException(status_code=404, detail="Revision not found or does not belong to this trip.")

                if rev_row["revision_number"] != expected_revision_number:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Revision number mismatch: expected {expected_revision_number} but revision is {rev_row['revision_number']}.",
                    )

                # 3. Check rejection
                rej_row = conn.execute(
                    "SELECT 1 FROM plan_revision_rejections WHERE trip_id = %s AND revision_id = %s",
                    (tid_str, rid_str),
                ).fetchone()
                if rej_row:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Revision {rid_str} is rejected; cannot authorize for booking.",
                    )

                # 4. Check approval prerequisite
                appr_row = conn.execute(
                    "SELECT 1 FROM plan_approvals WHERE trip_id = %s AND revision_id = %s AND approved_by_user_id = %s",
                    (tid_str, rid_str, uid_str),
                ).fetchone()
                if not appr_row or trip_row["status"] != "approved":
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Revision {rid_str} must have explicit plan approval before booking authorization.",
                    )

                # 5. Complete canonical booking state consistency (centralized helper)
                is_consistent, drift_reason = verify_canonical_booking_state_consistency(dict(trip_row), rev_row["plan_snapshot"])
                if not is_consistent:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Canonical state drift: {drift_reason}.",
                    )

                # 6. Check existing authorization (idempotent deterministic return per ADR-009 Section F)
                existing = conn.execute(
                    """
                    SELECT authorization_id, trip_id, revision_id, user_id, revision_number,
                           authorized_at, authorization_metadata
                    FROM booking_authorizations
                    WHERE trip_id = %s AND revision_id = %s
                    """,
                    (tid_str, rid_str),
                ).fetchone()

                if existing:
                    if (
                        str(existing["trip_id"]) != tid_str
                        or str(existing["revision_id"]) != rid_str
                        or str(existing["user_id"]) != uid_str
                        or int(existing["revision_number"]) != cur_num
                        or int(existing["revision_number"]) != expected_revision_number
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=f"Conflict: Existing authorization binding does not match revision {rid_str}.",
                        )
                    return BookingAuthorization(
                        authorization_id=existing["authorization_id"],
                        trip_id=existing["trip_id"],
                        revision_id=existing["revision_id"],
                        user_id=existing["user_id"],
                        revision_number=existing["revision_number"],
                        authorized_at=existing["authorized_at"],
                        authorization_metadata=existing["authorization_metadata"] or {},
                    )

                # 7. Insert new booking authorization
                auth_id = uuid4()
                conn.execute(
                    """
                    INSERT INTO booking_authorizations (
                        authorization_id, trip_id, revision_id, user_id, revision_number,
                        authorized_at, authorization_metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (str(auth_id), tid_str, rid_str, uid_str, cur_num, now, json.dumps(meta)),
                )

                return BookingAuthorization(
                    authorization_id=auth_id,
                    trip_id=UUID(tid_str),
                    revision_id=UUID(rid_str),
                    user_id=UUID(uid_str),
                    revision_number=cur_num,
                    authorized_at=now,
                    authorization_metadata=meta,
                )
