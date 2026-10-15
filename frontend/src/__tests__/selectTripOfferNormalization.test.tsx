/**
 * Frontend Regression Tests: Blocker 2 — selectTripOffer Response Normalization
 *
 * Verifies:
 * 1. Selecting one flight unpacks the backend { success, trip_id, offer_type, snapshot_id, trip }
 *    envelope and preserves destination, origin, dates, budget, currency, and revision intact.
 * 2. Selecting one hotel unpacks the backend envelope and keeps destination, origin, dates,
 *    budget, currency, and revision intact.
 * 3. The returned object from api.selectTripOffer() is an authoritative normalized TripDetail.
 * 4. Stale/empty envelope or raw inputs do not fabricate non-existent destination or budget state.
 * 5. Page refresh is NOT required to restore fields; the immediate return value contains
 *    all top-level fields needed by the React state setter setTrip(updated).
 */
import assert from 'node:assert';
import { apiClient, normalizeBackendTripDetail } from '../lib/apiClient';

console.log('Running selectTripOffer Normalization Tests...\n');

// Mock authoritative base trip representing live PostgreSQL state
const mockLiveBaseTrip = {
  id: 'be5f3802-6065-43ab-97c9-6d6e41c25c72',
  user_id: 'abc@gmail.com',
  title: 'Kerala, India Journey',
  destination: 'Kerala, India',
  origin: 'CCU',
  status: 'planning',
  current_revision_number: 1,
  current_revision_id: 'rev_cok_01',
  start_date: '2026-10-14',
  end_date: '2026-10-21',
  budget: 70000,
  currency: 'INR',
  travelers_count: 2,
  preferences: ['Backwaters', 'Ayurveda'],
  metadata: {
    origin: 'CCU',
    destination: 'Kerala, India',
    start_date: '2026-10-14',
    end_date: '2026-10-21',
    budget: 70000,
    currency: 'INR',
    constraints: {
      origin: 'CCU',
      destination: 'Kerala, India',
      departure_date: '2026-10-14',
      return_date: '2026-10-21',
      budget: 70000,
      budget_amount: 70000,
      currency: 'INR',
    },
    plan: {
      destination_airport: 'COK',
      route: 'CCU -> COK',
      trip_summary: '7-day Kerala exploration',
    },
  },
};

// ---------------------------------------------------------------------------
// Test 1: Selecting one flight keeps destination/date/budget intact & returns TripDetail
// ---------------------------------------------------------------------------
{
  const flightSnapshotId = 'snap_flight_ccu_cok_999';
  const backendEnvelope = {
    success: true,
    trip_id: mockLiveBaseTrip.id,
    offer_type: 'flight',
    snapshot_id: flightSnapshotId,
    trip: {
      ...mockLiveBaseTrip,
      metadata: {
        ...mockLiveBaseTrip.metadata,
        selected_flight_snapshot_id: flightSnapshotId,
      },
    },
  };

  const originalFetch = global.fetch;
  global.fetch = (async (url: string, init: any) => {
    assert(url.includes(`/api/trips/${mockLiveBaseTrip.id}/select-offer`), `Unexpected URL: ${url}`);
    assert.strictEqual(init.method, 'POST');
    return {
      ok: true,
      status: 200,
      json: async () => backendEnvelope,
    } as any;
  }) as any;

  try {
    const updated = await apiClient.selectTripOffer(mockLiveBaseTrip.id, {
      offer_type: 'flight',
      offer_id: flightSnapshotId,
      snapshot_id: flightSnapshotId,
    });

    // 1. Must return normalized TripDetail directly (not the raw { success, trip } envelope)
    assert.strictEqual((updated as any).success, undefined, 'Envelope field success must not leak as top-level trip field');
    assert.strictEqual(updated.trip_id, mockLiveBaseTrip.id, 'Trip ID must match');

    // 2. Destination, dates, and budget must remain intact without refresh
    assert.strictEqual(updated.destination, 'Kerala, India', 'Destination must remain Kerala, India');
    assert.strictEqual(updated.origin, 'CCU', 'Origin must remain CCU');
    assert.strictEqual(updated.start_date, '2026-10-14', 'Start date must remain 2026-10-14');
    assert.strictEqual(updated.end_date, '2026-10-21', 'End date must remain 2026-10-21');
    assert.strictEqual(updated.budget, 70000, 'Budget must remain ₹70,000');
    assert.strictEqual(updated.currency, 'INR', 'Currency must remain INR');

    // 3. Revision info preserved
    assert.strictEqual(updated.active_revision_number, 1, 'Active revision number must be 1');
    assert.strictEqual(updated.active_revision_id, 'rev_cok_01', 'Active revision ID must be preserved');

    // 4. Selected flight snapshot info preserved
    assert(updated.metadata?.selected_flight_snapshot_id === flightSnapshotId, 'selected_flight_snapshot_id must be preserved in metadata');
    assert(updated.selected_flight_offer !== undefined, 'selected_flight_offer must be populated for UI consumption');
    assert.strictEqual(updated.selected_flight_offer?.id, flightSnapshotId, 'Selected flight offer ID must match snapshot ID');

    console.log('✔ Test 1 Passed: Selecting one flight preserves destination/dates/budget and returns authoritative TripDetail');
  } finally {
    global.fetch = originalFetch;
  }
}

