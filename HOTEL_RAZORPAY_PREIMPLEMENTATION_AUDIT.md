# Hotel Razorpay — Pre-Implementation Audit

## 1. Executive Summary

This document presents a comprehensive, strictly read-only pre-implementation audit of the **Hotel Razorpay Payment + Hotel Booking Flow** in `TrustTrip_TravelPlanner`. The audit covers the entire lifecycle from hotel search, selection, immutable snapshot persistence, rate revalidation/prebook, Razorpay order creation, frontend checkout invocation, signature verification, payment authorization, supplier booking fulfillment, transaction persistence, through to read-service projection in My Trips.

### Overall System Assessment:
The architecture contains strong foundational primitives designed during Wave 3A and Section 10:
- Server-side HMAC-SHA256 signature verification and Razorpay payment fetch validation (`order_id`, `currency == INR`, exact paise amount, and `status == captured`).
- Atomic compare-and-set (`claim_booking`) preventing concurrent duplicate booking executions for the same transaction.
- Authoritative snapshot creation with AES-256-GCM payload encryption in `SearchSessionStore`.
- Strict revision eligibility gates (`assert_booking_eligible`) tied to trip ownership and revision locks.

### Critical Pre-Implementation Deficiencies Identified:
1. **Critical Frontend Order Recreation Defect (P0)**: In `frontend/src/services/bookingService.ts` (`processHotelPayment`), after the user successfully completes payment in the Razorpay Checkout modal for `order_1` (created by `initiateHotelPaymentOrder`), `processHotelPayment` generates a *new* idempotency key and calls `/api/hotel-payments/create-order` a *second time*. This creates an orphaned second transaction (`tx_2` / `order_2`). It then submits `tx_2` with the payment credentials from `order_1` to `/verify-and-book`. The backend rejects this with `HTTP 409 (Razorpay order does not match hotel transaction)`, causing all live checkout attempts to fail after the customer's credit card/UPI is already charged.
2. **Financial Pricing Integrity Bypass in `/create-order` (P1)**: The backend provides two order creation endpoints. While `/create-order-from-snapshot` correctly revalidates and enforces server-side immutable snapshot pricing, the legacy endpoint `POST /api/hotel-payments/create-order` (which `bookingService.ts` calls) accepts client-supplied `offer` dictionaries and computes INR amounts directly from `req.offer["price_inr"]` or `req.offer["price"]`. A malicious client can manipulate the request body to purchase hotel stays at arbitrary prices.
3. **Absence of Automated Refund on Booking Failure (P1)**: When payment is successfully captured by Razorpay but upstream supplier booking fails (`HotelService().book` raises `HotelProviderError` or network timeout), the backend updates the transaction to `booking_status='booking_failed'` and returns `{"requires_refund": True}`. However, `razorpay_service.refund()` is **never** invoked. Customer funds remain captured without an automated refund or automated reconciliation queue.
4. **No Upstream Confirmation Reconciliation on Network Timeout (P2)**: If supplier booking succeeds upstream but the connection drops before the HTTP response returns, the transaction is permanently marked `booking_failed`. There is no supplier query mechanism (analogous to Wave 3D-B's flight reconciliation) to recover the hotel confirmation code, resulting in orphaned active supplier bookings.

**Pre-Audit Verdict**: **REMEDIATION REQUIRED**

---

## 2. Frozen Baseline

- **Current HEAD**: `6e9917c` (`fix(wave3d-08): normalize LiteAPI hotel pricing basis`)
- **Parent Commit**: `03e64e6` (`feat(wave3d-b): reconcile flight bookings and handle planner outages`)
- **Frozen Waves**: Wave 1, Wave 2, Wave 3A, Wave 3B, Wave 3C, Wave 3D-A, Wave 3D-B, Wave 3D-08.
- **Audited Subsystem**: Hotel Razorpay payment, rate revalidation/prebook, booking fulfillment, transaction persistence, and read projection.

---

## 3. Working-Tree Safety

A baseline working-tree inspection was conducted prior to auditing:
- `git status` confirmed the presence of pre-existing modified and untracked files (including `migrations/012_hotel_snapshot_long_provider_offer_id.sql`).
- All existing working-tree changes remain untouched and strictly protected.
- No files were staged, committed, deleted, restored, or overwritten.
- No database migrations or DDL statements were executed.

---

## 4. Hotel Search and Snapshot Flow

### Lifecycle Trace:
```
Client /search (trip_id)
       ↓
hotel_api.py:search_hotels()
       ↓
TripStore.get_trip(trip_id, current_user.id)  [Tenant Check]
       ↓
HotelService.search(search_params) → LiteAPI / Hotelbeds
       ↓
SearchSessionStore.create_session()
       ↓
Offer Normalization & Snapshot Persistence:
  - HotelOffer domain object created with UUID snapshot_id
  - Price: PriceBreakdown(base_amount, total_amount, currency, inr_reference_amount)
  - Raw payload scrubbed of secrets and AES-256-GCM encrypted
  - Saved to `offer_snapshots` table
       ↓
Client /select (trip_id, snapshot_id)
       ↓
hotel_api.py:select_hotel() → TripStore.select_offer_atomic()
       ↓
Selected snapshot bound to trip metadata under active revision lock
```

### Observed Fact:
1. `hotel_api.py:search_hotels` requires an authenticated user (`current_user.id`) and validates trip ownership before initiating search.
2. The search parameters (`destination`, `checkin`, `checkout`, `adults`, `children`, `rooms`) are derived canonically from the trip and its metadata/constraints, preventing client-injected dates or destinations.
3. Every returned hotel offer is assigned an immutable `snapshot_id` (UUIDv4) and stored in `SearchSessionStore`. The raw supplier response is encrypted using AES-256-GCM before storage in PostgreSQL (`offer_snapshots`).
4. `hotel_api.py:select_hotel` verifies that `snapshot.user_id == current_user.id` and atomically attaches the selection to the trip record via `TripStore.select_offer_atomic`.

### Inference & Risk:
- The snapshot generation and offer selection architecture is robust and tamper-proof.
- However, as evaluated in Section 6, the payment initiation layer does not universally mandate the `snapshot_id`, maintaining a legacy pathway (`/create-order`) that accepts unverified client payloads.

---

## 5. Prebook / Rate Revalidation

### Flow Analysis:
Rate validation and supplier holding are implemented across two mechanisms:
1. **Standalone Endpoint (`POST /api/hotels/prebook`)**:
   - Accepts `trip_id` and `snapshot_id`.
   - Validates user trip ownership (`HTTP 403` on cross-tenant access).
   - Resolves the immutable snapshot from `SearchSessionStore`.
   - Checks snapshot availability (`AvailabilityStatus.AVAILABLE`) and expiry (`snapshot.expires_at <= now_utc`, 30-minute window).
   - Calls `HotelService().prebook(offer_dict)`.
   - Returns `{ prebook_id, amount, currency, status: "prebooked", prebook: {...} }`.
2. **Snapshot Order Creation (`POST /api/hotel-payments/create-order-from-snapshot`)**:
   - Calls `HotelBookingService.create_checkout_order_from_snapshot`.
   - **Gate 0**: `AuthorizationStore().assert_booking_eligible(trip_id, user_id, revision_id, expected_revision_number)`.
   - **Gate 1**: Tenant ownership check `SearchSessionStore.get_snapshot(snapshot_id, user_id)`.
   - **Gate 2**: Live revalidation gate `PlannerService().revalidate_offer(snapshot_id, user_id)`. If price changed, raises `FareChangedException (409)`; if inventory withdrawn, raises `OfferWithdrawnException (410)`.
   - **Gate 3**: Authoritative amount extraction directly from `snapshot.normalized_offer.price`.
   - **Gate 4**: Supplier prebook execution `HotelService().prebook(offer_dict)`. Rejects simulated/mock holds fail-closed.
3. **Legacy Order Creation (`POST /api/hotel-payments/create-order`)**:
   - Accepts `offer` and `prebook` dicts in request body.
   - Validates `assert_booking_eligible`.
   - Validates `prebook.get("prebook_id")` is present and `status != "simulated"`.
   - **Does NOT** call `PlannerService.revalidate_offer`.
   - **Does NOT** verify that `offer` matches any database snapshot.

### Specific Findings:
- **Price Authority**: Under `create-order-from-snapshot`, price is 100% server-authoritative. Under legacy `create-order`, price is client-controlled.
- **Stale Prices**: Stale prices are rejected under `create-order-from-snapshot` via `revalidate_offer`. Under legacy `create-order`, stale prices from hours or days ago can be submitted if the client retained a `prebook_id`.
- **Prebook Prerequisite**: Both endpoints require a non-empty `prebook_id` and explicitly reject simulated holds with HTTP 409.

---

## 6. Razorpay Order Creation

### Code Inspection: `hotel_payment_api.py` and `services/hotel_booking_service.py`

| Property | Modern Path (`/create-order-from-snapshot`) | Legacy Path (`/create-order`) |
| :--- | :--- | :--- |
| **Endpoint** | `POST /api/hotel-payments/create-order-from-snapshot` | `POST /api/hotel-payments/create-order` |
| **Amount Source** | `snapshot.normalized_offer.price.total_amount` | `req.offer.get("price_inr")` or `req.offer["price"] * FX` |
| **Currency** | Strictly `INR` (converted via live FX if required) | Strictly `INR` (converted via live FX if required) |
| **Client Price Tampering** | **Impossible** (server ignores client amount) | **Vulnerable** (client specifies `offer["price_inr"]`) |
| **Revision Lock** | Required (`assert_booking_eligible`) | Required (`assert_booking_eligible`) |
| **Idempotency** | Required (`payment_idempotency_key`, 8-128 chars) | Required (`payment_idempotency_key`, 8-128 chars) |
| **Store Reservation** | `HotelTransactionStore.reserve_payment_order` | `HotelTransactionStore.reserve_payment_order` |
| **Gateway Call** | `RazorpayService.create_order(amount, receipt, notes)` | `RazorpayService.create_order(amount, receipt, notes)` |

### Idempotency Behavior in `HotelTransactionStore.reserve_payment_order`:
- Uses database unique constraint: `uq_trust_trip_hotel_transactions_idempotency_key ON trust_trip_hotel_transactions (payment_idempotency_key)`.
- If an order with the same idempotency key exists:
  - If payload matches (`customer_ref`, `trip_id`, `revision_id`, `amount_inr`, `offer`, `holder`, `guests`), it replays the existing `razorpay_order_id` (`idempotent_replay: True`).
  - If payload differs, raises `ValueError("payment_idempotency_key was already used with a different hotel payment request")` $\to$ HTTP 409.
- If gateway creation fails (`RazorpayError`), the transaction is marked `payment_status="order_failed"`, `booking_status="payment_order_failed"`, and returns HTTP 502.

---

## 7. Razorpay Checkout

### Frontend Lifecycle Trace:
```
User clicks "Pay & Confirm" in PaymentReviewModal.tsx
       ↓
onInitiateOrder() [calls BookingWorkspace.handleInitiateHotelOrder]
       ↓
bookingService.initiateHotelPaymentOrder()
       ↓
api.createHotelPaymentOrder() → POST /api/hotel-payments/create-order
       ↓
Returns orderData: { transaction_id: "tx_1", razorpay_order_id: "order_1", amount_inr, key_id }
       ↓
loadRazorpayScript() loads official checkout.js
       ↓
new window.Razorpay({
  key: orderData.key_id,
  amount: Math.round(orderData.amount_inr * 100),
  currency: 'INR',
  order_id: orderData.razorpay_order_id,
  handler: (response) => { ... }
}).open()
```

### Critical Flaw Traced in Checkout Execution:
In `frontend/src/components/booking/PaymentReviewModal.tsx` (lines 104-115):
```typescript
handler: async (response) => {
  await onProcessPayment(paymentMethod, contact, {
    razorpay_order_id: response.razorpay_order_id || orderData.razorpay_order_id,
    razorpay_payment_id: response.razorpay_payment_id,
    razorpay_signature: response.razorpay_signature,
  });
}
```
In `frontend/src/components/booking/BookingWorkspace.tsx` (lines 893-900):
```typescript
const confirmRec = await bookingService.processHotelPayment(
  trip.trip_id, method, travelerContact, revId, revNum, paymentDetails, travelers
);
```
In `frontend/src/services/bookingService.ts` (lines 693-722):
```typescript
// 1. Create order via POST /api/hotel-payments/create-order
const idempotencyKey = `idem_ht_${tripId.replace(/-/g, '').slice(0, 16)}_${Date.now()}`;
const orderRes = await api.createHotelPaymentOrder({ ... }); // <-- CREATES SECOND TRANSACTION tx_2 & order_2!

// 2. Verify and book hotel via POST /api/hotel-payments/verify-and-book
const verifyRes = await api.verifyAndBookHotel({
  transaction_id: orderRes.transaction_id, // <-- tx_2
  razorpay_order_id: paymentDetails.razorpay_order_id || orderRes.razorpay_order_id, // <-- order_1
  razorpay_payment_id: paymentDetails.razorpay_payment_id,
  razorpay_signature: paymentDetails.razorpay_signature,
  ...
});
```

### Impact:
1. `initiateHotelPaymentOrder` creates `tx_1` / `order_1`.
2. User pays for `order_1` in Razorpay modal.
3. `processHotelPayment` runs and creates `tx_2` / `order_2` with a new timestamp-based idempotency key.
4. `processHotelPayment` invokes `/verify-and-book` with `transaction_id = tx_2` and `razorpay_order_id = order_1`.
5. Backend checks: `pending.get("razorpay_order_id") != req.razorpay_order_id` (`order_2 != order_1`).
6. Backend throws **`HTTP 409: Razorpay order does not match the hotel transaction`**.
7. The customer's payment is charged, but the verification fails permanently on the client!

---

## 8. Signature Verification

### Backend Verification: `hotel_payment_api.py:verify_and_book`

The verification path enforces five sequential security validations:
1. **User Confirmation**: `req.final_confirmation is True` (HTTP 409 if False).
2. **Transaction Ownership**:
   ```python
   effective_customer_ref = str(current_user.id) if current_user else req.customer_ref
   if pending.get("customer_ref") and effective_customer_ref != pending.get("customer_ref"):
       raise HTTPException(status_code=403, detail="This hotel transaction does not belong to the current customer.")
   ```
3. **Transaction Order Match**:
   ```python
   if pending.get("razorpay_order_id") != req.razorpay_order_id:
       raise HTTPException(status_code=409, detail="Razorpay order does not match the hotel transaction.")
   ```
4. **Server-Side Cryptographic Signature Verification**:
   ```python
   if not rz.verify_checkout_signature(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature):
       raise HTTPException(status_code=400, detail="Invalid Razorpay payment signature.")
   ```
   Uses HMAC-SHA256:
   `hmac.new(key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest() == signature`.
5. **Gateway Payment Fetch Validation**:
   ```python
   payment = rz.fetch_payment(req.razorpay_payment_id)
   if payment.get("order_id") != pending.get("razorpay_order_id"):
       raise HTTPException(status_code=400, detail="Payment is linked to a different Razorpay order.")
   if str(payment.get("currency") or "").upper() != "INR":
       raise HTTPException(status_code=400, detail="The captured hotel payment is not in INR.")
   if int(payment.get("amount") or 0) != int(round(float(pending["amount_inr"]) * 100)):
       raise HTTPException(status_code=400, detail="Captured payment amount does not match the stored hotel order.")
   if str(payment.get("status", "")).lower() != "captured":
       raise HTTPException(status_code=409, detail="Razorpay payment is not captured yet.")
   ```

### Evaluation:
- **Server Secret**: Genuine secret (`RAZORPAY_KEY_SECRET`) used server-side.
- **Independent Validation**: Payment ID is not trusted; live payment details are fetched directly from Razorpay.
- **Replay Protection**: The transaction state machine and order binding prevent signature reuse across different transactions.

---

## 9. Payment Authorization

### State Transition & Concurrency Control:
1. **Pre-Fulfillment Payment Persistence**:
   ```python
   store.update(req.transaction_id, payment_status="captured", booking_status="payment_captured",
                razorpay_payment_id=req.razorpay_payment_id)
   ```
2. **Atomic CAS Claim**:
   ```python
   claimed = store.claim_booking(req.transaction_id, req.razorpay_payment_id)
   ```
   Implemented via PostgreSQL query:
   ```sql
   UPDATE trust_trip_hotel_transactions
      SET booking_status='booking_in_progress',
          payment_status='captured',
          razorpay_payment_id=COALESCE(%s, razorpay_payment_id),
          updated_at=now()
    WHERE id=%s
      AND booking_status IN ('created', 'awaiting_payment', 'pending', 'payment_captured')
   RETURNING id
   ```
   - Only the first concurrent request claiming the transaction transitions it to `booking_in_progress`.
   - Concurrent requests receive `claimed = False` and are rejected with `HTTP 409 (Hotel booking is already in progress)`.
3. **Pre-Fulfillment Eligibility Re-Verification**:
   Immediately before calling the hotel provider, `AuthorizationStore().assert_booking_eligible` is re-evaluated against the active trip revision.
   If the plan revision was bumped or invalidated during checkout:
   - Fulfillment is aborted.
   - Transaction status updated to `booking_status="stale_revision_review"`.
   - Returns `{"success": False, "payment_captured": True, "requires_refund": True}`.

---

## 10. Supplier Hotel Booking

### Upstream Invocation:
Fulfillment is dispatched via `HotelService().book(offer, prebook, holder, guests, client_reference)`:
- **LiteAPI**: Invokes `POST /rates/book` with `prebookId`, `clientReference = transaction_id`, `holder`, `guests`, and `payment.method`.
- **Hotelbeds**: Invokes `POST /hotel-api/1.0/bookings` with `rateKey`, `clientReference = transaction_id`, `holder`, and `rooms`.

### Provider Confirmation Persistence:
On success, `store.update` records:
- `booking_status = "booked"`
- `payment_status = "captured"`
- `razorpay_payment_id = req.razorpay_payment_id`
- `booking_json = { "booking_id": ..., "confirmation_code": ..., "status": "CONFIRMED", ... }`

---

## 11. Payment-Success / Booking-Failure

### Deep Analysis of the Critical Failure Boundary:

```
Razorpay Signature Verified & Payment Captured
                   ↓
      Atomic Claim Acquired (booking_in_progress)
                   ↓
      HotelService().book() raises Exception
                   ↓
         hotel_payment_api.py (Lines 275-280):
           store.update(tx_id,
             payment_status="captured",
             booking_status="booking_failed",
             error=str(exc)
           )
           return {
             "success": False,
             "payment_captured": True,
             "booking_pending": False,
             "requires_refund": True,
             "transaction_id": tx_id,
             "error": "Payment succeeded but hotel booking failed..."
           }
```

### Concrete State Determination:
1. **Is money captured?** **YES**. Razorpay payment has already reached `captured` status.
2. **Is hotel booking marked successful?** **NO**. It is strictly marked `booking_status = "booking_failed"`.
3. **Can the user safely retry?** **NO**. Retrying `/verify-and-book` encounters `claim_booking`, which fails because `booking_status` is `"booking_failed"`. The endpoint returns `HTTP 409 Conflict`.
4. **Could retry create a duplicate booking?** No, the transaction cannot be reclaimed. However, if the user starts a completely new checkout, they will be charged a second time.
5. **Is there an automated refund path?** **NO**. Although `RazorpayService.refund()` exists, it is **never called** by `hotel_payment_api.py`. The response merely flags `"requires_refund": True`. No webhook, background worker, or automated refund job processes these records.
6. **Is the user given a truthful status?** **YES**. The API returns `success: False` with an explicit message that payment succeeded but booking failed.

---

## 12. Retry and Idempotency

| Scenario | Safety Classification | Enforcing Code / Mechanism | Description |
| :--- | :--- | :--- | :--- |
| **A. User double-clicks Pay** | **SAFE** | `uq_trust_trip_hotel_transactions_idempotency_key` & `reserve_payment_order` | Duplicate order creation is deduplicated at DB index level; returns existing order. |
| **B. Browser retries payment verification** | **SAFE** | `hotel_payment_api.py:196-198` | If `booking_status == 'booked'`, immediately returns `{ success: True, already_booked: True, booking: ... }`. |
| **C. Network timeout after payment capture** | **CONDITIONALLY SAFE** | `claim_booking` CAS | If payment capture succeeds on Razorpay but client disconnects before `/verify-and-book`, funds remain captured in Razorpay. Subsequent verification can complete it safely if called with the same transaction. |
| **D. Browser refreshes after successful payment** | **SAFE** | `BookingReadService._get_hotel_booking_for_trip` | Retrieves persisted transaction from DB; displays confirmed stay. |
| **E. Supplier booking succeeds but response is lost** | **UNSAFE** | `hotel_payment_api.py:278-280` | Network timeout between TrustTrip and supplier marks transaction `booking_failed`, even though hotel room was booked upstream. No supplier reconciliation exists. |
| **F. Supplier booking fails after payment** | **CONDITIONALLY SAFE** | `hotel_payment_api.py:275-277` | Truthfully marked `booking_failed`; prevents fake confirmations. However, refund is unautomated. |
| **G. Verification endpoint called concurrently** | **SAFE** | `HotelTransactionStore.claim_booking` | Atomic compare-and-set ensures only one execution thread enters `HotelService().book()`. |
| **H. Same Razorpay payment ID submitted twice** | **SAFE** | `rz.fetch_payment` + `payment.get("order_id") == pending.razorpay_order_id` | Payment ID is bound to a single Razorpay order ID. Each transaction has a distinct order ID. |

---

## 13. Hotel Transaction Store

### Code Inspection: `services/hotel_transaction_store.py`

- **Database Table**: `trust_trip_hotel_transactions`
- **Schema Columns**:
  - `id` (TEXT PK)
  - `customer_ref` (TEXT)
  - `provider` (TEXT NOT NULL)
  - `hotel_name` (TEXT)
  - `offer_json` (JSONB NOT NULL)
  - `prebook_json` (JSONB NOT NULL)
  - `holder_json` (JSONB NOT NULL)
  - `guests_json` (JSONB NOT NULL)
  - `amount_inr` (NUMERIC(14,2) NOT NULL)
  - `currency` (TEXT DEFAULT 'INR')
  - `razorpay_order_id` (TEXT)
  - `razorpay_payment_id` (TEXT)
  - `payment_status` (TEXT DEFAULT 'pending')
  - `booking_status` (TEXT DEFAULT 'pending')
  - `booking_json` (JSONB)
  - `error` (TEXT)
  - `trip_id` (UUID)
  - `revision_id` (UUID)
  - `revision_number` (INT)
  - `payment_idempotency_key` (TEXT)
  - `pdf_generated_at` (TIMESTAMPTZ)
  - `created_at`, `updated_at` (TIMESTAMPTZ)

### Indexes & Constraints:
- `PRIMARY KEY (id)`
- `UNIQUE INDEX uq_trust_trip_hotel_transactions_idempotency_key ON trust_trip_hotel_transactions (payment_idempotency_key) WHERE payment_idempotency_key IS NOT NULL`

---

## 14. Booking Read / My Trips

### Code Inspection: `services/booking_read_service.py` (`_get_hotel_booking_for_trip`)

1. **Isolation**: Queries `trust_trip_hotel_transactions` filtered by `trip_id` and `customer_ref == user_id`.
2. **Prioritization**:
   ```python
   booked_txs = [t for t in transactions if str(t.get("booking_status") or "").lower() == "booked"]
   target = booked_txs[0] if booked_txs else transactions[0]
   ```
3. **Truthful Flags**:
   - `is_booked = (booking_status == "booked")`
   - `confirmation_code = booking_json.get("confirmation_code") or booking_json.get("booking_id")`
   - Failed or pending transactions return `is_booked: False` and `confirmation_code: None`.

---

## 15. Financial Safety Assessment

| Safety Question | Assessment | Rationale & Code Evidence |
| :--- | :--- | :--- |
| **Can client cause TrustTrip to charge an amount different from server-authoritative hotel amount?** | **CONDITIONALLY SAFE / UNSAFE** | **SAFE** when routed through `/create-order-from-snapshot` (amount derived strictly from immutable snapshot).<br>**UNSAFE** when routed through legacy `/create-order` (amount computed from `req.offer.get("price_inr")` or `req.offer["price"]`). |
| **Can a verified Razorpay payment be attached to another hotel?** | **SAFE** | Order creation binds `transaction_id` and `offer` to the Razorpay order receipt/notes. Verification matches `payment.order_id == pending.razorpay_order_id`. |
| **Can a verified Razorpay payment be attached to another user?** | **SAFE** | `pending.customer_ref` is validated against `current_user.id` with `HTTP 403 Forbidden` on mismatch. |
| **Can the same payment authorize multiple hotel bookings?** | **SAFE** | `claim_booking` CAS allows only one transition to `booking_in_progress`. Replay returns `already_booked=True` without re-executing supplier booking. |
| **Can a stale hotel price reach payment?** | **CONDITIONALLY SAFE / UNSAFE** | **SAFE** in `/create-order-from-snapshot` (revalidation required before order creation).<br>**UNSAFE** in `/create-order` (no revalidation check). |
| **Can payment succeed while system falsely claims booking success?** | **SAFE** | System strictly records `booking_status="booking_failed"` when supplier booking fails. `BookingReadService` reports `is_booked=False`. |

**Overall Classification**: **CONDITIONALLY SAFE** (Architecture has strong safety checks, but legacy endpoint and frontend wiring present vulnerabilities).

---

## 16. Database / Schema Impact

- **Schema Changes Required Now**: **NONE**. The table `trust_trip_hotel_transactions` already contains all necessary lineage fields (`trip_id`, `revision_id`, `revision_number`, `payment_idempotency_key`, `razorpay_order_id`, `razorpay_payment_id`).
- **Post-Freeze Hardening Recommendation**: Add partial unique indexes on `razorpay_order_id` and `razorpay_payment_id` (`WHERE razorpay_order_id IS NOT NULL`).

---

## 17. Existing Test Coverage

1. `tests/test_wave3a_hotel_prebook_payment.py`:
   - Anonymous prebook rejected (401).
   - Cross-user trip prebook rejected (403).
   - Missing authoritative offer fails closed (422).
   - Expired snapshot rejected (409).
   - `create-order` returns Razorpay order metadata.
   - Fake signature strictly rejected (400).
   - Genuine signature succeeds and triggers `HotelService.book()`.
2. `tests/test_section10_booking_payment.py`:
   - Revision eligibility guard at hotel payment order creation and pre-fulfillment verify.
   - Payment order idempotency replay and concurrent conflict rejection.
   - Stale revision review state persistence after payment capture.
   - Rejection of uncaptured payments, wrong order IDs, and invalid signatures.
3. `tests/test_section10_payment_idempotency_concurrency.py`:
   - Concurrency tests for same-key order reservation.
4. `tests/test_hotel_booking_integration.py`:
   - Snapshot checkout success and cross-tenant snapshot rejection.
   - Rate change detection (409).
   - Atomic concurrency and idempotent verification retry.
5. `frontend/src/__tests__/section10HotelPayment.test.tsx` & `wave3aHotelCheckout.test.tsx`:
   - Tests `apiClient.createHotelPaymentOrder` and `verifyAndBookHotel` in mock environment.

---

## 18. Missing Test Coverage

1. **End-to-End Frontend Order Re-creation Regression Test**: No test caught the defect where `processHotelPayment` re-creates a second order instead of passing the existing `transaction_id`. Mock tests passed because mocks returned identical order IDs.
2. **Snapshot-First Frontend Checkout Test**: No frontend test verifying that `bookingService.initiateHotelPaymentOrder` passes `snapshot_id` to route to `/create-order-from-snapshot`.
3. **Automated Refund Trigger Test**: No backend test verifying whether a refund is issued upon supplier booking failure.
4. **Supplier 502/Timeout Reconciliation Test**: No test for lost upstream supplier responses.

---

## 19. Findings and Severity

### Summary Table:

| Finding ID | Severity | File | Function / Location | Problem Summary | Financial Impact | Blocks Prod? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **HPAY-F01** | **P0** | `frontend/src/services/bookingService.ts` | `processHotelPayment` (lines 693-722) | Re-creates a 2nd order instead of reusing `transaction_id`; causes 409 mismatch in `/verify-and-book`. | High (User charged but booking fails). | **YES** |
| **HPAY-F02** | **P1** | `hotel_payment_api.py` | `create_order` (lines 105-126) | Legacy endpoint accepts client-supplied `offer` pricing without verifying against a snapshot. | Critical (Client can manipulate room prices). | **YES** |
| **HPAY-F03** | **P1** | `hotel_payment_api.py` | `verify_and_book` (lines 275-280) | Money captured but booking fails; flags `requires_refund: True` without calling `rz.refund()`. | High (Captured funds without refund). | **YES** |
| **HPAY-F04** | **P2** | `hotel_payment_api.py` & `services/hotel/` | `verify_and_book` (lines 278-280) | Upstream supplier timeout is marked `booking_failed` without reconciliation lookup. | Medium (Possible ghost bookings at supplier). | No |
| **HPAY-F05** | **P2** | `services/hotel/hotelbeds_provider.py` | `prebook` (lines 129-134) | Non-`RECHECK` rates skip live Hotelbeds API rate check during prebook. | Medium (Risk of stale rates passing prebook). | No |
| **HPAY-F06** | **P3** | `services/hotel_transaction_store.py` | `setup` | No unique DB index on `razorpay_order_id` or `razorpay_payment_id`. | Low (Hardening against edge replays). | No |

---

## 20. Recommended Minimal Remediation

To achieve production readiness without architecture bloat:
1. **Fix Frontend Order Reuse (HPAY-F01)**:
   Update `TrustTripBookingService.processHotelPayment` to accept or look up `activeTransactionId` and `activeRazorpayOrderId` from `initiateHotelPaymentOrder` rather than invoking `createHotelPaymentOrder` a second time.
2. **Deprecate or Enforce Snapshot on `/create-order` (HPAY-F02)**:
   In `frontend/src/services/bookingService.ts`, pass `snapshot_id` so that `api.createHotelPaymentOrder` routes to `/api/hotel-payments/create-order-from-snapshot`. In backend `hotel_payment_api.py:create_order`, reject requests lacking an authoritative snapshot.
3. **Implement Immediate or Asynchronous Refund on Booking Failure (HPAY-F03)**:
   In `verify_and_book`, upon catching `HotelProviderError` or `Exception` when `payment_status == 'captured'`, invoke `RazorpayService().refund(req.razorpay_payment_id, amount_inr, idempotency_key=f"ref_{req.transaction_id}")`.

---

## 21. PRE-AUDIT VERDICT

# REMEDIATION REQUIRED

The current hotel payment flow cannot be deployed to production in its present state due to two critical blockers:
1. The frontend checkout lifecycle contains an order-recreation defect (**HPAY-F01**) that reliably causes live payments to fail during verification with `HTTP 409`.
2. The legacy order creation endpoint (**HPAY-F02**) trusts client-supplied pricing rather than enforcing immutable server-side snapshots.

Remediation must be scheduled and implemented under a controlled wave before enabling live transactions.
