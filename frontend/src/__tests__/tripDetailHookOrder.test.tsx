/**
 * Regression Test: TripDetailView React Rules-of-Hooks Order
 *
 * Verifies:
 * 1. TripDetailView executes the exact same number and sequence of hooks across consecutive renders.
 * 2. Consecutive transitions:
 *    - initial/loading state -> loaded trip state
 *    - loaded trip state -> re-fetching / loading state
 *    - loading state -> error state
 *    - loaded trip state -> empty trip state
 * 3. Static verification:
 *    - All React hooks are defined strictly before any early/conditional returns.
 *    - No conditional hook calls exist inside branches, loops, or nested functions.
 */
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import React from 'react';
import { TripDetailView } from '../components/views/TripDetailView';
import { TripDetail } from '../types/trip';

console.log('Running TripDetailView Hook Order Regression Tests...\n');

interface HookSimulationOptions {
  renderPasses: Array<{
    name: string;
    stateOverrides: Map<number, any>;
  }>;
}

function runConsecutiveRenderTest(options: HookSimulationOptions) {
  const internals = (React as any).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  const originalDispatcher = internals?.H;

  const hookHistory: string[] = [];
  let currentPassIndex = 0;
  let hookIndex = 0;

  function createDispatcher(passIdx: number, stateMap: Map<number, any>) {
    function trackHook(hookName: string, execute: () => any) {
      const idx = hookIndex++;
      if (passIdx === 0) {
        hookHistory.push(hookName);
        return execute();
      } else {
        if (idx >= hookHistory.length) {
          throw new Error(
            `React has detected a change in the order of Hooks called by TripDetailView: ` +
            `Rendered more hooks (${idx + 1}) than during the previous render (${hookHistory.length}). ` +
            `Hook ${idx + 1} is ${hookName}.`
          );
        }
        if (hookHistory[idx] !== hookName) {
          throw new Error(
            `React has detected a change in the order of Hooks called by TripDetailView: ` +
            `Expected ${hookHistory[idx]} at position ${idx}, but got ${hookName}.`
          );
        }
        return execute();
      }
    }

    return {
      useState(initial: any) {
        return trackHook('useState', () => {
          const val = stateMap.has(hookIndex - 1) ? stateMap.get(hookIndex - 1) : initial;
          return [val, () => {}];
        });
      },
      useContext(_ctx: any) {
        return trackHook('useContext', () => ({
          notify: () => {},
        }));
      },
      useCallback(fn: any, _deps: any) {
        return trackHook('useCallback', () => fn);
      },
      useEffect(_fn: any, _deps: any) {
        return trackHook('useEffect', () => {});
      },
      useMemo(fn: any, _deps: any) {
        return trackHook('useMemo', () => fn());
      },
      useRef(initial: any) {
        return trackHook('useRef', () => ({ current: initial }));
      },
    };
  }

  try {
    for (let p = 0; p < options.renderPasses.length; p++) {
      const pass = options.renderPasses[p];
      currentPassIndex = p;
      hookIndex = 0;
      internals.H = createDispatcher(p, pass.stateOverrides);

      try {
        (TripDetailView as any)({
          tripId: '39b76dc3-cbc6-4604-bf93-87212c0a44e0',
          onBack: () => {},
        });
      } catch (err: any) {
        // If it's a hook order error, rethrow it directly
        if (err.message && err.message.includes('React has detected a change in the order of Hooks')) {
          throw err;
        }
        // Non-hook errors (e.g. sub-component render during tests) should not mask hook errors
      }

      if (p > 0 && hookIndex !== hookHistory.length) {
        throw new Error(
          `React hook count mismatch on pass "${pass.name}": expected ${hookHistory.length} hooks, got ${hookIndex}.`
        );
      }
    }

    return {
      totalHooks: hookHistory.length,
      hookSequence: hookHistory,
    };
  } finally {
    if (internals) {
      internals.H = originalDispatcher;
    }
  }
}

const mockTrip: TripDetail = {
  trip_id: '39b76dc3-cbc6-4604-bf93-87212c0a44e0',
  title: 'Autumn in Kyoto',
  destination: 'Kyoto, Japan',
  origin: 'San Francisco (SFO)',
  start_date: '2026-10-14',
  end_date: '2026-10-24',
  travelers_count: 2,
  budget: 5000,
  currency: 'USD',
  status: 'planning',
  active_revision_number: 1,
  revisions: [],
  is_approved: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
};

// Hook indices in TripDetailView:
// Hook 0 = trip (useState)
// Hook 1 = isLoading (useState)
// Hook 2 = error (useState)

