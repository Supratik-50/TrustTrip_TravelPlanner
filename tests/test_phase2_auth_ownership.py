import os
import unittest
import uuid
from unittest.mock import patch, MagicMock

# Force in-memory auth and trip stores for test isolation
os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "test-secret-key-for-phase-2-ownership-suite-with-sufficient-length-64-bytes-long"
os.environ["AUTH_SECRET"] = "test-secret-key-for-phase-2-ownership-suite-with-sufficient-length-64-bytes-long"

from fastapi.testclient import TestClient
from app import app
from services.auth_store import UserStore
from services.trip_store import TripStore
from services.auth_service import hash_password, verify_password, create_access_token, decode_access_token


class Phase2AuthAndOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        # Clear in-memory stores between tests
        UserStore._IN_MEMORY_USERS.clear()
        TripStore._IN_MEMORY_TRIPS.clear()
        TripStore._IN_MEMORY_THREADS.clear()

    def _create_user(self, email: str, password: str = "P@ssword123!"):
        res = self.client.post("/api/auth/signup", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 201, f"Failed to create user: {res.text}")
        data = res.json()
        return data["user"], data["access_token"]

    def _auth_headers(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    # 1. Signup
    def test_signup(self):
        res = self.client.post("/api/auth/signup", json={"email": "alice@example.com", "password": "SecurePassword1!"})
        self.assertEqual(res.status_code, 201)
        body = res.json()
        self.assertIn("user", body)
        self.assertIn("access_token", body)
        self.assertEqual(body["user"]["email"], "alice@example.com")
        self.assertNotIn("password_hash", body["user"])

    # 2. Duplicate email rejection
    def test_duplicate_email_rejection(self):
        self._create_user("duplicate@example.com", "Password123!")
        res = self.client.post("/api/auth/signup", json={"email": "duplicate@example.com", "password": "AnotherPassword!"})
        self.assertEqual(res.status_code, 409)
        self.assertIn("already exists", res.json()["detail"].lower())

    # 3. Login
    def test_login(self):
        self._create_user("bob@example.com", "MyPass1234!")
        res = self.client.post("/api/auth/login", json={"email": "bob@example.com", "password": "MyPass1234!"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("access_token", body)
        self.assertEqual(body["user"]["email"], "bob@example.com")

    # 4. Invalid password rejection
    def test_invalid_password_rejection(self):
        self._create_user("charlie@example.com", "CorrectPass1!")
        res = self.client.post("/api/auth/login", json={"email": "charlie@example.com", "password": "WrongPassword!"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("invalid", res.json()["detail"].lower())

    # 5. Authenticated /me
    def test_authenticated_me(self):
        user, token = self._create_user("dan@example.com")
        res = self.client.get("/api/auth/me", headers=self._auth_headers(token))
        self.assertEqual(res.status_code, 200)
        body = res.json()["user"]
        self.assertEqual(body["id"], user["id"])
        self.assertEqual(body["email"], "dan@example.com")
        self.assertNotIn("password_hash", body)

    # 6. Unauthenticated protected endpoint rejection
    def test_unauthenticated_protected_endpoint_rejection(self):
        res_trips = self.client.get("/api/trips")
        self.assertEqual(res_trips.status_code, 401)

        res_post_trip = self.client.post("/api/trips", json={"title": "Paris"})
        self.assertEqual(res_post_trip.status_code, 401)

        res_travel = self.client.post("/api/travel", json={"message": "Go to Tokyo"})
        self.assertEqual(res_travel.status_code, 401)

        res_approve = self.client.post("/api/travel/approve", json={"thread_id": "thread-123"})
        self.assertEqual(res_approve.status_code, 401)

    # 7. User A can create a trip
    def test_user_a_can_create_trip(self):
        user_a, token_a = self._create_user("usera@example.com")
        res = self.client.post(
            "/api/trips",
            json={"title": "Goa Getaway", "destination": "GOI", "origin": "BOM", "budget": 15000},
            headers=self._auth_headers(token_a),
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["trip"]["title"], "Goa Getaway")
        self.assertEqual(str(data["trip"]["user_id"]), user_a["id"])

    # 8. User B cannot access User A's trip
    def test_user_b_cannot_access_user_a_trip(self):
        user_a, token_a = self._create_user("user_a_8@example.com")
        user_b, token_b = self._create_user("user_b_8@example.com")

        res_create = self.client.post(
            "/api/trips",
            json={"title": "Secret Trip A"},
            headers=self._auth_headers(token_a),
        )
        trip_id = str(res_create.json()["trip"]["id"])

        # User B attempts to access User A's trip
        res_b = self.client.get(f"/api/trips/{trip_id}", headers=self._auth_headers(token_b))
        self.assertEqual(res_b.status_code, 404)

    # 9. User B cannot delete User A's trip
    def test_user_b_cannot_delete_user_a_trip(self):
        user_a, token_a = self._create_user("user_a_9@example.com")
        user_b, token_b = self._create_user("user_b_9@example.com")

        res_create = self.client.post(
            "/api/trips",
            json={"title": "Undeletable Trip"},
            headers=self._auth_headers(token_a),
        )
        trip_id = str(res_create.json()["trip"]["id"])

        # User B tries to delete
        res_del = self.client.delete(f"/api/trips/{trip_id}", headers=self._auth_headers(token_b))
        self.assertEqual(res_del.status_code, 404)

        # User A verifies trip still exists
        res_get = self.client.get(f"/api/trips/{trip_id}", headers=self._auth_headers(token_a))
        self.assertEqual(res_get.status_code, 200)

    # 10. User A only sees their own trips
    def test_user_a_only_sees_their_own_trips(self):
        user_a, token_a = self._create_user("user_a_10@example.com")
        user_b, token_b = self._create_user("user_b_10@example.com")

        # User A creates 2 trips
        self.client.post("/api/trips", json={"title": "Trip A1"}, headers=self._auth_headers(token_a))
        self.client.post("/api/trips", json={"title": "Trip A2"}, headers=self._auth_headers(token_a))

        # User B creates 1 trip
        self.client.post("/api/trips", json={"title": "Trip B1"}, headers=self._auth_headers(token_b))

        # List trips for User A
        res_a = self.client.get("/api/trips", headers=self._auth_headers(token_a))
        self.assertEqual(res_a.status_code, 200)
        trips_a = res_a.json()["trips"]
        self.assertEqual(len(trips_a), 2)
        titles_a = [t["title"] for t in trips_a]
        self.assertIn("Trip A1", titles_a)
        self.assertIn("Trip A2", titles_a)
        self.assertNotIn("Trip B1", titles_a)

        # List trips for User B
        res_b = self.client.get("/api/trips", headers=self._auth_headers(token_b))
        self.assertEqual(res_b.status_code, 200)
        trips_b = res_b.json()["trips"]
        self.assertEqual(len(trips_b), 1)
        self.assertEqual(trips_b[0]["title"], "Trip B1")

    # 11. User B cannot access User A's thread
    def test_user_b_cannot_access_user_a_thread(self):
        user_a, token_a = self._create_user("user_a_11@example.com")
        user_b, token_b = self._create_user("user_b_11@example.com")

        # User A creates trip and thread
        trip_res = self.client.post("/api/trips", json={"title": "Trip A"}, headers=self._auth_headers(token_a))
        trip_id = str(trip_res.json()["trip"]["id"])

        thread_res = self.client.post(f"/api/trips/{trip_id}/threads", headers=self._auth_headers(token_a))
        self.assertEqual(thread_res.status_code, 201)
        thread_id = str(thread_res.json()["thread"]["id"])

        # Direct thread ownership check
        trip_store = TripStore()
        self.assertTrue(trip_store.verify_thread_ownership(thread_id, user_a["id"]))
        self.assertFalse(trip_store.verify_thread_ownership(thread_id, user_b["id"]))

    # 12. User B cannot resume User A's LangGraph thread
    def test_user_b_cannot_resume_user_a_langgraph_thread(self):
        user_a, token_a = self._create_user("user_a_12@example.com")
        user_b, token_b = self._create_user("user_b_12@example.com")

        trip_res = self.client.post("/api/trips", json={"title": "Trip A"}, headers=self._auth_headers(token_a))
        trip_id = str(trip_res.json()["trip"]["id"])
        thread_res = self.client.post(f"/api/trips/{trip_id}/threads", headers=self._auth_headers(token_a))
        self.assertEqual(thread_res.status_code, 201)
        thread_id = str(thread_res.json()["thread"]["id"])

        # User B attempts to call /api/travel with User A's thread_id
        res = self.client.post(
            "/api/travel",
            json={"message": "Hijack thread", "thread_id": thread_id},
            headers=self._auth_headers(token_b),
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("does not belong", res.json()["detail"])

        # User B attempts to call /api/travel/approve with User A's thread_id
        res_app = self.client.post(
            "/api/travel/approve",
            json={"thread_id": thread_id, "approved": True},
            headers=self._auth_headers(token_b),
        )
        self.assertEqual(res_app.status_code, 403)
        self.assertIn("does not belong", res_app.json()["detail"])

    # 13. Client-supplied user_id cannot impersonate another user
    def test_client_supplied_user_id_cannot_impersonate_another_user(self):
        user_a, token_a = self._create_user("user_a_13@example.com")
        user_b, token_b = self._create_user("user_b_13@example.com")

        # User A passes user_b's ID in request body
        res = self.client.post(
            "/api/trips",
            json={"title": "Impersonation Attempt", "user_id": user_b["id"]},
            headers=self._auth_headers(token_a),
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()["trip"]
        # Server MUST assign user_a's ID, completely ignoring client-supplied user_id
        self.assertEqual(str(data["user_id"]), user_a["id"])
        self.assertNotEqual(str(data["user_id"]), user_b["id"])

    # 14. Client-supplied customer_ref cannot bypass ownership
    def test_client_supplied_customer_ref_cannot_bypass_ownership(self):
        user_a, token_a = self._create_user("user_a_14@example.com")
        user_b, token_b = self._create_user("user_b_14@example.com")

        # In GET /api/trips?customer_ref=<user_a.id> sent by User B
        res = self.client.get(
            f"/api/trips?customer_ref={user_a['id']}",
            headers=self._auth_headers(token_b),
        )
        self.assertEqual(res.status_code, 200)
        # Result must be empty for User B because server scopes query strictly to authenticated user_b
        self.assertEqual(len(res.json()["trips"]), 0)

    # 15. Thread creation verifies trip ownership
    def test_thread_creation_verifies_trip_ownership(self):
        user_a, token_a = self._create_user("user_a_15@example.com")
        user_b, token_b = self._create_user("user_b_15@example.com")

        trip_res = self.client.post("/api/trips", json={"title": "Trip A"}, headers=self._auth_headers(token_a))
        trip_id = str(trip_res.json()["trip"]["id"])

        # User B attempts to create a thread on User A's trip
        res = self.client.post(f"/api/trips/{trip_id}/threads", headers=self._auth_headers(token_b))
        self.assertEqual(res.status_code, 404)
        self.assertIn("not found", res.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
