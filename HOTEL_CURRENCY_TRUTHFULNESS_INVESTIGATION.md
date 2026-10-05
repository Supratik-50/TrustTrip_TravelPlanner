# HOTEL CURRENCY TRUTHFULNESS INVESTIGATION REPORT

## 1. Executive Summary
An exhaustive read-only investigation was conducted into the end-to-end currency lifecycle for hotel search, offer normalization, snapshot persistence, prebook, payment review presentation, Razorpay checkout order generation, verification, and supplier booking fulfillment.

### Key Finding
**TrustTrip operates a hybrid multi-currency backend with an exclusively INR-denominated payment gateway (Razorpay), but exhibits critical presentation disconnects and dangerous fallback assumptions between the frontend and backend.**

Specifically:
1. **Search & Snapshot Currency**: LiteAPI provider defaults to `USD`, while Hotelbeds returns its contracted currency (typically `EUR` or `USD`). `HotelService.search()` performs server-side FX conversion to `INR` via `CurrencyConverter` (Frankfurter API) and persists both provider amount (`total_amount`, `currency`) and converted reference (`inr_reference_amount`, `fx_rate_applied`) inside the `OfferSnapshot`.
2. **Prebook Contract**: `POST /api/hotels/prebook` returns the provider-native currency and amount (e.g. `amount: 225.0, currency: "USD"`).
3. **Presentation Disconnect**: Unlike `FlightCard.tsx` (which supports dual-currency USD + INR est. display), `HotelCard.tsx`, `BookingWorkspace.tsx`, and `PaymentReviewModal.tsx` display ONLY the provider currency (e.g. `$225.00`), with `PaymentReviewModal` synthesizing an unbacked 10% tax in client React state.
4. **Checkout Currency Shock**: When the user clicks "Proceed to Pay" in `PaymentReviewModal`, Razorpay Checkout opens asking for **INR** (e.g. `₹18,749.00`). The user is presented with a sudden switch from `$248.00` to `₹18,749.00` with zero explanation of exchange rates or currency basis.
5. **Dangerous Fallback in `bookingService.ts`**: In `TrustTripBookingService.initiateHotelPaymentOrder` and `processHotelPayment`, lines 548 and 672 contain a severe semantic bug: `price_inr: prebook.amount`. If `cachedOffer` is absent, the dollar amount (e.g. `225`) is directly copied into `price_inr` without conversion (`price_inr = 225`), creating a theoretical vulnerability where 1 USD becomes 1 INR if a legacy checkout is executed without a server snapshot.

**Classification**: **CURRENCY FLOW REQUIRES DEFINED PRODUCT CONTRACT & FUTURE IMPLEMENTATION**  
**Immediate Action**: Read-only investigation completed. No code changes, no schema modifications, and no git commits were made.

---

## 2. Current Frozen Baseline
- **Commit**: `413d575` (`feat(hotel): harden Razorpay payment and booking recovery`)
- **Parent**: `6e9917c` (`fix(wave3d-08): normalize LiteAPI hotel pricing basis`)
- **Status**: Clean and verified. All pre-existing modified and untracked files remain protected.

---

## 3. Hotel Provider Currency Behavior

### LiteAPI (`services/hotel/liteapi_provider.py`)
- **Search Request**: `LiteAPIProvider.search()` constructs payload with:
  ```python
  "currency": str(request.get("currency", "USD")).upper()
  ```
  Because `hotel_api.py` does not pass `currency` in `search_params`, LiteAPI searches default to **`USD`**.
- **Search Response**: LiteAPI returns `total.get("currency")` (typically `"USD"`).
- **Offer Dictionary**: Emits `"price": amount`, `"currency": currency`. It does NOT compute or emit `price_inr`.

### Hotelbeds (`services/hotel/hotelbeds_provider.py`)
- **Search Request**: `HotelbedsProvider.search()` does not specify currency; Hotelbeds returns rates in the account's contracted currency (e.g. `EUR` or `USD`).
- **Search Response**: Emits `data.get("currency")`. Does NOT compute or emit `price_inr`.

### Provider Abstraction (`services/hotel/hotel_provider.py`)
- `HotelProvider` protocol mandates `search()`, `prebook()`, `book()`, and `reconcile()`. It does not dictate currency, leaving currency representation provider-native.

