# PHASE 9 GATE 2 — FINAL INDEPENDENT AUDIT REPORT

**Baseline:** `b25ecdc` (Phase 8 approved/frozen/pushed)  
**Scope:** Phase 9 flight booking lifecycle implementation and PostgreSQL migration  
**Audit mode:** Independent source, database, provider-boundary, security, and regression review  
**Implementation changes made by this audit:** None  
**Migration changes made by this audit:** None  

## Final verdict

# GATE 2 AUDIT: CHANGES REQUIRED

The state-machine enum and the visible unit/PostgreSQL tests pass, but the
production path does not currently prove the primary acceptance invariant:

> A stale, changed, expired, or old-revision offer must never reach handoff or
> provider booking.

The principal blockers are:

1. PostgreSQL lifecycle operations are not transactionally row-locked. The
   service claims `SELECT FOR UPDATE`, but the implementation contains no such
   query and performs separate `autocommit=True` updates.
2. Idempotency is optional at the API boundary, `_record_attempt_start` is
   never called, and the database uniqueness constraint is not used to
   serialize in-flight duplicate requests. Two processes can both reach the
   provider before a completed result exists.
3. Revalidation only compares price/currency and trusts a provider result flag;
   it does not compare route, departure/arrival, passenger composition, cabin,
   fare, or baggage attributes. The Duffel adapter itself returns fabricated
   UUIDs and hard-codes `schedule_changed=False`.
4. Handoff does not perform a fresh provider revalidation in the same
   serialized transaction immediately before provider session/order creation.
5. Database tenant integrity stops at the selected snapshot UUID. There is no
   composite tenant/revision foreign key from booking intents to
   `offer_snapshots`, nor tenant-safe foreign keys on attempts/transitions.
6. Database triggers audit status updates but do not enforce the approved legal
   transition graph. Direct SQL can set arbitrary enum states.
7. Duffel authentication is broken in the production adapter: `_headers()`
   emits `Authorization: ******` instead of the configured bearer token.
8. There is no flight callback/webhook consumer that authenticates a provider
   event and legally transitions `HANDED_OFF`; a client-facing redirect is
   only configured as a URL.

These are production correctness and security blockers, not test-only
deficiencies.

## 1. Booking domain model — **PARTIAL**

Evidence:

- [`domain/booking_lifecycle.py`](./domain/booking_lifecycle.py) defines typed
  Pydantic models for `BookingIntent`, `BookingStatus`, `BookingTransition`,
  `OfferRevalidationResult`, `PassengerBookingModel`, and
  `FlightHandoffDestinationResponse`.
- The authoritative identity is TrustTrip-generated `BookingIntent.id: UUID`;
  provider references are nullable secondary fields.
- `BookingIntent`, `BookingTransition`, `OfferRevalidationResult`, and
  `FlightHandoffDestinationResponse` use `extra="forbid"` and `frozen=True`.
- `PassengerBookingModel` is typed and forbids extras, but is mutable.
- `user_id`, `trip_id`, `revision_id`, and snapshot references are present.

The application model is immutable where intended, but immutability is not
fully mirrored in PostgreSQL for snapshot identity, and the `metadata` field
contains mutable dictionaries inside a frozen Pydantic object. PostgreSQL
identity freeze exists for the three tenant/revision columns only.

## 2. State machine — **PARTIAL**

The Python `LEGAL_TRANSITIONS` table exactly contains the required legal
transitions and terminal states. `validate_booking_transition` fails closed
for unknown/terminal/illegal transitions.

The database does not enforce that graph. `booking_status_enum` restricts
values only; there is no transition-validation trigger or `CHECK`/function
that rejects, for example, `DRAFT -> COMPLETED`. The service status update
uses a separate connection and does not verify affected-row identity or
current status. Illegal application transitions do fail before the update,
but direct SQL and concurrent writers are not protected.

`booking_transitions` is database immutable and receives audit rows from the
status trigger. The service also writes transitions separately, so the
service path can create duplicate audit records for one transition. The
database trigger uses generic `STATE_UPDATE`/`Automated transition` values
instead of the actual trigger, actor, and reason.

