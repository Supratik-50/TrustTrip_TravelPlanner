# TrustTrip — Project Map & Subsystem Architecture

**Date:** 2026-10-04  
**Scope:** Authoritative map of architecture, file ownership, database entities, and end-to-end business flows.

---

## PART 1 — System Architecture

```
[ FRONTEND (React 18 + Vite + TypeScript) ]
    │
    │  HTTP / JSON via apiClient.ts & bookingService.ts
    ▼
[ FASTAPI ROUTERS (API Layer) ]
    ├── auth_api.py             (/api/auth)
    ├── trips_api.py            (/api/trips)
    ├── app.py                  (/api/travel, /static, /)
    ├── flight_api.py           (/api/flights)
    ├── flight_booking_api.py   (/api/bookings)
    ├── hotel_api.py            (/api/hotels)
    ├── hotel_payment_api.py    (/api/hotel-payments)
    ├── payment_api.py          (/api/payments)
    ├── offers_api.py           (/api/offers)
    └── research_api.py         (/api/research)
    │
    ▼
[ SERVICE LAYER (Business & Intelligence Logic) ]
    ├── planner_service.py               (Plan generation & constraints orchestration)
    ├── chat_planner.py                  (LLM / Prompt generation & Gemini client)
    ├── travel_planner.py                (Place resolution & catalog dictionary)
    ├── flight_service.py                (Flight search/recheck orchestration)
    ├── flight_search_service.py         (Flight search criteria resolver & live queries)
    ├── flight_booking_service.py        (Section 5B/5C booking intent lifecycle)
    ├── flight_booking_lifecycle_service.py (State machine & idempotency)
    ├── hotel_booking_service.py         (Hotel pre-book & booking verification)
    ├── razorpay_service.py              (Razorpay orders & HMAC signature verification)
    ├── currency_converter.py            (Exchange rates & multi-currency conversion)
    ├── auth_service.py                  (JWT token creation & password hashing)
    └── ticket_service.py                (Flight / Hotel PDF receipt generation)
    │
    ▼
[ STORES & PERSISTENCE LAYER (Data Access & Concurrency Locks) ]
    ├── trip_store.py                    (TripStore: trips, threads, row-locked revisions)
    ├── evidence_store.py                (EvidenceRevisionStore: plan_revisions, approvals, rejections)
    ├── authorization_store.py           (AuthorizationStore: booking_authorizations & 10 eligibility guards)
    ├── search_session_store.py          (SearchSessionStore: search_sessions, offer_snapshots, booking_intents)
    ├── transaction_store.py             (TransactionStore: flight_transactions)
    ├── hotel_transaction_store.py       (HotelTransactionStore: hotel_transactions)
    └── auth_store.py                    (UserStore: users)
    │
    ▼
[ DATABASE & EXTERNAL PROVIDERS ]
    ├── PostgreSQL 15+                   (12 SQL migrations: trips, revisions, transactions, snapshots)
    ├── Duffel API                       (Airlines, offer search, flight booking orders)
    ├── LiteAPI / Hotel Mock             (Hotel inventory, rates, prebook & reservations)
    ├── Razorpay API                     (Payment gateway orders & payment capture)
    └── Google Gemini 2.5 Flash / Pro    (AI trip planner & intelligence synthesis)
```

---

## PART 2 — File Ownership & Contracts

### 1. API Boundary Layer

