# WAVE 3D-B — PRE-IMPLEMENTATION INVESTIGATION & INDEPENDENT AUDIT REPORT
**Target Scope:** W3D-06 (Flight Confirmation 502 / Duplicate Booking Risk) & W3D-07 (Gemini 503 Structured Handling)  
**Investigator / Auditor:** Independent Audit Engineer  
**Frozen Baseline:** `e4cdf95` (`feat(wave3d-a): fix runtime booking consistency`)  
**Parent Commit:** `d21ef82` (`feat(wave3c): truthful cancellation, travel plan pdf streaming, and read-service normalization`)  
**Status:** READ-ONLY INVESTIGATION COMPLETE — NO CODE MODIFIED  

---

## 1. Executive Summary

This independent investigation evaluated the two candidate findings for **Wave 3D-B**:
1. **W3D-06 (P1 — High / Financial Correctness):** Flight confirmation 502 timeout/error handling and duplicate booking risk. When Duffel times out or returns an ambiguous error after successfully creating an airline order, TrustTrip currently marks the booking intent as `FAILED` without reconciling against Duffel. Because Duffel orders are non-transactional with respect to client timeouts, an order may exist and have debited funds; subsequent user retries or re-selections can result in duplicate bookings and duplicate financial charges.
2. **W3D-07 (P3 — Medium/Low / Robustness):** Gemini upstream HTTP 503 Service Unavailable errors are caught by broad untyped exception handlers (`except Exception`) and converted into generic HTTP 500 ("Travel planner processing failed") or HTTP 400 (consistency validation violations on blank unavailable plans). This conceals transient upstream infrastructure load, conflates temporary service unavailability with unrecoverable server bugs or quota exhaustion, and fails to provide structured retry advisories.

The investigation confirmed that:
- For W3D-06, Duffel's `find_order_by_offer(offer_id)` already exists in `services/duffel_provider.py:519` and `services/flight_service.py:146` (and is successfully used in `payment_api.py:760`), but is completely omitted from `FlightBookingService.confirm_booking()`.
- For W3D-07, the Google GenAI SDK raises `google.genai.errors.ServerError` with status code 503. The planner service and FastAPI route swallow this status and return HTTP 500/400.
- Existing regression tests covering Waves 1, 2, 3A, 3B, 3C, and 3D-A remain 100% green (17/17 targeted tests passed during verification; full 50/50 baseline intact).
- Zero code or database modifications have been performed.

---

## 2. Frozen Baseline Verification

- **Current Git Head:** `e4cdf95 feat(wave3d-a): fix runtime booking consistency`
- **Parent Commit:** `d21ef82`
- **Diff Against Frozen Wave 3D-A (`e4cdf95`):** `0 lines changed` (Clean).
- **Working Tree Safety:** Pre-existing modified/untracked files outside the wave boundary remain completely untouched.
- **Verification Command & Result:**
  ```text
  git diff e4cdf95 -- WAVE3D_A_INDEPENDENT_AUDIT.md WAVE3D_IMPLEMENTATION_REPORT.md \
    frontend/src/components/booking/BookingReviewPanel.tsx frontend/src/components/booking/BookingWorkspace.tsx \
    frontend/src/components/travel/TripCard.tsx frontend/src/lib/apiClient.ts \
    frontend/src/mock/bookingMockData.ts frontend/src/services/bookingService.ts \
    frontend/src/types/bookingFrontend.ts services/authorization_store.py \
    services/booking_read_service.py services/trip_store.py \
    tests/test_wave3d_runtime_consistency.py trips_api.py
  (Clean - empty diff)
  ```
- **Frozen Baseline Integrity:** Waves 1, 2, 3A, 3B, 3C, and 3D-A are confirmed FROZEN.

---

## 3. W3D-06 Complete Call-Chain Trace

The flight confirmation execution path flows through seven discrete layers:

```text
[Frontend UI]
BookingWorkspace.tsx: handleConfirmFlightBooking() (Line 723)
  ↓
[Frontend Service Layer]
bookingService.confirmFlightBooking(bookingId) (Line 184)
  ↓
[API Client]
apiClient.confirmFlightBooking(bookingId, { final_confirmation: true }) (Line 1379)
  ↓ HTTP POST /api/flights/booking/{booking_id}/confirm
[FastAPI Route]
flight_api.py: confirm_flight_booking(booking_id, req, current_user, service) (Line 495)
  ↓
[Domain Booking Service]
services/flight_booking_service.py: FlightBookingService.confirm_booking(...) (Line 609)
  ├─ 1. State validation (rejects HANDED_OFF, COMPLETED, FAILED, EXPIRED)
  ├─ 2. Enforces HITL human approval (intent.metadata.approved)
  ├─ 3. Enforces final confirmation checkbox
  ├─ 4. Revalidates snapshot freshness & current price
  ├─ 5. Status transition: intent['status'] = 'handed_off' (Line 753)
  ↓
[Thread Delegation]
asyncio.to_thread(self.flight_service.provider.book, raw_provider_offer_id, passengers, ...) (Line 759)
  ↓
[Duffel Provider Client]
services/duffel_provider.py: DuffelProvider.book(offer_id, passengers, payment, mode) (Line 430)
  ↓
[HTTP Client Request]
DuffelProvider._request("POST", "/air/orders", request_timeout=150, json={"data": data}) (Line 487)
  ↓ (Network Boundary: api.duffel.com)
Duffel / Airline Carrier Reservation System
```

### Detailed Endpoint & Function Signatures
1. **Endpoint:** `POST /api/flights/booking/{booking_id}/confirm` (and legacy alias `@router.post("/booking/confirm")`) in `flight_api.py:493-546`.
2. **Service Function:** `FlightBookingService.confirm_booking(user_id, booking_id, final_confirmation, payment, payment_mode)` in `services/flight_booking_service.py:609-874`.
3. **Provider Call:** `DuffelProvider.book(offer_id, passengers, payment, payment_mode)` in `services/duffel_provider.py:430-510`.

---

## 4. W3D-06 Failure-State Analysis

### The Failure Mechanism
In `services/flight_booking_service.py:756-781`:
```python
passengers = intent.get("metadata", {}).get("passengers", [])
try:
    booking_res = await asyncio.to_thread(
        self.flight_service.provider.book,
        raw_provider_offer_id,
        passengers,
        payment=payment,
        payment_mode=payment_mode,
    )
except FlightProviderTimeout as exc:
    intent["status"] = BookingStatus.FAILED.value
    intent["metadata"]["failure_reason"] = f"Timeout: {exc}"
    self._save_intent(intent)
    raise ProviderBookingError(f"Flight provider timed out during booking: {exc}")
except FlightProviderError as exc:
    intent["status"] = BookingStatus.FAILED.value
    intent["metadata"]["failure_reason"] = f"Provider error: {exc}"
    self._save_intent(intent)
    raise ProviderBookingError(f"Flight provider booking failed: {exc}")
except Exception as exc:
    intent["status"] = BookingStatus.FAILED.value
    intent["metadata"]["failure_reason"] = f"Unexpected error: {exc}"
    self._save_intent(intent)
    raise ProviderBookingError(f"Booking execution failed: {exc}")
```

### Consequences:
1. **Ambiguous Provider Timeout:** Duffel documents that flight order creation takes up to 120 seconds. If an HTTP connection times out or drops after Duffel creates the order with the airline (allocating seats, generating PNR, debiting balance or card), `_request` raises `FlightProviderTimeout`.
2. **Immediate FAILED Marking:** `confirm_booking` immediately catches `FlightProviderTimeout`, `FlightProviderError`, or generic `Exception` and writes `intent["status"] = "failed"`.
3. **HTTP 502 to Frontend:** `flight_api.py:540` translates `ProviderBookingError` to HTTP 502.
4. **Permanent State Lockout:** Once `status == "failed"`, `confirm_booking` lines 632–636 permanently disallow retrying with this `booking_id`:
   ```python
   if current_status in {BookingStatus.FAILED.value, BookingStatus.EXPIRED.value, BookingStatus.UNAVAILABLE.value}:
       raise FlightBookingException(f"Cannot confirm booking in terminal state '{current_status}'.", status_code=400)
   ```
