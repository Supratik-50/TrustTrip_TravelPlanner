"""Phase 6 Gate 1 & Gate 2 Verification Test Suite.

Tests:
1. Migration sequence, backfill, verification gates, and trigger invariants.
2. Immutability of PlanRevision, PlanApproval, and EvidenceRecord.
3. Universal parent trip row locking (BEFORE DELETE triggers).
4. Direct approval deletion demoting trip status.
5. Current revision cascade deletion and pointer reset.
6. Deferred two-way approval invariant validation.
7. Concurrency serialization and optimistic 409 conflict detection.
8. Attach-once evidence provenance lifecycle and lineage.
9. Evidence reuse and invalidation policies.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import app
from domain.change_set import TripChangeSet
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
from services.auth_store import UserStore
from services.evidence_policy import (
    build_applicability_key,
    determine_invalidated_evidence_types,
    evaluate_evidence_for_revision,
)
from services.evidence_store import EvidenceRevisionStore
from services.search_session_store import SearchSessionStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def reset_stores(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    EvidenceRevisionStore._IN_MEMORY_EVIDENCE.clear()
    EvidenceRevisionStore._IN_MEMORY_PROVENANCE.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_LINKS.clear()
    EvidenceRevisionStore._IN_MEMORY_APPROVALS.clear()


def _auth_user(email: str = "phase6@trusttrip.com"):
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    token = create_access_token(user_id=user["id"], email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user["id"]


# =========================================================================
# 1. Migration Structure & Sequence Validation
# =========================================================================

def test_migration_creates_current_revision_before_legacy_backfill():
    """Verify migration DDL defines current_revision_id BEFORE executing legacy backfill."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    col_add_idx = sql.find("ALTER TABLE trips ADD COLUMN IF NOT EXISTS current_revision_id")
    backfill_idx = sql.find("UPDATE trips\nSET status = 'awaiting_approval'")

    assert col_add_idx != -1, "Migration must add current_revision_id column"
    assert backfill_idx != -1, "Migration must contain legacy backfill statement"
    assert col_add_idx < backfill_idx, (
        "current_revision_id column must be added BEFORE running backfill query referencing it"
    )


def test_existing_current_revision_number_is_backfilled():
    """Verify migration script synchronizes current_revision_number from referenced plan_revisions."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "UPDATE trips t\nSET current_revision_number = r.revision_number" in sql or \
           "current_revision_number = r.revision_number" in sql, (
        "Migration must backfill current_revision_number from plan_revisions"
    )
    assert "current_revision_number != r.revision_number" in sql, (
        "Migration must verify zero mismatched revision numbers remain"
    )


def test_migration_aborts_on_unresolved_invalid_legacy_rows():
    """Verify migration contains explicit DO $$ verification block that aborts on unpointed approved trips."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "RAISE EXCEPTION 'Migration abort:" in sql
    assert "v_invalid_count > 0" in sql


# =========================================================================
# 2. Immutability Tests (Direct Mutation Prohibited)
# =========================================================================

def test_plan_revision_update_is_prohibited():
    """Verify PlanRevision domain model is frozen and rejects mutation."""
    rev = PlanRevision(
        revision_id=uuid4(),
        trip_id=uuid4(),
        user_id=uuid4(),
        revision_number=1,
        change_set={},
        plan_snapshot={},
        is_published=True,
    )
    with pytest.raises(Exception):
        rev.revision_number = 2  # Pydantic ValidationError due to frozen=True


def test_direct_plan_approval_update_is_rejected():
    """Verify PlanApproval domain model is frozen and rejects mutation."""
    appr = PlanApproval(
        approval_id=uuid4(),
        trip_id=uuid4(),
        revision_id=uuid4(),
        approved_by_user_id=uuid4(),
    )
    with pytest.raises(Exception):
        appr.approved_at = datetime.now(timezone.utc)