| File | Primary Responsibility | Inputs | Outputs | Authoritative Data | Dependencies |
|---|---|---|---|---|---|
| `app.py` | Root app setup, lifespan, middleware, `/api/travel` plan generation orchestration | HTTP requests, `TravelRequest` | App lifecycle, plan JSON, Revision #1 response | Coordinates all routers | FastAPI, all routers, `PlannerService`, `TripStore` |
| `auth_api.py` | User registration, login, profile query | `UserCreate`, `UserLogin` | JWT access token, `UserResponse` | `users` table | `auth_store.py`, `auth_service.py` |
| `trips_api.py` | Trip CRUD, revision creation, revision approval, revision rejection, Section 9 booking-approval | Trip requests, revision bodies, approval IDs | Trip summaries, revision envelopes, eligibility states | `trips`, `plan_revisions`, `booking_authorizations` | `trip_store.py`, `evidence_store.py`, `authorization_store.py` |
| `flight_api.py` | Flight search, flexible search, offer selection, revalidation, booking confirm (Section 5B) | Search criteria, offer IDs, passenger lists | Flight offers, snapshots, booking intents, PNR | `offer_snapshots`, `booking_intents` | `flight_service.py`, `flight_booking_service.py`, `duffel_provider.py` |
| `flight_booking_api.py` | Flight booking lifecycle API (prepare, revalidate, handoff, cancel) | Prepare requests, revalidation requests | `BookingIntent`, `OfferRevalidationResult` | `booking_intents` | `flight_booking_lifecycle_service.py` |
| `hotel_api.py` | Hotel destination search, offers, hotel prebooking | City names, check-in/out dates, guest counts | Hotel list, hotel offers, `prebook_id` | Live provider / memory cache | `LiteAPI`, `hotel_booking_service.py` |
| `hotel_payment_api.py` | Hotel Razorpay order creation, payment signature verification, supplier booking | Prebook data, guest info, payment IDs | Razorpay order ID, `booking_reference`, confirmation | `hotel_transactions` | `hotel_booking_service.py`, `razorpay_service.py`, `hotel_transaction_store.py` |
| `payment_api.py` | Flight Razorpay order creation, signature verification, Duffel order fulfillment | Offer ID, passenger info, Razorpay payment ID | Razorpay order ID, Duffel PNR, confirmation | `flight_transactions` | `flight_service.py`, `razorpay_service.py`, `transaction_store.py`, `currency_converter.py` |
| `offers_api.py` | Unified offer selection across services | `TripOfferSelection` | Updated `TripDetail` | `trips.metadata` | `trip_store.py` |
| `research_api.py` | Destination research, fact extraction, citations | Queries, trip IDs | `ResearchResult` | `research_sessions`, `research_facts` | `evidence_store.py` |

### 2. Service Layer

| File | Primary Responsibility | Inputs | Outputs | Authoritative Data | Dependencies |
|---|---|---|---|---|---|
| `services/planner_service.py` | Synthesizes trip parameters and constraints into prompt context | Trip record, user query | Canonical travel plan | Trip metadata constraints | `trip_store.py`, `chat_planner.py` |
| `services/travel_planner.py` | Natural language destination resolution, gateway airport mapping, budget calculation | Place strings (e.g. "Darjeeling, India") | `(IATA, city, country)` tuple | `CITY_TO_IATA`, `REGION_TO_IATA`, `INDIAN_AIRPORTS` | Standard library / difflib |
| `services/flight_search_service.py` | Resolves canonical criteria from trip metadata and queries live providers | Trip dict, optional filters | Flight offers list, resolved criteria | Trip metadata | `flight_service.py`, `travel_planner.py` |
| `services/flight_booking_service.py` | Manages flight booking lifecycle (select -> revalidate -> approve -> confirm) | Booking intent ID, user ID | Booking status, PNR, supplier confirmation | `booking_intents` | `search_session_store.py`, `duffel_provider.py` |
| `services/hotel_booking_service.py` | Pre-books rooms and executes supplier reservations | Hotel ID, room ID, guest details | `prebook_id`, supplier confirmation | `hotel_transactions` | `hotel_transaction_store.py` |
| `services/razorpay_service.py` | Razorpay order creation and HMAC-SHA256 signature verification | Amount (in paise/cents), currency, signature | Razorpay order, boolean validity | Razorpay API | `hmac`, `hashlib`, `requests` |
| `services/currency_converter.py` | Currency exchange rates & conversions (e.g. USD/GBP to INR) | Amount, from_currency, to_currency | Target amount, rate, rate date | Exchange rate table / API | Standard math / config |

### 3. Persistence & Data Access Layer

