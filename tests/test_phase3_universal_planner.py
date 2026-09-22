import os
import unittest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from uuid import uuid4

# Set test environment flags
os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "test-secret-key-for-phase-3-universal-planner-suite-64-bytes-long"
os.environ["AUTH_SECRET"] = "test-secret-key-for-phase-3-universal-planner-suite-64-bytes-long"

from fastapi.testclient import TestClient
from app import app
from domain.budget import BudgetConstraints, BudgetMode
from domain.changes import TripChangeSet, apply_trip_change_set
from domain.compat import build_trip_request, trip_request_to_legacy
from domain.passenger import PassengerComposition
from domain.research import TripIntent
from domain.trip import Location, TripRequest, TripStatus
from services.auth_store import UserStore
from services.intent_router import IntentDecision, IntentRouter
from services.planner_service import PlannerService
from services.travel_planner import parse_trip_request
from services.trip_store import TripStore


class UniversalPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.router = IntentRouter()
        cls.planner = PlannerService()

    def setUp(self):
        UserStore._IN_MEMORY_USERS.clear()
        TripStore._IN_MEMORY_TRIPS.clear()
        TripStore._IN_MEMORY_THREADS.clear()

    def _create_user(self, email: str, password: str = "P@ssword123!"):
        res = self.client.post("/api/auth/signup", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        return data["user"], data["access_token"]

    def _auth_headers(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _sample_trip(self) -> TripRequest:
        return TripRequest(
            trip_id=uuid4(),
            user_id="user_test_123",
            thread_id="thread_test_123",
            original_query="Plan a 5-day trip from Mumbai to London for 2 adults under 150000",
            origin=Location(city="Mumbai", country="India", iata_code="BOM"),
            destination=Location(city="London", country="United Kingdom", iata_code="LHR"),
            duration_days=5,
            passengers=PassengerComposition(adults=2, children=0, infants=0),
            budget=BudgetConstraints(amount=Decimal("150000"), currency="INR", mode=BudgetMode.FLEXIBLE),
            intent=TripIntent.NEW_TRIP,
            revision=1,
            status=TripStatus.PLANNING,
        )

    # 1. Explicit NEW_TRIP intent when no existing trip
    def test_explicit_new_trip_intent_no_prior(self):
        decision = self.router.classify_intent("Plan a trip from Kolkata to Delhi")
        self.assertEqual(decision.intent, TripIntent.NEW_TRIP)
        self.assertGreaterEqual(decision.confidence, 0.90)
        self.assertIn("no_existing_trip", decision.source)
        self.assertIsNone(decision.changes)

    # 2. Explicit FOLLOW_UP intent on existing trip
    def test_explicit_follow_up_intent(self):
        trip = self._sample_trip()
        decision = self.router.classify_intent("Change destination to Tokyo", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertGreaterEqual(decision.confidence, 0.85)
        self.assertIsNotNone(decision.changes)
        self.assertIn("destination", decision.changes.changed_fields)

    # 3. GLOBAL_FLIGHT_RESEARCH routing boundary
    def test_global_flight_research_boundary(self):
        decision = self.router.classify_intent("Tell me about baggage allowance and airline hubs for international flight information")
        self.assertEqual(decision.intent, TripIntent.GLOBAL_FLIGHT_RESEARCH)
        self.assertGreaterEqual(decision.confidence, 0.90)
        self.assertIsNone(decision.changes)

        # Service boundary test: returns structured guidance without running planner agents
        res = self.planner.plan_travel("Tell me about baggage allowance and airline hubs for international flight information")
        self.assertEqual(res["intent"], TripIntent.GLOBAL_FLIGHT_RESEARCH.value)
        self.assertIn("Global Flight Information", res["final_response"])
        self.assertEqual(res["selected_agents"], [])

    # 4. BOOKING_OPERATION routing boundary
    def test_booking_operation_boundary(self):
        trip = self._sample_trip()
        decision = self.router.classify_intent("Proceed to checkout and book this flight", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.BOOKING_OPERATION)
        self.assertGreaterEqual(decision.confidence, 0.95)
        self.assertIsNone(decision.changes)

        # Service boundary test: returns booking handoff without running planning agents
        res = self.planner.plan_travel("Proceed to checkout and book this flight")
        self.assertEqual(res["intent"], TripIntent.BOOKING_OPERATION.value)
        self.assertTrue(res.get("booking_handoff"))
        self.assertIn("Booking operation recognized", res["final_response"])
        self.assertEqual(res["selected_agents"], [])

    # 5. New trip does not inherit old destination
    def test_new_trip_does_not_inherit_old_destination(self):
        trip = self._sample_trip()
        # Query specifies a new trip with independent route declaration
        decision = self.router.classify_intent("New trip from Delhi to Tokyo", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.NEW_TRIP)

        # Legacy parser isolation test
        fresh_parsed = parse_trip_request("New trip from Delhi to Tokyo", previous=trip)
        self.assertEqual(fresh_parsed["destination"], "HND")
        self.assertNotEqual(fresh_parsed["destination"], "LHR")

    # 6. New trip does not inherit old origin
    def test_new_trip_does_not_inherit_old_origin(self):
        trip = self._sample_trip()
        # Standalone destination "Japan trip"
        decision = self.router.classify_intent("Japan trip", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.NEW_TRIP)

        # Must not leak old origin (BOM)
        fresh_parsed = parse_trip_request("Japan trip", previous=trip)
        self.assertIsNone(fresh_parsed["origin"])
        self.assertNotEqual(fresh_parsed["origin"], "BOM")

    # 7. New trip does not inherit old passengers
    def test_new_trip_does_not_inherit_old_passengers(self):
        trip = self._sample_trip() # has 2 adults
        decision = self.router.classify_intent("Japan trip", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.NEW_TRIP)

        fresh_parsed = parse_trip_request("Japan trip", previous=trip)
        self.assertEqual(fresh_parsed["adults"], 1)
        self.assertEqual(fresh_parsed["passenger_count"], 1)

    # 8. Follow-up preserves origin when changing destination
    def test_follow_up_preserves_origin_when_changing_destination(self):
        trip = self._sample_trip() # origin: BOM, destination: LHR
        decision = self.router.classify_intent("Change destination to Tokyo", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertIn("destination", decision.changes.changed_fields)

        updated = apply_trip_change_set(trip, decision.changes)
        self.assertEqual(updated.origin.iata_code, "BOM")
        self.assertEqual(updated.origin.city, "Mumbai")
        self.assertEqual(updated.destination.iata_code, "HND")
        self.assertEqual(updated.destination.city, "Tokyo")

    # 9. Follow-up preserves destination when changing origin
    def test_follow_up_preserves_destination_when_changing_origin(self):
        trip = self._sample_trip() # origin: BOM, destination: LHR
        decision = self.router.classify_intent("Change origin to Delhi", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertIn("origin", decision.changes.changed_fields)

        updated = apply_trip_change_set(trip, decision.changes)
        self.assertEqual(updated.origin.iata_code, "DEL")
        self.assertIn(updated.origin.city, ("Delhi", "New Delhi"))
        self.assertEqual(updated.destination.iata_code, "LHR")
        self.assertEqual(updated.destination.city, "London")

    # 10. Destination-only change preserves passengers
    def test_destination_only_change_preserves_passengers(self):
        trip = self._sample_trip() # adults: 2
        decision = self.router.classify_intent("Change destination to Tokyo", existing_trip=trip)
        updated = apply_trip_change_set(trip, decision.changes)
        self.assertEqual(updated.passengers.adults, 2)
        self.assertEqual(updated.passengers.children, 0)
        self.assertEqual(updated.passengers.passenger_count, 2)

    # 11. Destination-only change preserves budget
    def test_destination_only_change_preserves_budget(self):
        trip = self._sample_trip() # budget: 150000 INR
        decision = self.router.classify_intent("Change destination to Tokyo", existing_trip=trip)
        updated = apply_trip_change_set(trip, decision.changes)
        self.assertEqual(updated.budget.amount, Decimal("150000"))
        self.assertEqual(updated.budget.currency, "INR")

    # 12. Budget change preserves route
    def test_budget_change_preserves_route(self):
        trip = self._sample_trip() # BOM -> LHR
        decision = self.router.classify_intent("Increase budget to 250000", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertIn("budget", decision.changes.changed_fields)

        updated = apply_trip_change_set(trip, decision.changes)
        self.assertEqual(updated.budget.amount, Decimal("250000"))
        self.assertEqual(updated.origin.iata_code, "BOM")
        self.assertEqual(updated.destination.iata_code, "LHR")

    # 13. Passenger change preserves route
    def test_passenger_change_preserves_route(self):
        trip = self._sample_trip() # BOM -> LHR
        decision = self.router.classify_intent("Change it to 4 travellers", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertIn("passengers", decision.changes.changed_fields)

        updated = apply_trip_change_set(trip, decision.changes)
        self.assertEqual(updated.passengers.adults, 4)
        self.assertEqual(updated.origin.iata_code, "BOM")
        self.assertEqual(updated.destination.iata_code, "LHR")

    # 14. Authenticated ownership remains strictly enforced
    def test_authenticated_ownership_remains_enforced(self):
        user_a, token_a = self._create_user("user_a_p3@example.com")
        user_b, token_b = self._create_user("user_b_p3@example.com")

        # User A creates trip and thread
        trip_res = self.client.post("/api/trips", json={"title": "Trip A"}, headers=self._auth_headers(token_a))
        trip_id = str(trip_res.json()["trip"]["id"])
        thread_res = self.client.post(f"/api/trips/{trip_id}/threads", headers=self._auth_headers(token_a))
        thread_id = str(thread_res.json()["thread"]["id"])

        # User B attempts to access User A's thread via /api/travel
        res = self.client.post(
            "/api/travel",
            json={"message": "Change destination to Tokyo", "thread_id": thread_id},
            headers=self._auth_headers(token_b),
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("does not belong", res.json()["detail"])

    # 15. Canonical TripRequest remains authoritative source of truth
    def test_canonical_trip_request_authority(self):
        trip = self._sample_trip()
        decision = self.router.classify_intent("Change destination to Tokyo", existing_trip=trip)
        updated_canonical = apply_trip_change_set(trip, decision.changes)

        # Derive legacy projection strictly from canonical model
        legacy = trip_request_to_legacy(updated_canonical)
        self.assertEqual(legacy["destination"], "HND")
        self.assertEqual(legacy["origin"], "BOM")
        self.assertEqual(legacy["budget_amount"], 150000.0)
        self.assertEqual(legacy["adults"], 2)

    # 16. Legacy trip_constraints compatibility projection works
    def test_legacy_trip_constraints_compatibility(self):
        trip = self._sample_trip()
        legacy = trip_request_to_legacy(trip)
        self.assertIn("origin", legacy)
        self.assertIn("destination", legacy)
        self.assertIn("duration_days", legacy)
        self.assertIn("passenger_count", legacy)
        self.assertIn("budget_amount", legacy)
        self.assertIn("journey_scope", legacy)
        self.assertEqual(legacy["journey_scope"], "international")

    # 17. Existing planner invocation still works with mock graph
    @patch("backend.travel_graph.invoke")
    def test_planner_service_delegates_to_langgraph(self, mock_invoke):
        mock_invoke.return_value = {
            "final_response": "Here is your plan to Delhi.",
            "trip_summary": "Trip to Delhi",
            "flight_results": "Flight 101",
            "hotel_results": "Hotel Taj",
            "itinerary": "Day 1: Sightseeing",
            "approved": False,
        }

        user, token = self._create_user("user_graph_test@example.com")
        res = self.client.post(
            "/api/travel",
            json={"message": "Plan a trip from Kolkata to Delhi for 2 adults under 30000"},
            headers=self._auth_headers(token),
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["intent"], TripIntent.NEW_TRIP.value)
        self.assertIn("trip_request", body)
        self.assertEqual(body["trip_request"]["destination"]["iata_code"], "DEL")

    # 18. Existing approval endpoint still works
    @patch("backend.travel_graph.invoke")
    def test_existing_approval_endpoint_works(self, mock_invoke):
        mock_invoke.return_value = {
            "final_response": "Resumed and approved.",
            "approved": True,
        }

        user, token = self._create_user("user_approval_test@example.com")
        trip_res = self.client.post("/api/trips", json={"title": "Approved Trip"}, headers=self._auth_headers(token))
        trip_id = str(trip_res.json()["trip"]["id"])
        thread_res = self.client.post(f"/api/trips/{trip_id}/threads", headers=self._auth_headers(token))
        thread_id = str(thread_res.json()["thread"]["id"])

        res = self.client.post(
            "/api/travel/approve",
            json={"thread_id": thread_id, "approved": True, "feedback": "Looks great!"},
            headers=self._auth_headers(token),
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

    # 19. Multiple changes in follow-up preserves unmodified fields
    def test_multiple_changes_in_follow_up(self):
        trip = self._sample_trip() # BOM -> LHR, 2 adults, 150000, 5 days
        decision = self.router.classify_intent(
            "Change destination to Tokyo and make it 7 days under 200000",
            existing_trip=trip,
        )
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertIn("destination", decision.changes.changed_fields)
        self.assertIn("duration_days", decision.changes.changed_fields)
        self.assertIn("budget", decision.changes.changed_fields)

        updated = apply_trip_change_set(trip, decision.changes)
        # Modified fields
        self.assertEqual(updated.destination.iata_code, "HND")
        self.assertEqual(updated.duration_days, 7)
        self.assertEqual(updated.budget.amount, Decimal("200000"))
        # Preserved fields
        self.assertEqual(updated.origin.iata_code, "BOM")
        self.assertEqual(updated.passengers.adults, 2)

    # 20. Ambiguous query in active thread preserves context
    def test_ambiguous_query_in_active_thread_preserves_context(self):
        trip = self._sample_trip()
        decision = self.router.classify_intent("Tell me more about the local culture", existing_trip=trip)
        self.assertEqual(decision.intent, TripIntent.FOLLOW_UP)
        self.assertEqual(decision.confidence, 0.60)
        self.assertIn("fallback", decision.source)

    # 21. Immutability of TripRequest when apply_trip_change_set is called
    def test_immutability_of_trip_request_on_change_set(self):
        original = self._sample_trip()
        orig_dest = original.destination.iata_code
        orig_budget = original.budget.amount
        orig_id = original.trip_id

        decision = self.router.classify_intent("Change destination to Tokyo", existing_trip=original)
        updated = apply_trip_change_set(original, decision.changes)

        # Original instance must NOT be modified in place
        self.assertIsNot(original, updated)
        self.assertEqual(original.destination.iata_code, orig_dest)
        self.assertEqual(original.budget.amount, orig_budget)
        self.assertEqual(original.trip_id, orig_id)

        # Updated instance has new destination and is a distinct object
        self.assertEqual(updated.destination.iata_code, "HND")

    # 22. New trip on existing thread guarantees isolation from stale checkpointer state
    @patch("backend.travel_graph.invoke")
    @patch("backend.travel_graph.get_state")
    def test_new_trip_on_existing_thread_isolation_with_checkpointer(self, mock_get_state, mock_invoke):
        stale_trip = self._sample_trip() # BOM -> LHR, 2 adults, 150k
        stale_trip.revision = 3

        # Simulate checkpointer holding stale trip state on this thread
        mock_get_state.return_value = MagicMock(values={
            "trip_request": stale_trip,
            "trip_constraints": trip_request_to_legacy(stale_trip),
            "revision_count": 3,
            "flight_results": "Old London flights",
        })

        captured_initial_state = {}
        def fake_invoke(initial_state, config):
            nonlocal captured_initial_state
            captured_initial_state = initial_state
            return {
                "final_response": "New Tokyo trip planned.",
                "trip_summary": "Trip to Tokyo",
                "approved": False,
            }
        mock_invoke.side_effect = fake_invoke

        res = self.planner.plan_travel(
            user_input="New trip from Delhi to Tokyo",
            thread_id="existing_thread_xyz",
        )

        # 1. Classified as NEW_TRIP
        self.assertEqual(res["intent"], TripIntent.NEW_TRIP.value)

        # 2. Canonical TripRequest passed to graph has zero leakage
        invoked_trip = captured_initial_state["trip_request"]
        self.assertEqual(invoked_trip.origin.iata_code, "DEL")
        self.assertEqual(invoked_trip.destination.iata_code, "HND")
        self.assertNotEqual(invoked_trip.trip_id, stale_trip.trip_id)
        self.assertEqual(invoked_trip.revision, 0)
        self.assertEqual(invoked_trip.passengers.adults, 1)

        # 3. Initial state cleared all prior results and reset revision_count
        self.assertEqual(captured_initial_state["revision_count"], 0)
        self.assertEqual(captured_initial_state["flight_results"], "")


if __name__ == "__main__":
    unittest.main()
