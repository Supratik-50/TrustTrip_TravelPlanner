"""Tests for Flight Slice Decomposition (P1).

Verifies that _build_selected_flight_metadata properly decomposes round-trip flights
into distinct outbound and return slices instead of collapsing them into CCU -> CCU.
"""
from __future__ import annotations

from services.trip_store import _build_selected_flight_metadata


def test_round_trip_two_segments_produces_two_slices():
    """Round-trip: CCU -> COK and COK -> CCU must produce 2 slices."""
    metadata = {
        "id": "fl_rt_1",
        "carrier_name": "Duffel Airways",
        "carrier_code": "DA",
        "total_amount": "329",
        "currency": "USD",
        "price_inr": 27471,
        "segments": [
            {
                "id": "seg_1",
                "origin": "CCU",
                "destination": "COK",
                "departure_time": "2026-11-01T06:00:00Z",
                "arrival_time": "2026-11-01T09:00:00Z",
                "carrier_name": "Duffel Airways",
                "carrier_code": "DA",
                "flight_number": "DA101",
                "duration_minutes": 180,
                "cabin_class": "economy",
            },
            {
                "id": "seg_2",
                "origin": "COK",
                "destination": "CCU",
                "departure_time": "2026-11-07T14:00:00Z",
                "arrival_time": "2026-11-07T17:00:00Z",
                "carrier_name": "Duffel Airways",
                "carrier_code": "DA",
                "flight_number": "DA102",
                "duration_minutes": 180,
                "cabin_class": "economy",
            },
        ],
    }

    result = _build_selected_flight_metadata(
        norm_offer=metadata,
        snapshot_id="snap_fl_1",
        trip={"destination": "COK"},
    )

    slices = result["slices"]
    assert len(slices) == 2, f"Expected 2 slices for round-trip, got {len(slices)}"

    # Slice 0: Outbound CCU -> COK
    assert slices[0]["origin"] == "CCU"
    assert slices[0]["destination"] == "COK"
    assert len(slices[0]["segments"]) == 1
    assert slices[0]["segments"][0]["id"] == "seg_1"

    # Slice 1: Return COK -> CCU
    assert slices[1]["origin"] == "COK"
    assert slices[1]["destination"] == "CCU"
    assert len(slices[1]["segments"]) == 1
    assert slices[1]["segments"][0]["id"] == "seg_2"


def test_one_way_flight_produces_single_slice():
    """One-way: CCU -> DEL must produce 1 slice."""
    metadata = {
        "id": "fl_ow_1",
        "carrier_name": "IndiGo",
        "total_amount": "150",
        "currency": "USD",
        "segments": [
            {
                "id": "seg_ow_1",
                "origin": "CCU",
                "destination": "DEL",
                "departure_time": "2026-11-01T06:00:00Z",
                "arrival_time": "2026-11-01T08:30:00Z",
                "carrier_name": "IndiGo",
                "carrier_code": "6E",
                "flight_number": "6E201",
                "duration_minutes": 150,
                "cabin_class": "economy",
            },
        ],
    }

    result = _build_selected_flight_metadata(
        norm_offer=metadata,
        snapshot_id="snap_fl_ow",
        trip={"destination": "DEL"},
    )

    slices = result["slices"]
    assert len(slices) == 1
    assert slices[0]["origin"] == "CCU"
    assert slices[0]["destination"] == "DEL"


def test_multi_leg_round_trip_produces_two_slices():
    """Multi-leg round trip: [CCU -> BOM, BOM -> COK] and [COK -> BLR, BLR -> CCU]."""
    metadata = {
        "id": "fl_ml_1",
        "carrier_name": "Air India",
        "total_amount": "400",
        "currency": "USD",
        "segments": [
            {
                "id": "seg_1",
                "origin": "CCU",
                "destination": "BOM",
                "departure_time": "2026-11-01T06:00:00Z",
                "arrival_time": "2026-11-01T08:30:00Z",
                "carrier_name": "Air India",
                "duration_minutes": 150,
            },
            {
                "id": "seg_2",
                "origin": "BOM",
                "destination": "COK",
                "departure_time": "2026-11-01T10:00:00Z",
                "arrival_time": "2026-11-01T12:00:00Z",
                "carrier_name": "Air India",
                "duration_minutes": 120,
            },
            {
                "id": "seg_3",
                "origin": "COK",
                "destination": "BLR",
                "departure_time": "2026-11-07T14:00:00Z",
                "arrival_time": "2026-11-07T15:00:00Z",
                "carrier_name": "Air India",
                "duration_minutes": 60,
            },
            {
                "id": "seg_4",
                "origin": "BLR",
                "destination": "CCU",
                "departure_time": "2026-11-07T16:30:00Z",
                "arrival_time": "2026-11-07T19:00:00Z",
                "carrier_name": "Air India",
                "duration_minutes": 150,
            },
        ],
    }

    result = _build_selected_flight_metadata(
        norm_offer=metadata,
        snapshot_id="snap_fl_ml",
        trip={"destination": "COK"},
    )

    slices = result["slices"]
    assert len(slices) == 2, f"Expected 2 slices, got {len(slices)}"
    assert slices[0]["origin"] == "CCU"
    assert slices[0]["destination"] == "COK"
    assert len(slices[0]["segments"]) == 2

    assert slices[1]["origin"] == "COK"
    assert slices[1]["destination"] == "CCU"
    assert len(slices[1]["segments"]) == 2
