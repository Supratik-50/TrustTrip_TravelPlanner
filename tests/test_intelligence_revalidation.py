"""Tests for Offer Revalidation Layering and Immutable Successor Snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from domain.live_offer import (
    AvailabilityStatus,
    FlightOffer,
    FreshnessStatus,
    PriceBreakdown,
    RevalidationResult,
    RevalidationStatus,
)
from services.planner_service import PlannerService
from services.search_session_store import SearchSessionStore


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    SearchSessionStore._IN_MEMORY_SESSIONS.clear()
    SearchSessionStore._IN_MEMORY_SNAPSHOTS.clear()
    SearchSessionStore._IN_MEMORY_REVALIDATIONS.clear()


def test_revalidate_creates_successor_snapshot_on_price_change():
    """Verify that when supplier price changes, original snapshot is untouched and successor snapshot is created."""
    import asyncio

    async def _run():
        user_id = uuid4()
        trip_id = uuid4()
        store = SearchSessionStore()

        session = store.create_session(
            user_id=user_id,
            trip_id=trip_id,
            thread_id="th_1",
            search_type="flights",
            criteria_hash="hash_1",
        )

        now = datetime.now(timezone.utc)
        original_offer = FlightOffer(
            snapshot_id=uuid4(),
            provider="duffel",
            provider_offer_id="off_original_1",
            price=PriceBreakdown(base_amount=Decimal("400.00"), total_amount=Decimal("400.00"), currency="USD"),
            stops=0,
            segments=[],
            passenger_count=1,
            retrieved_at=now,
            expires_at=now,
        )

        orig_snapshot = store.save_snapshot(session.session_id, original_offer, raw_payload={"fare": 400})

        planner = PlannerService()

        # Mock the adapter's revalidate_flight to return a price change
        mock_result = RevalidationResult(
            revalidation_id=uuid4(),
            original_snapshot_id=orig_snapshot.snapshot_id,
            status=RevalidationStatus.PRICE_CHANGED,
            verified_at=now,
            price_changed=True,
            original_price=Decimal("400.00"),
            current_price=Decimal("450.00"),
            currency="USD",
            details="Supplier fare increased to 450 USD",
        )

        with patch("services.planner_service.DuffelFlightAdapter.revalidate_flight", new_callable=AsyncMock) as mock_rev:
            mock_rev.return_value = mock_result
            reval_result = await planner.revalidate_offer(
                snapshot_id=orig_snapshot.snapshot_id,
                authenticated_user_id=user_id,
            )

        # RevalidationResult assertions
        assert reval_result.status == RevalidationStatus.PRICE_CHANGED
        assert reval_result.price_changed is True
        assert reval_result.subsequent_snapshot_id is not None
        assert reval_result.subsequent_snapshot_id != orig_snapshot.snapshot_id

        # Verify original snapshot remained IMMUTABLE (still 400.00)
        unaltered = store.get_snapshot(orig_snapshot.snapshot_id, user_id)
        assert unaltered is not None
        assert unaltered.price_total == Decimal("400.00")

        # Verify successor snapshot exists with new price and predecessor link
        successor = store.get_snapshot(reval_result.subsequent_snapshot_id, user_id)
        assert successor is not None
        assert successor.price_total == Decimal("450.00")
        assert successor.predecessor_snapshot_id == orig_snapshot.snapshot_id

    asyncio.run(_run())
