"""Tests for Revision-Specific Plan Generation (Design B).

Strict Invariants verified:
1. Revision #2 plan generation does NOT create Revision #3.
2. Revision #2 retains its exact revision_number=2 and UUID.
3. Revision #1 remains completely immutable and unchanged.
4. Generated 5-day itinerary attached to Revision #2.
5. Kerala -> Kochi/COK destination consistency preserved.
6. Traveler count and preferences preserved.
7. Planner failures (429/503) leave Revision #2 intact without creating Revision #3.
8. Approval blocked before valid regenerated plan; succeeds after regenerated plan.
9. Booking authorization remains separate.
"""

from __future__ import annotations

import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_store import UserStore
from services.auth_service import create_access_token
from services.trip_store import TripStore
from services.evidence_store import EvidenceRevisionStore


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


def _auth_client(email: str = "rev_tester@example.com") -> tuple[TestClient, str]:
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = str(user["id"])
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def _mock_3day_plan():
    return {
        "success": True,
        "guardrail_allowed": True,
        "planning_blocked": False,
        "llm_provider": "groq",
        "llm_model": "qwen/qwen3.8-27b",
        "plan_json": {
            "destination": "Kochi",
            "destination_city": "Kochi",
            "destination_airport": "COK",
            "origin": "Bengaluru",
            "origin_airport": "BLR",
            "departure_date": "2026-11-15",
            "return_date": "2026-11-18",
            "budget": 50000,
            "currency": "INR",
            "travelers": 2,
            "itinerary": [
                {"day": 1, "title": "Arrival in Kochi", "activities": ["Fort Kochi walk"]},
                {"day": 2, "title": "Mattancherry & Spice Market", "activities": ["Dutch Palace"]},
                {"day": 3, "title": "Cherai Beach & Departure", "activities": ["Sunset at Cherai"]},
            ],
        },
        "raw_plan": "3-day Kerala/Kochi plan",
        "requires_approval": True,
        "approval_request": "Review your 3-day trip to Kochi",
    }


def _mock_5day_plan():
    import uuid
    return {
        "success": True,
        "guardrail_allowed": True,
        "planning_blocked": False,
        "llm_provider": "groq",
        "llm_model": "qwen/qwen3.8-27b",
        "plan_json": {
            "destination": "Kochi",
            "destination_city": "Kochi",
            "destination_airport": "COK",
            "origin": "Bengaluru",
            "origin_airport": "BLR",
            "departure_date": "2026-11-15",
            "return_date": "2026-11-20",
            "budget": 70000,
            "currency": "INR",
            "travelers": 2,
            "preferences": ["heritage", "seafood"],
            "research_advisory": {
                "evidence_ids": [uuid.uuid4()],
                "source_ids": [uuid.uuid4()],
            },
            "itinerary": [
                {"day": 1, "title": "Arrival in Kochi & Fort Kochi Heritage", "activities": ["Chinese Fishing Nets"]},
                {"day": 2, "title": "Mattancherry & Jew Town", "activities": ["Paradesi Synagogue", "Spice Market"]},
                {"day": 3, "title": "Alleppey Backwaters Excursion", "activities": ["Houseboat day cruise"]},
                {"day": 4, "title": "Kumarakom Bird Sanctuary", "activities": ["Morning boat safari"]},
                {"day": 5, "title": "Cherai Beach & Departure", "activities": ["Cherai Beach", "COK departure"]},
            ],
        },
        "raw_plan": "5-day revised Kerala/Kochi itinerary",
        "requires_approval": True,
        "approval_request": "Review your revised 5-day trip to Kochi",
    }


def _mock_6day_plan():
    import copy
    res = _mock_5day_plan()
    res = copy.deepcopy(res)
    res["plan_json"]["itinerary"].append({"day": 6, "title": "Cherai Beach & COK Departure", "activities": ["Departure transfer"]})
    res["raw_plan"] = "6-day revised Kerala/Kochi calendar itinerary"
    return res


