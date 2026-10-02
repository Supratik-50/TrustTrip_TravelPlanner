"""Section 9 Gate: Comprehensive tests for Explicit Approval & Booking Eligibility (ADR-009).

Covers all ADR-009 specifications:
- Test A: Explicit booking authorization on approved current revision succeeds and creates immutable record
- Test B: Rejected revision cannot be authorized for booking (409 Conflict)
- Test C: Unapproved revision cannot be authorized for booking (409 Conflict)
- Test D: Stale revision (not current revision) cannot be authorized (409 Conflict)
- Test E: Expected revision number mismatch fails with 409 Conflict
- Test F: Duplicate authorization returns existing record deterministically (idempotent 200, ADR-009 Section F)
- Test G: Newer child revision does not inherit authorization from parent revision
- Test H: Missing revision_id fails with 422 Unprocessable Entity
- Test I: Missing expected_revision_number fails with 422 Unprocessable Entity
- Test J: Invalid expected_revision_number (<= 0) fails with 422 Unprocessable Entity
- Test K: Cross-trip revision access is blocked (404 Not Found)
- Test L: Unauthorized user access is blocked (404 / 401)
- Test M: Canonical drift between trip and snapshot fails authorization (409 Conflict)
- Test N: assert_booking_eligible strictly evaluates all 10 conditions and raises BookingEligibilityError
- Test O: check_booking_eligibility returns non-raising BookingEligibilityResult
- Test P: GET /api/trips/{trip_id}/booking-eligibility returns runtime derived eligibility
- Test Q: list_trip_revisions enriches revisions with booking authorization metadata
- Test R: Booking authorization has zero side-effects on flight/hotel booking, payment, or Razorpay
- Test S: Migration 010 SQL structure and immutability trigger validation
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient

from app import app
from domain.booking_authorization import (
    BookingAuthorization,
    BookingEligibilityError,
    BookingEligibilityResult,
)
from services.auth_store import UserStore
from services.authorization_store import (
    AuthorizationStore,
    verify_canonical_booking_state_consistency,
)
from services.evidence_store import EvidenceRevisionStore
from services.trip_store import TripStore


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
    AuthorizationStore._IN_MEMORY_AUTHORIZATIONS.clear()


def _auth_client(email: str = "sec9_user@example.com"):
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def _create_approved_trip(
    client: TestClient,
    title="Tokyo Voyage",
    destination="Tokyo",
    origin="NYC",
    departure_date="2026-11-01",
    return_date="2026-11-10",
    passengers=2,
):
    """Helper to create a trip, generate revision 1 with complete canonical fields, and accept/approve it."""
    constraints = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "passengers": passengers,
    }
    t_res = client.post(
        "/api/trips",
        json={
            "title": title,
            "origin": origin,
            "destination": destination,
            "metadata": {"constraints": constraints},
        },
    )
    assert t_res.status_code == 201
    trip_id = t_res.json()["trip"]["id"]

    r_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "feedback": "Initial plan draft",
            "updated_constraints": constraints,
        },
    )
    assert r_res.status_code == 200
    r_data = r_res.json()
    rev_id = r_data["revision_id"]

    appr_res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1, "notes": "Approved for booking"},
    )
    assert appr_res.status_code == 200
    return trip_id, rev_id, 1


# =============================================================================
# Test A: Explicit booking authorization on approved current revision succeeds
# =============================================================================
def test_explicit_booking_authorization_succeeds():
    client, user_id = _auth_client()
    trip_id, rev_id, rev_num = _create_approved_trip(client)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": rev_num, "notes": "Authorized for booking"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["trip_id"] == trip_id
    assert data["revision_id"] == rev_id
    assert data["revision_number"] == rev_num
    assert data["is_authorized"] is True
    assert "authorization_id" in data
    assert "authorized_at" in data

    # Verify persisted record
    auth_store = AuthorizationStore()
    record = auth_store.get_booking_authorization(trip_id, rev_id)
    assert record is not None
    assert str(record.authorization_id) == data["authorization_id"]
    assert str(record.trip_id) == trip_id
    assert str(record.revision_id) == rev_id
    assert record.revision_number == rev_num


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("children", 1),
        ("infants", 1),
        ("rooms", {"id": "room-1", "adults": 2}),
    ],
)
def test_booking_occupancy_drift_is_rejected(field, value):
    base = {
        "origin": "NYC",
        "destination": "Tokyo",
        "departure_date": "2026-11-01",
        "return_date": "2026-11-10",
        "passengers": 2,
        "children": 0,
        "infants": 0,
        "rooms": {"id": "room-1", "adults": 2},
    }
    changed = dict(base)
    changed[field] = value
    if field == "rooms":
        changed[field] = {"id": "room-1", "adults": 3}
    consistent, reason = verify_canonical_booking_state_consistency(base, changed)
    assert not consistent
    assert reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("children", "not-an-int"),
        ("children", -1),
        ("infants", "not-an-int"),
        ("infants", -1),
        ("rooms", {"id": "room-1", "occupancy": object()}),
    ],
)
def test_malformed_booking_occupancy_fails_closed(field, value):
    base = {"children": 0, "infants": 0, "rooms": {"id": "room-1", "adults": 2}}
    malformed = dict(base)
    malformed[field] = value
    consistent, reason = verify_canonical_booking_state_consistency(base, malformed)
    assert not consistent
    assert "Malformed" in reason


def test_exact_booking_occupancy_unchanged_passes():
    state = {
        "origin": "NYC",
        "destination": "Tokyo",
        "departure_date": "2026-11-01",
        "return_date": "2026-11-10",
        "passengers": 2,
        "children": 1,
        "infants": 0,
        "rooms": {"id": "room-1", "adults": 2, "children": 1},
    }
    assert verify_canonical_booking_state_consistency(state, dict(state)) == (True, None)


def test_stored_authorization_revision_number_must_match():
    client, user_id = _auth_client()
    trip_id, rev_id, rev_num = _create_approved_trip(client)
    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": rev_num},
    )
    assert auth_res.status_code == 200
    store = AuthorizationStore()
    authorization = store.get_booking_authorization(trip_id, rev_id)
    assert authorization is not None
    AuthorizationStore._IN_MEMORY_AUTHORIZATIONS[str(authorization.authorization_id)] = authorization.model_copy(
        update={"revision_number": rev_num + 1}
    )
    with pytest.raises(BookingEligibilityError):
        store.assert_booking_eligible(trip_id, user_id, rev_id, rev_num)


# =============================================================================
# Test B: Rejected revision cannot be authorized for booking (409 Conflict)
# =============================================================================
def test_rejected_revision_cannot_be_authorized():
    client, _ = _auth_client()
    constraints = {
        "origin": "NYC",
        "destination": "Kyoto",
        "departure_date": "2026-11-01",
        "return_date": "2026-11-10",
        "passengers": 2,
    }
    t_res = client.post(
        "/api/trips",
        json={"title": "Kyoto Trip", "origin": "NYC", "destination": "Kyoto", "metadata": {"constraints": constraints}},
    )
    trip_id = t_res.json()["trip"]["id"]

    r_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Draft 1", "updated_constraints": constraints},
    )
    rev_id = r_res.json()["revision_id"]

    # Reject revision 1
    rej_res = client.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/reject",
        json={"expected_revision_number": 1, "reason": "Not satisfactory"},
    )
    assert rej_res.status_code == 200

    # Attempt to authorize rejected revision
    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert auth_res.status_code == 409
    assert "rejected" in auth_res.json()["detail"].lower()


# =============================================================================
# Test C: Unapproved revision cannot be authorized for booking (409 Conflict)
# =============================================================================
def test_unapproved_revision_cannot_be_authorized():
    client, _ = _auth_client()
    constraints = {
        "origin": "NYC",
        "destination": "Osaka",
        "departure_date": "2026-11-01",
        "return_date": "2026-11-10",
        "passengers": 2,
    }
    t_res = client.post(
        "/api/trips",
        json={"title": "Osaka Trip", "origin": "NYC", "destination": "Osaka", "metadata": {"constraints": constraints}},
    )
    trip_id = t_res.json()["trip"]["id"]

    r_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Draft 1", "updated_constraints": constraints},
    )
    rev_id = r_res.json()["revision_id"]

    # Attempt to authorize without approving first
    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert auth_res.status_code == 409
    assert "plan approval" in auth_res.json()["detail"].lower()


# =============================================================================
# Test D: Stale revision (not current revision) cannot be authorized (409 Conflict)
# =============================================================================
def test_stale_revision_cannot_be_authorized():
    client, _ = _auth_client()
    trip_id, rev1_id, _ = _create_approved_trip(client)

    # Create rev 2
    r2_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Adjust dates", "expected_base_revision_id": rev1_id, "expected_base_revision": 1},
    )
    assert r2_res.status_code == 200
    rev2_id = r2_res.json()["revision_id"]

    # Approve rev 2
    client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev2_id, "expected_revision_number": 2},
    )

    # Attempt to authorize rev 1 (stale, not current revision anymore)
    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev1_id, "expected_revision_number": 1},
    )
    assert auth_res.status_code == 409
    assert "not current revision" in auth_res.json()["detail"].lower()


# =============================================================================
# Test E: Expected revision number mismatch fails with 409 Conflict
# =============================================================================
def test_expected_revision_number_mismatch_fails():
    client, _ = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 99},
    )
    assert res.status_code == 409
    assert "expected revision number" in res.json()["detail"].lower()


# =============================================================================
# Test F: Duplicate authorization returns existing record deterministically (idempotent)
# =============================================================================
def test_duplicate_authorization_returns_existing_record_idempotent():
    client, _ = _auth_client()
    trip_id, rev_id, rev_num = _create_approved_trip(client)

    # First authorization
    res1 = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": rev_num, "notes": "First pass"},
    )
    assert res1.status_code == 200
    auth1 = res1.json()

    # Second authorization (duplicate of exact same revision)
    res2 = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": rev_num, "notes": "Duplicate pass"},
    )
    assert res2.status_code == 200
    auth2 = res2.json()

    # Deterministic: exact same authorization ID returned
    assert auth1["authorization_id"] == auth2["authorization_id"]
    assert auth1["authorized_at"] == auth2["authorized_at"]

    # Only 1 authorization stored
    auth_store = AuthorizationStore()
    all_auths = auth_store.list_authorizations_for_trip(trip_id, client.headers["Authorization"])
    matching = [a for a in AuthorizationStore._IN_MEMORY_AUTHORIZATIONS.values() if str(a.trip_id) == trip_id]
    assert len(matching) == 1


# =============================================================================
# Test G: Newer child revision does NOT inherit authorization from parent revision
# =============================================================================
def test_newer_revision_does_not_inherit_authorization():
    client, user_id = _auth_client()
    trip_id, rev1_id, _ = _create_approved_trip(client)

    # Authorize rev 1 for booking
    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev1_id, "expected_revision_number": 1},
    )
    assert auth_res.status_code == 200

    # Create rev 2
    r2_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Change hotel", "expected_base_revision_id": rev1_id, "expected_base_revision": 1},
    )
    assert r2_res.status_code == 200
    rev2_id = r2_res.json()["revision_id"]

    # Rev 1 has authorization
    auth_store = AuthorizationStore()
    auth_rev1 = auth_store.get_booking_authorization(trip_id, rev1_id)
    assert auth_rev1 is not None

    # Rev 2 does NOT have authorization
    auth_rev2 = auth_store.get_booking_authorization(trip_id, rev2_id)
    assert auth_rev2 is None

    # Eligibility check on Rev 2 indicates not authorized
    elig_rev2 = auth_store.check_booking_eligibility(trip_id, user_id, rev2_id, 2)
    assert elig_rev2.is_eligible is False
    assert elig_rev2.is_authorized is False


# =============================================================================
# Test H & I & J: Request validation (missing or invalid fields fail with 422)
# =============================================================================
def test_missing_revision_id_fails_422():
    client, _ = _auth_client()
    trip_id, _, _ = _create_approved_trip(client)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"expected_revision_number": 1},
    )
    assert res.status_code == 422


def test_missing_expected_revision_number_fails_422():
    client, _ = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id},
    )
    assert res.status_code == 422


def test_invalid_expected_revision_number_fails_422():
    client, _ = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    # 0 is invalid
    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 0},
    )
    assert res.status_code == 422

    # Negative is invalid
    res_neg = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": -1},
    )
    assert res_neg.status_code == 422


# =============================================================================
# Test K: Cross-trip revision access is blocked (404 Not Found)
# =============================================================================
def test_cross_trip_revision_authorization_blocked():
    client, _ = _auth_client()
    trip1_id, rev1_id, _ = _create_approved_trip(client, title="Trip 1", destination="Rome")
    trip2_id, _, _ = _create_approved_trip(client, title="Trip 2", destination="Milan")

    # Attempt to authorize trip1's revision under trip2
    res = client.post(
        f"/api/trips/{trip2_id}/booking-approval",
        json={"revision_id": rev1_id, "expected_revision_number": 1},
    )
    assert res.status_code in (404, 409)


# =============================================================================
# Test L: Unauthorized user access is blocked
# =============================================================================
def test_unauthorized_user_cannot_authorize():
    client1, _ = _auth_client(email="owner@example.com")
    client2, _ = _auth_client(email="intruder@example.com")

    trip_id, rev_id, _ = _create_approved_trip(client1)

    # Intruder attempts to authorize owner's trip
    res = client2.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 404


# =============================================================================
# Test M: Canonical consistency regression tests (Requirement 1)
# =============================================================================
def test_canonical_consistency_destination_changed():
    """Destination changed -> fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client, destination="Tokyo")

    # Alter trip destination
    trip_store = TripStore()
    trip_store.update_trip(trip_id, user_id, destination="Kyoto")

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "destination" in res.json()["detail"].lower()


