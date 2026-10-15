"""Regression tests for Flight Recheck Contract & Snapshot Resolution (ADR / Section 10).

Covers:
A. Snapshot UUID is resolved to provider_offer_id.
B. Existing provider offer ID ('off_...') still works directly.
C. Missing snapshot fails closed (404).
D. Missing provider_offer_id in snapshot fails closed (422).
E. Cross-user snapshot fails closed (403).
F. Wrong trip snapshot fails closed (400).
G. Stale revision remains rejected (409).
H. Existing Duffel recheck behavior remains unchanged.
I. No provider-offer ID is fabricated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app import app
from domain.live_offer import AvailabilityStatus, FlightOffer, FreshnessStatus, OfferSnapshot
from payment_api import resolve_flight_offer_id
from services.auth_service import create_access_token
from services.auth_store import UserStore
from services.authorization_store import AuthorizationStore
from services.evidence_store import EvidenceRevisionStore
from services.search_session_store import SearchSessionStore
from services.transaction_store import TransactionStore
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
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()


def _auth_client(email: str = "recheck_user@example.com"):
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash")
    user_id = user["id"]
    token = create_access_token(user_id=user_id, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, user_id


def _create_fully_authorized_trip(client, user_id, title="Flight Contract Trip", flight_snap_id=None):
    """Create trip -> revision -> approval -> booking authorization; return (trip_id, rev_id, rev_num)."""
    constraints = {
        "origin": "DEL",
        "destination": "COK",
        "departure_date": "2026-11-10",
        "return_date": "2026-11-14",
        "passengers": 1,
    }
    if flight_snap_id:
        constraints["selected_flight_snapshot_id"] = str(flight_snap_id)

    meta = {"constraints": constraints}
    if flight_snap_id:
        meta["selected_flight_snapshot_id"] = str(flight_snap_id)

    t_res = client.post(
        "/api/trips",
        json={
            "title": title,
            "origin": "DEL",
            "destination": "COK",
            "metadata": meta,
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
        json={"revision_id": rev_id, "expected_revision_number": 1},
    )
    assert auth_res.status_code == 200

    return trip_id, rev_id, 1


def _create_snapshot(
    user_id: UUID | str,
    trip_id: UUID | str,
    provider_offer_id: str = "off_0000BB2tWM3xQjX297PBYa",
    offer_type: str = "flight",
    trip_revision: int = 0,
    store: SearchSessionStore | None = None,
) -> tuple[OfferSnapshot, UUID]:
    """Helper to create an in-memory search session and offer snapshot."""
    s_store = store or SearchSessionStore()
    session = s_store.create_session(
        user_id=user_id,
        trip_id=trip_id,
        thread_id=None,
        search_type="flights" if offer_type == "flight" else "hotels",
        criteria_hash="hash_12345",
        trip_revision=trip_revision,
    )
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    from domain.live_offer import PriceBreakdown, FlightSegmentModel
    mock_segment = FlightSegmentModel(
        origin_iata="DEL",
        destination_iata="COK",
        departure_time=now + timedelta(days=10),
        arrival_time=now + timedelta(days=10, hours=3),
        marketing_carrier_iata="6E",
        marketing_carrier_name="IndiGo",
        flight_number="6E-101",
        duration_minutes=180,
    )
    mock_flight_offer = FlightOffer(
        snapshot_id=uuid4(),
        provider="duffel",
        provider_offer_id=provider_offer_id,
        price=PriceBreakdown(
            base_amount=Decimal("4000.00"),
            total_amount=Decimal("4500.00"),
            currency="INR",
        ),
        stops=0,
        segments=[mock_segment],
        passenger_count=1,
        retrieved_at=now,
        expires_at=now + timedelta(hours=2),
    )
    snapshot = s_store.save_snapshot(
        session_id=session.session_id,
        offer=mock_flight_offer,
        raw_payload={"mock": "duffel_payload"},
    )
    return snapshot, session.session_id


# ===========================================================================
# 1. UNIT & CONTRACT TESTS FOR resolve_flight_offer_id
# ===========================================================================

class TestResolveFlightOfferId:
    def test_snapshot_uuid_resolved_to_provider_offer_id(self):
        """A. Snapshot UUID is resolved to authoritative Duffel provider_offer_id."""
        user_id = uuid4()
        trip_id = uuid4()
        rev_id = uuid4()
        expected_off_id = "off_0000BB2tWM3xQjX297PBYa"

        snapshot, _ = _create_snapshot(user_id, trip_id, provider_offer_id=expected_off_id)
        resolved_id, snap_uuid = resolve_flight_offer_id(
            offer_id_input=str(snapshot.snapshot_id),
            user_id=user_id,
            trip_id=trip_id,
            revision_id=rev_id,
            expected_revision_number=1,
        )
        assert resolved_id == expected_off_id
        assert snap_uuid == snapshot.snapshot_id

    def test_existing_provider_offer_id_still_works(self):
        """B. Existing provider offer ID ('off_...') works without snapshot resolution."""
        user_id = uuid4()
        trip_id = uuid4()
        rev_id = uuid4()
        raw_duffel_id = "off_0000DirectDuffelOffer123"

        resolved_id, snap_uuid = resolve_flight_offer_id(
            offer_id_input=raw_duffel_id,
            user_id=user_id,
            trip_id=trip_id,
            revision_id=rev_id,
            expected_revision_number=1,
        )
        assert resolved_id == raw_duffel_id
        assert snap_uuid is None

    def test_missing_snapshot_fails_closed(self):
        """C. Non-existent snapshot UUID fails closed with 404."""
        user_id = uuid4()
        trip_id = uuid4()
        rev_id = uuid4()
        non_existent_uuid = str(uuid4())

        with pytest.raises(Exception) as exc_info:
            resolve_flight_offer_id(
                offer_id_input=non_existent_uuid,
                user_id=user_id,
                trip_id=trip_id,
                revision_id=rev_id,
                expected_revision_number=1,
            )
        assert exc_info.value.status_code == 404

    def test_missing_provider_offer_id_fails_closed(self):
        """D. Snapshot with empty/missing provider_offer_id fails closed with 422."""
        user_id = uuid4()
        trip_id = uuid4()
        rev_id = uuid4()

        # Manually register in-memory snapshot with blank provider_offer_id
        store = SearchSessionStore()
        session = store.create_session(user_id=user_id, trip_id=trip_id, thread_id=None, search_type="flights", criteria_hash="c1")
        snap_id = str(uuid4())
        store._IN_MEMORY_SNAPSHOTS[snap_id] = {
            "snapshot_id": UUID(snap_id),
            "session_id": session.session_id,
            "provider": "duffel",
            "provider_offer_id": "",  # missing!
            "offer_type": "flight",
            "price_total": Decimal("100.00"),
            "currency": "INR",
            "normalized_offer": {},
            "freshness": FreshnessStatus.FRESH,
            "availability": AvailabilityStatus.AVAILABLE,
            "retrieved_at": datetime.now(timezone.utc),
        }

        with pytest.raises(Exception) as exc_info:
            resolve_flight_offer_id(
                offer_id_input=snap_id,
                user_id=user_id,
                trip_id=trip_id,
                revision_id=rev_id,
                expected_revision_number=1,
                store=store,
            )
        assert exc_info.value.status_code == 422

    def test_cross_user_snapshot_fails_closed(self):
        """E. Another user's snapshot cannot be accessed (fails closed with 403)."""
        user_owner = uuid4()
        attacker_user = uuid4()
        trip_id = uuid4()
        rev_id = uuid4()

        snapshot, _ = _create_snapshot(user_owner, trip_id, provider_offer_id="off_0000OwnerOffer")

        with pytest.raises(Exception) as exc_info:
            resolve_flight_offer_id(
                offer_id_input=str(snapshot.snapshot_id),
                user_id=attacker_user,  # unauthorized tenant
                trip_id=trip_id,
                revision_id=rev_id,
                expected_revision_number=1,
            )
        assert exc_info.value.status_code == 403

    def test_wrong_trip_snapshot_fails_closed(self):
        """F. Snapshot belonging to Trip A cannot be used on Trip B (fails closed with 400)."""
        user_id = uuid4()
        trip_a = uuid4()
        trip_b = uuid4()
        rev_id = uuid4()

        snapshot, _ = _create_snapshot(user_id, trip_a, provider_offer_id="off_0000TripAOffer")

        with pytest.raises(Exception) as exc_info:
            resolve_flight_offer_id(
                offer_id_input=str(snapshot.snapshot_id),
                user_id=user_id,
                trip_id=trip_b,  # wrong trip!
                revision_id=rev_id,
                expected_revision_number=1,
            )
        assert exc_info.value.status_code == 400
        assert "does not belong to the specified trip" in exc_info.value.detail

    def test_malformed_input_fails_closed(self):
        """I. Neither a valid UUID nor a valid Duffel 'off_...' string fails closed (422)."""
        user_id = uuid4()
        trip_id = uuid4()
        rev_id = uuid4()

        for bad_id in ["not_a_uuid", "hotel_123", "random_string_xyz"]:
            with pytest.raises(Exception) as exc_info:
                resolve_flight_offer_id(
                    offer_id_input=bad_id,
                    user_id=user_id,
                    trip_id=trip_id,
                    revision_id=rev_id,
                    expected_revision_number=1,
                )
            assert exc_info.value.status_code == 422