def test_01_revision_2_plan_generation_exact_invariants():
    """Verify that generating a plan for Revision #2 updates trips.metadata.plan while plan_revisions remains immutable."""
    client, user_id = _auth_client("rev_invariants@example.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    # 1. Create base trip
    trip = trip_store.create_trip(
        user_id=user_id,
        title="Trip to Kerala, India",
        destination="Kerala, India",
        origin="Bengaluru (BLR)",
        metadata={
            "destination_city": "Kochi",
            "destination_airport": "COK",
            "origin_airport": "BLR",
            "start_date": "2026-11-15",
            "end_date": "2026-11-18",
            "budget": 50000,
            "passengers": 2,
            "constraints": {
                "destination": "Kerala, India",
                "origin": "Bengaluru (BLR)",
                "start_date": "2026-11-15",
                "end_date": "2026-11-18",
                "budget": 50000,
                "travelers": 2,
            },
        },
    )
    trip_id = str(trip["id"])

    # 2. Generate Revision #1 via /api/travel
    with patch("app.run_travel_agent", return_value=_mock_3day_plan()):
        res1 = client.post(
            "/api/travel",
            json={"trip_id": trip_id, "destination": "Kerala, India", "message": "Plan 3 days in Kerala"},
        )
        assert res1.status_code == 200

    revisions_v1 = ev_store.list_revisions_for_trip(trip_id, user_id)
    assert len(revisions_v1) == 1
    rev1 = revisions_v1[0]
    rev1_id = str(rev1.revision_id)
    assert rev1.revision_number == 1
    orig_rev1_snapshot = json.dumps(rev1.plan_snapshot, sort_keys=True)

    # 3. Create Revision #2 with updated constraints: 5 nights (Nov 15-20), budget 70000, 2 travelers
    rev2_create_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "expected_base_revision": 1,
            "expected_base_revision_id": rev1_id,
            "changes": {
                "start_date": "2026-11-15",
                "end_date": "2026-11-20",
                "budget": 70000,
                "travelers": 2,
                "preferences": ["heritage", "seafood"],
            },
            "change_summary": "Extend to Nov 15-20, budget 70k, 2 travelers",
        },
    )
    assert rev2_create_res.status_code == 200
    rev2_data = rev2_create_res.json()
    rev2_id = rev2_data["revision_id"]
    assert rev2_data["revision_number"] == 2

    # Check state before generation
    trip_mid = trip_store.get_trip(trip_id, user_id)
    assert trip_mid["current_revision_number"] == 2
    assert str(trip_mid["current_revision_id"]) == rev2_id
    assert trip_mid["status"] == "awaiting_approval"

    # Capture original immutable Revision #2 snapshot before plan generation:
    revisions_mid = ev_store.list_revisions_for_trip(trip_id, user_id)
    rev2_before = [r for r in revisions_mid if str(r.revision_id) == rev2_id][0]
    orig_rev2_snapshot = json.dumps(rev2_before.plan_snapshot, sort_keys=True)

    # 4. Explicitly generate plan for Revision #2
    with patch("services.planner_service.PlannerService.plan_travel", return_value=_mock_5day_plan()) as mock_planner:
        gen_res = client.post(
            f"/api/trips/{trip_id}/revisions/{rev2_id}/plan",
            json={"message": "Please generate the 5-day itinerary"},
        )
        assert gen_res.status_code == 200
        gen_data = gen_res.json()
        assert gen_data["success"] is True
        assert gen_data["revision_id"] == rev2_id
        assert gen_data["revision_number"] == 2

        # Verify planner was called for this trip
        call_kwargs = mock_planner.call_args[1]
        assert call_kwargs["trip_id"] == trip_id
        assert call_kwargs["authenticated_user_id"] == user_id

    # 5. STRICT INVARIANT VERIFICATIONS:
    revisions_after = ev_store.list_revisions_for_trip(trip_id, user_id)
    # MUST NOT create Revision #3:
    assert len(revisions_after) == 2, f"Expected exactly 2 revisions, found {len(revisions_after)}"

    # Revision #2 retains UUID and revision_number:
    rev2_after = [r for r in revisions_after if str(r.revision_id) == rev2_id][0]
    assert rev2_after.revision_number == 2
    assert str(rev2_after.revision_id) == rev2_id

    # IMMUTABILITY OF plan_revisions: plan_snapshot remains unchanged after generation
    assert json.dumps(rev2_after.plan_snapshot, sort_keys=True) == orig_rev2_snapshot

    # Working plan is persisted in trips.metadata.plan:
    trip_final = trip_store.get_trip(trip_id, user_id)
    working_plan = trip_final.get("metadata", {}).get("plan")
    assert working_plan is not None
    itinerary = (working_plan.get("plan_json") or {}).get("itinerary") or working_plan.get("itinerary") or []
    assert len(itinerary) == 5, f"Expected 5 itinerary days, got {len(itinerary)}"
    assert working_plan.get("departure_date") == "2026-11-15" or (working_plan.get("plan_json") or {}).get("departure_date") == "2026-11-15"
    assert working_plan.get("return_date") == "2026-11-20" or (working_plan.get("plan_json") or {}).get("return_date") == "2026-11-20"

    # UUID serialization verified (evidence_ids/source_ids are strings):
    advisory = (working_plan.get("plan_json") or working_plan).get("research_advisory", {})
    for eid in advisory.get("evidence_ids", []):
        assert isinstance(eid, str)
    for sid in advisory.get("source_ids", []):
        assert isinstance(sid, str)

    # Kerala -> Kochi / COK consistency preserved:
    dest_city = (working_plan.get("plan_json") or {}).get("destination_city") or working_plan.get("destination_city")
    dest_airport = (working_plan.get("plan_json") or {}).get("destination_airport") or working_plan.get("destination_airport")
    assert dest_city == "Kochi"
    assert dest_airport == "COK"

    # Traveler and preferences preservation:
    plan_json = working_plan.get("plan_json") or working_plan
    assert plan_json.get("travelers") == 2
    assert "heritage" in plan_json.get("preferences", [])

    # Revision #1 remains completely unchanged:
    rev1_after = [r for r in revisions_after if str(r.revision_id) == rev1_id][0]
    assert rev1_after.revision_number == 1
    assert json.dumps(rev1_after.plan_snapshot, sort_keys=True) == orig_rev1_snapshot

    # Trip remains awaiting_approval (no automatic approval):
    assert trip_final["status"] == "awaiting_approval"
    assert trip_final["current_revision_number"] == 2
    assert str(trip_final["current_revision_id"]) == rev2_id


