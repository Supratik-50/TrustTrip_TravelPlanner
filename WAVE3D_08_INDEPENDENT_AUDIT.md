# WAVE 3D-08 — Independent Audit

## 1. Audit Scope

- **Audit Target**: Wave 3D-08 — LiteAPI Hotel `pricing_basis` Truthfulness.
- **Auditor Role**: Independent Read-Only Auditor.
- **Production File Audited**:
  - `services/hotel/liteapi_provider.py`
- **Test File Audited**:
  - `tests/test_w3d08_hotel_pricing_basis.py`
- **Reports Audited**:
  - `WAVE3D_08_PREIMPLEMENTATION_INVESTIGATION.md`
  - `WAVE3D_08_IMPLEMENTATION_REPORT.md`
- **Audit Rules**:
  - Strictly read-only; no code modifications.
  - No database schema changes, DDL, or migrations.
  - No commits or pushes.
  - All pre-existing working tree content treated as protected.

---

## 2. Baseline Verification

- **Current HEAD**: `03e64e6` (`feat(wave3d-b): reconcile flight bookings and handle planner outages`)
- **Parent Commit**: `e4cdf95` (`feat(wave3d-a): fix runtime booking consistency`)
- **Frozen Baseline Integrity**:
  - Wave 3D-B (`03e64e6`) remains unmodified and frozen.
  - Wave 3D-A (`e4cdf95`) remains unmodified and frozen.
  - Wave 3C (`d21ef82`) remains unmodified and frozen.
  - Wave 3B (`de75bbc`) remains unmodified and frozen.
- **Implementation State**:
  - The W3D-08 implementation was executed as uncommitted working-tree modifications on top of `03e64e6`.

---

## 3. Working-Tree Boundary

[OBSERVED FACT]
Running `git status`, `git diff --name-only`, and `git diff --stat` confirms:
1. **W3D-08 Production Code Modification**:
   - `services/hotel/liteapi_provider.py` (+12 lines, -0 lines).
2. **W3D-08 Untracked Test & Documentation Deliverables**:
   - `tests/test_w3d08_hotel_pricing_basis.py` (New test suite, 6 tests).
   - `WAVE3D_08_PREIMPLEMENTATION_INVESTIGATION.md` (Investigation report).
   - `WAVE3D_08_IMPLEMENTATION_REPORT.md` (Implementation report).
   - `WAVE3D_08_INDEPENDENT_AUDIT.md` (This audit report).
3. **Protected Pre-Existing Working-Tree Content**:
   - `migrations/012_hotel_snapshot_long_provider_offer_id.sql` was **NOT modified, staged, or executed**.
   - Pre-existing modified frontend and backend files from earlier waves remain in their exact pre-existing states.
   - Nothing was staged (`git add`), committed, or pushed.

---

## 4. Root Cause Verification

[OBSERVED FACT]
The auditor traced `services/travel_planner.py:budget_from_live_data()` lines 1106–1137:
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
Before W3D-08, `LiteAPIProvider.search()` returned a dictionary that had neither `pricing_basis`, `nights`, nor `total_price`.
Consequently:
- `hasattr(o, "nights")` evaluated to `False` (dictionary instance).
- `basis` was `None`.
- `offer_nights` was `0`.
- `o.get("total_price")` was `None`.
- The offer entered the `else:` block and was skipped fail-closed with the warning.
- `normalized_hotel_stays` was empty, causing `budget_from_live_data()` to fall back to regex scraping or ₹1,800/night planning estimates.
The root cause stated in the investigation and implementation reports is **verified as factual and accurate**.

---

## 5. LiteAPI Pricing Semantics

