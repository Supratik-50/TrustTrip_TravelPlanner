# TrustTrip — Wave 2 Pre-Implementation Audit
## Passenger Identity + Flight Booking Path

---

## 1. Executive Summary

This pre-implementation audit investigates the authoritative source of passenger/traveler identity and the canonical flight booking path in TrustTrip. The audit was conducted strictly in read-only mode with **0 production code modifications**.

### Key Findings
1. **Authoritative Source of Passenger Identity: CURRENTLY NON-EXISTENT IN PRODUCTION.**
   - The authenticated user entity (`User` / `UserResponse`) contains **only** `id`, `email`, `is_active`, and `created_at`. No passenger profile fields (`first_name`, `last_name`, `born_on`, `gender`, `phone_number`, `passport`) exist in the database or user schema.
   - The frontend contains **zero** traveler input forms, modals, or state setters.
   - In production execution, passenger data is constructed from hardcoded constants (`"Ananya Roy"`, `"Traveler User"`, `"1990-01-01"`, `"traveler@trusttrip.com"`).
2. **Canonical Flight Booking Path: PATH A (SECTION 5B `/api/flights/booking/*`) IS CANONICAL.**
   - The frontend UI exclusively calls Path A (`POST /api/flights/booking/{booking_id}/confirm`).
   - Path B (`POST /api/payments/create-order` in `payment_api.py`) has **zero frontend call sites**, zero methods in `apiClient.ts`, and zero components in `frontend/src/`. Path B is an unwired backend prototype.
   - For hotels, Section 10 (`/api/hotel-payments/*`) is the active payment path; for flights, Section 5B is the sole end-to-end active path.

---

## 2. Current Passenger Identity Architecture

### High-Level Topology

```
+-------------------------------------------------------------------------+
| Authenticated User (PostgreSQL 'users' table)                           |
| Fields: id, email, password_hash, is_active, created_at, updated_at     |
| [NO name, NO DOB, NO phone, NO gender, NO passport]                     |
+-------------------------------------------------------------------------+
                                    |
                                    v (Only user.email available)
+-------------------------------------------------------------------------+
| Frontend Booking Workspace (BookingWorkspace.tsx)                       |
| State: contact = useState(DEFAULT_TRAVELER_CONTACT)                     |
| [NO UI input modal, NO form fields, setContact is NEVER called]         |
| Default values: Ananya Roy / ananya.roy@example.travel                  |
+-------------------------------------------------------------------------+
                                    |
            +-----------------------+-----------------------+
            | Flight Booking                                | Hotel Booking
            v                                               v
+-------------------------------+             +-------------------------------+
| selectFlightOffer()           |             | prepareHotelBooking()         |
| Hardcoded fallback:           |             | Hardcoded payload:            |
| - given_name: 'Ananya'        |             | - guest_name: 'Ananya Roy'    |
| - family_name: 'Roy'          |             | - guest_email: 'traveler...'  |
| - born_on: '1992-05-15'       |             | processHotelPayment()         |
| - gender: 'f', title: 'ms'    |             | - holder / guests fallback:   |
| - email: 'traveler@...'       |             |   'Traveler' / 'traveler@...' |
+-------------------------------+             +-------------------------------+
            |                                               |
            v                                               v
+-------------------------------+             +-------------------------------+
| Backend Normalization         |             | Backend Normalization         |
| FlightPassenger Pydantic Model|             | HotelBookingService / LiteAPI |
| - stored in booking intent    |             | - stored in transaction store |
+-------------------------------+             +-------------------------------+
            |                                               |
            v                                               v
+-------------------------------+             +-------------------------------+
| Duffel Provider book()        |             | LiteAPI rates/book            |
| Order created with synthetic  |             | Booking confirmed with lead   |
| passenger data                |             | guest fallback                |
+-------------------------------+             +-------------------------------+
```

---

## 3. Hardcoded Passenger Identity Findings

The following table documents every verified hardcoded passenger, guest, and customer identity occurrence in the codebase:

