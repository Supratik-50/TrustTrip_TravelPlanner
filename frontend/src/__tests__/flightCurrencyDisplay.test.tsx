/**
 * Frontend Tests for Flight Dual-Currency Display
 *
 * Verifies:
 * A. price_inr and exchange_rate survive toFlightOffer().
 * B. USD offer renders provider price ($597).
 * C. USD offer also renders INR reference estimate (₹49,551 (est.)).
 * D. INR offer does not render unnecessary dual currency.
 * E. Missing price_inr does not fabricate an estimate.
 */
import assert from 'node:assert';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { FlightCard } from '../components/travel/FlightCard';
import { toFlightOffer, NormalizedFlightOffer, FlightOffer } from '../types/flight';
import { formatCurrency } from '../lib/currency';

console.log('Running Flight Dual-Currency Display Tests...\n');

// Sample base normalized offer
const baseNormalized: NormalizedFlightOffer = {
  offer_id: 'off_test_123',
  provider: 'duffel',
  origin: 'CCU',
  destination: 'COK',
  departure_time: '2026-10-14T10:00:00',
  arrival_time: '2026-10-14T13:00:00',
  return_departure_time: '2026-10-21T15:00:00',
  return_arrival_time: '2026-10-21T18:00:00',
  duration: '3h',
  stops: 0,
  carrier: 'Duffel Airways',
  carrier_code: 'DA',
  cabin_class: 'economy',
  price: 597,
  currency: 'USD',
  price_inr: 49551,
  exchange_rate: 83.0,
  refundable: true,
  segments: [
    {
      origin: 'CCU',
      destination: 'COK',
      departure_time: '2026-10-14T10:00:00',
      arrival_time: '2026-10-14T13:00:00',
      carrier: 'Duffel Airways',
      carrier_code: 'DA',
      flight_number: 'DA-3551',
    },
    {
      origin: 'COK',
      destination: 'CCU',
      departure_time: '2026-10-21T15:00:00',
      arrival_time: '2026-10-21T18:00:00',
      carrier: 'Duffel Airways',
      carrier_code: 'DA',
      flight_number: 'DA-3552',
    },
  ],
};

// ---------------------------------------------------------------------------
// Test A: price_inr and exchange_rate survive toFlightOffer()
// ---------------------------------------------------------------------------
{
  const flightOffer = toFlightOffer(baseNormalized);
  assert.strictEqual(flightOffer.price, 597, 'Authoritative price preserved');
  assert.strictEqual(flightOffer.currency, 'USD', 'Authoritative currency preserved');
  assert.strictEqual(flightOffer.price_inr, 49551, 'price_inr must survive toFlightOffer()');
  assert.strictEqual(flightOffer.exchange_rate, 83.0, 'exchange_rate must survive toFlightOffer()');
  assert.strictEqual(flightOffer.slices.length, 2, 'Round-trip slices constructed');
  assert.strictEqual(flightOffer.slices[0].origin, 'CCU');
  assert.strictEqual(flightOffer.slices[0].destination, 'COK');
  assert.strictEqual(flightOffer.slices[1].origin, 'COK');
  assert.strictEqual(flightOffer.slices[1].destination, 'CCU');
  console.log('✔ Test A: price_inr and exchange_rate survive toFlightOffer() with round-trip slices');
}

// ---------------------------------------------------------------------------
// Test B: USD offer renders provider price
// ---------------------------------------------------------------------------
{
  const usdOffer: FlightOffer = {
    id: 'off_usd',
    offer_id: 'off_usd',
    slices: [
      {
        origin: 'CCU',
        destination: 'COK',
        departure_time: '2026-10-14T10:00:00',
        arrival_time: '2026-10-14T13:00:00',
        segments: [],
        total_duration_minutes: 180,
        stops: 0,
      },
    ],
    total_amount: '597',
    price: 597,
    currency: 'USD',
    carrier_name: 'Duffel Airways',
    carrier_code: 'DA',
    cabin_class: 'economy',
    refundable: false,
  };

  const html = renderToStaticMarkup(React.createElement(FlightCard, { offer: usdOffer }));
  const formattedUsd = formatCurrency('597', 'USD');
  assert.ok(html.includes(formattedUsd), `HTML must render provider price (${formattedUsd})`);
  assert.ok(!html.includes('₹597'), 'Must not substitute currency symbols with fake conversions');
  console.log('✔ Test B: USD offer renders provider price without symbol substitution');
}

