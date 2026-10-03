/**
 * Section 10 Frontend Hotel Payment Contract & Integrity Tests
 *
 * Covers:
 * - Test 1: apiClient.createHotelPaymentOrder targets /api/hotel-payments/create-order with mandatory Section 10 fields
 * - Test 2: apiClient.createHotelPaymentOrder with snapshot_id targets /api/hotel-payments/create-order-from-snapshot
 * - Test 3: apiClient.verifyAndBookHotel targets /api/hotel-payments/verify-and-book
 * - Test 4: bookingService.processHotelPayment successfully executes order creation and verification
 * - Test 5: bookingService.processHotelPayment fails locally if revision_id or expected_revision_number is missing
 * - Test 6: Failed/non-authoritative payment does NOT become confirmation
 * - Test 7: Static audit — No occurrence of /api/hotels/payment in frontend/src
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { api } from '../lib/apiClient';
import { TrustTripBookingService } from '../services/bookingService';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function runTests() {
  console.log('\nRunning Section 10 Hotel Payment Frontend Contract Tests...');
  const originalFetch = global.fetch;

  // --------------------------------------------------------------------------
  // Test 1: apiClient.createHotelPaymentOrder targets /api/hotel-payments/create-order
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
          transaction_id: 'tx_sec10_hotel_1',
          razorpay_order_id: 'order_rzp_1',
          amount_inr: 15000,
          currency: 'INR',
          key_id: 'rzp_key_test',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await api.createHotelPaymentOrder({
      offer: { id: 'off_1', hotel_name: 'Hyatt' },
      prebook: { prebook_id: 'pb_1' },
      holder: { firstName: 'Alice', lastName: 'Smith' },
      guests: [{ firstName: 'Alice', lastName: 'Smith' }],
      trip_id: 'trip_101',
      revision_id: 'rev_101',
      expected_revision_number: 2,
      payment_idempotency_key: 'idem_key_101',
      final_confirmation: true,
    });

    assert.ok(calledUrl.includes('/api/hotel-payments/create-order'), `Expected URL to include /api/hotel-payments/create-order, got: ${calledUrl}`);
    assert.strictEqual(calledBody.trip_id, 'trip_101');
    assert.strictEqual(calledBody.revision_id, 'rev_101');
    assert.strictEqual(calledBody.expected_revision_number, 2);
    assert.strictEqual(calledBody.payment_idempotency_key, 'idem_key_101');
    assert.strictEqual(calledBody.final_confirmation, true);
    assert.strictEqual(res.success, true);
    assert.strictEqual(res.transaction_id, 'tx_sec10_hotel_1');
    console.log('✓ Test 1 Passed: createHotelPaymentOrder targets /api/hotel-payments/create-order with mandatory fields');
  }

  // --------------------------------------------------------------------------
  // Test 2: apiClient.createHotelPaymentOrder with snapshot_id targets /create-order-from-snapshot
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
          transaction_id: 'tx_sec10_snap_1',
          razorpay_order_id: 'order_rzp_snap',
          amount_inr: 12000,
          currency: 'INR',
          key_id: 'rzp_key_test',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await api.createHotelPaymentOrder({
      snapshot_id: 'snap_hotel_99',
      trip_id: 'trip_snap_1',
      revision_id: 'rev_snap_1',
      expected_revision_number: 1,
      payment_idempotency_key: 'idem_snap_key',
      holder: { firstName: 'Bob', lastName: 'Jones' },
      guests: [{ firstName: 'Bob', lastName: 'Jones' }],
      final_confirmation: true,
    });

    assert.ok(calledUrl.includes('/api/hotel-payments/create-order-from-snapshot'), `Expected /api/hotel-payments/create-order-from-snapshot, got: ${calledUrl}`);
    assert.strictEqual(calledBody.snapshot_id, 'snap_hotel_99');
    assert.strictEqual(calledBody.trip_id, 'trip_snap_1');
    assert.strictEqual(calledBody.revision_id, 'rev_snap_1');
    assert.strictEqual(calledBody.expected_revision_number, 1);
    assert.strictEqual(res.success, true);
    console.log('✓ Test 2 Passed: createHotelPaymentOrder with snapshot_id targets /api/hotel-payments/create-order-from-snapshot');
  }

  // --------------------------------------------------------------------------
  // Test 3: apiClient.verifyAndBookHotel targets /api/hotel-payments/verify-and-book
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
          transaction_id: 'tx_sec10_hotel_1',
          payment_captured: true,
          booking: {
            booking_id: 'bkg_ht_888',
            confirmation_code: 'CONF-HT-888',
            hotel_name: 'Hyatt Regency',
            status: 'confirmed',
          },
          amount_inr: 15000,
          pdf_available: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await api.verifyAndBookHotel({
      transaction_id: 'tx_sec10_hotel_1',
      razorpay_order_id: 'order_rzp_1',
      razorpay_payment_id: 'pay_rzp_1',
      razorpay_signature: 'sig_valid_1',
      offer: { id: 'off_1' },
      prebook: { prebook_id: 'pb_1' },
      holder: { firstName: 'Alice', lastName: 'Smith' },
      guests: [],
      trip_id: 'trip_101',
      revision_id: 'rev_101',
      expected_revision_number: 2,
      final_confirmation: true,
    });

    assert.ok(calledUrl.includes('/api/hotel-payments/verify-and-book'), `Expected /api/hotel-payments/verify-and-book, got: ${calledUrl}`);
    assert.strictEqual(calledBody.transaction_id, 'tx_sec10_hotel_1');
    assert.strictEqual(calledBody.trip_id, 'trip_101');
    assert.strictEqual(calledBody.revision_id, 'rev_101');
    assert.strictEqual(res.success, true);
    assert.strictEqual(res.booking.confirmation_code, 'CONF-HT-888');
    console.log('✓ Test 3 Passed: verifyAndBookHotel targets /api/hotel-payments/verify-and-book');
  }

  // --------------------------------------------------------------------------
  // Test 4: bookingService.processHotelPayment complete successful flow
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    const calls: string[] = [];

    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url);
      calls.push(u);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({
            prebook_id: 'pb_live_44',
            amount: 18000,
            currency: 'INR',
            hotel_name: 'Heritage Palace',
            expires_at: new Date(Date.now() + 600000).toISOString(),
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_flow_123',
            razorpay_order_id: 'ord_flow_123',
            amount_inr: 18000,
            currency: 'INR',
            key_id: 'rzp_key_live',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_flow_123',
            payment_captured: true,
            booking: {
              booking_id: 'ht_bkg_confirmed_77',
              confirmation_code: 'VOUCHER-HERITAGE-77',
              hotel_name: 'Heritage Palace',
              room_name: 'Deluxe Suite',
              status: 'confirmed',
            },
            amount_inr: 18000,
            pdf_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_flow_1', 'ht_palace_1');
    const result = await service.processHotelPayment(
      'trip_flow_1',
      'card',
      { first_name: 'Dev', last_name: 'Sharma', email: 'dev@trusttrip.com', phone: '+919988776655' },
      'rev_flow_uuid',
      3
    );

    assert.ok(calls.some((u) => u.includes('/api/hotel-payments/create-order')));
    assert.ok(calls.some((u) => u.includes('/api/hotel-payments/verify-and-book')));
    assert.strictEqual(result.confirmation_code, 'VOUCHER-HERITAGE-77');
    assert.strictEqual(result.property_name, 'Heritage Palace');
    assert.strictEqual(result.status, 'voucher_issued');
    console.log('✓ Test 4 Passed: bookingService.processHotelPayment complete flow succeeds with authoritative data');
  }

  // --------------------------------------------------------------------------
  // Test 5: bookingService.processHotelPayment fails locally if revision is missing
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();

    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_test', amount: 5000, currency: 'INR', hotel_name: 'Test Hotel' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      // When getTrip is called: return trip without revision
      if (u.includes('/api/trips/')) {
        return new Response(
          JSON.stringify({ trip_id: 'trip_no_rev', current_revision_id: null, current_revision_number: null }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_no_rev', 'ht_1');

    let threw = false;
    try {
      await service.processHotelPayment(
        'trip_no_rev',
        'card',
        { first_name: 'Test', last_name: 'User', email: 'test@example.com', phone: '+919876543210' },
        '', // Empty revision_id
        0   // Invalid revision number
      );
    } catch (err: any) {
      threw = true;
      assert.ok(err.message.includes('Exact revision identity') || err.message.includes('revision'), `Unexpected error: ${err.message}`);
    }
    assert.strictEqual(threw, true, 'Expected processHotelPayment to fail when revision identity is missing');
    console.log('✓ Test 5 Passed: bookingService.processHotelPayment fails locally on missing revision identity');
  }

  // --------------------------------------------------------------------------
  // Test 6: Failed/non-authoritative payment does NOT become confirmation
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();

    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_fail_test', amount: 10000, currency: 'INR', hotel_name: 'Fail Hotel' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({ success: true, transaction_id: 'tx_fail', razorpay_order_id: 'ord_fail' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        return new Response(
          JSON.stringify({
            success: false,
            payment_captured: true,
            requires_refund: true,
            error: 'Trip revision changed after payment capture. Booking requires manual review.',
          }),
          { status: 409, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_fail', 'ht_fail');

    let threw = false;
    try {
      await service.processHotelPayment(
        'trip_fail',
        'card',
        { first_name: 'Test', last_name: 'User', email: 'test@example.com', phone: '+919876543210' },
        'rev_123',
        1
      );
    } catch (err: any) {
      threw = true;
      assert.ok(err.message.includes('review') || err.message.includes('failed') || err.message.includes('revision'), `Unexpected error message: ${err.message}`);
    }
    assert.strictEqual(threw, true, 'Failed verification must not return a voucher or confirmed record');
    console.log('✓ Test 6 Passed: Failed payment verification throws error and does NOT fabricate confirmation');
  }

  // --------------------------------------------------------------------------
  // Test 7: Static audit — No occurrence of /api/hotels/payment anywhere in frontend/src
  // --------------------------------------------------------------------------
  {
    const srcDir = path.resolve(__dirname, '..');

    function searchDir(dir: string, pattern: string): string[] {
      const results: string[] = [];
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          results.push(...searchDir(fullPath, pattern));
        } else if (entry.isFile() && (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx'))) {
          // Skip the test file itself
          if (entry.name === 'section10HotelPayment.test.tsx') continue;
          const content = fs.readFileSync(fullPath, 'utf8');
          if (content.includes(pattern)) {
            results.push(fullPath);
          }
        }
      }
      return results;
    }

    const matches = searchDir(srcDir, '/api/hotels/payment');
    assert.strictEqual(
      matches.length,
      0,
      `Found forbidden /api/hotels/payment in files: ${matches.join(', ')}`
    );
    console.log('✓ Test 7 Passed: Static audit verified zero occurrences of /api/hotels/payment in frontend/src');
  }

  // --------------------------------------------------------------------------
  // Test 8: Exact Hotel Revision Identity Binding & No current_revision Fallback
  // --------------------------------------------------------------------------
  {
    const workspacePath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
    const workspaceCode = fs.readFileSync(workspacePath, 'utf8');

    // Extract handleProcessHotelPayment function body
    const match = workspaceCode.match(/const handleProcessHotelPayment = async \([\s\S]*?\n  \};/);
    assert.ok(match, 'handleProcessHotelPayment must exist in BookingWorkspace.tsx');
    const fnBody = match[0];

    // A. Valid active authorization sends its exact revision_id and revision_number
    assert.ok(fnBody.includes('activeAuthorization.revision_id'), 'Must read revision_id from activeAuthorization');
    assert.ok(fnBody.includes('activeAuthorization.revision_number'), 'Must read revision_number from activeAuthorization');

    // B. Missing active authorization does NOT fall back to current_revision
    assert.ok(!fnBody.includes('trip.current_revision'), 'FORBIDDEN: Must not fall back to trip.current_revision');
    assert.ok(!fnBody.includes('current_revision?.revision_id'), 'FORBIDDEN: Must not fall back to current_revision?.revision_id');
    assert.ok(!fnBody.includes('current_revision?.revision_number'), 'FORBIDDEN: Must not fall back to current_revision?.revision_number');

    // C. Missing exact revision fails locally before payment is attempted
    assert.ok(fnBody.includes('throw authErr') || fnBody.includes('throw new Error'), 'Must throw locally if activeAuthorization is missing/invalid');

    // D. No default revision number 1 remains in the production booking flow
    assert.ok(!fnBody.includes('|| 1'), 'FORBIDDEN: Must not default revision_number to 1');
    assert.ok(!fnBody.includes('?? 1'), 'FORBIDDEN: Must not default revision_number to 1');

    console.log('✓ Test 8 Passed: Exact hotel revision binding verified — no current_revision fallback, no default revision 1');
  }

  // --------------------------------------------------------------------------
  // Test 9: Confirmation Truthfulness — No Fabricated Values
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();

    // 9A: Full authoritative backend response -> confirmation succeeds with exact values
    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({
            prebook_id: 'pb_truth_1',
            amount: 22000,
            currency: 'INR',
            hotel_name: 'The Oberoi Grand',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_truth_1',
            razorpay_order_id: 'ord_truth_1',
            amount_inr: 22000,
            currency: 'INR',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_truth_1',
            payment_captured: true,
            booking: {
              booking_id: 'ht_bkg_999',
              confirmation_code: 'VOUCHER-OBEROI-999',
              hotel_name: 'The Oberoi Grand',
              address: '15 Jawaharlal Nehru Road, Kolkata',
              room_name: 'Luxury Suite',
              check_in: '2026-11-01',
              check_out: '2026-11-05',
              nights: 4,
              cancellation_policy: 'Non-refundable after 24h prior to check-in',
              status: 'confirmed',
            },
            amount_inr: 22000,
            pdf_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_truth_1', 'ht_oberoi');
    const fullResult = await service.processHotelPayment(
      'trip_truth_1',
      'card',
      { first_name: 'Priya', last_name: 'Sen', email: 'priya@example.com', phone: '+919876543210' },
      'rev_auth_1',
      2
    );

    assert.strictEqual(fullResult.confirmation_code, 'VOUCHER-OBEROI-999');
    assert.strictEqual(fullResult.property_name, 'The Oberoi Grand');
    assert.strictEqual(fullResult.check_in_date, '2026-11-01');
    assert.strictEqual(fullResult.check_out_date, '2026-11-05');
    assert.strictEqual(fullResult.nights_count, 4);
    assert.strictEqual(fullResult.cancellation_policy, 'Non-refundable after 24h prior to check-in');
    console.log('✓ Test 9A Passed: Full authoritative backend response populates confirmation accurately');

    // 9B: Missing check-in/check-out/nights -> no fabricated values
    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_truth_2', amount: 15000, currency: 'INR' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({ success: true, transaction_id: 'tx_truth_2', razorpay_order_id: 'ord_truth_2' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_truth_2',
            payment_captured: true,
            booking: {
              booking_id: 'ht_bkg_minimal',
              confirmation_code: 'VOUCHER-MINIMAL-01',
              // Omit check_in, check_out, nights
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_truth_2', 'ht_min');
    const minimalResult = await service.processHotelPayment(
      'trip_truth_2',
      'card',
      { first_name: 'Priya', last_name: 'Sen', email: 'priya@example.com', phone: '+919876543210' },
      'rev_auth_2',
      1
    );

    assert.notStrictEqual(minimalResult.check_in_date, '2026-10-12', 'Must not fabricate 2026-10-12');
    assert.notStrictEqual(minimalResult.check_out_date, '2026-10-18', 'Must not fabricate 2026-10-18');
    assert.notStrictEqual(minimalResult.nights_count, 6, 'Must not fabricate 6 nights');
    assert.strictEqual(minimalResult.check_in_date, undefined, 'Missing check-in must be undefined/absent');
    assert.strictEqual(minimalResult.check_out_date, undefined, 'Missing check-out must be undefined/absent');
    assert.strictEqual(minimalResult.nights_count, undefined, 'Missing nights must be undefined/absent');
    console.log('✓ Test 9B Passed: Missing check-in/check-out/nights are not fabricated');

    // 9C: Missing property name / cancellation policy -> no fabricated values
    assert.notStrictEqual(minimalResult.property_name, 'Boutique Stay', 'Must not fabricate Boutique Stay');
    assert.notStrictEqual(
      minimalResult.cancellation_policy,
      'Standard hotel cancellation terms apply.',
      'Must not fabricate default cancellation policy'
    );
    assert.strictEqual(minimalResult.property_name, undefined, 'Missing property name must be undefined/absent');
    assert.strictEqual(minimalResult.cancellation_policy, undefined, 'Missing cancellation policy must be undefined/absent');
    console.log('✓ Test 9C Passed: Missing property name / cancellation policy are not fabricated');

    // 9D: Failed payment / verification -> no confirmation
    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_truth_3', amount: 15000, currency: 'INR' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({ success: true, transaction_id: 'tx_truth_3' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        return new Response(
          JSON.stringify({
            success: false,
            payment_captured: false,
            error: 'Signature verification failed',
          }),
          { status: 400, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_truth_3', 'ht_fail_verify');
    let verifyThrew = false;
    try {
      await service.processHotelPayment(
        'trip_truth_3',
        'card',
        { first_name: 'Priya', last_name: 'Sen', email: 'priya@example.com', phone: '+919876543210' },
        'rev_auth_3',
        1
      );
    } catch {
      verifyThrew = true;
    }
    assert.strictEqual(verifyThrew, true, 'Failed payment must throw and not return confirmation');
    console.log('✓ Test 9D Passed: Failed payment verification throws error without confirmation');

    // 9E: Static audit of bookingService.ts — zero hardcoded fake defaults
    const servicePath = path.resolve(__dirname, '../services/bookingService.ts');
    const serviceCode = fs.readFileSync(servicePath, 'utf8');

    assert.ok(!serviceCode.includes("'2026-10-12'"), 'bookingService.ts must not contain literal 2026-10-12');
    assert.ok(!serviceCode.includes("'2026-10-18'"), 'bookingService.ts must not contain literal 2026-10-18');
    assert.ok(!serviceCode.includes("'Boutique Stay'"), 'bookingService.ts must not contain literal Boutique Stay');
    assert.ok(
      !serviceCode.includes('Standard hotel cancellation terms apply'),
      'bookingService.ts must not contain default cancellation policy string'
    );
    console.log('✓ Test 9E Passed: Static audit verified zero fake literals in bookingService.ts');
  }

  // --------------------------------------------------------------------------
  // Test 10: Micro-Remediation Z — Authoritative-Only Final Confirmation (A–G)
  // --------------------------------------------------------------------------
  {
    // Helper to build a fake prebook/offer with known "poison" values that must NOT
    // appear in the final confirmation when the backend omits those fields.
    const POISON_OFFER = {
      id: 'off_poison',
      hotel_name: 'POISON_OFFER_HOTEL',
      name: 'POISON_OFFER_NAME',
      address: 'POISON_OFFER_ADDRESS',
      room_name: 'POISON_OFFER_ROOM',
      check_in_date: '1900-01-01',
      checkin: '1900-01-01',
      check_out_date: '1900-12-31',
      checkout: '1900-12-31',
      nights: 999,
      nights_count: 999,
      cancellation_policy: 'POISON_OFFER_POLICY',
      provider: 'liteapi',
      price: 99999,
      currency: 'INR',
      price_inr: 99999,
    };

    // 10A: Full authoritative backend response → all supplied backend fields returned correctly
    {
      const service = new TrustTripBookingService();
      global.fetch = (async (url: string | URL | Request) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_a', amount: 22000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_a', razorpay_order_id: 'ord_z_a' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                confirmation_code: 'VOUCHER-Z-A',
                hotel_name: 'Grand Hotel Backend',
                address: '1 Backend Road',
                room_name: 'Suite Backend',
                check_in: '2027-03-10',
                check_out: '2027-03-15',
                nights: 5,
                cancellation_policy: 'Backend Policy',
                status: 'confirmed',
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      await service.prepareHotelBooking('trip_z_a', 'ht_z_a', POISON_OFFER);
      const res = await service.processHotelPayment(
        'trip_z_a', 'card',
        { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
        'rev_z_a', 1
      );
      assert.strictEqual(res.confirmation_code, 'VOUCHER-Z-A');
      assert.strictEqual(res.property_name, 'Grand Hotel Backend');
      assert.strictEqual(res.property_address, '1 Backend Road');
      assert.strictEqual(res.room_name, 'Suite Backend');
      assert.strictEqual(res.check_in_date, '2027-03-10');
      assert.strictEqual(res.check_out_date, '2027-03-15');
      assert.strictEqual(res.nights_count, 5);
      assert.strictEqual(res.cancellation_policy, 'Backend Policy');
      console.log('✓ Test 10A Passed: Full authoritative backend fields returned correctly');
    }

    // 10B: Backend omits dates → check_in_date and check_out_date are undefined; offer dates do NOT appear
    {
      const service = new TrustTripBookingService();
      global.fetch = (async (url: string | URL | Request) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_b', amount: 10000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_b', razorpay_order_id: 'ord_z_b' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                confirmation_code: 'VOUCHER-Z-B',
                hotel_name: 'Backend Hotel B',
                // check_in, check_out deliberately omitted
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      await service.prepareHotelBooking('trip_z_b', 'ht_z_b', POISON_OFFER);
      const res = await service.processHotelPayment(
        'trip_z_b', 'card',
        { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
        'rev_z_b', 1
      );
      assert.strictEqual(res.check_in_date, undefined, 'check_in_date must be undefined when backend omits it');
      assert.strictEqual(res.check_out_date, undefined, 'check_out_date must be undefined when backend omits it');
      assert.notStrictEqual(res.check_in_date, '1900-01-01', 'Poison offer date must NOT appear in confirmation');
      assert.notStrictEqual(res.check_out_date, '1900-12-31', 'Poison offer date must NOT appear in confirmation');
      console.log('✓ Test 10B Passed: Omitted backend dates are undefined; offer dates do not bleed through');
    }

    // 10C: Backend omits nights → nights_count is undefined; offer.nights does NOT appear
    {
      const service = new TrustTripBookingService();
      global.fetch = (async (url: string | URL | Request) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_c', amount: 10000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_c', razorpay_order_id: 'ord_z_c' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                confirmation_code: 'VOUCHER-Z-C',
                // nights deliberately omitted
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      await service.prepareHotelBooking('trip_z_c', 'ht_z_c', POISON_OFFER);
      const res = await service.processHotelPayment(
        'trip_z_c', 'card',
        { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
        'rev_z_c', 1
      );
      assert.strictEqual(res.nights_count, undefined, 'nights_count must be undefined when backend omits it');
      assert.notStrictEqual(res.nights_count, 999, 'Poison offer nights (999) must NOT appear in confirmation');
      console.log('✓ Test 10C Passed: Omitted backend nights are undefined; offer.nights does not bleed through');
    }

    // 10D: Backend omits property name → property_name is undefined; offer.hotel_name/name do NOT appear
    {
      const service = new TrustTripBookingService();
      global.fetch = (async (url: string | URL | Request) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_d', amount: 10000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_d', razorpay_order_id: 'ord_z_d' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                confirmation_code: 'VOUCHER-Z-D',
                // hotel_name / property_name / name deliberately omitted
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      await service.prepareHotelBooking('trip_z_d', 'ht_z_d', POISON_OFFER);
      const res = await service.processHotelPayment(
        'trip_z_d', 'card',
        { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
        'rev_z_d', 1
      );
      assert.strictEqual(res.property_name, undefined, 'property_name must be undefined when backend omits it');
      assert.notStrictEqual(res.property_name, 'POISON_OFFER_HOTEL', 'Poison offer.hotel_name must NOT appear in confirmation');
      assert.notStrictEqual(res.property_name, 'POISON_OFFER_NAME', 'Poison offer.name must NOT appear in confirmation');
      console.log('✓ Test 10D Passed: Omitted backend property_name is undefined; offer fields do not bleed through');
    }

    // 10E: Backend omits cancellation policy → cancellation_policy is undefined; offer.cancellation_policy does NOT appear
    {
      const service = new TrustTripBookingService();
      global.fetch = (async (url: string | URL | Request) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_e', amount: 10000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_e', razorpay_order_id: 'ord_z_e' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                confirmation_code: 'VOUCHER-Z-E',
                hotel_name: 'Some Hotel',
                // cancellation_policy deliberately omitted
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      await service.prepareHotelBooking('trip_z_e', 'ht_z_e', POISON_OFFER);
      const res = await service.processHotelPayment(
        'trip_z_e', 'card',
        { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
        'rev_z_e', 1
      );
      assert.strictEqual(res.cancellation_policy, undefined, 'cancellation_policy must be undefined when backend omits it');
      assert.notStrictEqual(res.cancellation_policy, 'POISON_OFFER_POLICY', 'Poison offer.cancellation_policy must NOT appear in confirmation');
      console.log('✓ Test 10E Passed: Omitted backend cancellation_policy is undefined; offer policy does not bleed through');
    }

    // 10F: Backend confirmation code missing → flow fails closed, no confirmation returned
    {
      const service = new TrustTripBookingService();
      global.fetch = (async (url: string | URL | Request) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_f', amount: 10000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_f', razorpay_order_id: 'ord_z_f' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                // No confirmation_code, no booking_id, no id
                hotel_name: 'Some Hotel',
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      await service.prepareHotelBooking('trip_z_f', 'ht_z_f', POISON_OFFER);
      let threwOnMissingCode = false;
      try {
        await service.processHotelPayment(
          'trip_z_f', 'card',
          { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
          'rev_z_f', 1
        );
      } catch (err: any) {
        threwOnMissingCode = true;
        assert.ok(
          err.message.includes('confirmation code') || err.message.includes('authoritative'),
          `Expected confirmation code error, got: ${err.message}`
        );
      }
      assert.strictEqual(threwOnMissingCode, true, 'Missing confirmation code must throw, not return partial confirmation');
      console.log('✓ Test 10F Passed: Missing backend confirmation code fails closed; no confirmation returned');
    }

    // 10G: Regression — pre-payment offer/prebook data is still sent to booking API;
    //      final confirmation does NOT depend on them.
    {
      const service = new TrustTripBookingService();
      const capturedOrderBody: any[] = [];

      global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        const u = String(url);
        if (u.includes('/api/hotels/prebook')) {
          return new Response(
            JSON.stringify({ prebook_id: 'pb_z_g', amount: 30000, currency: 'INR' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/create-order')) {
          capturedOrderBody.push(init?.body ? JSON.parse(String(init.body)) : null);
          return new Response(
            JSON.stringify({ success: true, transaction_id: 'tx_z_g', razorpay_order_id: 'ord_z_g' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (u.includes('/api/hotel-payments/verify-and-book')) {
          return new Response(
            JSON.stringify({
              success: true,
              payment_captured: true,
              booking: {
                confirmation_code: 'VOUCHER-Z-G',
                // All optional fields absent — backend is authoritative and omits them
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify({}), { status: 404 });
      }) as any;

      const OFFER_G = {
        ...POISON_OFFER,
        hotel_name: 'OFFER_G_HOTEL',
        check_in_date: '1999-06-15',
        check_out_date: '1999-06-20',
        nights: 5,
        cancellation_policy: 'OFFER_G_POLICY',
      };

      await service.prepareHotelBooking('trip_z_g', 'ht_z_g', OFFER_G);
      const res = await service.processHotelPayment(
        'trip_z_g', 'card',
        { first_name: 'A', last_name: 'B', email: 'a@b.com', phone: '+919999999999' },
        'rev_z_g', 1
      );

      // Offer data WAS sent to the create-order API (legitimate pre-booking use)
      assert.ok(capturedOrderBody.length > 0, 'offer/prebook data must be sent to create-order request');

      // But the final confirmation MUST NOT contain any offer-derived values
      assert.strictEqual(res.confirmation_code, 'VOUCHER-Z-G');
      assert.notStrictEqual(res.property_name, 'OFFER_G_HOTEL', 'Final confirmation must not use offer.hotel_name');
      assert.strictEqual(res.property_name, undefined, 'property_name absent from backend → undefined');
      assert.notStrictEqual(res.check_in_date, '1999-06-15', 'Final confirmation must not use offer.check_in_date');
      assert.strictEqual(res.check_in_date, undefined, 'check_in_date absent from backend → undefined');
      assert.notStrictEqual(res.nights_count, 5, 'Final confirmation must not use offer.nights');
      assert.strictEqual(res.nights_count, undefined, 'nights_count absent from backend → undefined');
      assert.notStrictEqual(res.cancellation_policy, 'OFFER_G_POLICY', 'Final confirmation must not use offer.cancellation_policy');
      assert.strictEqual(res.cancellation_policy, undefined, 'cancellation_policy absent from backend → undefined');
      console.log('✓ Test 10G Passed: Pre-booking offer/prebook data sent to API; final confirmation ignores it');
    }

    // Static audit: confirm zero offer/prebook fallback expressions in the final confirmation mapping
    {
      const servicePath = path.resolve(__dirname, '../services/bookingService.ts');
      const serviceCode = fs.readFileSync(servicePath, 'utf8');

      // Isolate only the final confirmation mapping block (after verify-and-book success check)
      const confirmMapStart = serviceCode.indexOf('// Section 10: Final confirmation MUST use ONLY');
      const confirmMapEnd = serviceCode.indexOf("status: 'voucher_issued'", confirmMapStart);
      assert.ok(confirmMapStart >= 0, 'Must find Section 10 final confirmation mapping comment');
      const confirmBlock = serviceCode.slice(confirmMapStart, confirmMapEnd + 50);

      const forbiddenFallbacks = [
        'offer.hotel_name',
        'offer.name',
        'prebook.hotel_name',
        'offer.check_in_date',
        'offer.checkin',
        'offer.check_out_date',
        'offer.checkout',
        'offer.nights',
        'offer.nights_count',
        'offer.cancellation_policy',
        'offer.room_name',
        'offer.address',
      ];
      for (const forbidden of forbiddenFallbacks) {
        assert.ok(
          !confirmBlock.includes(forbidden),
          `FORBIDDEN: Final confirmation mapping must not reference "${forbidden}"`
        );
      }
      console.log('✓ Test 10 Static Audit Passed: Zero offer/prebook fallback expressions in final confirmation mapping');
    }
  }

  // Restore fetch
  global.fetch = originalFetch;
  console.log('\nAll Section 10 Hotel Payment Frontend Contract Tests Passed!\n');
}

runTests().catch((err) => {
  console.error('Section 10 Hotel Payment test failed:', err);
  process.exit(1);
});