## 3. Prepare/snapshot binding — **PARTIAL**

`prepare_booking_intent` checks authenticated `user_id`, trip ownership,
current revision number, snapshot ownership through `SearchSessionStore`,
flight type, session ownership, trip association, revision number, and
availability before persisting.

This prevents the tested cross-user snapshot path. However, database
constraints do not bind `selected_offer_snapshot_id` to the same tenant,
trip, or revision. The database sees only a bare UUID foreign key. A direct
database writer can therefore associate a snapshot from another tenant when
the application is bypassed.

The initial persisted status is `SELECTED` rather than inserting `DRAFT` and
performing a database transition. That is consistent with the service's
chosen workflow, but the database cannot independently audit the insertion as
`DRAFT -> SELECTED`.

## 4. Revision safety — **PARTIAL**

The intent stores immutable `user_id`, `trip_id`, and `revision_id`, and the
service checks trip revision metadata and `current_revision_id` before
revalidation and handoff. Stale paths raise `REVISION_STALE` and expire the
intent where the status permits.

The check is not held in the same PostgreSQL transaction as the status update
or provider call. There is therefore a revision-vs-handoff race: the trip can
advance after the check and before provider handoff. The fallback path can
also synthesize a revision UUID when no revision row is found, which is not a
safe authoritative binding. A new revision is not cryptographically or
database-bound to a new intent.

## 5. HITL invalidation — **PARTIAL**

The service and `007_flight_booking_lifecycle.sql` attempt to expire active
intents with `REVISION_SUPERSEDED`. Origin, destination, departure date, and
return date are represented in the trigger predicate, and hotel-only changes
are intended not to invalidate flight intents.

The implementation does not reliably cover passenger composition or flight
selection changes. The SQL predicate tests JSON `->>` paths under
`metadata->'constraints'`; passenger composition may be a JSON array rather
than a scalar key, and a changed non-null selected flight is not detected
(only a non-null-to-null removal is). The application hook is called only
when `plan.requires_flight_search` and catches all exceptions silently.
The PostgreSQL trigger expires by `trip_id` without explicitly joining the
tenant, relying on the trip ID's prior uniqueness rather than proving the
full composite relationship.

## 6. Pre-handoff revalidation — **CHANGES REQUIRED**

The intended call order is not implemented as an atomic production sequence.
`handoff_booking` checks `READY_FOR_BOOKING`, revision metadata, snapshot TTL,
and then directly calls `create_handoff_session` or `create_api_order`.
It does not call `revalidate_booking_intent` itself.

The service revalidation path loads the original selected snapshot and calls
the provider with only offer ID, expected amount, and currency. It does not
perform application-side comparisons for:

- provider offer identity,
- origin/destination,
- departure/arrival,
- passenger composition,
- cabin/fare attributes,
- material baggage/fare changes.

`OfferRevalidationResult.divergent_fields` exists but is never populated by
the service. The adapter reports only price/currency and a provider
`schedule_changed` flag. Route/passenger/cabin/fare/baggage mismatch can
therefore proceed to `READY_FOR_BOOKING` and handoff.

Price changes do reach `PRICE_CHANGED`, inventory withdrawals reach
`UNAVAILABLE`, and TTL checks can reach `EXPIRED`, but those paths do not
repair the missing comparison or transaction boundary.

## 7. Successor snapshot — **PARTIAL**

`_persist_successor_snapshot` creates a new immutable `OfferSnapshot` with a
new UUID and `predecessor_snapshot_id`, and
`_update_intent_successor_id` updates
`current_revalidation_snapshot_id`. The original object is not mutated.

The successor is a clone of the old normalized offer with only price,
currency, retrieval time, and expiry changed. It is not a provider-derived
snapshot containing newly verified route/passenger/cabin/baggage data.
`offer_snapshots` has no Phase 9 database migration changes visible here that
bind the successor to tenant/trip/revision.

## 8. Price-change acknowledgement — **PASS (with concurrency qualification)**

