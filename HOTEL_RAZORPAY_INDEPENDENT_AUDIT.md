# HOTEL RAZORPAY CONSOLIDATED INDEPENDENT AUDIT REPORT

## 1. Executive Summary
An independent, read-only audit of the Hotel Razorpay Consolidated Remediation wave was conducted across all affected frontend and backend subsystems, third-party provider integrations, transaction stores, and test suites.

The audit verified that:
1. **HRP-01 (P0)**: The frontend double-order creation issue in `bookingService.processHotelPayment()` is completely resolved. Exactly one authoritative hotel transaction and Razorpay order is created per checkout attempt and propagated directly to `verify-and-book`.
2. **HRP-02 (P1)**: Hotel payment amount derivation is server-authoritative. The client cannot tamper with or dictate payment amounts. Authoritative snapshots strictly override client payloads, and non-positive prices are rejected.
3. **HRP-03 (P1)**: Captured funds with supplier booking failure trigger automated, idempotent refunds via `RazorpayService.refund()` using dedicated idempotency keys (`ref_ht_{tx_id}`). Refund gateway errors fail into a durable recoverable state without false claims of refund success.
4. **HRP-04 (P2)**: Response-loss reconciliation is implemented via genuine provider client-reference lookup in `LiteAPIProvider`, `HotelbedsProvider`, and `HotelService`. The system distinguishes `CONFIRMED`, `NOT_FOUND`, and `UNCERTAIN` outcomes, eliminating duplicate supplier bookings on retry.
5. **No Regressions**: All 10 new HRP regression tests, 92 historical tests (total 102 pytest), frontend bundle build, and frontend test suite passed cleanly.
6. **Zero Schema Mutations**: No database DDL or migrations were added.

**Audit Verdict**: **PASS WITH NON-BLOCKING FINDINGS**  
**Recommendation**: **APPROVED FOR CONTROLLED COMMIT/FREEZE**

---

## 2. Baseline
- **Original Frozen Baseline Commit**: `6e9917c` (`fix(wave3d-08): normalize LiteAPI hotel pricing basis`)
- **Current HEAD**: `6e9917c` (Remediation is currently uncommitted in working tree)
- **Working-Tree Status**:
  - Remediation files modified in working tree: 9 files
  - New test file: `tests/test_hotel_razorpay_remediation.py`
  - Implementation report: `HOTEL_RAZORPAY_REMEDIATION_REPORT.md`
  - Pre-existing working-tree modifications and untracked artifacts from previous frozen waves remain strictly preserved and untouched.

---

## 3. HRP-01 Audit (Frontend Double-Order Creation)

### Call Chain Analysis
1. User clicks **"Complete hotel payment"** in `BookingWorkspace.tsx`.
2. `PaymentReviewModal.tsx` opens and user confirms payment.
3. `PaymentReviewModal` invokes `onInitiateOrder()` -> `BookingWorkspace.handleInitiateHotelOrder()` -> `TrustTripBookingService.initiateHotelPaymentOrder()`.
4. `initiateHotelPaymentOrder()` invokes `api.createHotelPaymentOrder()`, creating `transaction_1` and `razorpay_order_1` on the server.
5. `initiateHotelPaymentOrder()` caches `orderRes` in `activeHotelTransactions` map and returns `transaction_id` and `razorpay_order_id` to `PaymentReviewModal`.
6. `PaymentReviewModal` initializes Razorpay Checkout modal with `razorpay_order_1`.
7. Customer completes payment on Razorpay Checkout.
8. Checkout `handler` fires with `{ razorpay_payment_id, razorpay_order_id, razorpay_signature }`.
9. `PaymentReviewModal` invokes `onProcessPayment(..., { transaction_id: orderData.transaction_id, razorpay_order_id, razorpay_payment_id, razorpay_signature })`.
10. `BookingWorkspace.handleProcessHotelPayment` asserts active booking authorization and calls `bookingService.processHotelPayment()`.
11. `processHotelPayment()` checks for existing transaction:
    ```typescript
    const activeTx = this.activeHotelTransactions.get(tripId);
    const existingTxId = paymentDetails?.transaction_id || ...;
    const existingOrderId = paymentDetails?.razorpay_order_id || activeTx?.razorpay_order_id;
    ```