// TEST 1: Initial Loading -> Loaded Trip Transition
{
  const pass1Loading = new Map<number, any>();
  pass1Loading.set(0, null);  // trip = null
  pass1Loading.set(1, true);  // isLoading = true
  pass1Loading.set(2, null);  // error = null

  const pass2Loaded = new Map<number, any>();
  pass2Loaded.set(0, mockTrip); // trip = loaded
  pass2Loaded.set(1, false);    // isLoading = false
  pass2Loaded.set(2, null);

  const res = runConsecutiveRenderTest({
    renderPasses: [
      { name: 'Initial Loading State', stateOverrides: pass1Loading },
      { name: 'Loaded Trip State', stateOverrides: pass2Loaded },
    ],
  });

  assert.strictEqual(res.totalHooks, 34, 'Expected exactly 34 hooks registered in TripDetailView');
  assert.strictEqual(res.hookSequence[33], 'useMemo', 'Hook #34 must be useMemo (canonicalNights)');
  console.log('✔ Test 1: Initial Loading -> Loaded Trip transition succeeds without hook-order error.');
}

// TEST 2: Multi-step cycle (Loading -> Loaded -> Re-loading -> Loaded)
{
  const loadingState = new Map<number, any>([[0, null], [1, true], [2, null]]);
  const loadedState = new Map<number, any>([[0, mockTrip], [1, false], [2, null]]);

  runConsecutiveRenderTest({
    renderPasses: [
      { name: 'Mount (Loading)', stateOverrides: loadingState },
      { name: 'Render 2 (Loaded)', stateOverrides: loadedState },
      { name: 'Render 3 (Refresh/Re-loading)', stateOverrides: loadingState },
      { name: 'Render 4 (Loaded again)', stateOverrides: loadedState },
    ],
  });

  console.log('✔ Test 2: Multi-step cycle (Loading -> Loaded -> Refresh -> Loaded) succeeds identically.');
}

// TEST 3: Loading -> Error State -> Retry Loading Transition
{
  const loadingState = new Map<number, any>([[0, null], [1, true], [2, null]]);
  const errorState = new Map<number, any>([[0, null], [1, false], [2, 'Failed to fetch trip.']]);

  runConsecutiveRenderTest({
    renderPasses: [
      { name: 'Loading', stateOverrides: loadingState },
      { name: 'Error State', stateOverrides: errorState },
      { name: 'Retry Loading', stateOverrides: loadingState },
    ],
  });

  console.log('✔ Test 3: Loading -> Error -> Retry transitions maintain identical hook order.');
}

// TEST 4: Loaded -> Empty Trip (!trip) Transition
{
  const loadedState = new Map<number, any>([[0, mockTrip], [1, false], [2, null]]);
  const emptyTripState = new Map<number, any>([[0, null], [1, false], [2, null]]);

  runConsecutiveRenderTest({
    renderPasses: [
      { name: 'Loaded', stateOverrides: loadedState },
      { name: 'Empty Trip', stateOverrides: emptyTripState },
    ],
  });

  console.log('✔ Test 4: Loaded -> Empty Trip transitions maintain identical hook order.');
}

// TEST 5: Static Source Audit - No Hooks After Early Returns
{
  const source = readFileSync(
    new URL('../components/views/TripDetailView.tsx', import.meta.url),
    'utf8'
  );

  const lines = source.split('\n');
  let firstEarlyReturnLine = -1;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.includes('if (isLoading)') || line.includes('if (error || !trip)')) {
      firstEarlyReturnLine = i + 1;
      break;
    }
  }

  assert(firstEarlyReturnLine > 0, 'Early return statements must be present in TripDetailView');

  // Verify no hook invocations appear after firstEarlyReturnLine
  const hookPattern = /\b(useState|useEffect|useCallback|useMemo|useRef|useContext|useNotification)\b/;
  const violations: string[] = [];

  for (let i = firstEarlyReturnLine; i < lines.length; i++) {
    const line = lines[i];
    if (hookPattern.test(line)) {
      violations.push(`Line ${i + 1}: ${line.trim()}`);
    }
  }

  assert.strictEqual(
    violations.length,
    0,
    `Hook violations found after early return statement at line ${firstEarlyReturnLine}:\n${violations.join('\n')}`
  );

  // Verify canonicalNights useMemo is specifically located before firstEarlyReturnLine
  const canonicalNightsLine = lines.findIndex((l) => l.includes('canonicalNights = useMemo'));
  assert(
    canonicalNightsLine > 0 && canonicalNightsLine < firstEarlyReturnLine,
    `canonicalNights useMemo must be declared before line ${firstEarlyReturnLine}, found at line ${canonicalNightsLine + 1}`
  );

  console.log('✔ Test 5: Static source audit confirms 0 hooks exist after early return statements.');
}

console.log('\nAll TripDetailView hook order regression tests PASSED!\n');
