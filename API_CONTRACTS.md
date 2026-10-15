# TrustTrip — Verified API Contracts

**Date:** 2026-10-04  
**Status:** Post-Wave-1 Verified Contracts  

---

## 1. Authentication (`/api/auth`)

### `POST /api/auth/register`
- **Request Body:**
  ```json
  {
    "email": "user@example.com",
    "password": "SecurePassword123!",
    "full_name": "Traveler Name"
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "id": "<user_uuid>",
    "email": "user@example.com",
    "full_name": "Traveler Name",
    "created_at": "2026-10-04T12:00:00Z"
  }
  ```

### `POST /api/auth/token`
- **Request Body (OAuth2 Form):** `username=<email>&password=<password>`
- **Response (200 OK):**
  ```json
  {
    "access_token": "<jwt_token>",
    "token_type": "bearer"
  }
  ```

### `GET /api/auth/me`
- **Headers:** `Authorization: Bearer <token>`
- **Response (200 OK):** UserResponse object

---

## 2. Travel Planning & Trips (`/api/trips` & `/api/travel`)

### `POST /api/trips`
- **Headers:** `Authorization: Bearer <token>`
- **Request Body:**
  ```json
  {
    "title": "Darjeeling Journey",
    "destination": "Darjeeling, India",
    "origin": "Kolkata, India",
    "metadata": {
      "budget": 50000,
      "start_date": "2026-11-01",
      "end_date": "2026-11-07"
    }
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "status": "success",
    "trip": {
      "id": "<trip_uuid>",
      "user_id": "<user_uuid>",
      "title": "Darjeeling Journey",
      "destination": "Darjeeling, India",
      "origin": "Kolkata, India",
      "status": "planning",
      "current_revision_number": 0,
      "metadata": { ... }
    }
  }
  ```

### `POST /api/travel` (Wave 1 Contract)
- **Headers:** `Authorization: Bearer <token>`
- **Request Body:**
  ```json
  {
    "message": "Plan a 6-day scenic trip to Darjeeling",
    "trip_id": "<trip_uuid>"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "plan_json": { ... },
    "revision_id": "<revision_uuid>",
    "revision_number": 1,
    "revision_status": "PROPOSED",
    "current_revision_id": "<revision_uuid>",
    "current_revision_number": 1,
    "destination_airport": "IXB",
    "origin_airport": "CCU"
  }
  ```

### `POST /api/trips/{trip_id}/approve`
- **Request Body:**
  ```json
  {
    "revision_id": "<revision_uuid>",
    "expected_revision_number": 1,
    "notes": "Approved by traveler"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "review_status": "APPROVED",
    "approval_id": "<approval_uuid>",
    "revision_id": "<revision_uuid>",
    "trip_id": "<trip_uuid>",
    "revision_number": 1
  }
  ```

### `POST /api/trips/{trip_id}/booking-approval` (Section 9 Authorization)
- **Request Body:**
  ```json
  {
    "revision_id": "<revision_uuid>",
    "expected_revision_number": 1,
    "notes": "Authorized for booking"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "is_authorized": true,
    "authorization_id": "<auth_uuid>",
    "trip_id": "<trip_uuid>",
    "revision_id": "<revision_uuid>",
    "revision_number": 1,
    "authorized_at": "2026-10-04T12:05:00Z"
  }
  ```

---

## 3. Flight Booking API (`/api/flights`)

### `POST /api/flights/search`
- **Request Body:**
  ```json
  {
    "trip_id": "<trip_uuid>",
    "origin": "CCU",
    "destination": "IXB",
    "departure_date": "2026-11-01",
    "return_date": "2026-11-07",
    "passengers": 2
  }
  ```
- **Response (200 OK):** List of flight offers and snapshot IDs.

### `POST /api/flights/booking/select`
- **Request Body:**
  ```json
  {
    "trip_id": "<trip_uuid>",
    "offer_id": "<snapshot_uuid_or_provider_id>",
    "passengers": [
      {
        "given_name": "Traveler",
        "family_name": "One",
        "born_on": "1992-05-15",
        "title": "mr",
        "gender": "m",
        "email": "traveler@example.com"
      }
    ]
  }
  ```