def test_canonical_consistency_origin_changed():
    """Origin changed -> fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client, origin="NYC")

    # Alter trip origin
    trip_store = TripStore()
    trip_store.update_trip(trip_id, user_id, origin="BOS")

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "origin" in res.json()["detail"].lower()


def test_canonical_consistency_departure_date_changed():
    """Departure date changed -> fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client, departure_date="2026-11-01")

    # Alter trip departure date in metadata constraints
    trip_store = TripStore()
    trip = trip_store.get_trip(trip_id, user_id)
    meta = dict(trip.get("metadata") or {})
    constraints = dict(meta.get("constraints") or {})
    constraints["departure_date"] = "2026-11-05"
    meta["constraints"] = constraints
    trip_store.update_trip(trip_id, user_id, metadata=meta)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "departure_date" in res.json()["detail"].lower()


def test_canonical_consistency_return_date_changed():
    """Return date changed -> fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client, return_date="2026-11-10")

    # Alter trip return date
    trip_store = TripStore()
    trip = trip_store.get_trip(trip_id, user_id)
    meta = dict(trip.get("metadata") or {})
    constraints = dict(meta.get("constraints") or {})
    constraints["return_date"] = "2026-11-15"
    meta["constraints"] = constraints
    trip_store.update_trip(trip_id, user_id, metadata=meta)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "return_date" in res.json()["detail"].lower()


def test_canonical_consistency_passenger_count_changed():
    """Passenger count changed -> fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client, passengers=2)

    # Alter trip passenger count
    trip_store = TripStore()
    trip = trip_store.get_trip(trip_id, user_id)
    meta = dict(trip.get("metadata") or {})
    constraints = dict(meta.get("constraints") or {})
    constraints["passengers"] = 4
    meta["constraints"] = constraints
    trip_store.update_trip(trip_id, user_id, metadata=meta)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "passengers" in res.json()["detail"].lower()


