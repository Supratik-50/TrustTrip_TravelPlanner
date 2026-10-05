# WAVE 3D-08 — Pre-Implementation Investigation

## 1. Executive Summary

This investigation independently examines defect **W3D-08 — LiteAPI hotel `pricing_basis` truthfulness** in `TrustTrip_TravelPlanner`.

### Primary Finding
The existing audit finding — *"LiteAPI pricing_basis None; fail closed; fix provider later"* — is **CONFIRMED ACCURATE AND VALIDATED AGAINST ACTIVE CODE**.
Specifically:
1. LiteAPI returns rate quotes for explicit check-in and check-out dates (`checkin` to `checkout`). The total amount in the supplier payload (`rate.retailRate.total[0].amount`) represents the **full stay cost** for the requested duration.
2. In `services/hotel/liteapi_provider.py:LiteAPIProvider.search()`, the provider adapter extracts `amount`, `currency`, `checkin`, and `checkout`, but **omits** `pricing_basis="full_stay"` and `nights`.
3. In `services/travel_planner.py:budget_from_live_data()`, when raw offer dictionaries from `HotelService().search()` are evaluated, the function checks `basis = getattr(o, "pricing_basis", None) or o.get("pricing_basis")`, `offer_nights > 1`, and `o.get("total_price")`. Because all three are missing/None on LiteAPI offer dictionaries, the planner safely **fails closed**, logging:
   `Hotel offer 'liteapi:...' has unrecognized pricing_basis=None; skipping offer to avoid silent full_stay assumption.`
4. Consequently, all valid LiteAPI hotel offers are discarded from the live itinerary budget, forcing the planner to fall back to regex scraping of unstructured text or the default ₹1,800/night planning estimate.
5. In the dedicated checkout and payment pipeline (`hotel_api.py`, `HotelBookingService`, `HotelTransactionStore`, and `hotel_payment_api.py`), financial safety is **strictly preserved**: hotel checkout operates on immutable snapshots, supplier prebook holds, rate revalidation gates, and authoritative `amount_inr` match checks before supplier booking. `pricing_basis` is not used in financial ledger tables or transaction records.
6. The defect is fully contained within the provider normalization layer. **NO SCHEMA CHANGE OR MIGRATION IS REQUIRED.**
7. The pre-audit recommendation is **READY FOR IMPLEMENTATION**.

---

## 2. Baseline and Working-Tree Safety

### Recorded Git State
- **Current HEAD**: `03e64e6` (`feat(wave3d-b): reconcile flight bookings and handle planner outages`)
- **Parent Commit**: `e4cdf95` (`feat(wave3d-a): fix runtime booking consistency`)
- **Current Branch**: `main`
- **Frozen Commit Status**:
  - Wave 3D-B commit `03e64e6` remains unmodified and frozen.
  - Wave 3D-A commit `e4cdf95` remains unmodified and frozen.
  - Wave 3C commit `d21ef82` remains unmodified and frozen.
  - Wave 3B commit `de75bbc` remains unmodified and frozen.

### Working-Tree Protection Verification
[OBSERVED FACT]
The working tree contains pre-existing modified and untracked files from earlier waves, including:
- `migrations/012_hotel_snapshot_long_provider_offer_id.sql` (PRE-EXISTING UNTRACKED)
- Modified frontend files (`HotelsView.tsx`, `TripDetailView.tsx`, `PaymentReviewModal.tsx`, etc.)
- Modified backend files (`hotel_api.py`, `payment_api.py`, `services/hotel_transaction_store.py`, `services/travel_planner.py`, `services/ticket_service.py`)
- Various untracked test fixtures and documentation reports

**All pre-existing working tree content was preserved untouched during this investigation.**
No files were staged, reset, restored, cleaned, or committed. No database migrations or DDL were executed.

---

## 3. W3D-08 Scope

### Authorized Scope
- Controlled investigation of hotel `pricing_basis` truthfulness for LiteAPI.
- End-to-end tracing of hotel pricing data flow from search to checkout and display.
- Identification of root cause for `pricing_basis=None`.
- Assessment of financial safety and truthfulness.
- Determination of schema/database impact.
- Specification of minimal remediation and regression test suite.