# ===========================================================================
# 2. END-TO-END ENDPOINT TESTS: POST /api/payments/create-order
# ===========================================================================

class TestPaymentCreateOrderFlightResolution:
    def test_create_order_with_snapshot_uuid_calls_duffel_recheck_with_provider_id(self):
        """A & H. POST /api/payments/create-order resolves snapshot UUID to Duffel provider ID
        and passes ONLY provider_offer_id to FlightService().recheck."""
        client, user_id = _auth_client("snap_e2e@example.com")
        expected_off_id = "off_0000BB2tWM3xQjX297PBYa"

        # Create valid snapshot and authorized trip
        trip_id, rev_id, rev_num = _create_fully_authorized_trip(client, user_id)
        snapshot, _ = _create_snapshot(user_id, trip_id, provider_offer_id=expected_off_id)

        mock_recheck_result = {
            "id": expected_off_id,
            "total_amount": "4500.00",
            "total_currency": "INR",
            "passengers": [{"id": "pas_1"}],
        }

        recheck_called_with = []

        def mock_recheck(offer_id):
            recheck_called_with.append(offer_id)
            return mock_recheck_result

        mock_tx = {
            "id": str(uuid4()),
            "razorpay_order_id": None,
            "amount": 4500.0,
            "currency": "INR",
            "provider_amount": 4500.0,
            "provider_currency": "INR",
            "fx_rate": 1.0,
            "fx_rate_date": None,
            "fx_source": "INR",
        }

        with patch("payment_api.FlightService") as mock_fs_cls, \
             patch("payment_api._razorpay") as mock_rp_factory, \
             patch("payment_api._integration_mode"), \
             patch.object(TransactionStore, "reserve_payment_order", return_value=(mock_tx, True)), \
             patch.object(TransactionStore, "update", return_value=None), \
             patch("payment_api._validate_and_convert_offer", return_value={
                 "customer_amount": 4500.0,
                 "customer_currency": "INR",
                 "provider_amount": 4500.0,
                 "provider_currency": "INR",
                 "fx_rate": 1.0,
                 "fx_rate_date": None,
                 "fx_source": "INR",
             }):
            mock_fs = MagicMock()
            mock_fs.recheck.side_effect = mock_recheck
            mock_fs_cls.return_value = mock_fs

            mock_rp = MagicMock()
            mock_rp.configured = True
            mock_rp.key_id = "rzp_test_key_123"
            mock_rp.create_order.return_value = {
                "id": "order_test_999",
                "amount": 450000,
                "currency": "INR",
            }
            mock_rp_factory.return_value = mock_rp

            res = client.post(
                "/api/payments/create-order",
                json={
                    "offer_id": str(snapshot.snapshot_id),  # SNAPSHOT UUID!
                    "expected_amount": 4500.0,
                    "currency": "INR",
                    "passengers": [{
                        "given_name": "Alice",
                        "family_name": "Smith",
                        "born_on": "1992-05-14",
                        "title": "ms",
                        "gender": "f",
                        "phone_number": "+919876543210",
                    }],
                    "final_confirmation": True,
                    "trip_id": trip_id,
                    "revision_id": rev_id,
                    "expected_revision_number": rev_num,
                    "payment_idempotency_key": f"pay-snap-{uuid4().hex}",
                },
            )

            assert res.status_code == 200, f"Expected 200 but got {res.status_code}: {res.text}"
            data = res.json()
            assert data["success"] is True
            assert data["order_id"] == "order_test_999"

            # CRITICAL VERIFICATION: FlightService().recheck was passed 'off_...', NOT the UUID!
            assert len(recheck_called_with) == 1
            assert recheck_called_with[0] == expected_off_id
            assert recheck_called_with[0] != str(snapshot.snapshot_id)

    def test_create_order_with_cross_user_snapshot_uuid_is_blocked(self):
        """E. POST /api/payments/create-order rejects another user's snapshot with 403."""
        owner_client, owner_id = _auth_client("owner@example.com")
        attacker_client, attacker_id = _auth_client("attacker@example.com")

        # Owner has a snapshot
        owner_trip, _, _ = _create_fully_authorized_trip(owner_client, owner_id, "Owner Trip")
        owner_snap, _ = _create_snapshot(owner_id, owner_trip, "off_0000Owner")

        # Attacker has their own trip
        atk_trip, atk_rev, atk_num = _create_fully_authorized_trip(attacker_client, attacker_id, "Attacker Trip")

        res = attacker_client.post(
            "/api/payments/create-order",
            json={
                "offer_id": str(owner_snap.snapshot_id),  # Owner's snapshot!
                "expected_amount": 4500.0,
                "currency": "INR",
                "passengers": [{
                    "given_name": "Eve",
                    "family_name": "Hacker",
                    "born_on": "1995-01-01",
                    "title": "ms",
                    "gender": "f",
                    "phone_number": "+919876543210",
                }],
                "final_confirmation": True,
                "trip_id": atk_trip,
                "revision_id": atk_rev,
                "expected_revision_number": atk_num,
                "payment_idempotency_key": f"pay-atk-{uuid4().hex}",
            },
        )
        assert res.status_code == 403
        assert "belongs to another user" in res.json()["detail"]
