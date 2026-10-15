# TrustTrip — Current Status & Milestone Tracking

**Date:** 2026-10-04  
**Current Milestone:** Post-Wave-2 Verified Baseline  

---

## 1. Completed Waves

### Wave 1: Canonical Plan → Proposed Revision + Airport Identity (COMPLETED & FROZEN)
- **Contract:**
  1. Initial generated itinerary becomes authoritative Revision #1.
  2. Revision #1 persisted in `EvidenceRevisionStore` (`plan_revisions` table).
  3. Revision #1 has real UUID and status `PROPOSED`.
  4. Replanning monotonically creates Revision N+1 (`PROPOSED`).
  5. Approved revisions proceed directly to Section 9 booking authorization.
  6. Natural-language destinations resolve to commercial gateway airports (e.g. Darjeeling -> IXB, Kolkata -> CCU).
  7. Human-readable text preserved in `trip.destination`.
  8. Canonical IATA stored in `trip.metadata["destination_airport"]` and constraints snapshot.
  9. Stale revision protection enforced.
  10. Booking authorization blocked for unapproved revisions.

### Wave 2: Real Traveler Identity & Passenger Propagation (REMEDIATED & FROZEN)
- **Contract:**
  1. Decoupled authenticated account user from traveler identity (users can book for themselves, others, or groups).
  2. Production traveler entry workflow (`TravelerModal.tsx`) in `BookingWorkspace.tsx` supporting 1 <= N <= 9 passengers based on `trip.travelers_count`.
  3. Removed all hardcoded/synthetic passenger literals ("Ananya Roy", "traveler@trusttrip.com", etc.) from production execution.
  4. Explicit traveler email takes precedence; authenticated email only used as initial default contact email when empty.
  5. Strict validation (fail-closed) on missing fields and passenger count mismatch against `trip.travelers_count`.
  6. Stale flight booking intent invalidated upon traveler info modification.
  7. Traveler array propagated to flight booking/verification (Duffel Path A) and hotel payment (`holder` + `guests`).
  8. Wave 1 contracts remain frozen and intact; Razorpay Path B remains unwired/unmerged.
  9. Audit findings closed: W2-FIND-01 (cancellation email), W2-FIND-02 (hotel prebook lead guest), W2-FIND-03 (unused mock constant).

---

## 2. Frozen Components
Do NOT rewrite or alter without explicit regression evidence:
- `services/trip_store.py:create_revision_atomic()`
- `services/evidence_store.py` (Revision and approval persistence)
- `services/authorization_store.py:assert_booking_eligible()` (10 Section 9 guards)
- `services/travel_planner.py:_resolve_place()` (Gateway airport catalog)
- `frontend/src/lib/apiClient.ts:normalizeBackendTripDetail()` (No synthetic ID fallback)
- `services/flight_booking_service.py` (Canonical Duffel Path A + passenger count validation)

---

## 3. Currently Passing Test Suites

### Backend Test Suites (`pytest`) — 812 Passed, 0 Failed (100% pass rate):
- `tests/test_wave2_traveler_identity.py`: 9/9 passed
- `tests/test_wave1_canonical_plan_revisions.py`: 10/10 passed
- `tests/test_section8_hitl_revisions.py`: 32/32 passed
- `tests/test_travelplan_date_canonical_consistency.py`: 9/9 passed
- `tests/test_destination_resolver_safeties.py`: 10/10 passed
- Complete repository test suite: 812 passed

### Frontend Test Suites (`npm test`) — 100% Passed:
- `wave2TravelerIdentity.test.tsx`: 7/7 passed
- Section 7 Research contract tests
- Section 8 HITL revisions contract tests & Remediation R tests
- Section 9 Booking authorization contract tests & Requirement 3/4 tests
- Section 10 Hotel payment contract tests (21 tests)
- TripDetailView hook order regression tests
- Flight dual-currency display tests
- Trip constraint & budget persistence tests
- Offer selection normalization tests
- Frontend TypeScript lint (`tsc --noEmit`): 0 errors

   - Need alignment on whether flight booking in sandbox should go through Razorpay payment verification or direct supplier booking.
3. **Currency Sanitization (Wave 3):**
   - Ensure formatted currency strings (e.g. `"₹12,000"`) cannot leak into numeric amount fields in API payloads.

---

## 5. Known Risks
- **Concurrency in Rapid Clicks:** Multiple quick clicks on "Authorize Booking" or "Pay" could issue parallel requests; idempotency keys mitigate this on backend, but UI buttons should disable while loading.
- **Provider Currency Mismatch:** Duffel offers priced in GBP/USD will fail `_validate_and_convert_offer` if frontend sends `expected_currency="INR"`.

---

## 6. Next Proposed Investigation
- Detailed audit of the passenger data pipeline from `AuthContext` -> `BookingWorkspace` -> `bookingService.ts`.
- Verification of currency conversion handoff between Duffel provider currency and Razorpay checkout.