- **Response (201 Created):**
  ```json
  {
    "booking_id": "<booking_intent_uuid>",
    "status": "ready_for_revalidation",
    "expected_amount": 12500.0,
    "currency": "INR"
  }
  ```

### `POST /api/flights/booking/{booking_id}/revalidate`
- **Response (200 OK):**
  ```json
  {
    "booking_id": "<booking_intent_uuid>",
    "price_changed": false,
    "current_price": 12500.0,
    "currency": "INR",
    "status": "ready_for_approval"
  }
  ```

### `POST /api/flights/booking/{booking_id}/confirm`
- **Request Body:**
  ```json
  {
    "final_confirmation": true
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "status": "completed",
    "booking_reference": "ABC123XYZ",
    "order_id": "ord_duffel_123",
    "pnr": "ABC123XYZ",
    "e_ticket": "ETK-123456789"
  }
  ```

---

## 4. Hotel Booking & Payments (`/api/hotels` & `/api/hotel-payments`)

### `POST /api/hotels/prebook`
- **Authentication:** Mandatory (`Bearer <token>`). Anonymous requests rejected with HTTP 401 Unauthorized.
- **Authorization & Isolation:** Tenant trip and snapshot isolation enforced. Cross-tenant trip returns HTTP 403 Forbidden; non-existent trip returns HTTP 404 Not Found; cross-tenant snapshot returns HTTP 403 Forbidden; expired snapshot returns HTTP 409 Conflict.
- **Source of Truth:** Authoritative price and currency are resolved server-side from the validated `snapshot_id` or trip selection metadata; missing authoritative offer fails closed with HTTP 422 Unprocessable Entity.
- **Request Body:**
  ```json
  {
    "trip_id": "<trip_uuid>",
    "snapshot_id": "<snapshot_uuid>",
    "hotel_id": "<hotel_id>",
    "room_id": "<room_id>",
    "check_in_date": "2026-11-01",
    "check_out_date": "2026-11-07",
    "guests": 2,
    "guest_name": "Traveler Name",
    "guest_email": "traveler@example.com"
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "prebook_id": "<prebook_uuid>",
    "amount": 18000.0,
    "currency": "INR",
    "status": "prebooked",
    "hotel_name": "Taj Mahal Palace",
    "expires_at": "2026-10-06T12:00:00Z",
    "prebook": {
      "prebook_id": "<prebook_uuid>",
      "status": "prebooked"
    }
  }
  ```

### `POST /api/hotel-payments/create-order`
- **Authentication:** Mandatory (`Bearer <token>`). Section 9 booking eligibility verified.
- **Idempotency:** `payment_idempotency_key` enforced at database and memory stores. Concurrent or replay requests with the same key return the existing transaction without duplicate order creation.
- **Request Body:**
  ```json
  {
    "offer": { "provider": "liteapi", "hotel_name": "...", "price": 18000.0, "currency": "INR", "price_inr": 18000.0 },
    "prebook": { "prebook_id": "<prebook_uuid>", "status": "prebooked" },
    "holder": { "firstName": "Lead", "lastName": "Traveler", "email": "lead@example.com" },
    "guests": [ { "occupancyNumber": 1, "firstName": "Lead", "lastName": "Traveler" } ],
    "trip_id": "<trip_uuid>",
    "revision_id": "<revision_uuid>",
    "expected_revision_number": 1,
    "payment_idempotency_key": "<idempotency_key>",
    "final_confirmation": true
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "transaction_id": "<tx_uuid>",
    "razorpay_order_id": "order_rzp_12345",
    "amount_inr": 18000.0,
    "currency": "INR",
    "key_id": "rzp_test_..."
  }
  ```

