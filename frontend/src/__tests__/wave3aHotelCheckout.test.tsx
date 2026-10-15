/**
 * Wave 3A Hotel Checkout & Prebook Contract Tests
 *
 * Tests:
 * 1. prebookHotel sends trip_id and returns authoritative amount, currency, and prebook_id.
 * 2. bookingService.processHotelPayment fails closed when paymentDetails are missing (no fake signatures).
 * 3. bookingService.processHotelPayment transmits genuine Razorpay payment confirmation fields to /verify-and-book.
 * 4. bookingService.initiateHotelPaymentOrder creates Razorpay order and returns metadata.
 * 5. Static audit: zero occurrences of fake signature templates (pay_${ or sig_${) in bookingService.ts.
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TrustTripBookingService } from '../services/bookingService';
import { api, apiClient } from '../lib/apiClient';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function runTests() {
  console.log('\nRunning Wave 3A Hotel Checkout & Prebook Tests...\n');
  const originalFetch = global.fetch;

  // --------------------------------------------------------------------------
  // Test 1: prebookHotel contract sends trip_id and returns authoritative amount
  // --------------------------------------------------------------------------
  {
    let capturedBody: any = null;
    global.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
      capturedBody = init?.body ? JSON.parse(String(init.body)) : null;
      return new Response(
        JSON.stringify({
          success: true,
          prebook_id: 'pb_w3a_auth_100',
          amount: 14500,
          currency: 'INR',
          status: 'prebooked',
          hotel_name: 'The Authoritative Hotel',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await api.prebookHotel({
      trip_id: 'trip_w3a_001',
      snapshot_id: 'snap_w3a_001',
      hotel_id: 'ht_w3a_001',
      guest_name: 'Aditi Rao',
      guest_email: 'aditi@example.com',
    });

    assert.strictEqual(capturedBody.trip_id, 'trip_w3a_001');
    assert.strictEqual(capturedBody.snapshot_id, 'snap_w3a_001');
    assert.strictEqual(res.prebook_id, 'pb_w3a_auth_100');
    assert.strictEqual(res.amount, 14500);
    assert.strictEqual(res.currency, 'INR');
    assert.strictEqual(res.status, 'prebooked');
    console.log('✔ Test 1: prebookHotel sends trip_id and receives authoritative amount, currency, and prebook_id.');
  }

  // --------------------------------------------------------------------------
  // Test 2: processHotelPayment refuses to fabricate synthetic signatures
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_w3a_2', amount: 10000, currency: 'INR' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({ success: true, transaction_id: 'tx_w3a_2', razorpay_order_id: 'ord_w3a_2' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_w3a_2', 'ht_w3a_2');

    let threw = false;
    try {
      await service.processHotelPayment(
        'trip_w3a_2',
        'card',
        { first_name: 'Rohan', last_name: 'Verma', email: 'rohan@example.com', phone: '+919876543210' },
        'rev_w3a_2',
        1
        // paymentDetails omitted
      );
    } catch (err: any) {
      threw = true;
      assert.ok(
        err.message.includes('Razorpay payment confirmation') || err.message.includes('Synthetic payment signatures are strictly prohibited'),
        `Expected signature error, got: ${err.message}`
      );
    }

    assert.strictEqual(threw, true, 'processHotelPayment must fail closed when payment confirmation is missing');
    console.log('✔ Test 2: processHotelPayment fails closed without genuine paymentDetails (no fake signatures).');
  }

  // --------------------------------------------------------------------------
  // Test 3: processHotelPayment sends genuine Razorpay callback values
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    let capturedVerifyBody: any = null;

    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_w3a_3', amount: 12000, currency: 'INR', hotel_name: 'Grand Hyatt' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_w3a_3',
            razorpay_order_id: 'order_rzp_real_999',
            amount_inr: 12000,
            currency: 'INR',
            key_id: 'rzp_live_key_3',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/verify-and-book')) {
        capturedVerifyBody = init?.body ? JSON.parse(String(init.body)) : null;
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_w3a_3',
            payment_captured: true,
            booking: {
              confirmation_code: 'CONF-HYATT-333',
              hotel_name: 'Grand Hyatt',
              status: 'confirmed',
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_w3a_3', 'ht_w3a_3');
    const result = await service.processHotelPayment(
      'trip_w3a_3',
      'card',
      { first_name: 'Karan', last_name: 'Mehra', email: 'karan@example.com', phone: '+919988776655' },
      'rev_w3a_3',
      1,
      {
        razorpay_order_id: 'order_rzp_real_999',
        razorpay_payment_id: 'pay_genuine_rzp_888',
        razorpay_signature: 'sig_genuine_hmac_sha256_777',
      }
    );

    assert.ok(capturedVerifyBody, 'verify-and-book must have been called');
    assert.strictEqual(capturedVerifyBody.razorpay_order_id, 'order_rzp_real_999');
    assert.strictEqual(capturedVerifyBody.razorpay_payment_id, 'pay_genuine_rzp_888');
    assert.strictEqual(capturedVerifyBody.razorpay_signature, 'sig_genuine_hmac_sha256_777');
    assert.strictEqual(result.confirmation_code, 'CONF-HYATT-333');
    assert.strictEqual(result.status, 'voucher_issued');
    console.log('✔ Test 3: processHotelPayment propagates genuine Razorpay credentials directly to /verify-and-book.');
  }

  // --------------------------------------------------------------------------
  // Test 4: initiateHotelPaymentOrder returns order metadata
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    global.fetch = (async (url: string | URL | Request) => {
      const u = String(url);
      if (u.includes('/api/hotels/prebook')) {
        return new Response(
          JSON.stringify({ prebook_id: 'pb_w3a_4', amount: 20000, currency: 'INR', hotel_name: 'Taj Palace' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (u.includes('/api/hotel-payments/create-order')) {
        return new Response(
          JSON.stringify({
            success: true,
            transaction_id: 'tx_w3a_4',
            razorpay_order_id: 'order_rzp_order_444',
            amount_inr: 20000,
            currency: 'INR',
            key_id: 'rzp_test_key_444',
            hotel_name: 'Taj Palace',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 404 });
    }) as any;

    await service.prepareHotelBooking('trip_w3a_4', 'ht_w3a_4');
    const order = await service.initiateHotelPaymentOrder(
      'trip_w3a_4',
      { first_name: 'Sunita', last_name: 'Patel', email: 'sunita@example.com', phone: '+919123456780' },
      'rev_w3a_4',
      2
    );

    assert.strictEqual(order.transaction_id, 'tx_w3a_4');
    assert.strictEqual(order.razorpay_order_id, 'order_rzp_order_444');
    assert.strictEqual(order.key_id, 'rzp_test_key_444');
    assert.strictEqual(order.amount_inr, 20000);
    assert.strictEqual(order.currency, 'INR');
    console.log('✔ Test 4: initiateHotelPaymentOrder creates Razorpay order and returns required metadata.');
  }

  // --------------------------------------------------------------------------
  // Test 5: Static audit — Zero fake payment signature templates in bookingService.ts
  // --------------------------------------------------------------------------
  {
    const servicePath = path.resolve(__dirname, '../services/bookingService.ts');
    const serviceCode = fs.readFileSync(servicePath, 'utf8');

    assert.ok(
      !serviceCode.includes('`pay_${'),
      'CRITICAL AUDIT: bookingService.ts must not contain `pay_${` template literal'
    );
    assert.ok(
      !serviceCode.includes('`sig_${'),
      'CRITICAL AUDIT: bookingService.ts must not contain `sig_${` template literal'
    );
    assert.ok(
      !serviceCode.includes('"pay_" +'),
      'CRITICAL AUDIT: bookingService.ts must not concatenate "pay_" +'
    );
    assert.ok(
      !serviceCode.includes('"sig_" +'),
      'CRITICAL AUDIT: bookingService.ts must not concatenate "sig_" +'
    );
    console.log('✔ Test 5: Static audit verified zero fake signature generators in bookingService.ts.');
  }

  // Restore fetch
  global.fetch = originalFetch;
  console.log('\nAll Wave 3A Hotel Checkout & Prebook Tests Passed Successfully!\n');
}

runTests().catch((err) => {
  console.error('Wave 3A Hotel Checkout test failed:', err);
  process.exit(1);
});