| File | Primary Responsibility | Key Tables Managed | Concurrency Mechanism |
|---|---|---|---|
| `services/trip_store.py` | Trip records, constraints, revision sequence | `trips`, `threads` | Row-level locking (`SELECT ... FOR UPDATE` / threading lock) |
| `services/evidence_store.py` | Revisions, approvals, rejections | `plan_revisions`, `plan_approvals`, `plan_revision_rejections` | Trip row-locked atomic status synchronization |
| `services/authorization_store.py` | Section 9 booking authorizations & 10 guard conditions | `booking_authorizations` | Exact revision match, monotonic checks, snapshot validation |
| `services/search_session_store.py` | Search sessions, flight offer snapshots, booking intents | `search_sessions`, `offer_snapshots`, `booking_intents` | Session isolation, tenant matching |
| `services/transaction_store.py` | Flight payment records & Duffel order mappings | `flight_transactions` | Unique transaction ID, status transitions |
| `services/hotel_transaction_store.py` | Hotel payment records & provider booking metadata | `hotel_transactions` | Idempotency key, transaction status |
| `services/auth_store.py` | User accounts, credentials | `users` | Unique email constraint |

### 4. Frontend Layer

| File | Primary Responsibility | Inputs | Outputs |
|---|---|---|---|
| `frontend/src/lib/apiClient.ts` | Authoritative HTTP client, envelope normalization, zero-fabrication guards | Backend API responses | Typed entities (`TripDetail`, `TripRevision`, `FlightOffer`, etc.) |
| `frontend/src/services/bookingService.ts` | State machine for flight & hotel booking workflows | User actions, offer IDs, passenger forms | Booking verification state, prebook data, confirmations |
| `frontend/src/components/booking/BookingWorkspace.tsx` | Main 2-step booking interface (Review -> Section 9 Auth -> Service 1 Flight -> Service 2 Hotel) | `TripDetail`, selected offers | Interactive booking, payments, confirmations |
| `frontend/src/components/views/TripDetailView.tsx` | Canonical trip hub: itinerary display, constraints edit, flight/hotel tabs | Route trip ID | Full trip view, modal dialogs, tab routing |

---

## PART 3 — Business Flow Graphs

### Flow 1: Trip Planning & Wave 1 Plan Generation
```
User clicks "Create Trip"
   │
   ▼
POST /api/trips (origin, destination, dates, budget)
   │  Writes trip row with status="planning", current_revision_number=0
   ▼
Trip Detail UI loaded
   │
   ▼
User requests plan: POST /api/travel (trip_id, message)
   │
   ├── PlannerService runs LLM prompt with trip constraints
   ├── travel_planner._resolve_place extracts gateway airports:
   │     e.g., "Darjeeling, India" -> "IXB", "Kolkata, India" -> "CCU"
   ├── app.py persists canonical airports:
   │     trip.metadata["destination_airport"] = "IXB"
   │     trip.metadata["origin_airport"] = "CCU"
   └── app.py calls ts.create_revision_atomic():
         - Creates Revision #1 in plan_revisions
         - Sets status = PROPOSED (is_approved = False)
         - Locks trip row and sets current_revision_id = new_uuid
         - Advances current_revision_number = 1
   │
   ▼
Frontend receives response with revision_id, revision_number=1, revision_status="PROPOSED"
```

### Flow 2: Human-in-the-Loop (HITL) Revision Lifecycle
```
Revision #1 (PROPOSED)
   │
   ├── Path A: User Approves
   │     │
   │     ▼
   │   POST /api/trips/{trip_id}/approve (revision_id, expected_revision_number=1)
   │     ├── Validates revision exists in EvidenceRevisionStore
   │     ├── Records approval row in plan_approvals
   │     └── Updates trip status = "approved"
   │     │
   │     ▼
   │   Revision #1 (APPROVED) ──► Eligible for Section 9 Booking Authorization
   │
   └── Path B: User Modifies Constraints or Re-plans
         │
         ▼
       POST /api/trips/{trip_id}/revisions or POST /api/travel (re-plan)
         ├── Validates expected base revision
         ├── Evaluates structured diff
         ├── Advances revision atomically: Revision #2 (PROPOSED)
         └── Demotes trip status to "planning" / "awaiting_approval"
```

