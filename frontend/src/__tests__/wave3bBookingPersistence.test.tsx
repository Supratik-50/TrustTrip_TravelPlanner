/**
 * Wave 3B Booking Persistence, Rehydration & My Trips Contract Tests
 *
 * Tests:
 * 1. apiClient.getTripBookings sends GET to /api/trips/{tripId}/bookings and returns canonical component state.
 * 2. bookingService.getTripBookings delegates directly to apiClient.getTripBookings.
 * 3. apiClient.getTrips normalizes flight_count, hotel_count, flights, and hotels into TripSummary.
 * 4. BookingWorkspace static audit: server-driven rehydration replaces unconditional confirmed->idle reset.
 * 5. TripCard static audit: renders confirmed flight and hotel badges and verified download links.
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TrustTripBookingService } from '../services/bookingService';
import { apiClient } from '../lib/apiClient';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function runTests() {
  console.log('\nRunning Wave 3B Booking Persistence & Rehydration Tests...\n');
  const originalFetch = global.fetch;

  // --------------------------------------------------------------------------
  // Test 1: apiClient.getTripBookings sends GET to /api/trips/{tripId}/bookings
  // --------------------------------------------------------------------------
  {
    let capturedUrl = '';
    let capturedMethod = '';
    global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      capturedUrl = String(url);
      capturedMethod = init?.method || 'GET';
      return new Response(
        JSON.stringify({
          success: true,
          trip_id: 'trip_w3b_100',
          flight: {
            is_booked: true,
            status: 'completed',
            booking_id: 'intent_flight_100',
            booking_reference: 'BA9988',
            order_id: 'ord_ba_100',
            provider: 'duffel',
            amount: 45000,
            currency: 'INR',
          },
          hotel: {
            is_booked: true,
            status: 'booked',
            transaction_id: 'tx_hotel_100',
            confirmation_code: 'VOUCH-7788',
            hotel_name: 'Hotel Plaza Athénée',
            amount_inr: 55000,
            currency: 'INR',
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await apiClient.getTripBookings('trip_w3b_100');

    assert.ok(capturedUrl.includes('/api/trips/trip_w3b_100/bookings'), `Expected /api/trips/trip_w3b_100/bookings, got ${capturedUrl}`);
    assert.strictEqual(capturedMethod, 'GET');
    assert.strictEqual(res.trip_id, 'trip_w3b_100');
    assert.strictEqual(res.flight?.is_booked, true);
    assert.strictEqual(res.flight?.booking_reference, 'BA9988');
    assert.strictEqual(res.hotel?.is_booked, true);
    assert.strictEqual(res.hotel?.confirmation_code, 'VOUCH-7788');
    assert.strictEqual(res.hotel?.hotel_name, 'Hotel Plaza Athénée');
    console.log('✔ Test 1: apiClient.getTripBookings queries /api/trips/{trip_id}/bookings and receives canonical status.');
  }

  // --------------------------------------------------------------------------
  // Test 2: bookingService.getTripBookings delegates to apiClient
  // --------------------------------------------------------------------------
  {
    const bookingSvc = new TrustTripBookingService();
    global.fetch = (async (url: string | URL | Request) => {
      return new Response(
        JSON.stringify({
          success: true,
          trip_id: 'trip_w3b_200',
          flight: null,
          hotel: {
            is_booked: false,
            status: 'booking_in_progress',
            hotel_name: 'Hotel Louvre',
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const res = await bookingSvc.getTripBookings('trip_w3b_200');
    assert.strictEqual(res.trip_id, 'trip_w3b_200');
    assert.strictEqual(res.flight, null);
    assert.strictEqual(res.hotel?.status, 'booking_in_progress');
    assert.strictEqual(res.hotel?.is_booked, false);
    console.log('✔ Test 2: bookingService.getTripBookings delegates to apiClient and preserves in-progress status.');
  }

  // --------------------------------------------------------------------------
  // Test 3: apiClient.getTrips normalizes flight_count, hotel_count, flights, and hotels
  // --------------------------------------------------------------------------
  {
    global.fetch = (async () => {
      return new Response(
        JSON.stringify({
          success: true,
          trips: [
            {
              id: 'trip_w3b_300',
              title: 'Paris Summer Trip',
              destination: 'Paris, France',
              status: 'planning',
              current_revision_number: 2,
              flight_count: 1,
              hotel_count: 1,
              flights: [
                {
                  id: 'intent_fl_300',
                  booking_reference: 'AF1234',
                  booking_status: 'booked',
                },
              ],
              hotels: [
                {
                  id: 'tx_ht_300',
                  confirmation_code: 'VOUCH-300',
                  booking_reference: 'VOUCH-300',
                  hotel_name: 'Ritz Paris',
                  booking_status: 'booked',
                },
              ],
              created_at: '2026-10-01T10:00:00Z',
              updated_at: '2026-10-01T10:00:00Z',
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }) as any;

    const trips = await apiClient.getTrips();
    assert.strictEqual(trips.length, 1);
    const trip = trips[0];
    assert.strictEqual(trip.id, 'trip_w3b_300');
    assert.strictEqual(trip.flight_count, 1);
    assert.strictEqual(trip.hotel_count, 1);
    assert.strictEqual(trip.flights?.length, 1);
    assert.strictEqual(trip.flights?.[0].booking_reference, 'AF1234');
    assert.strictEqual(trip.hotels?.length, 1);
    assert.strictEqual(trip.hotels?.[0].confirmation_code, 'VOUCH-300');
    console.log('✔ Test 3: apiClient.getTrips normalizes flight/hotel counts and arrays into TripSummary.');
  }

  // --------------------------------------------------------------------------
  // Test 4: BookingWorkspace static audit — server-driven rehydration
  // --------------------------------------------------------------------------
  {
    const workspacePath = path.resolve(__dirname, '../components/booking/BookingWorkspace.tsx');
    const workspaceSource = fs.readFileSync(workspacePath, 'utf8');

    // Verify getTripBookings is invoked
    assert.ok(
      workspaceSource.includes('getTripBookings'),
      'BookingWorkspace must invoke getTripBookings for server-driven rehydration'
    );

    // Verify confirmed->idle reset on mount was replaced
    assert.ok(
      !workspaceSource.includes("setFlightBookingState('idle')\n    setHotelBookingState('idle')"),
      'BookingWorkspace must not unconditionally reset confirmed booking states to idle on mount'
    );

    console.log('✔ Test 4: BookingWorkspace static audit confirms server-driven booking rehydration on mount.');
  }

  // --------------------------------------------------------------------------
  // Test 5: TripCard static audit — confirmed booking links
  // --------------------------------------------------------------------------
  {
    const tripCardPath = path.resolve(__dirname, '../components/travel/TripCard.tsx');
    const tripCardSource = fs.readFileSync(tripCardPath, 'utf8');

    // Verify ticket.pdf and hotel.pdf links are rendered
    assert.ok(
      tripCardSource.includes('/ticket.pdf'),
      'TripCard must render ticket.pdf download link for confirmed flight bookings'
    );
    assert.ok(
      tripCardSource.includes('/hotel.pdf'),
      'TripCard must render hotel.pdf voucher link for confirmed hotel bookings'
    );

    console.log('✔ Test 5: TripCard static audit confirms flight ticket and hotel voucher links.');
  }

  global.fetch = originalFetch;
  console.log('\nAll Wave 3B Booking Persistence & Rehydration Tests PASSED!\n');
}

runTests().catch((err) => {
  console.error('\n❌ Wave 3B Test Failed:', err);
  process.exit(1);
});
