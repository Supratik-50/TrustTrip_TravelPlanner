# HOTEL CURRENCY TRUTHFULNESS IMPLEMENTATION REPORT

## 1. Executive Summary

This implementation wave addresses the customer-facing currency ambiguity between foreign-currency hotel offers (USD/EUR) and Razorpay checkout (INR-only), removes client-side synthetic taxes, and eliminates the unsafe 1:1 USD->INR fallback in `bookingService.ts`.

All changes adhere strictly to the frozen baseline architecture (`413d575`) without schema mutations, DDL, or modifications to Razorpay/refund/reconciliation logic.

---

## 2. Baseline
- **Frozen Commit Baseline**: `413d575` (`feat(hotel): harden Razorpay payment and booking recovery`)
- **Status**: IMPLEMENTED & AUDITED (PASS)

---

## 3. Scope of Changes

### A. Eliminated Unsafe 1:1 USD->INR Fallback
**File**: `frontend/src/services/bookingService.ts`
- **Location**: `initiateHotelPaymentOrder` and `processHotelPayment`
- **Resolution**:
  - `price_inr: prebook.amount` was completely removed.
  - Authoritative INR amount is derived strictly from `cachedOffer.price_inr`, or from `prebook.amount` *only* if the offer currency is natively `INR`.
  - For foreign-currency offers lacking authoritative INR pricing, the service fails closed immediately with HTTP 422 (`MISSING_AUTHORITATIVE_INR_PRICE`).

### B. Payment Review Truthfulness & Tax Removal
**File**: `frontend/src/components/booking/PaymentReviewModal.tsx`
- Removed client-side synthetic 10% tax line and calculation.
- Displayed truthful provider amount (`$225.00`), applied FX rate disclosure (`1 USD = ₹83.33`), authoritative payable INR amount (`₹18,749`), and an explicit notice: `Payment processed in INR through Razorpay.`
- Submit CTA displays exact payable amount: `Confirm & Pay (₹18,749)`.
- For INR stays, domestic pricing is rendered cleanly without FX disclosures.

### C. Booking Workspace Integration
**File**: `frontend/src/components/booking/BookingWorkspace.tsx`
- Removed `taxesAmount` prop passing.
- Forwarded authoritative `priceInr={selectedHotel?.price_inr}` and `exchangeRate={selectedHotel?.exchange_rate}`.

### D. Dual-Currency Disclosure on Hotel Cards
**File**: `frontend/src/components/travel/HotelCard.tsx`
- Added reference INR price (`≈ ₹18,749`) directly derived from `hotel.price_inr` for foreign-currency stays.
- Retained clean single-currency totals for domestic INR stays.

---

## 4. Test Verification

1. **Dedicated Currency Regression Suite**: `frontend/src/__tests__/hotelCurrencyDisplay.test.tsx` (7/7 passed)
2. **Backend Pytest Suite**: `tests/test_hotel_razorpay_remediation.py`, `tests/test_wave3a_hotel_prebook_payment.py`, `tests/test_section10_booking_payment.py` (62/62 passed)
3. **Frontend Test Suite**: 13/13 suites passed
4. **TypeScript Compiler**: `npm run lint` (`tsc --noEmit`) passed with 0 errors
5. **Production Build**: `npm run build` completed successfully

---

## 5. Architectural & Database Invariants

- Schema Changes: 0
- Migrations: 0
- DDL: 0
- Razorpay Currency: Strictly INR (untouched)
- Snapshot Authority: Immutable server snapshots remain the sole financial source of truth