The service transitions `REVALIDATING -> PRICE_CHANGED` and requires
`accepted_amount` to match before transitioning back to `SELECTED` and
revalidating. Rejection raises an invalid-state error rather than silently
accepting. `PRICE_CHANGED -> ABANDONED` is legal through cancel.

This is correct in the single-process/in-memory tests, but the missing
PostgreSQL row lock means concurrent acceptance/cancellation is not proven.

## 9. Idempotency — **CHANGES REQUIRED**

The API accepts `Idempotency-Key` as an optional header and substitutes an
empty string. It does not reject missing or malformed keys, despite the
requirement that all mutating endpoints require one.

`_record_attempt_start` is defined but has no call sites. Consequently, the
implementation does not insert an in-progress attempt before provider work.
The completed-result lookup is a separate connection and only sees completed
rows. The unique constraint on
`(user_id, idempotency_key, action)` is useful for storage but is not used as
an in-flight claim/serialization mechanism.

A concurrent duplicate can pass both cache checks, call the provider twice,
and race on status/response updates. The in-memory lock does not protect
multiple workers or processes. Duplicate completed requests are cached in
the tested path, but the required concurrent guarantee is absent.

## 10. Concurrency — **CHANGES REQUIRED**

The service documentation claims `SELECT FOR UPDATE`, but
`flight_booking_lifecycle_service.py` contains no `SELECT ... FOR UPDATE`.
Intent reads use a normal `SELECT`; every status, amount, successor, and
handoff update uses an independent `autocommit=True` connection. Provider
calls occur outside a database transaction.

The asyncio per-intent lock protects only one Python process. It does not
serialize two workers, a revision writer, an invalidation trigger, or a
second application instance. CAS predicates on the old status are absent.
Thus revalidation-vs-handoff, HITL-vs-booking, and duplicate provider
operations are not proven safe.

## 11. Provider port — **PASS**

The lifecycle service depends on `FlightBookingProviderPort`, with
`DuffelBookingAdapter` and `StubBookingAdapter` implementing the protocol.
Core lifecycle code does not import Duffel request/response schema classes.
Provider-specific calls are concentrated in the adapter boundary.

The port exposes a generic `dict[str, Any]` payment/details surface, which
weakens type and PII guarantees, but the architectural dependency direction
is correct.

## 12. Duffel revalidation — **CHANGES REQUIRED**

`DuffelBookingAdapter.revalidate_offer` performs a live HTTP GET to
`/air/offers/{provider_offer_id}`, has an HTTP timeout, and maps timeout,
network, 429, and non-success responses to provider exceptions.

However:

- `_headers()` returns `Authorization: ******`; the configured token is not
  sent, so production live calls cannot authenticate.
- The adapter manufactures `uuid4()` values for `original_snapshot_id`.
- It sets `schedule_changed=False` unconditionally.
- It does not parse/compare route, times, passenger, cabin, fare, or
  baggage data.
- Non-success exceptions include raw `response.text` in a domain message
  that can be exposed by the API handler.

The stub is useful test infrastructure but cannot establish production
provider correctness.

## 13. Duffel links — **PARTIAL**

The Duffel adapter makes a provider POST to `/air/links`, takes the checkout
URL from the provider response, validates HTTPS/no-userinfo/no-IP and an
approved Duffel hostname, and the service validates it again. Callback
destinations are server-configured from `TRUSTTRIP_PUBLIC_BASE_URL`.
Flight card data is not stored by the lifecycle tables.

The URL is not fabricated from the session ID in the Duffel adapter, but the
fallback provider reference uses a fabricated `ses_...` when the provider
omits its reference. More importantly, no callback consumer authenticates or
processes completion, so a returned hosted URL is not a completed booking
proof.

## 14. Direct airline fallback — **PARTIAL**

`CARRIER_DOMAIN_ALLOWLIST` and server-side hostname validation reject
non-HTTPS URLs, userinfo, IP literals, localhost, and domains outside the
carrier-specific allowlist. The service reads the URL from a stored
provider/raw snapshot payload, not from an LLM or a client redirect field.