// ---------------------------------------------------------------------------
// Test 2: Selecting one hotel keeps destination/date/budget intact & returns TripDetail
// ---------------------------------------------------------------------------
{
  const hotelSnapshotId = 'snap_hotel_kochi_grand_888';
  const backendEnvelope = {
    success: true,
    trip_id: mockLiveBaseTrip.id,
    offer_type: 'hotel',
    snapshot_id: hotelSnapshotId,
    trip: {
      ...mockLiveBaseTrip,
      metadata: {
        ...mockLiveBaseTrip.metadata,
        selected_hotel_snapshot_id: hotelSnapshotId,
        selected_hotel: {
          id: hotelSnapshotId,
          snapshot_id: hotelSnapshotId,
          hotel_name: 'Grand Hyatt Kochi Bolgatty',
          price: 18000,
          currency: 'INR',
        },
      },
    },
  };

  const originalFetch = global.fetch;
  global.fetch = (async (url: string, init: any) => {
    return {
      ok: true,
      status: 200,
      json: async () => backendEnvelope,
    } as any;
  }) as any;

  try {
    const updated = await apiClient.selectTripOffer(mockLiveBaseTrip.id, {
      offer_type: 'hotel',
      offer_id: hotelSnapshotId,
      snapshot_id: hotelSnapshotId,
    });

    assert.strictEqual(updated.destination, 'Kerala, India');
    assert.strictEqual(updated.start_date, '2026-10-14');
    assert.strictEqual(updated.end_date, '2026-10-21');
    assert.strictEqual(updated.budget, 70000);
    assert.strictEqual(updated.currency, 'INR');

    // Selected hotel offer preserved
    assert.strictEqual(updated.selected_hotel_offer?.id, hotelSnapshotId);
    assert.strictEqual(updated.selected_hotel_offer?.hotel_name, 'Grand Hyatt Kochi Bolgatty');

    console.log('✔ Test 2 Passed: Selecting one hotel preserves destination/dates/budget and hotel snapshot');
  } finally {
    global.fetch = originalFetch;
  }
}

// ---------------------------------------------------------------------------
// Test 3: Stale/missing envelope does NOT fabricate state
// ---------------------------------------------------------------------------
{
  // If backend returns minimal/empty object without destination or budget,
  // the normalizer must NOT fabricate random defaults like "Paris" or ₹5000.
  const emptyRawTrip = {
    id: 'empty_trip_123',
    destination: '',
    budget: undefined,
  };

  const normalized = normalizeBackendTripDetail(emptyRawTrip);
  assert.strictEqual(normalized.destination, '', 'Must not fabricate destination when missing');
  assert.strictEqual(normalized.budget, undefined, 'Must not fabricate budget when missing');

  console.log('✔ Test 3 Passed: Missing fields do not fabricate state');
}

// ---------------------------------------------------------------------------
// Test 4: Single flight selection isolation contract
// ---------------------------------------------------------------------------
{
  // Verifies that a selected flight with snapshot ID matches only its own ID,
  // proving that sibling flight cards with different IDs remain unselected.
  const selectedSnapshot = 'snap_selected_flight_aaa';
  const otherSnapshot = 'snap_other_flight_bbb';

  const tripWithFlight = normalizeBackendTripDetail({
    ...mockLiveBaseTrip,
    metadata: {
      ...mockLiveBaseTrip.metadata,
      selected_flight_snapshot_id: selectedSnapshot,
    },
  });

  const cardA_Id = selectedSnapshot;
  const cardB_Id = otherSnapshot;

  const isCardASelected = tripWithFlight.selected_flight_offer?.id === cardA_Id;
  const isCardBSelected = tripWithFlight.selected_flight_offer?.id === cardB_Id;

  assert.strictEqual(isCardASelected, true, 'Card A must be selected');
  assert.strictEqual(isCardBSelected, false, 'Card B must NOT be selected (isolation maintained)');

  console.log('✔ Test 4 Passed: Selection isolation strictly separates chosen offer from sibling offers');
}

console.log('\nAll selectTripOffer Normalization Tests Passed Successfully!');