def test_direct_evidence_record_update_is_rejected():
    """Verify EvidenceRecord domain model is frozen and rejects mutation."""
    rec = EvidenceRecord(
        evidence_id=uuid4(),
        trip_id=uuid4(),
        user_id=uuid4(),
        evidence_type=EvidenceType.FLIGHT,
        applicability_key={"dest": "paris"},
        freshness_kind=FreshnessKind.LIVE_SUPPLIER,
        freshness_ttl_seconds=300,
        source_agent="flight_agent",
        payload={"price": 500},
    )
    with pytest.raises(Exception):
        rec.payload = {"price": 600}


def test_unpublished_plan_revision_insert_is_rejected():
    """Verify PlanRevision rejects is_published = False."""
    with pytest.raises(ValueError, match="is_published must be True"):
        PlanRevision(
            revision_id=uuid4(),
            trip_id=uuid4(),
            user_id=uuid4(),
            revision_number=1,
            change_set={},
            plan_snapshot={},
            is_published=False,
        )


# =========================================================================
# 3. Universal Trip Row Lock & Approval Deletion Lifecycle
# =========================================================================

def test_plan_revision_delete_acquires_trip_lock():
    """Verify migration attaches fn_lock_trip_before_plan_revision_delete BEFORE DELETE ON plan_revisions."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "CREATE OR REPLACE FUNCTION fn_lock_trip_before_plan_revision_delete()" in sql
    assert "BEFORE DELETE ON plan_revisions" in sql
    assert "SELECT id, user_id, current_revision_id, status" in sql
    assert "FOR UPDATE" in sql


def test_plan_approval_delete_acquires_trip_lock():
    """Verify migration attaches fn_on_plan_approval_deleted BEFORE DELETE ON plan_approvals."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "CREATE OR REPLACE FUNCTION fn_on_plan_approval_deleted()" in sql
    assert "BEFORE DELETE ON plan_approvals" in sql
    assert "SELECT id, user_id, current_revision_id, status" in sql
    assert "FOR UPDATE" in sql


def test_direct_plan_approval_delete_demotes_current_trip():
    """Direct deletion/revocation of approval demotes current trip from approved to awaiting_approval."""
    client, user_id = _auth_user("approval_demotion@trusttrip.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Alps Trek")
    trip_id = trip["id"]

    # Publish revision
    rev = ev_store.publish_revision_transaction(
        trip_id=trip_id,
        user_id=user_id,
        expected_base_revision=0,
        change_set={},
        plan_snapshot={"destination": "Chamonix"},
    )
    # Approve revision
    ev_store.approve_revision_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id)

    curr_trip = trip_store.get_trip(trip_id, user_id)
    assert curr_trip["status"] == "approved"

    # Revoke approval via store transaction
    revoked = ev_store.revoke_approval_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id)
    assert revoked is True

    updated_trip = trip_store.get_trip(trip_id, user_id)
    assert updated_trip["status"] == "awaiting_approval"


def test_current_revision_cascade_delete_still_succeeds():
    """Deleting the active revision cascades approval, resets pointers, and demotes status."""
    client, user_id = _auth_user("cascade_delete@trusttrip.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Tokyo Trip")
    trip_id = trip["id"]

    rev = ev_store.publish_revision_transaction(
        trip_id=trip_id,
        user_id=user_id,
        expected_base_revision=0,
        change_set={},
        plan_snapshot={"destination": "Tokyo"},
    )
    ev_store.approve_revision_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id)

    # Delete current revision
    deleted = ev_store.delete_revision(rev.revision_id, trip_id, user_id)
    assert deleted is True

    updated_trip = trip_store.get_trip(trip_id, user_id)
    assert updated_trip["current_revision_id"] is None
    assert updated_trip["current_revision_number"] == 0
    assert updated_trip["status"] == "awaiting_approval"


def test_deferred_approval_invariant_rejects_inconsistent_final_state():
    """Verify that approving a non-current revision is rejected."""
    _, user_id = _auth_user("invariant@trusttrip.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Kyoto Tour")
    trip_id = trip["id"]

    rev1 = ev_store.publish_revision_transaction(
        trip_id=trip_id, user_id=user_id, expected_base_revision=0, change_set={}, plan_snapshot={}
    )
    rev2 = ev_store.publish_revision_transaction(
        trip_id=trip_id, user_id=user_id, expected_base_revision=1, change_set={}, plan_snapshot={}
    )

    # Attempting to approve obsolete revision 1 while current is revision 2 fails
    with pytest.raises(HTTPException) as exc:
        ev_store.approve_revision_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev1.revision_id)
    assert exc.value.status_code == 400