### Flow 3: Section 9 Booking Authorization
```
Trip (Status="approved") + Active Revision (APPROVED)
   │
   ▼
User enters Booking Workspace
   │
   ▼
POST /api/trips/{trip_id}/booking-approval (revision_id, expected_revision_number)
   │
   ├── AuthorizationStore.assert_booking_eligible evaluates 10 strict guards:
   │     1. Trip exists and belongs to user
   │     2. Revision exists in EvidenceRevisionStore
   │     3. Revision is approved (not rejected)
   │     4. Expected revision number matches current authoritative revision
   │     5. Canonical dates present and departure >= today
   │     6. Origin and destination valid and non-identical
   │     7. Passengers / travelers count present and positive
   │     8. Selected offers (if present) belong to trip and user
   │     9. Revision snapshot matches canonical trip state
   │     10. No conflicting active rejections
   │
   ▼
Inserts record into booking_authorizations table
Returns is_authorized=True, authorization_id, authorized_at
```

### Flow 4: Flight Search, Selection & Confirmation
```
Trip with metadata["destination_airport"] = "IXB"
   │
   ▼
POST /api/flights/search (trip_id)
   │  resolve_canonical_trip_criteria reads meta["destination_airport"]
   │  Returns live Duffel flight offers
   ▼
User selects offer:
   │
   ▼
POST /api/trips/{trip_id}/select-offer (type="flight", offer_id, snapshot_id)
   │  Stores selected_flight_snapshot_id in trip metadata
   ▼
Booking Workspace: Service 1 Flight Verification:
   │
   ├── Revalidation: POST /api/flights/booking/{booking_id}/revalidate
   │     Verifies live fare and availability with Duffel
   ├── HITL Approval: POST /api/flights/booking/{booking_id}/approve
   │     Locks fare and marks booking intent approved
   └── Confirmation: POST /api/flights/booking/{booking_id}/confirm
         ├── Immediate pre-flight recheck
         ├── Provider booking call (Duffel API)
         └── Stores PNR, order_id, e_ticket in booking intent metadata
```

### Flow 5: Hotel Search, Pre-book & Razorpay Payment
```
User searches hotels: POST /api/hotels/search (city, checkin, checkout, guests)
   │
   ▼
User selects hotel:
   │
   ├── POST /api/trips/{trip_id}/select-offer (type="hotel", offer_id)
   └── Pre-book: POST /api/hotels/prebook (hotel_id, dates, guests)
         Returns prebook_id, locked amount, currency
   │
   ▼
Payment Order: POST /api/hotel-payments/create-order
   │  Validates Section 9 authorization for current revision
   │  Creates Razorpay Order (amount in paise)
   │  Writes record to hotel_transactions (status="created")
   │
   ▼
Razorpay Checkout (Simulated/Live):
   │  Collects payment, generates razorpay_payment_id and signature
   │
   ▼
Payment Verification & Booking: POST /api/hotel-payments/verify-and-book
   │  Verifies HMAC-SHA256 signature
   │  Captures Razorpay payment
   │  Executes supplier hotel booking
   └── Updates hotel_transactions (status="confirmed", booking_reference)
```

### Flow 6: Canonical Airport Identity Pipeline
```
Natural Language Input: "Darjeeling, India"
   │
   ▼
travel_planner._resolve_place("Darjeeling, India")
   ├── Normalize text -> "darjeeling"
   ├── Lookup CITY_TO_IATA["darjeeling"] -> "IXB" (Bagdogra gateway)
   ├── Validate "IXB" in INDIAN_AIRPORTS -> True
   └── Returns ("IXB", "Bagdogra", "India")
   │
   ▼
Persistence in app.py:
   ├── trip.destination = "Darjeeling, India"  (Human-readable UI preserved)
   ├── trip.metadata["destination_airport"] = "IXB"  (Canonical IATA)
   └── Revision snapshot constraints["destination_airport"] = "IXB"
   │
   ▼
Flight Search Resolver (resolve_canonical_trip_criteria):
   ├── Checks constraints["destination_airport"] -> "IXB"
   ├── Checks meta["destination_airport"] -> "IXB"
   └── Resolves destination_iata = "IXB" without 400 error
```
