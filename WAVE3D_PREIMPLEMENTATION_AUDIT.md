# TRUSTTRIP WAVE 3D — PRE-IMPLEMENTATION AUDIT

## 1. Executive Summary

This independent pre-implementation audit investigates nine post-Wave 3C runtime defects and architectural observations identified during manual runtime testing of the TrustTrip travel planning and booking platform.

Wave 3C is frozen and committed at `d21ef82 feat(wave3c): truthful cancellation, travel plan pdf streaming, and read-service normalization`. The audit was conducted in strict read-only mode: no source code was altered, no database migrations or mutations were executed, no tests were modified, and the Git tree was preserved.

### Key Investigation Takeaways
1. **FIND-W3D-01 (Ticket 401 Unauthorized):** Caused by direct `<a href>` browser navigation in `TripCard.tsx` bypassing the authenticated `apiClient` Bearer token header required by the FastAPI `get_current_user` dependency on `GET /api/trips/{transaction_id}/ticket.pdf`.
2. **FIND-W3D-02 (Flight Price ₹0 in Booking Review):** Caused by asymmetric metadata persistence in `services/trip_store.py:select_offer_atomic`. While hotels persist their full offer payload in `trip.metadata["selected_hotel"]`, flight selection only persists `selected_flight_snapshot_id`. On page rehydration, `selectedFlightOffer` lacks amount fields, defaulting `flightAmount` to `0` and rendering `₹0` in `BookingReviewPanel.tsx`.
3. **FIND-W3D-03 (Reapproval 409 Conflict):** The backend 409 response from `POST /api/trips/{trip_id}/approve` is **correct and intentional** server idempotency protection against duplicate revision approval in `EvidenceStore.approve_revision_transaction`. The defect is purely frontend state amnesia: `BookingWorkspace.tsx` does not derive or rehydrate approval state from the server trip record, leaving the "Approve Flight Booking" button active even after successful approval and booking.
4. **FIND-W3D-04 (Hotel Booking Authorization Required):** In `trips_api.py:get_trip`, the returned trip entity omits `booking_authorization` (which is only fetched in the `/revisions` endpoint). Consequently, `BookingWorkspace` sets `activeAuthorization = undefined` on refresh. Furthermore, manual test evidence revealed the user had approved the plan (Section 8) but had not triggered the distinct "Authorize for Booking" step (Section 9), meaning no row existed in `booking_authorizations`.
5. **FIND-W3D-05 (Duplicate Flight Entries in My Trips):** `BookingReadService.list_recent_flights_for_user` selects all `booking_intents` records for the user across all statuses without filtering out `FAILED` or abandoned intents. In the manual test run, intent 1 failed on a 502, and intent 2 succeeded (`27LXIN`). The frontend rendered both: the completed one as `Flight: 27LXIN Ticket` and the failed intent (having null provider reference) falling back to `Flight: Booked Ticket`.
6. **FIND-W3D-06 (Flight Confirmation 502 & Duplicate Booking Risk):** When Duffel times out or returns an unexpected error, `confirm_booking` marks the intent `FAILED` and raises `ProviderBookingError` (502). Because Duffel may have already created the order, a naive client retry produces a duplicate real booking. Duffel's existing `find_order_by_offer` reconciliation hook is not currently invoked prior to failing the intent.
7. **FIND-W3D-07 (Gemini 503 Service Unavailable):** Upstream Gemini 503 is caught by generic `except Exception` in `app.py:travel_planner_chat` and wrapped as an untyped 500 error.
8. **FIND-W3D-08 (Hotel `pricing_basis=None`):** `LiteApiHotelProvider` returns offer models omitting `pricing_basis` and `nights`. `travel_planner.py` safely fails closed by skipping these offers to prevent incorrect per-night vs full-stay arithmetic.
9. **FIND-W3D-09 (Flight Razorpay Architecture):** TrustTrip flight confirmation was deliberately designed around Duffel's native settlement mechanism (`hold`, `balance`, or provider payment). Flights do not use Razorpay; this is an architectural property, not a defect.

