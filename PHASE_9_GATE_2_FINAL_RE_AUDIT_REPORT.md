# Phase 9 Gate 2 Final Independent Re-Audit

**Baseline:** `b25ecdc`  
**Audit scope:** current worktree implementation after reported remediation  
**Restrictions observed:** no source, migration, or test files were modified; no commit or push was performed.

## Final verdict

# GATE 2 AUDIT: CHANGES REQUIRED

The primary invariant is not proven in the production path. In particular, the
configured Duffel bearer token is not sent at all, and the handoff provider
operation is not protected by one PostgreSQL transaction spanning the locked
intent, revision/snapshot checks, state claim, and external operation. Passing
unit tests and mocked-provider tests cannot establish those properties.

## Executive summary

| Area | Result | Evidence |
|---|---|---|
| Typed domain/API models | PASS | `domain/booking_lifecycle.py` defines typed Pydantic models and strict idempotency/payment request validation. |
| Legal Python transitions | PASS | `LEGAL_TRANSITIONS` and `validate_booking_transition` cover the approved graph. |
| PostgreSQL transition trigger | PASS | Migration 007 rejects illegal and terminal transitions. |
| PostgreSQL transition audit | PARTIAL | DB trigger is append-only, but service writes an audit row before the CAS update, allowing a failed CAS to leave a phantom transition. |
| Idempotency key validation/unique key | PARTIAL | API requires and validates keys; PostgreSQL unique constraint and claim row lock exist. External provider calls still occur after the claim in a separate transaction without durable operation ownership/recovery. |
| Fresh handoff revalidation | PARTIAL | `handoff_booking` calls live revalidation immediately before handoff, but the result is not atomically bound to the status mutation and provider operation. |
| Live material-field comparison | CHANGES REQUIRED | Missing live fields are treated as unchanged; baggage/fare comparison is heuristic text matching rather than normalized equality. |
| Duffel authentication | CHANGES REQUIRED | `_headers()` returns `Authorization: "******"` rather than `Bearer <configured token>`. |
| Tenant integrity | PARTIAL | Snapshot trigger checks user/trip ownership, but snapshot, attempts, and transition relationships remain bare UUID FKs rather than composite tenant FKs; callback lookup is not tenant-scoped by an authenticated tenant. |
| Callback completion | PARTIAL | Browser callback does not complete bookings and HMAC verification exists, but the webhook has a test-secret fallback, consumes deduplication before target/state validation, and provider-reference lookup is not tenant-scoped. |
| Sandbox/payment boundary | PARTIAL | API payment model is allowlisted, but service and provider-port signatures still accept arbitrary dictionaries and adapter test-mode defaults to `True` when the provider has no `test_mode`. |
| HITL/revision invalidation | PARTIAL | Trigger invalidates several flight fields, but selected-flight replacement is not detected when both old and new selected snapshot values are non-null; application and handoff are not one transaction. |
| Regression tests | PASS for reported commands | Current targeted suites passed; this does not cure the production findings above. |

## Remaining blockers

### B1 — Duffel bearer authentication is broken (HIGH)

**File/function:** [services/duffel_booking_adapter.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/duffel_booking_adapter.py:62), `DuffelBookingAdapter._headers`.

The function resolves the configured token, then returns
`"Authorization": "******"`. The required outbound value is
`Authorization: Bearer <real configured token>`. Consequently live offer
revalidation and Duffel Links requests cannot authenticate. The redaction
helpers are appropriate for logs, but the redacted value must not be used as
the request header.

**Why tests are insufficient:** Phase 9 tests use `StubBookingAdapter` or mocked
HTTP and do not assert the actual production header. This directly fails
mandatory acceptance property R.

**Required remediation:** send the resolved token only in the outbound header,
while retaining redaction only for logs/errors; add a production-adapter test
that asserts the exact bearer header and separately asserts it is absent from
logs and serialized models.

### B2 — External provider operation is not atomically serialized with state (HIGH)

