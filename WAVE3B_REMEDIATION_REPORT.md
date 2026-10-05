# TrustTrip — Wave 3B Surgical Remediation Report

**Date:** 2026-10-05  
**Remediation Target:** `FIND-W3B-01` (Severity: P2 — Medium)  
**Status:** Complete  

---

## 1. Finding

```text
FIND-W3B-01: Incorrect Aliasing of booking_reference as PNR
Severity: P2 — Medium
Component: services/flight_booking_service.py
```

During the Wave 3B independent implementation audit, an identifier semantics defect was identified where `pnr` was populated with `booking_reference` as a fallback when absent in upstream provider responses, and `get_booking()` queried `booking_ref` instead of `meta.get("pnr")`.

---

## 2. Root Cause

In [services/flight_booking_service.py](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py):
1. In `confirm_booking`:
   ```python
   # Defective lines:
   pnr_val = booking_res.get("pnr") or str(booking_ref)
   completed_intent["metadata"]["pnr"] = str(pnr_val) if pnr_val else str(booking_ref)
   return FlightBookingConfirmationResponse(
       ...
       pnr=str(pnr_val) if pnr_val else str(booking_ref),
   )
   ```
2. In `get_booking`:
   ```python
   # Defective lines:
   booking_ref = intent.get("provider_reference") or meta.get("booking_reference") or meta.get("pnr")
   "pnr": str(booking_ref) if booking_ref else None,
   ```

This conflated airline booking references (e.g. Duffel order references or locator IDs) with Passenger Name Records (PNR). Under strict booking truthfulness standards, a PNR must only exist when the upstream airline provider explicitly provides a PNR. Substituting `booking_reference` for `pnr` caused synthetic PNR values to be propagated and stored.

---

## 3. Changes Made

### File: `services/flight_booking_service.py`

1. **`confirm_booking` (Line 805)**:
   - Changed: `pnr_val = booking_res.get("pnr") or str(booking_ref)`
   - To: `pnr_val = booking_res.get("pnr")`
2. **`confirm_booking` Intent Metadata Persistence (Line 838)**:
   - Changed: `completed_intent["metadata"]["pnr"] = str(pnr_val) if pnr_val else str(booking_ref)`
   - To: `completed_intent["metadata"]["pnr"] = str(pnr_val) if pnr_val else None`
3. **`confirm_booking` Response Object (Line 868)**:
   - Changed: `pnr=str(pnr_val) if pnr_val else str(booking_ref)`
   - To: `pnr=str(pnr_val) if pnr_val else None`
4. **`get_booking` Status Query (Line 880)**:
   - Changed: `booking_ref = intent.get("provider_reference") or meta.get("booking_reference") or meta.get("pnr")`
   - To: `booking_ref = intent.get("provider_reference") or meta.get("booking_reference")`
   - Changed: `"pnr": str(booking_ref) if booking_ref else None`
   - To: `"pnr": meta.get("pnr")`

### File: `tests/test_wave3b_booking_persistence.py`

- In `test_duffel_booking_normalization_and_metadata_persistence`:
  - Updated assertion on `fake_duffel_res` (which provides only `booking_reference: "G4XYZ8"` and no `pnr`) from `assert res.pnr == "G4XYZ8"` to truthful assertion:
    ```python
    assert res.booking_reference == "G4XYZ8"
    assert res.pnr is None
    assert meta["booking_reference"] == "G4XYZ8"
    assert meta["pnr"] is None
    assert booking_record["booking_reference"] == "G4XYZ8"
    assert booking_record["pnr"] is None
    ```

---

## 4. Correct Semantics

`booking_reference` and `pnr` are now strictly independent and truthful:

| Provider Response Payload | Normalized `booking_reference` | Normalized `pnr` | Behavior Verified |
|---|---|---|---|
| `{"booking_reference": "REF-123"}` | `"REF-123"` | `None` | Verified (`test_booking_result_truthful_missing_identifiers_not_fabricated`) |
| `{"booking_reference": "TT-E2E-7788", "pnr": "PNR-AIR-E2E"}` | `"TT-E2E-7788"` | `"PNR-AIR-E2E"` | Verified (`test_e2e_flight_booking_happy_path`) |
| `{"pnr": "PNR-1"}` | Provider reference fallback (e.g. `"CONFIRMED"`) | `"PNR-1"` | Verified |
| `{}` | Valid fallback reference | `None` | Verified |