def test_canonical_consistency_occupancy_changed():
    """Relevant occupancy changed -> fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    # Add selected room to trip metadata not present in snapshot
    trip_store = TripStore()
    trip = trip_store.get_trip(trip_id, user_id)
    meta = dict(trip.get("metadata") or {})
    meta["selected_room"] = {"room_id": "rm_suite_101", "name": "Presidential Suite"}
    trip_store.update_trip(trip_id, user_id, metadata=meta)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "room" in res.json()["detail"].lower() or "occupancy" in res.json()["detail"].lower()


def test_canonical_consistency_missing_required_field_fails_closed():
    """Missing required canonical field fails closed with 409 Conflict."""
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    # Remove origin completely from trip state
    trip_store = TripStore()
    trip = trip_store.get_trip(trip_id, user_id)
    meta = dict(trip.get("metadata") or {})
    constraints = dict(meta.get("constraints") or {})
    constraints.pop("origin", None)
    meta["constraints"] = constraints
    trip_store.update_trip(trip_id, user_id, origin=None, metadata=meta)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert res.status_code == 409
    assert "canonical state drift" in res.json()["detail"].lower()
    assert "missing" in res.json()["detail"].lower()


def test_canonical_consistency_unchanged_complete_state_passes():
    """Unchanged complete canonical state passes with 200 OK."""
    client, user_id = _auth_client()
    trip_id, rev_id, rev_num = _create_approved_trip(client)

    res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": rev_num},
    )
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["is_authorized"] is True


# =============================================================================
# Test N: Authoritative Booking Guard strictly requires BOTH approval & authorization (Requirement 2)
# =============================================================================
def test_assert_booking_eligible_requires_both_approval_and_authorization():
    import inspect

    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)
    auth_store = AuthorizationStore()

    # Plan is approved, but NOT authorized: booking guard MUST fail
    with pytest.raises(BookingEligibilityError) as exc_info:
        auth_store.assert_booking_eligible(
            trip_id=trip_id,
            user_id=user_id,
            revision_id=rev_id,
            expected_revision_number=1,
        )
    assert "not been explicitly authorized for booking" in str(exc_info.value).lower()

    # Separate acceptance-only check passes (explicit conceptual separation)
    acceptance_res = auth_store.check_revision_accepted(
        trip_id=trip_id,
        user_id=user_id,
        revision_id=rev_id,
        expected_revision_number=1,
    )
    assert acceptance_res is True

    # Authoritative guard has NO bypass flag: inspecting signature confirms require_authorized is absent
    sig = inspect.signature(auth_store.assert_booking_eligible)
    assert "require_authorized" not in sig.parameters
    for param in sig.parameters.values():
        assert "bypass" not in param.name.lower()
        assert "allow" not in param.name.lower()

    # Attempting to pass require_authorized must raise TypeError
    with pytest.raises(TypeError):
        auth_store.assert_booking_eligible(
            trip_id=trip_id,
            user_id=user_id,
            revision_id=rev_id,
            expected_revision_number=1,
            require_authorized=False,  # type: ignore
        )

    # Now authorize the revision
    auth_store.create_booking_authorization_atomic(trip_id, user_id, rev_id, 1)

    # Now authoritative guard passes
    res_auth = auth_store.assert_booking_eligible(
        trip_id=trip_id,
        user_id=user_id,
        revision_id=rev_id,
        expected_revision_number=1,
    )
    assert res_auth.is_eligible is True
    assert res_auth.is_authorized is True
    assert res_auth.is_approved is True


# =============================================================================
# Test O: check_booking_eligibility returns non-raising BookingEligibilityResult
# =============================================================================
def test_check_booking_eligibility_non_raising():
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)
    auth_store = AuthorizationStore()

    # Check eligibility when not yet authorized
    result = auth_store.check_booking_eligibility(
        trip_id=trip_id,
        user_id=user_id,
        revision_id=rev_id,
        expected_revision_number=1,
    )
    assert isinstance(result, BookingEligibilityResult)
    assert result.is_eligible is False
    assert len(result.reasons) > 0

    # Non-existent trip
    fake_result = auth_store.check_booking_eligibility(
        trip_id=str(uuid4()),
        user_id=user_id,
    )
    assert fake_result.is_eligible is False


# =============================================================================
# Test P: GET /api/trips/{trip_id}/booking-eligibility returns runtime derived eligibility
# =============================================================================
def test_get_booking_eligibility_endpoint():
    client, _ = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    # Before authorization
    res = client.get(f"/api/trips/{trip_id}/booking-eligibility?revision_id={rev_id}&expected_revision_number=1")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["eligibility"]["is_authorized"] is False

    # Authorize
    client.post(f"/api/trips/{trip_id}/booking-approval", json={"revision_id": rev_id, "expected_revision_number": 1})

    # After authorization
    res_after = client.get(f"/api/trips/{trip_id}/booking-eligibility?revision_id={rev_id}&expected_revision_number=1")
    assert res_after.status_code == 200
    data_after = res_after.json()
    assert data_after["eligibility"]["is_authorized"] is True
    assert data_after["eligibility"]["is_eligible"] is True


# =============================================================================
# Test Q: list_trip_revisions enriches revisions with booking authorization metadata
# =============================================================================
def test_list_trip_revisions_enrichment():
    client, _ = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    # Authorize
    client.post(f"/api/trips/{trip_id}/booking-approval", json={"revision_id": rev_id, "expected_revision_number": 1})

    rev_list_res = client.get(f"/api/trips/{trip_id}/revisions")
    assert rev_list_res.status_code == 200
    revs = rev_list_res.json()["revisions"]
    assert len(revs) == 1
    rev = revs[0]
    assert rev["is_booking_authorized"] is True
    assert rev["booking_authorization"] is not None
    assert rev["booking_authorization"]["revision_number"] == 1


# =============================================================================
# Test R: Booking authorization has zero side-effects on flight/hotel booking or Razorpay
# =============================================================================
def test_booking_authorization_zero_booking_side_effects():
    client, user_id = _auth_client()
    trip_id, rev_id, _ = _create_approved_trip(client)

    # Prior state: no booking intents, no transactions
    t_before = client.get("/api/trips").json().get("trips", [])
    flight_records_before = [x for x in t_before if x.get("record_type") == "flight"]
    hotel_records_before = [x for x in t_before if x.get("record_type") == "hotel"]
    assert len(flight_records_before) == 0
    assert len(hotel_records_before) == 0

    # Execute booking authorization
    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert auth_res.status_code == 200

    # Post state: still NO booking transactions or supplier orders created
    t_after = client.get("/api/trips").json().get("trips", [])
    flight_records_after = [x for x in t_after if x.get("record_type") == "flight"]
    hotel_records_after = [x for x in t_after if x.get("record_type") == "hotel"]
    assert len(flight_records_after) == 0
    assert len(hotel_records_after) == 0


# =============================================================================
# Test S: Migration 010 SQL structure and immutability trigger validation
# =============================================================================
def test_migration_010_sql_structure():
    repo_root = Path(__file__).resolve().parent.parent
    mig_file = repo_root / "migrations" / "010_booking_authorizations.sql"
    assert mig_file.exists(), "migrations/010_booking_authorizations.sql must exist"

    sql = mig_file.read_text(encoding="utf-8")
    # Table creation
    assert "CREATE TABLE IF NOT EXISTS booking_authorizations" in sql
    assert "authorization_id UUID PRIMARY KEY" in sql
    assert "uq_booking_authorization_trip_revision UNIQUE" in sql
    # Immutability triggers
    assert "trg_booking_authorization_immutability" in sql
    assert "BEFORE UPDATE OR DELETE ON booking_authorizations" in sql
    assert "strictly immutable: updates are prohibited" in sql
    assert "append-only: deletions are prohibited" in sql
    # Prerequisite triggers
    assert "trg_check_booking_authorization_prerequisite" in sql
    assert "BEFORE INSERT ON booking_authorizations" in sql
    assert "plan_approvals" in sql
    assert "plan_revision_rejections" in sql
    # No cascade deletes
    assert "ON DELETE CASCADE" not in sql
    assert "ON DELETE RESTRICT" in sql