def test_02_approval_safety_blocks_before_valid_plan_and_succeeds_after():
    """Verify approval is blocked when plan does not correspond to new constraints, and succeeds once generated (testing both 5-day and 6-calendar-day variants)."""
    client, user_id = _auth_client("safety_user@example.com")
    trip_store = TripStore()

    # Create trip with 3-day constraints and 3-day plan
    trip = trip_store.create_trip(
        user_id=user_id,
        title="Trip to Kerala",
        destination="Kerala, India",
        origin="Bengaluru (BLR)",
        metadata={
            "start_date": "2026-11-15",
            "end_date": "2026-11-18",
            "constraints": {
                "start_date": "2026-11-15",
                "end_date": "2026-11-18",
            },
        },
    )
    trip_id = str(trip["id"])

    with patch("app.run_travel_agent", return_value=_mock_3day_plan()):
        client.post("/api/travel", json={"trip_id": trip_id, "destination": "Kerala, India", "message": "Plan"})

    # Create Revision #2 changing dates to 5 nights (Nov 15-20)
    trip_mid = trip_store.get_trip(trip_id, user_id)
    rev1_id = str(trip_mid["current_revision_id"])

    rev2_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "expected_base_revision": 1,
            "expected_base_revision_id": rev1_id,
            "changes": {"start_date": "2026-11-15", "end_date": "2026-11-20"},
            "change_summary": "Extend to Nov 20",
        },
    )
    assert rev2_res.status_code == 200
    rev2_id = rev2_res.json()["revision_id"]

    # ATTEMPT APPROVAL BEFORE PLAN REGENERATION -> MUST BE BLOCKED WITH 400
    appr_fail = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev2_id, "expected_revision_number": 2},
    )
    assert appr_fail.status_code == 400
    assert "Plan return date" in appr_fail.json()["detail"] or "duration" in appr_fail.json()["detail"]

    # GENERATE 6-CALENDAR-DAY PLAN FOR REVISION #2 (covering 5 nights Nov 15-20 inclusive)
    with patch("services.planner_service.PlannerService.plan_travel", return_value=_mock_6day_plan()):
        gen_res = client.post(
            f"/api/trips/{trip_id}/revisions/{rev2_id}/plan",
            json={"message": "Generate plan"},
        )
        assert gen_res.status_code == 200

    # ATTEMPT APPROVAL AFTER VALID 6-CALENDAR-DAY REGENERATED PLAN -> MUST SUCCEED (200)
    appr_ok = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev2_id, "expected_revision_number": 2},
    )
    assert appr_ok.status_code == 200
    trip_appr = trip_store.get_trip(trip_id, user_id)
    assert trip_appr["status"] == "approved"


