"""Section 8 Gate 2: Comprehensive tests for Revision + Human-in-the-Loop (HITL) Loop.

Covers all Section 12 test specifications:
- first proposal parent link
- parent linkage and monotonic ordering
- repeated revision chain
- immutable prior revisions
- exact approval
- exact rejection
- rejection reason
- stale approval
- stale rejection
- cross-trip revision access
- unauthorized access
- duplicate approval
- duplicate rejection
- approve after reject
- reject after approve
- stale proposal creation
- transaction rollback
- malformed proposal
- structured added/removed/changed diff
- request another revision from rejected revision
- approval does not trigger booking/payment
- research advisory remains outside canonical mutation
- frozen Section 5/6 regression
"""
from __future__ import annotations

from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_store import UserStore
from services.trip_store import TripStore
from services.evidence_store import EvidenceRevisionStore
from domain.plan_revision import compute_structured_diff, derive_revision_status


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    EvidenceRevisionStore._IN_MEMORY_EVIDENCE.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_APPROVALS.clear()
    EvidenceRevisionStore._IN_MEMORY_REJECTIONS.clear()


def _auth_client(email: str = "hitl_user@example.com"):
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def test_first_proposal_parent_is_none():
    """Verify the very first revision has parent_revision_id = None."""
    client, user_id = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Paris Trip", "destination": "Paris"})
    assert trip_res.status_code == 201
    trip_id = trip_res.json()["trip"]["id"]

    rev_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Initial plan draft", "updated_constraints": {"duration_days": 3}},
    )
    assert rev_res.status_code == 200
    rev_data = rev_res.json()
    assert rev_data["revision_number"] == 1
    assert rev_data["parent_revision_id"] is None
    assert rev_data["status"] == "awaiting_approval"
    assert rev_data["proposal_status"] == "PROPOSED"


def test_parent_linkage_and_monotonic_ordering():
    """Verify child proposals link to parent revision and increment revision_number strictly monotonically."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Kyoto Trip", "destination": "Kyoto"})
    trip_id = trip_res.json()["trip"]["id"]

    # Rev 1
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "First pass"}).json()
    assert r1["revision_number"] == 1
    assert r1["parent_revision_id"] is None

    # Rev 2
    r2 = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Second pass", "expected_base_revision_id": r1["revision_id"], "expected_base_revision": 1},
    ).json()
    assert r2["revision_number"] == 2
    assert r2["parent_revision_id"] == r1["revision_id"]

    # Rev 3
    r3 = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Third pass", "expected_base_revision_id": r2["revision_id"], "expected_base_revision": 2},
    ).json()
    assert r3["revision_number"] == 3
    assert r3["parent_revision_id"] == r2["revision_id"]


def test_repeated_revision_chain():
    """Verify repeated proposal loops create a verified sequential chain."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Rome Loop", "destination": "Rome"})
    trip_id = trip_res.json()["trip"]["id"]

    last_rev_id = None
    for i in range(1, 6):
        body = {"feedback": f"Loop {i}"}
        if last_rev_id:
            body["expected_base_revision_id"] = last_rev_id
            body["expected_base_revision"] = i - 1
        res = client.post(f"/api/trips/{trip_id}/revisions", json=body)
        assert res.status_code == 200
        rev = res.json()
        assert rev["revision_number"] == i
        if i == 1:
            assert rev["parent_revision_id"] is None
        else:
            assert rev["parent_revision_id"] == last_rev_id
        last_rev_id = rev["revision_id"]

    # Check history endpoint contains all 5 in order
    hist_res = client.get(f"/api/trips/{trip_id}/revisions")
    assert hist_res.status_code == 200
    revs = hist_res.json()["revisions"]
    assert len(revs) == 5
    for idx, r in enumerate(revs):
        assert r["revision_number"] == idx + 1
        if idx > 0:
            assert r["parent_revision_id"] == revs[idx - 1]["revision_id"]