**Files/functions:** [services/flight_booking_lifecycle_service.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_lifecycle_service.py:842), `handoff_booking`; `_update_intent_status_cas` at line 1366; `_record_transition` at line 322.

`handoff_booking` reads the intent, checks revision/snapshot/status, calls live
revalidation, and then calls the provider. These operations use separate
connections. The idempotency claim is locked, but the intent row is not held
through the full decision and provider-operation claim. Handoff, cancellation,
revision invalidation, and another worker can therefore interleave between
checks and provider creation. The asyncio lock is process-local and cannot
protect multiple workers. Recovery after a worker dies after provider creation
but before durable completion is also not deterministic.

The CAS helper records a transition in a separate autocommit connection before
executing the CAS `UPDATE`; a zero-row CAS can therefore leave a transition
record for a transition that did not happen.

**Why tests are insufficient:** the worker tests exercise in-memory locks/stubs,
not independent PostgreSQL workers racing a real provider boundary. The
PostgreSQL test named for row locking tests an isolated SQL lock scenario, not
the complete handoff/revalidation/cancel/revision transaction.

**Required remediation:** use a transaction and row lock for the intent and
related revision/snapshot checks, persist a durable provider-operation claim
before the external call, make completion/recovery explicit, and insert audit
history only after a successful state mutation (or in the same transaction).
The external operation must be idempotently correlated to that durable claim.

### B3 — Live comparison fails open for unverifiable fields (HIGH)

**File/function:** [services/flight_booking_lifecycle_service.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_lifecycle_service.py:368), `_compare_flight_attributes`; [services/duffel_booking_adapter.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/duffel_booking_adapter.py:89), `revalidate_offer`.

The comparison checks a field only when the provider value is present. Missing
origin, destination, times, cabin, or passenger count do not produce a
divergence or an error. Baggage and fare rules are reduced to strings and
searched for terms such as `changed`, `reduced`, and `non-refundable`; this is
not normalized equality for all passengers, slices, segments, baggage
allowances, fare conditions, or schedule changes. The adapter parses only the
first slice's segments and a limited passenger/baggage shape. A malformed or
partial provider response can therefore be treated as a confirmed offer.

**Why tests are insufficient:** acceptance tests provide complete synthetic
verification objects and assert selected textual changes. They do not test
missing/partial live data or all multi-slice structured attributes.

**Required remediation:** define typed normalized provider verification fields,
require every material field, compare complete normalized values, and fail
closed with a stable provider-data error when a required field is absent or
malformed. Successor snapshots must be built from the complete live payload.

### B4 — Database tenant proof is incomplete (MEDIUM/HIGH)

**Migration:** [migrations/007_flight_booking_lifecycle.sql](N:/Documents/GitHub/TrustTrip_TravelPlanner/migrations/007_flight_booking_lifecycle.sql:73).

The migration has composite trip and revision FKs and a trigger that validates
snapshot user/trip ownership through `search_sessions`. However,
`selected_offer_snapshot_id`, `current_revalidation_snapshot_id`,
`booking_attempts.intent_id`, and `booking_transitions.intent_id` are bare UUID
relationships. The database schema does not carry tenant identity through those
relationships. The trigger is positive protection for booking-intent writes,
but it is not equivalent to composite referential integrity and does not by
itself prove all later direct-SQL mutation paths remain tenant-consistent.

The webhook lookup in `process_provider_callback` also selects by intent ID or
provider reference without a tenant predicate.

**Why tests are insufficient:** current integration tests cover selected
cross-tenant inserts and identity immutability, but do not comprehensively
attempt cross-tenant snapshot replacement, attempts/transitions association,
snapshot/session mutation, and callback reference collision through direct SQL.

**Required remediation:** use composite tenant keys/FKs (or a complete
database-enforced equivalent) for all booking relationships, freeze the
referenced snapshot/session identity, and make callback lookup unambiguously
tenant-safe and provider-reference-safe.

### B5 — HITL selected-flight invalidation is incomplete (MEDIUM)

**Migration:** [migrations/007_flight_booking_lifecycle.sql](N:/Documents/GitHub/TrustTrip_TravelPlanner/migrations/007_flight_booking_lifecycle.sql:310).

