"""Tests for Revision Advancement Stale Offer Invalidation (P1).

Verifies:
1. Advancing a revision with changed travel dates triggers replanning that removes:
   - selected_flight_snapshot_id
   - selected_flight
   - selected_hotel_snapshot_id
   - selected_hotel
   - hotel_prebook
   - selected_room
2. Advancing a revision with itinerary notes only preserves prior selections.
"""
from __future__ import annotations

from uuid import uuid4
import pytest
from services.trip_store import TripStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    TripStore._IN_MEMORY_TRIPS.clear()
    TripStore._IN_MEMORY_THREADS.clear()


def test_revision_advancement_clears_stale_flight_and_hotel():
    trip_store = TripStore()
    user_id = str(uuid4())

    # 1. Create trip with initial revision
    trip = trip_store.create_trip(
        user_id=user_id,
        title="Kerala Exploration",
        origin="CCU",
        destination="COK",
        metadata={
            "budget": 70000.0,
            "currency": "INR",
            "selected_flight_snapshot_id": "snap_fl_1",
            "selected_flight": {"carrier_name": "Duffel Airways", "total_amount": "329", "currency": "USD"},
            "selected_hotel_snapshot_id": "snap_ht_1",
            "selected_hotel": {"hotel_name": "Cochin Legacy", "total_amount": 73, "currency": "USD"},
            "hotel_prebook": {"prebook_id": "pb_123"},
            "selected_room": {"id": "rm_1", "name": "Deluxe Room"},
            "constraints": {
                "departure_date": "2026-11-01",
                "return_date": "2026-11-07",
                "origin": "CCU",
                "destination": "COK",
            },
        },
    )
    trip_id = str(trip["id"])

    t_before = trip_store.get_trip(trip_id, user_id)
    assert t_before["metadata"]["selected_flight_snapshot_id"] == "snap_fl_1"
    assert t_before["metadata"]["selected_flight"]["carrier_name"] == "Duffel Airways"
    assert t_before["metadata"]["selected_hotel_snapshot_id"] == "snap_ht_1"
    assert t_before["metadata"]["selected_hotel"]["hotel_name"] == "Cochin Legacy"
    assert t_before["metadata"]["hotel_prebook"]["prebook_id"] == "pb_123"
    assert t_before["metadata"]["selected_room"]["id"] == "rm_1"

    # 2. Advance revision with shifted travel dates
    trip_store.create_revision_atomic(
        trip_id=trip_id,
        user_id=user_id,
        feedback="Move trip by one week later",
        updated_constraints={
            "departure_date": "2026-11-08",
            "return_date": "2026-11-15",
            "origin": "CCU",
            "destination": "COK",
        },
        expected_base_revision_id=t_before["current_revision_id"],
        expected_base_revision=0,
    )

    t_after = trip_store.get_trip(trip_id, user_id)
    assert t_after["current_revision_number"] == 1
    # Verify flight selections invalidated
    assert "selected_flight_snapshot_id" not in t_after["metadata"]
    assert "selected_flight" not in t_after["metadata"]

    # Verify hotel selections invalidated
    assert "selected_hotel_snapshot_id" not in t_after["metadata"]
    assert "selected_hotel" not in t_after["metadata"]
    assert "hotel_prebook" not in t_after["metadata"]
    assert "selected_room" not in t_after["metadata"]


def test_revision_advancement_retains_selections_when_not_invalidated():
    trip_store = TripStore()
    user_id = str(uuid4())

    trip = trip_store.create_trip(
        user_id=user_id,
        title="Kerala Exploration",
        origin="CCU",
        destination="COK",
        metadata={
            "budget": 70000.0,
            "currency": "INR",
            "selected_flight_snapshot_id": "snap_fl_1",
            "selected_flight": {"carrier_name": "Duffel Airways", "total_amount": "329", "currency": "USD"},
            "constraints": {
                "departure_date": "2026-11-01",
                "return_date": "2026-11-07",
                "origin": "CCU",
                "destination": "COK",
            },
        },
    )
    trip_id = str(trip["id"])

    t_before = trip_store.get_trip(trip_id, user_id)
    assert t_before["metadata"]["selected_flight_snapshot_id"] == "snap_fl_1"

    # Advance revision with notes only, preserving same constraints
    trip_store.create_revision_atomic(
        trip_id=trip_id,
        user_id=user_id,
        feedback="Add tea tasting note on afternoon of day 3",
        updated_constraints={
            "departure_date": "2026-11-01",
            "return_date": "2026-11-07",
            "origin": "CCU",
            "destination": "COK",
        },
        expected_base_revision_id=t_before["current_revision_id"],
        expected_base_revision=0,
    )

    t_after = trip_store.get_trip(trip_id, user_id)
    assert t_after["current_revision_number"] == 1
    # Prior flight selection is preserved
    assert t_after["metadata"]["selected_flight_snapshot_id"] == "snap_fl_1"
    assert t_after["metadata"]["selected_flight"]["carrier_name"] == "Duffel Airways"