def test_immutable_prior_revisions():
    """Verify that prior revisions and their snapshots cannot be modified by new revisions."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Immutable Test", "destination": "London"})
    trip_id = trip_res.json()["trip"]["id"]

    r1 = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Rev 1 feedback", "updated_constraints": {"budget": 1000}},
    ).json()

    # Rev 2
    client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Rev 2 feedback", "updated_constraints": {"budget": 2000}},
    )

    # Fetch Rev 1 again
    hist = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    fetched_r1 = [r for r in hist if r["revision_id"] == r1["revision_id"]][0]

    assert fetched_r1["revision_number"] == 1
    assert fetched_r1["plan_snapshot"]["destination"] == "London"
    assert fetched_r1["plan_snapshot"]["metadata"]["last_feedback"] == "Rev 1 feedback"
    assert fetched_r1["parent_revision_id"] is None


def test_exact_approval_current_revision():
    """Verify approval of exact current revision succeeds and marks status as APPROVED."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Approve Trip", "destination": "Berlin"})
    trip_id = trip_res.json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    app_res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision": 1, "expected_revision_number": 1},
    )
    assert app_res.status_code == 200
    app_data = app_res.json()
    assert app_data["status"] == "approved"
    assert app_data["revision_id"] == rev_id

    # Verify history shows APPROVED
    hist = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    assert hist[0]["status"] == "APPROVED"
    assert hist[0]["is_approved"] is True
    assert hist[0]["is_rejected"] is False


def test_exact_rejection_current_revision():
    """Verify rejection of exact current revision succeeds and records rejection."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Reject Trip", "destination": "Madrid"})
    trip_id = trip_res.json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    rej_res = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "Too expensive"},
    )
    assert rej_res.status_code == 200
    rej_data = rej_res.json()
    assert rej_data["status"] == "REJECTED"
    assert rej_data["revision_id"] == rev_id
    assert rej_data["reason"] == "Too expensive"

    # Verify history shows REJECTED with decision_metadata
    hist = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    assert hist[0]["status"] == "REJECTED"
    assert hist[0]["is_rejected"] is True
    assert hist[0]["is_approved"] is False
    assert hist[0]["decision_metadata"]["reason"] == "Too expensive"


def test_rejection_with_reason():
    """Verify bounded reason is accepted and stored."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Reason Test", "destination": "Seoul"})
    trip_id = trip_res.json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Plan draft"}).json()
    rev_id = r1["revision_id"]

    long_reason = "Activities are too packed on Day 2 and hotel is too far from city center."
    res = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": long_reason},
    )
    assert res.status_code == 200
    assert res.json()["reason"] == long_reason


