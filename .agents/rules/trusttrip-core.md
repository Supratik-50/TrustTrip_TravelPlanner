# TrustTrip Core Engineering Rules

These rules govern all engineering work within the TrustTrip codebase.

## 1. Source-of-Truth Discipline
- **Authoritative Identity:** Every entity (trip, revision, offer snapshot, booking intent, transaction) must be identified exclusively by its authoritative backend identity (UUID or provider identifier).
- **Never Synthesize or Fabricate:** Never invent synthetic IDs (e.g. `rev_<id>_01`, `mock_pnr_123`, `order_fake`). If backend data is missing, fail closed or return empty arrays/undefined.
- **Single Authoritative Source for Revisions:** `EvidenceRevisionStore` (`plan_revisions` table) is the single authoritative source of revision identity and approval status.
- **Single Authoritative Source for Trips:** `TripStore` (`trips` table) is the single authoritative source for trip constraints and metadata.

## 2. Minimal Changes & Surgical Remediation
- **Target Root Causes Only:** Never patch downstream symptoms (e.g. adding fallback formatting or mock overrides) when an upstream contract drift exists.
- **Preserve Unrelated Code:** Preserve all existing comments, docstrings, and verified implementations.
- **Zero Unnecessary Architecture:** Do NOT introduce Redis, Kafka, Celery, microservices, distributed locks, or new databases unless existing repository evidence proves they are required.

## 3. Frozen Architecture Protection
- **Wave 1 is Frozen:** Canonical plan generation (Revision #1 PROPOSED), atomic revision increments, human-in-the-loop (HITL) approval, Section 9 booking eligibility, and gateway airport resolution are verified and frozen. Do not modify Wave 1 unless concrete audit evidence proves it directly causes a defect.
- **Strict Verification Order:** Investigation and root-cause analysis must be completed and documented before any production code modification.

## 4. Test-Before-Claiming-Success
- **Evidence-Based Success:** Never claim a feature, fix, or wave is "verified" or "implemented" unless actual runtime test execution (`pytest`, `vitest`, browser session) has passed with output logged.
- **Full Regression Verification:** Any change to backend logic must pass the full pytest suite (`tests/`). Any change to frontend logic must pass the full vitest suite (`npm test -- --run`).
