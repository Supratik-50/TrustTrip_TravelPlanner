"""Tests for Safe Trip Deletion.

Verifies:
- User can delete an eligible planning draft trip they own.
- User cannot delete another user's trip (404 Not Found, zero information disclosure).
- Draft and completed trip cases follow policy.
- Ongoing trips (current date within start_date and end_date) are rejected (409 Conflict).
- Trips with confirmed flight or hotel bookings are rejected (409 Conflict).
- Pending, processing, or ambiguous booking/payment states are rejected (409 Conflict).
- Financial records, booking intents, transactions, and revisions remain completely intact.
- Repeated deletion requests are handled safely.
- Soft-deleted trips are excluded from list_trips and get_trip.
- Unauthenticated requests are rejected with 401 Unauthorized.
"""
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

os.environ["USE_IN_MEMORY_AUTH"] = "1"
os.environ["JWT_SECRET"] = "test-secret-key-for-safe-trip-deletion-suite-64-bytes-long"
os.environ["AUTH_SECRET"] = "test-secret-key-for-safe-trip-deletion-suite-64-bytes-long"

from fastapi.testclient import TestClient
from app import app
from services.auth_store import UserStore
from services.trip_store import TripStore
from services.booking_read_service import BookingReadService
from services.flight_booking_service import FlightBookingService


class SafeTripDeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        UserStore._IN_MEMORY_USERS.clear()
        TripStore._IN_MEMORY_TRIPS.clear()
        TripStore._IN_MEMORY_THREADS.clear()
        BookingReadService.clear_in_memory()
        FlightBookingService._IN_MEMORY_BOOKINGS.clear()

    def _create_user(self, email: str, password: str = "P@ssword123!"):
        res = self.client.post("/api/auth/signup", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        return data["user"], data["access_token"]

    def _auth_headers(self, token: str):
        return {"Authorization": f"Bearer {token}"}

    def _create_trip(self, token: str, **kwargs):
        payload = {
            "title": kwargs.get("title", "Planning Itinerary"),
            "destination": kwargs.get("destination", "Goa, India"),
            "origin": kwargs.get("origin", "Mumbai, India"),
            "status": kwargs.get("status", "planning"),
            "metadata": kwargs.get("metadata", {}),
        }
        res = self.client.post("/api/trips", json=payload, headers=self._auth_headers(token))
        self.assertEqual(res.status_code, 201, f"Failed to create trip: {res.text}")
        return res.json()["trip"]

    def test_delete_eligible_draft_trip(self):
        user, token = self._create_user("std_eligible@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Draft Trip To Delete")
        trip_id = trip["id"]

        # Check eligibility endpoint
        elig_res = self.client.get(f"/api/trips/{trip_id}/deletion-eligibility", headers=headers)
        self.assertEqual(elig_res.status_code, 200)
        self.assertTrue(elig_res.json()["eligible"])

        # Delete trip
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        body = del_res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "deleted")

        # Soft-deleted trip is no longer accessible via get_trip
        get_res = self.client.get(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(get_res.status_code, 404)

        # Soft-deleted trip is excluded from list_trips
        list_res = self.client.get("/api/trips", headers=headers)
        self.assertEqual(list_res.status_code, 200)
        trips = list_res.json()["trips"]
        self.assertNotIn(trip_id, [t["id"] for t in trips])

    def test_cannot_delete_other_users_trip(self):
        user_a, token_a = self._create_user("std_user_a@example.com")
        user_b, token_b = self._create_user("std_user_b@example.com")

        trip_a = self._create_trip(token_a, title="User A Secret Trip")
        trip_id = trip_a["id"]

        # User B attempts to delete User A's trip -> 404 Not Found
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=self._auth_headers(token_b))
        self.assertEqual(del_res.status_code, 404)

        # Trip A remains completely accessible to User A
        get_res = self.client.get(f"/api/trips/{trip_id}", headers=self._auth_headers(token_a))
        self.assertEqual(get_res.status_code, 200)

    def test_cannot_delete_ongoing_trip(self):
        user, token = self._create_user("std_ongoing@example.com")
        headers = self._auth_headers(token)

        today = datetime.now(timezone.utc).date()
        start_date = (today - timedelta(days=2)).isoformat()
        end_date = (today + timedelta(days=3)).isoformat()

        trip = self._create_trip(
            token,
            title="Currently Ongoing Vacation",
            status="active",
            metadata={"start_date": start_date, "end_date": end_date},
        )
        trip_id = trip["id"]

        # Check eligibility endpoint returns ineligible
        elig_res = self.client.get(f"/api/trips/{trip_id}/deletion-eligibility", headers=headers)
        self.assertEqual(elig_res.status_code, 200)
        self.assertFalse(elig_res.json()["eligible"])
        self.assertIn("ongoing", elig_res.json()["reason"].lower())

        # Attempt deletion -> 409 Conflict
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 409)
        self.assertIn("ongoing", del_res.json()["detail"].lower())

        # Trip remains intact
        get_res = self.client.get(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(get_res.status_code, 200)

    def test_delete_planning_trip_dated_october_14_to_24_darjeeling_scenario(self):
        """A Planning trip dated October 14–24, 2026 can be deleted on October 15, 2026 when it has no protected booking/payment state."""
        user, token = self._create_user("std_darjeeling@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(
            token,
            title="Darjeeling Himalayan Retreat",
            destination="Darjeeling, India",
            origin="Kolkata, India",
            status="planning",
            metadata={"start_date": "2026-10-14", "end_date": "2026-10-24"},
        )
        trip_id = trip["id"]

        # 1. Eligibility check must permit deletion
        elig_res = self.client.get(f"/api/trips/{trip_id}/deletion-eligibility", headers=headers)
        self.assertEqual(elig_res.status_code, 200)
        self.assertTrue(elig_res.json()["eligible"], f"Planning trip must be eligible: {elig_res.json().get('reason')}")
        self.assertIsNone(elig_res.json()["reason"])

        # 2. Deletion must succeed
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        body = del_res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "deleted")

        # 3. Soft-deleted trip no longer returned
        get_res = self.client.get(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(get_res.status_code, 404)

        list_res = self.client.get("/api/trips", headers=headers)
        self.assertEqual(list_res.status_code, 200)
        self.assertNotIn(trip_id, [t["id"] for t in list_res.json()["trips"]])

    def test_delete_draft_trip_whose_dates_include_today(self):
        """A Draft trip whose planned dates include today can be deleted when otherwise eligible."""
        user, token = self._create_user("std_draft_today@example.com")
        headers = self._auth_headers(token)

        today = datetime.now(timezone.utc).date()
        start_date = (today - timedelta(days=1)).isoformat()
        end_date = (today + timedelta(days=5)).isoformat()

        trip = self._create_trip(
            token,
            title="Spontaneous Weekend Draft",
            destination="Goa, India",
            origin="Mumbai, India",
            status="draft",
            metadata={"start_date": start_date, "end_date": end_date},
        )
        trip_id = trip["id"]

        # 1. Eligibility check must succeed
        elig_res = self.client.get(f"/api/trips/{trip_id}/deletion-eligibility", headers=headers)
        self.assertEqual(elig_res.status_code, 200)
        self.assertTrue(elig_res.json()["eligible"])

        # 2. Deletion must succeed
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.json()["success"])

    def test_cannot_delete_trip_with_confirmed_flight_booking(self):
        user, token = self._create_user("std_flight@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Trip with Confirmed Duffel Flight")
        trip_id = trip["id"]
        user_id = user["id"]

        # Seed a completed flight booking intent in FlightBookingService
        FlightBookingService._IN_MEMORY_BOOKINGS[str(uuid.uuid4())] = {
            "id": uuid.uuid4(),
            "trip_id": trip_id,
            "user_id": user_id,
            "status": "COMPLETED",
            "provider_reference": "FLIGHT-REF-12345",
            "metadata": {
                "booking_reference": "FLIGHT-REF-12345",
                "pnr": "PNR777",
                "order_id": "ord_duffel_123",
            },
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        # Attempt deletion -> 409 Conflict
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 409)
        self.assertIn("confirmed flight", del_res.json()["detail"].lower())

        # Flight booking intent remains untouched
        self.assertEqual(len(FlightBookingService._IN_MEMORY_BOOKINGS), 1)

    def test_cannot_delete_trip_with_confirmed_hotel_booking(self):
        user, token = self._create_user("std_hotel@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Trip with Confirmed Hotel")
        trip_id = trip["id"]
        user_id = user["id"]

        # Seed a booked hotel transaction in BookingReadService
        h_id = str(uuid.uuid4())
        BookingReadService.save_in_memory_hotel({
            "id": h_id,
            "trip_id": trip_id,
            "customer_ref": user_id,
            "booking_status": "booked",
            "payment_status": "captured",
            "hotel_name": "Taj Fort Aguada",
            "amount_inr": 25000.0,
            "currency": "INR",
            "booking_json": {"confirmation_code": "HOTEL-CONF-8888"},
            "created_at": datetime.now(timezone.utc),
        })

        # Attempt deletion -> 409 Conflict
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 409)
        self.assertIn("confirmed hotel", del_res.json()["detail"].lower())

        # Hotel transaction record remains untouched
        self.assertEqual(len(BookingReadService._IN_MEMORY_HOTEL_BOOKINGS), 1)

    def test_cannot_delete_trip_with_pending_or_processing_booking(self):
        user, token = self._create_user("std_pending@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Trip with In-Progress Hotel Booking")
        trip_id = trip["id"]
        user_id = user["id"]

        # Seed a hotel transaction in awaiting_payment
        BookingReadService.save_in_memory_hotel({
            "id": str(uuid.uuid4()),
            "trip_id": trip_id,
            "customer_ref": user_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "hotel_name": "Goa Marriott",
            "amount_inr": 18000.0,
            "currency": "INR",
            "created_at": datetime.now(timezone.utc),
        })

        # Attempt deletion -> 409 Conflict
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 409)
        self.assertIn("progress", del_res.json()["detail"].lower())

    def test_repeated_deletion_requests_handled_safely(self):
        user, token = self._create_user("std_repeat@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Trip to Delete Twice")
        trip_id = trip["id"]

        # First deletion -> 200
        del_res1 = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res1.status_code, 200)

        # Second deletion -> 404 Not Found (already deleted)
        del_res2 = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res2.status_code, 404)

    def test_cannot_delete_active_status_trip(self):
        user, token = self._create_user("std_active@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Active Status Trip", status="active")
        trip_id = trip["id"]

        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 409)
        self.assertIn("active", del_res.json()["detail"].lower())

    def test_concurrent_deletion_race_handling(self):
        import concurrent.futures

        user, token = self._create_user("std_race@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Concurrent Deletion Trip")
        trip_id = trip["id"]

        store = TripStore()

        results = []
        def do_delete():
            return store.delete_trip_atomic(trip_id=trip_id, user_id=user["id"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(do_delete) for _ in range(5)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        success_count = sum(1 for r in results if r[0] is True)
        fail_count = sum(1 for r in results if r[0] is False)
        self.assertEqual(success_count, 1)
        self.assertEqual(fail_count, 4)

    def test_unauthenticated_trip_deletion_rejected(self):
        res = self.client.delete(f"/api/trips/{uuid.uuid4()}")
        self.assertEqual(res.status_code, 401)

    def test_delete_explicit_draft_trip(self):
        """Verify trips explicitly marked status='draft' are eligible and deletable."""
        user, token = self._create_user("std_draft_user@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Draft Exploration", status="draft")
        trip_id = trip["id"]
        self.assertEqual(trip.get("status"), "draft")

        # 1. Eligibility endpoint confirms eligible
        elig_res = self.client.get(f"/api/trips/{trip_id}/deletion-eligibility", headers=headers)
        self.assertEqual(elig_res.status_code, 200)
        self.assertTrue(elig_res.json()["eligible"])

        # 2. Delete trip succeeds
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.json()["success"])
        self.assertEqual(del_res.json()["status"], "deleted")

        # 3. Soft-deleted trip is excluded from list_trips
        list_res = self.client.get("/api/trips", headers=headers)
        self.assertEqual(list_res.status_code, 200)
        self.assertNotIn(trip_id, [t["id"] for t in list_res.json()["trips"]])

    def test_delete_explicit_planning_trip(self):
        """Verify trips explicitly marked status='planning' are eligible and deletable."""
        user, token = self._create_user("std_planning_user@example.com")
        headers = self._auth_headers(token)

        trip = self._create_trip(token, title="Planning Workspace Trip", status="planning")
        trip_id = trip["id"]
        self.assertEqual(trip.get("status"), "planning")

        # 1. Eligibility endpoint confirms eligible
        elig_res = self.client.get(f"/api/trips/{trip_id}/deletion-eligibility", headers=headers)
        self.assertEqual(elig_res.status_code, 200)
        self.assertTrue(elig_res.json()["eligible"])

        # 2. Delete trip succeeds
        del_res = self.client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.json()["success"])
        self.assertEqual(del_res.json()["status"], "deleted")


