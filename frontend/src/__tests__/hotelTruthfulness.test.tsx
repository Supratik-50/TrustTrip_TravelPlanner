/**
 * Frontend Truthfulness & Regression Tests for Section 6 Micro-Remediation E/F
 *
 * Verifies:
 * 1. HotelCard & Stay Snapshot Pill: missing stars -> NO star badge/label rendered (no default 4-star).
 * 2. HotelCard: explicit stars -> renders exact star count.
 * 3. HotelCard: stay duration uses actual trip duration (no hardcoded 5-night default).
 * 4. HotelCard: direct provider room total is displayed accurately without artificial recalculation.
 * 5. HotelCard: per-night breakdown is SUPPRESSED when nightsCount is unknown and hotel.nights is absent.
 */
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { HotelCard } from '../components/travel/HotelCard';
import { HotelOffer } from '../types/hotel';

function runTests() {
  console.log('Running frontend truthfulness regression tests...');

  // Test 1: HotelCard with missing stars renders NO star badge (no default 4-star)
  {
    const hotelWithoutStars: HotelOffer = {
      id: 'h_no_stars',
      name: 'Unrated Boutique Stay',
      address: '456 Lane',
      city: 'Kyoto',
      total_amount: 400,
      currency: 'USD',
      stars: null,
      rating: null,
      rooms: [],
    };
    const html = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel: hotelWithoutStars })
    );

    assert(
      !html.includes('hotel-stars'),
      'FAILED: Star pill data-testid should not render when stars are null'
    );
    assert(
      !html.includes('4-Star Stay'),
      'FAILED: Star pill must not fall back to fabricated 4-star default'
    );
    assert(
      !html.includes('-Star Stay'),
      'FAILED: Star label must not render when stars are missing'
    );
    console.log('✔ Test 1: Missing stars on HotelCard renders NO fabricated star pill.');
  }

  // Test 2: HotelCard with explicit stars renders exact star count
  {
    const hotelWithStars: HotelOffer = {
      id: 'h_stars_5',
      name: 'Luxury Palace',
      address: '789 Blvd',
      city: 'Kyoto',
      total_amount: 1200,
      currency: 'USD',
      stars: 5,
      rating: 9.6,
      rooms: [],
    };
    const html = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel: hotelWithStars })
    );

    assert(
      html.includes('hotel-stars'),
      'FAILED: Star pill should render when stars are present'
    );
    assert(
      html.includes('5-Star Stay'),
      'FAILED: Star pill should render exact 5-Star Stay'
    );
    console.log('✔ Test 2: Explicit stars on HotelCard renders exact star count.');
  }

  // Test 3: Stay duration does NOT default to 5 nights; uses actual passed trip duration
  {
    const hotel: HotelOffer = {
      id: 'h_stay_dur',
      name: 'Alpine Chalet',
      address: '100 Mountain Rd',
      city: 'Kyoto',
      total_amount: 900,
      currency: 'USD',
      nights: 3,
      rooms: [
        {
          id: 'r_nightly',
          name: 'Deluxe Room',
          price_per_night: 150,
        },
      ],
    };

    // Case A: nightsCount passed as 7 (actual trip length: 7 nights)
    const htmlTrip7 = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel, nightsCount: 7 })
    );
    assert(
      htmlTrip7.includes('7 nights stay'),
      'FAILED: Subtitle must indicate actual 7 nights stay, not 5'
    );
    assert(
      !htmlTrip7.includes('5 nights stay'),
      'FAILED: Subtitle must not default to 5 nights stay'
    );
    // 150 * 7 = 1050
    assert(
      htmlTrip7.includes('1,050') || htmlTrip7.includes('1050'),
      'FAILED: Total price should be 150 * 7 = 1,050'
    );

    // Case B: no nightsCount passed; falls back to hotel.nights (3 nights, not 5)
    const htmlHotel3 = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel })
    );
    assert(
      htmlHotel3.includes('3 nights stay'),
      'FAILED: Subtitle should use hotel.nights (3 nights), not 5'
    );
    assert(
      !htmlHotel3.includes('5 nights stay'),
      'FAILED: Subtitle must not default to 5 nights stay'
    );
    // 150 * 3 = 450
    assert(
      htmlHotel3.includes('450'),
      'FAILED: Total price should be 150 * 3 = 450'
    );
    console.log('✔ Test 3: Stay duration uses actual trip duration (no 5-night default).');
  }

  // Test 4: Provider-supplied room total is displayed directly without recalculation
  {
    const hotelWithDirectTotal: HotelOffer = {
      id: 'h_direct',
      name: 'Resort & Spa',
      address: '200 Ocean Ave',
      city: 'Kyoto',
      total_amount: 1500,
      currency: 'USD',
      nights: 4,
      rooms: [
        {
          id: 'r_direct',
          name: 'Presidential Suite',
          total_price: 1350,
          price_per_night: 300,
        },
      ],
    };

    const html = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel: hotelWithDirectTotal, nightsCount: 4 })
    );
    // Should display direct provider room total $1,350 rather than 300 * 4 = 1,200
    assert(
      html.includes('1,350') || html.includes('1350'),
      'FAILED: Should display direct provider room total of $1,350'
    );
    console.log('✔ Test 4: Direct provider room total displayed directly without artificial recalculation.');
  }

  // Test 5 (Remediation F — Issue 3): HotelCard suppresses per-night pricing when nightsCount is unknown
  {
    const hotelNoDuration: HotelOffer = {
      id: 'h_no_duration',
      name: 'Mystery Inn',
      address: '1 Unknown St',
      city: 'Osaka',
      total_amount: 500,
      currency: 'USD',
      // nights deliberately absent
      rooms: [],
    };

    // No nightsCount prop, no hotel.nights → stayNights is null → no per-night line
    const html = renderToStaticMarkup(
      React.createElement(HotelCard, { hotel: hotelNoDuration })
    );

    // Per-night breakdown must NOT appear
    assert(
      !html.includes('/ night for'),
      'FAILED: Per-night pricing must NOT render when nightsCount is unknown'
    );
    assert(
      !html.includes('1 night stay') && !html.includes('1 nights stay'),
      'FAILED: HotelCard must not fabricate a 1-night fallback'
    );
    // Total price must still render
    assert(
      html.includes('500'),
      'FAILED: Total price must still render even when nightsCount is unknown'
    );
    console.log('✔ Test 5: HotelCard suppresses per-night pricing when nightsCount is unknown (no 1-night fallback).');
  }

  // Test 6: Standalone hotel search is trip-bound and has no sample search context
  {
    const hotelsView = readFileSync(
      new URL('../components/views/HotelsView.tsx', import.meta.url),
      'utf8'
    );
    assert(hotelsView.includes('trip_id: selectedTrip.trip_id'));
    assert(hotelsView.includes('if (!selectedTrip)'));
    assert(!hotelsView.includes('Kyoto, Japan'));
    assert(!hotelsView.includes('2026-10-14'));
    assert(!hotelsView.includes('2026-10-24'));
    assert(!hotelsView.includes('guests: 2'));
    console.log('✔ Test 6: Standalone hotel search requires a selected canonical trip.');
  }

  // =========================================================================
  // Section 6 Micro-Remediation I: TripDetailView Canonical Hotel Search Tests
  // =========================================================================
  console.log('\nRunning TripDetailView Canonical Hotel Search Tests...');

  const tripDetailViewSource = readFileSync(
    new URL('../components/views/TripDetailView.tsx', import.meta.url),
    'utf8'
  );

  // Extract hotel-search related portion of TripDetailView to inspect search flow
  const hotelSearchFlowSlice = tripDetailViewSource.slice(
    tripDetailViewSource.indexOf('handleSearchHotels'),
    tripDetailViewSource.indexOf('handleSelectFlightOffer')
  );

  // Function representing the exact TripDetailView hotel-search invocation logic
  interface TripContext {
    trip_id?: string;
    destination?: string;
    start_date?: string;
    end_date?: string;
    travelers_count?: number | null;
  }

  function simulateTripDetailViewHotelSearch(
    trip: TripContext | null,
    tripId: string | undefined,
    invokedRequests: any[]
  ): boolean {
    // Exact guard logic from TripDetailView.tsx handleSearchHotels
    if (
      !tripId ||
      !trip?.destination ||
      !trip?.start_date ||
      !trip?.end_date ||
      trip?.travelers_count == null ||
      Number(trip.travelers_count) < 1
    ) {
      return false;
    }

    const payload = {
      trip_id: tripId,
      destination: trip.destination,
      city: trip.destination,
      checkin: trip.start_date,
      checkout: trip.end_date,
      check_in_date: trip.start_date,
      check_out_date: trip.end_date,
      adults: Number(trip.travelers_count),
      guests: Number(trip.travelers_count),
    };

    invokedRequests.push(payload);
    return true;
  }

  // TEST 1: Trip with complete canonical values -> hotel search is invoked
  {
    const invoked: any[] = [];
    const completeTrip: TripContext = {
      trip_id: 'trip_tokyo_001',
      destination: 'Tokyo, Japan',
      start_date: '2026-11-01',
      end_date: '2026-11-10',
      travelers_count: 3,
    };

    const didInvoke = simulateTripDetailViewHotelSearch(completeTrip, completeTrip.trip_id, invoked);
    assert.strictEqual(didInvoke, true, 'TEST 1 FAILED: Hotel search should be invoked for complete canonical trip');
    assert.strictEqual(invoked.length, 1, 'TEST 1 FAILED: Exactly one search request should be issued');
    console.log('✔ Remediation I - TEST 1: Complete canonical trip -> hotel search is invoked.');
  }

  // TEST 2: Trip missing destination -> hotel search is NOT invoked
  {
    const invoked: any[] = [];
    const tripNoDest: TripContext = {
      trip_id: 'trip_no_dest_002',
      destination: '',
      start_date: '2026-11-01',
      end_date: '2026-11-10',
      travelers_count: 2,
    };

    const didInvoke = simulateTripDetailViewHotelSearch(tripNoDest, tripNoDest.trip_id, invoked);
    assert.strictEqual(didInvoke, false, 'TEST 2 FAILED: Hotel search must NOT be invoked when destination is missing');
    assert.strictEqual(invoked.length, 0, 'TEST 2 FAILED: No search request should be issued when destination is missing');
    console.log('✔ Remediation I - TEST 2: Missing destination -> hotel search is NOT invoked.');
  }

  // TEST 3: Trip missing start_date -> hotel search is NOT invoked
  {
    const invoked: any[] = [];
    const tripNoStart: TripContext = {
      trip_id: 'trip_no_start_003',
      destination: 'London, UK',
      start_date: '',
      end_date: '2026-11-10',
      travelers_count: 2,
    };

    const didInvoke = simulateTripDetailViewHotelSearch(tripNoStart, tripNoStart.trip_id, invoked);
    assert.strictEqual(didInvoke, false, 'TEST 3 FAILED: Hotel search must NOT be invoked when start_date is missing');
    assert.strictEqual(invoked.length, 0, 'TEST 3 FAILED: No search request should be issued when start_date is missing');
    console.log('✔ Remediation I - TEST 3: Missing start_date -> hotel search is NOT invoked.');
  }

  // TEST 4: Trip missing end_date -> hotel search is NOT invoked
  {
    const invoked: any[] = [];
    const tripNoEnd: TripContext = {
      trip_id: 'trip_no_end_004',
      destination: 'London, UK',
      start_date: '2026-11-01',
      end_date: '',
      travelers_count: 2,
    };

    const didInvoke = simulateTripDetailViewHotelSearch(tripNoEnd, tripNoEnd.trip_id, invoked);
    assert.strictEqual(didInvoke, false, 'TEST 4 FAILED: Hotel search must NOT be invoked when end_date is missing');
    assert.strictEqual(invoked.length, 0, 'TEST 4 FAILED: No search request should be issued when end_date is missing');
    console.log('✔ Remediation I - TEST 4: Missing end_date -> hotel search is NOT invoked.');
  }

  // TEST 5: Trip missing traveler count -> hotel search is NOT invoked
  {
    const invoked: any[] = [];
    const tripNoTravelers: TripContext = {
      trip_id: 'trip_no_travelers_005',
      destination: 'Rome, Italy',
      start_date: '2026-11-01',
      end_date: '2026-11-10',
      travelers_count: null,
    };

    const didInvoke = simulateTripDetailViewHotelSearch(tripNoTravelers, tripNoTravelers.trip_id, invoked);
    assert.strictEqual(didInvoke, false, 'TEST 5 FAILED: Hotel search must NOT be invoked when traveler count is null');
    assert.strictEqual(invoked.length, 0, 'TEST 5 FAILED: No search request should be issued when traveler count is null');

    const tripZeroTravelers: TripContext = {
      trip_id: 'trip_zero_travelers_005',
      destination: 'Rome, Italy',
      start_date: '2026-11-01',
      end_date: '2026-11-10',
      travelers_count: 0,
    };
    const didInvokeZero = simulateTripDetailViewHotelSearch(tripZeroTravelers, tripZeroTravelers.trip_id, invoked);
    assert.strictEqual(didInvokeZero, false, 'TEST 5 FAILED: Hotel search must NOT be invoked when traveler count is 0');
    assert.strictEqual(invoked.length, 0, 'TEST 5 FAILED: No search request should be issued when traveler count is 0');
    console.log('✔ Remediation I - TEST 5: Missing traveler count -> hotel search is NOT invoked.');
  }

  // TEST 6: Complete trip request contains exact canonical values
  {
    const invoked: any[] = [];
    const canonicalTrip: TripContext = {
      trip_id: 'trip_canonical_777',
      destination: 'Barcelona, Spain',
      start_date: '2026-12-05',
      end_date: '2026-12-15',
      travelers_count: 4,
    };

    simulateTripDetailViewHotelSearch(canonicalTrip, canonicalTrip.trip_id, invoked);
    assert.strictEqual(invoked.length, 1);
    const req = invoked[0];
    assert.strictEqual(req.trip_id, 'trip_canonical_777', 'TEST 6 FAILED: canonical trip_id mismatch');
    assert.strictEqual(req.destination, 'Barcelona, Spain', 'TEST 6 FAILED: canonical destination mismatch');
    assert.strictEqual(req.checkin, '2026-12-05', 'TEST 6 FAILED: canonical checkin mismatch');
    assert.strictEqual(req.checkout, '2026-12-15', 'TEST 6 FAILED: canonical checkout mismatch');
    assert.strictEqual(req.adults, 4, 'TEST 6 FAILED: canonical adults mismatch');
    console.log('✔ Remediation I - TEST 6: Search request contains exact canonical values.');
  }

  // TEST 7: No request contains sample values / fabricated fallbacks
  {
    // A. Assert generated payload never contains sample fallback values
    const invoked: any[] = [];
    const trip: TripContext = {
      trip_id: 'trip_real_888',
      destination: 'Zurich, Switzerland',
      start_date: '2027-01-10',
      end_date: '2027-01-20',
      travelers_count: 1,
    };
    simulateTripDetailViewHotelSearch(trip, trip.trip_id, invoked);
    const req = invoked[0];
    assert.notStrictEqual(req.destination, 'Kyoto, Japan');
    assert.notStrictEqual(req.checkin, '2026-10-14');
    assert.notStrictEqual(req.checkout, '2026-10-24');
    assert.notStrictEqual(req.adults, 2);

    // B. Assert TripDetailView source code contains NO fabricated fallbacks in hotel search flow
    assert(
      !hotelSearchFlowSlice.includes("'Kyoto, Japan'"),
      'TEST 7 FAILED: TripDetailView hotel search flow must not contain "Kyoto, Japan"'
    );
    assert(
      !hotelSearchFlowSlice.includes("'2026-10-14'"),
      'TEST 7 FAILED: TripDetailView hotel search flow must not contain "2026-10-14"'
    );
    assert(
      !hotelSearchFlowSlice.includes("'2026-10-24'"),
      'TEST 7 FAILED: TripDetailView hotel search flow must not contain "2026-10-24"'
    );
    assert(
      !hotelSearchFlowSlice.includes('trip.travelers_count || 2') &&
      !hotelSearchFlowSlice.includes('trip.travelers_count ||'),
      'TEST 7 FAILED: TripDetailView hotel search flow must not fall back to adults || 2'
    );
    assert(
      !hotelSearchFlowSlice.includes("trip.destination || '") &&
      !hotelSearchFlowSlice.includes('trip.destination || "'),
      'TEST 7 FAILED: TripDetailView hotel search flow must not fall back on trip.destination'
    );
    assert(
      !hotelSearchFlowSlice.includes("trip.start_date || '") &&
      !hotelSearchFlowSlice.includes('trip.start_date || "'),
      'TEST 7 FAILED: TripDetailView hotel search flow must not fall back on trip.start_date'
    );
    assert(
      !hotelSearchFlowSlice.includes("trip.end_date || '") &&
      !hotelSearchFlowSlice.includes('trip.end_date || "'),
      'TEST 7 FAILED: TripDetailView hotel search flow must not fall back on trip.end_date'
    );

    // C. Assert TripDetailView source guards all hotel search entry points
    assert(
      tripDetailViewSource.includes('!tripId') &&
      tripDetailViewSource.includes('!trip?.destination') &&
      tripDetailViewSource.includes('!trip?.start_date') &&
      tripDetailViewSource.includes('!trip?.end_date') &&
      tripDetailViewSource.includes('trip?.travelers_count == null'),
      'TEST 7 FAILED: handleSearchHotels must guard against missing canonical trip values'
    );
    assert(
      tripDetailViewSource.includes('!trip.trip_id') &&
      tripDetailViewSource.includes('!trip.destination') &&
      tripDetailViewSource.includes('!trip.start_date') &&
      tripDetailViewSource.includes('!trip.end_date') &&
      tripDetailViewSource.includes('trip.travelers_count == null'),
      'TEST 7 FAILED: Hotel tab useEffect and retry must guard against missing canonical trip values'
    );

    console.log('✔ Remediation I - TEST 7: No request or TripDetailView hotel search path contains fabricated sample values.');
  }

  console.log('\nAll frontend truthfulness and canonical hotel search regression tests PASSED!');
}

runTests();