The database does not enforce provenance of that raw payload or carrier URL,
and there is no provider-signed/direct-destination record. This is adequate
against the tested hostile URL cases but not a complete database-level
proof that every accepted URL was provider-supplied.

## 15. Direct API order / sandbox — **CHANGES REQUIRED**

The service selects `payment_mode="sandbox_balance"` for
`DUFFEL_API_ORDER`, but the request permits a client to select that mode and
pass an arbitrary `payment: dict`. The adapter checks a provider
`test_mode` flag; the behavior depends on `DuffelProvider` configuration and
is not an explicit server-enforced environment/architecture gate at the
endpoint.

The direct path immediately transitions `HANDED_OFF -> COMPLETED` from the
API response and has no callback/order-status verification. Production
must not be able to silently use this path.

## 16. Payment / PII security — **PARTIAL**

The Phase 9 tables and `BookingIntent.metadata` do not intentionally persist
PAN/CVV/CVC fields, and provider raw payload scrubbing removes common
credentials/PII keys. No Phase 9 code stores card credentials in the
booking intent response.

Nevertheless, `HandoffFlightBookingRequest.payment` is an untyped arbitrary
dictionary and is passed to `create_api_order`; the port and adapter accept
it without a positive allowlist. `_record_attempt_start` is currently
unused, so request payloads are not presently recorded, but enabling it as
written could persist the supplied payment dictionary. This is not a
strong enough PII boundary for a production API.

## 17. Database migration — **PARTIAL**

`migrations/007_flight_booking_lifecycle.sql` is the only file matching the
Phase 9 migration name and defines the three enums, the three booking
tables, indexes, identity freeze trigger, transition immutability trigger,
audit trigger, and revision invalidation trigger.

Positive constraints include the TrustTrip UUID primary key, intent identity
freeze, `(trip_id,user_id)` trip FK, `(revision_id,trip_id,user_id)` revision
FK, and idempotency unique keys.

Missing or insufficient constraints:

- selected/revalidation snapshot FKs are bare UUIDs, not composite
  tenant/trip/revision FKs;
- attempts and transitions have only bare `intent_id` FKs and no composite
  tenant FKs;
- no legal transition trigger/CAS constraint exists;
- no immutability trigger protects intent revision/snapshot binding beyond
  the three identity fields;
- no booking-attempt claim state/lease constraint prevents two workers from
  owning one operation.

## 18. Database tenant integrity — **CHANGES REQUIRED**

Cross-tenant trip and revision insertion is rejected by the composite intent
FKs, and the tested victim-trip/attacker-user insert fails. That is a
material positive result.

The required proof is incomplete because `selected_offer_snapshot_id` and
`current_revalidation_snapshot_id` reference only `offer_snapshots(id)`.
The migration does not prove snapshot ownership, trip association, or
revision association at the database layer. `booking_attempts` and
`booking_transitions` similarly do not carry composite tenant keys. An
application-level `WHERE user_id` check is therefore still the only guard
for these relationships.

## 19. Booking transition audit — **PARTIAL**

Transition rows have immutable primary IDs, from/to statuses, trigger,
actor, reason, metadata, and timestamp. The PostgreSQL trigger rejects
updates and deletes, and the integration test verifies both.

The audit is not fully authoritative: status updates can be made directly
without legal-graph validation, the trigger writes generic actor/reason
values, service and trigger records can duplicate one transition, and
cascade deletion of a booking intent also deletes historical transitions.
That last behavior conflicts with a strict historical append-only retention
requirement if the parent intent is removed.

## 20. Error model — **PARTIAL**

The API maps ownership, price change, offer change, stale revision,
expiration, unavailability, invalid state, rate limit, timeout, and
provider-unavailable domain exceptions to structured responses. Unknown
exceptions use a generic internal response.