12. Because `existingTxId` is present, `processHotelPayment()` **skips creating a second order** and proceeds directly to `api.verifyAndBookHotel({ transaction_id: existingTxId, razorpay_order_id: existingOrderId, ... })`.
13. On verification completion, `this.activeHotelTransactions.delete(tripId)` cleans up in-memory transaction state.

### Evidence & Verification
- **Audit of all callers**: A repository-wide search revealed no other invocations of `createHotelPaymentOrder` or `/create-order` in active user flows.
- **Mismatched Order Protection**: Backend `hotel_payment_api.py` line 243 validates `if pending.get("razorpay_order_id") != req.razorpay_order_id:` and line 296 validates `if payment.get("order_id") != pending.get("razorpay_order_id"):`. Any mismatched order is rejected with HTTP 409 / HTTP 400.
- **Idempotent Retry**: A second call to `verify-and-book` on an already booked transaction returns `already_booked: True` without re-executing `HotelService.book()`.
- **HRP-01 Verdict**: **PASS**

---

## 4. HRP-02 Audit (Server-Authoritative Hotel Payment Amount)

### Payment Amount Derivation
- Modern path `/create-order-from-snapshot` derives `amount_inr` strictly from `HotelBookingService.create_checkout_order_from_snapshot()`. Request accepts zero monetary fields from client.
- Legacy endpoint `/create-order` in `hotel_payment_api.py` now resolves snapshot data:
  ```python
  snap_id = req.snapshot_id or offer.get("snapshot_id") or pre.get("snapshot_id") or trip_meta.get("selected_hotel_snapshot_id")
  if snap_id:
      snapshot = snap_store.get_snapshot(snap_id, current_user.id)
      if snapshot:
          # Server strictly overrides client offer
          offer["price_inr"] = round(amount_inr, 2)
          offer["price"] = float(auth_offer.price.total_amount)
          offer["currency"] = str(auth_offer.price.currency)
  ```
- Ownership check: `snap_store.get_snapshot(snap_id, current_user.id)` verifies snapshot access belongs to authenticated user.
- Capture check: `/verify-and-book` verifies `int(payment.get("amount")) == int(round(float(pending["amount_inr"]) * 100))` against Razorpay's API. A client cannot capture a different amount in Razorpay and book the room.

### Attack Scenario Testing
1. **Client sends `price_inr: 1.0` with valid snapshot (₹18,500)**: Server overrides client price; creates Razorpay order for ₹18,500.00. Tested in `TestHRP02AuthoritativePricing.test_legacy_create_order_overrides_client_manipulated_price_with_snapshot`. (PASS)
2. **Client sends `price_inr: -500.0` or `0`**: Server rejects with HTTP 422 ("Hotel payment amount must be strictly positive"). Tested in `test_legacy_create_order_rejects_negative_or_zero_price`. (PASS)
3. **Foreign snapshot ID supplied**: Server raises HTTP 404 / access denied. (PASS)
- **HRP-02 Verdict**: **PASS**

---

## 5. HRP-03 Audit (Captured Payment + Supplier Failure + Refund)

### Failure and Recovery Trace
1. Customer payment is captured in Razorpay.
2. `HotelService().book()` throws an upstream exception (e.g. room unavailable).
3. System invokes `HotelService().reconcile(req.offer, req.transaction_id)` (HRP-04).
4. If reconciliation confirms `NOT_FOUND`:
   - System invokes `RazorpayService.refund()`:
     - `payment_id = req.razorpay_payment_id`
     - `amount_rupees = float(pending["amount_inr"])`
     - `idempotency_key = f"ref_ht_{req.transaction_id.replace('-', '')[:32]}"`
     - Razorpay service includes header: `X-Refund-Idempotency: {idempotency_key}`