| File | Line | Function | Hardcoded Data | Flow | Caller | Downstream API | Provider Payload |
|---|---:|---|---|---|---|---|---|
| `frontend/src/mock/bookingMockData.ts` | 40–46 | `DEFAULT_TRAVELER_CONTACT` | `first_name: 'Ananya'`, `last_name: 'Roy'`, `email: 'ananya.roy@example.travel'`, `phone: '+91 98301 23456'` | Initial state for booking workspace | `BookingWorkspace.tsx:167` | N/A (Frontend Memory) | Initial seed for all workspace booking flows |
| `frontend/src/components/booking/BookingWorkspace.tsx` | 167 | Component state | `useState<TravelerContactInfo>(DEFAULT_TRAVELER_CONTACT)` | Workspace contact initialization; `setContact` is never called anywhere | React render | N/A | Passed into review panel, flight select, and hotel payment |
| `frontend/src/components/booking/BookingWorkspace.tsx` | 318–328 | `handleVerifyFlight` | `given_name: contact.first_name \|\| 'Traveler'`, `family_name: contact.last_name \|\| 'User'`, `born_on: '1990-01-01'`, `title: 'mr'`, `gender: 'm'`, `email: contact.email \|\| 'traveler@trusttrip.com'`, `phone_number: contact.phone \|\| '+1234567890'` | Flight offer selection prior to revalidation | User clicks "Verify Flight" in `BookingWorkspace` | `POST /api/flights/booking/select` | Hashed into `passenger_hash`, stored in `intent.metadata.passengers`, sent to Duffel |
| `frontend/src/components/booking/BookingWorkspace.tsx` | 460–470 | `handleApproveFlight` | Same as lines 318–328: `'Traveler'`, `'User'`, `'1990-01-01'`, `'mr'`, `'m'`, `'traveler@trusttrip.com'`, `'+1234567890'` | Flight offer selection prior to approval | User clicks "Approve Fare & Route" when `bookingId` absent | `POST /api/flights/booking/select` | Same as above |
| `frontend/src/components/views/TripDetailView.tsx` | 250–260 | `handleSelectFlightOffer` | `given_name: 'Traveler'`, `family_name: 'User'`, `born_on: '1990-01-01'`, `title: 'mr'`, `gender: 'm'`, `email: 'traveler@trusttrip.com'` | Flight offer pre-initialization on card selection | User selects flight in `TripDetailView` | `POST /api/flights/booking/select` | Initializes booking intent on backend with dummy passenger |
| `frontend/src/services/bookingService.ts` | 231–240 | `verifyFlight` | `given_name: 'Ananya'`, `family_name: 'Roy'`, `born_on: '1992-05-15'`, `title: 'ms'`, `gender: 'f'`, `email: 'traveler@trusttrip.com'` | Fallback flight selection during offer verification | `BookingWorkspace.tsx:412` (legacy verify fallback) | `POST /api/flights/booking/select` | Overrides/initializes booking intent with Ananya Roy |
| `frontend/src/services/bookingService.ts` | 481–482 | `prepareHotelBooking` | `guest_name: 'Ananya Roy'`, `guest_email: 'traveler@trusttrip.com'` | Hotel prebooking preparation | `BookingWorkspace.tsx:489` | `POST /api/hotels/prebook` | Sent to hotel prebook endpoint (`HotelService().prebook()`) |
| `frontend/src/services/bookingService.ts` | 551–565 | `processHotelPayment` | `firstName: contact.first_name \|\| 'Traveler'`, `lastName: contact.last_name \|\| 'Traveler'`, `email: contact.email \|\| 'traveler@trusttrip.com'`, `phone: contact.phone \|\| '+919876543210'` | Hotel order creation & verification payload | `BookingWorkspace.tsx:944` | `POST /api/hotel-payments/create-order` | Populates `holder` and `guests` arrays sent to LiteAPI `/rates/book` |

---

## 4. Authenticated User Data Model

### Verified Schemas

#### Backend Domain (`domain/user.py`):
```python
class User(BaseModel):
    id: UUID
    email: str
    password_hash: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

class UserResponse(BaseModel):
    id: UUID
    email: str
    is_active: bool
    created_at: datetime
```