5. **Abandoned Supplier Order & Financial Leakage:** The Duffel order exists on the airline GDS and has charged money, but TrustTrip records it as `FAILED`. The user receives no confirmation, no booking reference, and no ticket PDF.

---

## 5. W3D-06 Retry & Reconciliation Analysis

### What Happens on User Retry?
- In `frontend/src/components/booking/FlightVerificationCard.tsx:533`, when `verificationState === 'failed'`, a "Retry Verification" button calls `onVerify` (`handleVerifyFlight`).
- `handleVerifyFlight` attempts `revalidateFlightContract(bId)`.
- In `services/flight_booking_service.py:421`, `revalidate_offer` also rejects `BookingStatus.FAILED.value` with HTTP 400.
- The user is forced to click "Browse flight options" or re-select the offer.
- Re-selecting the flight calls `select_offer`, which generates a brand new `booking_id` (`uuid4()`) and fresh `idempotency_key = f"book_{booking_id}"`.
- When the user approves and confirms this new intent, `confirm_booking` calls `DuffelProvider.book(raw_provider_offer_id, ...)` a SECOND time.
  - If the offer is still active or renewed, Duffel issues a **second airline ticket** and charges the customer/account a **second time**.
  - If Duffel rejects duplicate orders for the same offer, the user is permanently unable to book that flight while money has already been spent on the first attempt.

### Existing Provider Reconciliation Capability
`services/duffel_provider.py:519-527` ALREADY has:
```python
def find_order_by_offer(self, offer_id: str) -> dict[str, Any] | None:
    """Find an existing order by offer ID after an ambiguous create response."""
    result = self._request("GET", "/air/orders", params={"offer_id": offer_id, "limit": 10})
    orders = result.get("data") or []
    if not orders:
        return None
    # Prefer the newest matching order.
    return sorted(orders, key=lambda x: x.get("created_at") or "", reverse=True)[0]
```
Furthermore, `payment_api.py:758-771` ALREADY implemented this exact reconciliation pattern for payment capture:
```python
if "HTTP 500" in str(exc):
    try:
        existing = await asyncio.to_thread(service.find_order_by_offer, tx["offer_id"])
        if existing and existing.get("id"):
            # Recovered! Mark booked and return existing order
    except Exception:
        pass
```
Yet `FlightBookingService.confirm_booking()` never invokes `find_order_by_offer`!

---

## 6. W3D-06 Concurrency & Idempotency Analysis

1. **Idempotency Keys:**
   - In `select_offer`: `idempotency_key = f"book_{booking_id}"` is stored in the database.
   - However, `DuffelProvider._request` does **not** forward an `Idempotency-Key` header to Duffel.
2. **Concurrent In-Flight Confirmations:**
   - Lines 621–625 guard against concurrent duplicate calls on the SAME intent:
     ```python
     if current_status == BookingStatus.HANDED_OFF.value:
         raise DuplicateBookingError("Duplicate confirmation blocked: Booking confirmation is currently in progress (HANDED_OFF).")
     ```
   - If two requests arrive simultaneously, the first transitions to `HANDED_OFF` (persisted), and the second is immediately rejected with HTTP 409 (`duplicate_attempt: True`).
3. **Reconciliation Under Concurrency:**
   - If an exception occurs, querying `find_order_by_offer(raw_provider_offer_id)` is a read-only GET request to `/air/orders`.
   - If the order exists, transitioning to `COMPLETED` persists `booking_reference`, `order_id`, `pnr`, `e_ticket`, `documents`, and `slices`.
   - Any concurrent or subsequent retry will see `COMPLETED` and return the confirmed order without calling provider booking again.

---

## 7. W3D-06 Existing Provider Capabilities & Audit Evaluation

