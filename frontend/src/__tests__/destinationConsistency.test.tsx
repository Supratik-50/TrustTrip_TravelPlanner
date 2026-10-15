/**
 * Destination Consistency Validation Regression Tests
 *
 * Validates that PlanningWorkspace destination consistency:
 * 1. Accepts legitimate region/state to canonical hub city matches (e.g. Kerala -> Kochi)
 * 2. Accepts legitimate region to regional city matches (e.g. Kerala -> Munnar, Kerala -> Alleppey, Rajasthan -> Jaipur)
 * 3. Accepts direct city-to-city matches (e.g. Paris -> Paris)
 * 4. Strictly rejects mismatched / hallucinated destinations (e.g. Kerala -> Paris, Kerala -> Tokyo, Paris -> London)
 */
import assert from 'node:assert';
import { validateDestinationConsistency } from '../components/planner/PlanningWorkspace';

console.log('Running Destination Consistency Regression Tests...\n');

// ---------------------------------------------------------------------------
// 1. Positive Cases (Must Be Accepted)
// ---------------------------------------------------------------------------

// Test 1: Kerala, India -> Kochi (with backend canonical response)
{
  const canonicalDest = 'Kerala, India';
  const planDest = 'Kochi';
  const mockResponse = {
    plan_json: { destination: 'Kochi', destination_airport: 'COK' },
    trip_constraints: {
      destination_query: 'Kerala, India',
      destination_city: 'Kochi',
      destination_airport: 'COK',
      destination_country: 'India',
    },
    destination_airport: 'COK',
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, true, 'Test 1 Failed: Kerala -> Kochi must be accepted');
  console.log('✔ Test 1 Passed: Kerala -> Kochi (accepted)');
}

// Test 2: Kerala, India -> Munnar (regional catalog hierarchy match)
{
  const canonicalDest = 'Kerala, India';
  const planDest = 'Munnar';
  const mockResponse = {
    plan_json: { destination: 'Munnar' },
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, true, 'Test 2 Failed: Kerala -> Munnar must be accepted');
  console.log('✔ Test 2 Passed: Kerala -> Munnar (accepted)');
}

// Test 3: Kerala, India -> Alleppey (regional catalog hierarchy match)
{
  const canonicalDest = 'Kerala, India';
  const planDest = 'Alleppey';
  const mockResponse = {
    plan_json: { destination: 'Alleppey' },
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, true, 'Test 3 Failed: Kerala -> Alleppey must be accepted');
  console.log('✔ Test 3 Passed: Kerala -> Alleppey (accepted)');
}

// Test 4: Rajasthan, India -> Jaipur (regional hub city match)
{
  const canonicalDest = 'Rajasthan, India';
  const planDest = 'Jaipur';
  const mockResponse = {
    plan_json: { destination: 'Jaipur', destination_airport: 'JAI' },
    trip_constraints: {
      destination_query: 'Rajasthan, India',
      destination_city: 'Jaipur',
      destination_airport: 'JAI',
      destination_country: 'India',
    },
    destination_airport: 'JAI',
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, true, 'Test 4 Failed: Rajasthan -> Jaipur must be accepted');
  console.log('✔ Test 4 Passed: Rajasthan -> Jaipur (accepted)');
}

// Test 5: Paris, France -> Paris (direct lexical match)
{
  const canonicalDest = 'Paris, France';
  const planDest = 'Paris';
  const mockResponse = {
    plan_json: { destination: 'Paris', destination_airport: 'CDG' },
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, true, 'Test 5 Failed: Paris -> Paris must be accepted');
  console.log('✔ Test 5 Passed: Paris -> Paris (accepted)');
}

// ---------------------------------------------------------------------------
// 2. Negative Cases (Must Be Strictly Rejected)
// ---------------------------------------------------------------------------

// Test 6: Kerala, India -> Paris (cross-country mismatch MUST FAIL)
{
  const canonicalDest = 'Kerala, India';
  const planDest = 'Paris';
  const mockResponse = {
    plan_json: { destination: 'Paris', destination_airport: 'CDG' },
    trip_constraints: {
      destination_query: 'Kerala, India',
      destination_city: 'Kochi',
      destination_airport: 'COK',
      destination_country: 'India',
    },
    destination_airport: 'COK',
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, false, 'Test 6 Failed: Kerala -> Paris must be rejected');
  console.log('✔ Test 6 Passed: Kerala -> Paris (rejected)');
}

// Test 7: Kerala, India -> Tokyo (cross-country mismatch MUST FAIL)
{
  const canonicalDest = 'Kerala, India';
  const planDest = 'Tokyo';
  const mockResponse = {
    plan_json: { destination: 'Tokyo', destination_airport: 'HND' },
    trip_constraints: {
      destination_query: 'Kerala, India',
      destination_city: 'Kochi',
      destination_airport: 'COK',
      destination_country: 'India',
    },
    destination_airport: 'COK',
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, false, 'Test 7 Failed: Kerala -> Tokyo must be rejected');
  console.log('✔ Test 7 Passed: Kerala -> Tokyo (rejected)');
}

// Test 8: Paris, France -> London (cross-city/cross-country mismatch MUST FAIL)
{
  const canonicalDest = 'Paris, France';
  const planDest = 'London';
  const mockResponse = {
    plan_json: { destination: 'London', destination_airport: 'LHR' },
    trip_constraints: {
      destination_query: 'Paris, France',
      destination_city: 'Paris',
      destination_airport: 'CDG',
      destination_country: 'France',
    },
    destination_airport: 'CDG',
  };

  const isConsistent = validateDestinationConsistency(planDest, canonicalDest, mockResponse);
  assert.strictEqual(isConsistent, false, 'Test 8 Failed: Paris -> London must be rejected');
  console.log('✔ Test 8 Passed: Paris -> London (rejected)');
}

console.log('\nAll 8 Destination Consistency Tests Passed Successfully!\n');
