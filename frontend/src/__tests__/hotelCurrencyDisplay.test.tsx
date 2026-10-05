/**
 * Hotel Currency Truthfulness Regression Tests
 *
 * Verifies:
 * 1. Foreign-currency hotel displays provider amount + INR reference in HotelCard.
 * 2. INR hotel displays INR normally in HotelCard without duplicate estimate.
 * 3. PaymentReviewModal does NOT synthesize client-side 10% tax.
 * 4. PaymentReviewModal displays authoritative INR payable amount and FX disclosure for foreign currencies.
 * 5. PaymentReviewModal displays clean INR pricing for INR-native hotels.
 * 6. bookingService eliminates 1:1 USD->INR fallback and fails closed when authoritative INR price is missing.
 * 7. bookingService transmits authoritative INR price when present.
 */

import assert from 'node:assert';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { HotelCard } from '../components/travel/HotelCard';
import { PaymentReviewModal } from '../components/booking/PaymentReviewModal';
import { TrustTripBookingService } from '../services/bookingService';
import { HotelOffer } from '../types/hotel';

async function runTests() {
  console.log('Running Hotel Currency Truthfulness Regression Tests...\n');

  // --------------------------------------------------------------------------
  // Test 1: Foreign-currency hotel displays provider amount + INR reference
  // --------------------------------------------------------------------------
  {
    const usdHotel: HotelOffer = {
      id: 'h_usd_1',
      name: 'Grand Hyatt Tokyo',
      address: 'Roppongi Hills',
      city: 'Tokyo',
      total_amount: 225,
      price: 225,
      currency: 'USD',
      price_inr: 18749,
      exchange_rate: 83.33,
      nights: 2,
      nightly_price: 112.5,
      rooms: [],
    };

    const html = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel: usdHotel, nightsCount: 2 })
    );

    assert(html.includes('$225'), 'FAILED: USD hotel must render provider total $225');
    assert(html.includes('≈') && html.includes('₹18,749'), 'FAILED: USD hotel must display dual-currency reference ≈ ₹18,749');
    console.log('✔ Test 1: Foreign-currency hotel displays provider amount ($225) + INR reference (≈ ₹18,749).');
  }

  // --------------------------------------------------------------------------
  // Test 2: INR hotel displays INR normally without duplicate estimate
  // --------------------------------------------------------------------------
  {
    const inrHotel: HotelOffer = {
      id: 'h_inr_1',
      name: 'Taj Mahal Palace',
      address: 'Apollo Bunder',
      city: 'Mumbai',
      total_amount: 18750,
      price: 18750,
      currency: 'INR',
      price_inr: 18750,
      nights: 1,
      nightly_price: 18750,
      rooms: [],
    };

    const html = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel: inrHotel, nightsCount: 1 })
    );

    assert(html.includes('₹18,750'), 'FAILED: INR hotel must render ₹18,750');
    assert(!html.includes('≈'), 'FAILED: INR hotel must NOT render duplicate dual-currency estimate (≈)');
    console.log('✔ Test 2: INR hotel displays INR normally without unnecessary dual-currency estimate.');
  }

  // --------------------------------------------------------------------------
  // Test 3: PaymentReviewModal does NOT synthesize 10% tax and communicates INR
  // --------------------------------------------------------------------------
  {
    const html = renderToStaticMarkup(
      React.createElement(PaymentReviewModal, {
        isOpen: true,
        onClose: () => {},
        hotelName: 'Grand Hyatt Tokyo',
        roomName: 'Deluxe King',
        startDate: '2026-11-01',
        endDate: '2026-11-03',
        guestsCount: 2,
        roomAmount: 225,
        currency: 'USD',
        priceInr: 18749,
        exchangeRate: 83.33,
        contact: { first_name: 'John', last_name: 'Doe', email: 'john@example.com', phone: '+1234567890' },
        onProcessPayment: async () => {},
      })
    );

    // Verify 10% tax removal
    assert(!html.includes('Taxes &amp; Tourism Fees') && !html.includes('Taxes & Tourism Fees'), 'FAILED: PaymentReviewModal must NOT synthesize 10% taxes line');
    assert(!html.includes('$248') && !html.includes('247.5'), 'FAILED: PaymentReviewModal must NOT display synthetic $248 total');

    // Verify truthful pricing
    assert(html.includes('Hotel price:') && html.includes('$225'), 'FAILED: Hotel price must show provider amount $225');
    assert(html.includes('FX rate:') && html.includes('83.33'), 'FAILED: FX rate line must display applied rate 83.33');
    assert(html.includes('Amount to pay:') && html.includes('₹18,749'), 'FAILED: Amount to pay must show authoritative ₹18,749 INR');
    assert(html.includes('Payment processed in INR through Razorpay.'), 'FAILED: Must inform user of INR processing via Razorpay');
    assert(html.includes('Confirm &amp; Pay (₹18,749)') || html.includes('Confirm & Pay (₹18,749)'), 'FAILED: Submit button must state payable ₹18,749, not $225 or $248');

    console.log('✔ Test 3: PaymentReviewModal eliminates synthetic 10% tax and truthful USD->INR breakdown rendered.');
  }

  // --------------------------------------------------------------------------
  // Test 4: PaymentReviewModal for INR-native hotel is clean
  // --------------------------------------------------------------------------
  {
    const html = renderToStaticMarkup(
      React.createElement(PaymentReviewModal, {
        isOpen: true,
        onClose: () => {},
        hotelName: 'Taj Mahal Palace',
        roomName: 'Luxury Room',
        startDate: '2026-11-01',
        endDate: '2026-11-02',
        guestsCount: 1,
        roomAmount: 18750,
        currency: 'INR',
        priceInr: 18750,
        contact: { first_name: 'Rahul', last_name: 'Sharma', email: 'rahul@example.com', phone: '+919876543210' },
        onProcessPayment: async () => {},
      })
    );

    assert(html.includes('Hotel price:') && html.includes('₹18,750'), 'FAILED: Hotel price must show ₹18,750');
    assert(html.includes('Amount to pay:') && html.includes('₹18,750'), 'FAILED: Amount to pay must show ₹18,750');
    assert(!html.includes('FX rate:'), 'FAILED: INR hotel must NOT show FX rate line');
    assert(!html.includes('Payment processed in INR through Razorpay.'), 'FAILED: INR hotel does not require conversion disclosure');
    assert(html.includes('Confirm &amp; Pay (₹18,750)') || html.includes('Confirm & Pay (₹18,750)'), 'FAILED: Button must display ₹18,750');

    console.log('✔ Test 4: PaymentReviewModal for INR hotel renders cleanly in INR.');
  }

  // --------------------------------------------------------------------------
  // Test 5: No 1 USD = 1 INR fallback (Fails closed when authoritative INR missing)
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    const tripId = 'trip_usd_missing_inr';

    // Simulate prebook for USD hotel without cached offer / price_inr
    (service as any).activePrebookIds.set(tripId, 'pb_usd_99');
    (service as any).activePrebookData.set(tripId, {
      prebook_id: 'pb_usd_99',
      hotel_id: 'h_usd_99',
      amount: 225,
      currency: 'USD',
      status: 'confirmed',
    });

    let threw = false;
    try {
      await service.initiateHotelPaymentOrder(
        tripId,
        { first_name: 'Alice', last_name: 'Smith', email: 'alice@example.com', phone: '+1234567890' },
        'rev_1',
        1
      );
    } catch (err: any) {
      threw = true;
      assert.strictEqual(err.code, 'MISSING_AUTHORITATIVE_INR_PRICE', 'FAILED: Must fail closed with MISSING_AUTHORITATIVE_INR_PRICE');
      assert(err.message.includes('Authoritative INR payment amount is missing'), 'FAILED: Error message must explain missing authoritative INR');
    }

    assert(threw, 'FAILED: initiateHotelPaymentOrder must fail closed rather than treating $225 as ₹225');
    console.log('✔ Test 5: initiateHotelPaymentOrder fails closed when authoritative INR price is missing for USD hotel (no 1:1 fallback).');
  }

  // --------------------------------------------------------------------------
  // Test 6: Standalone processHotelPayment fails closed when authoritative INR missing
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    const tripId = 'trip_usd_process_fail';

    (service as any).activePrebookIds.set(tripId, 'pb_usd_100');
    (service as any).activePrebookData.set(tripId, {
      prebook_id: 'pb_usd_100',
      hotel_id: 'h_usd_100',
      amount: 350,
      currency: 'EUR',
      status: 'confirmed',
    });

    let threw = false;
    try {
      await service.processHotelPayment(
        tripId,
        'card',
        { first_name: 'Bob', last_name: 'Vance', email: 'bob@example.com', phone: '+1234567890' },
        'rev_1',
        1
      );
    } catch (err: any) {
      threw = true;
      assert.strictEqual(err.code, 'MISSING_AUTHORITATIVE_INR_PRICE');
    }

    assert(threw, 'FAILED: processHotelPayment must fail closed when authoritative INR price is missing for EUR hotel');
    console.log('✔ Test 6: processHotelPayment fails closed when authoritative INR price is missing for foreign currency hotel.');
  }

  // --------------------------------------------------------------------------
  // Test 7: Authoritative INR price is correctly propagated when present
  // --------------------------------------------------------------------------
  {
    const service = new TrustTripBookingService();
    const tripId = 'trip_usd_valid_auth';

    (service as any).activePrebookIds.set(tripId, 'pb_usd_200');
    (service as any).activePrebookData.set(tripId, {
      prebook_id: 'pb_usd_200',
      hotel_id: 'h_usd_200',
      amount: 225,
      currency: 'USD',
      status: 'confirmed',
    });
    (service as any).activeHotelOffers.set(tripId, {
      id: 'h_usd_200',
      provider: 'liteapi',
      price: 225,
      currency: 'USD',
      price_inr: 18749,
      hotel_name: 'Tokyo Tower Hotel',
    });

    let payloadSent: any = null;
    let endpointCalled: string = '';
    const originalFetch = global.fetch;
    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      endpointCalled = String(url);
      payloadSent = JSON.parse(init?.body as string);
      return new Response(
        JSON.stringify({
          success: true,
          transaction_id: 'tx_auth_123',
          razorpay_order_id: 'order_auth_123',
          amount_inr: 18749,
          currency: 'INR',
          key_id: 'rzp_test_key',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    try {
      const res = await service.initiateHotelPaymentOrder(
        tripId,
        { first_name: 'Carol', last_name: 'Danvers', email: 'carol@example.com', phone: '+1234567890' },
        'rev_1',
        1
      );

      assert.strictEqual(res.amount_inr, 18749, 'FAILED: Returned amount_inr must be 18749');
      assert(endpointCalled.includes('/api/hotel-payments/create-order'), 'FAILED: Must call create-order');
      assert.strictEqual(payloadSent?.offer?.price_inr, 18749, 'FAILED: Transmitted offer.price_inr must be 18749');
      assert.strictEqual(payloadSent?.offer?.price, 225, 'FAILED: Transmitted offer.price must be 225');
      assert.strictEqual(payloadSent?.offer?.currency, 'USD', 'FAILED: Transmitted offer.currency must be USD');
      console.log('✔ Test 7: initiateHotelPaymentOrder propagates authoritative price_inr (18749) rather than provider price (225).');
    } finally {
      global.fetch = originalFetch;
    }
  }

  console.log('\nAll Hotel Currency Truthfulness Regression Tests PASSED!\n');
}

runTests().catch((err) => {
  console.error('Test run failed:', err);
  process.exit(1);
});