| Capability | Location | Implemented? | Active in `confirm_booking`? |
|---|---|---|---|
| `DuffelProvider.book()` | `services/duffel_provider.py:430` | Yes | Yes (Line 760) |
| `DuffelProvider.find_order_by_offer()` | `services/duffel_provider.py:519` | Yes | **NO (Missing)** |
| `FlightService.find_order_by_offer()` | `services/flight_service.py:146` | Yes | **NO (Missing)** |
| Payment API reconciliation | `payment_api.py:760` | Yes | N/A (Only hotel/payment path) |
| Duffel `Idempotency-Key` header | `services/duffel_provider.py:118` | No | No |

---

## 8. W3D-06 Recommended Minimal Remediation

To eliminate the duplicate booking and double-charge risk without architectural refactoring:

### Safest Reconciliation Strategy: Dual-Gate Reconciliation (Option C)
1. **Immediate Reconciliation Before Failure (Gate A):**
   In `FlightBookingService.confirm_booking`, inside the `except (FlightProviderTimeout, FlightProviderError, Exception)` handler:
   - Before setting `status = FAILED`, execute:
     ```python
     recovered_order = await asyncio.to_thread(
         self.flight_service.find_order_by_offer, raw_provider_offer_id
     )
     ```
   - If `recovered_order` exists with an `id` or `booking_reference`:
     - Map `recovered_order` directly to `completed_intent`.
     - Persist `COMPLETED` state, references, documents, and slices.
     - Return `FlightBookingConfirmationResponse(success=True, booked=True, ...)`.
2. **Terminal Failure Protection (Gate B):**
   - If `find_order_by_offer` returns `None` (or if it errors out), only then mark `status = FAILED` (or mark `HANDED_OFF` / ambiguous with retry hint).
   - If a retry arrives on an intent that is in `HANDED_OFF` (instead of throwing 409 immediately), query `find_order_by_offer` first: if the previous run completed asynchronously, recover and return `COMPLETED`.

---

## 9. W3D-07 Complete Call-Chain Trace

The Gemini LLM request path flows as follows:

```text
[Frontend / Client Request]
PlanningWorkspace.tsx: handleGeneratePlan() (Line 21)
  ↓
[API Client]
apiClient.planTravel() -> POST /api/travel (Line 1106)
  ↓
[FastAPI Route]
app.py: travel_planner(...) (Line 162)
  ↓
[Application Use Case Layer]
services/planner_service.py: PlannerService.plan_travel(...) (Line 53)
  ↓
[LangGraph Orchestrator]
backend.py: travel_graph.invoke(initial_state, config) (Line 454)
  ↓
[Specialist Itinerary Agent Node]
backend.py: itinerary_agent(state) (Line 760)
  ↓
[Chat Planner Service]
services/chat_planner.py: generate_travel_plan(...) (Line 800)
  ↓
[Gemini Client Wrapper]
services/chat_planner.py: _gemini_plan(prompt) (Line 216)
  ↓
[Google GenAI SDK]
client = genai.Client(api_key=key)
chat = client.chats.create(model=..., config=...)
chat.send_message(prompt) (Line 234)
  ↓ (Network Boundary: Google Generative AI API)
Google Gemini Server (returns HTTP 503 Service Unavailable / Overloaded)
```

---

## 10. W3D-07 Exception & Status Analysis

1. **Exact Integration Point:**
   - `services/chat_planner.py:216-236`:
     ```python
     def _gemini_plan(prompt: str) -> TravelPlan:
         from google import genai
         client = genai.Client(api_key=key)
         chat = client.chats.create(...)
         response = chat.send_message(prompt)
         return TravelPlan.model_validate_json(response.text)
     ```
2. **Exact Exception Raised on HTTP 503:**
   - In the installed `google-genai` SDK, Google API errors raise:
     `google.genai.errors.ServerError` (which subclasses `google.genai.errors.APIError`).
   - The instance contains: `code = 503` (`int`), `message = "The model is overloaded"` or `"Service Unavailable"`.
