# TrustTrip — Wave 3 Pre-Implementation Audit
## End-to-End Booking Lifecycle, Hotel Checkout Realization & State Persistence

**Status:** INVESTIGATION COMPLETE  
**Role:** Wave 3 Investigator  
**Mode:** READ-ONLY (Zero production code modifications)  
**Date:** October 2026  
**Corpus / Workspace:** `TrustTrip_TravelPlanner`  

---

## 1. Executive Summary

Wave 1 established canonical revision architecture, atomic monotonic increments, Section 9 booking eligibility, and natural-language destination gateway airport identity (`CCU` $\to$ `IXB`). Wave 2 established real traveler identity ($1 \le N \le 9$ passengers), decoupled authenticated account users from travelers, removed synthetic passenger identities from production booking, and propagated explicit traveler data to Duffel Path A and hotel guest/holder structures. Both Wave 1 and Wave 2 are officially closed, verified, and frozen.

This read-only Wave 3 investigation was conducted to audit the entire repository, trace runtime flight, hotel, HITL, and payment paths, assess test suite coverage, identify latent defects, dead code, and security/reliability gaps, and define a coherent, high-value Wave 3 scope.

### Key Audit Findings
1. **Critical Hotel Prebook Contract Mismatch (P0 — [`W3-FIND-01`](#w3-find-01)):** Frontend `bookingService.prepareHotelBooking` transmits flat fields `{ hotel_id, room_id, check_in_date, check_out_date, guests, guest_name, guest_email }` expecting `{ prebook_id, amount, currency }`. Backend router `hotel_api.py:764` expects `HotelPrebookRequest(offer: dict)` and returns nested `{"success": true, "prebook": {"prebook_id": ...}}` omitting `amount` and `currency`. At live runtime, prebook fails with HTTP 422 (`Field required: offer`), and downstream `processHotelPayment` receives `undefined` amounts, crashing with HTTP 422.
2. **Simulated Payment Signatures & Disconnected Razorpay Checkout (P0 — [`W3-FIND-02`](#w3-find-02)):** `PaymentReviewModal.tsx` is an in-browser mock modal that does not load the Razorpay Checkout SDK. When the user confirms payment, `bookingService.processHotelPayment` fabricates synthetic signatures (`razorpay_payment_id: "pay_" + tx_id`, `razorpay_signature: "sig_" + tx_id`). In any environment where real Razorpay credentials exist, `hotel_payment_api.py:204` verifies the HMAC-SHA256 signature and strictly rejects the transaction with HTTP 400 (`Invalid Razorpay payment signature`). Real hotel payment is completely blocked.
3. **Confirmed Bookings Lost on Page Refresh / Rehydration (P1 — [`W3-FIND-03`](#w3-find-03)):** Confirmed bookings update local component state and localStorage, but never update the backend `trips` record (`trip.status` remains `"approved"` or `"planning"`). On line 237–240 of `BookingWorkspace.tsx`, the workspace mount effect explicitly resets confirmed state: `setFlightState(saved.flightState === 'confirmed' ? 'idle' : saved.flightState)` and `setFlightConfirmation(undefined)`. A page reload completely wipes the confirmation view.
4. **Canonical Path A Flight Bookings Invisible in "My Trips" Dashboard (P1 — [`W3-FIND-04`](#w3-find-04)):** `trips_api.py:list_trips` queries `flight_transactions` for flight records. The canonical production flight booking path (Path A in `FlightBookingService`) writes exclusively to `booking_intents`. Confirmed Path A flights never appear in the user's "My Trips" dashboard.
5. **Broken Post-Booking Cancellation Workflow (P1 — [`W3-FIND-05`](#w3-find-05)):** `TripConfirmationView.tsx` cancellation request passes `bkg_${tripId}` (a non-UUID string) to `POST /api/bookings/{id}/cancel-intent`. FastAPI rejects this with HTTP 422. Furthermore, `FlightBookingLifecycleService.cancel_booking_intent` rejects completed bookings with HTTP 400 (`INVALID_BOOKING_STATE`), and hotel bookings have zero cancellation endpoints.
6. **Synthetic Fallback Tokens in Production Booking Code (P2 — [`W3-FIND-06`](#w3-find-06)):** Fallback literals persist in `BookingWorkspace.tsx` (`'fl_off_101'`, `'ht_off_101'`, `'guest@trusttrip.com'`, `'+1234567890'`), `bookingService.ts` (`860`, `915`, `'rm_${hotelId}_01'`), and `hotel_booking_service.py:123` (hardcoded `* 85.0` exchange rate fallback).
7. **Architectural Triplication of Flight Booking (P2 — [`W3-FIND-07`](#w3-find-07)):** The repository houses 3 divergent flight booking subsystems: Path A (`FlightBookingService`, active in UI), Hosted Lifecycle Path (`FlightBookingLifecycleService`, 2,065 lines, uncalled), and Razorpay Path B (`payment_api.py`, 914 lines, uncalled).

---

## 2. Frozen Baseline

The investigation verified the integrity of the frozen Wave 1 and Wave 2 baseline against active source code and live test execution:

### Wave 1 Baseline (Verified & Green)
- **Canonical Revision Architecture:** Revision #1 is created atomically with status `PROPOSED` upon initial itinerary generation via `POST /api/travel` ([`app.py:366–397`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/app.py#L366-L397)). Re-planning monotonically increments to Revision N+1 (`PROPOSED`).
- **Real Persisted Revision UUID:** Authoritative UUIDs are persisted in `plan_revisions` (`EvidenceRevisionStore`). No synthetic `rev_` IDs reach production booking.
- **HITL Revision Approval:** Revision approval transitions `PROPOSED` $\to$ `APPROVED` under trip row lock via `POST /api/trips/{trip_id}/approve` ([`trips_api.py:561–628`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L561-L628)).
- **Canonical Airport Identity:** Natural language destinations resolve to commercial gateway airports (e.g. "Darjeeling, India" $\to$ `IXB`, "Kolkata, India" $\to$ `CCU`). Canonical IATA codes are persisted in `trip.metadata["destination_airport"]` and constraint snapshots ([`app.py:292–337`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/app.py#L292-L337)).
- **Section 9 Booking Authorization:** 10 strict eligibility guards enforced by `AuthorizationStore.assert_booking_eligible` ([`services/authorization_store.py:195–320`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/authorization_store.py#L195-L320)). Booking order creation is blocked without explicit authorization for the exact active revision.
- **Wave 1 Regression Status:** All 96 dedicated Wave 1 tests pass (10 in `test_wave1_canonical_plan_revisions.py`, 32 in `test_section8_hitl_revisions.py`, 54 in `test_section9_booking_authorization.py`).

### Wave 2 Baseline (Verified & Green)
- **Decoupled User vs Traveler:** Authenticated user identity (`domain/user.py`) contains only `id`, `email`, `is_active`, `created_at`. All traveler profile data is collected booking-specifically via [`TravelerModal.tsx`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/TravelerModal.tsx).
- **Explicit Traveler Precedence:** Explicit traveler email strictly overrides account email ([`BookingWorkspace.tsx:194–201`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/BookingWorkspace.tsx#L194-L201)).
- **Multi-Traveler Support ($1 \le N \le 9$):** Modal dynamically renders $N$ tabs synced with `trip.travelers_count`. Backend validates `len(passengers) == trip.travelers_count` in `FlightBookingService.select_offer` ([`services/flight_booking_service.py:275–295`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py#L275-L295)). Mismatches fail closed with HTTP 400.
- **Duffel Path A Traveler Propagation:** Exact traveler records propagate to `booking_intents.metadata["passengers"]` and into `DuffelProvider.book(offer_id, passengers)`.
- **Hotel Holder & Guest Mapping:** Lead traveler maps to hotel `holder`; all $N$ travelers map to hotel `guests` with sequential `occupancyNumber` ([`bookingService.ts:575–596`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L575-L596)).
- **Stale Intent Invalidation:** Editing traveler details invalidates pending `bookingId`, resets flight booking step, and clears intent ([`BookingWorkspace.tsx:295–318`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/BookingWorkspace.tsx#L295-L318)).
- **Wave 2 Remediation Audit Findings:** `W2-FIND-01` (cancellation contact email), `W2-FIND-02` (hotel prebook guest name derivation), and `W2-FIND-03` (mock constant removal) are closed and verified.
- **Wave 2 Regression Status:** 9/9 tests pass in `tests/test_wave2_traveler_identity.py`, 7/7 tests pass in `wave2TravelerIdentity.test.tsx`.

---

## 3. Repository / Architecture Map

### Subsystem Directory Map

```text
TrustTrip_TravelPlanner/
├── app.py                           # App entry point, lifespan, CORS, /api/travel planner & PDF routes
├── auth_api.py                      # /api/auth: register, login, me
├── trips_api.py                     # /api/trips: CRUD, revisions, approval, rejection, booking-approval, tickets
├── flight_api.py                    # /api/flights: search, Section 5B booking (Path A), recheck
├── flight_booking_api.py            # /api/bookings: lifecycle service (hosted checkout / handoff prototype)
├── hotel_api.py                     # /api/hotels: search, select, prebook
├── hotel_payment_api.py             # /api/hotel-payments: create-order, verify-and-book, create-order-from-snapshot
├── payment_api.py                   # /api/payments: flight Razorpay order & verify (Path B prototype)
├── offers_api.py                    # /api/offers: revalidate snapshot
├── research_api.py                  # /api/research: RAG research sessions & facts
│
├── services/
│   ├── trip_store.py                # TripStore: trips table, row locks, create_revision_atomic
│   ├── evidence_store.py            # EvidenceRevisionStore: plan_revisions, approvals, rejections
│   ├── authorization_store.py       # AuthorizationStore: booking_authorizations, 10 eligibility guards
│   ├── search_session_store.py      # SearchSessionStore: search_sessions, offer_snapshots, booking_intents
│   ├── transaction_store.py         # TransactionStore: flight_transactions (Path B only)
│   ├── hotel_transaction_store.py   # HotelTransactionStore: hotel_transactions
│   ├── auth_store.py                # UserStore: users table
│   ├── flight_booking_service.py    # FlightBookingService: Section 5B Path A canonical booking
│   ├── flight_booking_lifecycle_service.py # FlightBookingLifecycleService (hosted handoff prototype)
│   ├── flight_service.py            # FlightService: DuffelProvider abstraction
│   ├── duffel_provider.py           # DuffelProvider: live Duffel REST API client
│   ├── hotel/
│   │   ├── hotel_service.py         # HotelService: orchestrates LiteAPI & Hotelbeds
│   │   ├── liteapi_provider.py      # LiteAPIProvider: /hotels/rates, /rates/prebook, /rates/book
│   │   ├── hotelbeds_provider.py    # HotelbedsProvider: /hotel-api/1.0/hotels (mTLS required)
│   │   └── hotel_provider.py        # Abstract base provider & exceptions
│   ├── hotel_booking_service.py     # HotelBookingService: snapshot checkout bridge
│   ├── razorpay_service.py          # RazorpayService: orders, HMAC-SHA256 signature verification
│   ├── currency_converter.py        # CurrencyConverter: Frankfurter live FX rates
│   ├── ticket_service.py            # build_booking_pdf: Flight ticket PDF
│   └── hotel_booking_pdf.py         # build_hotel_booking_pdf: Hotel voucher PDF
│
├── frontend/src/
│   ├── lib/
│   │   ├── apiClient.ts             # Centralized HTTP client, envelope normalization, error mapping
│   │   └── currency.ts              # Currency formatting utilities
│   ├── services/
│   │   └── bookingService.ts        # Frontend booking state machine adapter
│   ├── types/
│   │   ├── trip.ts                  # TripDetail, TripRevision, BookingAuthorizationInfo
│   │   ├── flight.ts                # FlightOffer, FlightPassenger, FlightBookingSelectionResponse
│   │   ├── hotel.ts                 # HotelOffer, HotelPrebookRequest, HotelPrebookResponse
│   │   └── bookingFrontend.ts       # IBookingService interface, Confirmation records
│   └── components/booking/
│       ├── BookingWorkspace.tsx     # Main 4-stage booking hub
│       ├── BookingReviewPanel.tsx   # Review plan, revision info, travelers list
│       ├── ApprovalCard.tsx         # Plan approval card
│       ├── FlightVerificationCard.tsx # Revalidation, price change acceptance, HITL approval, confirm
│       ├── HotelBookingPanel.tsx    # Hotel selection, prebook trigger, payment button
│       ├── PaymentReviewModal.tsx   # Simulated payment review modal
│       ├── TravelerModal.tsx        # Multi-passenger identity entry modal (1..9)
│       └── TripConfirmationView.tsx # Confirmation hero banner, voucher display, cancellation modal
│
├── migrations/                      # 9 active SQL migration scripts (004 through 012)
└── tests/                           # 49 test suites (812 tests total, 100% passing)
```

### PROJECT_MAP.md Discrepancy Analysis
1. `PROJECT_MAP.md:54` states: "PostgreSQL 15+ (12 SQL migrations)". Active `migrations/` directory contains 9 files (`004_travel_intelligence.sql` through `012_hotel_snapshot_long_provider_offer_id.sql`). Initial schemas 001–003 are initialized via `services/db.py` / `services/auth_store.py` / `services/trip_store.py` or stored in `migrations/versions/`.
2. `PROJECT_MAP.md:75, 237–252` documents Hotel Pre-book as returning `prebook_id, locked amount, currency`. In reality, `hotel_api.py:764` expects `{"offer": {...}}` and returns `{"success": true, "prebook": {"prebook_id": ...}}` without `amount` or `currency`.
3. `PROJECT_MAP.md:76` lists `payment_api.py` as active for flight Razorpay payments. In reality, Path B is completely unwired from the frontend, while Path A (`flight_api.py`) is the sole active flight path.

---

## 4. Current Booking Runtime Paths

### Flight Booking Runtime Path (Canonical Path A)

```text
[Trip Workspace] (TripDetailView.tsx)
       │
       ▼ User selects flight card
POST /api/trips/{trip_id}/select-offer (OfferSelectionRequest)
       │  TripStore.select_offer_atomic() binds snapshot_id to trip.metadata
       ▼
[Booking Workspace: Stage 1 Review] (BookingWorkspace.tsx)
       │
       ├─► User reviews revision: POST /api/trips/{trip_id}/approve
       ├─► User authorizes booking: POST /api/trips/{trip_id}/booking-approval
       │     (AuthorizationStore.assert_booking_eligible enforces 10 guards)
       ▼
[Booking Workspace: Stage 2 Flight Verification]
       │
       ├─► User completes TravelerModal (1 <= N <= 9 passengers)
       │     (Completeness check: isAllTravelersComplete)
       ▼
POST /api/flights/booking/select
       │  Req: { trip_id, offer_id, passengers: [FlightPassenger x N] }
       │  FlightBookingService.select_offer()
       │    - Validates len(passengers) == trip.travelers_count
       │    - Inserts booking_intents row (status='SELECTED', expected_amount, currency)
       │  Returns: FlightBookingSelectionResponse (booking_id)
       ▼
POST /api/flights/booking/{booking_id}/revalidate
       │  FlightBookingService.revalidate_offer()
       │    - Rechecks fare via FlightService.recheck(raw_provider_offer_id)
       │    - If price changed: transitions to PRICE_CHANGED, raises 409
       │    - If unchanged: transitions to READY_FOR_BOOKING
       ▼
POST /api/flights/booking/{booking_id}/approve (HITL Gate)
       │  FlightBookingService.record_approval()
       │  Sets metadata["approved"] = true, metadata["approved_at"] = now
       ▼
POST /api/flights/booking/{booking_id}/confirm
       │  Req: { final_confirmation: true }
       │  FlightBookingService.confirm_booking()
       │    - Asserts approved == true
       │    - Rechecks live fare immediately prior to booking
       │    - Transitions status to HANDED_OFF
       │    - Calls DuffelProvider.book(raw_provider_offer_id, passengers, payment_mode="auto")
       │    - Duffel creates /air/orders
       │    - Transitions status to COMPLETED
       │    - Persists booking_reference, order_id to booking_intents
       ▼
[Client State Update]
       BookingWorkspace sets flightState='confirmed', saves to localStorage.
       NOTE: Backend trips table is NOT updated. Status remains "approved".
```

### Hotel Booking Runtime Path (Current Broken State)

```text
[Booking Workspace: Stage 3 Hotel Reservation]
       │
       ▼ User clicks "Prepare Reservation" (handlePrepareHotel)
bookingService.prepareHotelBooking(tripId, hotelId, offerData, guestInfo)
       │
       ▼ Sends: { hotel_id, room_id, check_in_date, check_out_date, guests, guest_name, guest_email }
POST /api/hotels/prebook
       │
       ❌ CRITICAL CONTRACT FAILURE (W3-FIND-01):
       Backend expects HotelPrebookRequest(offer: dict).
       FastAPI rejects payload with HTTP 422: "Field required: offer".
       (In test environment: mock returns { prebook_id: "pb_1" } without amount/currency).
       │
       ▼ User clicks "Proceed to Payment" -> PaymentReviewModal opens
       │
       ▼ User clicks "Confirm Payment"
bookingService.processHotelPayment(tripId, method, contact, revId, revNum, travelers)
       │
       ├─► Generates client idempotency key: `idem_ht_${tripId}_${Date.now()}`
       ├─► Maps lead traveler to holder, all travelers to guests
       ▼
POST /api/hotel-payments/create-order
       │  Req: { offer, prebook, holder, guests, trip_id, revision_id, expected_revision_number, payment_idempotency_key }
       │  hotel_payment_api.create_order():
       │    - Asserts Section 9 booking eligibility
       │    - If Razorpay configured: creates order with rz.create_order()
       │    - Stores reservation in HotelTransactionStore (status="created")
       │    - Returns { transaction_id, razorpay_order_id, amount_inr, key_id }
       ▼
[Client Razorpay Handoff]
       │
       ❌ SIMULATION FAILURE (W3-FIND-02):
       Frontend DOES NOT open Razorpay Checkout modal.
       Instead, bookingService synthesizes fake values:
         razorpay_payment_id: "pay_" + transaction_id
         razorpay_signature: "sig_" + transaction_id
       ▼
POST /api/hotel-payments/verify-and-book
       │  Req: { transaction_id, razorpay_order_id, razorpay_payment_id, razorpay_signature, ... }
       │
       ❌ SIGNATURE VERIFICATION FAILURE (W3-FIND-02):
       hotel_payment_api.py:204 checks rz.verify_checkout_signature().
       Because signature is fake, backend rejects with HTTP 400: "Invalid Razorpay payment signature".
       (If bypassed in mock: HotelService.book() calls LiteAPI /rates/book,
        updates hotel_transactions to "booked", and generates PDF).
       ▼
[Client State Update]
       BookingWorkspace sets hotelState='confirmed', saves to localStorage.
       NOTE: Backend trips table is NOT updated.
```

### HITL Revision & Booking Authorization Runtime Path

```text
[Trip Workspace]
       │
       ▼ User clicks "Create Trip" or edits constraints
POST /api/trips or POST /api/trips/{trip_id}/revisions
       │  TripStore.create_revision_atomic() locks trip row
       │  Inserts row in plan_revisions with revision_number = N+1, status = PROPOSED
       │  Sets trip.current_revision_id = new_uuid, trip.status = "planning"
       ▼
[Booking Workspace: Review Stage]
       │
       ▼ User clicks "Approve Plan"
POST /api/trips/{trip_id}/approve
       │  Req: { revision_id: UUID, expected_revision_number: int }
       │  trips_api.approve_trip():
       │    - Asserts revision_id == trip.current_revision_id
       │    - Asserts target_rev.revision_number == expected_revision_number
       │    - EvidenceRevisionStore.approve_revision_transaction() inserts plan_approvals row
       │    - Updates trip status = "approved"
       ▼
[Plan Revision Approved]
       │
       ▼ User clicks "Authorize Booking"
POST /api/trips/{trip_id}/booking-approval
       │  Req: { revision_id: UUID, expected_revision_number: int }
       │  trips_api.approve_booking():
       │    - AuthorizationStore.assert_booking_eligible() verifies 10 strict guards:
       │        1. Trip ownership
       │        2. Revision existence
       │        3. Plan approval presence
       │        4. Monotonic revision version matching
       │        5. Departure date >= today
       │        6. Origin != destination
       │        7. Passenger count > 0
       │        8. Selected offer tenant isolation
       │        9. Snapshot consistency
       │        10. No active rejections
       │    - Inserts booking_authorizations row
       │    - Returns { is_authorized: true, authorization_id, authorized_at }
```

### Payment Runtime Paths Comparison

| Attribute | Flights (Path A — Canonical) | Flights (Path B — Unwired Prototype) | Hotels (Section 10 — Active) |
|---|---|---|---|
| **Payment Gateway** | None (instant/hold via airline) | Razorpay INR orders | Razorpay INR orders |
| **API Routers** | `/api/flights/booking/*` | `/api/payments/*` | `/api/hotel-payments/*` |
| **Frontend Call Sites** | `BookingWorkspace.tsx:516` | **Zero call sites** | `BookingWorkspace.tsx:749` |
| **Authoritative Table** | `booking_intents` | `flight_transactions` | `hotel_transactions` |
| **Provider Settlement** | Duffel `/air/orders` (`payment_mode="auto"`) | Duffel `/air/orders` after capture | LiteAPI `/rates/book` after capture |
| **Signature Verification** | N/A | Server HMAC-SHA256 | Server HMAC-SHA256 |
| **Current Live Status** | **100% Active & Operational** | **Unwired Backend Code** | **Blocked by W3-FIND-01 & 02** |

---

## 5. Wave 3 Investigation Findings

### W3-FIND-01
- **Title:** Critical Contract Mismatch on Hotel Prebook (`POST /api/hotels/prebook`)
- **Priority:** P0 — Critical
- **Area:** Hotel Booking / API Contracts
- **Current Behavior:**
  - Frontend [`bookingService.ts:480–488`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L480-L488) calls `api.prebookHotel` with:
    `{ hotel_id, room_id, check_in_date, check_out_date, guests, guest_name, guest_email }`.
  - Backend router [`hotel_api.py:764–774`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/hotel_api.py#L764-L774) defines:
    ```python
    class HotelPrebookRequest(BaseModel):
        offer: dict[str, Any]
    @router.post("/prebook")
    async def prebook_hotel(req: HotelPrebookRequest):
    ```
  - Backend expects `req.offer.provider_offer_id` and returns `{"success": true, "prebook": {"prebook_id": ...}}`.
  - Frontend expects top-level `res.prebook_id`, `res.amount`, `res.currency`.
  - Because `offer` is missing from the payload, FastAPI returns HTTP 422: `Field required: offer`.
  - When mocked in unit tests, `res.amount` is `undefined`, causing `bookingService.ts:562` to pass `price: undefined`, crashing `hotel_payment_api.py:121` with `TypeError: float() argument must be a string or a real number, not 'NoneType'`.
- **Expected Behavior:** `POST /api/hotels/prebook` must accept a standardized Pydantic model compatible with the frontend client (or accept `snapshot_id`), validate tenant and trip ownership, invoke `HotelService.prebook()`, and return `{ success: true, prebook_id, amount, currency, status }`.
- **Evidence:** [`hotel_api.py:79–82, 764–774`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/hotel_api.py#L79-L82), [`frontend/src/types/hotel.ts:69–87`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/types/hotel.ts#L69-L87), [`frontend/src/services/bookingService.ts:474–508`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L474-L508), [`API_CONTRACTS.md:224–245`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/API_CONTRACTS.md#L224-L245).
- **Affected Files:** `hotel_api.py`, `frontend/src/services/bookingService.ts`, `frontend/src/types/hotel.ts`, `frontend/src/lib/apiClient.ts`.
- **Runtime Path:** `BookingWorkspace.handlePrepareHotel` $\to$ `bookingService.prepareHotelBooking` $\to$ `api.prebookHotel` $\to$ `POST /api/hotels/prebook`.
- **Why It Matters:** Blocks the entire hotel booking workflow at Step 1 of hotel reservation.
- **Risk:** High regression risk if payload structure changes without updating contract tests.
- **Dependencies:** `HotelService.prebook`, `SearchSessionStore`.
- **Recommended Scope:** Align the contract: have `prebook` accept `{ trip_id, snapshot_id, hotel_id, room_id, check_in_date, check_out_date, guests, guest_name, guest_email }`, resolve authoritative offer data, and return `{ prebook_id, amount, currency, status }`.
- **Out of Scope:** Refactoring hotel search scoring algorithms.
- **Suggested Validation:** Pytest test case executing `client.post("/api/hotels/prebook")` with exact frontend payload and verifying 200 response with `prebook_id`, `amount`, `currency`.

---

### W3-FIND-02
- **Title:** Simulated Razorpay Signatures & Disconnected Checkout in Hotel Payment Flow
- **Priority:** P0 — Critical
- **Area:** Payment / Security / Provider Integration
- **Current Behavior:**
  - In [`frontend/src/components/booking/PaymentReviewModal.tsx:267`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/PaymentReviewModal.tsx#L267), the UI displays a simulation notice: `"Simulated prototype boundary. In production, Razorpay handles credentials with zero sensitive card storage."`
  - When the user confirms payment, `bookingService.processHotelPayment` calls `createHotelPaymentOrder`, receives a real `razorpay_order_id`, and then fabricates fake payment signatures ([`bookingService.ts:619–620`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L619-L620)):
    ```typescript
    razorpay_payment_id: paymentDetails?.razorpay_payment_id || `pay_${orderRes.transaction_id}`,
    razorpay_signature: paymentDetails?.razorpay_signature || `sig_${orderRes.transaction_id}`,
    ```
  - When sent to `POST /api/hotel-payments/verify-and-book`, backend [`hotel_payment_api.py:204–216`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/hotel_payment_api.py#L204-L216) checks:
    ```python
    if not rz.verify_checkout_signature(req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid Razorpay payment signature.")
    ```
  - In any environment with live or test Razorpay keys configured, this signature verification fails with HTTP 400.
- **Expected Behavior:**
  - In live/test mode, the frontend should dynamically mount the Razorpay Checkout script (`checkout.razorpay.com/v1/checkout.js`), open the checkout modal using `key_id` and `razorpay_order_id`, and collect the real `razorpay_payment_id` and `razorpay_signature` upon customer payment completion before invoking `/verify-and-book`.
  - In sandbox/demo test environments without Razorpay keys, an explicit, secure server-side sandbox bypass mode should be declared (similar to `PRESENTATION_MODE` in `payment_api.py:64`), rather than fabricating signatures client-side.
- **Evidence:** [`frontend/src/components/booking/PaymentReviewModal.tsx:267`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/PaymentReviewModal.tsx#L267), [`frontend/src/services/bookingService.ts:618–620`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L618-L620), [`hotel_payment_api.py:202–216`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/hotel_payment_api.py#L202-L216).
- **Affected Files:** `frontend/src/components/booking/PaymentReviewModal.tsx`, `frontend/src/components/booking/BookingWorkspace.tsx`, `frontend/src/services/bookingService.ts`, `hotel_payment_api.py`.
- **Runtime Path:** `BookingWorkspace.handleProcessHotelPayment` $\to$ `bookingService.processHotelPayment` $\to$ `POST /api/hotel-payments/verify-and-book`.
- **Why It Matters:** Prevents real or test hotel checkout from functioning against the live payment gateway.
- **Risk:** High security and payment integrity risk.
- **Dependencies:** `RazorpayService`, `HotelTransactionStore`.
- **Recommended Scope:** Connect standard Razorpay modal checkout in frontend; handle payment response; transmit verified signature to backend.
- **Out of Scope:** Implementing alternative payment gateways (Stripe, PayPal).
- **Suggested Validation:** End-to-end checkout test with Razorpay test keys verifying signature validation succeeds and records captured payment.

---

### W3-FIND-03
- **Title:** Confirmed Bookings Lost on Browser Refresh / Rehydration
- **Priority:** P1 — High
- **Area:** Data Integrity / Frontend State Rehydration
- **Current Behavior:**
  - When flight confirmation completes in `BookingWorkspace.tsx:654–659`, confirmation is written only to local component state and localStorage (`saveStoredBookingState`).
  - No API call is made to update `trips.status = 'booked'` or attach the confirmation to the canonical trip record.
  - On mount, `syncFromStorage` in [`BookingWorkspace.tsx:237–240`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/BookingWorkspace.tsx#L237-L240) contains:
    ```typescript
    setFlightState(saved.flightState === 'confirmed' ? 'idle' : saved.flightState);
    setFlightConfirmation(undefined);
    setHotelState(saved.hotelState === 'confirmed' ? 'idle' : saved.hotelState);
    setHotelConfirmation(undefined);
    ```
  - Upon browser refresh, the workspace discards confirmed vouchers and resets flight and hotel steps to `'idle'`.
- **Expected Behavior:**
  - Upon successful booking confirmation (flight or hotel), the backend trip record should transition status (e.g. `"partially_booked"` or `"booked"`) and store authoritative references (`pnr`, `order_id`, hotel confirmation code) in trip metadata or a dedicated booking relationship table.
  - When `BookingWorkspace` loads, it must rehydrate confirmed vouchers directly from authoritative backend data (`trip.metadata.bookings` or `GET /api/trips/{id}`), displaying Stage 4 Confirmed state permanently.
- **Evidence:** [`frontend/src/components/booking/BookingWorkspace.tsx:232–241, 654–659`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/BookingWorkspace.tsx#L232-L241), [`services/flight_booking_service.py:802–816`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py#L802-L816).
- **Affected Files:** `frontend/src/components/booking/BookingWorkspace.tsx`, `services/flight_booking_service.py`, `hotel_payment_api.py`, `services/trip_store.py`.
- **Runtime Path:** Page refresh on `/trips/{trip_id}` after booking confirmation.
- **Why It Matters:** Severe UX regression — travelers believe their booking was lost after refreshing the page or returning to the site.
- **Risk:** Low risk; purely additive persistence and state rehydration.
- **Dependencies:** `TripStore.update_trip`, `EvidenceRevisionStore`.
- **Recommended Scope:** In `FlightBookingService.confirm_booking` and `hotel_payment_api.verify_and_book`, update the parent trip metadata with confirmed booking records; update `BookingWorkspace.syncFromStorage` to rehydrate confirmed states from `trip`.
- **Out of Scope:** Changing the revision approval state machine.
- **Suggested Validation:** Vitest test asserting that mounting `BookingWorkspace` with a booked trip rehydrates `flightState = 'confirmed'` and displays the confirmation banner.

---

### W3-FIND-04
- **Title:** Canonical Path A Flight Bookings Invisible in "My Trips" Dashboard
- **Priority:** P1 — High
- **Area:** Data Integrity / Triangulation
- **Current Behavior:**
  - In [`trips_api.py:86–112`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L86-L112), `list_trips` builds the dashboard list by querying:
    ```python
    canonical_trips = trip_store.list_trips(current_user.id, limit=50)
    flights = _store().list_recent(50, customer_ref=user_id_str)
    hotels = HotelTransactionStore().list_recent(50, customer_ref=user_id_str)
    ```
  - `_store()` is `TransactionStore()`, which manages the `flight_transactions` table.
  - Canonical Path A flight booking (`FlightBookingService`) writes exclusively to `booking_intents`. It never touches `flight_transactions`.
  - As a result, confirmed flights never appear in `GET /api/trips` ("My Trips" page).
- **Expected Behavior:** `list_trips` must query completed booking intents from `booking_intents` (where `status = 'COMPLETED'`) or query parent trip bookings, normalizing them into flight cards with `booking_reference`, `order_id`, and `pnr`.
- **Evidence:** [`trips_api.py:84–112`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L84-L112), [`services/flight_booking_service.py:355–384, 791–816`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py#L355-L384).
- **Affected Files:** `trips_api.py`, `services/search_session_store.py`.
- **Runtime Path:** `GET /api/trips` $\to$ `trips_api.list_trips`.
- **Why It Matters:** Users who successfully purchase flights cannot see their bookings in their account dashboard.
- **Risk:** Low regression risk; query-layer addition.
- **Dependencies:** `SearchSessionStore`, `booking_intents` schema.
- **Recommended Scope:** In `trips_api.py:list_trips`, include records from `booking_intents WHERE user_id = %s AND status = 'COMPLETED'`.
- **Out of Scope:** Removing `flight_transactions` table.
- **Suggested Validation:** Pytest test verifying that a completed booking intent created via Path A appears in `GET /api/trips` output with `record_type="flight"`.

---

### W3-FIND-05
- **Title:** Broken Post-Booking Cancellation Workflow
- **Priority:** P1 — High
- **Area:** Cancellation / API Contract
- **Current Behavior:**
  - In [`TripConfirmationView.tsx:78–85`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/TripConfirmationView.tsx#L78-L85), the cancellation modal invokes `onRequestCancellation({ booking_type, reason, contact_email })`.
  - `BookingWorkspace.tsx:768` passes `bookingService.requestCancellation(trip.trip_id, request)`.
  - `bookingService.ts:682` sends `api.cancelBookingIntent('bkg_' + tripId, ...)`.
  - Endpoint `POST /api/bookings/{id}/cancel-intent` in [`flight_booking_api.py:284–286`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_booking_api.py#L284-L286) expects `id: UUID`. Passing `'bkg_<uuid>'` fails with HTTP 422 (`Input should be a valid UUID`).
  - Furthermore, `FlightBookingLifecycleService.cancel_booking_intent` only cancels uncompleted intents (`SELECTED`, `REVALIDATING`); calling it on a `COMPLETED` booking raises `InvalidBookingStateTransitionError` (HTTP 400).
  - Meanwhile, `trips_api.py:cancellation_quote` and `cancellation_confirm` exist, but only operate on `flight_transactions` (Path B). There is no hotel cancellation endpoint anywhere.
- **Expected Behavior:** A unified cancellation inquiry/request endpoint should accept the target booking ID (UUID or booking reference), record the cancellation inquiry in a dedicated customer service ticket or transaction refund ledger, and return `{ ticket_id, status, message }`.
- **Evidence:** [`frontend/src/components/booking/TripConfirmationView.tsx:74–91`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/TripConfirmationView.tsx#L74-L91), [`frontend/src/services/bookingService.ts:678–693`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L678-L693), [`flight_booking_api.py:284–306`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_booking_api.py#L284-L306), [`trips_api.py:356–452`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L356-L452).
- **Affected Files:** `frontend/src/services/bookingService.ts`, `frontend/src/components/booking/BookingWorkspace.tsx`, `trips_api.py`, `flight_booking_api.py`.
- **Runtime Path:** User clicks "Request Cancellation" in `TripConfirmationView.tsx`.
- **Why It Matters:** Post-booking cancellation inquiries throw unhandled errors in the UI.
- **Risk:** Medium risk.
- **Dependencies:** `booking_intents`, `hotel_transactions`.
- **Recommended Scope:** Connect post-booking cancellation inquiries to a truthful customer request endpoint returning ticket confirmation without invalid UUID fabrication.
- **Out of Scope:** Automated Duffel live refund settlement (which requires airline manual review).
- **Suggested Validation:** Pytest & Vitest verifying cancellation request submission succeeds with valid ticket confirmation.

---

### W3-FIND-06
- **Title:** Synthetic Fallback Tokens & Placeholders in Production Booking Code
- **Priority:** P2 — Medium
- **Area:** Anti-Hallucination / Data Discipline
- **Current Behavior:**
  - `BookingWorkspace.tsx:430, 577`: `selectedFlight?.offer_id || selectedFlight?.id || 'fl_off_101'`
  - `BookingWorkspace.tsx:723`: `selectedHotel?.id || 'ht_off_101'`
  - `BookingWorkspace.tsx:162`: `useState<number>(860)` default verified flight amount
  - `BookingWorkspace.tsx:716–717`: `email: ... || 'guest@trusttrip.com'`, `phone: ... || '+1234567890'`
  - `bookingService.ts:320`: `amount: '860'` in legacy fallback
  - `bookingService.ts:405`: `String(acceptedPrice || 915)`
  - `bookingService.ts:482`: `room_id: offerData?.room_id || 'rm_${hotelId}_01'`
  - `services/hotel_booking_service.py:123`: `round(float(offer_dict["price"]) * 85.0, 2)` (hardcoded 85.0 exchange rate fallback on FX API failure)
- **Expected Behavior:** Per Rule 1 (Source-of-Truth Discipline) and Anti-Hallucination rules, missing offer IDs, traveler contacts, or FX rates must fail closed or disable actions rather than substituting synthetic placeholders like `'fl_off_101'`, `'guest@trusttrip.com'`, or `* 85.0`.
- **Evidence:** [`frontend/src/components/booking/BookingWorkspace.tsx:162, 430, 577, 716–717, 723`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/BookingWorkspace.tsx#L162), [`frontend/src/services/bookingService.ts:320, 405, 482`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/services/bookingService.ts#L320), [`services/hotel_booking_service.py:123`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/hotel_booking_service.py#L123).
- **Affected Files:** `frontend/src/components/booking/BookingWorkspace.tsx`, `frontend/src/services/bookingService.ts`, `services/hotel_booking_service.py`.
- **Runtime Path:** Offer verification, prebook, and hotel currency conversion.
- **Why It Matters:** Synthetic fallbacks can mask missing selections or send dummy identifiers to upstream providers.
- **Risk:** Low risk; fail-closed guards are straightforward to enforce.
- **Dependencies:** None.
- **Recommended Scope:** Replace synthetic string literals with strict validation and fail-closed errors.
- **Out of Scope:** None.
- **Suggested Validation:** Static audit assertions in `wave2TravelerIdentity.test.tsx` verifying zero occurrences of `'fl_off_101'`, `'ht_off_101'`, and `'guest@trusttrip.com'`.

---

### W3-FIND-07
- **Title:** Architectural Duplication: Three Divergent Flight Booking Subsystems
- **Priority:** P2 — Medium
- **Area:** Backend Architecture
- **Current Behavior:**
  - Three distinct flight booking implementations coexist in the backend:
    1. Canonical Path A (`flight_api.py:386–546`, `services/flight_booking_service.py`): Section 5B direct Duffel booking using `booking_intents`.
    2. Hosted Checkout Lifecycle Path (`flight_booking_api.py`, `services/flight_booking_lifecycle_service.py`): 2,065 lines of code managing external Duffel Links checkouts, `booking_attempts`, `booking_transitions`.
    3. Razorpay Flight Path B (`payment_api.py`): 914 lines of code managing Razorpay orders and Duffel fulfillment via `flight_transactions`.
  - The frontend only communicates with Path A. Paths 2 and 3 represent 2,979 lines of unwired/uncalled backend code with divergent models and duplicate `BookingStatus` enums.
- **Expected Behavior:** Path A is the frozen canonical flight booking architecture. The repository documentation and API router structure should formally designate Path A as canonical, and either deprecate or document the boundaries of Paths 2 and 3 to prevent contract confusion.
- **Evidence:** [`flight_api.py:386–546`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_api.py#L386-L546), [`flight_booking_api.py:1–339`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_booking_api.py#L1-L339), [`payment_api.py:1–914`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/payment_api.py#L1-L914), [`services/flight_booking_service.py:1–894`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py#L1-L894), [`services/flight_booking_lifecycle_service.py:1–2065`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_lifecycle_service.py#L1-L2065).
- **Affected Files:** `flight_booking_api.py`, `payment_api.py`, `PROJECT_MAP.md`, `ARCHITECTURE.md`.
- **Runtime Path:** N/A (code organization and maintenance).
- **Why It Matters:** Architectural bloat and confusion for developers tracing booking flows.
- **Risk:** Low risk; do NOT delete routes that active tests depend on without approval.
- **Dependencies:** None.
- **Recommended Scope:** Clarify router ownership in documentation; add explicit route deprecation notices to Path B.
- **Out of Scope:** Refactoring or removing `flight_booking_lifecycle_service.py` in Wave 3.
- **Suggested Validation:** Verification that all 812 pytest cases continue passing without router removal.

---

### W3-FIND-08
- **Title:** Unauthenticated Hotel Prebook Endpoint (`POST /api/hotels/prebook`)
- **Priority:** P2 — Medium
- **Area:** Security / Authorization
- **Current Behavior:** [`hotel_api.py:764–774`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/hotel_api.py#L764-L774) defines `@router.post("/prebook")` without `current_user: UserResponse = Depends(get_current_user)`. Any anonymous caller can trigger supplier room holds against the company's LiteAPI / Hotelbeds credentials.
- **Expected Behavior:** Every transactional booking operation (search, select, prebook, pay) must require authentication and tenant isolation.
- **Evidence:** [`hotel_api.py:764–774`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/hotel_api.py#L764-L774).
- **Affected Files:** `hotel_api.py`, `frontend/src/lib/apiClient.ts`.
- **Runtime Path:** `POST /api/hotels/prebook`.
- **Why It Matters:** Potential API abuse and inventory locking by unauthenticated callers.
- **Risk:** Low risk; frontend already transmits JWT Bearer tokens with all requests.
- **Dependencies:** `dependencies/auth.py`.
- **Recommended Scope:** Add `current_user: UserResponse = Depends(get_current_user)` to `prebook_hotel`.
- **Out of Scope:** None.
- **Suggested Validation:** Pytest test verifying anonymous call returns 401 Unauthorized.

---

### W3-FIND-09
- **Title:** Flight E-Ticket & Hotel Voucher PDF Delivery Gaps in UI
- **Priority:** P2 — Medium
- **Area:** Functional / Document Delivery
- **Current Behavior:**
  - In [`TripConfirmationView.tsx:126–146`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/TripConfirmationView.tsx#L126-L146), only one download button exists: "Download travel plan" (`POST /api/travel/pdf`), which generates an itinerary summary.
  - The flight ticket PDF service (`services/ticket_service.py`) and hotel voucher PDF service (`services/hotel_booking_pdf.py`) exist on the backend ([`trips_api.py:232, 267`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L232)), but:
    1. Flight ticket PDF (`/api/trips/{tx_id}/ticket.pdf`) only looks up `flight_transactions` (Path B), not `booking_intents` (Path A).
    2. The frontend has zero buttons or methods to download flight tickets or hotel vouchers.
- **Expected Behavior:** Users should have dedicated "Download Flight Ticket" and "Download Hotel Voucher" buttons in `TripConfirmationView` and on their trip dashboard, retrieving official PDFs generated from authoritative confirmation records.
- **Evidence:** [`frontend/src/components/booking/TripConfirmationView.tsx:126–146`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/components/booking/TripConfirmationView.tsx#L126-L146), [`trips_api.py:231–292`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L231-L292).
- **Affected Files:** `frontend/src/components/booking/TripConfirmationView.tsx`, `trips_api.py`, `frontend/src/lib/apiClient.ts`.
- **Runtime Path:** User views confirmation screen and requests ticket PDF.
- **Why It Matters:** Travelers need verifiable e-tickets and hotel check-in vouchers.
- **Risk:** Low risk; ReportLab generators are already built and tested.
- **Dependencies:** `ReportLab`, `TicketService`, `HotelBookingPdf`.
- **Recommended Scope:** Add ticket download endpoints supporting `booking_intents` and add download buttons to `TripConfirmationView.tsx`.
- **Out of Scope:** Redesigning PDF styling.
- **Suggested Validation:** Unit tests asserting PDF bytes returned for completed Path A flight and hotel bookings.

---

### W3-FIND-10
- **Title:** Top-Level Duffel PNR & E-Ticket Extraction in Path A Confirmation
- **Priority:** P3 — Low
- **Area:** Provider Integration / Data Normalization
- **Current Behavior:** In [`services/flight_booking_service.py:794–799`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py#L794-L799), `confirm_booking` queries `pnr_val = booking_res.get("pnr")` and `eticket_val = booking_res.get("e_ticket")`. In the Duffel REST API, `booking_reference` is the airline PNR, and electronic tickets are returned inside `documents` (e.g. `documents[0]["unique_identifier"]`). Consequently, `pnr` and `e_ticket` evaluate to `None`.
- **Expected Behavior:** When Duffel returns an order, `pnr` should be populated from `booking_reference`, and `e_ticket` should be extracted from the first document of type `electronic_ticket`.
- **Evidence:** [`services/flight_booking_service.py:794–799`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/flight_booking_service.py#L794-L799), [`services/duffel_provider.py:498–510`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/services/duffel_provider.py#L498-L510).
- **Affected Files:** `services/flight_booking_service.py`.
- **Runtime Path:** `FlightBookingService.confirm_booking`.
- **Why It Matters:** Ensures `pnr` and `e_ticket` fields on confirmation responses are populated faithfully from Duffel data.
- **Risk:** Very low risk.
- **Dependencies:** `DuffelProvider`.
- **Recommended Scope:** Map `booking_reference` to `pnr` and extract `documents` ticket numbers if present.
- **Out of Scope:** None.
- **Suggested Validation:** Pytest asserting response includes `pnr` matching `booking_reference`.

---

## 6. Test Coverage Gaps

| Area | Current Test Coverage | Identified Test Gap |
|---|---|---|
| **Hotel Prebook** | Mocks `global.fetch` in frontend; 0 pytest cases hitting `POST /api/hotels/prebook` | No integration test verifies `hotel_api.py:prebook_hotel` against real frontend payload format. |
| **Hotel Razorpay Payment** | Tests verify signature verification rejection on fake inputs, but mock `fetch` in UI | No test exercises real Razorpay checkout event handling in frontend components. |
| **Workspace Rehydration** | Hook order & initial load tests pass | No test asserts that a completed booking preserves voucher display upon component remount / refresh. |
| **Dashboard Booking Display** | `test_phase2_auth_ownership.py` tests `GET /api/trips` with `flight_transactions` | No test asserts that a Path A completed booking from `booking_intents` appears in `GET /api/trips`. |
| **Post-Booking Cancellation** | `test_phase9_booking_lifecycle.py` tests intent cancellation on uncompleted intents | No test exercises post-booking cancellation request from `TripConfirmationView` with real backend client. |
| **Flight Ticket PDF Delivery** | `test_planner_and_payments.py` tests PDF generator function | No test tests downloading a flight ticket PDF from a Path A booking ID. |

---

## 7. Dead / Legacy / Duplicate Paths

| Path / Symbol | File & Lines | Classification | Rationale / Evidence |
|---|---|---|---|
| `POST /api/payments/create-order` | [`payment_api.py:388–548`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/payment_api.py#L388-L548) | **DEAD / UNWIRED PATH** | Flight Razorpay Path B has zero frontend call sites, zero client methods, and is not exposed in `BookingWorkspace.tsx`. |
| `POST /api/payments/verify-and-book` | [`payment_api.py:778–890`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/payment_api.py#L778-L890) | **DEAD / UNWIRED PATH** | Unwired companion to create-order. |
| `POST /api/travel/approve` | [`app.py:454–494`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/app.py#L454-L494) | **LEGACY PATH** | Legacy LangGraph conversational approval invoked only by old Jinja2 template (`static/script.js:392`). Not used by React frontend. |
| `POST /api/flights/book` | [`flight_api.py:326–351`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_api.py#L326-L351) | **LEGACY PATH** | Early prototype endpoint without Section 5B state machine or Section 9 eligibility. |
| `POST /api/flights/pay` | [`flight_api.py:358–380`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_api.py#L358-L380) | **LEGACY PATH** | Prototype endpoint for paying hold orders with sandbox balance directly. |
| `POST /api/flights/select-booking` | [`flight_api.py:63–104`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/flight_api.py#L63-L104) | **LEGACY PATH** | Legacy single-step handoff destination endpoint. |
| `handleMockFallback` for booking | [`apiClient.ts:727–743`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/frontend/src/lib/apiClient.ts#L727-L743) | **DEAD / TEST-ONLY PATH** | Synthesizes mock booking intents in demo mode (`fl_snap_demo`, `860`). |
| `build_booking_pdf` import | [`trips_api.py:26`](file:///n:/Documents/GitHub/TrustTrip_TravelPlanner/trips_api.py#L26) | **DEAD CODE** | Unused import in `trips_api.py` header (though used on line 283). |

---

## 8. Security / Authorization Findings

1. **Unauthenticated Hotel Prebook (`hotel_api.py:764`):** Endpoint allows anyone to initiate room reservations against LiteAPI/Hotelbeds credentials. (Finding [`W3-FIND-08`](#w3-find-08)).
2. **Tenant Isolation on Completed Bookings:** `FlightBookingService._get_intent` correctly enforces `record["user_id"] == uid_str` and raises `BookingPermissionError` (403) on cross-tenant access. Verified secure.
3. **Section 9 Authorization Gates:** Both `FlightBookingService.select_offer`, `hotel_payment_api.create_order`, and `payment_api.create_payment_order` enforce Section 9 eligibility through `AuthorizationStore.assert_booking_eligible`. Verified secure.
4. **Idempotency Keys:** `booking_intents` and `hotel_transactions` enforce unique idempotency keys, preventing duplicate charge or booking execution during parallel clicks. Verified secure.

---

## 9. Data Integrity Findings

1. **Trip Status Disconnect:** Confirming a flight or hotel booking does not transition `trips.status` or record booking IDs in the canonical trip row, causing the booking to be orphaned from trip state upon client navigation. (Finding [`W3-FIND-03`](#w3-find-03)).
2. **Dashboard Query Inconsistency:** `trips_api.py:list_trips` queries `flight_transactions` instead of `booking_intents`, hiding completed Path A flights from "My Trips". (Finding [`W3-FIND-04`](#w3-find-04)).
3. **Hardcoded Fallbacks:** Exchange rate fallback (`* 85.0`) in `hotel_booking_service.py:123` violates financial integrity. If Frankfurter FX API is down, bookings could be priced incorrectly. (Finding [`W3-FIND-06`](#w3-find-06)).

---

## 10. Provider Integration Findings

1. **Duffel API (Flights):**
   - Seamless integration in Path A via `DuffelProvider.book(raw_provider_offer_id, passengers, payment_mode="auto")`.
   - Age derivation (`_passenger_type_for_dob`) correctly maps DOB against departure date.
   - Passenger count matching strictly enforced.
2. **LiteAPI (Hotels):**
   - Rates search and booking payloads are structurally sound.
   - However, prebook endpoint contract mismatch prevents the frontend from successfully passing room holds to the backend.
3. **Razorpay (Payments):**
   - Backend signature verification (`RazorpayService.verify_checkout_signature`) is strictly implemented using HMAC-SHA256.
   - However, the frontend does not open the Razorpay SDK modal and sends synthetic signatures, causing signature validation to fail.

---

## 11. Recommended Wave 3 Scope

### Title: **End-to-End Booking Lifecycle Truthfulness, Hotel Checkout Realization & State Persistence**

The recommended Wave 3 focuses surgically on **closing the runtime loop between the frontend booking workspace, hotel provider checkout, and persistent trip lifecycle state**, while strictly protecting the frozen Wave 1 and Wave 2 baseline.

### Included in Recommended Scope:
1. **Hotel Prebook Contract Resolution ([`W3-FIND-01`](#w3-find-01), [`W3-FIND-08`](#w3-find-08)):**
   - Align `POST /api/hotels/prebook` request and response models with the frontend client.
   - Require authentication (`get_current_user`) and validate trip ownership.
   - Return `{ success: true, prebook_id, amount, currency, status }` without undefined values.
2. **Hotel Razorpay Checkout Integration ([`W3-FIND-02`](#w3-find-02)):**
   - Integrate standard Razorpay Checkout modal script in `PaymentReviewModal.tsx` for real/test environments.
   - Collect and transmit genuine `razorpay_payment_id` and `razorpay_signature` to `/verify-and-book`.
   - Support safe, server-controlled sandbox checkout mode when running without live Razorpay keys.
3. **Authoritative Booking State Persistence & Refresh Rehydration ([`W3-FIND-03`](#w3-find-03)):**
   - Upon flight or hotel confirmation, update the parent trip metadata with confirmed booking references (`pnr`, `order_id`, hotel confirmation code) and advance `trip.status = 'booked'`.
   - Update `BookingWorkspace.syncFromStorage` to rehydrate confirmed vouchers from `trip` upon page refresh.
4. **Unified "My Trips" Dashboard Integration ([`W3-FIND-04`](#w3-find-04)):**
   - Update `trips_api.py:list_trips` to include completed bookings from `booking_intents` (Path A), ensuring all flight bookings appear on the user's dashboard.
5. **Elimination of Synthetic Placeholders ([`W3-FIND-06`](#w3-find-06)):**
   - Remove `'fl_off_101'`, `'ht_off_101'`, `'guest@trusttrip.com'`, `860`, and `* 85.0` fallbacks, enforcing fail-closed discipline.
6. **E-Ticket and Hotel Voucher Document Delivery ([`W3-FIND-09`](#w3-find-09), [`W3-FIND-10`](#w3-find-10)):**
   - Provide working download buttons in `TripConfirmationView` for flight e-tickets (built from `booking_intents`) and hotel vouchers.

---

## 12. Alternatives

### Alternative 1: Flight Subsystem Consolidation & Path B Deprecation
- **Scope:** Formally merge and deprecate Path B (`payment_api.py`) and the hosted checkout prototype (`flight_booking_lifecycle_service.py`), refactoring all flight operations into a single hardened `FlightBookingService`.
- **Tradeoff:** High architectural cleanliness, but does NOT solve the broken hotel checkout flow or the page refresh rehydration issue. Higher regression risk to existing test suites.
- **Rank:** Alternative 1 (Secondary).

### Alternative 2: Self-Service Post-Booking Servicing & Cancellation Engine
- **Scope:** Implement end-to-end flight and hotel cancellation workflows, including automated refund estimation, Duffel order cancellation, and Razorpay refund execution.
- **Tradeoff:** High post-booking value, but leaves the primary prebook and payment purchase flows broken.
- **Rank:** Alternative 2 (Tertiary).

---

## 13. Explicitly Out of Scope

The following items are **strictly excluded** from Wave 3:
- Modifying Wave 1 Revision Architecture (`services/trip_store.py:create_revision_atomic`).
- Modifying Wave 1 Approval Flow (`trips_api.py:approve_trip`).
- Modifying Wave 1 Section 9 Eligibility Rules (`services/authorization_store.py:assert_booking_eligible`).
- Modifying Wave 1 Gateway Airport Resolver (`services/travel_planner.py:_resolve_place`).
- Modifying Wave 2 Traveler Data Model (`domain/flight_booking.py:FlightPassenger`).
- Modifying Wave 2 Passenger Count Validation (`len(passengers) == trip.travelers_count`).
- Introducing new databases, Redis, Kafka, Celery, or background worker infrastructure.
- Automated international KYC / passport document collection.
- Rewriting or removing `flight_booking_lifecycle_service.py` (remains isolated).

---

## 14. Dependencies

- **Database:** Uses existing `trips`, `plan_revisions`, `booking_intents`, and `hotel_transactions` tables. Zero schema migrations required.
- **Providers:** Duffel API (Path A), LiteAPI (Hotels), Razorpay API (Checkout).
- **Libraries:** ReportLab (PDFs), Razorpay Python SDK, standard React hooks.

---

## 15. Proposed Definition of Done

The following measurable, objectively testable acceptance criteria are proposed for Wave 3:

```text
- [ ] Hotel Prebook Contract: POST /api/hotels/prebook accepts standard frontend payload, validates authentication, and returns 200 with { success: true, prebook_id, amount, currency, status }.
- [ ] Hotel Prebook Validation: Anonymous calls to /api/hotels/prebook return 401 Unauthorized.
- [ ] Hotel Payment Order: POST /api/hotel-payments/create-order receives valid prebook and amount, creating a real Razorpay order ID.
- [ ] Hotel Payment Verification: POST /api/hotel-payments/verify-and-book validates genuine payment signature and transitions hotel transaction to "booked".
- [ ] Trip Status Update: Confirming a flight or hotel booking transitions trip status and records booking references in trip metadata.
- [ ] Refresh Rehydration: Refreshing the browser on /trips/{id} after confirmation preserves and displays the confirmed flight/hotel voucher without resetting to 'idle'.
- [ ] Unified Dashboard: GET /api/trips returns completed flight bookings from booking_intents and completed hotel bookings from hotel_transactions.
- [ ] Document Delivery: Dedicated download buttons for Flight E-Ticket and Hotel Voucher in TripConfirmationView return valid PDF bytes.
- [ ] Zero Synthetic Placeholders: Static audit verifies zero occurrences of 'fl_off_101', 'ht_off_101', 'guest@trusttrip.com', and hardcoded '85.0' FX rates.
- [ ] Wave 1 Regression: All 96 existing Wave 1 tests remain 100% green.
- [ ] Wave 2 Regression: All 16 existing Wave 2 tests remain 100% green.
- [ ] Full Pytest Suite: 0 failed tests across the entire backend suite.
- [ ] Full Frontend Test Suite: 100% test suites pass in npm test.
- [ ] Type Check: tsc --noEmit exits with code 0.
```

---

## 16. Regression Protection

The following files and contracts are explicitly protected and must NOT be modified during Wave 3 implementation without explicit architectural review:
- `services/trip_store.py:create_revision_atomic` (Wave 1)
- `services/evidence_store.py` (Wave 1)
- `services/authorization_store.py:assert_booking_eligible` (Wave 1)
- `services/travel_planner.py:_resolve_place` (Wave 1)
- `services/flight_booking_service.py:select_offer` passenger count validation (Wave 2)
- `frontend/src/components/booking/TravelerModal.tsx` (Wave 2)
- `frontend/src/types/flight.ts:FlightPassenger` (Wave 2)

---

## 17. Git State

### Working Tree Status
```text
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
	modified:   app.py
	modified:   frontend/package.json
	modified:   frontend/src/components/booking/BookingReviewPanel.tsx
	modified:   frontend/src/components/booking/BookingWorkspace.tsx
	modified:   frontend/src/components/booking/TripConfirmationView.tsx
	modified:   frontend/src/components/travel/FlightCard.tsx
	modified:   frontend/src/components/views/HotelsView.tsx
	modified:   frontend/src/components/views/TripDetailView.tsx
	modified:   frontend/src/lib/apiClient.ts
	modified:   frontend/src/lib/mockTravelData.ts
	modified:   frontend/src/mock/bookingMockData.ts
	modified:   frontend/src/services/bookingService.ts
	modified:   frontend/src/types/bookingFrontend.ts
	modified:   frontend/src/types/flight.ts
	modified:   frontend/src/types/trip.ts
	modified:   hotel_api.py
	modified:   payment_api.py
	modified:   services/chat_planner.py
	modified:   services/flight_booking_service.py
	modified:   services/planner_service.py
	modified:   services/travel_planner.py
	modified:   services/trip_store.py
	modified:   tests/test_section10_payment_idempotency_concurrency.py
	modified:   tests/test_section6_hotel_search.py
	modified:   tests/test_section8_hitl_revisions.py

Untracked files:
	.agents/
	API_CONTRACTS.md
	ARCHITECTURE.md
	CURRENT_STATUS.md
	POST_WAVE1_AUDIT_REPORT.md
	PROJECT_MAP.md
	WAVE2_IMPLEMENTATION_REPORT.md
	WAVE2_POST_IMPLEMENTATION_AUDIT.md
	WAVE2_PREIMPLEMENTATION_AUDIT.md
	WAVE2_REMEDIATION_REPORT.md
	all_pytest_out.txt
	docker-compose.yml
	frontend/src/__tests__/flightCurrencyDisplay.test.tsx
	frontend/src/__tests__/selectTripOfferNormalization.test.tsx
	frontend/src/__tests__/tripConstraintPersistence.test.tsx
	frontend/src/__tests__/tripDetailHookOrder.test.tsx
	frontend/src/__tests__/wave2TravelerIdentity.test.tsx
	frontend/src/components/booking/TravelerModal.tsx
	migrations/012_hotel_snapshot_long_provider_offer_id.sql
	pytest.ini
	pytest_out.txt
	tests/test_destination_resolver_safeties.py
	tests/test_flight_recheck_contract.py
	tests/test_hotel_snapshot_long_id.py
	tests/test_travelplan_date_canonical_consistency.py
	tests/test_wave1_canonical_plan_revisions.py
	tests/test_wave2_traveler_identity.py
	WAVE3_PREIMPLEMENTATION_AUDIT.md
```

**Audit Note on Git Status:** All existing modified and untracked files are from the frozen Wave 1 and Wave 2 baseline implementations and reports. This investigation turn modified **zero** production code files, `.tsx`, `.ts`, `.py`, SQL, or configuration files. The only file created during this investigation is `WAVE3_PREIMPLEMENTATION_AUDIT.md`.

---

## 18. Final Recommendation

The Wave 3 investigation is complete. The findings demonstrate that while Wave 1 (Canonical Revisions & Airport Identity) and Wave 2 (Real Traveler Identity & Propagation) are fully solid and green, the subsequent checkout and post-booking stages suffer from critical contract mismatches, simulated payment signatures, and refresh rehydration bugs that prevent the travel planner from functioning as a truthful end-to-end commercial system.

Remediating these exact audited issues within the recommended scope will deliver a complete, reliable, and truthful end-to-end travel booking platform without disrupting the frozen Wave 1 and Wave 2 contracts.

```text
WAVE 3 — INVESTIGATION COMPLETE, READY FOR SCOPE APPROVAL
```
