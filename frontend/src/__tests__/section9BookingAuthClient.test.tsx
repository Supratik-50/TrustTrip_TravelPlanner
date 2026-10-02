/**
 * Section 9 Frontend Contract & Booking Authorization Integrity Tests
 *
 * Covers:
 * - Test 1: apiClient.authorizeBooking sends exact required revision_id and expected_revision_number
 * - Test 2: bookingService.authorizeBooking with valid revisionId + expectedRevision calls /booking-approval
 * - Test 3: bookingService.authorizeBooking missing revisionId fails locally; does NOT call backend
 * - Test 4: bookingService.authorizeBooking with non-positive expectedRevision (0) fails locally
 * - Test 5: apiClient.getBookingEligibility queries eligibility endpoint
 * - Test 6: Static audit — BookingWorkspace authorization has no current_revision fallback
 *
 * Requirement 4 Tests (Remove Mock Success Fallback for Booking Authorization):
 * - Test 7: Successful real API response updates authorization state
 * - Test 8: Real API 409 response produces conflict error, does NOT return success
 * - Test 9: Network failure on authorizeBooking throws error, does NOT invoke mock fallback, no fabricated object returned
 * - Test 10: Static audit — authorizeBooking in apiClient.ts has NO mock fallback; handleMockFallback has NO /booking-approval
 *
 * Requirement 3 Tests (Frontend Authorization Must Not Go Stale — Tests A–E):
 * - Test A: Revision N authorized → derived state shows authorized
 * - Test B: Active revision changes to N+1 → UI/derived state no longer treats N authorization as active/eligible
 * - Test C: Authorization for N + active N+1 → booking authorization action is shown as required again
 * - Test D: Matching revision ID but mismatched version → not eligible
 * - Test E: Historical N authorization remains visible as history but cannot enable booking for N+1
 * - Test F: Static audit — BookingWorkspace derives isBookingAuthorized from exact identity match
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { apiClient } from '../lib/apiClient';
import { TrustTripBookingService } from '../services/bookingService';
import type { TripDetail, BookingAuthorizationInfo } from '../types/trip';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function runTests() {
  console.log('\nRunning Section 9 Booking Authorization Frontend Contract Tests...');

  const originalFetch = global.fetch;

  // --------------------------------------------------------------------------
  // Test 1: apiClient.authorizeBooking sends exact required fields
  // --------------------------------------------------------------------------
  {
    let calledUrl = '';
    let calledBody: any = null;

    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calledUrl = String(url);
      calledBody = init?.body ? JSON.parse(String(init.body)) : null;
      return new Response(
        JSON.stringify({
          success: true,
          trip_id: 'trip_sec9_1',
          revision_id: 'rev_sec9_1',
          revision_number: 1,
          authorization_id: 'auth_sec9_1',
          authorized_at: new Date().toISOString(),
          is_authorized: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await apiClient.authorizeBooking('trip_sec9_1', {
      revision_id: 'rev_sec9_1',
      expected_revision_number: 1,
      notes: 'Test booking authorization',
    });

    assert.ok(calledUrl.includes('/api/trips/trip_sec9_1/booking-approval'));
    assert.strictEqual(calledBody?.revision_id, 'rev_sec9_1');
    assert.strictEqual(calledBody?.expected_revision_number, 1);
    assert.strictEqual(calledBody?.notes, 'Test booking authorization');
    assert.strictEqual(res.is_authorized, true);
    assert.strictEqual(res.authorization_id, 'auth_sec9_1');
    console.log('✔ Test 1: apiClient.authorizeBooking sends exact required revision_id and expected_revision_number.');
  }

  // --------------------------------------------------------------------------
  // Test 2: bookingService.authorizeBooking with valid params calls /booking-approval
  // --------------------------------------------------------------------------
  {
    let calledUrl = '';
    let calledBody: any = null;

    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calledUrl = String(url);
      calledBody = init?.body ? JSON.parse(String(init.body)) : null;
      return new Response(
        JSON.stringify({
          success: true,
          trip_id: 'trip_sec9_2',
          revision_id: 'rev_sec9_2',
          revision_number: 2,
          authorization_id: 'auth_sec9_2',
          authorized_at: new Date().toISOString(),
          is_authorized: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const service = new TrustTripBookingService();
    const res = await service.authorizeBooking('trip_sec9_2', 2, 'rev_sec9_2');

    assert.ok(calledUrl.includes('/api/trips/trip_sec9_2/booking-approval'));
    assert.strictEqual(calledBody?.revision_id, 'rev_sec9_2');
    assert.strictEqual(calledBody?.expected_revision_number, 2);
    assert.strictEqual(res.authorized, true);
    assert.strictEqual(res.authorization_id, 'auth_sec9_2');
    console.log('✔ Test 2: bookingService.authorizeBooking with valid revisionId + expectedRevision calls /booking-approval.');
  }

  // --------------------------------------------------------------------------
  // Test 3: bookingService.authorizeBooking missing revisionId fails locally
  // --------------------------------------------------------------------------
  {
    let fetchCalled = false;
    global.fetch = (async () => {
      fetchCalled = true;
      return new Response('{}', { status: 200 });
    }) as any;

    const service = new TrustTripBookingService();
    let threw = false;
    try {
      await service.authorizeBooking('trip_sec9_3', 1, '');
    } catch (err: any) {
      threw = true;
      assert.strictEqual(err.code, 'MISSING_REVISION_ID');
      assert.strictEqual(err.statusCode, 400);
    }

    assert.ok(threw, 'Should throw error when revisionId is empty');
    assert.strictEqual(fetchCalled, false, 'Fetch must NOT be called when revisionId is missing');
    console.log('✔ Test 3: bookingService.authorizeBooking missing revisionId fails locally; does NOT call backend.');
  }

  // --------------------------------------------------------------------------
  // Test 4: bookingService.authorizeBooking with invalid expectedRevision (0) fails locally
  // --------------------------------------------------------------------------
  {
    let fetchCalled = false;
    global.fetch = (async () => {
      fetchCalled = true;
      return new Response('{}', { status: 200 });
    }) as any;

    const service = new TrustTripBookingService();
    let threw = false;
    try {
      await service.authorizeBooking('trip_sec9_4', 0, 'rev_valid_id');
    } catch (err: any) {
      threw = true;
      assert.strictEqual(err.code, 'MISSING_EXPECTED_REVISION');
      assert.strictEqual(err.statusCode, 400);
    }

    assert.ok(threw, 'Should throw error when expectedRevision is 0');
    assert.strictEqual(fetchCalled, false, 'Fetch must NOT be called when expectedRevision is 0');
    console.log('✔ Test 4: bookingService.authorizeBooking with non-positive expectedRevision (0) fails locally.');
  }

  // --------------------------------------------------------------------------
  // Test 5: apiClient.getBookingEligibility queries eligibility endpoint
  // --------------------------------------------------------------------------
  {
    let calledUrl = '';
    global.fetch = (async (url: string | URL | Request) => {
      calledUrl = String(url);
      return new Response(
        JSON.stringify({
          success: true,
          eligibility: {
            is_eligible: true,
            trip_id: 'trip_sec9_5',
            revision_id: 'rev_sec9_5',
            revision_number: 1,
            is_approved: true,
            is_rejected: false,
            is_authorized: true,
            authorized_at: new Date().toISOString(),
            reasons: [],
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await apiClient.getBookingEligibility('trip_sec9_5', 'rev_sec9_5', 1);
    assert.ok(calledUrl.includes('/api/trips/trip_sec9_5/booking-eligibility'));
    assert.ok(calledUrl.includes('revision_id=rev_sec9_5'));
    assert.ok(calledUrl.includes('expected_revision_number=1'));
    assert.strictEqual(res.is_eligible, true);
    assert.strictEqual(res.is_authorized, true);
    console.log('✔ Test 5: apiClient.getBookingEligibility queries eligibility endpoint.');
  }

  // --------------------------------------------------------------------------
  // Test 6: Static audit — BookingWorkspace authorization has no current_revision fallback
  // --------------------------------------------------------------------------
  {
    const workspacePath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
    const content = fs.readFileSync(workspacePath, 'utf-8');

    // Extract handleAuthorizeBooking block
    const fnStart = content.indexOf('const handleAuthorizeBooking');
    assert.ok(fnStart !== -1, 'handleAuthorizeBooking must exist in BookingWorkspace.tsx');

    const fnEnd = content.indexOf('const handleVerifyFlight', fnStart);
    const fnBody = content.slice(fnStart, fnEnd);

    // Verify it uses ONLY active_revision_id and active_revision_number
    assert.ok(fnBody.includes('trip.active_revision_id'), 'Must read trip.active_revision_id');
    assert.ok(fnBody.includes('trip.active_revision_number'), 'Must read trip.active_revision_number');

    // Verify NO current_revision fallback in authorization handler
    assert.ok(
      !fnBody.includes('current_revision?.revision_id'),
      'FORBIDDEN: handleAuthorizeBooking must not fall back to current_revision?.revision_id'
    );
    assert.ok(
      !fnBody.includes('current_revision?.revision_number'),
      'FORBIDDEN: handleAuthorizeBooking must not fall back to current_revision?.revision_number'
    );

    console.log('✔ Test 6: Static audit — BookingWorkspace authorization has no current_revision fallback.');
  }

  // ==========================================================================
  // Requirement 4 Tests: Remove Mock Success Fallback for Booking Authorization
  // ==========================================================================
  console.log('\nRunning Requirement 4 Tests — No Mock Fallback for Booking Authorization...');

  // Test 7: Successful real API response updates authorization state
  {
    const authId = 'auth_real_777';
    const authAt = new Date().toISOString();

    global.fetch = (async () => {
      return new Response(
        JSON.stringify({
          success: true,
          trip_id: 'trip_req4_1',
          revision_id: 'rev_req4_1',
          revision_number: 1,
          authorization_id: authId,
          authorized_at: authAt,
          is_authorized: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await apiClient.authorizeBooking('trip_req4_1', {
      revision_id: 'rev_req4_1',
      expected_revision_number: 1,
    });

    assert.strictEqual(res.success, true);
    assert.strictEqual(res.is_authorized, true);
    assert.strictEqual(res.authorization_id, authId);
    console.log('✔ Test 7: Successful real API response updates authorization state.');
  }

  // Test 8: Real API 409 response produces conflict error, does NOT return success
  {
    global.fetch = (async () => {
      return new Response(
        JSON.stringify({
          detail: 'Trip canonical state has changed since revision creation',
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    let threw = false;
    try {
      await apiClient.authorizeBooking('trip_req4_2', {
        revision_id: 'rev_req4_2',
        expected_revision_number: 1,
      });
    } catch (err: any) {
      threw = true;
      assert.strictEqual(err.statusCode, 409);
      assert.ok(err.message.includes('Trip canonical state has changed'));
    }

    assert.ok(threw, 'API 409 must throw conflict error, no success returned');
    console.log('✔ Test 8: API 409 produces conflict error, does NOT return success.');
  }

  // Test 9: Network failure on authorizeBooking throws error, does NOT invoke mock fallback, no fabricated object returned
  {
    global.fetch = (async () => {
      const err = new TypeError('Failed to fetch');
      (err as any).code = 'NETWORK_ERROR';
      throw err;
    }) as any;

    let threw = false;
    let returnedObj: any = null;
    try {
      returnedObj = await apiClient.authorizeBooking('trip_req4_3', {
        revision_id: 'rev_req4_3',
        expected_revision_number: 1,
      });
    } catch (err: any) {
      threw = true;
      assert.strictEqual(err.code, 'NETWORK_ERROR');
    }

    assert.ok(threw, 'Network failure must throw deterministically');
    assert.strictEqual(returnedObj, null, 'No fabricated authorization object returned');
    console.log('✔ Test 9: Network failure throws error; mock fallback is NOT invoked; no fabricated object returned.');
  }

  // Test 10: Static audit — authorizeBooking in apiClient.ts has NO mock fallback; handleMockFallback has NO /booking-approval
  {
    const apiClientPath = path.resolve(__dirname, '../lib/apiClient.ts');
    const content = fs.readFileSync(apiClientPath, 'utf-8');

    // Extract authorizeBooking implementation
    const authMethodStart = content.indexOf('async authorizeBooking(');
    assert.ok(authMethodStart !== -1, 'authorizeBooking must exist in apiClient.ts');
    const authMethodEnd = content.indexOf('async getBookingEligibility(', authMethodStart);
    const authMethodBody = content.slice(authMethodStart, authMethodEnd);

    // authorizeBooking must NOT call handleMockFallback
    assert.ok(
      !authMethodBody.includes('handleMockFallback'),
      'FORBIDDEN: authorizeBooking must not call handleMockFallback on network error'
    );
    assert.ok(
      !authMethodBody.includes('catch (err: any)'),
      'FORBIDDEN: authorizeBooking must not catch network errors to return mock success'
    );

    // handleMockFallback must NOT contain /booking-approval mock block
    assert.ok(
      !content.includes("endpoint.endsWith('/booking-approval')"),
      'FORBIDDEN: handleMockFallback must not handle /booking-approval'
    );

    console.log('✔ Test 10: Static audit — authorizeBooking has NO mock fallback; handleMockFallback has NO /booking-approval.');
  }

  // ==========================================================================
  // Requirement 3 Tests: Frontend Authorization Must Not Go Stale (Tests A–E)
  // ==========================================================================
  console.log('\nRunning Requirement 3 Tests — Frontend Authorization Must Not Go Stale (Tests A–E)...');

  // Helper to simulate the exact identity matching rule defined in BookingWorkspace:
  // authorization.revision_id === active_revision_id AND authorization.revision_number === active_revision_number
  const checkWorkspaceAuthorization = (trip: TripDetail, localAuth?: BookingAuthorizationInfo) => {
    const activeRevId = trip.active_revision_id;
    const activeRevNum = trip.active_revision_number;
    const isPlanAccepted = Boolean(trip.is_approved || trip.status === 'approved');

    // Candidate authorization from state or trip
    const candidateAuth = localAuth || (
      trip.booking_authorization &&
      activeRevId &&
      activeRevNum &&
      trip.booking_authorization.revision_id === activeRevId &&
      trip.booking_authorization.revision_number === activeRevNum
        ? trip.booking_authorization
        : undefined
    );

    const isBookingAuthorized = Boolean(
      isPlanAccepted &&
      candidateAuth &&
      activeRevId &&
      activeRevNum &&
      candidateAuth.revision_id === activeRevId &&
      candidateAuth.revision_number === activeRevNum
    );

    const isStaleRevision = Boolean(
      (trip.current_revision?.revision_id && activeRevId && trip.current_revision.revision_id !== activeRevId) ||
      (trip.current_revision?.revision_number && activeRevNum && trip.current_revision.revision_number !== activeRevNum)
    );

    const canAuthorizeBooking = Boolean(
      isPlanAccepted &&
      activeRevId &&
      activeRevNum &&
      activeRevNum > 0 &&
      !isStaleRevision
    );

    const historicalAuthorization = Boolean(
      trip.booking_authorization &&
      (!activeRevId || !activeRevNum ||
       trip.booking_authorization.revision_id !== activeRevId ||
       trip.booking_authorization.revision_number !== activeRevNum)
    ) ? trip.booking_authorization : undefined;

    return {
      isBookingAuthorized,
      canAuthorizeBooking,
      historicalAuthorization,
    };
  };

  const baseTrip: TripDetail = {
    trip_id: 'trip_stale_test',
    title: 'Kyoto Journey',
    origin: 'Kolkata (CCU)',
    destination: 'Osaka (KIX)',
    start_date: '2026-10-14',
    end_date: '2026-10-24',
    status: 'approved',
    is_approved: true,
    active_revision_number: 1,
    active_revision_id: 'rev_1',
    travelers_count: 2,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    revisions: [],
  };

  // Test A: Revision N authorized → UI / state derives authorized=true
  {
    const authN: BookingAuthorizationInfo = {
      authorization_id: 'auth_n',
      trip_id: baseTrip.trip_id,
      revision_id: 'rev_1',
      revision_number: 1,
      authorized_at: new Date().toISOString(),
    };

    const tripN: TripDetail = {
      ...baseTrip,
      active_revision_id: 'rev_1',
      active_revision_number: 1,
      booking_authorization: authN,
    };

    const state = checkWorkspaceAuthorization(tripN, authN);
    assert.strictEqual(state.isBookingAuthorized, true, 'Test A: Revision N authorized must show authorized');
    assert.strictEqual(state.historicalAuthorization, undefined, 'Test A: No historical authorization when active matches');
    console.log('✔ Test A: Revision N authorized → UI shows authorized.');
  }

  // Test B: Active revision changes to N+1 → UI no longer treats N authorization as active/eligible
  {
    const authN: BookingAuthorizationInfo = {
      authorization_id: 'auth_n',
      trip_id: baseTrip.trip_id,
      revision_id: 'rev_1',
      revision_number: 1,
      authorized_at: new Date().toISOString(),
    };

    // Active revision changes to N+1 (rev_2, version 2)
    const tripNPlus1: TripDetail = {
      ...baseTrip,
      active_revision_id: 'rev_2',
      active_revision_number: 2,
      booking_authorization: authN, // historical record on trip
    };

    const state = checkWorkspaceAuthorization(tripNPlus1, undefined);
    assert.strictEqual(state.isBookingAuthorized, false, 'Test B: N authorization must NOT be treated as active/eligible for N+1');
    console.log('✔ Test B: Active revision changes to N+1 → UI no longer treats N authorization as active/eligible.');
  }

  // Test C: Authorization for N + active N+1 → booking authorization action is shown as required again
  {
    const authN: BookingAuthorizationInfo = {
      authorization_id: 'auth_n',
      trip_id: baseTrip.trip_id,
      revision_id: 'rev_1',
      revision_number: 1,
      authorized_at: new Date().toISOString(),
    };

    const tripNPlus1: TripDetail = {
      ...baseTrip,
      active_revision_id: 'rev_2',
      active_revision_number: 2,
      booking_authorization: authN,
    };

    const state = checkWorkspaceAuthorization(tripNPlus1, undefined);
    assert.strictEqual(state.isBookingAuthorized, false, 'Booking is not authorized');
    assert.strictEqual(state.canAuthorizeBooking, true, 'Test C: Booking authorization action is shown as required and available for N+1');
    console.log('✔ Test C: Authorization for N + active N+1 → booking authorization action is shown as required again.');
  }

  // Test D: Matching revision ID but mismatched version → not eligible
  {
    const authMismatchedVersion: BookingAuthorizationInfo = {
      authorization_id: 'auth_mismatch',
      trip_id: baseTrip.trip_id,
      revision_id: 'rev_1',
      revision_number: 1, // version 1
      authorized_at: new Date().toISOString(),
    };

    const tripMismatched: TripDetail = {
      ...baseTrip,
      active_revision_id: 'rev_1',
      active_revision_number: 2, // version 2!
      booking_authorization: authMismatchedVersion,
    };

    const state = checkWorkspaceAuthorization(tripMismatched, authMismatchedVersion);
    assert.strictEqual(state.isBookingAuthorized, false, 'Test D: Matching revision ID but mismatched version must NOT be eligible');
    console.log('✔ Test D: Matching revision ID but mismatched version → not eligible.');
  }

  // Test E: Historical N authorization remains visible as history but cannot enable booking for N+1
  {
    const authN: BookingAuthorizationInfo = {
      authorization_id: 'auth_n',
      trip_id: baseTrip.trip_id,
      revision_id: 'rev_1',
      revision_number: 1,
      authorized_at: new Date().toISOString(),
    };

    const tripNPlus1: TripDetail = {
      ...baseTrip,
      active_revision_id: 'rev_2',
      active_revision_number: 2,
      booking_authorization: authN,
    };

    const state = checkWorkspaceAuthorization(tripNPlus1, undefined);
    assert.strictEqual(state.isBookingAuthorized, false, 'Test E: Historical N authorization cannot enable booking for N+1');
    assert.ok(state.historicalAuthorization, 'Test E: Historical authorization remains visible');
    assert.strictEqual(state.historicalAuthorization?.revision_number, 1, 'Historical authorization retains rev 1 info');
    console.log('✔ Test E: Historical N authorization remains visible as history but cannot enable booking for N+1.');
  }

  // Test F: Static audit of BookingWorkspace.tsx
  {
    const workspacePath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
    const content = fs.readFileSync(workspacePath, 'utf-8');

    // Must not have unvalidated boolean state
    assert.ok(
      !content.includes('const [isBookingAuthorized, setIsBookingAuthorized] = useState<boolean>('),
      'FORBIDDEN: isBookingAuthorized must not be a standalone mutable boolean state'
    );

    // Must derive exact revision identity match
    assert.ok(
      content.includes('candidateAuth.revision_id === activeRevId'),
      'Must verify revision_id exact match'
    );
    assert.ok(
      content.includes('candidateAuth.revision_number === activeRevNum'),
      'Must verify revision_number exact match'
    );

    // Must check staleness
    assert.ok(
      content.includes('isStaleRevision'),
      'Must evaluate isStaleRevision'
    );

    console.log('✔ Test F: Static audit — BookingWorkspace derives isBookingAuthorized from exact identity match.');
  }

  // Restore original fetch
  global.fetch = originalFetch;

  console.log('\nAll Section 9 Frontend Contract & Integrity Tests PASSED!\n');
}

runTests().catch((err) => {
  console.error('Section 9 Frontend Contract Tests FAILED:', err);
  process.exit(1);
});