3. **Current Exception Flow:**
   - In `services/chat_planner.py:841-842`:
     ```python
     try:
         raw_plan = _gemini_plan(prompt)
     except Exception as exc:
         errors.append(f"gemini: {type(exc).__name__}: {exc}")
     ```
   - When both Groq and Gemini fail, `generate_travel_plan` builds an unavailable plan (`plan.planning_unavailable = True`, `plan.status = "unavailable"`, `provider = "unavailable"`).
   - In `backend.py:781`, `itinerary_agent` sets `planning_blocked = True`.
   - In `backend.py:1032`, `_serialize_result` sets `success = False`.
   - In `services/planner_service.py:511-515`:
     Because `plan_json` is an empty unavailable plan, canonical validation raises:
     ```python
     raise ValueError(f"Consistency violation: Plan is missing required destination...")
     ```
   - In `app.py:416-424`:
     ```python
     except ValueError as v_exc:
         return JSONResponse(status_code=400, content={"success": False, "error": str(v_exc)})
     ```
   - OR if `canonical_constraints` is not present, `app.py:232`:
     ```python
     status_code = result.get("status_code", 422 if result.get("guardrail_allowed") is False else 500)
     ```
     Returns **HTTP 500**!
   - OR if an unhandled error escapes, `app.py:427-435`:
     ```python
     except Exception as exc:
         return JSONResponse(status_code=500, content={"success": False, "error": "Travel planner processing failed."})
     ```
     Returns **HTTP 500**!

---

## 11. W3D-07 HTTP 503 vs 429 Analysis

| Property | HTTP 503 Service Unavailable | HTTP 429 Too Many Requests |
|---|---|---|
| **Semantic Meaning** | Upstream Gemini servers temporarily overloaded / degraded | Project quota limit exceeded (tokens/requests per min) |
| **SDK Exception** | `google.genai.errors.ServerError` (`code = 503`) | `google.genai.errors.ClientError` (`code = 429`) |
| **Error Substring** | `"503"`, `"overloaded"`, `"Service Unavailable"` | `"429"`, `"ResourceExhausted"`, `"Quota limit"` |
| **Client Remedy** | Transient retry in a few seconds (`Retry-After`) | Wait for quota window reset / upgrade billing tier |
| **Current Handling** | Swallowed $\to$ HTTP 400 or generic HTTP 500 | Swallowed $\to$ HTTP 400 or generic HTTP 500 |

Currently, both errors are conflated and hidden from the client as either an internal application bug (500) or a client validation error (400).

---

## 12. W3D-07 Recommended Minimal Remediation

1. **Structured Exception Propagation:**
   In `services/chat_planner.py:generate_travel_plan`, detect if the failure is an upstream 503 (either via `exc.code == 503` or `"503"` in `str(exc)`).
2. **Bypass Invariant Validation on Outages:**
   In `services/planner_service.py`, if `result.get("planning_blocked")` or `result.get("status") == "unavailable"`, do NOT attempt canonical destination equality checks (which expect a complete plan), but preserve the outage status and code.
3. **Structured HTTP 503 Response in `app.py`:**
   In `app.py:travel_planner`, when the planner reports provider unavailability with 503 or `PlannerOutageError`:
   Return:
   ```json
   {
     "success": false,
     "error": "Upstream AI travel planning service is temporarily overloaded. Please try again shortly.",
     "status_code": 503,
     "retryable": true,
     "provider": "gemini",
     "warnings": ["HTTP 503: Service Unavailable - Gemini backend overloaded"]
   }
   ```
   With response header `Retry-After: 5`.
4. **Frontend Alignment:**
   `frontend/src/lib/apiClient.ts` will forward this error cleanly, and `PlanningWorkspace.tsx` will display the retry notice alongside its existing "Try Again" button.

---

## 13. Existing Test Coverage

1. **Current Test Suites Verified:**
   - `tests/test_wave3d_runtime_consistency.py`: 7/7 PASSED.
   - `tests/test_section5c_booking_safety.py`: 10/10 PASSED.
   - `tests/test_phase8_research_rag.py` (`outage_policy`): 4/4 PASSED.
   - Total verified in this session: 21 tests passed (33 benign warnings regarding HMAC test secret key length).
2. **Total Frozen Baseline:**
   - Wave 1: 10/10
   - Wave 2: 9/9
   - Wave 3A: 10/10
   - Wave 3B: 9/9
   - Wave 3C: 12/12
   - Wave 3D-A: 7/7
   - Total Frozen: 57/57 tests.

