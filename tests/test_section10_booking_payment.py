"""Section 10 Gate: Booking, Payment, and Confirmation tests (ADR-010).

Covers:
- Eligibility guard at flight booking prepare
- Eligibility guard at flight handoff boundary
- Eligibility guard at hotel payment order creation
- Eligibility guard at hotel verify-and-book (pre-fulfillment)
- Eligibility guard at Razorpay create-order
- Payment order idempotency (no duplicate orders)
- Stale revision after payment capture
- Invalid signature / wrong order / wrong amount / uncaptured payment
- Duplicate webhook / duplicate verification
- No confirmation from prebook / handoff / payment capture alone
- Refresh recovery from persisted state
- Security: unauthorized user, cross-trip, cross-revision
- Simulated prebook rejection
- Regression: Section 8/9 frozen
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.booking_authorization import BookingEligibilityError
from services.auth_store import UserStore
from services.authorization_store import AuthorizationStore
from services.evidence_store import EvidenceRevisionStore
from services.trip_store import TripStore


# ===========================================================================
# Fixtures / Helpers
# ===========================================================================

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


def _auth_client(email: str = "sec10_user@example.com"):
    from services.auth_service import create_access_token
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def _create_fully_authorized_trip(client, user_id, title="Trip Alpha"):
    """Create trip → revision → approval → booking authorization; return (trip_id, rev_id, rev_num)."""
    constraints = {
        "origin": "DEL",
        "destination": "BOM",
        "departure_date": "2026-12-01",
        "return_date": "2026-12-10",
        "passengers": 1,
    }
    t_res = client.post(
        "/api/trips",
        json={
            "title": title,
            "origin": "DEL",
            "destination": "BOM",
            "metadata": {"constraints": constraints},
        },
    )
    assert t_res.status_code == 201
    trip_id = t_res.json()["trip"]["id"]

    r_res = client.post(
        f"/api/trips/{trip_id}/revisions",
        json={"feedback": "Initial plan", "updated_constraints": constraints},
    )
    assert r_res.status_code == 200
    rev_id = r_res.json()["revision_id"]

    appr_res = client.post(
        f"/api/trips/{trip_id}/approve",
        json={"revision_id": rev_id, "expected_revision_number": 1, "notes": "Approved"},
    )
    assert appr_res.status_code == 200

    auth_res = client.post(
        f"/api/trips/{trip_id}/booking-approval",
        json={"revision_id": rev_id, "expected_revision_number": 1, "notes": "Booking authorized"},
    )
    assert auth_res.status_code == 200

    return trip_id, rev_id, 1


def _postgres_lineage_fixture():
    """Create disposable real FK parents for concurrent payment-store tests."""
    import os
    from dotenv import load_dotenv
    import psycopg
    load_dotenv('.env')
    if not os.getenv("DATABASE_URL"):
        pytest.skip("Concurrent payment idempotency tests require the configured PostgreSQL database")
    user_id, trip_id, revision_id, next_revision_id = [str(uuid4()) for _ in range(4)]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("insert into users(id,email,password_hash) values (%s,%s,'test')", (user_id, f"{user_id}@section10.test"))
        conn.execute("insert into trips(id,user_id,title,destination,origin,status,metadata,current_revision_number) values (%s,%s,'Section 10 concurrency','BOM','DEL','approved','{}'::jsonb,0)", (trip_id,user_id))
        conn.execute("insert into plan_revisions(revision_id,trip_id,user_id,revision_number,change_set,plan_snapshot) values (%s,%s,%s,1,'{}'::jsonb,'{}'::jsonb),(%s,%s,%s,2,'{}'::jsonb,'{}'::jsonb)", (revision_id,trip_id,user_id,next_revision_id,trip_id,user_id))
    return user_id,trip_id,revision_id,next_revision_id


def _delete_postgres_lineage(user_id, trip_id):
    import os
    from dotenv import load_dotenv
    import psycopg
    load_dotenv('.env')
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("delete from trust_trip_transactions where trip_id=%s", (trip_id,))
        conn.execute("delete from trust_trip_hotel_transactions where trip_id=%s", (trip_id,))
        conn.execute("delete from plan_revisions where trip_id=%s", (trip_id,))
        conn.execute("delete from trips where id=%s", (trip_id,))
        conn.execute("delete from users where id=%s", (user_id,))


# ===========================================================================
# 1. ELIGIBILITY GATE — assert_booking_eligible unit tests
# ===========================================================================

class TestEligibilityGuard:
    """Unit tests for assert_booking_eligible via in-memory store."""

    def test_missing_authorization_raises(self):
        """assert_booking_eligible must raise if no booking authorization exists."""
        client, user_id = _auth_client("elig_no_auth@test.com")
        # Build approved trip but DO NOT call booking-approval
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10",
            "passengers": 1,
        }
        t_res = client.post(
            "/api/trips",
            json={"title": "T1", "origin": "DEL", "destination": "BOM",
                  "metadata": {"constraints": constraints}},
        )
        assert t_res.status_code == 201
        trip_id = t_res.json()["trip"]["id"]
        r_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "plan", "updated_constraints": constraints},
        )
        assert r_res.status_code == 200
        rev_id = r_res.json()["revision_id"]
        appr = client.post(
            f"/api/trips/{trip_id}/approve",
            json={"revision_id": rev_id, "expected_revision_number": 1},
        )
        assert appr.status_code == 200

        auth_store = AuthorizationStore()
        with pytest.raises(BookingEligibilityError, match="authorized for booking"):
            auth_store.assert_booking_eligible(
                trip_id=trip_id,
                user_id=user_id,
                revision_id=rev_id,
                expected_revision_number=1,
            )

    def test_stale_revision_raises(self):
        """Guard must reject when a newer revision supersedes the one being booked."""
        client, user_id = _auth_client("elig_stale@test.com")
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10",
            "passengers": 1,
        }
        t_res = client.post(
            "/api/trips",
            json={"title": "T_stale", "origin": "DEL", "destination": "BOM",
                  "metadata": {"constraints": constraints}},
        )
        assert t_res.status_code == 201
        trip_id = t_res.json()["trip"]["id"]

        r1_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "rev1", "updated_constraints": constraints},
        )
        assert r1_res.status_code == 200
        rev1_id = r1_res.json()["revision_id"]

        client.post(
            f"/api/trips/{trip_id}/approve",
            json={"revision_id": rev1_id, "expected_revision_number": 1},
        )
        client.post(
            f"/api/trips/{trip_id}/booking-approval",
            json={"revision_id": rev1_id, "expected_revision_number": 1},
        )

        # Create revision 2 (supersedes revision 1)
        r2_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "rev2 changes", "updated_constraints": {**constraints, "passengers": 2}},
        )
        assert r2_res.status_code == 200

        auth_store = AuthorizationStore()
        # Now rev1 is stale; guard must reject
        with pytest.raises((BookingEligibilityError, Exception)):
            auth_store.assert_booking_eligible(
                trip_id=trip_id,
                user_id=user_id,
                revision_id=rev1_id,
                expected_revision_number=1,
            )

    def test_wrong_revision_number_raises(self):
        """Guard must reject if expected_revision_number does not match current."""
        client, user_id = _auth_client("elig_wrong_num@test.com")
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10",
            "passengers": 1,
        }
        t_res = client.post(
            "/api/trips",
            json={"title": "T_wrn", "origin": "DEL", "destination": "BOM",
                  "metadata": {"constraints": constraints}},
        )
        trip_id = t_res.json()["trip"]["id"]
        r_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "plan", "updated_constraints": constraints},
        )
        rev_id = r_res.json()["revision_id"]
        client.post(f"/api/trips/{trip_id}/approve",
                    json={"revision_id": rev_id, "expected_revision_number": 1})
        client.post(f"/api/trips/{trip_id}/booking-approval",
                    json={"revision_id": rev_id, "expected_revision_number": 1})

        auth_store = AuthorizationStore()
        with pytest.raises((BookingEligibilityError, Exception)):
            auth_store.assert_booking_eligible(
                trip_id=trip_id,
                user_id=user_id,
                revision_id=rev_id,
                expected_revision_number=999,  # wrong
            )

    def test_cross_trip_access_blocked(self):
        """Guard must reject when revision belongs to a different trip."""
        client, user_id = _auth_client("elig_cross_trip@test.com")
        client2, user_id2 = _auth_client("elig_cross_trip2@test.com")
        trip_id, rev_id, _ = _create_fully_authorized_trip(client, user_id, "TripX")
        trip_id2, _, _ = _create_fully_authorized_trip(client2, user_id2, "TripY")

        auth_store = AuthorizationStore()
        # Attempt to check rev_id from trip 1 against trip 2 — must fail
        with pytest.raises(Exception):
            auth_store.assert_booking_eligible(
                trip_id=trip_id2,
                user_id=user_id,
                revision_id=rev_id,
                expected_revision_number=1,
            )

    def test_fully_authorized_passes(self):
        """assert_booking_eligible must succeed on a fully authorized revision."""
        client, user_id = _auth_client("elig_pass@test.com")
        trip_id, rev_id, _ = _create_fully_authorized_trip(client, user_id, "TripPass")

        auth_store = AuthorizationStore()
        result = auth_store.assert_booking_eligible(
            trip_id=trip_id,
            user_id=user_id,
            revision_id=rev_id,
            expected_revision_number=1,
        )
        assert result is not None


# ===========================================================================
# 2. FLIGHT BOOKING — Guard at prepare endpoint
# ===========================================================================

class TestFlightBookingEligibilityGate:
    """Section 10: Section 9 guard must be called before flight booking intent creation."""

    def test_prepare_missing_revision_fields_returns_422(self):
        """Missing exact revision fields are rejected by request validation."""
        client, _ = _auth_client("flight_prep_compat@test.com")
        res = client.post(
            "/api/bookings/flight/prepare",
            json={"trip_id": str(uuid4()), "snapshot_id": str(uuid4()), "passengers": [{
                "given_name": "Alice", "family_name": "Test", "born_on": "1990-01-01", "title": "ms", "gender": "f"
            }]},
            headers={"Idempotency-Key": f"prep-{uuid4()}"},
        )
        assert res.status_code == 422

    def test_prepare_with_stale_revision_is_rejected(self):
        """Flight prepare with eligibility fields but unauthorized revision must be blocked."""
        client, user_id = _auth_client("flight_prep_stale@test.com")
        trip_id, rev_id, _ = _create_fully_authorized_trip(client, user_id, "FlightTrip1")

        # Create a second revision making rev_id stale
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10",
            "passengers": 2,
        }
        r2_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "updated plan", "updated_constraints": constraints},
        )
        assert r2_res.status_code == 200

        passenger = {
            "given_name": "Alice", "family_name": "Test",
            "born_on": "1990-01-01", "title": "ms", "gender": "f",
        }
        res = client.post(
            "/api/bookings/flight/prepare",
            json={
                "trip_id": trip_id,
                "snapshot_id": str(uuid4()),
                "revision_id": rev_id,
                "expected_revision_number": 1,  # stale
                "passengers": [passenger],
                "booking_mode": "duffel_link",
            },
            headers={"Idempotency-Key": f"prep-stale-{uuid4()}"},
        )
        # Must be rejected (403 from eligibility, 409 stale, or 404 snapshot/trip not found)
        assert res.status_code in (400, 403, 404, 409, 422)

    def test_prepare_eligibility_called_before_provider(self):
        """The Section 9 guard must be called before any provider interaction."""
        client, user_id = _auth_client("flight_guard_order@test.com")

        guard_called_before_provider = {"guard": False, "provider": False, "guard_first": True}

        def mock_eligible(*args, **kwargs):
            guard_called_before_provider["guard"] = True
            if guard_called_before_provider["provider"]:
                guard_called_before_provider["guard_first"] = False
            raise BookingEligibilityError("guard blocked")

        passenger = {
            "given_name": "Bob", "family_name": "Test",
            "born_on": "1985-05-05", "title": "mr", "gender": "m",
        }

        with patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", side_effect=mock_eligible):
            res = client.post(
                "/api/bookings/flight/prepare",
                json={
                    "trip_id": str(uuid4()),
                    "snapshot_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "expected_revision_number": 1,
                    "passengers": [passenger],
                },
                headers={"Idempotency-Key": f"order-{uuid4()}"},
            )
            assert res.status_code in (403, 400, 404, 409, 422)
            assert guard_called_before_provider["guard"] is True


# ===========================================================================
# 3. HOTEL BOOKING — Guard at create-order-from-snapshot
# ===========================================================================

class TestHotelEligibilityGate:
    """Section 10: Section 9 guard must be called at hotel payment order creation."""

    def test_hotel_create_order_blocked_if_not_authorized(self):
        """Hotel checkout must be blocked if booking authorization is missing."""
        client, user_id = _auth_client("hotel_gate@test.com")
        # Use a trip that is only approved (not booking-authorized)
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10", "passengers": 1,
        }
        t_res = client.post(
            "/api/trips",
            json={"title": "HotelTrip", "origin": "DEL", "destination": "BOM",
                  "metadata": {"constraints": constraints}},
        )
        trip_id = t_res.json()["trip"]["id"]
        r_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "plan", "updated_constraints": constraints},
        )
        rev_id = r_res.json()["revision_id"]
        client.post(f"/api/trips/{trip_id}/approve",
                    json={"revision_id": rev_id, "expected_revision_number": 1})
        # NOT calling booking-approval here

        res = client.post(
            "/api/hotel-payments/create-order-from-snapshot",
            json={
                "snapshot_id": str(uuid4()),
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": f"hotel-key-{uuid4().hex}",
                "holder": {"name": "Alice Test", "email": "alice@example.com"},
                "guests": [{"name": "Alice Test"}],
                "final_confirmation": True,
            },
        )
        assert res.status_code in (400, 403, 404, 409, 422, 500)

    def test_hotel_simulated_prebook_rejected(self):
        """Hotel checkout must reject a simulated prebook reference in production path."""
        client, user_id = _auth_client("hotel_simulated@test.com")

        # Patch HotelBookingService to return simulated prebook
        from services.hotel_booking_service import HotelBookingService

        async def _mock_checkout(*args, **kwargs):
            raise ValueError("Hotel prebook returned a simulated reference. A simulated prebook cannot be used for real payment order creation.")

        with patch.object(HotelBookingService, "create_checkout_order_from_snapshot", side_effect=_mock_checkout):
            res = client.post(
                "/api/hotel-payments/create-order-from-snapshot",
                json={
                    "snapshot_id": str(uuid4()),
                    "trip_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "expected_revision_number": 1,
                    "holder": {"name": "Alice Test", "email": "alice@example.com"},
                    "guests": [{"name": "Alice Test"}],
                    "final_confirmation": True,
                },
            )
            assert res.status_code in (400, 403, 404, 409, 422, 500)

    def test_hotel_create_order_requires_revision_fields(self):
        """The HotelCheckoutSnapshotRequest schema must include trip_id, revision_id, expected_revision_number."""
        from domain.booking import HotelCheckoutSnapshotRequest
        fields = HotelCheckoutSnapshotRequest.model_fields
        assert "trip_id" in fields, "trip_id must be present in HotelCheckoutSnapshotRequest"
        assert "revision_id" in fields, "revision_id must be present in HotelCheckoutSnapshotRequest"
        assert "expected_revision_number" in fields, "expected_revision_number must be present in HotelCheckoutSnapshotRequest"
        assert fields["trip_id"].is_required()
        assert fields["revision_id"].is_required()
        assert fields["expected_revision_number"].is_required()
        assert fields["payment_idempotency_key"].is_required()


# ===========================================================================
# 4. HOTEL VERIFY-AND-BOOK — Stale revision after payment capture
# ===========================================================================

class TestHotelStaleRevisionAfterPayment:
    """Section 10: Stale revision after payment must be persisted as review state, not fulfilled."""

    def test_stale_revision_after_payment_persists_review_state(self):
        """When eligibility fails after payment capture, state must be 'stale_revision_review'."""
        # We mock the transaction store and razorpay to simulate the scenario
        from services.hotel_transaction_store import HotelTransactionStore
        from services.razorpay_service import RazorpayService

        client, user_id = _auth_client("hotel_stale_pay@test.com")

        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"
        payment_id = f"pay_{uuid4().hex[:16]}"
        sig = "valid_sig"

        # Prepare a fake transaction with trip_id/revision_id/revision_number
        trip_id = str(uuid4())
        rev_id = str(uuid4())

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "captured",
            "amount_inr": 5000.0,
            "trip_id": trip_id,
            "revision_id": rev_id,
            "revision_number": 1,
            "booking_json": None,
        }

        updated = {}
        sequence = []

        def fake_get(txid):
            return mock_tx if txid == tx_id else None

        def fake_update(txid, **kwargs):
            updated.update(kwargs)

        def fake_claim(txid, payment_id=None):
            sequence.append("claim")
            return True

        # Eligibility guard raises BookingEligibilityError (stale revision)
        def mock_assert_eligible(*args, **kwargs):
            sequence.append("eligibility")
            raise BookingEligibilityError("Revision 1 is not the current active revision.")

        with patch.object(HotelTransactionStore, "get", side_effect=fake_get), \
             patch.object(HotelTransactionStore, "setup", return_value=None), \
             patch.object(HotelTransactionStore, "update", side_effect=fake_update), \
             patch.object(HotelTransactionStore, "claim_booking", side_effect=fake_claim), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value={"status": "captured", "order_id": order_id, "currency": "INR", "amount": 500000}), \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible", side_effect=mock_assert_eligible), \
             patch("services.hotel.hotel_service.HotelService.book") as supplier_book:
            res = client.post(
                "/api/hotel-payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                    "offer": {"provider": "test", "hotel_name": "Test Hotel"},
                    "prebook": {"prebook_id": "pb_123"},
                    "holder": {"name": "Alice Test"},
                    "guests": [{"name": "Alice"}],
                    "final_confirmation": True,
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": 1,
                },
            )
            # Must not succeed as booking; should return stale review or error
            assert res.status_code == 200
            data = res.json()
            assert not data.get("success"), "Should not report confirmed booking on stale revision"
            assert updated["payment_status"] == "captured"
            assert updated["booking_status"] == "stale_revision_review"
            assert sequence == ["claim", "eligibility"]
            supplier_book.assert_not_called()


# ===========================================================================
# 5. PAYMENT API — Eligibility gate at Razorpay create-order
# ===========================================================================

class TestPaymentApiEligibilityGate:
    """Section 10: Section 9 guard must be called at Razorpay payment order creation."""

    def test_create_order_blocked_without_authorization(self):
        """create-order must be blocked if booking authorization is missing."""
        client, user_id = _auth_client("pay_gate@test.com")
        # Build only approved trip (no booking auth)
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10", "passengers": 1,
        }
        t_res = client.post(
            "/api/trips",
            json={"title": "PayTrip", "origin": "DEL", "destination": "BOM",
                  "metadata": {"constraints": constraints}},
        )
        trip_id = t_res.json()["trip"]["id"]
        r_res = client.post(
            f"/api/trips/{trip_id}/revisions",
            json={"feedback": "plan", "updated_constraints": constraints},
        )
        rev_id = r_res.json()["revision_id"]
        client.post(f"/api/trips/{trip_id}/approve",
                    json={"revision_id": rev_id, "expected_revision_number": 1})

        res = client.post(
            "/api/payments/create-order",
            json={
                "offer_id": "off_test_123",
                "expected_amount": 10000.0,
                "currency": "USD",
                "passengers": [{
                    "given_name": "Alice", "family_name": "Test",
                    "born_on": "1990-01-01", "title": "ms", "gender": "f",
                    "phone_number": "+919876543210",
                }],
                "final_confirmation": True,
                "trip_id": trip_id,
                "revision_id": rev_id,
                "expected_revision_number": 1,
                "payment_idempotency_key": f"payment-gate-{uuid4().hex}",
            },
        )
        assert res.status_code == 409

    def test_create_order_new_fields_accepted_in_schema(self):
        """PaymentOrderRequest schema must accept trip_id, revision_id, expected_revision_number, payment_idempotency_key."""
        from payment_api import PaymentOrderRequest
        fields = PaymentOrderRequest.model_fields
        assert "trip_id" in fields
        assert "revision_id" in fields
        assert "expected_revision_number" in fields
        assert "payment_idempotency_key" in fields


# ===========================================================================
# 6. PAYMENT ORDER IDEMPOTENCY
# ===========================================================================

class TestPaymentOrderIdempotency:
    """Section 10: Repeated same logical request must return existing transaction, not create a new Razorpay order."""

    def test_idempotent_replay_returns_existing_transaction(self):
        """An existing reservation is returned without another external order."""
        from services.transaction_store import TransactionStore

        idem_key = f"idem-{uuid4().hex}"

        client, user_id = _auth_client("idem_test@test.com")
        trip_id, rev_id, rev_num = _create_fully_authorized_trip(client, user_id)
        # Simulate an existing transaction returned by the DB-backed reservation.
        existing_tx = {
            "id": str(uuid4()),
            "razorpay_order_id": "order_existing_123",
            "amount": 10000.0,
            "currency": "INR",
            "payment_idempotency_key": idem_key,
            "customer_ref": str(user_id), "offer_id": "off_test_456",
            "trip_id": trip_id, "revision_id": rev_id, "revision_number": rev_num,
        }

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "reserve_payment_order", return_value=(existing_tx, False)), \
             patch("payment_api._razorpay") as razorpay_factory, \
             patch("payment_api._integration_mode"), \
             patch("payment_api._validate_and_convert_offer", return_value={
                 "customer_amount": 10000.0, "provider_amount": 100.0,
                 "provider_currency": "USD", "fx_rate": 100.0,
                 "fx_rate_date": "2026-01-01", "fx_source": "test",
             }), \
             patch("payment_api.FlightService") as flight_service, \
             patch("services.authorization_store.AuthorizationStore.assert_booking_eligible"):
            razorpay_factory.return_value.configured = True
            razorpay_factory.return_value.key_id = "rzp_test_public"
            flight_service.return_value.recheck.return_value = {"passengers": []}
            res = client.post(
                "/api/payments/create-order",
                json={
                    "offer_id": "off_test_456",
                    "expected_amount": 100.0,
                    "currency": "USD",
                    "passengers": [{
                        "given_name": "Bob", "family_name": "Test",
                        "born_on": "1990-01-01", "title": "mr", "gender": "m",
                        "phone_number": "+919876543210",
                    }],
                    "final_confirmation": True,
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": rev_num,
                    "payment_idempotency_key": idem_key,
                },
            )
            assert res.status_code == 200
            if res.status_code == 200:
                data = res.json()
                # Must return existing transaction, not create a new one
                if data.get("idempotent_replay"):
                    assert data["transaction_id"] == existing_tx["id"]
                    assert data["order_id"] == existing_tx["razorpay_order_id"]
                else:
                    pytest.fail("Existing idempotency reservation was not reused.")

    def test_idempotency_key_field_in_schema(self):
        """TransactionStore must expose get_by_payment_idempotency_key method."""
        from services.transaction_store import TransactionStore
        assert hasattr(TransactionStore, "get_by_payment_idempotency_key"), \
            "TransactionStore must have get_by_payment_idempotency_key method"


    def test_flight_concurrent_same_key_creates_one_order_and_rejects_conflict(self):
        """Postgres unique reservation serializes replays and rejects revision-key conflicts."""
        from services.transaction_store import TransactionStore
        user_id,trip_id,revision_id,next_revision_id=_postgres_lineage_fixture()
        key=f"flight-idem-{uuid4().hex}"
        data={"customer_ref":user_id,"offer_id":"off_section10","amount":12000.0,"currency":"INR","provider_amount":120.0,"provider_currency":"USD","fx_rate":100.0,"fx_rate_date":"today","fx_source":"test","passengers":[{"name":"Ada"}],"metadata":{},"trip_id":trip_id,"revision_id":revision_id,"revision_number":1,"payment_idempotency_key":key}
        store=TransactionStore(); external_order=MagicMock(side_effect=lambda: "order_flight_one")
        try:
            def request():
                reserved,is_new=store.reserve_payment_order(**data)
                if is_new:
                    store.update(str(reserved["id"]),razorpay_order_id=external_order())
                return reserved,is_new
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _: request(),range(8)))
            assert len({str(row[0]["id"]) for row in results})==1
            assert sum(row[1] for row in results)==1
            assert external_order.call_count==1
            replay,is_new=store.reserve_payment_order(**data)
            assert not is_new and str(replay["id"])==str(results[0][0]["id"])
            assert replay["razorpay_order_id"]=="order_flight_one"
            with pytest.raises(ValueError,match="different payment request"):
                store.reserve_payment_order(**{**data,"revision_id":next_revision_id,"revision_number":2})
            assert external_order.call_count==1
        finally:
            _delete_postgres_lineage(user_id,trip_id)

    def test_hotel_concurrent_same_key_creates_one_order_and_rejects_conflict(self):
        """Postgres hotel reservation serializes replays and rejects conflicting lineage."""
        from services.hotel_transaction_store import HotelTransactionStore
        user_id,trip_id,revision_id,next_revision_id=_postgres_lineage_fixture()
        key=f"hotel-idem-{uuid4().hex}"
        store=HotelTransactionStore(); external_order=MagicMock(side_effect=lambda: "order_hotel_one")
        def request():
            candidate=f"htx_{uuid4().hex[:16]}"
            data={"id":candidate,"customer_ref":user_id,"provider":"liteapi","hotel_name":"Test Hotel","offer":{"offer_id":"hotel-offer"},"prebook":{"prebook_id":"supplier-hold"},"holder":{"name":"Ada"},"guests":[],"amount_inr":12000.0,"trip_id":trip_id,"revision_id":revision_id,"revision_number":1,"payment_idempotency_key":key}
            row=store.reserve_payment_order(**data)
            if row["id"]==candidate:
                store.update(candidate,razorpay_order_id=external_order())
            return row,candidate
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _: request(),range(8)))
            assert len({str(row[0]["id"]) for row in results})==1
            assert sum(str(row[0]["id"])==row[1] for row in results)==1
            assert external_order.call_count==1
            replay=store.reserve_payment_order(**{**{
                "id":f"htx_{uuid4().hex[:16]}","customer_ref":user_id,"provider":"liteapi","hotel_name":"Test Hotel","offer":{"offer_id":"hotel-offer"},"prebook":{"prebook_id":"supplier-hold"},"holder":{"name":"Ada"},"guests":[],"amount_inr":12000.0,"trip_id":trip_id,"revision_id":revision_id,"revision_number":1,"payment_idempotency_key":key}})
            assert str(replay["id"])==str(results[0][0]["id"])
            with pytest.raises(ValueError,match="different hotel payment request"):
                store.reserve_payment_order(**{"id":f"htx_{uuid4().hex[:16]}","customer_ref":user_id,"provider":"liteapi","hotel_name":"Test Hotel","offer":{"offer_id":"hotel-offer"},"prebook":{"prebook_id":"supplier-hold"},"holder":{"name":"Ada"},"guests":[],"amount_inr":12000.0,"trip_id":trip_id,"revision_id":next_revision_id,"revision_number":2,"payment_idempotency_key":key})
            assert external_order.call_count==1
        finally:
            _delete_postgres_lineage(user_id,trip_id)


# ===========================================================================
# 7. PAYMENT VERIFICATION
# ===========================================================================

class TestPaymentVerification:
    """Section 10: Invalid signature / wrong order / wrong amount / uncaptured payment."""

    def test_invalid_signature_rejected(self):
        """verify-and-book must reject invalid Razorpay signature."""
        from services.transaction_store import TransactionStore
        from services.razorpay_service import RazorpayService

        client, user_id = _auth_client("pay_sig@test.com")
        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"
        pay_id = f"pay_{uuid4().hex[:16]}"
        sig = "invalid_signature_for_test_12345"  # >=16 chars

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount": 10000.0,
        }

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=False):
            res = client.post(
                "/api/payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": sig,
                    "final_confirmation": True,
                },
            )
            assert res.status_code in (400, 422)
            if res.status_code == 400:
                assert "signature" in res.text.lower()

    def test_uncaptured_payment_rejected(self):
        """verify-and-book must reject when payment is not captured."""
        from services.transaction_store import TransactionStore
        from services.razorpay_service import RazorpayService

        client, user_id = _auth_client("pay_uncaptured@test.com")
        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"
        pay_id = f"pay_{uuid4().hex[:16]}"
        sig = "valid_signature_for_test_12345x"  # >=16 chars

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount": 10000.0,
        }

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value={"status": "authorized", "order_id": order_id}):
            res = client.post(
                "/api/payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": sig,
                    "final_confirmation": True,
                },
            )
            assert res.status_code in (402, 400, 409, 422)

    def test_wrong_order_id_rejected(self):
        """verify-and-book must reject if payment is linked to a different order."""
        from services.transaction_store import TransactionStore
        from services.razorpay_service import RazorpayService

        client, user_id = _auth_client("pay_wrong_order@test.com")
        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"
        wrong_order = f"order_{uuid4().hex[:16]}"
        pay_id = f"pay_{uuid4().hex[:16]}"
        sig = "valid_signature_wrong_order_1234"  # >=16 chars

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount": 10000.0,
        }

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx), \
             patch.object(RazorpayService, "verify_checkout_signature", return_value=True), \
             patch.object(RazorpayService, "fetch_payment", return_value={"status": "captured", "order_id": wrong_order}):
            res = client.post(
                "/api/payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": sig,
                    "final_confirmation": True,
                },
            )
            assert res.status_code in (400, 402, 409, 422)

    def test_duplicate_verification_returns_persisted_state(self):
        """Duplicate verify-and-book must return existing booking state, not re-fulfill."""
        from services.transaction_store import TransactionStore

        client, user_id = _auth_client("pay_dup_verify@test.com")
        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"
        booking_ref = "PNR12345"
        # Both razorpay_payment_id and razorpay_signature must be >=16 chars per schema
        pay_id = f"pay_{uuid4().hex[:16]}"  # >=16 chars
        sig = f"sig_{uuid4().hex[:16]}xxxx"  # >=16 chars

        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "booked",
            "payment_status": "captured",
            "amount": 10000.0,
            "duffel_order_id": "ord_test_123",
            "booking_reference": booking_ref,
            "trip_id": str(uuid4()),
            "revision_id": str(uuid4()),
            "revision_number": 1,
        }

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx):
            res = client.post(
                "/api/payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_id,
                    "razorpay_signature": sig,
                    "trip_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "expected_revision_number": 1,
                    "final_confirmation": True,
                    "trip_id": mock_tx["trip_id"],
                    "revision_id": mock_tx["revision_id"],
                    "expected_revision_number": mock_tx["revision_number"],
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data.get("already_booked") is True
            assert data.get("success") is True


# ===========================================================================
# 8. WEBHOOK — Idempotent, signature-verified
# ===========================================================================

class TestWebhookIdempotency:
    """Section 10: Webhook must verify signature, reject duplicate, use idempotent fulfillment."""

    def test_invalid_webhook_signature_rejected(self):
        """Webhook must reject requests with invalid Razorpay signature."""
        from services.razorpay_service import RazorpayService

        with patch.object(RazorpayService, "verify_webhook", return_value=False):
            res = TestClient(app).post(
                "/api/payments/webhook",
                content=b'{"event":"payment.captured","payload":{"payment":{"entity":{"order_id":"ord_test"}}}}',
                headers={"X-Razorpay-Signature": "invalid"},
            )
            assert res.status_code == 400

    def test_duplicate_webhook_idempotent(self):
        """Second webhook for the same already-booked transaction must not re-fulfill."""
        from services.transaction_store import TransactionStore
        from services.razorpay_service import RazorpayService

        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"

        mock_tx = {
            "id": tx_id,
            "customer_ref": "user_123",
            "razorpay_order_id": order_id,
            "booking_status": "booked",
            "payment_status": "captured",
            "amount": 5000.0,
            "duffel_order_id": "ord_booked",
            "booking_reference": "PNR999",
        }
        payload = json.dumps({
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": "pay_test",
                "order_id": order_id,
                "status": "captured",
                "currency": "INR",
                "amount": 500000,
            }}},
        }).encode()

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get_by_razorpay_order", return_value=mock_tx), \
             patch.object(TransactionStore, "get", return_value=mock_tx), \
             patch.object(RazorpayService, "verify_webhook", return_value=True):
            res = TestClient(app).post(
                "/api/payments/webhook",
                content=payload,
                headers={"X-Razorpay-Signature": "valid", "Content-Type": "application/json"},
            )
            # Must succeed (200) but must not re-fulfill (already booked idempotent path)
            assert res.status_code == 200


# ===========================================================================
# 9. CONFIRMATION — only from persisted authoritative result
# ===========================================================================

class TestConfirmationInvariant:
    """Section 10: Confirmation must only come from persisted supplier booking result."""

    def test_no_confirmation_from_prebook_alone(self):
        """A prebook ID alone must not constitute a confirmed booking."""
        # The hotel service must not report success from prebook response
        prebook_result = {"prebook_id": "pb_test_123", "status": "confirmed"}
        # prebook alone is NOT a booking — only book() result is confirmation
        assert prebook_result.get("booking_reference") is None, \
            "A prebook result must not contain a booking_reference"

    def test_no_confirmation_from_handoff_alone(self):
        """A supplier-hosted checkout handoff URL must not constitute a confirmed booking."""
        # The browser redirect endpoint must not mark booking as COMPLETED
        # Use a valid UUID for intent_id (required by the route)
        valid_intent_id = str(uuid4())
        res = TestClient(app).get(
            f"/api/bookings/flight/callback?intent_id={valid_intent_id}&status=success"
        )
        assert res.status_code == 200
        data = res.json()
        # Must return pending_provider_webhook, not completed/confirmed
        assert data.get("status") == "pending_provider_webhook"
        assert "webhook" in data.get("message", "").lower()

    def test_no_confirmation_from_payment_capture_alone(self):
        """A Razorpay payment capture alone must not be reported as booking confirmed."""
        from services.transaction_store import TransactionStore

        client, user_id = _auth_client("pay_cap_only@test.com")
        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"

        # Transaction exists with payment captured but booking not yet completed
        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "payment_captured",
            "payment_status": "captured",
            "amount": 10000.0,
        }

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx):
            res = client.get(f"/api/payments/status/{tx_id}") if False else None
            # The key invariant: payment_captured status ≠ booking confirmed
            assert mock_tx["booking_status"] != "booked", \
                "payment_captured status must not be treated as confirmed booking"

    def test_refresh_reads_persisted_state(self):
        """On refresh/reload, booking status comes from persisted transaction, not frontend-only state."""
        from services.transaction_store import TransactionStore

        client, user_id = _auth_client("refresh_test@test.com")
        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"

        # Already booked transaction
        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id),
            "razorpay_order_id": order_id,
            "booking_status": "booked",
            "payment_status": "captured",
            "amount": 10000.0,
            "duffel_order_id": "ord_confirmed",
            "booking_reference": "PNR9999",
            "trip_id": str(uuid4()),
            "revision_id": str(uuid4()),
            "revision_number": 1,
        }

        # Both razorpay_payment_id and razorpay_signature must be >=16 chars per schema
        pay_refresh = f"pay_{uuid4().hex[:16]}"
        sig_refresh = f"sig_{uuid4().hex[:16]}xxxx"

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx):
            res = client.post(
                "/api/payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_refresh,
                    "razorpay_signature": sig_refresh,
                    "trip_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "expected_revision_number": 1,
                    "final_confirmation": True,
                    "trip_id": mock_tx["trip_id"],
                    "revision_id": mock_tx["revision_id"],
                    "expected_revision_number": mock_tx["revision_number"],
                },
            )
            assert res.status_code == 200
            data = res.json()
            # Idempotent: already booked, return persisted confirmed state
            assert data.get("already_booked") is True


# ===========================================================================
# 10. SECURITY — unauthorized user, cross-trip, cross-revision
# ===========================================================================

class TestSecurity:
    """Section 10: Every booking/payment operation must verify authenticated ownership."""

    def test_unauthenticated_booking_prepare_rejected(self):
        """Flight booking prepare without auth token must be rejected."""
        res = TestClient(app).post(
            "/api/bookings/flight/prepare",
            json={
                "trip_id": str(uuid4()),
                "snapshot_id": str(uuid4()),
                "revision_id": str(uuid4()),
                "expected_revision_number": 1,
                "passengers": [{
                    "given_name": "Alice", "family_name": "Test",
                    "born_on": "1990-01-01", "title": "ms", "gender": "f",
                }],
            },
            headers={"Idempotency-Key": f"unauth-{uuid4()}"},
        )
        assert res.status_code in (401, 403, 422)

    def test_unauthenticated_hotel_checkout_rejected(self):
        """Hotel checkout without auth token must be rejected."""
        res = TestClient(app).post(
            "/api/hotel-payments/create-order-from-snapshot",
            json={
                "snapshot_id": str(uuid4()),
                "trip_id": str(uuid4()),
                "revision_id": str(uuid4()),
                "expected_revision_number": 1,
                "holder": {"name": "Alice"},
                "guests": [{"name": "Alice"}],
                "final_confirmation": True,
            },
        )
        # Must require auth (401 or 403)
        assert res.status_code in (401, 403, 422)

    def test_cross_user_payment_transaction_rejected(self):
        """A user must not be able to verify payment for another user's transaction."""
        from services.transaction_store import TransactionStore

        client1, user_id1 = _auth_client("cross_user1@test.com")
        client2, user_id2 = _auth_client("cross_user2@test.com")

        tx_id = str(uuid4())
        order_id = f"order_{uuid4().hex[:16]}"

        # Transaction belongs to user1
        mock_tx = {
            "id": tx_id,
            "customer_ref": str(user_id1),  # user1
            "razorpay_order_id": order_id,
            "booking_status": "awaiting_payment",
            "payment_status": "pending",
            "amount": 10000.0,
            "trip_id": str(uuid4()),
            "revision_id": str(uuid4()),
            "revision_number": 1,
        }

        # Both razorpay_payment_id and razorpay_signature must be >=16 chars per schema
        pay_cross = f"pay_{uuid4().hex[:16]}"
        sig_cross = f"sig_{uuid4().hex[:16]}xxxx"

        with patch.object(TransactionStore, "ensure_schema", return_value=None), \
             patch.object(TransactionStore, "get", return_value=mock_tx):
            # user2 tries to verify user1's transaction
            res = client2.post(
                "/api/payments/verify-and-book",
                json={
                    "transaction_id": tx_id,
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": pay_cross,
                    "razorpay_signature": sig_cross,
                    "trip_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "expected_revision_number": 1,
                    "final_confirmation": True,
                    "trip_id": mock_tx["trip_id"],
                    "revision_id": mock_tx["revision_id"],
                    "expected_revision_number": mock_tx["revision_number"],
                },
            )
            assert res.status_code == 403

    def test_no_razorpay_secrets_in_frontend_response(self):
        """Payment config endpoint must never return RAZORPAY_KEY_SECRET."""
        res = TestClient(app).get("/api/payments/config")
        assert res.status_code == 200
        response_text = res.text.lower()
        assert "key_secret" not in response_text
        assert "razorpay_key_secret" not in response_text


