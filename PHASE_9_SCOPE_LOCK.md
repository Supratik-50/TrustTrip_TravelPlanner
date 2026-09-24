# ARCHITECTURAL DECISION RECORD: PHASE 9 SCOPE LOCK

**Status**: APPROVED / LOCKED  
**Date**: 2026-09-24  
**Context**: TrustTrip Flight Booking / Duffel Lifecycle  

---

## 1. Project Context & Scale Target

TrustTrip is:
- A single-developer M.Tech academic project designed for research and live demonstration.
- Designed for approximately 1–3 concurrent demo users.
- Intended to run as **one FastAPI application instance** (single process/worker).
- Intended to use **one PostgreSQL database**.

---

## 2. Decision: Simplified, Demo-Safe Concurrency Model

Rather than introducing distributed systems infrastructure that exceeds the project's scale, TrustTrip explicitly adopts a **two-layer local-and-database concurrency model**:

```
Client Request
      ↓
Per-Booking asyncio.Lock (in-memory per-intent lock within the single FastAPI process)
      ↓
PostgreSQL Transaction BEGIN
      ↓
SELECT * FROM booking_intents WHERE id = %s FOR UPDATE
      ↓
State & Revision Validation
      ↓
Live Duffel Revalidation & Deterministic Verification
      ↓
Compare-And-Swap (CAS) UPDATE booking_intents SET status = %s WHERE id = %s AND status = %s
      ↓
PostgreSQL Transaction COMMIT
      ↓
Release asyncio.Lock
```

### Rationale:
1. **Per-booking `asyncio.Lock`**: Completely prevents intra-process worker races on the same booking intent before hitting the database, eliminating unnecessary locking contention or connection pool exhaustion.
2. **PostgreSQL `SELECT ... FOR UPDATE` & CAS**: Serves as the authoritative source of truth, enforcing ACID guarantees, deterministic serialization, and isolation at the persistence layer.
3. **Database Trigger Audit**: `trg_audit_booking_transition` fires `AFTER UPDATE OF status ON booking_intents`, guaranteeing zero phantom transitions if CAS returns zero rows.

---

## 3. Explicitly Deferred / Out of Scope

The following enterprise-scale technologies and patterns are **deliberately excluded**:
- **No Redis / Redis Sentinel / Redis Clusters**: Not needed for 1–3 users; PostgreSQL row-level locks and `booking_attempts` table handle idempotency and state serialization cleanly.
- **No Kafka / RabbitMQ**: Synchronous FastAPI endpoints and authenticated webhooks (`/api/bookings/flight/webhook`) provide simple, reliable delivery without queue infrastructure.
- **No Distributed Lock Managers (Redlock, etc.)**: A single FastAPI process with `asyncio.Lock` + PostgreSQL row locking makes distributed lock coordination redundant.
- **No Event Sourcing / Sagas / Temporal**: Standard relational state machines with transactional audit tables (`booking_transitions`) provide full auditability without complex event log management.
- **No Microservices**: TrustTrip is intentionally structured as a modular monolith.
- **No Direct Airline Card Payment Processing**: TrustTrip delegates payment capture to Duffel's hosted checkout via Duffel Link. No PAN, CVV, or card data touches TrustTrip servers.

---

## 4. Final Flight Booking Flow

```
1. User searches & selects flight offer
   ↓
2. POST /api/bookings/flight/prepare
   - Validates passenger details and offer snapshot
   - Creates BookingIntent in SELECTED status
   ↓
3. POST /api/bookings/flight/{id}/revalidate
   - Acquires per-intent asyncio.Lock
   - Executes live provider revalidation with 15s timeout
   - Performs strict deterministic attribute comparison
   - On price change: transitions to PRICE_CHANGED (requires user acceptance)
   - On match: transitions to READY_FOR_BOOKING
   ↓
4. POST /api/bookings/flight/{id}/handoff
   - Acquires per-intent asyncio.Lock + PostgreSQL SELECT FOR UPDATE
   - Verifies trip revision and snapshot expiration
   - Executes fresh live provider revalidation (fails closed if altered or withdrawn)
   - Generates verified Duffel Link session
   - Transitions intent to HANDED_OFF
   - Returns hosted redirect URL (links.duffel.com)
   ↓
5. User completes payment externally on Duffel hosted checkout
   ↓
6. POST /api/bookings/flight/webhook
   - Fails closed if DUFFEL_WEBHOOK_SECRET is missing or signature is invalid
   - Resolves target booking intent before consuming event ID
   - Idempotently handles duplicate deliveries
   - Transitions intent from HANDED_OFF to COMPLETED
```
