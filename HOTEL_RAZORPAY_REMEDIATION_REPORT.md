# HOTEL RAZORPAY CONSOLIDATED REMEDIATION REPORT

## 1. Baseline Commit
- **Frozen Baseline Commit**: `6e9917c` (`fix(wave3d-08): normalize LiteAPI hotel pricing basis`)
- **Parent Commit**: `03e64e6`
- **Scope**: Controlled remediation of findings HRP-01 (P0), HRP-02 (P1), HRP-03 (P1), and HRP-04 (P2) established during the independent read-only audit.

---

## 2. HRP-01 Root Cause (Frontend Double-Order Creation)
- In `frontend/src/services/bookingService.ts`, `TrustTripBookingService.processHotelPayment()` was creating a brand-new hotel payment order (`api.createHotelPaymentOrder(...)`) with a newly generated timestamped idempotency key *after* Razorpay Checkout had already completed.
- Prior to checkout, `PaymentReviewModal.tsx` had already invoked `initiateHotelPaymentOrder()` which created the original Razorpay order `order_1` and transaction `tx_1`.
- When the customer completed payment on `order_1`, `processHotelPayment()` created `order_2` / `tx_2` and subsequently called `/verify-and-book` with `transaction_id = tx_2` but `razorpay_order_id = order_1`.
- The backend detected an order mismatch (`order_1 != tx_2.razorpay_order_id`) and raised an HTTP 409 Conflict, causing valid customer payments to fail immediately post-checkout.

---

## 3. HRP-01 Fix
1. **Frontend Transaction & Order Propagation**:
   - Updated `PaymentReviewModalProps` and `PaymentDetails` in `frontend/src/types/bookingFrontend.ts` to include optional `transaction_id?: string`.
   - Updated `PaymentReviewModal.tsx` so that upon Razorpay checkout success, the existing `orderData.transaction_id` is supplied directly in `paymentDetails`.
   - Updated `BookingWorkspace.tsx` to forward `paymentDetails` with `transaction_id` into `bookingService.processHotelPayment()`.
2. **Single Authoritative Checkout Order**:
   - In `TrustTripBookingService.processHotelPayment()`, added check for an existing `transaction_id` (either provided via `paymentDetails` or tracked in `activeHotelTransactions`).
   - If an active transaction exists, the redundant `api.createHotelPaymentOrder` invocation is skipped entirely.
   - The authoritative `transaction_id` and original `razorpay_order_id` are passed directly to `/api/hotel-payments/verify-and-book`.
   - Maintained cleanup of active transactions upon completion or failure.

---

## 4. HRP-02 Root Cause (Client-Supplied Pricing on Legacy `/create-order`)
- In `hotel_payment_api.py`, `create_order()` accepted monetary values (`price_inr` and `price`) directly from the client request payload `req.offer`.
- A malicious client could alter `req.offer["price_inr"]` to arbitrarily low amounts (e.g. ₹1.00) while booking high-value hotel rooms.
- In contrast, the modern snapshot-based endpoint (`/create-order-from-snapshot`) derived authoritative prices from server-side snapshot records.

---

## 5. HRP-02 Fix
1. **Authoritative Server-Side Pricing Resolution**:
   - Extended `HotelPaymentOrder` model with optional `snapshot_id: str | None = None`.
   - In `create_order()`, implemented authoritative snapshot resolution:
     - Checked `req.snapshot_id`, `req.offer.get("snapshot_id")`, `req.prebook.get("snapshot_id")`, or trip metadata (`selected_hotel_snapshot_id`).
     - When a snapshot is resolved, the endpoint queries `SearchSessionStore.get_snapshot()` and strictly enforces the server-authoritative INR reference amount (`snapshot.normalized_offer.price.inr_reference_amount` or currency-converted total).
     - Client-provided prices in `req.offer` are overwritten with the server's snapshot values before order creation.