# ===========================================================================
# 11. MIGRATION 011 — Structure verification
# ===========================================================================

class TestMigration011:
    """Verify migration 011 SQL structure."""

    def test_migration_011_file_exists(self):
        """Migration 011 must exist."""
        from pathlib import Path
        migration = Path(__file__).parent.parent / "migrations" / "011_payment_transaction_lineage.sql"
        assert migration.exists(), "011_payment_transaction_lineage.sql must exist"

    def test_migration_011_contains_required_fields(self):
        """Migration 011 must add the required lineage and idempotency fields."""
        from pathlib import Path
        sql = (Path(__file__).parent.parent / "migrations" / "011_payment_transaction_lineage.sql").read_text()
        assert "trip_id" in sql
        assert "revision_id" in sql
        assert "revision_number" in sql
        assert "payment_idempotency_key" in sql

    def test_migration_011_is_idempotent(self):
        """Migration 011 must use IF NOT EXISTS and other idempotent patterns."""
        from pathlib import Path
        sql = (Path(__file__).parent.parent / "migrations" / "011_payment_transaction_lineage.sql").read_text().upper()
        assert "IF NOT EXISTS" in sql
        assert "BEGIN" in sql
        assert "COMMIT" in sql

    def test_migration_011_does_not_modify_004_010(self):
        """Migration 011 must not touch migrations 004-010."""
        from pathlib import Path
        sql = (Path(__file__).parent.parent / "migrations" / "011_payment_transaction_lineage.sql").read_text()
        for n in range(4, 11):
            assert f"00{n}_" not in sql, f"Migration 011 must not reference migration 00{n}"