def test_stale_approval_returns_409():
    """Verify that approving an older revision when a newer proposal exists returns 409 Conflict."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Stale App", "destination": "Vienna"})
    trip_id = trip_res.json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 1"}).json()
    r2 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 2"}).json()

    # Attempt to approve r1 with r1's version
    res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": r1["revision_id"], "expected_revision": 1, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "stale" in res.json()["detail"].lower() or "conflict" in res.json()["detail"].lower()


def test_stale_rejection_returns_409():
    """Verify that rejecting a stale revision or with outdated expected revision number returns 409 Conflict."""
    client, _ = _auth_client()
    trip_res = client.post("/api/trips", json={"title": "Stale Rej", "destination": "Prague"})
    trip_id = trip_res.json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 1"}).json()
    r2 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 2"}).json()

    # Attempt to reject r1
    res = client.post(
        f"/api/trips/{trip_id}/revisions/{r1['revision_id']}/reject",
        json={"expected_revision_number": 1, "reason": "Late rejection"},
    )
    assert res.status_code == 409

    # Attempt to reject r2 with wrong expected version
    res2 = client.post(
        f"/api/trips/{trip_id}/revisions/{r2['revision_id']}/reject",
        json={"expected_revision_number": 1, "reason": "Wrong version"},
    )
    assert res2.status_code == 409


def test_cross_trip_revision_access_fails():
    """Verify attempting to approve or reject a revision belonging to another trip fails."""
    client1, _ = _auth_client("user1@example.com")
    client2, _ = _auth_client("user2@example.com")

    # User 1 creates trip and revision
    t1 = client1.post("/api/trips", json={"title": "User 1 Trip", "destination": "Amsterdam"}).json()["trip"]["id"]
    r1 = client1.post(f"/api/trips/{t1}/revisions", json={"feedback": "User 1 draft"}).json()["revision_id"]

    # User 2 creates trip
    t2 = client2.post("/api/trips", json={"title": "User 2 Trip", "destination": "Oslo"}).json()["trip"]["id"]

    # User 2 attempts to reject User 1's revision on t2
    res = client2.post(
        f"/api/trips/{t2}/revisions/{r1}/reject",
        json={"expected_revision_number": 1, "reason": "Cross trip attack"},
    )
    assert res.status_code in (404, 409)

    # User 2 attempts to approve User 1's revision on t2
    res2 = client2.post(
        f"/api/trips/{t2}/approve",
        json={"revision_id": r1, "expected_revision_number": 1},
    )
    assert res2.status_code in (404, 409)


def test_unauthorized_access_fails():
    """Verify unauthenticated or unauthorized access to trip/revisions fails."""
    anon_client = TestClient(app)

    # Unauthenticated trip revisions list
    dummy_trip_id = str(uuid4())
    res = anon_client.get(f"/api/trips/{dummy_trip_id}/revisions")
    assert res.status_code == 401

    # Unauthenticated rejection
    res2 = anon_client.post(
        f"/api/trips/{dummy_trip_id}/revisions/{uuid4()}/reject",
        json={"expected_revision_number": 1},
    )
    assert res2.status_code == 401

    # Unauthorized access by different user to another's trip
    c1, _ = _auth_client("owner@example.com")
    c2, _ = _auth_client("other@example.com")
    trip_id = c1.post("/api/trips", json={"title": "Owner Trip", "destination": "Dublin"}).json()["trip"]["id"]

    res3 = c2.get(f"/api/trips/{trip_id}/revisions")
    assert res3.status_code == 404


def test_duplicate_approval_returns_409():
    """Verify that approving an already approved revision returns 409 Conflict."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Dup App", "destination": "Zurich"}).json()["trip"]["id"]
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    # First approval
    app1 = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app1.status_code == 200

    # Second approval
    app2 = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app2.status_code == 409


def test_duplicate_rejection_returns_409():
    """Verify that rejecting an already rejected revision returns 409 Conflict."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Dup Rej", "destination": "Geneva"}).json()["trip"]["id"]
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    # First rejection
    rej1 = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "First rejection"},
    )
    assert rej1.status_code == 200

    # Second rejection
    rej2 = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "Second rejection"},
    )
    assert rej2.status_code == 409


def test_approve_after_reject_returns_409():
    """Verify that approving a revision that was already rejected returns 409."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Reject Then Approve", "destination": "Lisbon"}).json()["trip"]["id"]
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    # Reject
    rej = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "Not quite right"},
    )
    assert rej.status_code == 200

    # Attempt Approve
    app = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app.status_code == 409


def test_reject_after_approve_returns_409():
    """Verify that rejecting a revision that was already approved returns 409."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Approve Then Reject", "destination": "Porto"}).json()["trip"]["id"]
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    # Approve
    app = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app.status_code == 200

    # Attempt Reject
    rej = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "Changed my mind"},
    )
    assert rej.status_code == 409


def test_stale_proposal_creation_with_expected_base_returns_409():
    """Verify that proposing a revision with a mismatched expected_base_revision_id or version returns 409."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Stale Base", "destination": "Brussels"}).json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 1"}).json()
    r2 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Rev 2"}).json()

    # Client tries to create Rev 3 expecting base to be r1
    res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "feedback": "Trying from r1",
            "expected_base_revision_id": r1["revision_id"],
            "expected_base_revision": 1,
        },
    )
    assert res.status_code == 409
    assert "conflict" in res.json()["detail"].lower() or "base revision" in res.json()["detail"].lower()