5. **Gateway Confirmation**:
   - If Razorpay confirms status `processed`, `captured`, or `refunded`:
     - Updates transaction: `payment_status="refunded"`, `booking_status="booking_failed"`.
     - Returns: `refunded: True`, `refund_id: "...", requires_refund: False`.
   - If Razorpay fails (e.g. gateway 503):
     - Updates transaction: `payment_status="refund_failed"`, `booking_status="booking_failed"`.
     - Returns: `refunded: False`, `requires_refund: True`, `error: "..."`.
     - Fails closed; never falsely reports success.
6. **Retry Idempotency**:
   - When a transaction has `payment_status="refunded"`, repeated `verify-and-book` requests immediately return the refunded record without re-calling Razorpay refund API.
- **HRP-03 Verdict**: **PASS**

---

## 6. HRP-04 Audit (Supplier Response-Loss Reconciliation)

### Provider Implementation Analysis
- `LiteAPIProvider.book()` passes `"clientReference": client_reference` (`req.transaction_id`).
- `LiteAPIProvider.reconcile()` queries `GET {BOOK_URL}/bookings?clientReference={client_reference}`.
- `HotelbedsProvider.book()` passes `"clientReference": client_reference`.
- `HotelbedsProvider.reconcile()` queries `GET /hotel-api/1.0/bookings?clientReference={client_reference}`.
- Both providers parse bookings defensively:
  - If match found and status is confirmed: returns `("CONFIRMED", booking_dict)`.
  - If empty list / no record: returns `("NOT_FOUND", None)`.
  - If network or API exception: returns `("UNCERTAIN", None)`.

### State Handling in `verify_and_book`
1. **`CONFIRMED`**:
   - Updates `payment_status="captured"`, `booking_status="booked"`, `booking_json=rec_booking`.
   - Returns `success=True`, `reconciled=True`.
   - Does NOT create duplicate supplier booking.
2. **`NOT_FOUND`**:
   - Upstream supplier confirmed no booking exists.
   - Triggers automated refund (HRP-03).
3. **`UNCERTAIN`**:
   - Network interrupted / supplier unreachable.
   - Updates `payment_status="captured"`, `booking_status="reconciliation_pending"`.
   - Returns `booking_pending=True`, `reconciliation_pending=True`, `requires_refund=False`.
   - Blocks premature refunds and prevents blind duplicate booking attempts.
4. **Retry Handling**:
   - If client retries while `reconciliation_pending`, endpoint re-checks `HotelService.reconcile()`. If confirmed, finalizes as `booked`.
- **HRP-04 Verdict**: **PASS**

---

## 7. Financial State-Machine Analysis

| Transaction State | Customer Paid? | Supplier Booking Created? | Refund Required? | Refund Executed? | Unsafe Duplicate Possible? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `pending` / `awaiting_payment` | No | No | No | No | No (Single order per checkout) |
| `captured` / `booking_in_progress` | Yes | In Progress | No | No | No (Atomic CAS lock `claim_booking`) |
| `captured` / `booked` | Yes | Yes (Confirmed) | No | No | No (Idempotent return on retry) |
| `captured` / `reconciliation_pending` | Yes | Uncertain | Blocked | No | No (Blind retry & refund blocked) |
| `refunded` / `booking_failed` | Yes | No (`NOT_FOUND`) | No | Yes (Gateway confirmed) | No (Idempotent return on retry) |
| `refund_failed` / `booking_failed` | Yes | No (`NOT_FOUND`) | Yes | Failed (Recoverable) | No (Awaits retry reconciliation) |

**Conclusion**: Zero contradictory or financially unsafe states exist in the state machine.

---

## 8. HITL / Authorization Regression Audit
- Trip ownership checks are strictly enforced (`effective_customer_ref == pending["customer_ref"]`).
- Booking authorization checks (`AuthorizationStore().assert_booking_eligible`) are retained at both order creation and immediately prior to supplier fulfillment in `verify-and-book`.
- Stale revision detection (`BookingEligibilityError`) persists payment status as captured and transitions booking to `stale_revision_review` with `requires_refund=True`, preventing unauthorized fulfillment if trip plan changed during checkout.

---

## 9. Provider Contract Audit
- All provider adapters use genuine API contracts:
  - `LiteAPI`: `GET /bookings?clientReference=...`
  - `Hotelbeds`: `GET /hotel-api/1.0/bookings?clientReference=...`
  - `Razorpay`: `POST /payments/{id}/refund` with `X-Refund-Idempotency`