---

## 2. Frozen Baseline

The current baseline is frozen and unmodified:
- **Frozen Commit:** `d21ef82 feat(wave3c): truthful cancellation, travel plan pdf streaming, and read-service normalization`
- **Parent Commit:** `de75bbc fix: finalize wave 3b booking persistence`
- **Verified Frozen Test Suite:**
  - Wave 1 tests: 10/10 passed
  - Wave 2 tests: 9/9 passed
  - Wave 3A tests: 10/10 passed
  - Wave 3B tests: 9/9 passed
  - Wave 3C tests: 12/12 passed
  - Total Frozen Baseline: 38/38 passed
  - Full Backend Suite: 843/843 passed
  - Frontend Suites: 12/12 passed (TypeScript clean, ESLint clean)

All findings identified below are runtime, integration, or presentation findings discovered post-Wave 3C freeze.

---

## 3. FIND-W3D-01 — Ticket 401 Unauthorized

### Exact Endpoint
`GET /api/trips/{transaction_id}/ticket.pdf` (routed in [trips_api.py](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/routes/trips_api.py#L702-L735))

### Frontend Caller
[frontend/src/components/travel/TripCard.tsx](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/TripCard.tsx#L204-L214)

### Authentication Mechanism
Backend endpoint signature:
```python
@router.get("/trips/{transaction_id}/ticket.pdf")
async def download_ticket(
    transaction_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
```
`get_current_user` extracts the JWT from the HTTP `Authorization: Bearer <token>` header. If the header is absent or invalid, it immediately raises `HTTPException(status_code=401, detail="Could not validate credentials")`.

### Root Cause
In [TripCard.tsx:204-213](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/TripCard.tsx#L204-L213), the ticket link is rendered as an unauthenticated direct browser anchor:
```tsx
<a
  href={`/api/trips/${f.id}/ticket.pdf`}
  target="_blank"
  rel="noopener noreferrer"
  className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium"
>
  Ticket
  <ExternalLink className="w-3 h-3" />
</a>
```
When a user clicks a standard `<a href="...">` link with `target="_blank"`, the browser opens a new tab and sends a native GET request. Native browser navigations **do not attach custom headers** (`Authorization: Bearer <jwt>`) stored in client-side `localStorage`. Consequently, the request arrives at the backend with no `Authorization` header, triggering an immediate `401 Unauthorized`.

Note: In contrast, the Travel Plan PDF download in [TripCard.tsx:182-192](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/TripCard.tsx#L182-L192) correctly uses an authenticated client-side handler:
```tsx
const handleDownloadPdf = async () => {
  setIsDownloadingPdf(true);
  try {
    await bookingService.downloadTravelPlanPdf(trip.id);
  } ...
};
```

### Severity
**P1 (High)** — Prevents all end-users from downloading their booked flight tickets from the "My Trips" dashboard.

### Recommended Minimal Fix
1. Add `downloadTicketPdf(transactionId: string): Promise<void>` to `bookingService` in [frontend/src/services/apiClient.ts](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/apiClient.ts) that calls `fetchWithAuth(`/api/trips/${transactionId}/ticket.pdf`)`, converts the response to a Blob, and triggers browser file download.
2. In [TripCard.tsx](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/TripCard.tsx), replace `<a href=...>` with `<button onClick={() => handleDownloadTicket(f.id)}>` invoking `bookingService.downloadTicketPdf`.

---

## 4. FIND-W3D-02 — Flight Price Becomes ₹0

### Source Amount & Currency
- Duffel Offer Search: **$314.64 USD** (~ ₹30,268 estimated).
- Booking Intent in Database: `expected_amount = 314.64`, `currency = 'USD'`.
- Confirmed Duffel Booking: `total_amount = '314.64'`, `total_currency = 'USD'`.

### Transformation Path
```text
Duffel Search / Offer Selection
   ↓ (POST /api/trips/{trip_id}/select-offer)
select_offer_atomic in services/trip_store.py
   ↓ (persists meta["selected_flight_snapshot_id"], BUT DOES NOT save meta["selected_flight"])
Trip Rehydration (GET /api/trips/{trip_id})
   ↓ (trip.metadata has selected_flight_snapshot_id, selected_hotel, but NO selected_flight)
frontend/src/services/apiClient.ts:438
   ↓ (normalizes selectedFlightOffer as { id: meta.selected_flight_snapshot_id } without amount)
BookingReviewPanel.tsx / BookingWorkspace.tsx
   ↓ (flightAmount = Number(flight.total_amount) || 0 → evaluates to 0)
formatCurrency(0) → ₹0
```

### Root Cause
In [services/trip_store.py:select_offer_atomic](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/trip_store.py#L290-L346):
- When `offer_type == "hotel"`, the code retrieves the offer snapshot and saves:
  `meta["selected_hotel"] = offer_snapshot.get("offer_details")` (containing price, currency, hotel details).
- When `offer_type == "flight"`, lines 314–318 do:
  ```python
  meta["selected_flight_snapshot_id"] = req.offer_id
  meta["flight_selected_at"] = datetime.now(timezone.utc).isoformat()
  ```
  It **completely omits** saving `meta["selected_flight"]` with the offer snapshot data (price, currency, slices).

When the frontend rehydrates the trip via `GET /api/trips/{trip_id}`, [frontend/src/services/apiClient.ts:437-440](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/apiClient.ts#L437-L440) parses:
```typescript
selectedFlightOffer: meta.selected_flight
  ? meta.selected_flight
  : meta.selected_flight_snapshot_id
    ? { id: meta.selected_flight_snapshot_id }
    : undefined
```
Because `meta.selected_flight` is missing, it falls back to `{ id: meta.selected_flight_snapshot_id }` with no `total_amount` or `total_currency`.

In [BookingReviewPanel.tsx:64-75](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/BookingReviewPanel.tsx#L64-L75):
```typescript
const flightAmount = Number(flight.total_amount) || 0;
```
Because `total_amount` is `undefined`, `flightAmount` evaluates to `0`. `formatCurrency(0)` then displays:
```text
Flight Transportation: ₹0
Taxes & Partner Booking Fees: ₹0
Total Commitment: ₹0
```
Later, when the flight is confirmed, Duffel confirmation reads directly from `booking_intents.expected_amount` (`314.64 USD`), which is why the confirmation modal correctly shows `$315`.

### Severity
**P1 (High)** — Causes critical financial misinformation in the user checkout workflow, showing a ₹0 total commitment before charging the user.

### Recommended Minimal Fix
1. In [services/trip_store.py:select_offer_atomic](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/trip_store.py#L314-L325), save `meta["selected_flight"]` from `offer_snapshot.get("offer_details")` alongside `selected_flight_snapshot_id`, symmetric to `selected_hotel`.
2. Ensure currency conversion and display in [BookingReviewPanel.tsx](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/BookingReviewPanel.tsx) handles USD-denominated flight offers accurately.

---

## 5. FIND-W3D-03 — Reapproval / 409 Conflict

### Revision State & Approval State Machine
In [services/evidence_store.py:approve_revision_transaction](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/evidence_store.py#L614-L625):
```python
cur.execute(
    """
    SELECT 1 FROM plan_approvals
    WHERE trip_id = %s AND revision_id = %s
    """,
    (trip_id, revision_id),
)
if cur.fetchone():
    return {
        "status": "conflict",
        "message": f"Revision {revision_id} for trip {trip_id} has already been approved.",
        "trip_id": trip_id,
        "revision_id": revision_id,
    }
```
And in [routes/trips_api.py:approve_trip_plan](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/routes/trips_api.py#L292-L300):
```python
if result.get("status") == "conflict":
    raise HTTPException(status_code=409, detail=result["message"])
```

### Server vs Frontend State
1. **Server State:**
   - Trip `5f936834-eccb-40b0-8c04-a2addc6a0182`: `status = "approved"`, `current_revision_id = 0c331af2-48cb-459b-8e9e-a9fe9f66e0c1`.
   - Table `plan_approvals`: row exists for `trip_id = 5f936834...` and `revision_id = 0c331af2...`.
   - Table `booking_intents`: row exists with `status = "COMPLETED"`, `provider_reference = "27LXIN"`.
2. **Frontend State in `BookingWorkspace.tsx`:**
   - In [BookingWorkspace.tsx:90](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/pages/BookingWorkspace.tsx#L90):
     `const [isFlightApproved, setIsFlightApproved] = useState<boolean>(false);`
   - In `syncFromStorage()` ([BookingWorkspace.tsx:190-280](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/pages/BookingWorkspace.tsx#L190-L280)), `isFlightApproved` is **never rehydrated** from the server trip status. Even though `trip.status === "approved"` and a completed booking intent exists, `isFlightApproved` resets to `false` whenever the component remounts or the page is refreshed.
   - When the user clicks "Approve Flight Booking", the frontend invokes `handleApprove()`, which calls `POST /api/trips/{trip_id}/approve`. The backend sees that `revision_id` is already approved and returns `409 Conflict`.

### Root Cause
The backend 409 Conflict response is **correct server protection** enforcing immutability of plan approvals. The defect is in frontend state synchronization:
1. `BookingWorkspace.tsx` stores `isFlightApproved` as volatile component state.
2. It fails to derive approval status from `trip.status === 'approved'` or existing booking intents.
3. It displays the "Awaiting Human Approval" prompt for an already-approved, already-booked revision.

### Severity
**P2 (Medium)** — Confuses the user by prompting for approval after booking is complete and throwing an unhandled 409 error banner.

### Recommended Minimal Fix
1. In [BookingWorkspace.tsx](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/pages/BookingWorkspace.tsx), derive `isPlanApproved` directly from `trip.status === 'approved' || !!trip.is_approved || existingBookings.length > 0`.
2. When the revision is already approved or booked, transition the workspace UI directly to the authorized/booked step and hide the approval CTA.
3. If `POST /approve` returns 409, treat it gracefully as "already approved" in the client handler rather than showing an unhandled error.

---

## 6. FIND-W3D-04 — Hotel Booking Authorization

### Investigation of Authorization Flow
The runtime error observed during hotel payment was:
```text
Active booking authorization is required before processing hotel payment.
Please authorize the plan revision first.
```

### Trace:
1. [BookingWorkspace.tsx:556-568](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/pages/BookingWorkspace.tsx#L556-L568):
```typescript
const handleInitiateHotelOrder = async (provider: 'razorpay' | 'stripe' = 'razorpay') => {
  if (!activeAuthorization) {
    setPaymentError('Active booking authorization is required before processing hotel payment. Please authorize the plan revision first.');
    return;
  }
...
```
2. In `syncFromStorage()` ([BookingWorkspace.tsx:219-223](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/pages/BookingWorkspace.tsx#L219-L223)):
```typescript
if (serverTrip.booking_authorization) {
  setActiveAuthorization(serverTrip.booking_authorization);
}
```
3. In [routes/trips_api.py:get_trip](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/routes/trips_api.py#L225-L270), `GET /api/trips/{trip_id}` returns the serialized trip from `trip_store.get_trip(trip_id)`. **It does not attach `booking_authorization`** to the trip dict! (Only `GET /api/trips/{trip_id}/revisions` queries `AuthorizationStore`).
4. Database inspection of Trip `5f936834-eccb-40b0-8c04-a2addc6a0182`:
   Querying `SELECT * FROM booking_authorizations WHERE trip_id = '5f936834-eccb-40b0-8c04-a2addc6a0182'` returned **zero rows**.
   The user had executed Plan Approval (`POST /api/trips/{trip_id}/approve`), but had never executed Booking Authorization (`POST /api/trips/{trip_id}/booking-approval`).

### Root Cause
Two interrelated factors caused this failure:
1. **API Incompleteness:** `GET /api/trips/{trip_id}` does not attach the active `booking_authorization` record for the current revision, causing `BookingWorkspace` to lose `activeAuthorization` on any page refresh or remount.
2. **UX Ambiguity between Approval and Authorization:** In TrustTrip's design, Revision Approval (`/approve`) and Booking Authorization (`/booking-approval`) are two distinct security gates. The UI does not clearly guide the user from Plan Approval to Booking Authorization prior to opening the hotel checkout modal.

### Severity
**P1 (High)** — Blocks hotel checkout when rehydrating a trip or when booking authorization was not sequentially triggered.

### Recommended Minimal Fix
1. In [routes/trips_api.py:get_trip](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/routes/trips_api.py#L250), query `AuthorizationStore.get_active_authorization(trip_id, trip.current_revision_id)` and populate `trip["booking_authorization"]` in the response.
2. In [BookingWorkspace.tsx](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/pages/BookingWorkspace.tsx), clearly display the Booking Authorization CTA when plan approval is complete but authorization has not yet been executed, preventing opening the payment modal until authorization is active.

---

## 7. FIND-W3D-05 — Duplicate Flight Entries

### Database Reality vs Presentation
Read-only database query of `booking_intents` for trip `5f936834-eccb-40b0-8c04-a2addc6a0182`:
- Intent `61f9023a-f331-41d3-a5c9-25f0f35359a6`:
  - `status`: `FAILED`
  - `provider_reference`: `None`
  - `created_at`: 2026-10-05 18:22:36
- Intent `2a3d6345-d421-4f16-bd8d-dd7d7b5bf4fc`:
  - `status`: `COMPLETED`
  - `provider_reference`: `'27LXIN'`
  - `created_at`: 2026-10-05 18:24:08

Both records are associated with the same trip and user.

### API Aggregation in `trips_api.py` and `BookingReadService`
In [services/booking_read_service.py:list_recent_flights_for_user](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/booking_read_service.py#L70-L85):
```python
cur.execute(
    """
    SELECT id, trip_id, user_id, provider, provider_reference, status, created_at, ...
    FROM booking_intents
    WHERE user_id = %s
    ORDER BY created_at DESC
    LIMIT %s
    """,
    (user_id, limit),
)
```
The query does **NOT** filter by `status = 'COMPLETED'` or `status NOT IN ('FAILED', 'CANCELLED')`.

In [routes/trips_api.py:list_trips](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/routes/trips_api.py#L210-L218):
All flight intents returned for that trip are attached to `trip.flights`.

In [TripCard.tsx:200-205](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/travel/TripCard.tsx#L200-L205):
```tsx
{trip.flights.map((f, i) => (
  <div key={f.id || i} className="flex items-center justify-between text-xs text-secondary-600">
    <span>Flight: {f.booking_reference || 'Booked'}</span>
    <a href={`/api/trips/${f.id}/ticket.pdf`} ...>Ticket</a>
  </div>
))}
```
- For the `COMPLETED` intent (`2a3d...`), `f.booking_reference` is `"27LXIN"`, rendering: `Flight: 27LXIN Ticket`.
- For the `FAILED` intent (`61f9...`), `f.booking_reference` is `null`, so it falls back to `'Booked'`, rendering: `Flight: Booked Ticket`.

### Root Cause Determination
The duplication is a combination of **API over-selection** and **frontend presentation fallacy**:
1. `BookingReadService` returns all `booking_intents`, including failed transactions.
2. `TripCard.tsx` assumes every entry in `trip.flights` represents a valid confirmed flight and defaults missing booking references to `'Booked'`.

### Severity
**P2 (Medium)** — Confusing to travelers who see ghost "Booked" flights on their trip dashboard.

### Recommended Minimal Fix
1. In `BookingReadService.list_recent_flights_for_user` (or in `trips_api.py:list_trips`), filter intents to only include active/completed bookings (`status IN ('COMPLETED', 'CONFIRMED')`).
2. In `TripCard.tsx`, filter out uncompleted or failed flights and do not render ticket download links for non-completed intents.

---

## 8. FIND-W3D-06 — Flight Confirmation 502 / Retry Safety

### Investigation of 502 Bad Gateway
In `services/flight_booking_service.py:confirm_booking`:
When `POST /confirm` is called, it communicates with Duffel API:
```python
try:
    order = await flight_service.provider.book(...)
except Exception as e:
    intent.status = "FAILED"
    raise ProviderBookingError(f"Duffel order creation failed: {e}")
```
If Duffel times out or returns a network error, FastAPI translates `ProviderBookingError` to HTTP 502 Bad Gateway.

### Duplicate Booking Risk (Case B vs Case C)
- If the provider received the order request and successfully placed it before the socket connection timed out, Duffel has created a real booking.
- However, TrustTrip marked the local intent as `FAILED`.
- If the user or frontend retries by creating a new booking intent and confirming again, **Duffel will create a second booking**, charging the customer twice!
- Duffel's provider implementation in `services/flight_service.py` already includes `find_order_by_offer(offer_id)` to reconcile whether an order for that offer exists. However, `confirm_booking` currently fails immediately without executing this reconciliation query.

### Severity
**P1 (High / Financial Risk)** — Risk of multiple provider bookings and double-charging upon retries following network glitches.

### Recommended Minimal Fix
1. In `flight_booking_service.confirm_booking`, before marking an intent as `FAILED` on provider error/timeout, call `find_order_by_offer(intent.offer_id)`.
2. If an order exists on Duffel, recover truthfully: link the order ID, update status to `COMPLETED`, and return success rather than failing.

---

## 9. FIND-W3D-07 — Gemini 503 $\to$ `/api/travel` 500

### Evidence Analysis
Runtime logs showed Gemini returned `503 Service Unavailable`, which resulted in `/api/travel` returning `500 Internal Server Error`.
In [app.py:travel_planner_chat](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/app.py#L320-L335):
```python
try:
    response = await run_travel_agent(...)
except Exception as exc:
    logger.error(f"Error in travel_planner: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Travel planner processing failed.", "detail": str(exc)},
    )
```
When Google Gemini experiences transient load or temporary upstream outages, the Google Generative AI SDK raises `google.api_core.exceptions.ServiceUnavailable` (HTTP 503).
Because `travel_planner_chat` uses a blanket `except Exception`, it wraps this upstream 503 into an internal 500.

### Determination
- **Not Quota Exhaustion:** Quota exhaustion in Google GenAI emits HTTP 429 `ResourceExhausted`. A 503 response indicates a transient provider-side server unavailable state.
- **Error Propagation:** The API should catch upstream 503 errors and emit a structured HTTP 503 with a `Retry-After` header or a user-friendly "Travel planning service temporarily unavailable; please retry" payload rather than an opaque 500 crash.

### Severity
**P3 (Low)** — Error-handling refinement.

### Recommended Minimal Fix
In [app.py:travel_planner_chat](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/app.py), add specific exception handling for upstream service unavailability, returning HTTP 503 with a retry advisory.

---

## 10. FIND-W3D-08 — Hotel `pricing_basis=None`

### Log Evidence
```text
Hotel offer ... has unrecognized pricing_basis=None;
skipping offer to avoid silent full_stay assumption.
```

### Provider Inspection
In `services/hotel/liteapi_provider.py`:
LiteAPI rates are retrieved for a specific date range (`checkin` to `checkout`). The total amount in the response represents the full stay cost. However, `liteapi_provider.py` does not explicitly set `pricing_basis = "full_stay"` on the converted `HotelOffer` objects.

In `services/travel_planner.py:calculate_budget`:
```python
if offer.pricing_basis == "full_stay":
    total = offer.price
elif offer.pricing_basis == "per_night":
    total = offer.price * nights
else:
    logger.warning(f"Hotel offer {offer.id} has unrecognized pricing_basis={offer.pricing_basis}; skipping offer...")
    continue
```
Because `offer.pricing_basis` is `None`, the validator skips the offer.

### Validation of Safety Behavior
The fail-closed behavior in `travel_planner.py` is **correct and safe**: it avoids erroneously multiplying a full-stay rate by the number of nights.
The fix must not remove the fail-closed check; rather, `liteapi_provider.py` must explicitly populate `pricing_basis="full_stay"` and calculate `nights = (checkout - checkin).days`.

### Severity
**P2 (Medium)** — Discards valid hotel options from search results.

### Recommended Minimal Fix
In `services/hotel/liteapi_provider.py`, set `pricing_basis="full_stay"` and compute `nights` on every parsed offer.

---

## 11. FIND-W3D-09 — Flight Razorpay Requirement

### Architecture Review
- **Hotel Booking Architecture:**
  `Hotel Selection` $\to$ `Prebook` $\to$ `Razorpay Order Creation` $\to$ `Razorpay Checkout Modal` $\to$ `Payment Verification` $\to$ `Hotel Provider Confirmation`.
- **Flight Booking Architecture (Duffel):**
  `Flight Selection` $\to$ `Revalidation` $\to$ `Human-in-the-Loop Revision Approval` $\to$ `Booking Authorization` $\to$ `Duffel Order Confirmation` (`services/flight_service.py` / `flight_booking_service.py`).
  Duffel operates as the merchant of record or settles directly via Duffel Balance / airline direct ticketing.

### Classification
Flight booking in TrustTrip was deliberately architected without Razorpay. This is **NOT a defect**.
If product requirements dictate that flights must also be routed through Razorpay prior to Duffel issuance, that represents a **New Product Feature / Architectural Change**, which is out of scope for Wave 3D bug remediation.

---

## 12. Revision/HITL Readiness

The revision system was inspected for manual testing readiness:
1. `Revision #1 Creation`: Triggered on initial trip generation (`current_revision_number = 1`, `status = 'draft'`).
2. `Revision #1 Approval`: `POST /api/trips/{trip_id}/approve` checks `plan_approvals`. If absent, inserts record and transitions trip to `status = 'approved'`.
3. `Booking Authorization`: `POST /api/trips/{trip_id}/booking-approval` creates a row in `booking_authorizations` for `(trip_id, revision_id)`.
4. `Stale Revision Detection`: In `assert_booking_eligible`, the backend validates that `intent.revision_id == trip.current_revision_id` and that an active authorization exists for that exact `revision_id`.

**Required Manual Test Scenario for Future Wave:**
- Create Trip $\to$ Modify flight dates $\to$ Verify Revision #2 is created with `current_revision_number = 2`.
- Verify Revision #1 authorization is invalidated.
- Verify hotel payment cannot proceed until Revision #2 is approved and authorized.

---

## 13. Security Audit

All affected paths were evaluated against TrustTrip security and tenant isolation policies:
1. **Ticket Download (`GET /api/trips/{transaction_id}/ticket.pdf`):**
   - Correctly enforces `current_user = Depends(get_current_user)`.
   - Correctly enforces tenant isolation: queries `booking_intents` where `id = transaction_id` AND `user_id = current_user["id"]`.
   - The 401 defect is strictly caused by the frontend anchor failing to transmit the user's valid JWT.
2. **Booking Approval & Authorization:**
   - Both endpoints verify trip ownership (`trip.user_id == current_user["id"]`) and tenant tenancy.
3. **Data Integrity:**
   - The backend 409 prevents duplicate or conflicting approvals for the same revision.

---

## 14. Git/Safety Audit

- **Frozen Commit:** `d21ef82`
- **Worktree State:** Zero modifications were introduced by this pre-implementation audit.
- **Untracked / Pre-existing:** Pre-existing working tree state from earlier waves remains unchanged.
- **Database Safety:** All queries executed were `SELECT` statements; no `INSERT`, `UPDATE`, `DELETE`, `ALTER`, or `DROP` statements were run.

---

## 15. Test Baseline

Existing test suite execution verified that the baseline remains intact:
- Wave 1, 2, 3A, 3B, 3C tests: **38/38 PASSED**
- Full backend suite: **843/843 PASSED**
- Frontend unit tests: **12/12 PASSED**

---

## 16. Finding Summary

| ID | Severity | Finding | Root Cause Known? | Recommended Scope |
|---|---|---|---|---|
| **W3D-01** | P1 | Ticket download returns 401 Unauthorized | Yes (browser `<a href>` bypasses Bearer token) | **3D-A** (Critical) |
| **W3D-02** | P1 | Flight price displays as ₹0 in Booking Review | Yes (`select_offer_atomic` omits `selected_flight`) | **3D-A** (Critical) |
| **W3D-03** | P2 | Approval button 409 Conflict after booking | Yes (Frontend amnesia; backend 409 is correct) | **3D-A** (Critical) |
| **W3D-04** | P1 | Hotel payment blocked by missing authorization | Yes (`get_trip` omits auth record + UX gap) | **3D-A** (Critical) |
| **W3D-05** | P2 | Duplicate flight entries in My Trips | Yes (Reads failed intents; frontend labels 'Booked') | **3D-A** (Critical) |
| **W3D-06** | P1 | 502 during flight confirmation / retry safety | Yes (`confirm_booking` fails without Duffel reconcile)| **3D-B** (Robustness) |
| **W3D-07** | P3 | Gemini 503 mapped to generic 500 | Yes (Untyped exception catch in `travel_planner_chat`)| **3D-B** (Robustness) |
| **W3D-08** | P2 | Hotel offers skipped with `pricing_basis=None` | Yes (LiteAPI provider omits pricing_basis/nights) | **3D-C** (Normalization) |
| **W3D-09** | Info | Flight Razorpay architecture | Yes (Intended design outside Razorpay) | **Out of Scope** |

---

## 17. Recommended Wave 3D Scope

### 3D-A: Critical Booking, Document & State Defects
1. **W3D-01:** Convert Ticket PDF download in `TripCard.tsx` from raw anchor to authenticated `apiClient` fetch.
2. **W3D-02:** Persist `selected_flight` offer snapshot in `services/trip_store.py:select_offer_atomic` to eliminate the ₹0 price display.
3. **W3D-03:** Derive plan approval state in `BookingWorkspace.tsx` from server state and handle 409 gracefully.
4. **W3D-04:** Attach `booking_authorization` in `routes/trips_api.py:get_trip` and align workspace checkout state.
5. **W3D-05:** Filter `BookingReadService.list_recent_flights_for_user` to active/completed bookings and clean up `TripCard.tsx`.

### 3D-B: Infrastructure & Provider Robustness
1. **W3D-06:** Reconcile Duffel orders via `find_order_by_offer` prior to marking intents as failed.
2. **W3D-07:** Translate upstream Gemini 503 to structured HTTP 503 response.

### 3D-C: Hotel Provider Normalization
1. **W3D-08:** Populate `pricing_basis = "full_stay"` and `nights` in `LiteApiHotelProvider`.

### Out of Scope
- **W3D-09:** Razorpay integration for flight booking (architectural enhancement).
- Any modifications to frozen Wave 3C cancellation endpoints.
- Any manual mutations of the database.

---

## 18. Final Decision

**`WAVE 3D — INVESTIGATION COMPLETE, READY FOR CONTROLLED IMPLEMENTATION`**