### Scope Boundaries (Strictly Excluded)
- Implementation of any code changes (W3D-08 implementation is NOT authorized in this phase).
- W3D-06 reconciliation/idempotency post-freeze findings (`SELECT FOR UPDATE`, provider idempotency propagation).
- W3D-07 planner outage handling enhancements.
- Modifications to `migrations/012_hotel_snapshot_long_provider_offer_id.sql`.
- Modifications to database schema, tables, enums, or migrations.
- Modification of Razorpay, flight payment, or cancellation services.

---

## 4. End-to-End Hotel Pricing Call Chain

[OBSERVED FACT]
The TrustTrip repository contains two related hotel search and pricing pathways:
1. **Chat Planner Flow** (`backend.py` $\to$ `hotel_agent` $\to$ `budget_agent` $\to$ `travel_planner.py`): Returns raw provider offer dictionaries into `live_hotel_offers` for itinerary planning and budget estimation.
2. **Dedicated UI / Checkout Flow** (`hotel_api.py` $\to$ `SearchSessionStore` $\to$ `HotelBookingService` $\to$ `hotel_payment_api.py`): Powers the Hotels tab, room selection, snapshot persistence, prebook holds, and Razorpay checkout.

### Comprehensive Call-Chain Table

| Step | File | Class / Function | Input | Output | `pricing_basis` Involvement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Hotel Search (Provider)** | `services/hotel/liteapi_provider.py` | `LiteAPIProvider.search()` | `request: dict` (`destination`, `checkin`, `checkout`, `adults`, `rooms`, `currency`) | `list[dict]` of raw hotel offers with `price`, `currency`, `checkin`, `checkout` | **OMITTED** (`pricing_basis` and `nights` are NOT set in the returned dictionary). |
| **2. Multi-Provider Orchestration & FX** | `services/hotel/hotel_service.py` | `HotelService.search()` | `request: dict` | `dict` containing `offers: list[dict]`, sorted by score | **PASS-THROUGH** (Applies FX to compute `price_inr`; does not add `pricing_basis` or `nights`). |
| **3. Chat Hotel Agent** | `backend.py` | `hotel_agent()` | `state: TravelState` | `dict` with `live_hotel_offers: list[dict]` | **PASS-THROUGH** (Populates `live_hotel_offers` in LangGraph state). |
| **4. Chat Budget Agent** | `backend.py` | `budget_agent()` | `state: TravelState` | `dict` with `live_plan: {"budget": summary}` | Calls `budget_from_live_data()` with `live_hotel_offers`. |
| **5. Itinerary Budget Estimation** | `services/travel_planner.py` | `budget_from_live_data()` | `constraints`, `flight_offers`, `hotel_text`, `hotel_offers` | `dict` with `hotel_estimate_inr`, `hotel_pricing_basis`, `hotel_price_source` | **FAIL-CLOSED EVALUATION**: `basis = o.get("pricing_basis")`. Because `basis is None` and `nights` is missing, logs warning and **skips offer**. Budget falls back to regex snippet / estimate. |
| **6. Dedicated Hotel Search & Snapshots** | `hotel_api.py` | `search_hotels()` | `HotelSearchRequest`, `trip_id` | `dict` with `offers: list[dict]` | Computes `nights = (d_out - d_in).days`, persists `HotelOffer` domain model to `SearchSessionStore`, emits `normalized_offers` with `total_amount`, `nightly_price`, `nights`. |
| **7. Hotel Offer Selection** | `hotel_api.py` | `select_hotel()` | `HotelSelectRequest` (`trip_id`, `snapshot_id`, `room_id`) | Updated trip dictionary | Binds `snapshot_id` to trip metadata `selected_hotel_snapshot_id`. No `pricing_basis` evaluation. |
| **8. Hotel Prebook Hold** | `hotel_api.py` | `prebook_hotel()` | `HotelPrebookRequest` (`trip_id`, `snapshot_id`) | `dict` with `prebook_id`, `amount`, `currency`, `expires_at` | Resolves authoritative snapshot, invokes `HotelService().prebook()`, returns supplier hold reference and total amount. |
| **9. Revalidation & Order Creation** | `services/hotel_booking_service.py` | `HotelBookingService.create_checkout_order_from_snapshot()` | `snapshot_id`, `user_id`, `trip_id`, `revision_id`, `idempotency_key` | `dict` with `transaction_id`, `razorpay_order_id`, `amount_inr` | Revalidates supplier rate, executes `assert_booking_eligible`, reserves transaction in `HotelTransactionStore`, creates Razorpay order. |
| **10. Payment Signature Verification** | `hotel_payment_api.py` | `verify_and_book()` | `HotelVerifyBook` (`transaction_id`, `razorpay_order_id`, `razorpay_payment_id`, `signature`) | `dict` with `success`, `booking`, `amount_inr` | Verifies Razorpay HMAC signature, verifies captured amount equals `amount_inr * 100`, claims transaction atomically, executes supplier booking. |
| **11. Supplier Booking Execution** | `services/hotel/liteapi_provider.py` | `LiteAPIProvider.book()` | `prebook`, `holder`, `guests`, `client_reference` | `dict` with `booking_id`, `confirmation_code`, `status` | Issues POST to `/rates/book` using confirmed `prebookId`. |
| **12. Transaction Persistence** | `services/hotel_transaction_store.py` | `HotelTransactionStore.update()` | `tx_id`, `booking_status='booked'`, `booking_json` | Persisted row in `trust_trip_hotel_transactions` | Persists `offer_json`, `booking_json`, `amount_inr`. |
| **13. Booking Read Service** | `services/booking_read_service.py` | `BookingReadService._get_hotel_booking_for_trip()` | `trip_id`, `user_id` | Canonical booking summary dict | Reads `amount_inr`, `hotel_name`, `check_in`, `check_out` for frontend display. |
| **14. Frontend Display** | `frontend/src/components/travel/HotelCard.tsx` | `HotelCard` component | `hotel: HotelOffer` | Rendered card with total and nightly rate | Displays `effectiveTotal` labeled "total", and `effectiveNightly` labeled `/ night for {stayNights} nights stay`. |