The requested stable names are not exact: the implementation emits
`BOOKING_PROVIDER_TIMEOUT` and `BOOKING_PROVIDER_UNAVAILABLE`, not
`PROVIDER_TIMEOUT` and `PROVIDER_UNAVAILABLE`. Raw provider response text is
embedded in `BookingProviderUnavailable` messages and can reach clients via
the generic `BookingLifecycleException` branch. Missing idempotency is
reported as normal operation rather than a stable required-key error.

## 21. Webhook/callback — **CHANGES REQUIRED**

The service constructs success/failure/abandonment callback URLs for Duffel
Links, but there is no flight callback/webhook route that verifies provider
signatures, looks up provider references tenant-safely, deduplicates events,
or transitions `HANDED_OFF` legally.

The only direct `COMPLETED` path is the sandbox API-order response. A browser
redirect cannot prove successful provider booking, and no production
completion synchronization is implemented.

## 22. Cancel flow — **PARTIAL**

`cancel_booking_intent` checks ownership through `get_booking_intent`,
rejects terminal states, transitions to `ABANDONED`, and preserves the
transition record. It does not delete the intent.

The row is not database-locked, the endpoint does not require an
Idempotency-Key, and cancellation races with revalidation/handoff are not
serialized across workers. The in-memory lock is insufficient for
PostgreSQL deployment.

## 23. API contracts — **PARTIAL**

All five requested routes exist in
[`flight_booking_api.py`](./flight_booking_api.py), use authenticated
`get_current_user`, and return typed response models with structured domain
errors.

The mutating routes accept missing idempotency keys, revalidate/handoff/cancel
request bodies are optional, payment is an arbitrary dictionary, and the
handoff endpoint permits mode selection from the client. Provider timeout,
availability, and raw error mapping are not uniformly explicit in the
handler. There is no callback route and no handoff-time fresh revalidation.

## 24. Regression safety — **PASS for executed regression, not proof of production safety**

The independent commands produced:

| Verification | Result | Duration |
|---|---:|---:|
| `.\venv\python.exe -m pytest --strict-markers -q` | **340 passed, 0 failed, 0 skipped** | **566.72 s** |
| Phase 9 unit + PostgreSQL group | **31 passed** | **48.18 s** |
| Flight/revision regression group | **86 passed** | **196.23 s** |
| `.\venv\python.exe -m compileall . -q` | **PASS** | Not separately timed |
| `git diff --check b25ecdc` | **PASS** | — |
| `git status --short --untracked-files=all` | **PASS / inspected** | — |

The suite inventory contains 29 Python files under `tests/`, of which
`tests/integration/__init__.py` is non-executable. The 28 executable modules
were each observed exactly once in the following disjoint inventory:

| Partition | Modules |
|---|---|
| Phase 9 (31 observed tests) | `tests/test_phase9_booking_lifecycle.py`; `tests/integration/test_phase9_postgres.py` |
| Phase 8 regression | `tests/test_phase8_research_rag.py`; `tests/integration/test_phase8_postgres.py`; `tests/test_intelligence_orchestrator.py`; `tests/test_live_intelligence_domain.py` |
| Phase 7 / flight / revision regression | `tests/test_phase7_flexible_flight_search.py`; `tests/integration/test_phase7_postgres.py`; `tests/test_flight_booking_handoff.py`; `tests/test_intelligence_revalidation.py`; `tests/test_live_intelligence_security.py`; `tests/test_hitl_unlimited_revisions.py` |
| Phase 6 | `tests/test_phase6_evidence_lifecycle.py`; `tests/integration/test_phase6_postgres.py` |
| Phase 1–5 and shared | `tests/test_domain_models.py`; `tests/test_flight_service.py`; `tests/test_hotel_booking_integration.py`; `tests/test_hotel_booking_pdf_hardening.py`; `tests/test_hotel_service.py`; `tests/test_phase2_auth_ownership.py`; `tests/test_phase3_universal_planner.py`; `tests/test_migration_lifecycle.py`; `tests/test_planner_and_payments.py`; `tests/test_planner_stability.py`; `tests/test_provider_limitations.py`; `tests/test_recommended_flight_price.py`; `tests/test_replanning_dependency.py`; `tests/test_search_session_store.py` |