2. **Strict Amount Validation & Fallback Guard**:
   - If no snapshot is present, the endpoint validates that `amount_inr` is strictly positive (`> 0`), rejecting zero or negative amounts with HTTP 422 Unprocessable Entity.
   - The modern frontend `initiateHotelPaymentOrder()` routes via snapshot (`create-order-from-snapshot`), and legacy calls are guaranteed server-authoritative pricing whenever a snapshot exists.

---

## 6. HRP-03 Root Cause (Captured Funds Without Automated Refund)
- When a customer's payment succeeded and was captured in Razorpay, but the upstream hotel supplier booking call failed (e.g. room sold out, rate unavailable), `verify_and_book()` marked the transaction as `booking_failed` and returned `requires_refund: True`.
- However, `RazorpayService.refund()` was never invoked automatically. This left customer funds captured without any automated refund or durable recovery workflow, requiring manual intervention for every supplier failure.

---

## 7. HRP-03 Fix
1. **Automated Refund Dispatch with Provider Idempotency**:
   - In `hotel_payment_api.py`, upon supplier booking failure where reconciliation confirms no supplier booking exists (`rec_status == "NOT_FOUND"`), `verify_and_book()` triggers `RazorpayService.refund()`.
   - The refund request uses an explicit idempotency key: `f"ref_ht_{req.transaction_id.replace('-', '')[:32]}"`, passed via `X-Refund-Idempotency` header to prevent duplicate refunds on retries.
2. **Durable, Truthful State Transitions**:
   - If the refund succeeds (gateway returns `status` in `{"processed", "captured", "refunded"}`):
     - Transaction is updated to `payment_status="refunded"`, `booking_status="booking_failed"`.
     - API returns `success=False`, `refunded=True`, `requires_refund=False`, and `refund_id`.
   - If the refund call fails (e.g. gateway 503 or network failure):
     - Transaction is updated to `payment_status="refund_failed"`, `booking_status="booking_failed"`.
     - API returns `success=False`, `refunded=False`, `requires_refund=True`, signaling that the payment was captured and remains pending refund recovery.
   - The system never falsely reports `refunded: True` unless Razorpay explicitly confirms the refund.
3. **Idempotent Replay Handling**:
   - Repeated calls to `verify-and-book` on transactions with `payment_status="refunded"` return the existing refunded state idempotently without initiating duplicate gateway refund requests.

---

## 8. HRP-04 Root Cause (Supplier Booking Success But Response Lost)
- During supplier booking, if a network timeout, socket disconnect, or gateway 504 occurred after the supplier had already confirmed the booking on their end:
  - TrustTrip caught the exception and marked the local transaction as `booking_failed`.
  - A subsequent retry attempt or blind re-booking would initiate a brand new supplier booking, causing duplicate room bookings and duplicate supplier charges.

---

## 9. HRP-04 Fix
1. **Supplier Provider Reconciliation Interface**:
   - Added `reconcile(client_reference: str) -> tuple[str, dict[str, Any] | None]` to the base `HotelProvider` abstract class.
   - Implemented provider-specific reconciliation:
     - **`LiteAPIProvider`**: Queries `GET /bookings?clientReference={client_reference}` using the transaction ID as the client reference.
     - **`HotelbedsProvider`**: Queries `GET /hotel-api/1.0/bookings?clientReference={client_reference}`.
   - Returned tri-state outcomes:
     - `("CONFIRMED", booking_dict)`: Booking exists upstream with confirmation voucher.
     - `("NOT_FOUND", None)`: Supplier confirms no booking exists for this client reference.
     - `("UNCERTAIN", None)`: Network or supplier error during lookup; state cannot be confirmed.
2. **Reconciliation in Hotel Booking Lifecycle**:
   - Exposed `HotelService.reconcile(offer, client_reference)`.
   - In `hotel_payment_api.py`, when `HotelService.book()` raises an exception:
     - The system immediately invokes `HotelService.reconcile(req.offer, req.transaction_id)`.
     - **If `CONFIRMED`**: Transaction is finalized as `payment_status="captured"`, `booking_status="booked"`, `booking_json=rec_booking`. Returns `success=True`, `reconciled=True`. No duplicate booking is created.
     - **If `UNCERTAIN`**: Transaction is marked `booking_status="reconciliation_pending"`, `payment_status="captured"`. Returns `booking_pending=True`, `reconciliation_pending=True`, `requires_refund=False`. Blind retries and premature refunds are blocked.
     - **If `NOT_FOUND`**: Enters automated refund flow (HRP-03).