---

## 5. `pricing_basis` Data Flow

### Definition and Semantics
`pricing_basis` is an internal string token representing whether a numerical price quotation applies to:
- `"full_stay"`: Total monetary amount for the entire duration of the booking across all requested nights.
- `"per_night"`: Monetary amount for a single night, requiring multiplication by the number of stay nights (`price * nights`) to determine the stay cost.
- `"unknown"` / missing: Undetermined semantic basis.

### Complete Lifecycle of `pricing_basis` in Active Code
1. **Creation**:
   - `services/travel_planner.py:budget_from_live_data()` creates a local variable `primary_pricing_basis = "unknown"`.
   - In test fixtures (`tests/test_section4_remediation.py:113, 125, 370`), test dictionaries explicitly set `"pricing_basis": "per_night"` or `"pricing_basis": "full_stay"`.
   - In `services/hotel/liteapi_provider.py`, it is **NEVER created or assigned**.
   - In `services/hotel/hotelbeds_provider.py`, it is **NEVER created or assigned**.
   - In `services/travel_intelligence/liteapi_adapter.py`, it is **NEVER created or assigned**.
   - In `hotel_api.py`, it is **NEVER created or assigned**.
2. **Assignment & Transformation**:
   - In `services/travel_planner.py:1107`:
     ```python
     basis = getattr(o, "pricing_basis", None) or (o.get("pricing_basis") if isinstance(o, dict) else None)
     ```
   - If `hasattr(o, "nights") and hasattr(o, "price")`:
     `primary_pricing_basis = "full_stay"`
   - If `isinstance(o, dict)`:
     - If `basis == "per_night"` or `"night"` in `o.get("rate_type")`: `primary_pricing_basis = "per_night"`
     - If `basis == "full_stay"` or `offer_nights > 1` or `o.get("total_price") is not None`: `primary_pricing_basis = "full_stay"`
     - Otherwise: logs warning and skips offer.
3. **Persistence**:
   - `pricing_basis` is **NOT persisted in any relational database table or column**.
   - Neither `offer_snapshots` nor `trust_trip_hotel_transactions` has a `pricing_basis` column.
   - If present in an offer dictionary, it would reside solely inside the encrypted `raw_payload` or `offer_json` JSONB blobs.
4. **API Responses**:
   - Emitted in the return dictionary of `budget_from_live_data`:
     `"hotel_pricing_basis": primary_pricing_basis`
   - Emitted in `backend.py:budget_agent` as part of the human-readable text:
     `"Hotel pricing source: live dual-provider hotel offer (full_stay)"` vs `"planning estimate (fallback)"`.