---

## 4. Snapshot Currency Behavior

### Conversion at Search Time (`services/hotel/hotel_service.py`)
- When `HotelService.search()` receives raw offers from providers, it iterates through all offers and calls `self._inr(o)` (lines 61-62).
- `_inr(offer)` checks `cur = offer.get("currency")`:
  - If `cur == "INR"`, sets `price_inr = price`, `exchange_rate = 1.0`.
  - If `cur != "INR"` (e.g. `USD`), it calls `CurrencyConverter().get_rate(cur, "INR")`.
  - Updates `offer` with:
    - `price_inr = round(price * rate, 2)`
    - `exchange_rate = float(rate)`
    - `exchange_rate_date = r.get("date")`
    - `price_inr_source = "reference FX conversion"`

### Persistence in `OfferSnapshot` (`hotel_api.py`)
- In `hotel_api.py` lines 643-651, `PriceBreakdown` is constructed:
  ```python
  price=PriceBreakdown(
      base_amount=Decimal(str(round(price_val, 2))),
      tax_amount=Decimal("0.00"),
      fee_amount=Decimal("0.00"),
      total_amount=Decimal(str(round(price_val, 2))),
      currency=currency_val,  # e.g. "USD"
      inr_reference_amount=Decimal(str(round(price_inr_val, 2))),  # e.g. 18749.25
      fx_rate_applied=Decimal(str(rate_val)),  # e.g. 83.33
  )
  ```
- **Conclusion**: The snapshot correctly and truthfully preserves the provider currency (`total_amount: 225, currency: "USD"`) alongside the frozen reference amount in INR (`inr_reference_amount: 18749.25, fx_rate_applied: 83.33`). The exchange rate is locked at snapshot creation time.

---

## 5. Prebook Currency Behavior

### Endpoint: `POST /api/hotels/prebook` (`hotel_api.py`)
- Lines 874-883:
  ```python
  result = HotelService().prebook(offer_dict)
  return {
      "success": True,
      "prebook_id": result.get("prebook_id"),
      "amount": float(offer_dict.get("price") or 0.0),
      "currency": str(offer_dict.get("currency") or "INR"),
      "status": "prebooked",
      "hotel_name": offer_dict.get("hotel_name", "Hotel"),
      "expires_at": snapshot.expires_at.isoformat() if (snapshot and snapshot.expires_at) else None,
      "prebook": result,
  }
  ```
- **Observed Behavior**:
  - `amount`: Returns the provider-native amount (`hotel_offer.price.total_amount`), e.g. `225.0`.
  - `currency`: Returns the provider-native currency (`hotel_offer.price.currency`), e.g. `"USD"`.
  - `price_inr` is NOT returned at the top level of prebook response, although it was present in `offer_dict`.
- **Prebook vs Snapshot**: Prebook amount matches snapshot `total_amount` exactly. However, it returns `currency: "USD"` while subsequent Razorpay payment creation strictly expects `INR`.

---

## 6. Frontend Currency Display Behavior

### 1. `HotelCard.tsx`
- Line 278: `{formatCurrency(effectiveTotal, hotel.currency)}`
- Renders: `$225.00 total`.
- **Defect**: It does NOT display the INR reference amount or mention that payment will be processed in INR. (In contrast, `FlightCard.tsx` renders dual currency: `$225.00 (₹18,749 est.)`).

### 2. `BookingWorkspace.tsx`
- Lines 1272-1274 pass `roomAmount` and `currency` directly to `PaymentReviewModal`:
  ```tsx
  roomAmount={selectedHotel ? Number(selectedHotel.total_amount || selectedHotel.price || 0) : 0}
  taxesAmount={Math.round((selectedHotel ? Number(selectedHotel.total_amount || selectedHotel.price || 0) : 720) * 0.1)}
  currency={selectedHotel?.currency || trip.currency || 'USD'}
  ```

### 3. `PaymentReviewModal.tsx`
- Lines 68 & 269-281:
  - Room Charges: `formatCurrency(roomAmount, currency)` -> `$225.00`
  - Taxes & Tourism Fees: `formatCurrency(taxesAmount, currency)` -> `$23.00` (synthesized 10%)
  - Total: `formatCurrency(grandTotal, currency)` -> **`$248.00`**
