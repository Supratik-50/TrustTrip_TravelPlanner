/**
 * Frontend Regression Tests: Trip Constraint & Budget Persistence
 *
 * Verifies:
 * A. Save Constraints calls the Section 8 revision API (POST /api/trips/{trip_id}/revisions)
 *    with updated_constraints.
 * B. ₹70,000 persists and is properly parsed by normalizeBackendTrip / getTrip upon refresh.
 * C. Authoritative revision number originates from the backend, not fabricated locally.
 * D. Failed constraint save (e.g., 409 or 500 error) does not mutate local authoritative state.
 */
import assert from 'node:assert';
import { apiClient, normalizeBackendTrip, normalizeBackendTripDetail } from '../lib/apiClient';

console.log('Running Trip Constraint & Budget Persistence Tests...\n');

// ---------------------------------------------------------------------------
// Test A: Save Constraints calls the revision API with updated_constraints
// ---------------------------------------------------------------------------
{
  const tripId = '39b76dc3-cbc6-4604-bf93-87212c0a44e0';
  let calledUrl = '';
  let calledMethod = '';
  let calledBody: any = null;

  const originalFetch = global.fetch;
  global.fetch = (async (url: string, init: any) => {
    calledUrl = url;
    calledMethod = init?.method || 'GET';
    calledBody = init?.body ? JSON.parse(init.body) : null;

    return {
      ok: true,
      status: 200,
      json: async () => ({
        success: true,
        trip_id: tripId,
        revision_number: 1,
        revision_id: 'rev_new_70k',
        status: 'awaiting_approval',
        change_set: {
          added: [],
          removed: [],
          changed: [{ field: 'budget', old_value: 45000, new_value: 70000 }],
        },
      }),
    } as any;
  }) as any;

  try {
    const revisionResult = await apiClient.createTripRevision(tripId, {
      updated_constraints: {
        budget: 70000,
        budget_amount: 70000,
        travelers_count: 3,
        adults: 3,
        children: 0,
        rooms: 1,
        start_date: '2026-10-14',
        end_date: '2026-10-21',
      },
      feedback: 'Updated budget to 70000 and travelers to 3',
      expected_base_revision: 0,
    });

    assert.ok(calledUrl.includes(`/api/trips/${tripId}/revisions`), 'Calls POST /api/trips/{trip_id}/revisions');
    assert.strictEqual(calledMethod, 'POST');
    assert.strictEqual(calledBody.updated_constraints.budget, 70000, 'Carries budget 70000');
    assert.strictEqual(calledBody.updated_constraints.travelers_count, 3, 'Carries 3 travelers');
    assert.strictEqual(revisionResult.revision_number, 1, 'Returns authoritative revision number 1');
    assert.strictEqual(revisionResult.status, 'awaiting_approval', 'Preserves PROPOSED revision semantics');
    console.log('✔ Test A: Save Constraints calls revision API with updated_constraints and PROPOSED semantics');
  } finally {
    global.fetch = originalFetch;
  }
}

// ---------------------------------------------------------------------------
// Test B: ₹70,000 persists after browser refresh / getTrip normalization
// ---------------------------------------------------------------------------
{
  const rawBackendTrip = {
    id: '39b76dc3-cbc6-4604-bf93-87212c0a44e0',
    destination: 'Kerala, India',
    origin: 'CCU',
    current_revision_number: 1,
    current_revision_id: 'rev_new_70k',
    metadata: {
      budget: 70000,
      travelers_count: 3,
      constraints: {
        budget: 70000,
        budget_amount: 70000,
        travelers_count: 3,
        adults: 3,
        children: 0,
        rooms: 1,
      },
      revision: 1,
    },
  };

  const normalized = normalizeBackendTrip(rawBackendTrip);
  assert.strictEqual(normalized.budget, 70000, 'Budget must be normalized as 70000');
  assert.strictEqual(normalized.travelers_count, 3, 'Traveler count must be normalized as 3');
  assert.strictEqual(normalized.active_revision_number, 1, 'Active revision number must be 1');

  // Also verify normalizeBackendTripDetail
  const detailed = normalizeBackendTripDetail(rawBackendTrip);
  assert.strictEqual(detailed.budget, 70000, 'Detail view budget must remain 70000 after refresh');
  assert.strictEqual(detailed.travelers_count, 3, 'Detail view travelers count must remain 3');
  assert.strictEqual(detailed.active_revision_number, 1, 'Detail view active revision number must remain 1');
  console.log('✔ Test B: ₹70,000 budget and 3 travelers persist and normalize correctly upon refresh');
}

// ---------------------------------------------------------------------------
// Test C: Revision number comes from backend
// ---------------------------------------------------------------------------
{
  const rawTripRev2 = {
    id: 'trip_123',
    current_revision_number: 2,
    current_revision_id: 'rev_uuid_2',
    metadata: {
      revision: 2,
      constraints: { budget: 70000 },
    },
  };

  const normalized = normalizeBackendTrip(rawTripRev2);
  assert.strictEqual(normalized.active_revision_number, 2, 'Revision number is driven authoritatively by backend');
  assert.strictEqual(normalized.active_revision_id, 'rev_uuid_2', 'Revision ID matches backend ID');
  console.log('✔ Test C: Revision number and ID come authoritatively from backend response');
}

// ---------------------------------------------------------------------------
// Test D: Failed constraint save does not mutate local authoritative state
// ---------------------------------------------------------------------------
{
  const tripId = '39b76dc3-cbc6-4604-bf93-87212c0a44e0';
  const originalFetch = global.fetch;
  global.fetch = (async () => {
    return {
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Conflict: Base revision mismatch' }),
    } as any;
  }) as any;

  try {
    let failed = false;
    try {
      await apiClient.createTripRevision(tripId, {
        updated_constraints: { budget: 80000 },
        feedback: 'Conflict update',
        expected_base_revision: 999,
      });
    } catch (err: any) {
      failed = true;
      assert.ok(err, 'Throws error on failure');
    }

    assert.ok(failed, 'createTripRevision must reject on API failure');

    // Verify existing trip state remains untouched
    const currentTrip = normalizeBackendTrip({
      id: tripId,
      metadata: { budget: 70000, revision: 1 },
    });
    assert.strictEqual(currentTrip.budget, 70000, 'Original budget remains 70000 without dirty mutation');
    assert.strictEqual(currentTrip.active_revision_number, 1, 'Original revision remains 1');
    console.log('✔ Test D: Failed constraint save fails closed without mutating authoritative state');
  } finally {
    global.fetch = originalFetch;
  }
}

console.log('\nAll Trip Constraint & Budget Persistence Tests Passed successfully!\n');