5. **Frontend Consumption**:
   [OBSERVED FACT]
   The frontend (`frontend/src/`) does **NOT** consume or reference `pricing_basis` or `hotel_pricing_basis`.
   `frontend/src/types/hotel.ts` and `frontend/src/types/trip.ts` contain no definition for `pricing_basis`.
   Frontend display logic in `HotelCard.tsx` consumes `total_amount`, `nightly_price`, and `nights`.
6. **Consequence when `pricing_basis` is None/Missing**:
   - For domain objects with `.nights` and `.price` (`HotelOffer`): Treated as `full_stay` because both duration and structured price breakdown exist.
   - For plain dictionaries (from `HotelService.search()`): Evaluates as unrecognized, causing the offer to be **skipped** and excluded from the normalized stays list.

---

## 6. LiteAPI Provider Response Analysis

[OBSERVED FACT]
Inspecting `services/hotel/liteapi_provider.py` and test mock fixtures in `tests/test_section6_hotel_search.py:1839-1860`:

### LiteAPI Search Query & Payload
- Search Endpoint: `POST https://api.liteapi.travel/v3.0/hotels/rates`
- Request Parameters:
  - `checkin`: ISO Date string (e.g., `"2026-10-14"`)
  - `checkout`: ISO Date string (e.g., `"2026-10-20"`)
  - `occupancies`: Array of adult and child counts
  - `currency`: 3-letter currency code (e.g., `"USD"`)
  - `guestNationality`: 2-letter ISO country code (e.g., `"IN"`)
  - `maxRatesPerHotel`: `1`
  - `roomMapping`: `True`
  - `includeHotelData`: `True`

### Received Pricing Fields Breakdown

| Field Name | Source in LiteAPI Response | Semantic Meaning | Data Type | Mandatory / Optional | Persisted in DB | Exposed to Frontend | Supports `pricing_basis`? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `amount` | `rate.retailRate.total[0].amount` | Total retail price for the **entire stay** across all requested nights | `float` / `int` | Mandatory for inclusion (skipped if missing) | Yes (as `price_total` in snapshots, `amount_inr` in transactions) | Yes (as `total_amount` and `price`) | **YES** — Represents authoritative full stay price. |
| `currency` | `rate.retailRate.total[0].currency` | ISO 4217 currency code of the rate quote | `str` (3 chars) | Mandatory in `hotel_api.py` (skipped if missing) | Yes (`currency`) | Yes (`currency`) | Contextual currency for `amount`. |
| `offerId` | `rate.offerId` or `room.offerId` | Supplier rate key for prebook hold | `str` | Mandatory for booking | Yes (`provider_offer_id`) | No (shielded server-side) | Key for rate lock. |
| `cancellationPolicies` | `rate.cancellationPolicies` | Cancellation rules and refundability | `dict` | Optional | Yes (in `raw_payload`) | Normalized as `refundable` bool | Refund terms. |
| `refundableTag` | `rate.cancellationPolicies.refundableTag` | Refundable indicator (`RFN` or `NRF`) | `str` | Optional | Yes (in `raw_payload`) | Normalized as `refundable` bool | Unrelated to pricing basis. |
| `base price` | Not parsed by `liteapi_provider.py` | Net supplier rate before markup/taxes | `None` / omitted | Optional in raw | No | No | Not received. |
| `taxes / fees` | `rate.retailRate.taxesAndFees` (unparsed) | Itemized tax breakdown | `None` / omitted | Optional in raw | No | No | Not received. |
| `markup / commission` | Not provided in LiteAPI retail rate response | Intermediary margin | `None` | N/A | No | No | Not received. |
| `prebookId` | `POST /rates/prebook` $\to$ `data.prebookId` | Active supplier hold identifier | `str` | Mandatory at prebook | Yes (in `prebook_json`) | No (shielded) | Establishes final hold. |
| `bookingId` | `POST /rates/book` $\to$ `data.bookingId` | Confirmed supplier reservation ID | `str` | Mandatory at booking | Yes (`booking_json`) | Yes (as `confirmation_code`) | Final confirmed booking. |

### Technical Verification of Duration
LiteAPI rate search is explicitly constrained by `checkin` and `checkout` query parameters. LiteAPI does not return an open-ended nightly tariff; it computes and returns the **aggregate retail rate for that specific interval**.
Therefore, `rate.retailRate.total[0].amount` is unconditionally a **full-stay** price quote for the requested dates.

---

## 7. Root Cause of `pricing_basis=None`