The full suite count is independently verified. Passing tests do not cover
the missing multi-process transaction, provider-field comparison, or
production callback invariants identified above.

## 25. Static security review — **CHANGES REQUIRED**

The review found:

- **Provider authentication defect:** `DuffelBookingAdapter._headers()` emits
  a masked authorization value.
- **Raw provider error leakage:** response text is placed in a domain error
  returned through the API.
- **Optional idempotency:** duplicate provider calls are possible.
- **Unsafe direct payment surface:** arbitrary client payment dictionaries
  cross the provider port.
- **No database snapshot tenant binding:** cross-tenant snapshot association
  is not impossible at SQL level.
- **No database state-graph enforcement:** direct SQL can bypass the
  lifecycle service.
- **No fresh handoff revalidation:** stale/changed provider state can race
  between revalidation and handoff.
- **No authenticated flight callback:** completion cannot be proven from the
  provider.

Positive findings: no hard-coded provider secret was found in the Phase 9
source, URL validation rejects HTTPS violations/userinfo/IP literals, and
the production code routes provider operations through the port rather than
calling Duffel directly from the lifecycle service.

## 26. Critical acceptance tests — **CHANGES REQUIRED**

| Acceptance property | Result | Evidence |
|---|---|---|
| Stale/changed/expired offer never reaches handoff | **FAIL** | Handoff does not invoke live revalidation; comparison is incomplete and not transactionally bound. |
| Old revision never reaches flight handoff | **PARTIAL** | Revision checked, but no transaction/row lock protects the check against a concurrent revision. |
| Duplicate request never creates duplicate provider operation | **FAIL** | Optional key, unused in-progress claim, no PostgreSQL lock/CAS. |
| Cross-tenant request cannot access/mutate another booking | **PARTIAL** | Application ownership and trip/revision composite FKs pass tested paths; snapshot and attempts/transitions lack composite tenant constraints. |

## Blocker-by-blocker disposition

| # | Blocker | Severity | Disposition |
|---:|---|---|---|
| 1 | Atomic PostgreSQL lock/CAS/transaction around intent and handoff | Critical | CHANGES REQUIRED |
| 2 | Mandatory idempotency and in-flight duplicate serialization | Critical | CHANGES REQUIRED |
| 3 | Complete provider-field revalidation and immediate handoff gate | Critical | CHANGES REQUIRED |
| 4 | Composite snapshot/attempt/transition tenant constraints | High | CHANGES REQUIRED |
| 5 | Database legal-transition enforcement | High | CHANGES REQUIRED |
| 6 | Duffel bearer authentication and safe provider error mapping | High | CHANGES REQUIRED |
| 7 | Authenticated callback/order-status completion flow | High | CHANGES REQUIRED |
| 8 | Strictly typed/allowlisted sandbox payment surface | High | CHANGES REQUIRED |

## Production/test boundary analysis

The tests primarily exercise the in-memory service and isolated migration
objects. They prove useful local behavior and several PostgreSQL constraints,
but they do not prove two independent service processes contending on the
same intent, a revision update racing a handoff, a provider change between
revalidation and handoff, or a real Duffel authenticated request. The
`StubBookingAdapter` cannot establish those production properties.

## Required remediation before Gate 2 approval

1. Make mutating keys mandatory and claim them atomically in PostgreSQL.
2. Use one transaction with `SELECT FOR UPDATE`/CAS for ownership, revision,
   snapshot, status transition, and handoff gate; define provider-call
   idempotency/recovery semantics explicitly.
3. Revalidate the live offer immediately before handoff and compare all
   material fields using provider-derived data.
4. Add composite tenant/trip/revision constraints for snapshot references,
   attempts, and transitions.
5. Enforce the legal transition graph at the database boundary.
6. Send the real Duffel bearer token, redact provider errors, and add
   authenticated callback/order-status processing.
7. Remove the arbitrary payment dictionary from the production flight API or
   enforce a server-only, typed sandbox contract with positive field
   allowlisting.

**STOP:** This report is the only file changed by this audit. No source code,
migration, commit, or push was performed.
