/**
 * Hotel Reservation & Payment Button Interaction and Integrity Tests
 *
 * Covers:
 * 1. Button A (Prepare Hotel Reservation):
 *    - Invokes prebook API with complete parameters (trip_id, snapshot_id, hotel_id, guests, offer).
 *    - UI guard: HotelBookingPanel disables button and warns when hotel selection is missing.
 *    - Error handling: Catches API rejection, renders visible error banner, and resets loading state.
 *    - Duplicate click lock: Prevents duplicate submissions while preparation is in flight.
 *    - State rehydration: Recovers prebook from trip.metadata.hotel_prebook across refresh.
 *
 * 2. Button B (Complete Hotel Payment):
 *    - Valid prebook permits payment order initiation.
 *    - Missing or invalid prebook fails closed and prevents order creation.
 *    - Reconciliation guard: Pending reconciliation bypasses new order creation and re-opens verification.
 *    - Mandatory backend verification: Payment cannot transition to booked without genuine verification.
 *    - Static source audit: Verifies error handling, loading states, traveler validation, and authorization awareness.
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
  console.log('\nRunning Hotel Reservation & Payment Buttons Test Suite...\n');
  const originalFetch = global.fetch;

  // --------------------------------------------------------------------------
  // Test 1: Button A invokes prebookHotel with complete parameters and updates state
  // --------------------------------------------------------------------------
  {
    let capturedBody: any = null;
    let capturedUrl = '';
    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      capturedUrl = String(url);
      capturedBody = init?.body ? JSON.parse(String(init.body)) : null;
      return new Response(
        JSON.stringify({
          success: true,
          prebook_id: 'pb_btn_test_001',
          amount: 22000,
          currency: 'INR',
          status: 'prebooked',
          hotel_name: 'Oberoi Grand Kolkata',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const service = new TrustTripBookingService();
    const offer = {
      id: 'snap_oberoi_kolkata',
      snapshot_id: 'snap_oberoi_kolkata',
      provider_offer_id: 'liteapi_off_oberoi',
      provider: 'liteapi',
      hotel_name: 'Oberoi Grand Kolkata',
      price_inr: 22000,
      currency: 'INR',
    };
    const result = await service.prepareHotelBooking(
      'trip_btn_001',
      'ht_oberoi_kolkata',
      offer,
      { name: 'Karan Johar', email: 'karan@example.com' }
    );

    assert.ok(capturedUrl.includes('/api/hotels/prebook'), `Expected /api/hotels/prebook, got: ${capturedUrl}`);
    assert.strictEqual(capturedBody.trip_id, 'trip_btn_001');
    assert.strictEqual(capturedBody.hotel_id, 'ht_oberoi_kolkata');
    assert.strictEqual(capturedBody.snapshot_id, 'snap_oberoi_kolkata');
    assert.strictEqual(capturedBody.guest_name, 'Karan Johar');
    assert.strictEqual(capturedBody.guest_email, 'karan@example.com');
    assert.strictEqual(result.prebook_id, 'pb_btn_test_001');
    assert.strictEqual(service.hasActiveHotelPrebook('trip_btn_001'), true);

    console.log('✔ Test 1: Prepare hotel reservation button invokes prebook API with complete parameters and caches state.');
  }

  // --------------------------------------------------------------------------
  // Test 2: Persisted prebook can be recovered after refresh from trip metadata
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    // Simulate fresh instance where memory & session storage were lost, but backend trip metadata has prebook
    const tripMeta = {
      hotel_prebook: {
        prebook_id: 'pb_meta_recovered_888',
        amount: 18500,
        currency: 'INR',
        status: 'prebooked',
        created_at: new Date().toISOString(),
      },
      selected_hotel: {
        id: 'ht_meta_rec',
        hotel_name: 'The Heritage Palace',
        price_inr: 18500,
      },
    };

    const hasPrebook = service.hasActiveHotelPrebook('trip_rec_001', tripMeta);
    assert.strictEqual(hasPrebook, true, 'hasActiveHotelPrebook must return true when trip metadata contains valid prebook');

    // Rehydrate into service
    service.setPrebookData('trip_rec_001', tripMeta.hotel_prebook, tripMeta.selected_hotel as any);
    assert.strictEqual(service.hasActiveHotelPrebook('trip_rec_001'), true);

    console.log('✔ Test 2: Prebook state is successfully recovered after refresh via trip metadata.');
  }

  // --------------------------------------------------------------------------
  // Test 3: Missing or invalid prebook prevents payment order creation
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    let orderCalled = false;
    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotel-payments/create-order')) {
        orderCalled = true;
        return new Response(JSON.stringify({ success: true }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    let threw = false;
    try {
      await service.initiateHotelPaymentOrder(
        'trip_no_prebook',
        { first_name: 'Ananya', last_name: 'Pandey', email: 'ananya@example.com', phone: '+919999999999' },
        'rev_1',
        1
      );
    } catch (err: any) {
      threw = true;
      assert.ok(
        err.message.includes('prebook') || err.message.includes('missing'),
        `Expected prebook missing error, got: ${err.message}`
      );
    }

    assert.strictEqual(threw, true, 'initiateHotelPaymentOrder must throw error when prebook is missing');
    assert.strictEqual(orderCalled, false, 'create-order must NOT be invoked when prebook is missing');

    console.log('✔ Test 3: Missing or invalid prebook prevents payment order creation.');
  }

  // --------------------------------------------------------------------------
  // Test 4: Valid prebook permits payment order creation with authoritative backend contract
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    let orderBody: any = null;
    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/hotel-payments/create-order')) {
        orderBody = init?.body ? JSON.parse(String(init.body)) : null;
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_order_valid_001',
            razorpay_order_id: 'order_rzp_valid_001',
            amount_inr: 35000,
            currency: 'INR',
            key_id: 'rzp_test_key',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    service.setPrebookData(
      'trip_order_valid',
      { prebook_id: 'pb_order_valid_001', amount: 35000, currency: 'INR', status: 'prebooked' },
      { id: 'ht_valid', hotel_name: 'Valid Grand' } as any
    );

    const orderMeta = await service.initiateHotelPaymentOrder(
      'trip_order_valid',
      { first_name: 'Ranbir', last_name: 'Kapoor', email: 'ranbir@example.com', phone: '+919876543210' },
      'rev_order_valid',
      1
    );

    assert.strictEqual(orderMeta.transaction_id, 'tx_order_valid_001');
    assert.strictEqual(orderMeta.razorpay_order_id, 'order_rzp_valid_001');
    assert.strictEqual(orderBody.trip_id, 'trip_order_valid');
    assert.strictEqual(orderBody.prebook.prebook_id, 'pb_order_valid_001');
    assert.strictEqual(orderBody.holder.firstName, 'Ranbir');
    assert.strictEqual(orderBody.holder.lastName, 'Kapoor');

    console.log('✔ Test 4: Valid prebook permits payment order initiation with authoritative contract.');
  }

  // --------------------------------------------------------------------------
  // Test 5: Backend payment verification remains strictly mandatory
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    let verifyCalled = false;
    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        verifyCalled = true;
        return new Response(
          JSON.stringify({
            success: false,
            error: 'Signature mismatch',
            status: 'failed',
          }),
          { status: 400, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    service.setPrebookData('trip_v_01', { prebook_id: 'pb_v_01' }, { id: 'ht_v' } as any);

    let threw = false;
    try {
      await service.processHotelPayment(
        'trip_v_01',
        'card',
        { first_name: 'Alia', last_name: 'Bhatt', email: 'alia@example.com', phone: '+919876543211' },
        'rev_v_01',
        1,
        {
          transaction_id: 'tx_v_01',
          razorpay_order_id: 'ord_v_01',
          razorpay_payment_id: 'pay_v_01',
          razorpay_signature: 'sig_invalid_test',
        }
      );
    } catch (err) {
      threw = true;
    }

    assert.strictEqual(verifyCalled, true, 'Payment verification endpoint must be called');
    assert.strictEqual(threw, true, 'Failed backend verification must fail closed and never report booked');

    console.log('✔ Test 5: Backend payment verification remains strictly mandatory.');
  }

  // --------------------------------------------------------------------------
  // Test 6: Static code audit of HotelBookingPanel.tsx
  // --------------------------------------------------------------------------
  {
    const panelPath = path.resolve(__dirname, '../components/booking/HotelBookingPanel.tsx');
    assert.ok(fs.existsSync(panelPath), 'HotelBookingPanel.tsx must exist');
    const panelSrc = fs.readFileSync(panelPath, 'utf8');

    // 1. Prepare error banner and dismiss handler
    assert.ok(panelSrc.includes('prepareError'), 'Must declare prepareError state');
    assert.ok(panelSrc.includes('role="alert"'), 'Must render visible alert banner for errors');

    // 2. Disabled guard when hotel is not selected
    assert.ok(panelSrc.includes('!hotel'), 'Must disable Prepare reservation button when hotel is missing');
    assert.ok(panelSrc.includes('Select a stay first'), 'Must display helpful label when hotel is not selected');

    // 3. Loading state and duplicate click prevention
    assert.ok(panelSrc.includes('isPreparing'), 'Must track isPreparing loading state');
    assert.ok(panelSrc.includes('isLoading={isPreparing}'), 'Must pass isLoading to button during request');

    // 4. Authorization awareness in payment_required state
    assert.ok(panelSrc.includes('isBookingAuthorized'), 'Must be aware of isBookingAuthorized');
    assert.ok(panelSrc.includes('onAuthorizeBooking'), 'Must expose onAuthorizeBooking callback');

    console.log('✔ Test 6: Static audit of HotelBookingPanel.tsx passes all interaction and error visibility checks.');
  }

  // --------------------------------------------------------------------------
  // Test 7: Static code audit of BookingWorkspace.tsx
  // --------------------------------------------------------------------------
  {
    const workspacePath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
    assert.ok(fs.existsSync(workspacePath), 'BookingWorkspace.tsx must exist');
    const wsSrc = fs.readFileSync(workspacePath, 'utf8');

    // 1. Hotel selection fallback to metadata
    assert.ok(
      wsSrc.includes('trip.selected_hotel_offer || (trip.metadata as any)?.selected_hotel'),
      'Must resolve selectedHotel from both top-level and trip.metadata'
    );

    // 2. Prebook recovery on sync / refresh
    assert.ok(
      wsSrc.includes('bookingService.hasActiveHotelPrebook(trip.trip_id, trip.metadata)'),
      'Must check and recover prebook state from trip.metadata on load'
    );

    // 3. Complete hotel payment loading state and duplicate submission lock
    assert.ok(wsSrc.includes('isCompletingPayment'), 'Must track isCompletingPayment state');
    assert.ok(wsSrc.includes('disabled={isCompletingPayment}'), 'Must disable Complete hotel payment button while in progress');

    // 4. Reconciliation-pending bypass
    assert.ok(
      wsSrc.includes("hotelState === 'reconciliation_pending'"),
      'Must check for reconciliation_pending state'
    );

    // 5. Traveler completion validation
    assert.ok(
      wsSrc.includes('!isAllTravelersComplete(travelers, travelersCount)') || wsSrc.includes('!lead || !lead.given_name'),
      'Must validate passenger completeness before prebook or payment'
    );

    // 6. No silent error swallowing in handleCompleteHotelPayment
    assert.ok(
      !wsSrc.includes('try { await handlePrepareHotel(); } catch { return; }'),
      'Must NOT silently swallow errors in handleCompleteHotelPayment'
    );

    console.log('✔ Test 7: Static audit of BookingWorkspace.tsx passes all contract, recovery, and guard checks.');
  }

  // Restore original fetch
  global.fetch = originalFetch;
  console.log('\nAll Hotel Reservation & Payment Buttons tests passed successfully!\n');
}

runTests().catch((err) => {
  console.error('\n❌ Test suite failed:', err);
  process.exit(1);
});