[OBSERVED FACT]
The root cause is classified as:
**B. Provider adapter failing to normalize available information**
combined with
**F. Intentional fail-closed behavior in `travel_planner.py`**

### Detailed Code Evidence
1. **Omission in Provider Adapter (`services/hotel/liteapi_provider.py:105-130`)**:
   When `LiteAPIProvider.search()` constructs each offer dictionary, it writes:
   ```python
   offers.append({
       "id": f"liteapi:{offer_id}",
       "provider": self.name,
       "provider_offer_id": offer_id,
       ...
       "price": amount,
       "currency": currency,
       "rooms": rooms,
       "adults": int(request.get("adults", 1)),
       "children": int(request.get("children", 0)),
       "checkin": request.get("checkin"),
       "checkout": request.get("checkout"),
       "raw": rate,
   })
   ```
   The dictionary completely omits:
   - `"pricing_basis"`
   - `"nights"`
   - `"total_price"`
   Even though `request["checkin"]` and `request["checkout"]` are present in `request`, the provider never calculates `nights = (checkout - checkin).days` and never declares `"pricing_basis": "full_stay"`.

2. **Rejection in Itinerary Budget Calculator (`services/travel_planner.py:1106-1137`)**:
   ```python
   for o in hotel_offers:
       basis = getattr(o, "pricing_basis", None) or (o.get("pricing_basis") if isinstance(o, dict) else None)
       if hasattr(o, "nights") and hasattr(o, "price"):
           price_val = float(o.price.inr_reference_amount) if getattr(o.price, "inr_reference_amount", None) is not None else float(o.price.total_amount)
           if price_val > 0:
               normalized_hotel_stays.append(price_val)
               primary_pricing_basis = "full_stay"
       elif isinstance(o, dict):
           p_inr = o.get("price_inr") or o.get("price") or o.get("price_total") or o.get("total_amount")
           if p_inr is not None and float(p_inr) > 0:
               val = float(p_inr)
               offer_nights = int(o.get("nights") or 0)
               if basis == "per_night" or "night" in str(o.get("rate_type") or "").lower():
                   stay_nights = offer_nights if offer_nights > 0 else trip_nights
                   normalized_hotel_stays.append(val * stay_nights)
                   primary_pricing_basis = "per_night"
               elif basis == "full_stay" or offer_nights > 1 or o.get("total_price") is not None:
                   normalized_hotel_stays.append(val)
                   primary_pricing_basis = "full_stay"
               else:
                   import logging as _logging
                   _logging.getLogger("trusttrip.budget").warning(
                       "Hotel offer '%s' has unrecognized pricing_basis=%r; "
                       "skipping offer to avoid silent full_stay assumption.",
                       o.get("id", "<unknown>"),
                       basis,
                   )
   ```
3. **Execution Path Analysis**:
   - `o` is a plain `dict` emitted by `LiteAPIProvider.search()`.
   - `hasattr(o, "nights")` is `False`.
   - `isinstance(o, dict)` is `True`.
   - `p_inr` is positive.
   - `basis` is `None` (key does not exist).
   - `offer_nights` is `0` (key `"nights"` does not exist).
   - `basis == "per_night"` $\to$ `False`.
   - `"night" in str(o.get("rate_type") or "").lower()` $\to$ `False`.
   - `basis == "full_stay"` $\to$ `False`.
   - `offer_nights > 1` $\to$ `False` (`0 > 1` is False).
   - `o.get("total_price") is not None` $\to$ `False` (`"total_price"` is not set).
   - The code enters `else:` and **logs the warning and skips the offer**.

---

## 8. Pricing Truthfulness and Financial Safety

### Safety Evaluation
Overall Classification: **CONDITIONALLY SAFE**