[OBSERVED FACT]
The auditor inspected repository evidence regarding LiteAPI's pricing semantics across multiple layers:
1. **Provider Query**: In `services/hotel/liteapi_provider.py:47-48`, the request payload to `POST https://api.liteapi.travel/v3.0/hotels/rates` requires explicit `checkin` and `checkout` date strings.
2. **Provider Adapter**: In `services/travel_intelligence/liteapi_adapter.py:121-147`, `LiteAPIHotelAdapter` parses `retailRate.total[0].amount` directly into `PriceBreakdown.total_amount` for the stay, and computes `nightly_dec = round(float(price_dec) / nights, 2)`.
3. **Snapshot Ingestion**: In `hotel_api.py:380, 644-653, 697-703`, `price_val = float(raw_price)` is stored as `total_amount`, and `nightly_price` is computed as `round(price_val / nights, 2)`.
4. **Client Presentation**: In `frontend/src/components/travel/HotelCard.tsx:48-58`, `effectiveTotal` is displayed as `total`, and `effectiveNightly` is computed by dividing `hotel.total_amount / stayNights`.
5. **Prebook & Payment**: In `hotel_payment_api.py:148`, the user is billed `amount_inr` derived directly from `offer.get("price")` for the entire stay.

**Auditor Conclusion**:
Repository evidence establishes conclusively that `retailRate.total[0].amount` is the **aggregate full-stay retail cost** for the requested date window and occupancy, not a per-night rate.

---

## 6. `pricing_basis` Verification

[OBSERVED FACT]
In `services/hotel/liteapi_provider.py:127-133`:
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
- `pricing_basis == "full_stay"` accurately matches the supplier's rate semantics.
- `price` and `total_price` both retain the unmodified provider amount `amount = total.get("amount")`.
- The amount is **NOT divided by nights**.
- The amount is **NOT multiplied by nights**.
- No synthetic exchange rates or fictitious surcharges are introduced.

---

## 7. `nights` Calculation Safety

[OBSERVED FACT]
The auditor critically inspected the newly introduced date parsing and nights computation in `services/hotel/liteapi_provider.py:60-66`:
```python
# Compute stay nights from checkin and checkout dates
try:
    d_in = date.fromisoformat(str(request.get("checkin") or "").split("T")[0])
    d_out = date.fromisoformat(str(request.get("checkout") or "").split("T")[0])
    nights = max(1, (d_out - d_in).days)
except Exception:
    nights = 1
```

### Safety Analysis by Input Case
| Case | Input Example | Parsing Result | Arithmetic Outcome | Truthfulness / Safety Assessment |
| :--- | :--- | :--- | :--- | :--- |
| **Standard Multi-Night** | `checkin="2026-10-10"`, `checkout="2026-10-13"` | `(2026-10-13 - 2026-10-10).days = 3` | `nights = 3` | **TRUTHFUL & SAFE** |
| **Standard Single-Night** | `checkin="2026-10-10"`, `checkout="2026-10-11"` | `(2026-10-11 - 2026-10-10).days = 1` | `nights = 1` | **TRUTHFUL & SAFE** |
| **ISO Datetime with Time / Z** | `checkin="2026-10-10T14:00:00Z"` | `.split("T")[0]` extracts `"2026-10-10"` | Parses successfully | **TRUTHFUL & SAFE** |
| **Equal Dates (`d_out == d_in`)** | `checkin="2026-10-10"`, `checkout="2026-10-10"` | `(d_out - d_in).days = 0` | `max(1, 0) = 1` | **SAFE** (Prevents division-by-zero downstream; upstream validation in `hotel_api.py` rejects `d_in >= d_out` with HTTP 400). |
| **Inverted Dates (`d_out < d_in`)** | `checkin="2026-10-15"`, `checkout="2026-10-10"` | `(d_out - d_in).days = -5` | `max(1, -5) = 1` | **SAFE** (Replaced with 1; upstream validation rejects `d_in >= d_out` before reaching provider). |
| **Missing / Malformed Dates** | `checkin=None` or `checkin="invalid"` | `ValueError` in `date.fromisoformat` | Falls back to `nights = 1` | **SAFE** (See below). |