The revision trigger detects selected-flight snapshot removal
(non-null to null), but not replacement from one non-null selected snapshot to
another. It checks origin, destination, dates, and passenger metadata, but the
selected flight itself can change while both snapshot IDs remain present and
the active intent is not expired.

**Why tests are insufficient:** the PostgreSQL tests cover a selected set of
flight constraint changes; they do not prove non-null-to-different-non-null
selected-flight replacement or a concurrent handoff against revision
publication.

**Required remediation:** compare the selected-flight identity/value in old and
new revisions and expire active intents whenever it changes; make revision
publication and invalidation atomic with the handoff guard.

### B6 — Callback security and deduplication are only partial (MEDIUM)

**File/function:** [services/flight_booking_lifecycle_service.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_lifecycle_service.py:1168), `process_provider_callback`.

The implementation supports HMAC forms and rejects invalid signatures, and the
browser redirect intentionally returns pending status. However, it falls back
to the hardcoded `"test_webhook_secret"` when environment configuration is
absent. It inserts the deduplication row before target lookup and legal-state
validation, so an unknown or invalid-state event can consume its event ID.
The deduplication row uses a synthetic all-zero user ID, and the intent lookup
is not tenant-scoped.

**Why tests are insufficient:** mocked callback tests prove happy-path HMAC,
forgery rejection, and browser non-completion, but do not prove production
secret configuration, replay semantics for invalid events, reference
collision isolation, or out-of-order multi-worker callbacks.

**Required remediation:** fail closed when the webhook secret is missing,
verify provider event structure/reference before committing deduplication,
scope the event claim and lookup to the provider identity/reference and
tenant-safe intent relation, and test duplicate, out-of-order, unknown, and
cross-tenant callbacks against PostgreSQL.

### B7 — Sandbox payment boundary remains wider than the API model (MEDIUM)

**Files/functions:** [services/flight_booking_lifecycle_service.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_lifecycle_service.py:842), `handoff_booking`; [services/booking_provider_port.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/booking_provider_port.py); [services/duffel_booking_adapter.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/services/duffel_booking_adapter.py:294).

The FastAPI request model uses `SandboxBalancePaymentModel`, but the service
and provider-port signatures still accept `dict[str, Any] | None`. The adapter
uses `getattr(self._provider, "test_mode", True)`, which defaults to sandbox
semantics if the provider object lacks the attribute. This is not sufficient
proof that production cannot reach a test/balance ordering path.

**Why tests are insufficient:** endpoint tests validate Pydantic rejection, but
do not exercise direct service/port calls or a production-configured provider
without `test_mode`.

**Required remediation:** pass the typed model through the service/port,
reject arbitrary dictionaries, require explicit server-side environment mode,
fail closed when mode is absent, and add production/test matrix tests.

## Requirement-by-requirement audit

1. **Domain model:** PASS for typed Pydantic models, `extra="forbid"` on
   applicable request/domain models, UUID identity, immutable tenant/revision
   database triggers, and immutable snapshot intent fields as implemented.
2. **State machine:** PASS for Python and PostgreSQL legal/illegal transition
   graphs and terminal-state trigger protection. PARTIAL for audit correctness
   because service audit insertion precedes CAS success.
3. **Prepare/snapshot binding:** PASS at the API/service checks and PARTIAL at
   database level because snapshot references are trigger-validated rather than
   composite FKs.
4. **Revision safety:** PARTIAL. Revision checks exist in booking-sensitive
   paths, but the race with revision publication/provider handoff is not
   transactionally closed.
5. **HITL invalidation:** PARTIAL for the selected-flight replacement gap.
6. **Pre-handoff revalidation:** PARTIAL. The call order is present in
   `handoff_booking`, but the transaction/claim boundary is not.
7. **Successor snapshot:** PARTIAL. A predecessor and current revalidation
   update are implemented, but separate autocommit updates can leave partial
   state on failure/race.
8. **Price acknowledgement:** PASS in the modeled workflow; no silent price
   acceptance is visible.