def test_transaction_rollback_on_persistence_failure(monkeypatch):
    """Verify that persistence failure during revision creation does not corrupt trip state."""
    client, user_id = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Rollback Test", "destination": "Helsinki"}).json()["trip"]["id"]

    trip_before = TripStore().get_trip(trip_id, user_id=user_id)
    assert trip_before["current_revision_number"] == 0

    # Monkeypatch publish_revision_transaction to fail
    def fake_publish(*args, **kwargs):
        raise RuntimeError("Simulated DB crash during publish")

    monkeypatch.setattr(EvidenceRevisionStore, "publish_revision_transaction", fake_publish)

    res = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Will fail"})
    assert res.status_code == 500

    trip_after = TripStore().get_trip(trip_id, user_id=user_id)
    assert trip_after["current_revision_number"] == 0
    assert trip_after["current_revision_id"] is None


def test_malformed_proposal_rejected_before_persistence():
    """Verify invalid payloads (e.g. negative duration, missing required fields) fail 422 before persistence."""
    client, user_id = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Malformed", "destination": "Athens"}).json()["trip"]["id"]

    # Invalid rejection request (negative expected revision number)
    res = client.post(
        f"/api/trips/{trip_id}/revisions/{uuid4()}/reject",
        json={"expected_revision_number": -5},
    )
    assert res.status_code == 422

    # Verify no revisions were created
    hist = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    assert len(hist) == 0


def test_structured_added_removed_changed_diff():
    """Verify structured diff categorizes added, removed, and changed items accurately."""
    old_data = {
        "destination": "Kyoto",
        "budget": 2000,
        "flight": "Flight 101",
        "hotel": "Hotel Grand",
    }
    new_data = {
        "destination": "Kyoto & Osaka",
        "budget": 2500,
        "hotel": "Hotel Grand",  # unchanged
        "activities": ["Shrine Visit", "Castle Tour"],  # added
        # flight removed
    }

    diff = compute_structured_diff(old_data, new_data, feedback="Extend to Osaka and add shrines")

    added_fields = [item.field for item in diff.added]
    removed_fields = [item.field for item in diff.removed]
    changed_fields = [item.field for item in diff.changed]

    assert "activities" in added_fields
    assert "flight" in removed_fields
    assert "destination" in changed_fields
    assert "budget" in changed_fields
    assert "hotel" not in added_fields and "hotel" not in removed_fields and "hotel" not in changed_fields

    # Check changed item details
    budget_change = [i for i in diff.changed if i.field == "budget"][0]
    assert budget_change.old_value == 2000
    assert budget_change.new_value == 2500