#### Database Schema (`services/auth_store.py:46–55`):
```sql
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### Frontend Interface (`frontend/src/types/auth.ts:1–6`):
```typescript
export interface User {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
}
```

### Verified Field Presence
- **First Name:** DOES NOT EXIST.
- **Last Name:** DOES NOT EXIST.
- **Full Name:** DOES NOT EXIST.
- **Email:** EXISTS (`email: string`).
- **Phone Number:** DOES NOT EXIST.
- **Date of Birth:** DOES NOT EXIST.
- **Gender:** DOES NOT EXIST.
- **Passport Details:** DOES NOT EXIST.
- **Nationality:** DOES NOT EXIST.
- **Address:** DOES NOT EXIST.

**Conclusion:** The authenticated user data model is strictly an authentication credential (email + password hash). It contains zero traveler or passenger profile information.

---

## 5. Traveler Input Model

1. **Where traveler information is entered:**
   - There is **no form or modal** to enter traveler names, dates of birth, genders, or passport details.
   - The only traveler input in the entire UI is `travelers_count` (an integer number of travelers, e.g. 1, 2, 3) in `TripDetailView.tsx` / `OverviewView.tsx`.
2. **What fields can be entered:**
   - None for passenger identity.
3. **Where it is stored:**
   - `contact` state in `BookingWorkspace.tsx:167`.
4. **Whether it survives navigation/re-render:**
   - No. It is held in local React component state and resets to `DEFAULT_TRAVELER_CONTACT` on unmount or navigation.
5. **Whether it is associated with a selected flight:**
   - Only at the moment `POST /api/flights/booking/select` is called, where the hardcoded payload is written to `booking_intents.metadata.passengers`.
6. **Whether it is associated with a selected hotel:**
   - Only at the moment `POST /api/hotel-payments/create-order` is called, where it is written to `trust_trip_hotel_transactions.holder_json` and `guests_json`.
7. **Whether it is persisted or only held in frontend state:**
   - It is not persisted as a user profile. It only exists ephemerally in memory or locked inside specific booking intent rows.
8. **Whether multiple travelers are supported:**
   - **No in frontend:** All frontend call sites send an array with exactly 1 hardcoded passenger, even when `trip.travelers_count > 1`.
   - **Yes in backend schema:** `FlightBookingSelectionRequest` accepts `passengers: list[FlightPassenger] = Field(min_length=1, max_length=9)`.
   - **Mismatch bug:** If a flight offer was searched with 2 passengers, Duffel returns 2 offer passengers. Sending 1 passenger triggers `FlightProviderError: Passenger count mismatch: the selected offer expects 2 passenger(s), but 1 passenger(s) were supplied.` (`services/duffel_provider.py:387`).

---

## 6. Identity Precedence Analysis

### Scenario A: Authenticated user has profile data but no explicit traveler input
- **Current Code:** Authenticated user has no profile data except `email`. Booking falls back to hardcoded `"Traveler User"` or `"Ananya Roy"` and `"traveler@trusttrip.com"`.
- **Expected Architecture:** The authenticated user's email should be the default contact email, but passenger names and DOBs must be collected explicitly since the database has no profile fields.

### Scenario B: Authenticated user exists and traveler modal contains explicit traveler data
- **Analysis:** Explicit traveler input **must take 100% precedence** over the authenticated user. In travel booking, the logged-in user frequently books for a spouse, colleague, family member, or client. The authenticated user identity represents the account owner, NOT necessarily the traveler.

### Scenario C: Traveler input is incomplete
- **Analysis:** The booking must be blocked at the client and server boundary:
  - `FlightPassenger` enforces `given_name` (1–40 chars), `family_name` (1–40 chars), `born_on` (YYYY-MM-DD regex), `title` (`mr|mrs|ms|miss|mx|dr`), and `gender` (`m|f|x`).
  - Missing any of these fields causes HTTP 422 Unprocessable Entity.
  - Silent fallbacks to `"Traveler"`, `"User"`, or `"1990-01-01"` must never occur in production.

### Scenario D: Multiple travelers are entered
- **Analysis:**
  - Backend schema supports up to 9 passengers (`min_length=1, max_length=9`).
  - Duffel requires an exact 1:1 match between supplied passengers and the offer's passenger requirements (`services/duffel_provider.py:386–390`).
  - Current frontend sends an array of 1 passenger regardless of `travelers_count`, which causes provider failures for multi-passenger bookings.

### Scenario E: User edits traveler information after selecting a flight/hotel
- **Analysis:**
  - In `services/flight_booking_service.py:278`, `passenger_hash = hashlib.sha256(...)` is computed during `create_booking` (`/select`).
  - There is no endpoint to mutate passengers on an existing booking intent.
  - If traveler information changes after selection, a new booking intent must be created or the existing intent must be invalidated.

---

## 7. Flight Booking Paths

### Path A (Section 5B / 5C — Direct Duffel Booking)
- **Frontend Component:** `frontend/src/components/booking/BookingWorkspace.tsx` (`handleConfirmFlight`, line 516)
- **Frontend Service:** `bookingService.confirmFlightBooking(bId)` (`frontend/src/services/bookingService.ts:184`)
- **API Client:** `api.confirmFlightBooking(bookingId, { final_confirmation: true })` (`frontend/src/lib/apiClient.ts:1383`)
- **Backend Endpoint:** `POST /api/flights/booking/{booking_id}/confirm` (`flight_api.py:493`)
- **Backend Service:** `FlightBookingService.confirm_booking()` (`services/flight_booking_service.py:588`)
- **Provider:** `DuffelProvider.book(raw_provider_offer_id, passengers, payment_mode="auto")` (`services/duffel_provider.py:363`)
- **Confirmation:** Returns `FlightBookingConfirmationResponse` containing `booking_reference`, `order_id`, `pnr`, `e_ticket`.
- **UI Reachability:** **100% REACHABLE AND ACTIVE.** This is the exact button clicked by users in `BookingWorkspace`.

### Path B (Section 10 — Razorpay Flight Checkout Flow)
- **Frontend Component:** **NONE.** No component in `frontend/src` invokes this path.
- **Frontend Service:** **NONE.** No method in `bookingService.ts` invokes `/api/payments/*`.
- **API Client:** **NONE.** `frontend/src/lib/apiClient.ts` contains zero references to `/api/payments/create-order` or `/api/payments/verify-and-book`.
- **Backend Endpoint:** `POST /api/payments/create-order` (`payment_api.py:29`) and `POST /api/payments/verify-and-book` (`payment_api.py`)
- **Backend Service:** `FlightService` / `TransactionStore` / `RazorpayService`
- **Provider:** `FlightService.provider.book(...)`
- **UI Reachability:** **0% REACHABLE. TOTALLY UNWIRED IN FRONTEND.**

---

## 8. Authoritative Flight Booking Path

### Status: CANONICAL — PATH A (SECTION 5B)

**Evidence:**
1. **Frontend Call Sites:** `BookingWorkspace.tsx:516` directly invokes `bookingService.confirmFlightBooking()`, which exclusively targets Path A (`POST /api/flights/booking/{id}/confirm`).
2. **API Client Coverage:** `frontend/src/lib/apiClient.ts` has full client implementations for Path A (`selectFlightOffer`, `revalidateFlightContract`, `recordFlightApproval`, `confirmFlightBooking`), while Path B has zero client methods.
3. **E2E & Integration Tests:**
   - `tests/test_section5c_booking_safety.py` tests Path A end-to-end (approval guards, price change guards, duplicate confirmation blocking).
   - `tests/test_fast_track_frontend_e2e.py:185–250` tests Path A (`/api/flights/booking/select`, `/api/flights/booking/{id}/revalidate`, `/api/flights/booking/{id}/approve`, `/api/flights/booking/{id}/confirm`).
4. **Separation of Concerns:**
   - Section 10 payment flow was designed and implemented for **Hotels** (`/api/hotel-payments/*`).
   - Section 5B/5C was designed and implemented for **Flights** (`/api/flights/booking/*`).
   - `payment_api.py` contains a flight prototype for Razorpay, but it was never connected to the frontend booking workspace.

---

## 9. Flight Passenger Provider Payload

### Data Flow
```
Frontend (BookingWorkspace / bookingService)
  ↓ JSON payload
POST /api/flights/booking/select
  ↓ Pydantic FlightBookingSelectionRequest
Backend Normalization (FlightPassenger)
  ↓ Stored in booking_intents table (metadata JSONB)
POST /api/flights/booking/{id}/confirm
  ↓ FlightBookingService.confirm_booking()
DuffelProvider.book(offer_id, passengers)
  ↓ Python dict normalization
Duffel API POST /air/orders
```

### Provider Payload Fields (Duffel API)
| Field | Type | Required | Source | Description |
|---|---|---|---|---|
| `id` | string | **Yes** | `offer_passenger.get("id")` | Injected by backend from selected Duffel offer |
| `type` | string | **Yes** | Derived by backend | Derived from DOB and departure date (`adult`, `child`, `infant_without_seat`) |
| `given_name` | string | **Yes** | Client request | Max 40 characters |
| `family_name` | string | **Yes** | Client request | Max 40 characters |
| `born_on` | string | **Yes** | Client request | Format: `YYYY-MM-DD` |
| `title` | string | **Yes** | Client request | One of: `mr`, `mrs`, `ms`, `miss`, `mx`, `dr` |
| `gender` | string | **Yes** | Client request | One of: `m`, `f`, `x` |
| `email` | string | Optional | Client request | Omitted if empty or whitespace |
| `phone_number` | string | Optional | Client request | Normalized to international E.164 (`+...`), omitted if invalid |

---

## 10. Hotel Guest Provider Payload

### Data Flow
```
Frontend (BookingWorkspace / bookingService)
  ↓
POST /api/hotels/prebook (sends offer)
  ↓
POST /api/hotel-payments/create-order (sends offer, prebook, holder, guests)
  ↓
POST /api/hotel-payments/verify-and-book
  ↓
HotelBookingService / HotelService.book()
  ↓
LiteAPI /rates/book or Hotelbeds
```

### Provider Payload Fields (LiteAPI `/rates/book`)
| Field | Type | Required | Source | Description |
|---|---|---|---|---|
| `prebookId` | string | **Yes** | `prebook["prebook_id"]` | Supplier hold reference from prebook call |
| `clientReference` | string | **Yes** | Generated | UUID / transaction ID |
| `holder.firstName` | string | **Yes** | Client request | Lead booker first name |
| `holder.lastName` | string | **Yes** | Client request | Lead booker last name |
| `holder.email` | string | **Yes** | Client request | Lead booker email |
| `holder.phone` | string | **Yes** | Client request | Lead booker phone number |
| `guests` | list[dict] | **Yes** | Client request | List of guest objects with `occupancyNumber`, `firstName`, `lastName`, `email`, `phone` |
| `payment.method` | string | **Yes** | Server config | `ACC_CREDIT_CARD` (sandbox) or `WALLET` (production) |

---

## 11. Payment Relationship

### Flights
- **Current Relationship:** Instant/Hold settlement via Duffel Provider.
- `DuffelProvider.book()` supports:
  - `auto`: Instant order if balance available, or hold order if permitted by airline.
  - `sandbox_balance`: Paid from Duffel test credit balance (used for FYP demonstration).
  - `card`: Card details passed directly to Duffel.
- Razorpay is **not** currently in the execution path for flights.

### Hotels
- **Current Relationship:** Two-phase escrow via Razorpay.
  1. Client revalidates snapshot and initiates prebook.
  2. Server creates Razorpay order (`/api/hotel-payments/create-order`) and locks transaction in `trust_trip_hotel_transactions`.
  3. Client collects payment via Razorpay checkout.
  4. Server verifies HMAC-SHA256 signature (`/api/hotel-payments/verify-and-book`).
  5. Upon verified payment capture, server invokes supplier booking via LiteAPI/Hotelbeds.

---

## 12. Test Coverage

### Strong Tests
- `tests/test_section5c_booking_safety.py`: Thorough coverage of Section 5B/5C state machine (approval required, duplicate booking prevention, price change invalidation, snapshot expiration).
- `frontend/src/__tests__/section10HotelPayment.test.tsx`: Comprehensive contract testing for hotel payment endpoints, idempotency keys, and revision validation.
- `tests/test_hotel_snapshot_long_id.py`: Verifies hotel snapshot UUID binding and length handling.

### Coverage Gaps
- **Test Gap 1 (Identity Propagation):** `tests/test_fast_track_frontend_e2e.py:188` explicitly sends `"given_name": "Ananya"`, `"family_name": "Roy"`. Tests pass *because* the test data hardcodes the same name as the production mock.
- **Test Gap 2 (Authenticated User Disconnect):** No test asserts that a logged-in user `user@domain.com` cannot produce a booking under `"traveler@trusttrip.com"`.
- **Test Gap 3 (Multi-Traveler Flight):** No test exercises flight offer selection or booking with `travelers_count > 1` matching Duffel passenger slots.
- **Test Gap 4 (No UI Input Test):** No frontend test checks whether user input fields exist for traveler name, DOB, or gender.

---

## 13. Defects

| ID | Severity | Category | Description |
|---|---|---|---|
| **DEF-01** | **P0** | Data Integrity | **Synthetic Passenger Injection in Production Code:** `bookingService.ts:233`, `BookingWorkspace.tsx:320`, `BookingWorkspace.tsx:462`, `TripDetailView.tsx:252` inject hardcoded `'Ananya Roy'` or `'Traveler User'` into live provider payloads. |
| **DEF-02** | **P0** | Product / UI | **Missing Traveler Input UI:** `BookingWorkspace` has no form, input fields, or modal for the user to enter their legal travel name, date of birth, gender, or phone number. |
| **DEF-03** | **P1** | Provider Contract | **Multi-Passenger Array Mismatch:** Frontend hardcodes a 1-element passenger array `[ {...} ]`. If a trip has `travelers_count = 2`, Duffel rejects the order creation due to passenger count mismatch. |
| **DEF-04** | **P1** | Architecture | **Unwired Flight Payment Flow (Path B):** `payment_api.py` exposes `/api/payments/create-order` for flights, but it is completely disconnected from the frontend, creating architectural confusion with Path A. |
| **DEF-05** | **P2** | Data Integrity | **Hardcoded Hotel Prebook Guest:** `bookingService.ts:481` hardcodes `guest_name: 'Ananya Roy'` and `guest_email: 'traveler@trusttrip.com'` in `prepareHotelBooking`. |

---

## 14. Recommended Minimal Remediation

*(DO NOT IMPLEMENT — Recommendations for next phase)*

1. **Phase 2A — Traveler Identity Input Modal / Drawer in `BookingWorkspace`:**
   - Create a clean, required Traveler Input Form before flight verification/selection.
   - Form fields: `given_name`, `family_name`, `born_on` (YYYY-MM-DD), `gender` (m/f/x), `title` (mr/mrs/ms/dr), `phone_number`.
   - Default `email` to `currentUser.email` from `AuthContext`.
   - Support `N` travelers where `N = trip.travelers_count || 1`.
2. **Phase 2B — Eliminate Hardcoded Fallbacks in `bookingService.ts` and `BookingWorkspace.tsx`:**
   - Disallow fallback to `'Ananya'` or `'Traveler'`.
   - Require valid traveler data before `selectFlightOffer` or `prepareHotelBooking` can execute.
3. **Phase 2C — Clarify Flight Booking Path Documentation:**
   - Formally document Section 5B (`/api/flights/booking/*`) as the authoritative flight booking path.
   - Deprecate or clearly mark Path B in `payment_api.py` as an experimental/unwired prototype.

---

## 15. Required Regression Tests

1. `test_flight_booking_passenger_propagation`: Verify that arbitrary custom passenger names (e.g., `"Siddhartha Mukherjee"`, `"1985-11-20"`, `"m"`) supplied by frontend correctly reach Duffel provider without fallback.
2. `test_multi_passenger_flight_selection`: Verify that selecting a 2-traveler flight passes 2 distinct `FlightPassenger` objects to the backend and Duffel.
3. `test_booking_rejects_missing_passenger_info`: Verify that calling `/select` without DOB or name fails with 422 Unprocessable Entity rather than injecting defaults.
4. `test_current_user_email_binding`: Verify that the logged-in user's email is utilized as the primary contact email by default.
5. `test_hotel_guest_propagation`: Verify that the lead traveler name entered in the UI propagates through `create-order` and `verify-and-book` to LiteAPI.

---

## 16. Unknowns

1. **User Profile Persistence:** It is currently unknown whether product specifications require persisting traveler profiles (e.g., "Saved Passengers" in a database table) or whether travelers should be collected per-booking only.
2. **Passport Requirements:** Duffel supports passport details (`passport_number`, `issuing_country`), but domestic flights do not require them. It is unverified whether international flights will mandate passport collection in Wave 2.
3. **Razorpay Flight Requirement:** It is unknown whether product leadership intends to replace Duffel's instant/sandbox balance booking (Path A) with Razorpay INR collection (Path B), or if Path A is the intended final-year project architecture.