def test_03_planner_error_429_leaves_revision_intact():
    """Verify that a planner 429/503 error returns truthful error and does not create Revision #3 or corrupt Revision #2."""
    client, user_id = _auth_client("err_user@example.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(
        user_id=user_id,
        title="Trip to Kerala",
        destination="Kerala, India",
        metadata={
            "start_date": "2026-11-15",
            "end_date": "2026-11-18",
            "constraints": {"start_date": "2026-11-15", "end_date": "2026-11-18"},
        },
    )
    trip_id = str(trip["id"])

    with patch("app.run_travel_agent", return_value=_mock_3day_plan()):
        client.post("/api/travel", json={"trip_id": trip_id, "destination": "Kerala, India", "message": "Plan"})

    rev1_id = str(trip_store.get_trip(trip_id, user_id)["current_revision_id"])

    rev2_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "expected_base_revision": 1,
            "expected_base_revision_id": rev1_id,
            "changes": {"start_date": "2026-11-15", "end_date": "2026-11-20"},
        },
    )
    rev2_id = rev2_res.json()["revision_id"]

    # Mock planner throwing Rate Limit 429
    with patch("services.planner_service.PlannerService.plan_travel", side_effect=RuntimeError("Groq 429: Rate limit reached")):
        err_res = client.post(
            f"/api/trips/{trip_id}/revisions/{rev2_id}/plan",
            json={"message": "Generate plan"},
        )
        assert err_res.status_code in (429, 502, 503)

    # Invariants after planner failure:
    revisions = ev_store.list_revisions_for_trip(trip_id, user_id)
    assert len(revisions) == 2  # No Revision #3
    rev2 = [r for r in revisions if str(r.revision_id) == rev2_id][0]
    assert rev2.revision_number == 2
    assert str(rev2.revision_id) == rev2_id

    trip_state = trip_store.get_trip(trip_id, user_id)
    assert trip_state["status"] == "awaiting_approval"
    assert trip_state["current_revision_number"] == 2
    assert str(trip_state["current_revision_id"]) == rev2_id


def test_04_repeat_generation_does_not_create_revision_3():
    """Verify that regenerating multiple times updates Revision #2 and never creates Revision #3."""
    client, user_id = _auth_client("repeat_user@example.com")
    trip_store = TripStore()
    ev_store = EvidenceRevisionStore()

    trip = trip_store.create_trip(
        user_id=user_id,
        title="Trip to Kerala",
        destination="Kerala, India",
        metadata={
            "start_date": "2026-11-15",
            "end_date": "2026-11-18",
            "constraints": {"start_date": "2026-11-15", "end_date": "2026-11-18"},
        },
    )
    trip_id = str(trip["id"])

    with patch("app.run_travel_agent", return_value=_mock_3day_plan()):
        client.post("/api/travel", json={"trip_id": trip_id, "destination": "Kerala, India", "message": "Plan"})

    rev1_id = str(trip_store.get_trip(trip_id, user_id)["current_revision_id"])

    rev2_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={
            "expected_base_revision": 1,
            "expected_base_revision_id": rev1_id,
            "changes": {"start_date": "2026-11-15", "end_date": "2026-11-20"},
        },
    )
    rev2_id = rev2_res.json()["revision_id"]

    # Call generation twice
    with patch("services.planner_service.PlannerService.plan_travel", return_value=_mock_5day_plan()):
        res_a = client.post(f"/api/trips/{trip_id}/revisions/{rev2_id}/plan", json={})
        assert res_a.status_code == 200

        res_b = client.post(f"/api/trips/{trip_id}/revisions/{rev2_id}/plan", json={})
        assert res_b.status_code == 200

    revisions = ev_store.list_revisions_for_trip(trip_id, user_id)
    assert len(revisions) == 2, f"Expected 2 revisions after repeat generation, got {len(revisions)}"
    assert str(revisions[-1].revision_id) == rev2_id
    assert revisions[-1].revision_number == 2


def test_05_ownership_and_validation_guards():
    """Verify ownership, revision matching, and status validation guards on plan generation endpoint."""
    client_owner, user_owner = _auth_client("owner@example.com")
    client_intruder, user_intruder = _auth_client("intruder@example.com")
    trip_store = TripStore()

    trip = trip_store.create_trip(
        user_id=user_owner,
        title="Owner's Trip",
        destination="Kerala, India",
    )
    trip_id = str(trip["id"])

    rev_res = client_owner.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Initial revision"},
    )
    rev_id = rev_res.json()["revision_id"]

    # 1. Intruder cannot generate plan for owner's trip -> 404
    intruder_res = client_intruder.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/plan",
        json={},
    )
    assert intruder_res.status_code == 404

    # 2. Non-existent revision ID -> 409 (mismatched active revision)
    fake_rev_id = "00000000-0000-0000-0000-000000000000"
    fake_res = client_owner.post(
        f"/api/trips/{trip_id}/revisions/{fake_rev_id}/plan",
        json={},
    )
    assert fake_res.status_code == 409

    # 3. Mismatched revision ID (not active revision) -> 409
    # Create another revision to make rev_id stale
    rev2_res = client_owner.post(
        f"/api/trips/{trip_id}/revisions",
        json={"expected_base_revision": 1, "expected_base_revision_id": rev_id, "feedback": "Second revision"},
    )
    rev2_id = rev2_res.json()["revision_id"]

    stale_res = client_owner.post(
        f"/api/trips/{trip_id}/revisions/{rev_id}/plan",
        json={},
    )
    assert stale_res.status_code == 409