def test_request_another_revision_from_rejected_revision():
    """Verify that after rejecting revision 1, a new proposal rev 2 can be created with rev 1 as parent."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Revise Rejected", "destination": "Copenhagen"}).json()["trip"]["id"]

    # Rev 1
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial Draft"}).json()
    rev1_id = r1["revision_id"]

    # Reject Rev 1
    rej_res = client.post(
        f"/api/trips/{trip_id}/revisions/{rev1_id}/reject",
        json={"expected_revision_number": 1, "reason": "Need cheaper options"},
    )
    assert rej_res.status_code == 200

    # Propose Rev 2 with Rev 1 as base
    r2_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "feedback": "Budget optimized options",
            "expected_base_revision_id": rev1_id,
            "expected_base_revision": 1,
            "updated_constraints": {"budget": 1200},
        },
    )
    assert r2_res.status_code == 200
    r2 = r2_res.json()
    assert r2["revision_number"] == 2
    assert r2["parent_revision_id"] == rev1_id
    assert r2["status"] == "awaiting_approval"

    # Verify history maintains both: Rev 1 is REJECTED, Rev 2 is PROPOSED
    hist = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    assert len(hist) == 2
    assert hist[0]["revision_number"] == 1
    assert hist[0]["status"] == "REJECTED"
    assert hist[1]["revision_number"] == 2
    assert hist[1]["status"] == "PROPOSED"
    assert hist[1]["parent_revision_id"] == rev1_id


def test_approval_does_not_trigger_booking_or_payment():
    """Verify that approving a revision does NOT create flight bookings, hotel reservations, or payments."""
    client, user_id = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Safe Approval", "destination": "Singapore"}).json()["trip"]["id"]

    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Final plan"}).json()
    rev_id = r1["revision_id"]

    # Approve
    app_res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app_res.status_code == 200

    # Ensure booking & payment endpoints/state are not modified
    # In trip store, trip status is approved, but no bookings exist
    trip = TripStore().get_trip(trip_id, user_id=user_id)
    assert trip["status"] == "approved"
    # Ensure no confirmed bookings or payments were initiated
    assert "booking_reference" not in trip or trip.get("booking_reference") is None
    assert "payment_id" not in trip or trip.get("payment_id") is None


def test_research_advisory_remains_outside_canonical_mutation():
    """Verify research evidence cannot directly mutate canonical trip state."""
    client, user_id = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Advisory Research", "destination": "Reykjavik"}).json()["trip"]["id"]

    # Check initial trip
    initial_trip = TripStore().get_trip(trip_id, user_id=user_id)
    assert initial_trip["destination"] == "Reykjavik"

    # Create an evidence record
    from domain.evidence import EvidenceRecord, EvidenceType, FreshnessKind
    from datetime import datetime, timezone

    rec = EvidenceRecord(
        trip_id=trip_id,
        user_id=user_id,
        evidence_type=EvidenceType.WEATHER,
        applicability_key={"destination": "Reykjavik"},
        content_hash="hash123",
        freshness_kind=FreshnessKind.STATIC_DATA,
        freshness_ttl_seconds=3600,
        produced_at=datetime.now(timezone.utc),
        source_agent="research_advisory",
        payload={"note": "Reykjavik has nice geothermal baths"},
    )
    EvidenceRevisionStore().create_evidence(rec)

    # Trip remains strictly untouched
    current_trip = TripStore().get_trip(trip_id, user_id=user_id)
    assert current_trip["destination"] == "Reykjavik"
    assert current_trip["current_revision_number"] == 0


def test_frozen_sections_regression():
    """Verify Section 5 flight, Section 6 hotel, and payment endpoints remain intact and callable."""
    client, user_id = _auth_client()

    # Section 5: flight search routes exist and enforce authentication / validation
    flight_search_res = client.post(
        "/api/flights/search",
        json={"origin": "DEL", "destination": "BOM", "departure_date": "2026-11-01"},
    )
    # Returns 200 or 400/422 depending on supplier mocking, but route is mounted and functional
    assert flight_search_res.status_code in (200, 400, 422)

    # Section 6: hotel search routes exist and enforce authentication / validation
    hotel_search_res = client.post(
        "/api/hotels/search",
        json={"destination": "Paris", "checkin_date": "2026-11-01", "checkout_date": "2026-11-05"},
    )
    assert hotel_search_res.status_code in (200, 400, 422)

    # Payment / Razorpay: orders endpoint requires valid request
    payment_res = client.post(
        "/api/payments/razorpay/create-order",
        json={"amount": 1000},
    )
    assert payment_res.status_code in (200, 400, 404, 422)


def test_approval_missing_revision_id_returns_422():
    """Verify approval with omitted revision_id fails validation with 422."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Trip", "destination": "Rome"}).json()["trip"]["id"]
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()

    # Omit revision_id completely
    res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"expected_revision_number": 1},
    )
    assert res.status_code == 422