3. **Recovery Retries on Pending Transactions**:
   - If a client retries `verify-and-book` while the transaction is in `reconciliation_pending`:
     - Re-evaluates `HotelService.reconcile()`.
     - If confirmed, finalizes as `booked`.
     - If still uncertain, returns 409 Conflict with clear pending status instructions.

---

## 10. Transaction State Behavior
The hotel payment transaction lifecycle in `trust_trip_hotel_transactions` now safely models all scenarios:

| Payment Status | Booking Status | Description |
| :--- | :--- | :--- |
| `pending` | `awaiting_payment` | Order created; awaiting Razorpay Checkout. |
| `captured` | `booking_in_progress` | Signature verified, payment captured, supplier hold claimed. |
| `captured` | `booked` | Supplier booking confirmed (directly or via reconciliation). |
| `captured` | `reconciliation_pending` | Network lost during booking; awaiting provider confirmation. |
| `refunded` | `booking_failed` | Supplier confirmed no booking; automated refund processed. |
| `refund_failed` | `booking_failed` | Supplier confirmed no booking; automated refund failed at gateway (recoverable). |

---

## 11. Refund Behavior
- **Prerequisite**: Payment must be verified as `captured` in Razorpay.
- **Trigger**: Confirmed supplier failure (`NOT_FOUND` status from reconciliation).
- **Idempotency Key**: `ref_ht_{tx_id[:32]}` passed in `X-Refund-Idempotency` header.
- **Provider Gate**: Evaluates `res.get("status")` from Razorpay response.
- **No False Positives**: Fails into `refund_failed` / `refund_pending` if Razorpay returns an error; does not claim success unless confirmed.

---

## 12. Supplier Reconciliation Behavior
- Uses authoritative client reference (`transaction_id`) assigned at transaction creation and passed into provider booking payloads.
- Resolves upstream booking state before failing closed or triggering refunds.
- Distinguishes `CONFIRMED`, `NOT_FOUND`, and `UNCERTAIN` without fabricating booking records or voucher codes.

---

## 13. Tests Executed
1. **Targeted Remediation Suite**:
   - `tests/test_hotel_razorpay_remediation.py` (10 tests)
     - `TestHRP01TransactionIdentity`:
       - `test_verify_and_book_rejects_mismatched_order_id` (PASSED)
       - `test_verify_and_book_idempotent_retry_on_already_booked` (PASSED)
     - `TestHRP02AuthoritativePricing`:
       - `test_legacy_create_order_overrides_client_manipulated_price_with_snapshot` (PASSED)
       - `test_legacy_create_order_rejects_negative_or_zero_price` (PASSED)
     - `TestHRP03AutomatedRefund`:
       - `test_captured_payment_supplier_failure_triggers_successful_refund` (PASSED)
       - `test_captured_payment_refund_gateway_failure_enters_recoverable_state` (PASSED)
       - `test_repeat_verify_and_book_on_refunded_tx_is_idempotent` (PASSED)
     - `TestHRP04SupplierReconciliation`:
       - `test_supplier_network_timeout_reconciles_to_confirmed_booking` (PASSED)
       - `test_supplier_uncertain_state_blocks_blind_refund_and_blocks_duplicate_booking` (PASSED)
       - `test_reconciliation_pending_retry_finalizes_when_supplier_confirms` (PASSED)
