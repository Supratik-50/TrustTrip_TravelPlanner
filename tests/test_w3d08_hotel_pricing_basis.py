"""Tests for W3D-08: LiteAPI Hotel pricing_basis truthfulness and planner budget integration."""
from __future__ import annotations

from unittest.mock import patch
import pytest

from services.hotel.liteapi_provider import LiteAPIProvider
from services.travel_planner import budget_from_live_data


def _make_liteapi_provider(monkeypatch) -> LiteAPIProvider:
    monkeypatch.setenv("LITEAPI_SANDBOX_API_KEY", "test-liteapi-key")
    monkeypatch.setenv("LITEAPI_MODE", "sandbox")
    return LiteAPIProvider()


def _mock_liteapi_response(amount: float = 450.0, currency: str = "USD") -> dict:
    return {
        "hotels": [{"id": "h_test_1", "name": "Tokyo Luxury Palace"}],
        "data": [
            {
                "hotelId": "h_test_1",
                "roomTypes": [
                    {
                        "name": "Deluxe King",
                        "rates": [
                            {
                                "offerId": "rate_liteapi_100",
                                "name": "Deluxe King Room",
                                "boardName": "Room Only",
                                "cancellationPolicies": {"refundableTag": "RFN"},
                                "retailRate": {
                                    "total": [{"amount": amount, "currency": currency}]
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_liteapi_search_populates_pricing_basis_full_stay(monkeypatch):
    """Verify LiteAPI offer contains pricing_basis == 'full_stay'."""
    provider = _make_liteapi_provider(monkeypatch)
    resp = _mock_liteapi_response(amount=600.0, currency="USD")
    with patch.object(provider, "_request", return_value=resp):
        offers = provider.search({
            "checkin": "2026-10-10",
            "checkout": "2026-10-13",
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "currency": "USD",
        })

    assert len(offers) == 1
    offer = offers[0]
    assert offer["pricing_basis"] == "full_stay"


def test_liteapi_search_computes_multi_night_stay(monkeypatch):
    """Verify multi-night stay computes nights = 3 (2026-10-10 to 2026-10-13)."""
    provider = _make_liteapi_provider(monkeypatch)
    resp = _mock_liteapi_response(amount=750.0, currency="USD")
    with patch.object(provider, "_request", return_value=resp):
        offers = provider.search({
            "checkin": "2026-10-10",
            "checkout": "2026-10-13",
            "adults": 1,
            "children": 0,
            "rooms": 1,
        })

    assert len(offers) == 1
    offer = offers[0]
    assert offer["nights"] == 3
    assert offer["pricing_basis"] == "full_stay"
    assert offer["total_price"] == 750.0
    assert offer["price"] == 750.0


def test_liteapi_search_computes_single_night_stay(monkeypatch):
    """Verify single-night stay computes nights = 1 (2026-10-10 to 2026-10-11)."""
    provider = _make_liteapi_provider(monkeypatch)
    resp = _mock_liteapi_response(amount=250.0, currency="USD")
    with patch.object(provider, "_request", return_value=resp):
        offers = provider.search({
            "checkin": "2026-10-10",
            "checkout": "2026-10-11",
            "adults": 1,
            "children": 0,
            "rooms": 1,
        })

    assert len(offers) == 1
    offer = offers[0]
    assert offer["nights"] == 1
    assert offer["pricing_basis"] == "full_stay"
    assert offer["total_price"] == 250.0
    assert offer["price"] == 250.0


def test_liteapi_search_preserves_authoritative_full_stay_amount(monkeypatch):
    """Verify provider full-stay amount is preserved without alteration."""
    provider = _make_liteapi_provider(monkeypatch)
    raw_amount = 1234.56
    resp = _mock_liteapi_response(amount=raw_amount, currency="EUR")
    with patch.object(provider, "_request", return_value=resp):
        offers = provider.search({
            "checkin": "2026-11-01",
            "checkout": "2026-11-05",
            "adults": 2,
            "rooms": 1,
            "currency": "EUR",
        })

    assert len(offers) == 1
    offer = offers[0]
    assert offer["price"] == raw_amount
    assert offer["total_price"] == raw_amount
    assert offer["currency"] == "EUR"
    assert offer["nights"] == 4


def test_planner_budget_includes_normalized_liteapi_offer(monkeypatch):
    """Verify budget_from_live_data accepts normalized LiteAPI offer without skipping."""
    provider = _make_liteapi_provider(monkeypatch)
    resp = _mock_liteapi_response(amount=30000.0, currency="INR")
    with patch.object(provider, "_request", return_value=resp):
        offers = provider.search({
            "checkin": "2026-10-10",
            "checkout": "2026-10-13",
            "adults": 2,
            "currency": "INR",
        })

    assert len(offers) == 1
    lite_offer = offers[0]
    # Simulate HotelService._inr() pass-through
    lite_offer["price_inr"] = 30000.0

    constraints = {"duration_days": 4, "passenger_count": 2}
    budget = budget_from_live_data(constraints, [], "some hotel text", [lite_offer])

    assert budget["hotel_pricing_basis"] == "full_stay"
    assert "live dual-provider hotel offer (full_stay)" in budget["hotel_price_source"]
    assert budget["hotel_estimate_inr"] == 30000.0


def test_planner_budget_still_fails_closed_on_unrecognized_basis():
    """Verify budget_from_live_data still discards offers with unrecognized/missing basis."""
    unrecognized_offer = {
        "id": "bad_offer_1",
        "price_total": 5000.0,
        "price_inr": 5000.0,
        "currency": "INR",
        "pricing_basis": "mystery_basis",
        "nights": 0,
    }
    constraints = {"duration_days": 5, "passenger_count": 1}
    budget = budget_from_live_data(constraints, [], "", [unrecognized_offer])

    # Unrecognized offer must NOT be accepted as full_stay or per_night
    assert budget["hotel_pricing_basis"] in ("estimated_per_night", "unknown")
    assert budget["hotel_estimate_inr"] != 5000.0