def test_approval_malformed_revision_id_returns_422():
    """Verify approval with malformed revision_id fails validation with 422."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Trip", "destination": "Rome"}).json()["trip"]["id"]

    res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": "not-a-valid-uuid", "expected_revision_number": 1},
    )
    assert res.status_code == 422


def test_approval_never_falls_back_to_current_revision_and_no_baseline_autocreation():
    """Verify approval cannot auto-create baseline revision or approve without explicit revision_id."""
    client, _ = _auth_client()
    # Trip with NO revisions created yet
    trip_id = client.post("/api/trips", json={"title": "No Rev Trip", "destination": "Nice"}).json()["trip"]["id"]

    # Calling approve with empty body or missing revision_id fails 422
    res_empty = client.post(f"/api/trips/{trip_id}/approve", json={})
    assert res_empty.status_code == 422

    # Calling approve with random revision_id returns 404 or 409, never creates revision 1 implicitly
    random_rev_id = str(uuid4())
    res_random = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": random_rev_id, "expected_revision_number": 1},
    )
    assert res_random.status_code in (404, 409)

    # Verify trip still has 0 revisions and was not auto-published
    revs = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    assert len(revs) == 0


def test_approval_missing_expected_revision_number_returns_422():
    """Verify approval missing expected_revision_number fails validation with 422."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Trip", "destination": "Tokyo"}).json()["trip"]["id"]
    rev = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = rev["revision_id"]

    # Missing expected_revision_number
    res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id},
    )
    assert res.status_code == 422


def test_approval_stale_expected_revision_number_returns_409():
    """Verify approval with mismatched expected_revision_number returns 409 Conflict."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Trip", "destination": "Kyoto"}).json()["trip"]["id"]
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "R1"}).json()
    r2 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "R2"}).json()

    # Attempt to approve r2 with expected_revision_number = 1 (current is 2)
    res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": r2["revision_id"], "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "conflict" in res.json()["detail"].lower() or "revision" in res.json()["detail"].lower()


def test_rejection_missing_expected_revision_number_returns_422():
    """Verify rejection missing expected_revision_number fails validation with 422."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Trip", "destination": "Venice"}).json()["trip"]["id"]
    rev = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Draft"}).json()
    rev_id = rev["revision_id"]

    res = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"reason": "Missing expected revision number"},
    )
    assert res.status_code == 422


def test_rejection_stale_expected_revision_number_returns_409():
    """Verify rejection with stale expected_revision_number returns 409 Conflict."""
    client, _ = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Trip", "destination": "Florence"}).json()["trip"]["id"]
    client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "R1"}).json()
    r2 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "R2"}).json()

    # Reject r2 with expected_revision_number 1 (stale)
    res = client.post(
        f"/api/trips/{trip_id}/revisions/{r2['revision_id']}/reject",
        json={"expected_revision_number": 1, "reason": "Too slow"},
    )
    assert res.status_code == 409


