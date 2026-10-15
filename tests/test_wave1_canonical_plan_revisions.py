"""Wave 1 Tests -- Canonical Plan to Proposed Revision + Airport Identity.

Tests verifying the Wave 1 Business Contract:
- The first successfully generated itinerary is Revision #1 with STATUS = PROPOSED.
- User can approve Revision #1 directly without a redundant "Create Revision" step.
- Section 9 booking authorization succeeds on approved Revision #1.
- Re-planning creates Revision #2 PROPOSED.
- Destination/origin canonical airport identity (e.g. Darjeeling -> IXB) is persisted
  in trip metadata and constraint snapshots without altering human-readable destination text.
- Flight search resolution succeeds using canonical airport criteria.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_store import UserStore
from services.auth_service import create_access_token
from services.trip_store import TripStore
from services.evidence_store import EvidenceRevisionStore
from services.flight_search_service import resolve_canonical_trip_criteria


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


def _auth_client(email: str = "wave1_tester@example.com") -> tuple[TestClient, str]:
    """Helper to create an authenticated TestClient and return (client, user_id)."""
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = str(user["id"])
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def _mock_plan_result(destination: str = "Darjeeling, India", dest_iata: str | None = None, orig_iata: str | None = None):
    plan_json = {
        "destination": destination,
        "itinerary": [{"day": 1, "description": "Arrival and sightseeing"}],
    }
    if dest_iata:
        plan_json["destination_airport"] = dest_iata
    if orig_iata:
        plan_json["origin_airport"] = orig_iata
    return {
        "success": True,
        "guardrail_allowed": True,
        "planning_blocked": False,
        "plan_json": plan_json,
        "approval_request": "Please review the plan.",
        "requires_approval": True,
        "messages": [],
    }


class TestWave1CanonicalPlanAndRevisionLifecycle:

    def test_01_first_plan_generation_creates_revision_1_proposed(self):
        """1. Generating first plan creates Revision #1 with PROPOSED status, real UUID, and updates trip."""
        client, user_id = _auth_client("wave1_user1@example.com")
        trip_store = TripStore()
        ev_store = EvidenceRevisionStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Darjeeling Journey",
            destination="Darjeeling, India",
            origin="Kolkata (CCU)",
            metadata={
                "budget": 50000,
                "start_date": "2026-11-01",
                "end_date": "2026-11-07",
                "constraints": {
                    "budget": 50000,
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-07",
                },
            },
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Darjeeling, India")):
            response = client.post(
                "/api/travel",
                json={
                    "message": "Plan a scenic 6-day holiday to Darjeeling",
                    "trip_id": trip_id,
                },
            )
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["success"] is True
        assert "revision_id" in data
        assert uuid.UUID(data["revision_id"])
        assert data["revision_number"] == 1
        assert data["revision_status"] == "PROPOSED"
        assert data["current_revision_id"] == data["revision_id"]
        assert data["current_revision_number"] == 1

        persisted = trip_store.get_trip(trip_id, user_id)
        assert persisted is not None
        assert persisted.get("current_revision_number") == 1
        assert str(persisted.get("current_revision_id")) == data["revision_id"]

        rev_record = ev_store.get_revision(data["revision_id"], user_id)
        assert rev_record is not None
        assert rev_record.revision_number == 1
        assert str(rev_record.trip_id) == trip_id
        assert ev_store.get_approval_for_revision(trip_id, data["revision_id"]) is None

    def test_02_replan_creates_revision_2_proposed(self):
        """2. Calling plan generation on a trip with Revision #1 creates Revision #2 PROPOSED."""
        client, user_id = _auth_client("wave1_user2@example.com")
        trip_store = TripStore()
        ev_store = EvidenceRevisionStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Sikkim Mountain Trek",
            destination="Gangtok, India",
            origin="Kolkata (CCU)",
            metadata={
                "budget": 60000,
                "start_date": "2026-11-10",
                "end_date": "2026-11-16",
            },
        )
        trip_id = str(trip["id"])

        # First plan
        with patch("app.run_travel_agent", return_value=_mock_plan_result("Gangtok, India")):
            res1 = client.post("/api/travel", json={"message": "Initial Sikkim plan", "trip_id": trip_id})
        assert res1.status_code == 200
        rev1_id = res1.json()["revision_id"]
        assert res1.json()["revision_number"] == 1

        # Replan / second iteration
        with patch("app.run_travel_agent", return_value=_mock_plan_result("Gangtok, India")):
            res2 = client.post("/api/travel", json={"message": "Please add more monastery visits", "trip_id": trip_id})
        assert res2.status_code == 200
        data2 = res2.json()

        assert data2["revision_number"] == 2
        assert data2["revision_status"] == "PROPOSED"
        assert data2["revision_id"] != rev1_id
        assert uuid.UUID(data2["revision_id"])

        refreshed = trip_store.get_trip(trip_id, user_id)
        assert refreshed["current_revision_number"] == 2
        assert str(refreshed["current_revision_id"]) == data2["revision_id"]

        rev2_record = ev_store.get_revision(data2["revision_id"], user_id)
        assert rev2_record is not None
        assert rev2_record.revision_number == 2
        assert ev_store.get_approval_for_revision(trip_id, data2["revision_id"]) is None

    def test_03_approve_directly_on_generated_revision_1(self):
        """3. User can directly approve Revision #1 without creating a manual revision first."""
        client, user_id = _auth_client("wave1_user3@example.com")
        trip_store = TripStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Goa Beach Vacation",
            destination="Goa, India",
            origin="Delhi (DEL)",
            metadata={"budget": 40000},
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Goa, India")):
            plan_res = client.post("/api/travel", json={"message": "Plan a 4-day Goa trip", "trip_id": trip_id})
        assert plan_res.status_code == 200
        rev_id = plan_res.json()["revision_id"]

        approve_res = client.post(
            f"/api/trips/{trip_id}/approve",
            json={
                "revision_id": rev_id,
                "expected_revision_number": 1,
            },
        )
        assert approve_res.status_code == 200, approve_res.text
        app_data = approve_res.json()
        assert app_data.get("review_status") == "APPROVED" or app_data.get("status") == "approved"

        persisted = trip_store.get_trip(trip_id, user_id)
        assert persisted["status"] == "approved"

    def test_04_booking_authorization_succeeds_on_approved_revision_1(self):
        """4. Section 9 booking authorization succeeds on approved Revision #1."""
        client, user_id = _auth_client("wave1_user4@example.com")
        trip_store = TripStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Jaipur Heritage Tour",
            destination="Jaipur, India",
            origin="Mumbai (BOM)",
            metadata={
                "budget": 35000,
                "departure_date": "2026-12-01",
                "return_date": "2026-12-05",
                "start_date": "2026-12-01",
                "end_date": "2026-12-05",
                "passengers": 2,
                "travelers_count": 2,
                "adults": 2,
                "destination_airport": "JAI",
                "origin_airport": "BOM",
                "constraints": {
                    "budget": 35000,
                    "departure_date": "2026-12-01",
                    "return_date": "2026-12-05",
                    "start_date": "2026-12-01",
                    "end_date": "2026-12-05",
                    "passengers": 2,
                    "travelers_count": 2,
                    "adults": 2,
                    "destination_airport": "JAI",
                    "origin_airport": "BOM",
                },
            },
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Jaipur, India")):
            plan_res = client.post("/api/travel", json={"message": "Heritage tour Jaipur", "trip_id": trip_id})
        assert plan_res.status_code == 200
        rev_id = plan_res.json()["revision_id"]

        app_res = client.post(
            f"/api/trips/{trip_id}/approve",
            json={"revision_id": rev_id, "expected_revision_number": 1},
        )
        assert app_res.status_code == 200

        auth_res = client.post(
            f"/api/trips/{trip_id}/booking-approval",
            json={"revision_id": rev_id, "expected_revision_number": 1},
        )
        assert auth_res.status_code == 200, auth_res.text
        auth_data = auth_res.json()
        assert auth_data.get("is_authorized") is True or auth_data.get("status") == "authorized" or auth_data.get("authorized") is True


