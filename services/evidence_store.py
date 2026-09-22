"""Phase 6 Evidence, Revision, and Approval Store.

Enforces:
1. Universal parent trip row lock (SELECT ... FOR UPDATE)
2. Immutability of EvidenceRecord, PlanRevision, and PlanApproval
3. Attach-once EvidenceProvenance
4. Optimistic locking on revision publication (expected base revision -> 409)
5. Two-way approval invariant and atomic status synchronization
6. Tenant isolation on all queries and mutations
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from domain.evidence import (
    EvidenceProvenance,
    EvidenceRecord,
    EvidenceType,
    FreshnessKind,
    PlanRevisionEvidenceLink,
    UsageState,
    canonical_hash,
)
from domain.plan_approval import PlanApproval
from domain.plan_revision import PlanRevision


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


class EvidenceRevisionStore:
    """Store managing EvidenceRecords, PlanRevisions, Links, and PlanApprovals."""

    # In-memory stores for unit testing / memory mode
    _IN_MEMORY_EVIDENCE: dict[str, EvidenceRecord] = {}
    _IN_MEMORY_PROVENANCE: dict[str, EvidenceProvenance] = {}
    _IN_MEMORY_REVISIONS: dict[str, PlanRevision] = {}
    _IN_MEMORY_LINKS: list[PlanRevisionEvidenceLink] = []
    _IN_MEMORY_APPROVALS: dict[str, PlanApproval] = {}

    def __init__(self) -> None:
        self.use_in_memory = bool(os.getenv("USE_IN_MEMORY_AUTH"))

    def _connect(self):
        return psycopg.connect(_dsn(), row_factory=dict_row)

    # -------------------------------------------------------------
    # 1. Evidence Record Management
    # -------------------------------------------------------------
    def create_evidence(
        self,
        record: EvidenceRecord,
        provenance: EvidenceProvenance | None = None,
    ) -> EvidenceRecord:
        """Create an immutable evidence record and optional attach-once provenance."""
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            self._IN_MEMORY_EVIDENCE[str(record.evidence_id)] = record
            if provenance:
                self._IN_MEMORY_PROVENANCE[str(provenance.evidence_id)] = provenance
            return record

        with self._connect() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO evidence_records (
                        evidence_id, trip_id, user_id, evidence_type,
                        applicability_key, applicability_hash, content_hash,
                        freshness_kind, freshness_ttl_seconds, produced_at,
                        supplier_expires_at, source_agent, payload, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s::jsonb, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s::jsonb, %s
                    )
                    """,
                    (
                        str(record.evidence_id),
                        str(record.trip_id),
                        str(record.user_id),
                        record.evidence_type.value,
                        json.dumps(record.applicability_key),
                        record.applicability_hash,
                        record.content_hash,
                        record.freshness_kind.value,
                        record.freshness_ttl_seconds,
                        record.produced_at,
                        record.supplier_expires_at,
                        record.source_agent,
                        json.dumps(record.payload),
                        record.created_at,
                    ),
                )
                if provenance:
                    conn.execute(
                        """
                        INSERT INTO evidence_provenance (
                            evidence_id, trip_id, user_id,
                            supplier_session_id, supplier_snapshot_id, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(provenance.evidence_id),
                            str(provenance.trip_id),
                            str(provenance.user_id),
                            str(provenance.supplier_session_id) if provenance.supplier_session_id else None,
                            str(provenance.supplier_snapshot_id) if provenance.supplier_snapshot_id else None,
                            provenance.created_at,
                        ),
                    )
            return record

    def get_evidence(self, evidence_id: UUID | str, user_id: UUID | str) -> EvidenceRecord | None:
        """Fetch evidence record verifying tenant ownership."""
        eid_str = str(evidence_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            rec = self._IN_MEMORY_EVIDENCE.get(eid_str)
            if rec and str(rec.user_id) == uid_str:
                return rec
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evidence_records WHERE evidence_id = %s AND user_id = %s",
                (eid_str, uid_str),
            ).fetchone()
            if not row:
                return None
            return EvidenceRecord(
                evidence_id=UUID(str(row["evidence_id"])),
                trip_id=UUID(str(row["trip_id"])),
                user_id=UUID(str(row["user_id"])),
                evidence_type=EvidenceType(row["evidence_type"]),
                applicability_key=row["applicability_key"],
                applicability_hash=row["applicability_hash"],
                content_hash=row["content_hash"],
                freshness_kind=FreshnessKind(row["freshness_kind"]),
                freshness_ttl_seconds=int(row["freshness_ttl_seconds"]),
                produced_at=row["produced_at"],
                supplier_expires_at=row["supplier_expires_at"],
                source_agent=row["source_agent"],
                payload=row["payload"],
                created_at=row["created_at"],
            )

    def find_reusable_evidence(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        evidence_type: EvidenceType,
        applicability_key: dict[str, Any],
        now: datetime | None = None,
    ) -> EvidenceRecord | None:
        """Find an active, fresh evidence record matching the applicability key."""
        target_hash = canonical_hash(applicability_key)
        now_dt = now or datetime.now(timezone.utc)
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            for rec in self._IN_MEMORY_EVIDENCE.values():
                if (
                    str(rec.trip_id) == tid_str
                    and str(rec.user_id) == uid_str
                    and rec.evidence_type == evidence_type
                    and rec.applicability_hash == target_hash
                    and rec.is_fresh(now_dt)
                ):
                    return rec
            return None

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evidence_records
                WHERE trip_id = %s AND user_id = %s AND evidence_type = %s AND applicability_hash = %s
                ORDER BY produced_at DESC
                """,
                (tid_str, uid_str, evidence_type.value, target_hash),
            ).fetchall()

            for row in rows:
                rec = EvidenceRecord(
                    evidence_id=UUID(str(row["evidence_id"])),
                    trip_id=UUID(str(row["trip_id"])),
                    user_id=UUID(str(row["user_id"])),
                    evidence_type=EvidenceType(row["evidence_type"]),
                    applicability_key=row["applicability_key"],
                    applicability_hash=row["applicability_hash"],
                    content_hash=row["content_hash"],
                    freshness_kind=FreshnessKind(row["freshness_kind"]),
                    freshness_ttl_seconds=int(row["freshness_ttl_seconds"]),
                    produced_at=row["produced_at"],
                    supplier_expires_at=row["supplier_expires_at"],
                    source_agent=row["source_agent"],
                    payload=row["payload"],
                    created_at=row["created_at"],
                )
                if rec.is_fresh(now_dt):
                    return rec
            return None

    # -------------------------------------------------------------
    # 2. Plan Revision & Publication Management
    # -------------------------------------------------------------
    def get_revision(self, revision_id: UUID | str, user_id: UUID | str) -> PlanRevision | None:
        """Fetch plan revision verifying tenant ownership."""
        rid_str = str(revision_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            rev = self._IN_MEMORY_REVISIONS.get(rid_str)
            if rev and str(rev.user_id) == uid_str:
                return rev
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plan_revisions WHERE revision_id = %s AND user_id = %s",
                (rid_str, uid_str),
            ).fetchone()
            if not row:
                return None
            return PlanRevision(
                revision_id=UUID(str(row["revision_id"])),
                trip_id=UUID(str(row["trip_id"])),
                user_id=UUID(str(row["user_id"])),
                revision_number=int(row["revision_number"]),
                change_set=row["change_set"],
                plan_snapshot=row["plan_snapshot"],
                is_published=bool(row["is_published"]),
                published_at=row["published_at"],
                created_at=row["created_at"],
            )

    def list_revisions_for_trip(self, trip_id: UUID | str, user_id: UUID | str) -> list[PlanRevision]:
        """List all revisions for a trip ordered by revision_number ascending."""
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            revs = [
                r for r in self._IN_MEMORY_REVISIONS.values()
                if str(r.trip_id) == tid_str and str(r.user_id) == uid_str
            ]
            revs.sort(key=lambda x: x.revision_number)
            return revs

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_revisions WHERE trip_id = %s AND user_id = %s ORDER BY revision_number ASC",
                (tid_str, uid_str),
            ).fetchall()
            return [
                PlanRevision(
                    revision_id=UUID(str(r["revision_id"])),
                    trip_id=UUID(str(r["trip_id"])),
                    user_id=UUID(str(r["user_id"])),
                    revision_number=int(r["revision_number"]),
                    change_set=r["change_set"],
                    plan_snapshot=r["plan_snapshot"],
                    is_published=bool(r["is_published"]),
                    published_at=r["published_at"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def publish_revision_transaction(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        expected_base_revision: int | None,
        change_set: dict[str, Any],
        plan_snapshot: dict[str, Any],
        evidence_links: list[tuple[UUID | str, UsageState]] | None = None,
    ) -> PlanRevision:
        """Publish a new plan revision under parent trip row lock with optimistic concurrency."""
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)
        now = datetime.now(timezone.utc)
        links = evidence_links or []

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            trip = TripStore().get_trip(trip_id, user_id)
            if not trip:
                raise HTTPException(status_code=404, detail="Trip not found.")

            current_num = int(trip.get("current_revision_number") or 0)
            if expected_base_revision is not None and current_num != expected_base_revision:
                raise HTTPException(
                    status_code=409,
                    detail=f"Conflict: Expected base revision {expected_base_revision} but current is {current_num}.",
                )

            new_revision_number = current_num + 1
            new_rev_id = uuid4()
            rev = PlanRevision(
                revision_id=new_rev_id,
                trip_id=UUID(tid_str),
                user_id=UUID(uid_str),
                revision_number=new_revision_number,
                change_set=change_set,
                plan_snapshot=plan_snapshot,
                is_published=True,
                published_at=now,
                created_at=now,
            )
            self._IN_MEMORY_REVISIONS[str(new_rev_id)] = rev

            # Link evidence
            for eid, usage in links:
                link = PlanRevisionEvidenceLink(
                    link_id=uuid4(),
                    revision_id=new_rev_id,
                    evidence_id=UUID(str(eid)),
                    trip_id=UUID(tid_str),
                    user_id=UUID(uid_str),
                    usage_state=usage,
                    linked_at=now,
                )
                self._IN_MEMORY_LINKS.append(link)

            # Atomic trip pointer update: resets status to awaiting_approval
            TripStore().update_trip(
                trip_id,
                user_id,
                current_revision_id=str(new_rev_id),
                current_revision_number=new_revision_number,
                status="awaiting_approval",
            )
            return rev

        with self._connect() as conn:
            with conn.transaction():
                # 1. Universal parent trip row lock FOR UPDATE
                trip_row = conn.execute(
                    """
                    SELECT id, user_id, current_revision_id, current_revision_number, status
                    FROM trips
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (tid_str, uid_str),
                ).fetchone()

                if not trip_row:
                    raise HTTPException(status_code=404, detail="Trip not found.")

                current_num = int(trip_row["current_revision_number"] or 0)
                if expected_base_revision is not None and current_num != expected_base_revision:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Conflict: Expected base revision {expected_base_revision} but current is {current_num}.",
                    )

                new_revision_number = current_num + 1
                new_rev_id = uuid4()

                # 2. Insert new revision directly as published
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
                        json.dumps(change_set),
                        json.dumps(plan_snapshot),
                        now,
                        now,
                    ),
                )

                # 3. Insert evidence links
                for eid, usage in links:
                    conn.execute(
                        """
                        INSERT INTO plan_revision_evidence_links (
                            link_id, revision_id, evidence_id, trip_id, user_id, usage_state, linked_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid4()),
                            str(new_rev_id),
                            str(eid),
                            tid_str,
                            uid_str,
                            usage.value,
                            now,
                        ),
                    )

                # 4. Atomic trip pointer advance + status reset to awaiting_approval
                conn.execute(
                    """
                    UPDATE trips
                    SET current_revision_id = %s,
                        current_revision_number = %s,
                        status = 'awaiting_approval',
                        updated_at = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (str(new_rev_id), new_revision_number, now, tid_str, uid_str),
                )

            return PlanRevision(
                revision_id=new_rev_id,
                trip_id=UUID(tid_str),
                user_id=UUID(uid_str),
                revision_number=new_revision_number,
                change_set=change_set,
                plan_snapshot=plan_snapshot,
                is_published=True,
                published_at=now,
                created_at=now,
            )

    # -------------------------------------------------------------
    # 3. Plan Approval & Revocation Management
    # -------------------------------------------------------------
    def approve_revision_transaction(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        revision_id: UUID | str,
        metadata: dict[str, Any] | None = None,
    ) -> PlanApproval:
        """Approve trip revision under parent trip row lock."""
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)
        rid_str = str(revision_id)
        now = datetime.now(timezone.utc)
        meta = metadata or {}

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            trip = TripStore().get_trip(trip_id, user_id)
            if not trip:
                raise HTTPException(status_code=404, detail="Trip not found.")
            if str(trip.get("current_revision_id")) != rid_str:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot approve revision {rid_str}: not current revision ({trip.get('current_revision_id')}).",
                )

            appr_id = uuid4()
            approval = PlanApproval(
                approval_id=appr_id,
                trip_id=UUID(tid_str),
                revision_id=UUID(rid_str),
                approved_by_user_id=UUID(uid_str),
                approved_at=now,
                approval_metadata=meta,
            )
            self._IN_MEMORY_APPROVALS[str(appr_id)] = approval
            TripStore().update_trip(trip_id, user_id, status="approved")
            return approval

        with self._connect() as conn:
            with conn.transaction():
                # 1. Universal parent trip lock FOR UPDATE
                trip_row = conn.execute(
                    """
                    SELECT id, user_id, current_revision_id, status
                    FROM trips
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """,
                    (tid_str, uid_str),
                ).fetchone()

                if not trip_row:
                    raise HTTPException(status_code=404, detail="Trip not found.")

                if str(trip_row["current_revision_id"]) != rid_str:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot approve revision {rid_str}: not current revision ({trip_row['current_revision_id']}).",
                    )

                appr_id = uuid4()
                # 2. Insert approval
                conn.execute(
                    """
                    INSERT INTO plan_approvals (
                        approval_id, trip_id, revision_id, approved_by_user_id, approved_at, approval_metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (str(appr_id), tid_str, rid_str, uid_str, now, json.dumps(meta)),
                )

                # 3. Update status to approved
                conn.execute(
                    """
                    UPDATE trips
                    SET status = 'approved', updated_at = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (now, tid_str, uid_str),
                )

            return PlanApproval(
                approval_id=appr_id,
                trip_id=UUID(tid_str),
                revision_id=UUID(rid_str),
                approved_by_user_id=UUID(uid_str),
                approved_at=now,
                approval_metadata=meta,
            )

    def revoke_approval_transaction(
        self,
        trip_id: UUID | str,
        user_id: UUID | str,
        revision_id: UUID | str | None = None,
    ) -> bool:
        """Revoke approval under trip row lock, demoting trip status to awaiting_approval."""
        from services.trip_store import TripStore

        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            trip = TripStore().get_trip(trip_id, user_id)
            if not trip:
                return False

            target_rid = str(revision_id) if revision_id else str(trip.get("current_revision_id"))
            deleted = False
            for aid, a in list(self._IN_MEMORY_APPROVALS.items()):
                if str(a.trip_id) == tid_str and str(a.revision_id) == target_rid:
                    del self._IN_MEMORY_APPROVALS[aid]
                    deleted = True

            if deleted:
                if str(trip.get("current_revision_id")) == target_rid and trip.get("status") == "approved":
                    TripStore().update_trip(trip_id, user_id, status="awaiting_approval")
            return deleted

        with self._connect() as conn:
            with conn.transaction():
                # In PostgreSQL, BEFORE DELETE trigger trg_on_plan_approval_deleted
                # acquires trip FOR UPDATE and demotes status to awaiting_approval
                if revision_id:
                    row = conn.execute(
                        "DELETE FROM plan_approvals WHERE trip_id = %s AND revision_id = %s RETURNING approval_id",
                        (tid_str, str(revision_id)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        DELETE FROM plan_approvals a
                        USING trips t
                        WHERE a.trip_id = t.id AND t.id = %s AND t.user_id = %s AND a.revision_id = t.current_revision_id
                        RETURNING a.approval_id
                        """,
                        (tid_str, uid_str),
                    ).fetchone()
                return bool(row)

    # -------------------------------------------------------------
    # 4. Revision Pruning & Deletion
    # -------------------------------------------------------------
    def delete_revision(
        self,
        revision_id: UUID | str,
        trip_id: UUID | str,
        user_id: UUID | str,
    ) -> bool:
        """Delete a plan revision with trip-first locking and deterministic demotion if current."""
        from services.trip_store import TripStore

        rid_str = str(revision_id)
        tid_str = str(trip_id)
        uid_str = str(user_id)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            trip = TripStore().get_trip(trip_id, user_id)
            if not trip:
                return False

            rev = self._IN_MEMORY_REVISIONS.get(rid_str)
            if not rev or str(rev.trip_id) != tid_str or str(rev.user_id) != uid_str:
                return False

            del self._IN_MEMORY_REVISIONS[rid_str]
            # Cascade delete approvals for this revision
            for aid, a in list(self._IN_MEMORY_APPROVALS.items()):
                if str(a.revision_id) == rid_str:
                    del self._IN_MEMORY_APPROVALS[aid]

            # If current revision was deleted, reset pointer and demote
            if str(trip.get("current_revision_id")) == rid_str:
                TripStore().update_trip(
                    trip_id,
                    user_id,
                    current_revision_id=None,
                    current_revision_number=0,
                    status="awaiting_approval",
                )
            return True

        with self._connect() as conn:
            with conn.transaction():
                # In PostgreSQL, trg_lock_trip_before_plan_revision_delete fires BEFORE DELETE,
                # locks trips FOR UPDATE, approval cascades, and ON DELETE SET NULL on trips fires
                row = conn.execute(
                    "DELETE FROM plan_revisions WHERE revision_id = %s AND trip_id = %s AND user_id = %s RETURNING revision_id",
                    (rid_str, tid_str, uid_str),
                ).fetchone()
                return bool(row)
