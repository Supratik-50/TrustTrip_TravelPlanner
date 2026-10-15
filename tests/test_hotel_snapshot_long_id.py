"""Regression test for Blocker 1: Proving long provider_offer_id (> 800 chars)

Verifies:
1. Long provider_offer_id (>800 chars, e.g. 1024 chars typical of LiteAPI/Hotelbeds compound rate keys)
   persists into PostgreSQL offer_snapshots table without psycopg.errors.StringDataRightTruncation.
2. The retrieved snapshot contains the exact, untruncated provider_offer_id.
3. Existing short provider_offer_id values continue to persist and retrieve as expected.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import os
import pytest

from domain.live_offer import HotelOffer, PriceBreakdown
from services.search_session_store import SearchSessionStore
from services.auth_store import UserStore
from services.trip_store import TripStore


@pytest.fixture
def test_user_id():
    """Ensure a test user exists in Postgres/memory store."""
    email = f"test_long_id_{uuid4().hex[:8]}@example.com"
    user_store = UserStore()
    user = user_store.create_user(email=email, password_hash="hash123")
    return user["id"]


def test_hotel_snapshot_long_provider_offer_id_persistence(test_user_id):
    """Verify provider_offer_id exceeding 255 chars (e.g., 1024 chars) persists without truncation."""
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=test_user_id, destination="Kochi", origin="CCU")
    trip_id = trip["id"]
    thread = trip_store.create_thread(user_id=test_user_id, trip_id=trip_id)
    thread_id = thread["id"]

    store = SearchSessionStore()
    session = store.create_session(
        user_id=test_user_id,
        trip_id=trip_id,
        thread_id=thread_id,
        search_type="hotels",
        criteria_hash="crit_long_1",
    )

    # 1024-character compound rate key simulating real LiteAPI/Hotelbeds payloads
    long_rate_key = "liteapi_rate_" + ("abc123xyz789_compound_token_segment_" * 28) + "_final_checksum"
    assert len(long_rate_key) > 800, f"Expected length > 800, got {len(long_rate_key)}"

    now = datetime.now(timezone.utc)
    offer = HotelOffer(
        snapshot_id=uuid4(),
        provider="liteapi",
        provider_offer_id=long_rate_key,
        hotel_id="lp_long_hotel_01",
        hotel_name="Resort with Compound LiteAPI Key",
        city="Kochi",
        room_name="Deluxe Suite",
        board_type="Room Only",
        refundable=True,
        price=PriceBreakdown(
            base_amount=Decimal("12000.00"),
            total_amount=Decimal("14160.00"),
            currency="INR",
            inr_reference_amount=Decimal("14160.00"),
        ),
        nightly_price=Decimal("7080.00"),
        nights=2,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        infants_count=0,
        retrieved_at=now,
    )

    # Must succeed without StringDataRightTruncation
    saved_snapshot = store.save_snapshot(
        session_id=session.session_id,
        offer=offer,
        raw_payload={"long_key": long_rate_key, "supplier": "liteapi"},
    )
    assert saved_snapshot is not None
    assert saved_snapshot.provider_offer_id == long_rate_key
    assert len(saved_snapshot.provider_offer_id) == len(long_rate_key)

    # Retrieve and verify round-trip persistence
    retrieved = store.get_snapshot(saved_snapshot.snapshot_id, test_user_id)
    assert retrieved is not None
    assert retrieved.provider_offer_id == long_rate_key
    assert len(retrieved.provider_offer_id) == len(long_rate_key)


def test_hotel_snapshot_standard_short_id_still_works(test_user_id):
    """Verify normal short provider_offer_id values continue to persist cleanly."""
    trip_store = TripStore()
    trip = trip_store.create_trip(user_id=test_user_id, destination="Kochi", origin="CCU")
    trip_id = trip["id"]
    thread = trip_store.create_thread(user_id=test_user_id, trip_id=trip_id)
    thread_id = thread["id"]

    store = SearchSessionStore()
    session = store.create_session(
        user_id=test_user_id,
        trip_id=trip_id,
        thread_id=thread_id,
        search_type="hotels",
        criteria_hash="crit_short_1",
    )

    short_id = "liteapi_short_rate_123"
    now = datetime.now(timezone.utc)
    offer = HotelOffer(
        snapshot_id=uuid4(),
        provider="liteapi",
        provider_offer_id=short_id,
        hotel_id="lp_short_hotel_02",
        hotel_name="Standard Boutique Hotel",
        city="Kochi",
        room_name="Standard Room",
        board_type="Breakfast Included",
        refundable=True,
        price=PriceBreakdown(
            base_amount=Decimal("5000.00"),
            total_amount=Decimal("5900.00"),
            currency="INR",
            inr_reference_amount=Decimal("5900.00"),
        ),
        nightly_price=Decimal("2950.00"),
        nights=2,
        rooms_count=1,
        adults_count=2,
        children_count=0,
        infants_count=0,
        retrieved_at=now,
    )

    saved_snapshot = store.save_snapshot(
        session_id=session.session_id,
        offer=offer,
        raw_payload={"rate_id": short_id},
    )
    assert saved_snapshot is not None
    assert saved_snapshot.provider_offer_id == short_id

    retrieved = store.get_snapshot(saved_snapshot.snapshot_id, test_user_id)
    assert retrieved is not None
    assert retrieved.provider_offer_id == short_id