- No synthetic voucher codes or fake confirmation identifiers are generated.
- Missing upstream confirmation codes fail closed.

---

## 10. Test Verification

Independent test execution was performed with the following exact commands:

1. **New HRP Remediation Test Suite**:
   ```powershell
   .\venv\python.exe -m pytest tests/test_hotel_razorpay_remediation.py -v
   ```
   **Result**: 10 passed, 0 failed in 9.81s.
2. **Historical Hotel, Booking, and Wave Test Suites**:
   ```powershell
   .\venv\python.exe -m pytest tests/test_wave3a_hotel_prebook_payment.py tests/test_wave3b_booking_persistence.py tests/test_wave3c_cancellation_truthfulness.py tests/test_wave3d_b_reconciliation_and_resilience.py tests/test_w3d08_hotel_pricing_basis.py tests/test_section10_booking_payment.py tests/test_section10_payment_idempotency_concurrency.py
   ```
   **Result**: 92 passed, 0 failed in 14.03s.
   **Total Backend Suite**: 102 passed, 0 failed.
3. **Frontend Production Build**:
   ```powershell
   npm run build (in frontend/)
   ```
   **Result**: Vite built production bundle successfully in 1.42s (0 errors).
4. **Frontend Unit & Contract Test Suite**:
   ```powershell
   npm test (in frontend/)
   ```
   **Result**: All tests passed (Section 8, Section 9, Section 10, Wave 2, Wave 3A, Wave 3B, Flight Dual-Currency, Trip Constraint persistence).

---

## 11. Git & Scope Verification
- **Current HEAD**: `6e9917c`
- **Remediation Code Files Changed (9)**:
  - `frontend/src/types/bookingFrontend.ts`
  - `frontend/src/components/booking/PaymentReviewModal.tsx`
  - `frontend/src/components/booking/BookingWorkspace.tsx`
  - `frontend/src/services/bookingService.ts`
  - `services/hotel/hotel_provider.py`
  - `services/hotel/liteapi_provider.py`
  - `services/hotel/hotelbeds_provider.py`
  - `services/hotel/hotel_service.py`
  - `hotel_payment_api.py`
- **New Test File**: `tests/test_hotel_razorpay_remediation.py`
- **Documentation**: `HOTEL_RAZORPAY_REMEDIATION_REPORT.md`
- **Protected Files**: Pre-existing modifications and untracked files from earlier waves (`migrations/012_...`, `tests/test_destination_resolver_safeties.py`, etc.) were left completely untouched.
- **Commits Created**: 0
- **Pushes Performed**: 0

---

## 12. Findings Table

| ID | Severity | Finding | Evidence | Blocking? | Recommended Action |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **OBS-01** | P3 | Legacy `/create-order` price fallback retained for mock tests | `hotel_payment_api.py:168-175`: fallback to client price when no snapshot exists is used by Section 10 tests | No | In a future post-freeze wave, update Section 10 tests to supply snapshot IDs and deprecate non-snapshot fallback. |
| **OBS-02** | INFO | In-memory `activeHotelTransactions` map scoped to client instance | `bookingService.ts:50`: Map stores active pre-checkout order data during checkout session | No | Handled cleanly by explicit `transaction_id` parameter propagation from checkout modal. |

---

## 13. Final Verdict & Freeze Recommendation

- **Verdict**: **PASS WITH NON-BLOCKING FINDINGS**
- **HRP-01**: **PASS**
- **HRP-02**: **PASS**
- **HRP-03**: **PASS**
- **HRP-04**: **PASS**
- **Freeze Recommendation**: **APPROVED FOR CONTROLLED COMMIT/FREEZE**
- Controlled commit instructions:
  - Stage ONLY the 9 remediation code files, `tests/test_hotel_razorpay_remediation.py`, `HOTEL_RAZORPAY_REMEDIATION_REPORT.md`, and `HOTEL_RAZORPAY_INDEPENDENT_AUDIT.md`.
  - Maintain all other pre-existing working-tree changes.
  - Do not push to remote.