- **Defect**: User reviews a bill for `$248.00 USD`. When clicking "Proceed to Pay", `onInitiateOrder()` calls the server, which creates a Razorpay order for **`₹18,749.00 INR`**. Razorpay Checkout modal pops up in INR, creating severe customer confusion.

### 4. `bookingService.ts` Semantic Bug (Lines 548 & 672)
- In fallback offer reconstruction:
  ```typescript
  const offer = cachedOffer || {
    id: prebook.hotel_id,
    provider_offer_id: prebook.hotel_id,
    provider: prebook.provider || 'liteapi',
    hotel_name: prebook.hotel_name,
    price: prebook.amount,
    currency: prebook.currency || 'INR',
    price_inr: prebook.amount, // <-- DANGEROUS: copies 225 USD into price_inr without FX conversion!
  };
  ```
  If `cachedOffer` is absent, `price_inr` becomes `225`. In legacy `/create-order` without snapshot, this would create an order for `₹225` instead of `₹18,750`.

---

## 7. Razorpay Currency Contract

### `RazorpayService.create_order()` (`services/razorpay_service.py`)
- Lines 67-79:
  ```python
  def create_order(self, amount_rupees: float, receipt: str, notes: dict[str, str] | None = None) -> dict[str, Any]:
      amount_paise = int(round(amount_rupees * 100))
      return self._request(
          "POST", "/orders",
          json={
              "amount": amount_paise,
              "currency": "INR",  # HARDCODED
              "receipt": receipt[:40],
              "notes": notes or {},
          },
      )
  ```
- **Currency is strictly hardcoded to `INR`**. The service accepts `amount_rupees` and calculates `amount_paise`. Multi-currency is not supported by `RazorpayService`.

---

## 8. Payment Verification Currency Behavior

### `verify_and_book` (`hotel_payment_api.py`)
- Lines 298-301:
  ```python
  if str(payment.get("currency") or "").upper() != "INR":
      raise HTTPException(status_code=400, detail="The captured hotel payment is not in INR.")
  if int(payment.get("amount") or 0) != int(round(float(pending["amount_inr"]) * 100)):
      raise HTTPException(status_code=400, detail="Captured payment amount does not match the stored hotel order.")
  ```
- **Validation**: Strict fail-closed validation. Any payment from Razorpay that is not in `INR` or does not match `pending["amount_inr"] * 100` paise is rejected with HTTP 400.

---

## 9. Supplier Booking Currency Behavior

### Supplier Settlement
1. **LiteAPI**:
   - `LiteAPIProvider.book()` passes `prebookId` and `payment: { method: "ACC_CREDIT_CARD" | "WALLET" }`.
   - The LiteAPI booking endpoint charges the agency's commercial account in LiteAPI's contracted currency (e.g. `USD`).
   - The customer never pays LiteAPI directly. TrustTrip is the merchant of record collecting INR via Razorpay, while TrustTrip settles with LiteAPI in USD.
2. **Hotelbeds**:
   - `HotelbedsProvider.book()` passes `rateKey`. Hotelbeds settles against the agency's commercial credit line in `EUR` or `USD`.
- **Conclusion**: The customer-facing transaction and the supplier settlement transaction occur across a legitimate commercial currency boundary:
  - **Customer Leg**: INR (via Razorpay).
  - **Supplier Leg**: USD/EUR (via Provider B2B API account).

---

## 10. Existing FX / Conversion Mechanism
- **Service**: `services/currency_converter.py` (`CurrencyConverter`).
- **Upstream Source**: Frankfurter API (`https://api.frankfurter.dev/v2/rate/{base}/{quote}`).
- **Backing Data**: European Central Bank (ECB) daily reference rates.
- **Freshness**: Updated once daily on ECB working days.
- **Storage**:
  - Search time: Cached in `HotelService.rate_cache`.
  - Snapshot time: Persisted in `offer_snapshots.normalized_offer.price.fx_rate_applied` and `inr_reference_amount`.
  - Transaction time: Persisted in `trust_trip_hotel_transactions.amount_inr`.
- **Emergency Fallback**: If Frankfurter is down during checkout order creation, fallback rate is hardcoded to `85.0` (`hotel_booking_service.py:123` and `hotel_payment_api.py:161`).

---

## 11. Scenario A–E Results