### Detailed Assessment of `except Exception: nights = 1`
1. Can missing or malformed dates reach `LiteAPIProvider.search()` in production?
   - In `backend.py:444-450`, `checkout` is deterministically computed from `departure` and `duration_days`.
   - In `hotel_api.py:286-298`, strict validation checks `d_in >= d_out` and raises HTTP 400 before provider invocation.
   - In `LiteAPIProvider.search():47-48`, the request dictionary keys are directly accessed as `request["checkin"]` and `request["checkout"]` when assembling the HTTP request body. If missing, a `KeyError` is raised before reaching the offers loop.
2. Even if an unparseable date reached this block (e.g. in a stubbed test):
   - Downstream in `budget_from_live_data()`, `pricing_basis == "full_stay"` ensures that `val` is used directly as the stay estimate. It is **never multiplied by `nights`**.
   - Downstream in checkout, payment relies on the immutable snapshot and LiteAPI prebook hold, never on `nights`.
3. **Auditor Finding**: The fallback `except Exception: nights = 1` prevents unhandled provider parsing exceptions without introducing any financial calculation error.

---

## 8. Planner Integration

[OBSERVED FACT]
With `pricing_basis="full_stay"`, `nights=nights`, and `total_price=amount`:
1. `services/travel_planner.py:budget_from_live_data()` matches:
   ```python
   elif basis == "full_stay" or offer_nights > 1 or o.get("total_price") is not None:
       normalized_hotel_stays.append(val)
       primary_pricing_basis = "full_stay"
   ```
2. The offer is appended without warning.
3. The hotel budget estimate equals `min(normalized_hotel_stays)`.
4. `summary["hotel_pricing_basis"]` reports `"full_stay"`.
5. `summary["hotel_price_source"]` reports `"live dual-provider hotel offer (full_stay)"`.
6. Live planning results in chat correctly reflect actual supplier rates instead of fallback estimates.

---

## 9. Fail-Closed Behavior

[OBSERVED FACT]
The auditor verified that W3D-08 did **not** modify or loosen `services/travel_planner.py`.
The fail-closed guard in `travel_planner.py:1126-1136` remains active:
- When an offer dictionary has `pricing_basis=None` and `nights=0`: Skipped.
- When an offer dictionary has `pricing_basis="mystery_basis"` and `nights=0`: Skipped.
- When an offer dictionary has `pricing_basis="unknown"` and `nights=0`: Skipped.
This is verified by existing test `test_hotel_unrecognized_pricing_basis_skipped` in `tests/test_section4_remediation.py` and newly added test `test_planner_budget_still_fails_closed_on_unrecognized_basis` in `tests/test_w3d08_hotel_pricing_basis.py`.

---

## 10. Hotel Checkout / Razorpay Isolation

[OBSERVED FACT]
The auditor verified that W3D-08 changes are strictly isolated to the search normalization method of `LiteAPIProvider`.
No modifications were made to:
- `hotel_api.py` (prebook and selection contracts untouched)
- `services/hotel_booking_service.py` (snapshot verification and revalidation gates untouched)
- `services/hotel_transaction_store.py` (lineage fields and idempotency index untouched)
- `hotel_payment_api.py` (Razorpay signature verification and amount match check untouched)
- `payment_api.py` (flight payment flow untouched)
Financial transactions remain completely isolated from planner budget normalization.

---

## 11. Database / Schema Verification

[OBSERVED FACT]
- Zero database tables modified.
- Zero columns added, removed, or altered.
- Zero database migrations added or modified.
- Zero database enums modified.
- Zero DDL executed.
- `migrations/012_hotel_snapshot_long_provider_offer_id.sql` remains completely untouched.

---

## 12. Test Verification

