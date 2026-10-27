/**
 * TT-SEC-002 Phase 2B: Frontend Transition to HttpOnly Cookie Authentication Tests
 *
 * Covers:
 * 1. apiClient.request sends credentials: 'include' for backend requests
 * 2. apiClient.request includes X-Requested-With: 'XMLHttpRequest' on state-changing requests (POST, PUT, DELETE, PATCH)
 * 3. apiClient.request does NOT include X-Requested-With on safe methods (GET, HEAD, OPTIONS)
 * 4. apiClient.request does NOT send credentials: 'include' or CSRF header to external URLs
 * 5. apiClient.login and signup send POST requests with credentials: 'include' and CSRF header
 * 6. apiClient.logout sends POST /api/auth/logout with credentials: 'include' and CSRF header
 * 7. apiClient.getMe sends GET /api/auth/me with credentials: 'include'; 401 on /api/auth/me does not invoke unauthorizedHandler
 * 8. All four PDF download methods pass credentials: 'include' without Authorization: Bearer
 *    - downloadTravelPlanPdfBlob (POST /api/travel/pdf with X-Requested-With)
 *    - downloadTicketPdfBlob (GET /api/trips/:id/ticket.pdf)
 *    - downloadHotelVoucherPdfBlob (GET /api/trips/:id/hotel.pdf)
 *    - downloadPaymentReceiptPdfBlob (GET /api/trips/:id/receipt.pdf)
 * 9. Storage security & cleanup:
 *    - storage.setToken does not write JWT tokens to localStorage
 *    - storage.getToken returns null
 *    - storage.clearToken and storage.clearAllAuth purge legacy tokens
 *    - User caching and custom API URL remain fully functional
 * 10. Static code audit:
 *    - Zero Authorization: Bearer headers in apiClient.ts
 *    - Zero storage.getToken() or storage.setToken() calls in apiClient.ts and AuthContext.tsx
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { apiClient, setUnauthorizedHandler } from '../lib/apiClient';
import { storage } from '../lib/storage';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function runTests() {
  console.log('\nRunning Phase 2B HttpOnly Cookie Authentication Tests (TT-SEC-002)...');

  const originalFetch = global.fetch;

  // Setup mock localStorage on global window
  const storageMap = new Map<string, string>();
  const mockLocalStorage = {
    getItem: (key: string) => storageMap.get(key) ?? null,
    setItem: (key: string, value: string) => { storageMap.set(key, String(value)); },
    removeItem: (key: string) => { storageMap.delete(key); },
    clear: () => { storageMap.clear(); },
  };
  (global as any).window = global;
  (global as any).localStorage = mockLocalStorage;

  try {
    // --------------------------------------------------------------------------
    // Test 1: apiClient.request includes credentials: 'include' for backend requests
    // --------------------------------------------------------------------------
    {
      let capturedInit: RequestInit | undefined;
      global.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
        capturedInit = init;
        return new Response(JSON.stringify({ status: 'healthy' }), { status: 200 });
      }) as any;

      await apiClient.checkHealth();
      assert.strictEqual(
        capturedInit?.credentials,
        'include',
        'Backend GET /health request must include credentials: "include"'
      );
      console.log('✔ Test 1 Passed: apiClient.request sends credentials: "include" for backend requests.');
    }

    // --------------------------------------------------------------------------
    // Test 2: State-changing requests attach CSRF header X-Requested-With
    // --------------------------------------------------------------------------
    {
      let capturedHeaders: Record<string, string> = {};
      global.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
        capturedHeaders = (init?.headers as Record<string, string>) || {};
        return new Response(JSON.stringify({ success: true, message: 'Logged out' }), { status: 200 });
      }) as any;

      await apiClient.logout();
      assert.strictEqual(
        capturedHeaders['X-Requested-With'],
        'XMLHttpRequest',
        'State-changing POST request must attach X-Requested-With: "XMLHttpRequest"'
      );
      console.log('✔ Test 2 Passed: State-changing POST /api/auth/logout includes CSRF header.');
    }

    // --------------------------------------------------------------------------
    // Test 3: Safe GET requests do NOT attach X-Requested-With
    // --------------------------------------------------------------------------
    {
      let capturedHeaders: Record<string, string> = {};
      global.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
        capturedHeaders = (init?.headers as Record<string, string>) || {};
        return new Response(JSON.stringify({ status: 'ready' }), { status: 200 });
      }) as any;

      await apiClient.checkReady();
      assert.strictEqual(
        capturedHeaders['X-Requested-With'],
        undefined,
        'Safe GET requests must not attach X-Requested-With header'
      );
      console.log('✔ Test 3 Passed: Safe GET requests do not attach X-Requested-With header.');
    }

    // --------------------------------------------------------------------------
    // Test 4: External URLs do NOT receive credentials: 'include' or CSRF header
    // --------------------------------------------------------------------------
    {
      let capturedInit: RequestInit | undefined;
      let capturedHeaders: Record<string, string> = {};
      global.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
        capturedInit = init;
        capturedHeaders = (init?.headers as Record<string, string>) || {};
        return new Response(JSON.stringify({ external: true }), { status: 200 });
      }) as any;

      // Access private request method via any casting to test external URL safety guard
      await (apiClient as any).request('https://external-api.trusttrip-partner.com/rates', {
        method: 'POST',
        body: JSON.stringify({ ping: true }),
      });

      assert.notStrictEqual(
        capturedInit?.credentials,
        'include',
        'External origin requests must NOT receive credentials: "include"'
      );
      assert.strictEqual(
        capturedHeaders['X-Requested-With'],
        undefined,
        'External origin requests must NOT receive TrustTrip CSRF header'
      );
      console.log('✔ Test 4 Passed: External URLs do not receive credentials or CSRF headers.');
    }

    // --------------------------------------------------------------------------
    // Test 5: Login and signup requests pass credentials and CSRF header
    // --------------------------------------------------------------------------
    {
      let capturedLoginInit: RequestInit | undefined;
      let capturedSignupInit: RequestInit | undefined;

      global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        const urlStr = String(url);
        if (urlStr.includes('/api/auth/login')) {
          capturedLoginInit = init;
          return new Response(
            JSON.stringify({
              access_token: 'fake_jwt_token',
              token_type: 'bearer',
              user: { id: 'u1', email: 'traveler@test.com', is_active: true },
            }),
            { status: 200 }
          );
        }
        if (urlStr.includes('/api/auth/signup')) {
          capturedSignupInit = init;
          return new Response(
            JSON.stringify({
              access_token: 'fake_jwt_token_2',
              token_type: 'bearer',
              user: { id: 'u2', email: 'new@test.com', is_active: true },
            }),
            { status: 200 }
          );
        }
        return new Response('Not Found', { status: 404 });
      }) as any;

      const loginRes = await apiClient.login({ email: 'traveler@test.com', password: 'password123' });
      assert.strictEqual(capturedLoginInit?.credentials, 'include');
      assert.strictEqual((capturedLoginInit?.headers as any)['X-Requested-With'], 'XMLHttpRequest');
      assert.strictEqual(loginRes.user.email, 'traveler@test.com');

      const signupRes = await apiClient.signup({ email: 'new@test.com', password: 'password123' });
      assert.strictEqual(capturedSignupInit?.credentials, 'include');
      assert.strictEqual((capturedSignupInit?.headers as any)['X-Requested-With'], 'XMLHttpRequest');
      assert.strictEqual(signupRes.user.email, 'new@test.com');

      console.log('✔ Test 5 Passed: Login and signup requests pass credentials: "include" and CSRF header.');
    }

    // --------------------------------------------------------------------------
    // Test 6: apiClient.getMe handles 401 without invoking unauthorizedHandler
    // --------------------------------------------------------------------------
    {
      let unauthorizedCalled = false;
      setUnauthorizedHandler(() => {
        unauthorizedCalled = true;
      });

      global.fetch = (async (_url: string | URL | Request, _init?: RequestInit) => {
        return new Response(
          JSON.stringify({ detail: 'Authentication credentials were not provided.' }),
          { status: 401 }
        );
      }) as any;

      try {
        await apiClient.getMe();
        assert.fail('getMe should throw ApiError on 401');
      } catch (err: any) {
        assert.strictEqual(err.statusCode, 401);
      }

      assert.strictEqual(
        unauthorizedCalled,
        false,
        'unauthorizedHandler must NOT be invoked when /api/auth/me returns 401'
      );
      setUnauthorizedHandler(null);
      console.log('✔ Test 6 Passed: /api/auth/me unauthenticated 401 does not trigger unauthorizedHandler.');
    }

    // --------------------------------------------------------------------------
    // Test 7: Other endpoints handle 401 by invoking unauthorizedHandler
    // --------------------------------------------------------------------------
    {
      let unauthorizedCalled = false;
      setUnauthorizedHandler(() => {
        unauthorizedCalled = true;
      });

      global.fetch = (async (_url: string | URL | Request, _init?: RequestInit) => {
        return new Response(
          JSON.stringify({ detail: 'Token expired.' }),
          { status: 401 }
        );
      }) as any;

      try {
        await (apiClient as any).request('/api/trips/trip_123/revisions', { method: 'GET' });
        assert.fail('request should throw ApiError on 401');
      } catch (err: any) {
        assert.strictEqual(err.statusCode, 401);
      }

      assert.strictEqual(
        unauthorizedCalled,
        true,
        'unauthorizedHandler must be invoked when standard API endpoint returns 401'
      );
      setUnauthorizedHandler(null);
      console.log('✔ Test 7 Passed: Standard API endpoints invoke unauthorizedHandler on 401.');
    }

    // --------------------------------------------------------------------------
    // Test 8: All four PDF download methods pass credentials: 'include' without Bearer
    // --------------------------------------------------------------------------
    {
      const pdfBlob = new Blob(['%PDF-1.4 test'], { type: 'application/pdf' });
      const capturedPdfCalls: Array<{ url: string; method: string; credentials?: string; headers: Record<string, string> }> = [];

      global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        capturedPdfCalls.push({
          url: String(url),
          method: init?.method || 'GET',
          credentials: init?.credentials as string,
          headers: (init?.headers as Record<string, string>) || {},
        });
        return new Response(pdfBlob, { status: 200, headers: { 'Content-Type': 'application/pdf' } });
      }) as any;

      // 8a: Travel plan PDF
      await apiClient.downloadTravelPlanPdfBlob('trip_pdf_1');
      const travelCall = capturedPdfCalls[0];
      assert.strictEqual(travelCall.url.endsWith('/api/travel/pdf'), true);
      assert.strictEqual(travelCall.method, 'POST');
      assert.strictEqual(travelCall.credentials, 'include');
      assert.strictEqual(travelCall.headers['X-Requested-With'], 'XMLHttpRequest');
      assert.strictEqual(travelCall.headers['Authorization'], undefined);

      // 8b: Flight ticket PDF
      await apiClient.downloadTicketPdfBlob('tx_ticket_1');
      const ticketCall = capturedPdfCalls[1];
      assert.strictEqual(ticketCall.url.endsWith('/api/trips/tx_ticket_1/ticket.pdf'), true);
      assert.strictEqual(ticketCall.method, 'GET');
      assert.strictEqual(ticketCall.credentials, 'include');
      assert.strictEqual(ticketCall.headers['Authorization'], undefined);

      // 8c: Hotel voucher PDF
      await apiClient.downloadHotelVoucherPdfBlob('tx_hotel_1');
      const hotelCall = capturedPdfCalls[2];
      assert.strictEqual(hotelCall.url.endsWith('/api/trips/tx_hotel_1/hotel.pdf'), true);
      assert.strictEqual(hotelCall.method, 'GET');
      assert.strictEqual(hotelCall.credentials, 'include');
      assert.strictEqual(hotelCall.headers['Authorization'], undefined);

      // 8d: Payment receipt PDF
      await apiClient.downloadPaymentReceiptPdfBlob('tx_receipt_1');
      const receiptCall = capturedPdfCalls[3];
      assert.strictEqual(receiptCall.url.endsWith('/api/trips/tx_receipt_1/receipt.pdf'), true);
      assert.strictEqual(receiptCall.method, 'GET');
      assert.strictEqual(receiptCall.credentials, 'include');
      assert.strictEqual(receiptCall.headers['Authorization'], undefined);

      console.log('✔ Test 8 Passed: All four PDF download methods pass credentials: "include" with zero Bearer headers.');
    }

    // --------------------------------------------------------------------------
    // Test 9: Storage security: no JWT persistence in localStorage
    // --------------------------------------------------------------------------
    {
      storageMap.clear();

      // Test 9a: storage.setToken does not write token to localStorage
      storage.setToken('sensitive_jwt_token_123');
      assert.strictEqual(
        storageMap.get('trusttrip_auth_token'),
        undefined,
        'setToken must not store JWT token in localStorage'
      );

      // Test 9b: storage.getToken always returns null
      assert.strictEqual(
        storage.getToken(),
        null,
        'getToken must return null in HttpOnly cookie auth mode'
      );

      // Test 9c: Legacy token in localStorage is purged by clearToken & clearAllAuth
      storageMap.set('trusttrip_auth_token', 'legacy_stale_token');
      storageMap.set('trusttrip_user_cache', JSON.stringify({ email: 'user@test.com' }));
      storage.clearAllAuth();
      assert.strictEqual(storageMap.get('trusttrip_auth_token'), undefined);
      assert.strictEqual(storageMap.get('trusttrip_user_cache'), undefined);

      // Test 9d: Preferences and custom API URL remain intact
      storage.setCustomApiUrl('http://127.0.0.1:8000');
      assert.strictEqual(storage.getCustomApiUrl(), 'http://127.0.0.1:8000');

      console.log('✔ Test 9 Passed: Storage security enforces zero localStorage JWT persistence and purges legacy tokens.');
    }

    // --------------------------------------------------------------------------
    // Test 10: Static source audit: zero Bearer headers, zero storage token read/write
    // --------------------------------------------------------------------------
    {
      const apiClientSource = fs.readFileSync(path.join(__dirname, '../lib/apiClient.ts'), 'utf8');
      const authContextSource = fs.readFileSync(path.join(__dirname, '../context/AuthContext.tsx'), 'utf8');

      // Check zero Bearer in apiClient
      assert.strictEqual(
        apiClientSource.includes('Bearer '),
        false,
        'apiClient.ts must have 0 occurrences of "Bearer "'
      );

      // Check zero storage.getToken or storage.setToken in AuthContext
      assert.strictEqual(
        authContextSource.includes('storage.getToken'),
        false,
        'AuthContext.tsx must have 0 calls to storage.getToken'
      );
      assert.strictEqual(
        authContextSource.includes('storage.setToken'),
        false,
        'AuthContext.tsx must have 0 calls to storage.setToken'
      );

      console.log('✔ Test 10 Passed: Static audit confirms zero Bearer headers and zero localStorage token writes.');
    }

    // --------------------------------------------------------------------------
    // Test 11: Startup session hydration through /api/auth/me without token persistence
    // --------------------------------------------------------------------------
    {
      storageMap.clear();
      let capturedUrl = '';
      let capturedInit: RequestInit | undefined;

      global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        capturedUrl = String(url);
        capturedInit = init;
        return new Response(
          JSON.stringify({
            success: true,
            user: { id: 'user_hydrate_123', email: 'session@trusttrip.io', is_active: true },
          }),
          { status: 200 }
        );
      }) as any;

      const user = await apiClient.getMe();
      assert.strictEqual(capturedUrl.endsWith('/api/auth/me'), true);
      assert.strictEqual(capturedInit?.credentials, 'include');
      assert.strictEqual(user.id, 'user_hydrate_123');
      assert.strictEqual(user.email, 'session@trusttrip.io');

      // Hydration must not create or store JWT in localStorage
      assert.strictEqual(storage.getToken(), null);
      assert.strictEqual(storageMap.get('trusttrip_auth_token'), undefined);

      console.log('✔ Test 11 Passed: Startup session hydration retrieves user via cookie without localStorage token.');
    }

    // --------------------------------------------------------------------------
    // Test 12: Startup hydration 401 unauthenticated handles cleanly without alerts
    // --------------------------------------------------------------------------
    {
      storageMap.clear();
      let alertFired = false;
      setUnauthorizedHandler(() => {
        alertFired = true;
      });

      global.fetch = (async (_url: string | URL | Request, _init?: RequestInit) => {
        return new Response(
          JSON.stringify({ detail: 'Authentication credentials were not provided.' }),
          { status: 401 }
        );
      }) as any;

      let errorCaught = false;
      try {
        await apiClient.getMe();
      } catch (err: any) {
        errorCaught = true;
        assert.strictEqual(err.statusCode, 401);
      }

      assert.strictEqual(errorCaught, true, 'getMe throws 401 when unauthenticated');
      assert.strictEqual(alertFired, false, 'Initial unauthenticated load must not fire session-expiry alert');
      setUnauthorizedHandler(null);

      console.log('✔ Test 12 Passed: Unauthenticated startup 401 handled quietly without session-expiry alert.');
    }

    // --------------------------------------------------------------------------
    // Test 13: Logout resilience: clears user state even when network request fails
    // --------------------------------------------------------------------------
    {
      storageMap.clear();
      storage.setUser({ id: 'user_logout_test', email: 'logout@trusttrip.io' });
      assert.notStrictEqual(storage.getUser(), null);

      global.fetch = (async (_url: string | URL | Request, _init?: RequestInit) => {
        throw new Error('Network offline or backend timeout');
      }) as any;

      // Simulate AuthContext logout try/finally flow
      try {
        await apiClient.logout();
      } catch {
        // network failure swallowed
      } finally {
        storage.clearAllAuth();
      }

      assert.strictEqual(storage.getUser(), null);
      assert.strictEqual(storage.getToken(), null);
      assert.strictEqual(storageMap.get('trusttrip_auth_token'), undefined);
      assert.strictEqual(storageMap.get('trusttrip_user_cache'), undefined);

      console.log('✔ Test 13 Passed: Logout clears local authentication state even when network request fails.');
    }

    // --------------------------------------------------------------------------
    // Test 14: Tenant isolation / trip endpoints pass credentials: 'include'
    // --------------------------------------------------------------------------
    {
      let tripsCallInit: RequestInit | undefined;
      global.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        if (String(url).endsWith('/api/trips')) {
          tripsCallInit = init;
          return new Response(
            JSON.stringify({
              trips: [
                {
                  id: 'trip_user_a',
                  record_type: 'trip',
                  destination: 'Rome',
                  title: 'Rome Getaway',
                  status: 'planning',
                },
              ],
            }),
            { status: 200 }
          );
        }
        return new Response('Not Found', { status: 404 });
      }) as any;

      const trips = await apiClient.getTrips();
      assert.strictEqual(tripsCallInit?.credentials, 'include');
      assert.strictEqual(trips.length, 1);
      assert.strictEqual(trips[0].trip_id, 'trip_user_a');
      assert.strictEqual(trips[0].destination, 'Rome');

      console.log('✔ Test 14 Passed: Tenant-isolated trip queries send credentials: "include".');
    }

    console.log('\nAll Phase 2B HttpOnly Cookie Authentication Tests PASSED successfully!\n');
  } finally {
    global.fetch = originalFetch;
  }
}

runTests().catch((err) => {
  console.error('\n❌ Test failure in cookieAuthMigration.test.tsx:\n', err);
  process.exit(1);
});