### Scenario A (Provider returns USD 225)
- **Search Result**: Emits `{ price: 225, currency: "USD", price_inr: 18749.25 }`.
- **Snapshot**: Persists `total_amount: 225, currency: "USD", inr_reference_amount: 18749.25`.
- **Prebook**: Returns `amount: 225, currency: "USD"`.
- **Customer UI**: Displays `$248.00` (`$225` + `$23` client tax).
- **Razorpay Checkout**: Opens in `INR` for `₹18,749.00`.
- **Supplier Booking**: Confirmed via `prebook_id` in `USD 225.00`.
- **Verdict**: **INCORRECT PRESENTATION / DISCONNECTED UX**. The backend correctly charges ₹18,749.25 and books $225, but the frontend misleads the user with $248 and abruptly changes currency to INR in Checkout.

### Scenario B (Provider returns INR 18,750)
- **Search Result**: Emits `{ price: 18750, currency: "INR", price_inr: 18750 }`.
- **Snapshot**: Persists `total_amount: 18750, currency: "INR", inr_reference_amount: 18750`.
- **Prebook**: Returns `amount: 18750, currency: "INR"`.
- **Customer UI**: Displays `₹20,625.00` (`₹18,750` + `₹1,875` client tax).
- **Razorpay Checkout**: Opens in `INR` for `₹18,750.00`.
- **Supplier Booking**: Confirmed in `INR 18,750.00`.
- **Verdict**: **PARTIAL DEFECT**. Currency is consistent (INR), but client-side tax in `PaymentReviewModal` creates a price discrepancy between the modal total (₹20,625) and Razorpay charge (₹18,750).

### Scenario C (Provider returns USD 225, but frontend displays ₹225)
- **Can it happen?**:
  In `HotelCard.tsx`, no (it formats using `hotel.currency`).
  However, in `bookingService.ts:547-548`, if `prebook.currency` was omitted, it defaulted to `INR`, setting `price: 225, currency: "INR"`.
- **Verdict**: Prevented in live flow by server snapshots, but present as a latent bug in frontend service fallback.

### Scenario D (Provider returns USD 225, frontend displays $225, but Razorpay charges ₹225)
- **Can it happen?**:
  In modern snapshot path: **NO** (server enforces `inr_reference_amount = 18749.25`).
  In legacy `/create-order` without a snapshot: **YES**. If `snapshot_id` is missing, `bookingService.ts:548` sends `price_inr: 225.0`. The legacy endpoint accepts `price_inr` and charges `₹225.00` for a $225 room!
- **Verdict**: **CRITICAL FINANCIAL RISK IN LEGACY PATH** if invoked without a snapshot.

### Scenario E (Exchange rate changes between search and payment)
- **Behavior**: The rate is frozen in `offer_snapshots` at search time (`inr_reference_amount`). During the 30-minute snapshot lifetime, the customer is guaranteed the exact INR amount calculated at search. If expired, prebook and checkout fail closed (409 Conflict), requiring a fresh search.
- **Verdict**: **CORRECT & SAFE**. Prevents mid-checkout rate slippage.

---

## 12. Currency Inconsistencies Found

| ID | Location | Inconsistency | Impact |
| :--- | :--- | :--- | :--- |
| **INC-01** | `HotelCard.tsx:278` | Only renders provider currency (e.g. `$225`), omitting `price_inr` | Customer is unaware that checkout will be charged in INR |
| **INC-02** | `PaymentReviewModal.tsx:281` | Displays total in provider currency (`$248`), then opens Razorpay in `INR` | Extreme UX confusion / currency mismatch shock |
| **INC-03** | `PaymentReviewModal.tsx:68` | Client synthesizes 10% tax (`roomAmount * 0.1`) that server never charges | Modal total differs from Razorpay charged amount |
| **INC-04** | `bookingService.ts:548, 672` | Fallback sets `price_inr: prebook.amount` (1 USD = 1 INR) | Severe undercharging if legacy order is created without snapshot |
| **INC-05** | `liteapi_provider.py:46` | Defaults search to `"USD"` instead of trip/tenant preference | Forces international USD currency even for domestic Indian searches |

---

## 13. Product Architecture Options

### Option A: Customer Pays USD via Razorpay
- **Feasibility**: NOT supported. `RazorpayService.create_order()` hardcodes `"INR"`. `verify-and-book` explicitly rejects non-INR payments. Razorpay international currency support requires RBI authorizations not present in code.