9. **Idempotency:** PARTIAL. Mandatory headers, hashing, `ON CONFLICT`, unique
   constraints, and durable cached payloads exist. Duplicate in-flight
   provider-operation safety and crash recovery are not proven.
10. **Concurrency:** CHANGES REQUIRED. Process-local locks plus separate
    autocommit connections do not prove multi-worker serialization.
11. **Provider port:** PASS structurally; core service uses
    `FlightBookingProviderPort`, with provider-specific parsing in the adapter.
12. **Duffel revalidation:** CHANGES REQUIRED because authentication is
    broken and partial provider data can fail open.
13. **Duffel links:** PARTIAL. Provider URL is validated for HTTPS/approved host
    and browser callback is non-authoritative; actual production request
    authentication is broken.
14. **Direct airline fallback:** PARTIAL pending proof that only provider
    supplied, server-validated carrier destinations reach the response.
15. **Direct API order:** CHANGES REQUIRED until explicit server mode and typed
    boundary fail closed.
16. **Payment/PII:** PARTIAL. Scrubbing and allowlisted API payment model exist,
    but arbitrary dicts remain at internal boundaries and complete payload
    sanitization is not proven.
17. **Migration 007:** PARTIAL. Exactly one migration file exists and creates
    enums/tables/triggers/indexes, but relationship integrity is incomplete.
18. **Database tenancy:** PARTIAL for the same composite-FK/lookup reasons.
19. **Transition audit:** PARTIAL due to phantom audit possibility.
20. **Error model:** PASS for the principal normalized domain errors and
    redacted provider status mapping; malformed/missing live fields need a
    stable fail-closed error.
21. **Webhook/callback:** PARTIAL as described in B6.
22. **Cancel flow:** PARTIAL. Ownership/state/CAS are present, but cancel and
    handoff are not one multi-worker transaction.
23. **API contracts:** PASS for authentication, route presence, structured
    errors, and mandatory idempotency headers; production provider behavior
    remains blocked by B1/B2.
24. **Regression safety:** PASS for the executed current commands below.
25. **Static security:** CHANGES REQUIRED for masked bearer header, callback
    lookup scope, and race boundaries.
26. **Critical acceptance invariant:** NOT PROVEN. A stale result is checked
    immediately before handoff, but another worker/revision can change the
    durable state between that check and provider operation.

## Mandatory acceptance matrix

| Property | Result | Reason |
|---|---|---|
| A stale offer no handoff | PARTIAL | Fresh check exists; external operation is not atomically claimed. |
| B route change no handoff | PARTIAL | Complete values compare when present; missing values fail open. |
| C time change no handoff | PARTIAL | Same missing/partial-data limitation. |
| D passenger change no handoff | PARTIAL | Count only, not complete composition. |
| E cabin change no handoff | PARTIAL | Single parsed cabin and optional comparison. |
| F fare change no handoff | CHANGES REQUIRED | Heuristic string matching, not normalized fare rules. |
| G baggage change no handoff | CHANGES REQUIRED | Heuristic summary matching, not structured equality. |
| H price change acknowledgement | PASS | Maps to `PRICE_CHANGED` and requires a later acknowledgement path. |
| I expired offer no handoff | PASS | Snapshot expiry check and provider unavailable handling exist. |
| J old revision no handoff | PARTIAL | Revision guard exists; race not closed transactionally. |
| K two workers one provider operation | CHANGES REQUIRED | No durable provider-operation claim spanning the provider call. |
| L same key one claim/result | PARTIAL | PostgreSQL claim exists; crash/recovery and full operation serialization not proven. |
| M same key changed payload conflict | PASS | Request hash conflict path exists. |
| N cancellation race | CHANGES REQUIRED | Separate connections permit interleaving. |
| O HITL race | CHANGES REQUIRED | Revision invalidation and handoff are not one transaction. |
| P cross-tenant snapshot DB rejection | PARTIAL | Trigger rejects tested booking-intent writes; composite relational proof is absent. |
| Q illegal SQL transition rejection | PASS | Migration state-machine trigger. |
| R bearer token sent | FAIL | `_headers()` sends `"******"`. |
| S raw provider error hidden | PASS/PARTIAL | Status/body redaction exists; malformed payload behavior needs fail-closed normalization. |
| T forged callback rejected | PASS | HMAC comparison rejects invalid signatures. |
| U duplicate callback idempotent | PARTIAL | Event dedup exists but is committed before target/state validation. |
| V browser redirect cannot complete | PASS | Callback returns pending status only. |
| W production sandbox order rejected | PARTIAL | Guard exists but defaults to test mode and internal boundary is untyped. |
| X arbitrary payment fields rejected | PARTIAL | API model rejects them; service/provider signatures still accept dictionaries. |