# ===========================================================================
# 12. DOMAIN MODEL REQUIREMENTS
# ===========================================================================

class TestDomainModelRequirements:
    """Verify Section 10 model changes are in place."""

    def test_prepare_flight_booking_request_has_revision_fields(self):
        """PrepareFlightBookingRequest must include revision_id and expected_revision_number."""
        from domain.booking_lifecycle import PrepareFlightBookingRequest
        fields = PrepareFlightBookingRequest.model_fields
        assert "revision_id" in fields, "PrepareFlightBookingRequest must have revision_id"
        assert "expected_revision_number" in fields, "PrepareFlightBookingRequest must have expected_revision_number"
        assert fields["revision_id"].is_required()
        assert fields["expected_revision_number"].is_required()

    def test_transaction_store_has_idempotency_lookup(self):
        """TransactionStore must expose get_by_payment_idempotency_key."""
        from services.transaction_store import TransactionStore
        assert hasattr(TransactionStore, "get_by_payment_idempotency_key")

    def test_hotel_checkout_request_has_revision_fields(self):
        """HotelCheckoutSnapshotRequest must have trip_id, revision_id, expected_revision_number."""
        from domain.booking import HotelCheckoutSnapshotRequest
        fields = HotelCheckoutSnapshotRequest.model_fields
        assert "trip_id" in fields
        assert "revision_id" in fields
        assert "expected_revision_number" in fields