### Option B: Provider USD -> Authoritative USD->INR Conversion -> Customer Pays INR (Current Intended Backend Architecture)
- **Feasibility**: Fully supported by backend (`CurrencyConverter`, `PriceBreakdown.inr_reference_amount`, `HotelTransactionStore.amount_inr`).
- **Requirement**: Fix frontend presentation to truthfully disclose dual currency:
  - Display `$225.00 (₹18,749)` on `HotelCard`.
  - In `PaymentReviewModal`, show: Provider Total (`$225.00`) + Guaranteed INR Exchange Rate (`83.33`) = Total to Pay (`₹18,749.00 INR`).
  - Eliminate client-side 10% tax hallucination.

### Option C: Provider Returns Native INR Directly
- **Feasibility**: High for LiteAPI. In `LiteAPIProvider.search()`, LiteAPI accepts `"currency": "INR"`. If TrustTrip passes `"currency": "INR"`, LiteAPI returns native INR prices directly, eliminating FX conversion overhead.
- **Limitation**: Hotelbeds may still return EUR/USD depending on the supplier contract.

---

## 14. Recommended Currency Contract

We recommend adopting **Option B with Option C optimization**:
1. **Primary**: Pass `currency: "INR"` to hotel providers (LiteAPI) during search so providers return native INR whenever supported.
2. **Fallback / Multi-Currency**: When a provider returns foreign currency (`USD`, `EUR`):
   - Server computes and freezes `inr_reference_amount` and `fx_rate_applied` in the snapshot (already working).
   - Frontend displays **dual currency** identically to `FlightCard.tsx`:
     `$225.00 (₹18,749 est.)`.
   - `PaymentReviewModal` explicitly presents:
     - Room Rate: `$225.00 USD`
     - Guaranteed Conversion Rate: `1 USD = ₹83.33`
     - Authoritative Payment Total: **`₹18,749.00 INR`**
   - Eliminate client-synthesized 10% tax.
3. **Service Layer**: Fix `bookingService.ts:548` to never copy `prebook.amount` directly into `price_inr`.

---

## 15. Implementation Status
- **Implementation Required?**: **YES** (Scheduled for a dedicated post-freeze wave).
- **Current Wave Action**: **NONE**. Current wave remains 100% frozen at `413d575`.

---

## 16. Minimal Future Implementation Scope (Future Wave)
1. **Frontend Presentation**:
   - Update `HotelCard.tsx` to render dual currency (`USD` + `INR reference`) when `currency != 'INR'` and `price_inr` exists.
   - Update `PaymentReviewModal.tsx` to display the exact server-authoritative INR amount and exchange rate, removing synthetic 10% taxes.
2. **Frontend Service**:
   - Fix `bookingService.ts` lines 548 and 672 to prevent assigning `prebook.amount` to `price_inr` without conversion.
3. **Provider Search**:
   - Update `hotel_api.py:300` and `LiteAPIProvider.search()` to pass `"currency": "INR"` by default for INR-denominated trips.

---

## 17. Required Tests for Future Implementation
1. `test_hotel_dual_currency_display`: Verify hotel cards render both foreign currency and INR estimate.
2. `test_payment_modal_currency_truthfulness`: Verify `PaymentReviewModal` displays authoritative INR amount matching Razorpay order.
3. `test_no_1_usd_equals_1_inr_fallback`: Verify `bookingService` never emits 1:1 USD:INR fallback.
4. `test_liteapi_inr_request`: Verify LiteAPI search requests INR when configured.

---

## 18. Risks
- Delaying the frontend fix leaves customer confusion where the modal says `$248` and Razorpay charges `₹18,749`.
- However, financial integrity is safeguarded on the server because `create-order-from-snapshot` enforces the snapshot's calculated `inr_reference_amount`.

---

## 19. Confirmation of Read-Only Audit State
- **Code Modified**: **NO** (0 files modified).
- **Tests Modified**: **NO** (0 tests modified).
- **Schema Modified**: **NO** (0 schema changes).
- **Commit Created**: **NO** (0 commits).
- **Push Performed**: **NO** (0 pushes).
- **Working Tree**: Preserved exactly at frozen commit `413d575`.