// ---------------------------------------------------------------------------
// Test C: USD offer also renders INR reference estimate
// ---------------------------------------------------------------------------
{
  const usdOfferWithInr: FlightOffer = {
    id: 'off_usd_inr',
    offer_id: 'off_usd_inr',
    slices: [
      {
        origin: 'CCU',
        destination: 'COK',
        departure_time: '2026-10-14T10:00:00',
        arrival_time: '2026-10-14T13:00:00',
        segments: [],
        total_duration_minutes: 180,
        stops: 0,
      },
    ],
    total_amount: '597',
    price: 597,
    currency: 'USD',
    price_inr: 49551,
    exchange_rate: 83.0,
    carrier_name: 'Duffel Airways',
    carrier_code: 'DA',
    cabin_class: 'economy',
    refundable: false,
  };

  const html = renderToStaticMarkup(React.createElement(FlightCard, { offer: usdOfferWithInr }));
  const formattedUsd = formatCurrency('597', 'USD');
  const formattedInr = formatCurrency(49551, 'INR');

  assert.ok(html.includes(formattedUsd), `HTML must render provider price ${formattedUsd}`);
  assert.ok(html.includes(formattedInr), `HTML must render reference INR estimate ${formattedInr}`);
  assert.ok(html.includes('(est.)'), 'HTML must clearly display (est.) marker for reference conversion');
  console.log('✔ Test C: USD offer renders both provider price and reference INR estimate with (est.) marker');
}

// ---------------------------------------------------------------------------
// Test D: INR offer does not render unnecessary dual currency
// ---------------------------------------------------------------------------
{
  const inrOffer: FlightOffer = {
    id: 'off_inr',
    offer_id: 'off_inr',
    slices: [
      {
        origin: 'CCU',
        destination: 'COK',
        departure_time: '2026-10-14T10:00:00',
        arrival_time: '2026-10-14T13:00:00',
        segments: [],
        total_duration_minutes: 180,
        stops: 0,
      },
    ],
    total_amount: '49551',
    price: 49551,
    currency: 'INR',
    price_inr: 49551,
    carrier_name: 'IndiGo',
    carrier_code: '6E',
    cabin_class: 'economy',
    refundable: true,
  };

  const html = renderToStaticMarkup(React.createElement(FlightCard, { offer: inrOffer }));
  const formattedInr = formatCurrency('49551', 'INR');

  assert.ok(html.includes(formattedInr), `HTML must render INR price ${formattedInr}`);
  assert.ok(!html.includes('(est.)'), 'INR offer must not display (est.) or duplicate conversion');
  console.log('✔ Test D: INR offer does not render unnecessary dual currency or duplicate estimate');
}

// ---------------------------------------------------------------------------
// Test E: Missing price_inr does not fabricate an estimate
// ---------------------------------------------------------------------------
{
  const usdOfferNoInr: FlightOffer = {
    id: 'off_usd_no_inr',
    offer_id: 'off_usd_no_inr',
    slices: [
      {
        origin: 'CCU',
        destination: 'COK',
        departure_time: '2026-10-14T10:00:00',
        arrival_time: '2026-10-14T13:00:00',
        segments: [],
        total_duration_minutes: 180,
        stops: 0,
      },
    ],
    total_amount: '597',
    price: 597,
    currency: 'USD',
    price_inr: undefined,
    carrier_name: 'Duffel Airways',
    carrier_code: 'DA',
    cabin_class: 'economy',
    refundable: false,
  };

  const html = renderToStaticMarkup(React.createElement(FlightCard, { offer: usdOfferNoInr }));
  const formattedUsd = formatCurrency('597', 'USD');

  assert.ok(html.includes(formattedUsd), `HTML must render provider price ${formattedUsd}`);
  assert.ok(!html.includes('(est.)'), 'Must not fabricate (est.) when price_inr is absent');
  assert.ok(!html.includes('₹'), 'Must not fabricate INR symbol when price_inr is absent');
  console.log('✔ Test E: Missing price_inr does not fabricate an estimate or exchange rate');
}

console.log('\nAll Flight Dual-Currency Display Tests Passed successfully!\n');