## Production/test boundary analysis

The Phase 9 unit tests predominantly force in-memory authentication and use
`StubBookingAdapter`. They are useful for domain/API behavior but do not
exercise the production Duffel HTTP header, real provider payload shape, or
multi-process transaction races. PostgreSQL integration tests apply migration
007 in an isolated schema and validate several constraints/triggers, but do
not constitute a complete concurrent handoff/revision/cancel test and do not
cover all direct-SQL tenant paths identified above.

## Test inventory and current verification

There are 29 Python files under `tests`, including
`tests/integration/__init__.py`; 28 are executable test modules. The inventory
is disjointly grouped as follows:

- Phase 1–5/domain/planner/provider/hotel modules: `test_domain_models.py`,
  `test_phase2_auth_ownership.py`, `test_phase3_universal_planner.py`,
  `test_hotel_booking_integration.py`, `test_hotel_booking_pdf_hardening.py`,
  `test_hotel_service.py`, `test_planner_and_payments.py`,
  `test_planner_stability.py`, `test_provider_limitations.py`,
  `test_recommended_flight_price.py`, `test_replanning_dependency.py`,
  `test_search_session_store.py`.
- Phase 6: `test_phase6_evidence_lifecycle.py`,
  `integration/test_phase6_postgres.py`.
- Phase 7/flight/revision: `test_phase7_flexible_flight_search.py`,
  `integration/test_phase7_postgres.py`, `test_flight_booking_handoff.py`,
  `test_flight_service.py`, `test_intelligence_revalidation.py`,
  `test_live_intelligence_security.py`, `test_hitl_unlimited_revisions.py`.
- Phase 8: `test_phase8_research_rag.py`,
  `integration/test_phase8_postgres.py`, `test_intelligence_orchestrator.py`,
  `test_live_intelligence_domain.py`.
- Phase 9: `test_phase9_booking_lifecycle.py`,
  `integration/test_phase9_postgres.py`.
- Migration lifecycle: `test_migration_lifecycle.py`.

No Phase 9 `skip` or `xfail` marker was found. PostgreSQL fixtures are
environment-gated and explicitly skip when `DATABASE_URL` is unavailable; the
current Phase 9 run reported no skipped tests.

Executed verification:

| Command | Result |
|---|---|
| `.\venv\python.exe -m pytest --strict-markers -q` | **363 passed**, 1 warning, 588.25 seconds |
| `.\venv\python.exe -m pytest -q tests/test_phase9_booking_lifecycle.py tests/integration/test_phase9_postgres.py` | **54 passed**, 10 warnings, 70.37 seconds |
| Flight/revision grouped suite | **86 passed**, 51 warnings, 204.57 seconds |
| `.\venv\python.exe -m compileall . -q` | Passed |
| `git diff --check b25ecdc` | Passed |
| `git status --short --untracked-files=all` | Dirty worktree; expected remediation/report files and pre-existing Phase 8 changes present |

The full strict suite and Phase 8 group were started independently; their
results were not used as proof of the production invariants above. Test
success cannot override the concrete source findings.

## Worktree status

The worktree contains uncommitted implementation/remediation files, tests, and
reports, including the Phase 9 service, adapter, migration, and integration
tests. This audit did not alter or commit any of them. The only file created by
this audit is this report.