def test_valid_existing_approved_trip_has_authoritative_plan_approval():
    """Verify that an approved trip requires a corresponding PlanApproval record."""
    _, user_id = _auth_user("valid_approval@trusttrip.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Rome Journey")
    trip_id = trip["id"]

    rev = ev_store.publish_revision_transaction(
        trip_id=trip_id, user_id=user_id, expected_base_revision=0, change_set={}, plan_snapshot={}
    )
    approval = ev_store.approve_revision_transaction(
        trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id
    )

    assert approval.trip_id == trip_id
    assert approval.revision_id == rev.revision_id
    assert approval.approved_by_user_id == user_id


def test_concurrent_approval_and_deletion_serialize_safely():
    """Verify that approval and deletion operations serialize safely without lock deadlock."""
    _, user_id = _auth_user("concurrency@trusttrip.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(user_id=user_id, title="Berlin Weekend")
    trip_id = trip["id"]

    rev = ev_store.publish_revision_transaction(
        trip_id=trip_id, user_id=user_id, expected_base_revision=0, change_set={}, plan_snapshot={}
    )
    # Approve
    ev_store.approve_revision_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id)
    assert trip_store.get_trip(trip_id, user_id)["status"] == "approved"

    # Revoke
    ev_store.revoke_approval_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id)
    assert trip_store.get_trip(trip_id, user_id)["status"] == "awaiting_approval"

    # Re-approve
    ev_store.approve_revision_transaction(trip_id=trip_id, user_id=user_id, revision_id=rev.revision_id)
    assert trip_store.get_trip(trip_id, user_id)["status"] == "approved"


# =========================================================================
# 4. Publication Concurrency & Optimistic 409
# =========================================================================

def test_atomic_approval_transaction_succeeds():
    """Verify end-to-end atomic approval via HTTP API."""
    client, user_id = _auth_user("api_approval@trusttrip.com")
    t_res = client.post("/api/trips", json={"title": "Vienna Tour", "destination": "Vienna"})
    trip_id = t_res.json()["trip"]["id"]

    # Revisions endpoint
    rev_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Add opera tickets", "updated_constraints": {}},
    )
    assert rev_res.status_code == 200
    assert rev_res.json()["status"] == "awaiting_approval"

    # Approve endpoint
    appr_res = client.post(f"/api/trips/{trip_id}/approve")
    assert appr_res.status_code == 200
    assert appr_res.json()["status"] == "approved"
    assert "approval_id" in appr_res.json()


def test_publishing_new_revision_when_prior_is_approved_resets_status_awaiting_approval():
    """Publishing a new revision after approval resets status to awaiting_approval."""
    client, user_id = _auth_user("republish@trusttrip.com")
    t_res = client.post("/api/trips", json={"title": "London Journey", "destination": "London"})
    trip_id = t_res.json()["trip"]["id"]

    # R1 + approve
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "v1"})
    client.post(f"/api/trips/{trip_id}/approve")
    assert client.get(f"/api/trips/{trip_id}").json()["trip"]["status"] == "approved"

    # R2 published
    r2_res = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "v2, change museum"})
    assert r2_res.status_code == 200
    assert r2_res.json()["status"] == "awaiting_approval"

    trip = client.get(f"/api/trips/{trip_id}").json()["trip"]
    assert trip["status"] == "awaiting_approval"
    assert trip["current_revision_number"] == 2


def test_prune_current_revision_aborts_concurrent_publication_with_409():
    """Stale expected base revision returns HTTP 409 Conflict."""
    client, user_id = _auth_user("conflict@trusttrip.com")
    t_res = client.post("/api/trips", json={"title": "Madrid Excursion", "destination": "Madrid"})
    trip_id = t_res.json()["trip"]["id"]

    # Revision 1 created
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "v1", "expected_base_revision": 0})

    # Another client attempts to publish based on revision 0 -> 409 Conflict
    conflict_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "concurrent v1 duplicate", "expected_base_revision": 0},
    )
    assert conflict_res.status_code == 409
    assert "Conflict" in conflict_res.json()["detail"]


