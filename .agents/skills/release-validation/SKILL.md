---
name: release-validation
description: Comprehensive verification of backend, frontend, and end-to-end business flows before release.
---

# TrustTrip Release Validation Skill

Use this workflow to validate that all layers of the TrustTrip application are functioning properly before claiming a milestone or release is complete.

## 1. Backend Validation
- **Health & Readiness:** Verify `GET /api/auth/me` and database connectivity (`probe_db()`).
- **Migrations:** Ensure all migrations in `migrations/*.sql` apply cleanly via `run_migrations()`.
- **Pytest:** Run complete test suite:
  ```powershell
  .\venv\python.exe -m pytest tests/
  ```
  *PASS requirement: 0 failed tests.*
- **API Contracts:** Verify Pydantic validation passes without 422 or 500 errors on standard payloads.

## 2. Frontend Validation
- **Type Checking:** Run TypeScript compiler check:
  ```powershell
  npm run typecheck # or npx tsc --noEmit
  ```
- **Vitest Unit & Contract Tests:** Run all frontend test suites:
  ```powershell
  npm test -- --run
  ```
  *PASS requirement: 100% test suites pass.*
- **Production Bundle:** Test compilation without syntax or module errors:
  ```powershell
  npm run build
  ```

## 3. End-to-End Business Flow Validation
Runtime evidence must be verified for the complete sequence:
1. **Signup / Login:** Authentication yields valid JWT and loads user profile.
2. **Create Trip:** `POST /api/trips` creates trip in `planning` state.
3. **Generate Plan:** `POST /api/travel` generates itinerary, writes `Revision #1 (PROPOSED)` to `EvidenceRevisionStore`, and populates `destination_airport`.
4. **Revision Approval:** `POST /api/trips/{id}/approve` approves Revision #1.
5. **Flight Search & Selection:** Live search returns offers; selection stores `selected_flight_snapshot_id`.
6. **Hotel Search & Selection:** Live hotel search returns offers; selection stores hotel snapshot.
7. **Booking Authorization:** `POST /api/trips/{id}/booking-approval` validates Section 9 eligibility.
8. **Payment & Confirmation:** Payment order creation, signature verification, and supplier booking record truthful confirmations.

> **CRITICAL RULE:** PASS means runtime evidence exists. Compilation alone is NOT a pass.