| Evaluation Criterion | Assessment | Technical Evidence |
| :--- | :--- | :--- |
| **Can the application claim a price is authoritative when `pricing_basis` is None?** | **NO** | In the budget planner, offers with `pricing_basis=None` are skipped. The budget output explicitly reports `hotel_price_source: planning estimate (fallback)` or `live search snippet` rather than claiming authoritative live pricing. |
| **Can checkout proceed when `pricing_basis` is unknown?** | **YES, BUT SAFELY** | Checkout does not evaluate `pricing_basis`. Checkout operates through `HotelBookingService`, which uses the authoritative snapshot `total_amount` from LiteAPI (`rate.retailRate.total[0].amount`) and locks it via supplier prebook. |
| **Can the frontend display a price without identifying its basis?** | **NO** | `HotelCard.tsx:280-288` explicitly displays `{formatCurrency(effectiveTotal, currency)} total` and `{formatCurrency(effectiveNightly, currency)} / night for {stayNights} nights stay`. |
| **Can payment amount differ from provider-authoritative amount?** | **NO** | `hotel_payment_api.py:211` strictly validates that Razorpay captured amount equals stored `amount_inr * 100`: `if int(payment.get("amount") or 0) != int(round(float(pending["amount_inr"]) * 100)): raise HTTPException(400)`. |
| **Can a stale search price reach payment?** | **NO** | `HotelBookingService.create_checkout_order_from_snapshot()` executes `planner.revalidate_offer(snapshot_id)`. If the supplier price or availability changed, it raises `FareChangedException` (409) or `OfferWithdrawnException` (410). |
| **Does prebook/recheck establish the final authoritative price?** | **YES** | Prebook calls LiteAPI `/rates/prebook` to obtain a `prebook_id`. If simulated or unavailable, checkout fails closed with HTTP 409/422. |
| **Is the system fail-open or fail-closed?** | **FAIL-CLOSED** | The budget calculator intentionally fails closed by discarding unverified offers rather than making unsafe arithmetic assumptions. |

### Why "CONDITIONALLY SAFE"?
- **Financial integrity is 100% SAFE**: Under no circumstance can a user be charged an arbitrary or unverified amount. The amount charged is strictly the supplier-held retail price.
- **Planner truthfulness is DEGRADED**: The chat planner discards legitimate, fresh hotel search results from budget calculations and reports a generic ₹1,800/night fallback estimate because the provider omitted the metadata flag `pricing_basis="full_stay"`.

---

## 9. Database / Schema Impact

### Verdict
**NO SCHEMA CHANGE REQUIRED**

### Detailed Architectural Justification
1. `pricing_basis` is an ephemeral in-memory domain attribute used for budget aggregation in `services/travel_planner.py`.
2. Existing PostgreSQL tables for hotel intelligence and checkout:
   - `search_sessions`: Stores session criteria and status.
   - `offer_snapshots`: Stores `snapshot_id`, `price_total`, `currency`, and encrypted `raw_payload`.
   - `trust_trip_hotel_transactions`: Stores `id`, `provider`, `hotel_name`, `amount_inr`, `offer_json`, `prebook_json`, `payment_status`, `booking_status`.
3. Neither table defines or requires a `pricing_basis` column.
4. No enum modifications, no DDL scripts, and no database migrations are needed.

---

## 10. Proposed Minimal Remediation

*(Investigation only — No implementation authorized)*

### Target File
[services/hotel/liteapi_provider.py](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/hotel/liteapi_provider.py)

### Method
`LiteAPIProvider.search(self, request: dict[str, Any]) -> list[dict[str, Any]]`

### Specific Changes
1. **Compute Stay Duration**:
   Calculate `nights` from `checkin` and `checkout` dates present in `request`:
   ```python
   from datetime import date
   try:
       d_in = date.fromisoformat(str(request.get("checkin")).split("T")[0])
       d_out = date.fromisoformat(str(request.get("checkout")).split("T")[0])
       nights = max(1, (d_out - d_in).days)
   except Exception:
       nights = 1
   ```
2. **Populate Truthful Pricing Attributes**:
   In the dictionary appended to `offers` (around line 120):
   ```python
   offers.append({
       ...
       "price": amount,
       "total_price": amount,
       "pricing_basis": "full_stay",
       "nights": nights,
       "currency": currency,
       ...
   })
   ```

### Secondary Co-located Observation (Hotelbeds)
In `services/hotel/hotelbeds_provider.py:117-127`, `HotelbedsProvider.search()` also emits `price` without `nights`, `pricing_basis`, or `total_price`. If multi-provider consistency is required, the exact same three keys can be populated there.

### Downstream Impact
- `services/travel_planner.py:budget_from_live_data()` will now match:
  `elif basis == "full_stay" or offer_nights > 1 or o.get("total_price") is not None:`
  and will append `val` directly to `normalized_hotel_stays`, reporting:
  `primary_pricing_basis = "full_stay"`
  `hotel_price_source = "live dual-provider hotel offer (full_stay)"`.