### `POST /api/hotel-payments/verify-and-book`
- **Authentication:** Mandatory (`Bearer <token>`).
- **Signature Verification:** Genuine HMAC-SHA256 signature verification (`RazorpayService.verify_checkout_signature`). Fake/synthetic signatures (`sig_...`) rejected with HTTP 400 Bad Request.
- **Payment Verification:** Fetches captured payment record from Razorpay API; rejects uncaptured payments or mismatched orders.
- **Fulfillment Guard:** Executes `HotelService.book()` only after signature verification and authorization checks pass.
- **Request Body:**
  ```json
  {
    "transaction_id": "<tx_uuid>",
    "razorpay_order_id": "order_rzp_12345",
    "razorpay_payment_id": "pay_rzp_real_67890",
    "razorpay_signature": "<genuine_hmac_sha256_signature>",
    "offer": { ... },
    "prebook": { "prebook_id": "<prebook_uuid>" },
    "holder": { "firstName": "Lead", "lastName": "Traveler" },
    "guests": [ ... ],
    "trip_id": "<trip_uuid>",
    "revision_id": "<revision_uuid>",
    "expected_revision_number": 1,
    "final_confirmation": true
  }
  ```
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "booking_status": "confirmed",
    "booking_reference": "HTL-998877",
    "booking_id": "bkg_provider_12345",
    "transaction_id": "<tx_uuid>"
  }
  ```

---

## 4. Booking Persistence, Rehydration & Documents (`/api/trips`) (Wave 3B)

### `GET /api/trips/{trip_id}/bookings`
- **Authentication:** Mandatory (`Bearer <token>`).
- **Authorization:** Strictly enforces tenant isolation. Returns `403 Forbidden` if the trip exists but is owned by another user; `404 Not Found` if trip does not exist.
- **Purpose:** Server-driven authoritative rehydration for booking components (Flight & Hotel).
- **Response (200 OK):**
  ```json
  {
    "success": true,
    "trip_id": "<trip_uuid>",
    "flight": {
      "is_booked": true,
      "status": "completed",
      "in_progress": false,
      "booking_id": "<intent_uuid>",
      "booking_reference": "G4XYZ8",
      "pnr": "G4XYZ8",
      "order_id": "ord_12345",
      "provider": "duffel",
      "amount": 450.0,
      "currency": "GBP",
      "passengers": [ ... ],
      "documents": [ { "type": "electronic_ticket", "unique_identifier": "0125-9876543210" } ],
      "slices": [ ... ],
      "completed_at": "2026-10-05T12:00:00Z"
    },
    "hotel": {
      "is_booked": true,
      "status": "booked",
      "in_progress": false,
      "transaction_id": "<tx_uuid>",
      "confirmation_code": "VOUCH-1234",
      "hotel_name": "Grand Palace Hotel",
      "check_in": "2026-11-01",
      "check_out": "2026-11-07",
      "amount_inr": 25000.0,
      "currency": "INR",
      "holder": { ... },
      "guests": [ ... ]
    }
  }
  ```
- **Partial Bookings:** If only flight or only hotel is booked, the absent component is `null`. In-progress operations preserve `in_progress: true` and appropriate state (e.g. `HANDED_OFF` or `booking_in_progress`).

### `GET /api/trips/{transaction_id}/ticket.pdf`
- **Authentication:** Mandatory (`Bearer <token>`).
- **Authorization:** Resolves canonical `booking_intents` records owned by `current_user.id` (with legacy fallback). Cross-user access returns `403 Forbidden`. Nonexistent records return `404 Not Found`.
- **Offline / Degraded Fallback:** Generates valid PDF directly from persisted itinerary `slices`, `passengers`, `documents`, and `booking_reference` in `booking_intents.metadata` without failing if live Duffel refresh is unavailable.
- **Response (200 OK):** Binary `application/pdf` byte stream with `Content-Disposition: attachment; filename="Trust-Trip-MAS-<ref>.pdf"`.

### `GET /api/trips`
- **Authentication:** Mandatory (`Bearer <token>`).
- **Booking Aggregation:** Queries canonical `booking_intents` and `trust_trip_hotel_transactions` for current user. Attaches `flight_count`, `hotel_count`, `flights`, and `hotels` arrays to canonical trips while preserving legacy top-level transaction entries for backward compatibility.