2. **Regression & Historical Suites**:
   - `tests/test_wave3a_hotel_prebook_payment.py` (10 tests) — PASSED
   - `tests/test_wave3b_booking_persistence.py` (9 tests) — PASSED
   - `tests/test_wave3c_cancellation_truthfulness.py` (12 tests) — PASSED
   - `tests/test_wave3d_b_reconciliation_and_resilience.py` (11 tests) — PASSED
   - `tests/test_w3d08_hotel_pricing_basis.py` (6 tests) — PASSED
   - `tests/test_section10_booking_payment.py` (42 tests) — PASSED
   - `tests/test_section10_payment_idempotency_concurrency.py` (2 tests) — PASSED
   - Total Pytest: **102 tests passed, 0 failed**.
3. **Frontend Build & Test Suite**:
   - `npm run build` (Vite / TypeScript production bundle): PASSED (0 errors).
   - `npm test` (All frontend contract and regression tests): PASSED (All suites green).

---

## 14. Test Results
- **Pytest**: 102 passed, 0 failed, 127 warnings in 18.54s.
- **Frontend Build**: Built in 13.65s (Clean production build).
- **Frontend Tests**: 100% passing across Section 8, Section 9, Section 10, Wave 2, Wave 3A, Wave 3B, Flight Dual-Currency, and Trip Constraint tests.

---

## 15. Files Changed
1. **`frontend/src/types/bookingFrontend.ts`**:
   - Added `transaction_id?: string` to `PaymentDetails` and `PaymentReviewModalProps`.
2. **`frontend/src/components/booking/PaymentReviewModal.tsx`**:
   - Passed `transaction_id: orderData.transaction_id` from pre-checkout order to payment processing callback.
3. **`frontend/src/components/booking/BookingWorkspace.tsx`**:
   - Propagated `paymentDetails.transaction_id` to `bookingService.processHotelPayment`.
4. **`frontend/src/services/bookingService.ts`**:
   - Maintained `activeHotelTransactions` map; eliminated secondary order creation post-checkout; reused authoritative pre-checkout `transaction_id` and `razorpay_order_id`.
5. **`services/hotel/hotel_provider.py`**:
   - Added `reconcile(client_reference: str)` abstract method.
6. **`services/hotel/liteapi_provider.py`**:
   - Implemented LiteAPI booking reconciliation via `GET /bookings?clientReference={client_reference}`.
7. **`services/hotel/hotelbeds_provider.py`**:
   - Implemented Hotelbeds booking reconciliation via `GET /hotel-api/1.0/bookings?clientReference={client_reference}`.
8. **`services/hotel/hotel_service.py`**:
   - Added `reconcile(offer, client_reference)` dispatcher.
9. **`hotel_payment_api.py`**:
   - Enforced server-authoritative snapshot pricing in `/create-order` and positive amount validation.
   - Integrated supplier reconciliation and automated Razorpay refunds in `/verify-and-book`.
   - Handled `reconciliation_pending` and `refunded` idempotent states.
10. **`tests/test_hotel_razorpay_remediation.py`** (new test suite):
    - Comprehensive regression tests for HRP-01, HRP-02, HRP-03, and HRP-04.

---

## 16. Schema Status
- **Schema Changed**: **NO**.
- **Assessment**: The existing `trust_trip_hotel_transactions` table columns (`payment_status` VARCHAR, `booking_status` VARCHAR, `booking_json` JSONB, `error` TEXT) natively support the required states (`reconciliation_pending`, `refunded`, `refund_failed`) without requiring any DDL or migration.

---

## 17. Known Limitations
- When supplier booking status is `UNCERTAIN` and upstream supplier APIs are unreachable for an extended period, transaction resolution requires an administrative or background cron reconciliation pass to finalize or refund once the supplier recovers.
- Offline payment methods or cash-on-delivery are not applicable (Razorpay only).

---

## 18. Remaining Risks
- If a hotel supplier adapter is added in the future that does not support client reference searches, that adapter will default to `UNCERTAIN` on lost responses, safely requiring reconciliation.

---

## 19. Confirmation of Git State
- **Commit Created**: **NO**.
- **Push Performed**: **NO**.
- **Working Tree Clean/Reset**: **NO**.
- Pre-existing untracked files and working-tree changes remain intact.