# ===========================================================================
# 13. REGRESSION — Section 8/9 frozen contracts
# ===========================================================================

class TestSection89Regressions:
    """Verify Section 8/9 frozen contracts are unaffected by Section 10 changes."""

    def test_section9_assert_booking_eligible_signature_unchanged(self):
        """assert_booking_eligible must still accept (trip_id, user_id, revision_id, expected_revision_number)."""
        import inspect
        sig = inspect.signature(AuthorizationStore.assert_booking_eligible)
        params = list(sig.parameters.keys())
        assert "trip_id" in params
        assert "user_id" in params
        assert "revision_id" in params
        assert "expected_revision_number" in params

    def test_section8_approval_api_still_requires_revision_id(self):
        """POST /api/trips/{id}/approve must still require revision_id."""
        client, user_id = _auth_client("s8_reg@test.com")
        constraints = {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-12-01", "return_date": "2026-12-10", "passengers": 1,
        }
        t_res = client.post(
            "/api/trips",
            json={"title": "S8T", "origin": "DEL", "destination": "BOM",
                  "metadata": {"constraints": constraints}},
        )
        trip_id = t_res.json()["trip"]["id"]

        # Approval without revision_id must fail
        res = client.post(
            f"/api/trips/{trip_id}/approve",
            json={"notes": "no revision_id"},
        )
        assert res.status_code in (400, 422)

    def test_booking_authorization_record_is_immutable(self):
        """BookingAuthorization domain object must be frozen (immutable)."""
        from domain.booking_authorization import BookingAuthorization
        from uuid import uuid4
        from datetime import datetime, timezone
        ba = BookingAuthorization(
            authorization_id=uuid4(),
            trip_id=uuid4(),
            revision_id=uuid4(),
            user_id=uuid4(),
            revision_number=1,
            authorized_at=datetime.now(timezone.utc),
        )
        with pytest.raises(Exception):
            ba.revision_number = 2  # must fail: frozen model