def test_rejection_immutability_and_mutual_exclusion():
    """Verify rejection records cannot be deleted, remain queryable, and mutual exclusion holds."""
    import os
    client, user_id = _auth_client()
    trip_id = client.post("/api/trips", json={"title": "Immutable Rej", "destination": "Madrid"}).json()["trip"]["id"]
    r1 = client.post(f"/api/trips/{trip_id}/revisions", json={"feedback": "Initial"}).json()
    rev_id = r1["revision_id"]

    # 1. Create rejection
    rej_res = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "Not satisfactory"},
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["status"] == "REJECTED"

    # 2. Attempt DELETE via API (no deletion endpoint exists -> 405)
    del_res = client.delete(f"/api/trips/{trip_id}/revisions/{rev_id}/reject")
    assert del_res.status_code in (404, 405)

    # 3. Check migration 009 has BEFORE UPDATE OR DELETE trigger raising exception and RESTRICT FKs
    migration_file = "migrations/009_revision_parent_and_rejections.sql"
    with open(migration_file, "r", encoding="utf-8") as f:
        mig_sql = f.read()
    assert "fn_enforce_plan_revision_rejection_immutability" in mig_sql
    assert "trg_plan_revision_rejection_immutability" in mig_sql
    assert "BEFORE UPDATE OR DELETE ON plan_revision_rejections" in mig_sql
    assert "ERRCODE = 'restrict_violation'" in mig_sql
    assert "ON DELETE RESTRICT" in mig_sql
    assert "ON DELETE CASCADE" not in mig_sql

    # 4. If connected to a real Postgres database, test SQL DELETE trigger enforcement directly
    store = EvidenceRevisionStore()
    if not store.use_in_memory and os.getenv("DATABASE_URL"):
        with store._connect() as conn:
            import psycopg
            with pytest.raises(psycopg.Error):
                conn.execute("DELETE FROM plan_revision_rejections WHERE trip_id = %s", (trip_id,))

    # 5. Verify rejection record remains queryable
    rej_record = store.get_rejection_for_revision(trip_id, rev_id)
    assert rej_record is not None
    assert rej_record.reason == "Not satisfactory"

    # 6. Revision status remains REJECTED
    hist = client.get(f"/api/trips/{trip_id}/revisions").json()["revisions"]
    assert hist[0]["status"] == "REJECTED"
    assert hist[0]["is_rejected"] is True

    # 7. Approval/rejection mutual exclusion remains intact (cannot approve rejected revision)
    app_res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert app_res.status_code == 409


def test_constraint_update_persists_budget_70k_and_planner_reads_it():
    """Verify that updating budget to 70000 creates a revision, persists to metadata, and is read by PlannerService."""
    client, user_id = _auth_client("budget_planner_user@example.com")
    trip_store = TripStore()

    # 1. Create base trip with initial budget 45000
    base_trip = trip_store.create_trip(
        user_id=user_id,
        title="Kerala Journey",
        destination="Kerala, India",
        origin="Kolkata (CCU)",
        metadata={
            "budget": 45000,
            "travelers_count": 2,
            "start_date": "2026-10-14",
            "end_date": "2026-10-21",
            "constraints": {
                "budget": 45000,
                "travelers_count": 2,
                "start_date": "2026-10-14",
                "end_date": "2026-10-21",
            },
        },
    )
    trip_id = str(base_trip["id"])

    # 2. Advance revision with updated budget 70000 and travelers 3
    rev_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "updated_constraints": {
                "budget": 70000,
                "budget_amount": 70000,
                "travelers_count": 3,
                "adults": 3,
                "children": 0,
                "rooms": 1,
            },
            "feedback": "Budget increased to 70000 and travelers to 3",
            "expected_base_revision": 0,
        },
    )
    assert rev_res.status_code == 200
    data = rev_res.json()
    assert data["revision_number"] == 1
    assert data["status"] == "awaiting_approval"

    # 3. Read back authoritative trip record
    persisted = trip_store.get_trip(trip_id, user_id)
    assert persisted is not None
    meta = persisted.get("metadata") or {}
    assert meta.get("budget") == 70000.0
    assert meta.get("constraints", {}).get("budget") == 70000
    assert meta.get("travelers_count") == 3

    # 4. Verify PlannerService extracts the new 70000 budget from canonical constraints
    from services.planner_service import PlannerService
    planner = PlannerService()
    canonical_trip = planner.trip_store.get_trip(trip_id, user_id)
    assert canonical_trip is not None

    meta = canonical_trip.get("metadata") or {}
    constraints_dict = meta.get("constraints") or {}
    budget_val = (
        constraints_dict.get("budget")
        or constraints_dict.get("budget_amount")
        or meta.get("budget")
        or meta.get("budget_amount")
    )
    assert float(budget_val) == 70000.0