class TestWave1CanonicalAirportIdentity:

    def test_05_darjeeling_resolves_to_ixb_in_metadata(self):
        """5. Destination 'Darjeeling, India' persists canonical gateway airport 'IXB' in trip metadata."""
        client, user_id = _auth_client("wave1_darjeeling@example.com")
        trip_store = TripStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Darjeeling Hills",
            destination="Darjeeling, India",
            origin="Kolkata (CCU)",
            metadata={},
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Darjeeling, India")):
            res = client.post("/api/travel", json={"message": "Darjeeling tea garden trip", "trip_id": trip_id})
        assert res.status_code == 200
        data = res.json()

        assert data.get("destination_airport") == "IXB"

        persisted = trip_store.get_trip(trip_id, user_id)
        meta = persisted.get("metadata") or {}
        assert persisted.get("destination") == "Darjeeling, India"
        assert meta.get("destination_airport") == "IXB"

    def test_06_origin_kolkata_resolves_to_ccu_in_metadata(self):
        """6. Origin 'Kolkata, India' persists canonical airport 'CCU' in trip metadata."""
        client, user_id = _auth_client("wave1_kolkata@example.com")
        trip_store = TripStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Kolkata to Kerala",
            destination="Kochi, India",
            origin="Kolkata, India",
            metadata={},
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Kochi, India")):
            res = client.post("/api/travel", json={"message": "Kolkata to Kochi trip", "trip_id": trip_id})
        assert res.status_code == 200
        data = res.json()

        assert data.get("origin_airport") == "CCU"
        assert data.get("destination_airport") == "COK"

        persisted = trip_store.get_trip(trip_id, user_id)
        meta = persisted.get("metadata") or {}
        assert meta.get("origin_airport") == "CCU"
        assert meta.get("destination_airport") == "COK"

    def test_07_resolve_canonical_trip_criteria_succeeds_with_resolved_airport(self):
        """7. Flight search criteria resolver succeeds without 400 when destination_airport is in metadata."""
        trip_store = TripStore()
        client, user_id = _auth_client("wave1_flight_crit@example.com")

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Darjeeling Gateway Test",
            destination="Darjeeling, India",
            origin="Kolkata, India",
            metadata={
                "destination_airport": "IXB",
                "origin_airport": "CCU",
                "start_date": "2026-11-15",
                "end_date": "2026-11-20",
            },
        )

        crit = resolve_canonical_trip_criteria(trip)
        assert crit["destination_iata"] == "IXB"
        assert crit["origin_iata"] == "CCU"
        assert str(crit["departure_date"]) == "2026-11-15"

    def test_08_revision_constraints_snapshot_preserves_airports(self):
        """8. The auto-created Revision #1 snapshot carries canonical airport codes."""
        client, user_id = _auth_client("wave1_snapshot@example.com")
        trip_store = TripStore()
        ev_store = EvidenceRevisionStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Ladakh Adventure",
            destination="Leh, India",
            origin="Delhi (DEL)",
            metadata={},
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Leh, India")):
            res = client.post("/api/travel", json={"message": "Plan Leh trip", "trip_id": trip_id})
        assert res.status_code == 200
        rev_id = res.json()["revision_id"]

        rev_record = ev_store.get_revision(rev_id, user_id)
        assert rev_record is not None
        constraints = rev_record.plan_snapshot.get("constraints") or {}
        assert constraints.get("destination_airport") == "IXL"
        assert constraints.get("origin_airport") == "DEL"


