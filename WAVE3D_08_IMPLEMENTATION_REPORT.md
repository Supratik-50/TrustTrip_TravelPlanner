# WAVE 3D-08 — Implementation Report

## 1. Scope

- **Wave**: 3D-08 (Controlled Implementation)
- **Task**: Fix LiteAPI hotel offer normalization so valid LiteAPI offers participate correctly in live planner budget calculations.
- **Baseline**: `03e64e6` (Wave 3D-B freeze)
- **Parent**: `e4cdf95` (Wave 3D-A freeze)
- **Authorized File Modified**:
  - `services/hotel/liteapi_provider.py`
- **Authorized Test Added**:
  - `tests/test_w3d08_hotel_pricing_basis.py`
- **Strictly Excluded**:
  - Hotel checkout logic
  - Razorpay payment verification
  - Database schema, tables, models, enums, DDL, and migrations
  - Pre-existing working-tree modifications and untracked files
  - Commits and pushes

---

## 2. Root Cause

In `services/hotel/liteapi_provider.py:LiteAPIProvider.search()`, parsed hotel offers emitted:
- `price: amount`
- `currency: currency`
- `checkin: request.get("checkin")`
- `checkout: request.get("checkout")`

However, the adapter omitted:
- `pricing_basis`
- `nights`
- `total_price`

Downstream in `services/travel_planner.py:budget_from_live_data()`, when raw offer dictionaries from `HotelService().search()` were evaluated, the planner inspected `basis = o.get("pricing_basis")`, `offer_nights > 1`, and `o.get("total_price")`. Because all three were absent/None on LiteAPI offer dictionaries, the planner safely failed closed, logging:
`Hotel offer 'liteapi:...' has unrecognized pricing_basis=None; skipping offer to avoid silent full_stay assumption.`

Consequently, all valid LiteAPI hotel offers returned during live chat planning were discarded from the budget calculation, forcing the budget agent to fall back to regex scraping or the ₹1,800/night planning estimate.

---

## 3. Changes Made

In `services/hotel/liteapi_provider.py`:
1. **Added Date Import**:
   ```python
   from datetime import date
   ```
2. **Computed Stay Duration (`nights`)**:
   Derived the exact stay duration from `request["checkin"]` and `request["checkout"]`:
   ```python
   try:
       d_in = date.fromisoformat(str(request.get("checkin") or "").split("T")[0])
       d_out = date.fromisoformat(str(request.get("checkout") or "").split("T")[0])
       nights = max(1, (d_out - d_in).days)
   except Exception:
       nights = 1
   ```
3. **Populated Normalized Pricing Attributes on Every Offer**:
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

---

## 4. LiteAPI Normalization

- **Authoritative Provider Amount**:
  `amount = total.get("amount")` from `rate.retailRate.total[0]`.
  The LiteAPI endpoint queried (`https://api.liteapi.travel/v3.0/hotels/rates`) takes explicit `checkin` and `checkout` dates and returns the aggregate retail price for that specific date window and occupancy.
- **Normalization Integrity**:
  The numerical amount is preserved as the authoritative full-stay price without modification, re-calculation, or division by nights. Both `price` and `total_price` store this exact amount.

---

## 5. `pricing_basis` Semantics

- **Declared Semantics**: `"full_stay"`
- **Downstream Recognition**:
  In `services/travel_planner.py:budget_from_live_data()`:
  ```python
  elif basis == "full_stay" or offer_nights > 1 or o.get("total_price") is not None:
      normalized_hotel_stays.append(val)
      primary_pricing_basis = "full_stay"
  ```
  With `pricing_basis="full_stay"`, `nights=nights`, and `total_price=amount`, the offer dictionary matches this branch directly.
- **Fail-Closed Protection**:
  Offers with missing, None, or unrecognized `pricing_basis` (and `nights=0`) continue to enter the `else:` branch and are safely skipped.

---

## 6. Test Coverage

A dedicated test suite was created in `tests/test_w3d08_hotel_pricing_basis.py`:

| Test Name | Verification Target | Status |
| :--- | :--- | :--- |
| `test_liteapi_search_populates_pricing_basis_full_stay` | Verifies `pricing_basis == "full_stay"` is populated. | **PASSED** |
| `test_liteapi_search_computes_multi_night_stay` | Multi-night stay (2026-10-10 to 2026-10-13) yields `nights == 3`. | **PASSED** |
| `test_liteapi_search_computes_single_night_stay` | Single-night stay (2026-10-10 to 2026-10-11) yields `nights == 1`. | **PASSED** |
| `test_liteapi_search_preserves_authoritative_full_stay_amount` | Authoritative provider rate is preserved in `price` and `total_price`. | **PASSED** |
| `test_planner_budget_includes_normalized_liteapi_offer` | `budget_from_live_data()` accepts offer without skipping; returns `hotel_pricing_basis="full_stay"`. | **PASSED** |
| `test_planner_budget_still_fails_closed_on_unrecognized_basis` | Genuinely unrecognized basis values (`mystery_basis`) are skipped fail-closed. | **PASSED** |

Execution:
`.\venv\python.exe -m pytest tests/test_w3d08_hotel_pricing_basis.py -v` $\to$ **6 passed in 0.28s**.

---

## 7. Regression Results

1. **LiteAPI Provider Truthfulness Suite**:
   `.\venv\python.exe -m pytest tests/test_section6_hotel_search.py -k TestLiteAPIProviderTruthfulness -v`
   $\to$ **7 passed, 77 deselected in 6.02s**.
2. **Hotel Prebook Contract & Security Suite**:
   `.\venv\python.exe -m pytest tests/test_wave3a_hotel_prebook_payment.py -v`
   $\to$ **10 passed in 7.48s**.
3. **Hotel Booking Integration & Snapshot Checkout Suite**:
   `.\venv\python.exe -m pytest tests/test_hotel_booking_integration.py -v`
   $\to$ **5 passed in 8.97s**.
4. **Wave 3D-B Reconciliation & Resilience Suite**:
   `.\venv\python.exe -m pytest tests/test_wave3d_b_reconciliation_and_resilience.py -v`
   $\to$ **11 passed in 7.83s**.
5. **Section 4 Pricing Basis Suite**:
   All 6 `hotel_pricing_basis` tests in `tests/test_section4_remediation.py` passed (`per_night`, `full_stay`, `mystery`, `None`, `unknown`). The single failure in that file (`test_api_travel_failure_propagation_never_forces_success_true`) is an unrelated assertion on 503 error text introduced by the frozen Wave 3D-B planner outage handler, which standardizes 503 messages.

---

## 8. Files Changed

### Modified
- `services/hotel/liteapi_provider.py` (12 lines added, 0 lines deleted)

### Added (Untracked)
- `tests/test_w3d08_hotel_pricing_basis.py` (Dedicated W3D-08 test suite)
- `WAVE3D_08_PREIMPLEMENTATION_INVESTIGATION.md` (Investigation report)
- `WAVE3D_08_IMPLEMENTATION_REPORT.md` (This implementation report)

---

## 9. Schema / Migration Impact

- **Schema Changes**: **NONE**
- **Migrations Added/Modified**: **NONE**
- **DDL Executed**: **NONE**
- **DB Enums Modified**: **NONE**
- `pricing_basis`, `nights`, and `total_price` operate entirely as in-memory dictionary attributes between the provider adapter and the planner budget agent.

---

## 10. Working-Tree Safety

- Pre-existing modified files: Preserved untouched.
- Pre-existing untracked files (including `migrations/012_hotel_snapshot_long_provider_offer_id.sql`): Preserved untouched.
- No files staged (`git add`).
- No commits created (`git commit`).
- No changes pushed (`git push`).

---

## 11. Remaining Risks

- None. The change is surgical and strictly additive (`pricing_basis`, `nights`, `total_price`).
- Existing fail-closed protections against unverified or malformed offers remain fully active.

---

## 12. Implementation Verdict

```text
============================================================
WAVE 3D-08 IMPLEMENTATION VERDICT: PASS
============================================================
```

- Normalization completed as specified.
- All focused W3D-08 tests passed.
- Relevant regression suites passed.
- Zero schema or database impact.
- Working tree protected.
- Ready for independent read-only audit.