The auditor independently ran the dedicated test suite:
```bash
.\venv\python.exe -m pytest tests/test_w3d08_hotel_pricing_basis.py -v
```
**Results: 6 passed in 0.28s**:
1. `test_liteapi_search_populates_pricing_basis_full_stay` (PASSED)
2. `test_liteapi_search_computes_multi_night_stay` (PASSED)
3. `test_liteapi_search_computes_single_night_stay` (PASSED)
4. `test_liteapi_search_preserves_authoritative_full_stay_amount` (PASSED)
5. `test_planner_budget_includes_normalized_liteapi_offer` (PASSED)
6. `test_planner_budget_still_fails_closed_on_unrecognized_basis` (PASSED)

---

## 13. Regression Failure Analysis

### Investigation of `test_api_travel_failure_propagation_never_forces_success_true`
In `tests/test_section4_remediation.py:325`, the test failed with:
```text
AssertionError: assert 'Flight search provider unavailable' in 'Upstream AI travel planning service is temporarily overloaded. Please try again shortly.'
```

### Root Cause Analysis of the Failure
1. **Pre-Existing Baseline Origin**:
   In frozen commit `03e64e6` (Wave 3D-B: `feat(wave3d-b): reconcile flight bookings and handle planner outages`), `app.py:236-245` was introduced to standardize 503 outage responses:
   ```python
   if outage_code == 503 or (result.get("planning_blocked") and result.get("outage_status_code") == 503):
       out_dict["success"] = False
       out_dict["error"] = "Upstream AI travel planning service is temporarily overloaded. Please try again shortly."
       out_dict["retryable"] = True
       out_dict["status_code"] = 503
       return JSONResponse(status_code=503, content=jsonable_encoder(out_dict), headers={"Retry-After": "5"})
   ```
2. **Old Test Assertion**:
   The older test in `tests/test_section4_remediation.py` mocked a failure with `status_code: 503` and `error: "Flight search provider unavailable."`, and asserted that the custom mock error string was returned in `data["error"]`. Because `app.py` now overrides 503 errors with the standardized outage message, the string assertion failed.
3. **Independence from W3D-08**:
   - W3D-08 did NOT touch `app.py`.
   - W3D-08 did NOT touch `tests/test_section4_remediation.py`.
   - The failure was already committed into HEAD `03e64e6` as part of the Wave 3D-B freeze.
   - All 6 hotel pricing basis tests in `tests/test_section4_remediation.py` passed cleanly.
4. **Conclusion**:
   The failure is a pre-existing baseline test expectation mismatch resulting from Wave 3D-B, completely unrelated to W3D-08, and does not block W3D-08.

---

## 14. Findings

### Finding F-W3D08-01 (INFO / P3): Defensive date parsing fallback
- **File**: `services/hotel/liteapi_provider.py:65`
- **Description**: `except Exception: nights = 1` ensures that unparseable date strings do not crash the provider search. In the event of malformed dates reaching this block, `nights` defaults to 1.
- **Safety Assessment**: Budget calculations in `travel_planner.py` evaluate `pricing_basis == "full_stay"` and do not multiply by `nights`. Upstream validation in `backend.py` and `hotel_api.py` prevents invalid dates from reaching the provider in production.
- **Severity**: P3 (Informational / Non-blocking). No action required for freeze.

---

## 15. Severity Classification

| Finding ID | Title | Severity | Impact on Freeze |
| :--- | :--- | :--- | :--- |
| **F-W3D08-01** | Defensive date parsing fallback `nights=1` | **P3** | **Non-blocking** |

---

## 16. Final Verdict

```text
============================================================
WAVE 3D-08 INDEPENDENT AUDIT VERDICT: PASS WITH NON-BLOCKING FINDINGS
============================================================
```

### Recommendation
**APPROVED FOR CONTROLLED COMMIT & FREEZE.**

- LiteAPI provider normalization accurately emits `pricing_basis="full_stay"`, `nights`, and `total_price`.
- Downstream planner budget calculations correctly incorporate valid LiteAPI hotel offers.
- Fail-closed behavior is strictly preserved for unverified pricing bases.
- Hotel checkout, snapshot persistence, and Razorpay payment flows remain isolated and intact.
- Zero database or schema changes.
- Pre-existing working tree content is fully preserved.