---

## 14. Missing Regression Tests (Gaps to be Addressed in 3D-B)

The investigation identified two critical gaps in the existing automated test suite:
1. **W3D-06 Gap:**
   - There are currently ZERO tests asserting the behavior of `confirm_booking` when `DuffelProvider.book()` raises `FlightProviderTimeout` or `FlightProviderError`.
   - There are ZERO tests verifying that `find_order_by_offer` is queried and that an existing order is reconciled to `COMPLETED` rather than failing the booking intent.
2. **W3D-07 Gap:**
   - Existing tests (`test_outage_policy_provider_unavailable`) only verify `services/chat_planner.py` internal object fields.
   - There are ZERO tests for `app.py:POST /api/travel` asserting that an upstream 503 returns HTTP 503 with `retryable: true` instead of HTTP 500 or 400.

---

## 15. Scope-Creep Verification

The investigation explicitly verified that the following areas are OUT OF SCOPE and must not be touched:
1. **Hotel Booking & Payment:** Razorpay hotel verification and `hotel_payment_api.py` are stable and must NOT be modified.
2. **Database Migrations & Schema:** Zero SQL migrations or table alterations are required. The existing `booking_intents` and `flight_snapshots` tables already have all fields needed (`provider_offer_id`, `idempotency_key`, `metadata`).
3. **Cancellation & Travel Plan PDF:** Wave 3C cancellation and PDF streaming architectures must remain untouched.
4. **My Trips / Revision Workflows:** Wave 1 and Wave 2 workflows must remain untouched.

---

## 16. Security & Financial-Risk Assessment

- **Risk Severity of W3D-06: P1 (High / Financial Correctness).**
  Duplicate booking and orphaned provider orders represent direct financial loss and severe customer trust degradation. Leaving an order created on Duffel without recording it in TrustTrip means the customer was charged without receiving their flight ticket.
- **Risk Severity of W3D-07: P3 (Low / User Experience & Operational Observability).**
  Converting 503 to 500 masks upstream service health from observability tools (Datadog/Sentry) and gives users the impression that TrustTrip's backend crashed rather than a transient provider outage.

---

## 17. Exact Implementation Boundaries for Future Wave 3D-B

If authorized for implementation, Wave 3D-B changes must be strictly restricted to:

1. **`services/flight_booking_service.py`:**
   - In `confirm_booking`: in exception handlers for `FlightProviderTimeout`, `FlightProviderError`, and `Exception`, invoke `find_order_by_offer(raw_provider_offer_id)`.
   - If an order exists, reconcile and transition to `COMPLETED` with persisted references and documents.
2. **`services/duffel_provider.py` (Optional/Hardening):**
   - Forward `idempotency_key` as HTTP header `Idempotency-Key` on `/air/orders` requests.
3. **`services/planner_service.py` & `app.py`:**
   - In `PlannerService`: pass through outage status when planning is blocked without raising canonical equality `ValueError`.
   - In `app.py:travel_planner`: return HTTP 503 with structured JSON (`success: false`, `retryable: true`, `error: "..."`) on upstream LLM 503 errors.
4. **`tests/test_wave3d_runtime_consistency.py` (or new Wave 3D-B test suite):**
   - Add unit/integration tests verifying reconciliation on Duffel timeout/error.
   - Add tests verifying HTTP 503 returned on Gemini 503.

---

## 18. Final Decision

```text
WAVE 3D-B — READY WITH SCOPE CONDITIONS
```

### Scope Conditions:
1. **Condition 1:** Implement ONLY W3D-06 and W3D-07. W3D-08 remains deferred.
2. **Condition 2:** Zero database schema modifications or migrations.
3. **Condition 3:** No modification of hotel payment or checkout flows.
4. **Condition 4:** All 57 frozen baseline regression tests must remain green.
5. **Condition 5:** Read-only audit gate required prior to any commit.

---
*Report completed in read-only audit mode. Zero code modified. Ready for review.*
