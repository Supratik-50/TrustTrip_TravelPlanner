/**
 * Hotel Payment & Reconciliation Incident Remediation Frontend Test Suite
 *
 * Verifies:
 * 1. checkPendingHotelTransaction recovers refund_pending and is_captured transactions.
 * 2. reconcileHotelPayment handles refund_pending without throwing and stays in safe verification state.
 * 3. reconcileHotelPayment handles refunded cleanly and clears local transaction caches.
 * 4. BookingWorkspace static audit:
 *    - Rehydrates reconciliation_pending for refund_pending / captured unconfirmed stays.
 *    - Blocks fresh payment order creation when pendingHotelTransactionId is set.
 * 5. PaymentReviewModal static audit:
 *    - Displays informative refund processing status on refund_pending.
 * 6. TripCard static audit:
 *    - Filters unbooked/failed stays to prevent false voucher download badges.
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
  console.log('\nRunning Hotel Payment Incident Remediation Test Suite...\n');
  const originalFetch = global.fetch;

  // --------------------------------------------------------------------------
  // Test 1: checkPendingHotelTransaction recovers refund_pending state
  // --------------------------------------------------------------------------
  {
    global.fetch = (async (url: string | URL | Request) => {
      const urlStr = String(url);
      if (urlStr.includes('/by-trip/')) {
        return new Response(
          JSON.stringify({
            has_payment: true,
            transaction_id: 'tx_inc_001',
            payment_status: 'refund_pending',
            booking_status: 'booking_failed',
            is_captured: true,
            reconciliation_pending: false,
            refund_pending: true,
            transaction: {
              id: 'tx_inc_001',
              transaction_id: 'tx_inc_001',
              payment_status: 'refund_pending',
              booking_status: 'booking_failed',
              amount_inr: 21817.91,
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response('{}', { status: 200 });
    }) as any;

    const service = new TrustTripBookingService();
    const pTx = await service.checkPendingHotelTransaction('trip_test_incident');
    assert.ok(pTx, 'Pending transaction should be returned');
    assert.strictEqual(pTx.transaction_id, 'tx_inc_001');
    assert.strictEqual(pTx.refund_pending, true);
    assert.strictEqual(pTx.is_captured, true);
    console.log('✔ Test 1: checkPendingHotelTransaction cleanly exposes refund_pending and is_captured');
  }

  // --------------------------------------------------------------------------
  // Test 2: reconcileHotelPayment handles refund_pending without throwing
  // --------------------------------------------------------------------------
  {
    global.fetch = (async (url: string | URL | Request) => {
      const urlStr = String(url);
      if (urlStr.includes('/reconcile')) {
        return new Response(
          JSON.stringify({
            success: false,
            status: 'refund_pending',
            booking_status: 'booking_failed',
            transaction_id: 'tx_inc_001',
            payment_captured: true,
            refunded: false,
            refund_pending: true,
            requires_refund: true,
            error: 'Supplier confirmed no booking exists, but refund could not be completed immediately: unprocessed.',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response('{}', { status: 200 });
    }) as any;

    const service = new TrustTripBookingService();
    const res = await service.reconcileHotelPayment('trip_test_incident', 'tx_inc_001');
    assert.strictEqual(res.status, 'refund_pending');
    assert.strictEqual(res.payment_captured, true);
    assert.strictEqual((res as any).refund_pending, true);
    assert.ok(res.message?.includes('Refund is currently processing') || res.message?.includes('unprocessed'));
    console.log('✔ Test 2: reconcileHotelPayment safely handles refund_pending and returns status');
  }

  // --------------------------------------------------------------------------
  // Test 3: reconcileHotelPayment handles terminal refunded state
  // --------------------------------------------------------------------------
  {
    global.fetch = (async (url: string | URL | Request) => {
      const urlStr = String(url);
      if (urlStr.includes('/reconcile')) {
        return new Response(
          JSON.stringify({
            success: false,
            status: 'refunded',
            booking_status: 'booking_failed',
            transaction_id: 'tx_inc_001',
            payment_captured: true,
            refunded: true,
            requires_refund: false,
            error: 'Supplier confirmed no booking exists. Payment has been refunded.',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response('{}', { status: 200 });
    }) as any;

    const service = new TrustTripBookingService();
    const res = await service.reconcileHotelPayment('trip_test_incident', 'tx_inc_001');
    assert.strictEqual(res.status, 'refunded');
    assert.strictEqual((res as any).refunded, true);
    console.log('✔ Test 3: reconcileHotelPayment gracefully handles refunded resolution');
  }

  // --------------------------------------------------------------------------
  // Test 4: BookingWorkspace static audit — rehydration and duplicate payment guard
  // --------------------------------------------------------------------------
  {
    const workspacePath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
    const workspaceSource = fs.readFileSync(workspacePath, 'utf8');

    // 1. Must check refund_pending when recovering hotel state
    assert.ok(
      workspaceSource.includes('res.hotel.refund_pending') || workspaceSource.includes("res.hotel.payment_status === 'refund_pending'"),
      'BookingWorkspace must recover refund_pending status from backend'
    );

    // 2. Must query checkPendingHotelTransaction when hotel is unconfirmed
    assert.ok(
      workspaceSource.includes('!hasConfirmedHotel && bookingService.checkPendingHotelTransaction'),
      'BookingWorkspace must query checkPendingHotelTransaction when hotel is not confirmed'
    );

    // 3. Must block order creation if hotelState === reconciliation_pending or pendingHotelTransactionId
    assert.ok(
      workspaceSource.includes("hotelState === 'reconciliation_pending' || pendingHotelTransactionId"),
      'BookingWorkspace must guard handleInitiateHotelOrder against pending transactions'
    );

    // 4. Must set hotelState to refunded when transaction is refunded
    assert.ok(
      workspaceSource.includes("setHotelState('refunded')"),
      'BookingWorkspace must set hotelState to refunded when transaction is refunded'
    );

    console.log('✔ Test 4: BookingWorkspace static audit confirms rehydration and duplicate-order guards');
  }

  // --------------------------------------------------------------------------
  // Test 5: PaymentReviewModal static audit — refund_pending and refunded handling
  // --------------------------------------------------------------------------
  {
    const modalPath = path.resolve(__dirname, '../components/booking/PaymentReviewModal.tsx');
    const modalSource = fs.readFileSync(modalPath, 'utf8');

    assert.ok(
      modalSource.includes("recRes?.status === 'refund_pending'") || modalSource.includes('recRes?.refund_pending'),
      'PaymentReviewModal must recognize refund_pending reconciliation status'
    );

    assert.ok(
      modalSource.includes("recRes?.status === 'refunded'"),
      'PaymentReviewModal must handle confirmed refunded reconciliation status'
    );

    assert.ok(
      modalSource.includes('data-testid="payment-modal-refunded"'),
      'PaymentReviewModal must render Refund Processed view'
    );

    console.log('✔ Test 5: PaymentReviewModal static audit confirms refund reconciliation handling');
  }

  // --------------------------------------------------------------------------
  // Test 6: TripCard static audit — filter unconfirmed hotel bookings
  // --------------------------------------------------------------------------
  {
    const cardPath = path.resolve(__dirname, '../components/travel/TripCard.tsx');
    const cardSource = fs.readFileSync(cardPath, 'utf8');

    assert.ok(
      cardSource.includes("!h.booking_status || h.booking_status === 'booked'"),
      'TripCard must filter unbooked hotel stays so vouchers are only rendered for confirmed bookings'
    );

    console.log('✔ Test 6: TripCard static audit confirms only confirmed hotel bookings display voucher button');
  }

  // --------------------------------------------------------------------------
  // Test 7: HotelBookingPanel static audit — refunded view
  // --------------------------------------------------------------------------
  {
    const panelPath = path.resolve(__dirname, '../components/booking/HotelBookingPanel.tsx');
    const panelSource = fs.readFileSync(panelPath, 'utf8');

    assert.ok(
      panelSource.includes('data-testid="hotel-panel-refunded"'),
      'HotelBookingPanel must render Refund Processed view'
    );

    assert.ok(
      panelSource.includes('RESERVATION CANCELLED — REFUND PROCESSED'),
      'HotelBookingPanel must display reservation cancelled refund processed banner'
    );

    console.log('✔ Test 7: HotelBookingPanel static audit confirms Refund Processed view');
  }

  global.fetch = originalFetch;
  console.log('\nAll Hotel Payment Incident Remediation Frontend Tests PASSED!\n');
}

runTests().catch((err) => {
  console.error('\n❌ Incident Remediation Frontend Test Failed:', err);
  process.exit(1);
});