- The warning log will no longer trigger for valid LiteAPI offers.
- No frontend changes are required.

---

## 11. Required Regression Tests

When implementation is authorized, the following tests must be executed or introduced:

1. **LiteAPI Provider Output Normalization**:
   - `test_liteapi_populates_pricing_basis_and_nights`: Assert `LiteAPIProvider.search()` returns offers containing `pricing_basis="full_stay"` and `nights=(checkout - checkin).days`.
2. **Budget Planner Integration**:
   - `test_budget_from_live_data_accepts_liteapi_offers`: Feed live LiteAPI offer dictionaries into `budget_from_live_data()`; assert offers are included without warning, `hotel_pricing_basis == "full_stay"`, and `hotel_estimate_inr` equals the lowest offer total.
3. **Fail-Closed Preservation**:
   - `test_hotel_unrecognized_pricing_basis_skipped`: Verify that an offer with `pricing_basis=None` and `nights=0` continues to be skipped fail-closed (existing test in `tests/test_section4_remediation.py:356-384`).
4. **Hotel Search Snapshot Regression**:
   - Run `tests/test_section6_hotel_search.py` (specifically `TestLiteAPIProviderTruthfulness`).
5. **Hotel Checkout & Prebook Regression**:
   - Run `tests/test_wave3a_hotel_prebook_payment.py`.
   - Run `tests/test_hotel_booking_integration.py`.
6. **Existing Wave 3D Baseline Regression**:
   - Run `tests/test_wave3d_b_reconciliation_and_resilience.py`.

---

## 12. Exact Files Potentially In Scope

### Production Code (To be modified in implementation phase)
- [services/hotel/liteapi_provider.py](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/hotel/liteapi_provider.py) (Add `pricing_basis`, `nights`, `total_price` to `search()` offer dict)

### Test Code (To be added / updated in implementation phase)
- [tests/test_section6_hotel_search.py](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/tests/test_section6_hotel_search.py) (Add unit test verifying `pricing_basis="full_stay"` and `nights` normalization)
- [tests/test_section4_remediation.py](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/tests/test_section4_remediation.py) (Verify integration with `budget_from_live_data`)

---

## 13. Out-of-Scope Items

The following are strictly out of scope for W3D-08:
- `migrations/012_hotel_snapshot_long_provider_offer_id.sql`
- Any modification to database schemas, tables, views, or DDL
- Flight booking reconciliation or idempotency (`W3D-06`)
- Gemini planner retry resilience (`W3D-07`)
- Cancellation service modifications (`Wave 3C`)
- Razorpay payment modal or signature verification
- Any package/dependency modifications in `package.json` or `pyproject.toml`

---

## 14. Risks and Open Questions

| Risk / Question | Assessment | Mitigation |
| :--- | :--- | :--- |
| **Risk of double multiplication** | If an offer were erroneously marked `per_night` when its price was full-stay, the budget would multiply by nights (e.g. ₹50,000 for 5 nights would become ₹250,000). | LiteAPI is explicitly `full_stay` because LiteAPI rates are queried per date interval. Marking `full_stay` prevents any multiplication. |
| **Hotelbeds parity** | Does Hotelbeds also omit `pricing_basis`? | Yes, Hotelbeds omits `pricing_basis` and `nights`. It can either be addressed alongside LiteAPI or tracked as a follow-up normalization task. |
| **Single-night stays (`nights = 1`)** | When `nights = 1`, `per_night` and `full_stay` produce identical arithmetic. | Marking `pricing_basis="full_stay"` explicitly removes ambiguity regardless of night count. |

---

## 15. PRE-AUDIT RECOMMENDATION

```text
============================================================
PRE-AUDIT DECISION: READY FOR IMPLEMENTATION
============================================================
```

### Justification
1. **Root cause is established**: `LiteAPIProvider.search()` fails to emit `pricing_basis="full_stay"`, `nights`, and `total_price` on raw offer dictionaries, causing `budget_from_live_data()` to trigger its fail-closed skip guard.
2. **Remediation is minimal, safe, and surgical**: Affects solely the dictionary construction in `LiteAPIProvider.search()`.
3. **No schema changes**: Zero migrations, zero DDL, zero database changes.
4. **Financial safety confirmed**: Checkout, snapshots, and payments operate on authoritative locked amounts and are not compromised.
5. **Clean scope boundary**: Completely decoupled from protected working-tree modifications and frozen commits.
