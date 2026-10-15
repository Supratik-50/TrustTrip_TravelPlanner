"""PostgreSQL-backed Section 10 payment-order idempotency race tests."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_store import UserStore
from services.evidence_store import EvidenceRevisionStore
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def reset_memory_state(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    UserStore._IN_MEMORY_USERS.clear()
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()
    EvidenceRevisionStore._IN_MEMORY_REVISIONS.clear()
    EvidenceRevisionStore._IN_MEMORY_APPROVALS.clear()
    EvidenceRevisionStore._IN_MEMORY_REJECTIONS.clear()


def _dsn():
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("PostgreSQL concurrency validation requires DATABASE_URL")
    return value if "sslmode=" in value else value + ("&" if "?" in value else "?") + "sslmode=require"


def _authorized_trip():
    from services.auth_service import create_access_token
    email = f"s10-race-{uuid4().hex}@example.test"
    user = UserStore().create_user(email=email, password_hash="test")
    uid = user["id"]
    token = create_access_token(user_id=uid, email=email)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    constraints = {"origin": "DEL", "destination": "BOM", "departure_date": "2027-01-10", "return_date": "2027-01-15", "passengers": 1}
    trip = client.post("/api/trips", json={"title": "Payment idempotency", "origin": "DEL", "destination": "BOM", "metadata": {"constraints": constraints}}).json()["trip"]["id"]
    rev = client.post(f"/api/trips/{trip}/revisions", json={"feedback": "initial", "updated_constraints": constraints}).json()["revision_id"]
    client.post(f"/api/trips/{trip}/approve", json={"revision_id": rev, "expected_revision_number": 1})
    client.post(f"/api/trips/{trip}/booking-approval", json={"revision_id": rev, "expected_revision_number": 1})
    rev2 = client.post(f"/api/trips/{trip}/revisions", json={"feedback": "changed", "updated_constraints": {**constraints, "passengers": 2}}).json()["revision_id"]

    with psycopg.connect(_dsn()) as conn:
        conn.execute("INSERT INTO users (id,email,password_hash) VALUES (%s,%s,'test')", (uid, email))
        conn.execute("INSERT INTO trips (id,user_id,title,destination,origin,status,metadata,current_revision_number) VALUES (%s,%s,'Payment idempotency','BOM','DEL','approved','{}'::jsonb,0)", (trip, uid))
        conn.execute("INSERT INTO plan_revisions (revision_id,trip_id,user_id,revision_number,change_set,plan_snapshot,is_published) VALUES (%s,%s,%s,1,'{}'::jsonb,'{}'::jsonb,true),(%s,%s,%s,2,'{}'::jsonb,'{}'::jsonb,true)", (rev, trip, uid, rev2, trip, uid))
        conn.execute("UPDATE trips SET current_revision_id=%s,current_revision_number=1 WHERE id=%s", (rev, trip))
    return client, uid, trip, rev, rev2, token


def _cleanup(table, key):
    with psycopg.connect(_dsn()) as conn:
        conn.execute(f"DELETE FROM {table} WHERE payment_idempotency_key=%s", (key,))


def _cleanup_trip(trip, uid):
    with psycopg.connect(_dsn()) as conn:
        conn.execute("DELETE FROM plan_revisions WHERE trip_id=%s", (trip,))
        conn.execute("DELETE FROM trips WHERE id=%s", (trip,))
        conn.execute("DELETE FROM users WHERE id=%s", (uid,))


def _post(token, path, body):
    with TestClient(app) as client:
        return client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


def test_flight_payment_order_same_key_is_single_under_concurrency_and_rejects_conflict():
    from services.transaction_store import TransactionStore
    client, uid, trip, rev, rev2, token = _authorized_trip()
    key = f"flight-race-{uuid4().hex}"
    payload = {
        "offer_id": "off_race_flight", "expected_amount": 100, "currency": "USD",
        "passengers": [{"given_name": "A", "family_name": "B", "born_on": "1990-01-01", "title": "ms", "gender": "f"}],
        "final_confirmation": True, "trip_id": trip, "revision_id": rev,
        "expected_revision_number": 1, "payment_idempotency_key": key,
    }
    calls = 0
    calls_lock = threading.Lock()
    def create_order(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.25)
        return {"id": f"order-flight-{uuid4().hex[:12]}", "amount": 10000, "currency": "INR"}

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("payment_api._integration_mode", lambda: "test")
            mp.setattr("payment_api._validate_and_convert_offer", lambda *a: {
                "customer_amount": 100.0, "provider_amount": 1.0, "provider_currency": "USD",
                "customer_currency": "INR",
                "fx_rate": 100.0, "fx_rate_date": "2027-01-01", "fx_source": "test",
            })
            mp.setattr("payment_api.FlightService.recheck", lambda self, oid: {"passengers": []})
            class FakeRazorpay:
                configured = True
                key_id = "rzp_test_public"
            FakeRazorpay.create_order = staticmethod(create_order)
            mp.setattr("payment_api._razorpay", FakeRazorpay)
            mp.setattr("services.authorization_store.AuthorizationStore.assert_booking_eligible", lambda *a, **k: None)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: _post(token, "/api/payments/create-order", payload), range(2)))
            assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
            data = [r.json() for r in responses]
            assert len({x["transaction_id"] for x in data}) == 1
            assert calls == 1
            stored = TransactionStore().get_by_payment_idempotency_key(key)
            assert stored and str(stored["id"]) == data[0]["transaction_id"]
            conflict = {**payload, "revision_id": rev2, "expected_revision_number": 2}
            assert _post(token, "/api/payments/create-order", conflict).status_code == 409
            assert calls == 1
    finally:
        _cleanup("trust_trip_transactions", key)
        _cleanup_trip(trip, uid)


def test_hotel_payment_order_same_key_is_single_under_concurrency_and_rejects_conflict():
    client, uid, trip, rev, rev2, token = _authorized_trip()
    key = f"hotel-race-{uuid4().hex}"
    payload = {
        "offer": {"provider": "test", "hotel_name": "Hotel A", "price": 100, "currency": "INR", "price_inr": 100},
        "prebook": {"prebook_id": "supplier-hold-1"}, "holder": {"name": "A"}, "guests": [{"name": "A"}],
        "trip_id": trip, "revision_id": rev, "expected_revision_number": 1,
        "payment_idempotency_key": key, "final_confirmation": True,
    }
    calls = 0
    calls_lock = threading.Lock()
    def create_order(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.25)
        return {"id": f"order-hotel-{uuid4().hex[:12]}"}

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("services.authorization_store.AuthorizationStore.assert_booking_eligible", lambda *a, **k: None)
            class FakeRazorpay:
                configured = True
                key_id = "rzp_test_public"
            FakeRazorpay.create_order = staticmethod(create_order)
            mp.setattr("hotel_payment_api.RazorpayService", FakeRazorpay)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: _post(token, "/api/hotel-payments/create-order", payload), range(2)))
            assert [r.status_code for r in responses] == [200, 200]
            data = [r.json() for r in responses]
            assert len({x["transaction_id"] for x in data}) == 1
            assert calls == 1
            from services.hotel_transaction_store import HotelTransactionStore
            stored = HotelTransactionStore().get_by_payment_idempotency_key(key)
            assert stored and str(stored["id"]) == data[0]["transaction_id"]
            conflict = {**payload, "revision_id": rev2, "expected_revision_number": 2}
            assert _post(token, "/api/hotel-payments/create-order", conflict).status_code == 409
            assert calls == 1
    finally:
        _cleanup("trust_trip_hotel_transactions", key)
        _cleanup_trip(trip, uid)