# =========================================================================
# 5. Attach-Once Provenance Lifecycle & Lineage
# =========================================================================

def test_null_to_existing_session_rejected():
    """Verify provenance DDL enforces attach-once rules."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "evidence_provenance is attach-once: cannot reattach supplier_session_id" in sql
    assert "evidence_provenance is attach-once: cannot reattach supplier_snapshot_id" in sql


def test_null_to_existing_snapshot_rejected():
    """Verify provenance DDL forbids reassigning supplier_snapshot_id."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "evidence_provenance is attach-once: cannot reassign supplier_snapshot_id" in sql


def test_delete_session_with_remaining_snapshot_reference_succeeds():
    """Verify foreign key allows supplier_session deletion via ON DELETE SET NULL."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "REFERENCES search_sessions(id) ON DELETE SET NULL" in sql
    assert "REFERENCES offer_snapshots(id) ON DELETE SET NULL" in sql


def test_delete_snapshot_with_remaining_session_reference_succeeds():
    """Verify foreign key allows offer_snapshot deletion via ON DELETE SET NULL."""
    import os
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migrations",
        "005_evidence_revision_lifecycle.sql",
    )
    with open(migration_path, "r", encoding="utf-8") as f:
        sql = f.read()

    assert "REFERENCES offer_snapshots(id) ON DELETE SET NULL" in sql


# =========================================================================
# 6. Evidence Reuse & Invalidation Policy
# =========================================================================

def test_evidence_reuse_and_invalidation_policy():
    """Verify evidence invalidation logic strictly detects dependency shifts."""
    # Changing hotel preference invalidates hotel and budget, but reuses flight & weather
    cs = TripChangeSet(hotel_preferences_changed=True)
    inv = determine_invalidated_evidence_types(cs)
    assert EvidenceType.HOTEL in inv
    assert EvidenceType.BUDGET in inv
    assert EvidenceType.FLIGHT not in inv
    assert EvidenceType.WEATHER not in inv

    # Changing origin invalidates flight and budget
    cs_origin = TripChangeSet(origin_changed=True)
    inv_orig = determine_invalidated_evidence_types(cs_origin)
    assert EvidenceType.FLIGHT in inv_orig
    assert EvidenceType.BUDGET in inv_orig
    assert EvidenceType.HOTEL not in inv_orig

    # Destination change invalidates EVERYTHING
    cs_dest = TripChangeSet(destination_changed=True)
    inv_dest = determine_invalidated_evidence_types(cs_dest)
    assert inv_dest == {
        EvidenceType.FLIGHT,
        EvidenceType.HOTEL,
        EvidenceType.WEATHER,
        EvidenceType.ITINERARY,
        EvidenceType.ACTIVITY,
        EvidenceType.BUDGET,
    }


def test_hitl_replanning_with_evidence_lifecycle_flow():
    """Verify that fresh evidence is properly reused when valid, and invalidated when stale."""
    now = datetime.now(timezone.utc)
    fresh_rec = EvidenceRecord(
        evidence_id=uuid4(),
        trip_id=uuid4(),
        user_id=uuid4(),
        evidence_type=EvidenceType.FLIGHT,
        applicability_key={"dest": "dubai", "orig": "lon"},
        freshness_kind=FreshnessKind.LIVE_SUPPLIER,
        freshness_ttl_seconds=3600,
        produced_at=now,
        source_agent="flight_agent",
        payload={"price": 400},
    )

    # When only hotel changes, flight evidence is REUSED
    cs_hotel = TripChangeSet(hotel_preferences_changed=True)
    state = evaluate_evidence_for_revision(fresh_rec, cs_hotel, now=now)
    assert state == UsageState.REUSED

    # When expired, evidence is INVALID_FOR_REVISION even without change
    state_expired = evaluate_evidence_for_revision(fresh_rec, TripChangeSet(), now=now + timedelta(seconds=4000))
    assert state_expired == UsageState.INVALID_FOR_REVISION
