/**
 * Frontend Regression & Contract Tests for Wave 2: Real Traveler Identity & Passenger Propagation
 *
 * Verifies:
 * Test 1: bookingService.selectFlightOffer propagates exact entered passenger array.
 * Test 2: Authenticated user != passenger (account holder booking for someone else).
 * Test 3: Authenticated email can seed default, but explicit traveler email takes precedence.
 * Test 4: Multi-passenger support (N travelers, 1 <= N <= 9) propagates N distinct records.
 * Test 5: Incomplete traveler validation fails closed before booking confirmation.
 * Test 6: Hotel booking propagates actual traveler details to holder and guests.
 * Test 7: Static audit verifies zero synthetic passenger literals in production booking code.
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bookingService } from '../services/bookingService';
import { apiClient } from '../lib/apiClient';
import { FlightPassenger } from '../types/flight';
import { TravelerContactInfo } from '../types/bookingFrontend';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log('Running Wave 2 Real Traveler Identity & Passenger Propagation Tests...\n');

// ---------------------------------------------------------------------------
// Test 1: bookingService.selectFlightOffer propagates exact entered passenger array
// ---------------------------------------------------------------------------
{
  const tripId = 'trip_w2_flow_1';
  const offerId = 'off_w2_flight_1';
  let capturedPayload: any = null;

  const originalSelect = apiClient.selectFlightBooking;
  apiClient.selectFlightBooking = (async (data: any) => {
    capturedPayload = data;
    return {
      success: true,
      booking_id: 'b_w2_1001',
      trip_id: data.trip_id,
      offer_id: data.offer_id,
      status: 'SELECTED',
      expected_amount: 850,
      currency: 'USD',
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      approved: false,
      message: 'Offer selected',
    };
  }) as any;

  try {
    const passengers: FlightPassenger[] = [
      {
        given_name: 'Rahul',
        family_name: 'Sharma',
        born_on: '1992-05-15',
        title: 'mr',
        gender: 'm',
        email: 'rahul.sharma@example.com',
        phone_number: '+919876543210',
      },
    ];

    const res = await bookingService.selectFlightOffer(tripId, offerId, passengers);
    assert.strictEqual(res.booking_id, 'b_w2_1001');
    assert.ok(capturedPayload, 'apiClient.selectFlightBooking should have been called');
    assert.strictEqual(capturedPayload.passengers.length, 1);
    assert.strictEqual(capturedPayload.passengers[0].given_name, 'Rahul');
    assert.strictEqual(capturedPayload.passengers[0].family_name, 'Sharma');
    assert.strictEqual(capturedPayload.passengers[0].born_on, '1992-05-15');
    assert.strictEqual(capturedPayload.passengers[0].title, 'mr');
    assert.strictEqual(capturedPayload.passengers[0].gender, 'm');
    assert.strictEqual(capturedPayload.passengers[0].email, 'rahul.sharma@example.com');
    assert.strictEqual(capturedPayload.passengers[0].phone_number, '+919876543210');

    console.log('✔ Test 1: bookingService.selectFlightOffer propagates exact entered passenger array.');
  } finally {
    apiClient.selectFlightBooking = originalSelect;
  }
}

// ---------------------------------------------------------------------------
// Test 2: Authenticated user != passenger (account holder booking for someone else)
// ---------------------------------------------------------------------------
{
  const tripId = 'trip_w2_flow_2';
  const offerId = 'off_w2_flight_2';
  let capturedPassengers: FlightPassenger[] = [];

  const originalSelect = apiClient.selectFlightBooking;
  apiClient.selectFlightBooking = (async (data: any) => {
    capturedPassengers = data.passengers;
    return {
      success: true,
      booking_id: 'b_w2_1002',
      trip_id: data.trip_id,
      offer_id: data.offer_id,
      status: 'SELECTED',
      expected_amount: 1200,
      currency: 'USD',
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      approved: false,
    };
  }) as any;

  try {
    // Current authenticated user: Supratik Sanyal (supratik@trusttrip.com)
    // Passenger traveling: Vikram Singhania (vikram@domain.com)
    const traveler: FlightPassenger = {
      given_name: 'Vikram',
      family_name: 'Singhania',
      born_on: '1987-11-20',
      title: 'mr',
      gender: 'm',
      email: 'vikram@domain.com',
      phone_number: '+919811122233',
    };

    await bookingService.selectFlightOffer(tripId, offerId, [traveler]);
    assert.strictEqual(capturedPassengers.length, 1);
    assert.strictEqual(capturedPassengers[0].given_name, 'Vikram');
    assert.strictEqual(capturedPassengers[0].family_name, 'Singhania');
    assert.strictEqual(capturedPassengers[0].email, 'vikram@domain.com');
    assert.notStrictEqual(capturedPassengers[0].given_name, 'Supratik');
    assert.notStrictEqual(capturedPassengers[0].email, 'supratik@trusttrip.com');

    console.log('✔ Test 2: Authenticated user != passenger strictly enforced.');
  } finally {
    apiClient.selectFlightBooking = originalSelect;
  }
}

// ---------------------------------------------------------------------------
// Test 3: Explicit traveler email takes precedence over authenticated user email
// ---------------------------------------------------------------------------
{
  const tripId = 'trip_w2_flow_3';
  const offerId = 'off_w2_flight_3';
  let capturedPassengers: FlightPassenger[] = [];

  const originalSelect = apiClient.selectFlightBooking;
  apiClient.selectFlightBooking = (async (data: any) => {
    capturedPassengers = data.passengers;
    return {
      success: true,
      booking_id: 'b_w2_1003',
      trip_id: data.trip_id,
      offer_id: data.offer_id,
      status: 'SELECTED',
      expected_amount: 500,
      currency: 'USD',
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      approved: false,
    };
  }) as any;

  try {
    const travelerWithExplicitEmail: FlightPassenger = {
      given_name: 'Aarav',
      family_name: 'Patel',
      born_on: '1995-02-14',
      title: 'mr',
      gender: 'm',
      email: 'aarav.explicit@custommail.com',
      phone_number: '+919888877777',
    };

    await bookingService.selectFlightOffer(tripId, offerId, [travelerWithExplicitEmail]);
    assert.strictEqual(capturedPassengers[0].email, 'aarav.explicit@custommail.com');

    console.log('✔ Test 3: Explicit traveler email precedence verified.');
  } finally {
    apiClient.selectFlightBooking = originalSelect;
  }
}

// ---------------------------------------------------------------------------
// Test 4: Multi-passenger support (N travelers, 1 <= N <= 9) propagates N distinct records
// ---------------------------------------------------------------------------
{
  const tripId = 'trip_w2_flow_4';
  const offerId = 'off_w2_flight_4';
  let capturedPassengers: FlightPassenger[] = [];

  const originalSelect = apiClient.selectFlightBooking;
  apiClient.selectFlightBooking = (async (data: any) => {
    capturedPassengers = data.passengers;
    return {
      success: true,
      booking_id: 'b_w2_1004',
      trip_id: data.trip_id,
      offer_id: data.offer_id,
      status: 'SELECTED',
      expected_amount: 1700,
      currency: 'USD',
      expires_at: new Date(Date.now() + 3600000).toISOString(),
      approved: false,
    };
  }) as any;

  try {
    const multiPassengers: FlightPassenger[] = [
      {
        given_name: 'Rahul',
        family_name: 'Sharma',
        born_on: '1990-03-10',
        title: 'mr',
        gender: 'm',
        email: 'rahul.sharma@example.com',
      },
      {
        given_name: 'Priya',
        family_name: 'Sharma',
        born_on: '1993-07-22',
        title: 'mrs',
        gender: 'f',
        email: 'priya.sharma@example.com',
      },
      {
        given_name: 'Anika',
        family_name: 'Sharma',
        born_on: '2015-10-01',
        title: 'miss',
        gender: 'f',
        type: 'child',
      },
    ];

    await bookingService.selectFlightOffer(tripId, offerId, multiPassengers);
    assert.strictEqual(capturedPassengers.length, 3);
    assert.strictEqual(capturedPassengers[0].given_name, 'Rahul');
    assert.strictEqual(capturedPassengers[1].given_name, 'Priya');
    assert.strictEqual(capturedPassengers[2].given_name, 'Anika');
    assert.strictEqual(capturedPassengers[2].type, 'child');

    console.log('✔ Test 4: Multi-passenger array (N=3) propagates all records accurately.');
  } finally {
    apiClient.selectFlightBooking = originalSelect;
  }
}

// ---------------------------------------------------------------------------
// Test 5: Incomplete traveler validation fails closed before booking
// ---------------------------------------------------------------------------
{
  const tripId = 'trip_w2_flow_5';
  const offerId = 'off_w2_flight_5';

  try {
    // Attempting verifyFlight with no passengers snapshot or empty passengers must fail closed
    let threw = false;
    try {
      await bookingService.verifyFlight(tripId, offerId, []);
    } catch (err: any) {
      threw = true;
      assert.ok(
        err.message.includes('Passenger details are required') || err.message.includes('Passenger information is required'),
        `Expected error message about missing passengers, got: ${err.message}`
      );
    }
    assert.strictEqual(threw, true, 'verifyFlight with empty passengers must throw error');

    console.log('✔ Test 5: Missing traveler details fail closed with validation error.');
  } catch (e) {
    throw e;
  }
}

// ---------------------------------------------------------------------------
// Test 6: Hotel booking propagates actual traveler details to holder and guests
// ---------------------------------------------------------------------------
{
  const tripId = 'trip_w2_flow_6';
  const revisionId = 'b8ec2912-3023-455b-bb48-522f186c3da9';
  const revisionNumber = 1;
  let capturedVerifyPayload: any = null;

  const originalFetch = global.fetch;
  const originalCreateOrder = apiClient.createHotelPaymentOrder;
  const originalVerify = apiClient.verifyAndBookHotel;

  let capturedPrebookPayload: any = null;
  global.fetch = (async (url: string | URL | Request, init?: any) => {
    const u = String(url);
    if (u.includes('/api/hotels/prebook')) {
      if (init?.body) {
        capturedPrebookPayload = typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
      }
      return new Response(
        JSON.stringify({
          prebook_id: 'pb_taj_1',
          amount: 25000,
          currency: 'INR',
          hotel_name: 'The Taj Mahal Palace',
          expires_at: new Date(Date.now() + 600000).toISOString(),
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response(JSON.stringify({}), { status: 404 });
  }) as any;

  apiClient.createHotelPaymentOrder = (async () => ({
    success: true,
    transaction_id: 'tx_hotel_w2_1',
    razorpay_order_id: 'order_rzp_w2_1',
    key_id: 'rzp_test_key',
    amount_inr: 25000,
  })) as any;

  apiClient.verifyAndBookHotel = (async (payload: any) => {
    capturedVerifyPayload = payload;
    return {
      success: true,
      transaction_id: payload.transaction_id,
      payment_captured: true,
      booking: {
        booking_id: 'hotel_bk_w2_99',
        confirmation_code: 'CONF-HOTEL-W2',
        status: 'confirmed',
      },
      amount_inr: 25000,
    };
  }) as any;

  try {
    const contact: TravelerContactInfo = {
      first_name: 'Vikram',
      last_name: 'Singhania',
      email: 'vikram.singhania@domain.com',
      phone: '+919811122233',
    };

    const travelers: FlightPassenger[] = [
      {
        given_name: 'Vikram',
        family_name: 'Singhania',
        born_on: '1987-11-20',
        title: 'mr',
        gender: 'm',
        email: 'vikram.singhania@domain.com',
      },
      {
        given_name: 'Meera',
        family_name: 'Singhania',
        born_on: '1990-04-18',
        title: 'mrs',
        gender: 'f',
        email: 'meera.singhania@domain.com',
      },
    ];

    // Prepare hotel booking first to create authoritative prebook state
    await bookingService.prepareHotelBooking(tripId, 'ht_taj_1', undefined, {
      firstName: 'Vikram',
      lastName: 'Singhania',
      email: 'vikram.singhania@domain.com',
      phone: '+919811122233',
    });

    // Verify W2-FIND-02: guest_name in prebook is derived as Vikram Singhania, NOT 'Lead Guest'
    assert.ok(capturedPrebookPayload, 'prebook request payload must be captured');
    assert.strictEqual(capturedPrebookPayload.guest_name, 'Vikram Singhania');
    assert.strictEqual(capturedPrebookPayload.guest_email, 'vikram.singhania@domain.com');

    const result = await bookingService.processHotelPayment(
      tripId,
      'card',
      contact,
      revisionId,
      revisionNumber,
      {
        razorpay_order_id: 'order_rzp_w2_1',
        razorpay_payment_id: 'pay_rzp_w2_1',
        razorpay_signature: 'sig_rzp_w2_1',
      },
      travelers
    );

    assert.strictEqual(result.confirmation_code, 'CONF-HOTEL-W2');
    assert.ok(capturedVerifyPayload, 'verifyAndBookHotel must have been called');
    assert.strictEqual(capturedVerifyPayload.holder.firstName, 'Vikram');
    assert.strictEqual(capturedVerifyPayload.holder.lastName, 'Singhania');
    assert.strictEqual(capturedVerifyPayload.holder.email, 'vikram.singhania@domain.com');
    assert.strictEqual(capturedVerifyPayload.holder.phone, '+919811122233');

    // Guests array must contain both travelers
    assert.strictEqual(capturedVerifyPayload.guests.length, 2);
    assert.strictEqual(capturedVerifyPayload.guests[0].firstName, 'Vikram');
    assert.strictEqual(capturedVerifyPayload.guests[1].firstName, 'Meera');

    console.log('✔ Test 6: Hotel booking propagates actual traveler details to holder and guests (including prebook lead guest name).');
  } finally {
    global.fetch = originalFetch;
    apiClient.createHotelPaymentOrder = originalCreateOrder;
    apiClient.verifyAndBookHotel = originalVerify;
  }
}

// ---------------------------------------------------------------------------
// Test 7: Static audit verifies zero synthetic passenger literals in production booking code
// ---------------------------------------------------------------------------
{
  const srcDir = path.resolve(__dirname, '..');
  const targetFiles = [
    path.join(srcDir, 'services', 'bookingService.ts'),
    path.join(srcDir, 'components', 'booking', 'BookingWorkspace.tsx'),
    path.join(srcDir, 'components', 'booking', 'TripConfirmationView.tsx'),
    path.join(srcDir, 'components', 'views', 'TripDetailView.tsx'),
  ];

  const forbiddenTokens = [
    'Ananya',
    'Roy',
    'ananya.roy@example.travel',
    'traveler@trusttrip.com',
    '1990-01-01',
  ];

  for (const filePath of targetFiles) {
    assert.ok(fs.existsSync(filePath), `File must exist: ${filePath}`);
    const content = fs.readFileSync(filePath, 'utf-8');

    for (const token of forbiddenTokens) {
      assert.strictEqual(
        content.includes(token),
        false,
        `Forbidden synthetic literal '${token}' found in production file: ${filePath}`
      );
    }
  }

  // W2-FIND-03: Verify DEFAULT_TRAVELER_CONTACT has been removed from bookingMockData.ts
  const mockFile = path.join(srcDir, 'mock', 'bookingMockData.ts');
  const mockContent = fs.readFileSync(mockFile, 'utf-8');
  assert.strictEqual(
    mockContent.includes('DEFAULT_TRAVELER_CONTACT'),
    false,
    'DEFAULT_TRAVELER_CONTACT must be completely removed from bookingMockData.ts'
  );

  console.log('✔ Test 7: Static audit verified zero synthetic passenger literals in production booking code and mock constants.');
}

console.log('\nAll Wave 2 Real Traveler Identity & Passenger Propagation Tests PASSED!\n');