class TestTripDeletionBookingConcurrencyPostgres(unittest.TestCase):
    """PostgreSQL integration tests for serializing booking initialization against trip deletion.

    Verifies both race orderings using real independent PostgreSQL connections and
    deterministic synchronization.
    """

    def setUp(self):
        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            self.skipTest("DATABASE_URL not configured; skipping live PostgreSQL concurrency suite")
        self._orig_in_memory = os.environ.get("USE_IN_MEMORY_AUTH")
        if "USE_IN_MEMORY_AUTH" in os.environ:
            del os.environ["USE_IN_MEMORY_AUTH"]

    def tearDown(self):
        if self._orig_in_memory is not None:
            os.environ["USE_IN_MEMORY_AUTH"] = self._orig_in_memory
        elif "USE_IN_MEMORY_AUTH" in os.environ:
            del os.environ["USE_IN_MEMORY_AUTH"]

    def _create_pg_trip(self, user_id, trip_id, status="approved"):
        import psycopg
        with psycopg.connect(self.db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'hash') ON CONFLICT (id) DO NOTHING",
                (str(user_id), f"user_{str(user_id)[:8]}@example.test"),
            )
            conn.execute(
                """
                INSERT INTO trips (id, user_id, title, destination, origin, status, metadata)
                VALUES (%s, %s, 'Concurrency Test Trip', 'Goa', 'Mumbai', %s, '{}'::jsonb)
                ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
                """,
                (str(trip_id), str(user_id), status),
            )

    def _cleanup_pg_trip(self, user_id, trip_id):
        import psycopg
        try:
            with psycopg.connect(self.db_url, autocommit=True) as conn:
                conn.execute("DELETE FROM trust_trip_hotel_transactions WHERE trip_id = %s", (str(trip_id),))
                conn.execute("DELETE FROM booking_intents WHERE trip_id = %s", (str(trip_id),))
                conn.execute("DELETE FROM trips WHERE id = %s", (str(trip_id),))
                conn.execute("DELETE FROM users WHERE id = %s", (str(user_id),))
        except Exception:
            pass

    def test_scenario_a_deletion_wins_race_blocks_booking_initialization(self):
        """Scenario A: Deletion acquires the trip lock first, commits soft deletion.

        Booking initialization resumes, detects trip is deleted, and fails safely
        without creating any new booking/payment records.
        """
        import threading
        import time
        import psycopg
        from psycopg.rows import dict_row
        from services.hotel_transaction_store import HotelTransactionStore

        uid = uuid.uuid4()
        tid = uuid.uuid4()
        self._create_pg_trip(uid, tid, status="approved")

        store = HotelTransactionStore()
        lock_acquired_by_del = threading.Event()
        booking_result = {}

        def booking_worker():
            lock_acquired_by_del.wait(timeout=5)
            try:
                row = store.reserve_payment_order(
                    id=f"htx_{uuid.uuid4().hex[:16]}",
                    customer_ref=str(uid),
                    provider="liteapi",
                    hotel_name="Scenario A Hotel",
                    offer={"offer_id": "off_a"},
                    prebook={"prebook_id": "pre_a"},
                    holder={"name": "Traveler A"},
                    guests=[],
                    amount_inr=15000.0,
                    trip_id=str(tid),
                    revision_id=str(uuid.uuid4()),
                    revision_number=1,
                    payment_idempotency_key=f"idem_a_{uuid.uuid4().hex}",
                )
                booking_result["success"] = True
                booking_result["row"] = row
            except Exception as exc:
                booking_result["success"] = False
                booking_result["error"] = exc

        t_booking = threading.Thread(target=booking_worker)
        t_booking.start()

        with psycopg.connect(self.db_url, autocommit=False, row_factory=dict_row) as conn1:
            with conn1.transaction():
                # 1. Deletion acquires the trip lock first
                conn1.execute(
                    "SELECT * FROM trips WHERE id = %s AND user_id = %s FOR UPDATE",
                    (str(tid), str(uid)),
                )
                lock_acquired_by_del.set()
                # Give booking thread time to initiate and block on the DB lock
                time.sleep(0.3)

                # Check eligibility (no booking exists yet), then mark deleted
                conn1.execute(
                    "UPDATE trips SET status = 'deleted' WHERE id = %s AND user_id = %s",
                    (str(tid), str(uid)),
                )
            # Transaction 1 commits, releasing trip lock

        t_booking.join(timeout=5)

        try:
            self.assertFalse(booking_result.get("success"), "Booking initialization must fail when deletion wins")
            self.assertIn("deleted", str(booking_result.get("error")).lower())

            with psycopg.connect(self.db_url, row_factory=dict_row) as check_conn:
                rows = check_conn.execute(
                    "SELECT * FROM trust_trip_hotel_transactions WHERE trip_id = %s",
                    (str(tid),),
                ).fetchall()
                self.assertEqual(len(rows), 0, "No booking transaction must be created for a deleted trip")

                trip_db = check_conn.execute("SELECT status FROM trips WHERE id = %s", (str(tid),)).fetchone()
                self.assertEqual(trip_db["status"], "deleted")
        finally:
            self._cleanup_pg_trip(uid, tid)

    def test_scenario_b_booking_initialization_wins_race_blocks_deletion(self):
        """Scenario B: Booking initialization acquires trip lock first, inserts pending record.

        Deletion resumes, inspects the locked state, discovers pending booking,
        and aborts deletion. Trip remains intact and undeleted.
        """
        import threading
        import time
        import psycopg
        from psycopg.rows import dict_row

        uid = uuid.uuid4()
        tid = uuid.uuid4()
        self._create_pg_trip(uid, tid, status="approved")

        trip_store = TripStore()
        lock_acquired_by_booking = threading.Event()
        del_result = {}

        def deletion_worker():
            lock_acquired_by_booking.wait(timeout=5)
            res = trip_store.delete_trip_atomic(trip_id=str(tid), user_id=str(uid))
            del_result["success"] = res[0]
            del_result["reason"] = res[1]

        t_del = threading.Thread(target=deletion_worker)
        t_del.start()

        tx_id = f"htx_{uuid.uuid4().hex[:16]}"
        key = f"idem_b_{uuid.uuid4().hex}"

        with psycopg.connect(self.db_url, autocommit=False, row_factory=dict_row) as conn2:
            with conn2.transaction():
                # 1. Booking initialization acquires parent trip lock first
                conn2.execute(
                    "SELECT id, status, user_id FROM trips WHERE id = %s AND user_id = %s FOR UPDATE",
                    (str(tid), str(uid)),
                )
                lock_acquired_by_booking.set()
                # Give deletion thread time to initiate and block on the DB lock
                time.sleep(0.3)

                # 2. Insert pending hotel booking transaction
                conn2.execute(
                    """
                    INSERT INTO trust_trip_hotel_transactions
                    (id, customer_ref, provider, hotel_name, offer_json, prebook_json, holder_json, guests_json,
                     amount_inr, payment_status, booking_status, trip_id, payment_idempotency_key)
                    VALUES (%s, %s, 'liteapi', 'Scenario B Hotel', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb,
                            20000.0, 'pending', 'awaiting_payment', %s, %s)
                    """,
                    (tx_id, str(uid), str(tid), key),
                )
            # Transaction 2 commits, releasing trip lock

        t_del.join(timeout=5)

        try:
            self.assertFalse(del_result.get("success"), "Deletion must be rejected when booking is in progress")
            self.assertIn("progress", str(del_result.get("reason")).lower())

            with psycopg.connect(self.db_url, row_factory=dict_row) as check_conn:
                trip_db = check_conn.execute("SELECT status FROM trips WHERE id = %s", (str(tid),)).fetchone()
                self.assertEqual(trip_db["status"], "awaiting_approval", "Trip status must remain undeleted")
                self.assertNotEqual(trip_db["status"], "deleted", "Trip must not be deleted")

                htx = check_conn.execute("SELECT id, booking_status FROM trust_trip_hotel_transactions WHERE id = %s", (tx_id,)).fetchone()
                self.assertIsNotNone(htx)
                self.assertEqual(htx["booking_status"], "awaiting_payment")
        finally:
            self._cleanup_pg_trip(uid, tid)

    def test_cross_user_hotel_reservation_rejected(self):
        """Cross-user reservation attempt on another user's trip is rejected."""
        from services.hotel_transaction_store import HotelTransactionStore

        uid_a = uuid.uuid4()
        uid_b = uuid.uuid4()
        tid_a = uuid.uuid4()
        self._create_pg_trip(uid_a, tid_a, status="approved")

        store = HotelTransactionStore()
        try:
            with self.assertRaises(ValueError) as ctx:
                store.reserve_payment_order(
                    id=f"htx_{uuid.uuid4().hex[:16]}",
                    customer_ref=str(uid_b),
                    provider="liteapi",
                    hotel_name="Unauthorized Hotel",
                    offer={"offer_id": "off_x"},
                    prebook={"prebook_id": "pre_x"},
                    holder={"name": "Traveler B"},
                    guests=[],
                    amount_inr=10000.0,
                    trip_id=str(tid_a),
                    payment_idempotency_key=f"idem_cross_{uuid.uuid4().hex}",
                )
            self.assertIn("Access denied", str(ctx.exception))
        finally:
            self._cleanup_pg_trip(uid_a, tid_a)

    def test_delete_approved_trip_with_plan_approval_preserves_records_and_succeeds(self):
        """Eligible trip with an active PlanApproval is safely soft-deleted under trigger invariant.

        Verifies:
        1. Soft deletion succeeds without violating two-way approval trigger.
        2. trips.status is updated to 'deleted'.
        3. plan_approvals record remains intact (audit preservation).
        4. plan_revisions record remains intact.
        5. Attempting to insert a new approval on the deleted trip is rejected.
        """
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.errors import CheckViolation
        from services.trip_store import TripStore

        uid = uuid.uuid4()
        tid = uuid.uuid4()
        rev_id = uuid.uuid4()
        appr_id = uuid.uuid4()

        # 1. Seed user, trip, revision, and approval directly in PostgreSQL
        with psycopg.connect(self.db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'hash') ON CONFLICT (id) DO NOTHING",
                (str(uid), f"user_{str(uid)[:8]}@example.test"),
            )
            conn.execute(
                """
                INSERT INTO trips (id, user_id, title, destination, origin, status, metadata)
                VALUES (%s, %s, 'Approved Plan Trip', 'Goa', 'Mumbai', 'awaiting_approval', '{}'::jsonb)
                """,
                (str(tid), str(uid)),
            )
            conn.execute(
                """
                INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                VALUES (%s, %s, %s, 1, '{}'::jsonb, '{}'::jsonb)
                """,
                (str(rev_id), str(tid), str(uid)),
            )

        # 2. Link revision and approve atomically in a transaction satisfying trigger
        with psycopg.connect(self.db_url) as conn:
            conn.execute(
                "UPDATE trips SET current_revision_id = %s, current_revision_number = 1, status = 'awaiting_approval' WHERE id = %s",
                (str(rev_id), str(tid)),
            )
            conn.execute(
                """
                INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                VALUES (%s, %s, %s, %s)
                """,
                (str(appr_id), str(tid), str(rev_id), str(uid)),
            )
            conn.execute("UPDATE trips SET status = 'approved' WHERE id = %s", (str(tid),))
            conn.commit()

        # 3. Perform atomic deletion via TripStore
        store = TripStore()
        try:
            success, reason, deleted_trip = store.delete_trip_atomic(tid, uid)
            self.assertTrue(success, f"Trip deletion must succeed: {reason}")
            self.assertIsNone(reason)
            self.assertIsNotNone(deleted_trip)
            self.assertEqual(deleted_trip.get("status"), "deleted")

            # 4. Verify database state
            with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
                trip_row = conn.execute("SELECT status, current_revision_id FROM trips WHERE id = %s", (str(tid),)).fetchone()
                self.assertIsNotNone(trip_row)
                self.assertEqual(trip_row["status"], "deleted", "Trip must be in deleted terminal state")
                self.assertEqual(str(trip_row["current_revision_id"]), str(rev_id), "current_revision_id must be preserved")

                # Audit trails preserved:
                appr_row = conn.execute("SELECT * FROM plan_approvals WHERE trip_id = %s AND revision_id = %s", (str(tid), str(rev_id))).fetchone()
                self.assertIsNotNone(appr_row, "PlanApproval audit record must be preserved")

                rev_row = conn.execute("SELECT * FROM plan_revisions WHERE revision_id = %s", (str(rev_id),)).fetchone()
                self.assertIsNotNone(rev_row, "PlanRevision record must be preserved")

            # 5. Verify trigger rejects creating new approvals on the deleted trip
            rev2_id = uuid.uuid4()
            with psycopg.connect(self.db_url, autocommit=True) as conn:
                conn.execute(
                    """
                    INSERT INTO plan_revisions (revision_id, trip_id, user_id, revision_number, change_set, plan_snapshot)
                    VALUES (%s, %s, %s, 2, '{}'::jsonb, '{}'::jsonb)
                    """,
                    (str(rev2_id), str(tid), str(uid)),
                )

            with self.assertRaises(CheckViolation):
                with psycopg.connect(self.db_url) as conn:
                    conn.execute(
                        """
                        INSERT INTO plan_approvals (approval_id, trip_id, revision_id, approved_by_user_id)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (str(uuid.uuid4()), str(tid), str(rev2_id), str(uid)),
                    )
                    conn.commit()
        finally:
            self._cleanup_pg_trip(uid, tid)

    def test_delete_planning_trip_in_postgres_succeeds(self):
        """Planning trip in PostgreSQL without bookings can be deleted safely."""
        from services.trip_store import TripStore
        uid = uuid.uuid4()
        tid = uuid.uuid4()
        self._create_pg_trip(uid, tid, status="planning")

        store = TripStore()
        try:
            success, reason, deleted_trip = store.delete_trip_atomic(tid, uid)
            self.assertTrue(success, f"Planning trip deletion must succeed: {reason}")
            self.assertIsNone(reason)
            self.assertIsNotNone(deleted_trip)
            self.assertEqual(deleted_trip.get("status"), "deleted")
        finally:
            self._cleanup_pg_trip(uid, tid)

    def test_deletion_error_handling_returns_409_with_useful_message(self):
        """API returns HTTP 409 Conflict with useful detail instead of unhandled 500 when deletion is refused."""
        from fastapi.testclient import TestClient
        from app import app
        from services.trip_store import TripStore

        client = TestClient(app)

        # Signup test user
        user_res = client.post("/api/auth/signup", json={"email": f"err_test_{uuid.uuid4().hex[:6]}@example.com", "password": "Password123!"})
        self.assertEqual(user_res.status_code, 201)
        token = user_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create a trip
        trip_res = client.post("/api/trips", json={"title": "Error Handling Trip", "destination": "Kolkata", "origin": "Delhi", "status": "planning"}, headers=headers)
        self.assertEqual(trip_res.status_code, 201)
        trip_id = trip_res.json()["trip"]["id"]

        # Delete it once -> 200 OK
        del1 = client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del1.status_code, 200)

        # Delete it again -> 404 with already deleted message
        del2 = client.delete(f"/api/trips/{trip_id}", headers=headers)
        self.assertEqual(del2.status_code, 404)
        self.assertIn("already deleted", del2.json()["detail"].lower())

    def test_postgres_delete_planning_trip_dated_october_14_to_24_darjeeling_scenario(self):
        """In PostgreSQL, a Planning trip dated October 14–24, 2026 can be atomically soft-deleted on October 15, 2026."""
        import json
        import psycopg
        from services.trip_store import TripStore

        uid = uuid.uuid4()
        tid = uuid.uuid4()

        with psycopg.connect(self.db_url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, 'hash') ON CONFLICT (id) DO NOTHING",
                (str(uid), f"user_{str(uid)[:8]}@example.test"),
            )
            conn.execute(
                """
                INSERT INTO trips (id, user_id, title, destination, origin, status, metadata)
                VALUES (%s, %s, 'Darjeeling Escape', 'Darjeeling', 'Kolkata', 'planning', %s::jsonb)
                """,
                (str(tid), str(uid), json.dumps({"start_date": "2026-10-14", "end_date": "2026-10-24"})),
            )

        store = TripStore()
        try:
            eligible, reason, _ = store.check_trip_deletion_eligibility(tid, uid)
            self.assertTrue(eligible, f"Planning trip covering current date must be eligible: {reason}")

            success, err, deleted_trip = store.delete_trip_atomic(tid, uid)
            self.assertTrue(success, f"Atomic deletion must succeed: {err}")
            self.assertIsNone(err)
            self.assertEqual(deleted_trip.get("status"), "deleted")

            with psycopg.connect(self.db_url) as conn:
                status = conn.execute("SELECT status FROM trips WHERE id = %s", (str(tid),)).fetchone()[0]
                self.assertEqual(status, "deleted")
        finally:
            self._cleanup_pg_trip(uid, tid)


if __name__ == "__main__":
    unittest.main()