class TestWave1IntegrityAndSafeties:

    def test_09_cannot_approve_with_mismatched_revision_number(self):
        """9. Approving Revision #1 with expected_revision_number=99 is rejected (409 conflict)."""
        client, user_id = _auth_client("wave1_mismatch@example.com")
        trip_store = TripStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Ooty Hills",
            destination="Ooty, India",
            origin="Bangalore (BLR)",
            metadata={},
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Ooty, India")):
            res = client.post("/api/travel", json={"message": "Ooty holiday", "trip_id": trip_id})
        assert res.status_code == 200
        rev_id = res.json()["revision_id"]

        app_res = client.post(
            f"/api/trips/{trip_id}/approve",
            json={"revision_id": rev_id, "expected_revision_number": 99},
        )
        assert app_res.status_code in (400, 409)

    def test_10_booking_authorization_rejected_if_not_approved(self):
        """10. Booking authorization is strictly blocked if Revision #1 is still PROPOSED."""
        client, user_id = _auth_client("wave1_unapproved@example.com")
        trip_store = TripStore()

        trip = trip_store.create_trip(
            user_id=user_id,
            title="Manali Trip",
            destination="Manali, India",
            origin="Delhi (DEL)",
            metadata={},
        )
        trip_id = str(trip["id"])

        with patch("app.run_travel_agent", return_value=_mock_plan_result("Manali, India")):
            res = client.post("/api/travel", json={"message": "Manali retreat", "trip_id": trip_id})
        assert res.status_code == 200
        rev_id = res.json()["revision_id"]

        auth_res = client.post(
            f"/api/trips/{trip_id}/booking-approval",
            json={"revision_id": rev_id, "expected_revision_number": 1},
        )
        assert auth_res.status_code in (400, 403, 409, 422)