- **Booking Reference:** Sourced truthfully from provider reference identifiers.
- **PNR:** Sourced ONLY from `booking_res.get("pnr")`. When absent from provider, it evaluates and persists strictly as `None`. It is never overwritten by or substituted with `booking_reference`.

---

## 5. Tests & Verification

### Targeted Tests (Previously Failing)
```bash
./venv/python.exe -m pytest \
tests/test_fast_track_frontend_e2e.py::test_booking_result_truthful_missing_identifiers_not_fabricated \
tests/test_fast_track_frontend_e2e.py::test_e2e_flight_booking_happy_path \
-v
```
**Result:**
```text
tests/test_fast_track_frontend_e2e.py::test_booking_result_truthful_missing_identifiers_not_fabricated PASSED [ 50%]
tests/test_fast_track_frontend_e2e.py::test_e2e_flight_booking_happy_path PASSED [100%]
======================== 2 passed, 5 warnings in 7.00s ========================
```

### Wave 3B Regression
```bash
./venv/python.exe -m pytest tests/test_wave3b_booking_persistence.py -v
```
**Result:**
```text
collected 9 items
tests/test_wave3b_booking_persistence.py::test_duffel_booking_normalization_and_metadata_persistence PASSED
tests/test_wave3b_booking_persistence.py::test_booking_read_service_aggregation_and_tenant_isolation PASSED
tests/test_wave3b_booking_persistence.py::test_booking_read_service_partial_and_in_progress_states PASSED
tests/test_wave3b_booking_persistence.py::test_rehydration_endpoint_auth_and_ownership PASSED
tests/test_wave3b_booking_persistence.py::test_rehydration_endpoint_returns_confirmed_bookings PASSED
tests/test_wave3b_booking_persistence.py::test_my_trips_lists_canonical_flight_and_hotel_bookings PASSED
tests/test_wave3b_booking_persistence.py::test_ticket_download_from_booking_intents_with_provider_offline PASSED
tests/test_wave3b_booking_persistence.py::test_ticket_download_cross_user_forbidden PASSED
tests/test_wave3b_booking_persistence.py::test_ticket_download_not_found PASSED
======================= 9 passed, 13 warnings in 5.91s ========================
```

### Wave 1, Wave 2, and Wave 3A Regressions
```bash
./venv/python.exe -m pytest \
tests/test_wave3a_hotel_prebook_payment.py \
tests/test_wave2_traveler_identity.py \
tests/test_wave1_canonical_plan_revisions.py
```
**Result:**
```text
collected 29 items
tests/test_wave3a_hotel_prebook_payment.py ..........                    [ 34%]
tests/test_wave2_traveler_identity.py .........                          [ 65%]
tests/test_wave1_canonical_plan_revisions.py ..........                  [100%]
======================= 29 passed, 56 warnings in 7.66s =======================
```

### Full Backend Regression
```bash
./venv/python.exe -m pytest
```
**Result:**
```text
================== 831 passed, 1 warning in 79.24s (0:01:19) ==================
```
- Total test items: 831
- Passed: 831
- Failed: 0
- Failures attributable to `FIND-W3B-01`: 0 (eliminated)

### Frontend Regression & Lint
```bash
npm --prefix frontend run lint
npm --prefix frontend test
```
**Result:**
- **TypeScript (`tsc --noEmit` via `npm run lint`):** 0 errors (exited 0)
- **Frontend Test Suite (`npm test`):** 12 suites, all tests passed (exited 0)

---

## 6. Scope Verification

- [x] **No Wave 3C work:** No cancellation, refund, or external sync workflows introduced.
- [x] **No database schema changes or migrations:** Zero migrations created or altered.
- [x] **No payment changes:** Razorpay and prebook workflows untouched.
- [x] **No frontend architecture changes:** Zero frontend production code modified during remediation.
- [x] **No booking architecture redesign:** Hand-off, confirmation state transitions, and `BookingReadService` remained intact.
- [x] **Git Safety:** Zero destructive commands executed (`git reset`, `git clean`, `git restore` were strictly avoided).

---

## 7. Final Remediation Decision

```text
WAVE 3B — REMEDIATION COMPLETE, READY FOR FINAL INDEPENDENT RE-AUDIT
```
