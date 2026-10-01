
# TrustTrip — Travel Planner & Booking Platform

## Deployment & Production Quickstart Guide

This guide provides reproducible instructions for configuring, building, and deploying the TrustTrip platform.

### 1. Prerequisites
- **Python**: 3.10+ (Python 3.11 recommended)
- **Node.js**: 18+ (with npm 9+)
- **PostgreSQL with pgvector**: 15+ (local instance, Docker, or managed cloud database e.g., Supabase / Neon / AWS RDS). **Crucial:** The PostgreSQL instance must have the `pgvector` extension installed and available, as `migrations/006_research_and_rag.sql` executes `CREATE EXTENSION IF NOT EXISTS vector;`.
- **Git**: 2.30+

### 2. Backend Setup
1. Clone the repository and navigate to root:
   ```bash
   git clone <repo-url>
   cd TrustTrip_TravelPlanner
   ```
2. Create and activate a Python virtual environment:
   ```bash
   # Windows PowerShell
   python -m venv venv
   .\venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install production and test dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

### 3. Frontend Setup
1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install npm dependencies:
   ```bash
   npm install
   ```
3. Return to project root:
   ```bash
   cd ..
   ```

### 4. Environment Variables & Production Security Configuration
Copy the provided `.env.example` templates to configure the runtime environment:

1. **Backend Environment** (`.env` in repository root):
   ```bash
   cp .env.example .env
   ```

   #### Mandatory Production Security Variables (`services/config.py`):
   When running in production (`APP_ENV=production`), the application strictly enforces that the following variables are non-empty and valid; otherwise, it **fails closed** with a `RuntimeError` at startup:
   - `APP_ENV=production`: Activates strict production validation mode.
   - `JWT_SECRET`: High-entropy persistent secret (min 32 bytes recommended) used for HMAC-SHA256 user authentication tokens. Must be persistent across server restarts so authenticated user sessions are maintained.
   - `PAYLOAD_ENCRYPTION_KEY`: A persistent 32-byte base64-encoded key used for AES-256-GCM search session payload encryption in PostgreSQL.
     Generate a valid key using:
     ```bash
     python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
     ```
   > **Important:** Never rely on ephemeral development fallbacks in production. In development (`APP_ENV=development`), ephemeral in-memory keys are generated if omitted, which invalidate sessions and encrypted payloads upon process restart.

   #### Core Runtime & Provider Variables:
   - `DATABASE_URL`: PostgreSQL connection URI (`postgresql://<user>:<password>@<host>:<port>/<database>`).
   - `FRONTEND_API_URL` or `FRONTEND_URL`: URL of the deployed frontend for CORS allowlist (e.g., `https://trusttrip.example.com` or `http://localhost:3000`).
   - `ALLOWED_ORIGINS`: Comma-separated CORS origins fallback (defaults to `http://localhost:3000,http://localhost:8000`).
   - `GROQ_API_KEY`: Primary LLM planner API key.
   - `GEMINI_API_KEY`: Fallback LLM API key.
   - `DUFFEL_API_TOKEN` / `DUFFEL_TEST_API_TOKEN`: Flight inventory provider token.
   - `TAVILY_API_KEY`: Search & accommodation evidence gathering.

2. **Frontend Environment** (`frontend/.env`):
   ```bash
   cp frontend/.env.example frontend/.env
   ```
   - `VITE_API_BASE_URL`: Base URL of the backend API (e.g., `http://127.0.0.1:8000` or `https://api.trusttrip.example.com`). Leave empty (`""`) if using a reverse proxy routing `/api` to the backend.

> **Security Note:** Never commit `.env` or production credentials to source control.

### 5. Fresh Database Bootstrap Sequence
TrustTrip separates base tenant ownership tables from versioned feature migrations. Migration `004_travel_intelligence.sql` defines foreign keys referencing `users`, `trips`, and `threads`. Therefore, a fresh database must be bootstrapped in the following order:

```text
Fresh PostgreSQL DB (with pgvector extension available)
    ↓
1. Initialize Base Application Tables (`users`, `trips`, `threads`)
    ↓
2. Apply Migration 004 (`search_sessions`, `offer_snapshots`, `revalidation_history`)
    ↓
3. Apply Migration 005 (`plan_revisions`, `evidence_records`, etc.)
    ↓
4. Apply Migration 006 (`research_sessions`, `rag_documents`, `vector` extension, etc.)
    ↓
5. Apply Migration 007 (`booking_intents`, `booking_attempts`, `booking_transitions`)
    ↓
6. Application Startup Schema Validation (`validate_database_schema()`)
```

#### Step-by-Step Bootstrap Commands:
1. **Initialize Base Application Tables**:
   Run the Python bootstrap helper to create the core `users`, `trips`, and `threads` tables:
   ```bash
   python -c "from services.auth_store import init_auth_tables; from services.trip_store import init_trip_tables; init_auth_tables(); init_trip_tables(); print('Base tables initialized.')"
   ```
2. **Apply Versioned SQL Migrations**:
   Run the migrations sequentially using `psql`:
   ```bash
   psql -d "$DATABASE_URL" -f migrations/004_travel_intelligence.sql
   psql -d "$DATABASE_URL" -f migrations/005_evidence_revision_lifecycle.sql
   psql -d "$DATABASE_URL" -f migrations/006_research_and_rag.sql
   psql -d "$DATABASE_URL" -f migrations/007_flight_booking_lifecycle.sql
   ```
3. **Startup Schema Verification**:
   At application startup, `validate_database_schema()` in `services/search_session_store.py` performs a zero-DDL check ensuring all required tables exist across all migrations:
   - `migrations/004`: `search_sessions`, `offer_snapshots`, `revalidation_history`
   - `migrations/005`: `plan_revisions`, `evidence_records`, `evidence_provenance`, `plan_revision_evidence_links`, `plan_approvals`
   - `migrations/006`: `research_sessions`, `research_queries`, `research_sources`, `research_evidence`, `research_citations`, `rag_documents`, `rag_chunks`
   - `migrations/007`: `booking_intents`, `booking_attempts`, `booking_transitions`

### 6. Local Run
To run both backend and frontend locally for development or testing:

1. **Start Backend**:
   ```bash
   uvicorn app:app --host 127.0.0.1 --port 8000 --reload
   ```
2. **Start Frontend** (in a separate terminal):
   ```bash
   cd frontend
   npm run dev
   ```
   Open `http://localhost:3000` in your browser (configured in `vite.config.ts`).

### 7. Production Build
1. In the `frontend/` directory, run:
   ```bash
   cd frontend
   npm run build
   ```
2. The compiled static single-page application is output to `frontend/dist/`.
3. To preview the production bundle locally:
   ```bash
   npm run preview
   ```

### 8. Deployment Commands
TrustTrip supports standard stateless container or VM deployments:

- **Standalone Production Server (Uvicorn / Gunicorn)**:
  ```bash
  uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
  ```
- **Docker Deployment**:
  ```bash
  # Build production image
  docker build -t trusttrip:latest .

  # Run container with environment configuration
  docker run -d -p 8000:8000 \
    --env-file .env \
    --name trusttrip-backend \
    trusttrip:latest
  ```
- **Static Frontend Hosting**:
  Deploy `frontend/dist/` to any static hosting provider (Vercel, Netlify, Cloudflare Pages, AWS S3/CloudFront, or Nginx). Ensure `VITE_API_BASE_URL` points to the deployed backend address.

### 9. API Health & Readiness Probes
Verify backend process liveness and database readiness:

- **Process Liveness**:
  ```bash
  curl -i http://localhost:8000/health
  # or
  curl -i http://localhost:8000/api/health
  ```
  **Success Semantics**: `HTTP 200 OK`
  ```json
  {
    "status": "ok",
    "version": "2.0.0",
    "message": "Trust Trip MAS API is running",
    "features": [ ... ]
  }
  ```

- **Database Readiness**:
  ```bash
  curl -i http://localhost:8000/ready
  ```
  **Healthy Semantics**: `HTTP 200 OK`
  ```json
  {
    "status": "ready"
  }
  ```
  **Unhealthy Semantics**: If database connectivity fails, returns `HTTP 503 Service Unavailable`:
  ```json
  {
    "status": "not_ready",
    "reason": "database_unavailable"
  }
  ```

### 10. End-to-End Demo Flow
To demonstrate the complete verified TrustTrip workflow:
1. **Trip Planning**: Enter travel criteria (e.g., origin, destination, dates, budget) in the travel assistant prompt.
2. **Flight Search**: The platform queries live/sandbox inventory and persists immutable offer snapshots.
3. **Offer Selection**: Choose an itinerary offer; view fare breakdown, baggage, cabin class, and rules.
4. **Revalidation**: Click to revalidate the selected offer before booking to guarantee fare freshness and seat availability.
5. **Human-in-the-Loop (HITL) Review**: Enter passenger details and review policy constraints. If revalidation detects a price change, the approval resets and requires explicit confirmation of the new fare.
6. **Final Confirmation**: Confirm the booking with explicit user consent (`final_confirmation=true`).
7. **Booking Fulfillment**: The backend claims the transaction, books with the supplier, and presents verified PNR and booking reference.

---

## Final Multi-Agent Architecture

Trust Trip uses **one Supervisor Agent coordinating exactly five specialist agents**:

1. **Flight Agent** — searches and ranks flights through **Duffel only**.
2. **Hotel Agent** — gathers accommodation options/evidence.
3. **Weather Agent** — gathers current weather and forecast information.
4. **Budget Agent** — aggregates flight, hotel, transport, activity and contingency costs.
5. **Itinerary Agent** — generates the final structured travel plan from the collected evidence.

The Supervisor routes work through these five agents. Duffel is a provider/tool used by the Flight Agent, **not a separate agent**. Manual Flight Search uses the same `FlightService` and Duffel provider. Human Review (HITL) remains outside the five specialist-agent count and can route feedback back through the workflow.

### Flight flow

```text
Supervisor -> Flight Agent -> FlightService -> Duffel
                                      |
                                      v
                           INR + provider currency
                                      |
                                      v
                               Human Review
                                      |
                               changed flight need
                                      v
                              Flight Agent -> Duffel
```

Flight booking requires a fresh offer recheck and explicit final confirmation.

# Multi-Agent-System-using-LangGraph-MCP-Supervisor-Guardrails-HITL

A demo multi-agent system that uses LangGraph and MCP to implement a travel-planning assistant with a Supervisor, input Guardrails, and Human-In-The-Loop (HITL) approval flows. The project includes a FastAPI frontend, example MCP server, and client helpers to demonstrate how agents, supervisors, and guardrails can be composed into a safe, reviewable planning pipeline.

Key ideas:
- Multi-agent coordination using LangGraph and MCP
- Supervisor agent to manage complex workflows
- Input guardrails to validate user requests
- Human-in-the-loop approval for generated plans

Contents
- `app.py`: FastAPI web frontend and API endpoints
- `backend.py`: core agent orchestration / travel-planner logic
- `mcp_client.py`: client helpers to interact with the MCP server
- `custom_weather_mcp_server.py`: example MCP server for weather checks
- `templates/`, `static/`: frontend UI assets (HTML, JS, CSS)

Features
- Interactive web UI for sending travel planning prompts
- Endpoint for drafting travel plans and separate approval endpoint
- Example MCP server demonstrating domain adapters (weather, checkpoints)

Prerequisites
- Python 3.10+ (recommended)
- Git (to clone the repo)
- A virtual environment tool (venv) or similar

Quick start (Windows)

1. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1    # PowerShell
```

2. Install dependencies

```powershell
pip install -r requirements.txt
```

3. Run the FastAPI app (development)

```powershell
# option A (run module)
python app.py

# option B (uvicorn)
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

4. Open the web UI

Visit http://127.0.0.1:8000 in your browser to use the TripMate frontend.

Running the MCP server (example)
- The repository includes `custom_weather_mcp_server.py` as an example MCP server. Run it in a separate terminal if you want to experiment with custom adapters used by the demo.

```powershell
# start example MCP server (if needed)
python custom_weather_mcp_server.py
```

API Endpoints
- `POST /api/travel` — create or resume a travel planning thread. JSON: `{ "message": "<user prompt>", "thread_id": "optional-thread-id" }`
- `POST /api/travel/approve` — approve or request revisions for a draft. JSON: `{ "thread_id": "<id>", "approved": true|false, "feedback": "optional" }`
- `POST /api/flights/search` — search flight offers.
- `POST /api/flights/recheck` — refresh a selected offer immediately before booking.
- `POST /api/flights/book` — book/hold only when `final_confirmation=true`; price changes require explicit user acceptance.
- `GET /health` — basic health check and features list

Configuration & environment
- Secrets and API keys are not included in the repo. Use environment variables or a `.env` file for any required keys consumed by `langgraph`, `langchain`, or other adapters.

Development notes
- The project keeps synchronous convenience wrappers in `backend.py` while running an async FastAPI server — `nest_asyncio` is applied in `app.py` to allow the sync helpers to call async MCP helpers.
- Tests are not included; to experiment, interact with the web UI or call the API endpoints directly.

Contributing
- Contributions are welcome. Please open issues or pull requests for bug fixes, documentation improvements, or new adapter examples.

License
- This repository follows the license in the `LICENSE` file.

Acknowledgements
- Built as a demonstration of LangGraph + MCP patterns with supervisor and guardrail concepts.

Contact
- For questions or suggestions, open an issue or contact the repository owner.





### Current flight/planner safety behavior

- Chat travel requests automatically trigger live flight recommendations only when route and departure date are available.
- Changing passenger count, route, or departure date during Human Review refreshes provider data so flight recommendations match the new request. Ordinary itinerary-only feedback does not repeat flight searches.
- Flight cards never infer remaining airline inventory from the number of passengers in an offer. An exact remaining-seat count is shown only if the provider explicitly supplies one; otherwise the UI tells the user that availability is rechecked before booking.
- If live flight data is unavailable or no offer is returned, the planner does not fabricate a flight. It returns an actionable message instead.
- If the evidence-based budget exceeds the requested budget, the planner stops before LLM generation and asks the user to increase the budget, choose cheaper accommodation/dates/activities, or change the destination.
- Groq is primary and Gemini is fallback; the planner uses one successful LLM generation per draft/revision.
- Duffel supports separate test/live credentials through `DUFFEL_MODE`, `DUFFEL_TEST_API_TOKEN`, and `DUFFEL_LIVE_API_TOKEN`.
- Webhooks are intentionally not required in the current development flow. They can be enabled as a separate post-deployment phase.

## Flight Booking Agent

The application now includes a provider-agnostic flight shopping layer at `/api/flights`.
The default provider is Duffel, using its test environment for development. The flow is:

1. Search flight offers.
2. Rank offers by fare/stops.
3. User selects an offer.
4. Recheck the offer immediately before booking.
5. Require explicit final confirmation from the user.
6. Create a sandbox hold order when no payment method is supplied.

Set `DUFFEL_TEST_API_TOKEN / DUFFEL_LIVE_API_TOKEN` and `FLIGHT_PROVIDER=duffel` in `.env`. Duffel test mode is intended for risk-free development and supports test search/order flows; test-mode schedules and prices are not realistic production inventory.

Production booking should use production provider credentials and an approved payment/fulfilment setup. Never commit `.env` or real credentials.

### Phase 2 test flow

For a safe end-to-end sandbox test, create a Duffel developer test token (it begins with `duffel_test_`) and set it in `.env`. Duffel documents predictable test routes, including routes for no-flight, connecting-flight, offer-expiry, price-change, and order-error scenarios.

Example search request:

```json
{
  "slices": [
    {"origin": "JFK", "destination": "EWR", "departure_date": "2026-09-10"}
  ],
  "adults": 1,
  "cabin_class": "economy",
  "max_connections": 2
}
```

Do not put real card details or production credentials in `.env` while testing.

## Flight booking demo: end-to-end sandbox payment

Trust Trip MAS separates flight shopping, fare recheck, human confirmation, order creation, and payment.

### Recommended final-project demo

Use a Duffel **test token** (`duffel_test_...`). Test mode is a sandbox and does not spend real money.

1. Start the FastAPI server.
2. Search a flight.
3. Select an offer.
4. Enter passenger details.
5. Choose **Pay now — Duffel Sandbox Balance (demo, no real money)**.
6. Confirm the final booking dialog.
7. Trust Trip MAS rechecks the fare immediately before creating the order.
8. The backend creates an **instant** Duffel test order using the test balance.
9. The UI displays the order ID and booking reference.

For an offer that supports holding, the user may instead choose **Hold now — pay later (sandbox)**. The resulting hold order shows its payment deadline and provides a separate **Pay Hold in Sandbox** action.

### Why there is no raw credit-card form

Real card booking should not collect raw PAN/CVC values in a normal FastAPI form. A production card flow uses a PCI-safe payment collection component, Duffel Payment Intents/Card APIs, and 3-D Secure authentication. The project therefore uses Duffel's test balance for a reliable, safe classroom demonstration while keeping the provider interface ready for a future authenticated card-payment implementation.

### Troubleshooting a booking error

The frontend now surfaces Duffel's actual error detail. HTTP 502 means the provider rejected the order; HTTP 504 means the provider timed out. Do not blindly retry a timed-out order because an airline-side order may have been created even if the client timed out.

## Real-time adaptive planning + production transaction architecture

Trust Trip MAS now supports adaptive planning from the same LangGraph thread. A request such as:

`Plan a 5-day trip from Kolkata to Delhi under ₹25,000.`

extracts route, duration and budget constraints, refreshes live Duffel flight offers, runs live accommodation search through Tavily, retrieves weather, recalculates a budget, and feeds the provider-backed results into the itinerary draft. A follow-up such as `Make it cheaper.` reuses the saved trip constraints and runs the flight and accommodation searches again in cost-first mode instead of merely rewriting the previous answer.

If a departure date is omitted, the planner searches a small set of near-term flexible windows and clearly labels the date strategy. For an actual booking, users should provide an exact travel date.

### Transaction layer

The project also contains a production-oriented payment/booking transaction layer:

1. Live flight search and transparent ranking.
2. Live offer recheck immediately before fulfilment.
3. Passenger validation and Duffel passenger IDs.
4. Secure Razorpay Checkout integration (optional until credentials are configured).
5. Server-side Razorpay payment signature verification.
6. Razorpay webhook verification endpoint.
7. Duffel order creation after verified payment.
8. PostgreSQL payment/booking state tracking.
9. Human confirmation and payment-before-booking safeguards.
10. Basic idempotency protection so a verified transaction is not booked twice.
11. My Trips transaction history endpoint/UI.
12. Duffel cancellation quote/confirmation plus Razorpay refund workflow when a customer payment exists.

### Production environment

Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` only on the server. Razorpay Checkout collects the customer's payment details in its hosted checkout; raw card data is not posted to this FastAPI application. Use Razorpay test keys for development and live keys only after completing provider onboarding/KYC.

For production flight fulfilment, use a production Duffel access token and a funded/approved Duffel account. The customer payment collected by Razorpay and the airline/provider payment used by Duffel are separate financial legs; the application records both states and must not claim a ticket is booked until the Duffel order succeeds.

## Environment files

- `.env.example` / `.env.test.example`: local sandbox development. Use a `duffel_test_...` token and Razorpay test keys.
- `.env.production.example`: production template. Use only after Duffel production access and Razorpay live-mode onboarding are complete.
- Never commit `.env` or real credentials.

Razorpay requires server-side order creation and server-side signature verification before fulfillment. Configure Razorpay webhooks on the deployed HTTPS URL for asynchronous payment status updates.

## Phase 2 — Payment and Flight Transaction Flow

Trust Trip MAS Phase 2 implements a human-confirmed flight transaction flow:

1. User selects a live Duffel offer.
2. Passenger details are collected in the browser.
3. The offer is rechecked immediately before payment.
4. If the fare changed, the user must explicitly confirm the new fare.
5. The server rechecks the offer again before creating the Razorpay order. The browser cannot choose the payable amount.
6. Razorpay Standard Checkout opens in the browser. Raw card/UPI credentials never reach FastAPI.
7. Razorpay returns `razorpay_payment_id`, `razorpay_order_id`, and `razorpay_signature`.
8. The server verifies the signature and fetches the payment to confirm it is `captured` and that the amount matches the server-side transaction.
9. PostgreSQL atomically claims the transaction so a browser refresh/retry cannot create a second airline order.
10. Duffel creates the supplier flight order using the agency/Duffel balance. The customer payment and supplier settlement are intentionally separate transaction legs.
11. `My Trips` shows payment, booking and refund state for the browser's customer reference.
12. Booked trips can request a Duffel cancellation quote and confirm cancellation. If the airline returns an INR refund amount, Trust Trip MAS can initiate an idempotent Razorpay refund up to the amount actually captured.

### Test mode

Use Razorpay Test Mode and a Duffel test token while developing. The application will not accept raw card details. Configure:

```env
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
DUFFEL_TEST_API_TOKEN / DUFFEL_LIVE_API_TOKEN=duffel_test_...
DUFFEL_ENVIRONMENT=test
```

For the Razorpay Checkout flow, the browser must be able to load `https://checkout.razorpay.com/v1/checkout.js`.

### Production mode

Use separate live credentials only after the Razorpay account is enabled for live payments and the Duffel organisation has production booking/funding access:

```env
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
DUFFEL_TEST_API_TOKEN / DUFFEL_LIVE_API_TOKEN=...
DUFFEL_ENVIRONMENT=production
```

Do not commit `.env` or live credentials.

### Important payment/booking boundary

Razorpay collects the customer payment. Duffel is the airline content/order provider and is funded through the agency's Duffel balance for this architecture. A successful Razorpay payment is therefore **not** treated as an airline booking until the Duffel order has been successfully created. If Duffel order creation times out, the transaction is marked `booking_ambiguous` and must not be blindly retried, because a supplier-side order may already exist.

## USD Duffel fares with INR Razorpay Checkout

Duffel test offers may be returned in USD while the customer checkout is INR.
The payment flow now:

1. Rechecks the live Duffel offer and validates its provider currency/price.
2. Gets the latest available USD/INR reference rate from Frankfurter (no API key required).
3. Converts the provider fare to INR and rounds the customer amount to paise-safe INR cents.
4. Shows the provider fare, INR customer amount, and exchange rate for a second final payment confirmation.
5. Creates the Razorpay order in INR using the converted amount.
6. Stores the provider amount/currency and exact FX rate/date/source in PostgreSQL.
7. After Razorpay signature + captured-amount verification, rechecks the supplier fare in the original provider currency before booking.
8. Uses the stored FX rate when a provider cancellation refund is returned in the provider currency, capped at the customer's captured INR amount.

The conversion service uses Frankfurter's public v2 rate endpoint. It is a latest-reference-rate source, not a guaranteed intraday market quote. If the rate service is unavailable, the payment order is refused rather than using a hard-coded exchange rate.


## Presentation Mode (test/demo only)

For a final-year-project demonstration, set `PRESENTATION_MODE=true` **only with Razorpay Test credentials and a Duffel test token**. If Razorpay Test Checkout is unavailable or returns `payment.failed`, the UI offers **Presentation Test Booking (Duffel TEST)**. That path creates an actual Duffel TEST booking using the sandbox balance, records it in My Trips, waits for the real Duffel test `booking_reference` when necessary, and enables the same downloadable booking-confirmation PDF. It does not collect a card number, charge real money, or create a live airline reservation.

If Razorpay Checkout itself fails, do not enter a real card. Use the presentation fallback. Keep `PRESENTATION_MODE=false` for staging and production.

## Live booking + test booking behavior

Trust Trip MAS now has two safe payment/booking modes:

### Production / Live

Set the Razorpay **Live** keys and Duffel **Live** access token in the deployment environment:

```text
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
DUFFEL_TEST_API_TOKEN / DUFFEL_LIVE_API_TOKEN=...
DUFFEL_ENVIRONMENT=production
PRESENTATION_MODE=false
```

Configure the Razorpay `payment.captured` webhook to:

```text
https://YOUR-PUBLIC-DOMAIN/api/payments/webhook
```

The browser still performs immediate server-side payment verification so the user gets a fast result. The webhook is the asynchronous safety net: if Checkout succeeds but the browser response is lost, the captured payment can still reach the same idempotent supplier-fulfilment path. Razorpay recommends using webhooks together with API verification for critical user-facing payment flows.

The flight supplier is funded through the configured Duffel/agency balance. Razorpay is the customer-payment leg; it is not a direct airline settlement channel.

### Test / Sandbox

Use separate Razorpay Test keys and a Duffel test token:

```text
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
# RAZORPAY_WEBHOOK_SECRET may be omitted for a local test
DUFFEL_TEST_API_TOKEN / DUFFEL_LIVE_API_TOKEN=duffel_test_...
DUFFEL_ENVIRONMENT=test
PRESENTATION_MODE=false
```

A Razorpay webhook is **not required** for the normal test Checkout flow. The backend verifies the Checkout signature, fetches the captured payment, and then creates the Duffel test booking. If Duffel accepts an order asynchronously, the application polls the existing supplier order; it never creates a second order. The UI also has **Refresh PNR** for a pending supplier confirmation.

### Real PNR and downloadable ticket

After Duffel returns a confirmed airline order, Trust Trip MAS stores the real Duffel order ID and airline booking reference/PNR. The UI shows the PNR in the booking card and My Trips. My Trips exposes **Download Ticket PDF**, which retrieves the current Duffel order and generates a downloadable booking confirmation containing the actual PNR, passenger and itinerary information, supplier total, payment reference and any ticket/document number that Duffel exposes.

The generated PDF is a booking confirmation/e-ticket receipt from the provider data; it is **not** a boarding pass. Airline check-in and boarding-pass issuance remain with the airline.

### Duplicate-booking protection

The transaction table is the single-flight lock. Checkout verification and Razorpay webhook delivery both call the same fulfilment function. Only a transaction in the paid-awaiting-booking state can be claimed. If a supplier create request times out or returns an ambiguous HTTP 500, the application attempts to locate an existing order by offer ID before allowing any other action; it never blindly retries an airline order.

### Important financial requirement

For a real live booking, the Duffel organisation/agency must have the appropriate commercial setup and sufficient supplier balance for the chosen live offer. A Razorpay customer payment alone does not grant Trust Trip MAS permission to settle an airline order through Duffel.

## Passenger validation fix

The booking form now collects the passenger title and gender required by Duffel and validates given/family names before supplier fulfilment. Names are limited to 40 characters and accept letters, spaces, hyphens, and apostrophes. Invalid passenger data is rejected before a Duffel order is attempted.

## Production Chat Planner (v4)

The travel chatbot now uses a dedicated `services/chat_planner.py` boundary.

- **Groq is primary:** `openai/gpt-oss-120b` by default.
- **Gemini is fallback:** `gemini-2.5-flash-lite` only when Groq fails.
- **No Ollama / no third LLM provider.**
- **One LLM generation per draft or human-feedback revision.** Specialist agents are not rerun when feedback is submitted, so flight suggestions remain unchanged during an itinerary revision.
- The model receives provider evidence and a deterministic budget. It cannot invent flight/hotel prices, availability, weather, or booking references.
- Output is validated against a Pydantic structured schema and rendered as a structured itinerary in the UI.
- Human feedback routes back to the itinerary node, generates a new structured plan, and pauses for review again. Approving the plan moves to the final response.
- `MAX_PLAN_REVISIONS` limits repeated revisions (default 5).

### LLM environment

```env
LLM_PROVIDER=groq
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash-lite
MAX_PLAN_REVISIONS=5
```

Install with:

```bash
pip install -r requirements.txt
```

Keep `.env` local and never commit API keys.

## Dual-provider Hotel Booking Agent (v10)

Trust Trip now has a real Hotel Agent using two provider adapters behind one `HotelService`:

- **LiteAPI / Nuitee Connect** — sandbox and production keys; search → prebook → book.
- **Hotelbeds / HBX Group** — test and production credentials; search/check-rate/book where the required mTLS client certificate is configured.

The Hotel Agent normalizes provider offers, converts foreign-currency prices to INR for display/budgeting, preserves the provider amount/currency as the source of truth, and ranks offers using budget fit, price, rating, refundability and board information.

### Hotel environment variables

See `.env.example` for the full list. Never commit real credentials.

```env
LITEAPI_MODE=sandbox
LITEAPI_SANDBOX_API_KEY=
LITEAPI_PRODUCTION_API_KEY=
LITEAPI_BOOK_PAYMENT_METHOD=ACC_CREDIT_CARD

HOTELBEDS_MODE=test
HOTELBEDS_API_KEY=
HOTELBEDS_SECRET=
HOTELBEDS_CLIENT_CERT=
HOTELBEDS_CLIENT_KEY=
HOTELBEDS_DESTINATION_CODES_JSON={}
```

Hotelbeds currently documents mutual TLS for hotel availability, CheckRate and booking operations; the adapter therefore reports a clear configuration error instead of pretending the key alone is sufficient. LiteAPI documents a sandbox search → prebook → book workflow. 

### Hotel booking/payment flow

```text
Chat / Manual Hotel Search
        ↓
Hotel Agent
        ↓
LiteAPI + Hotelbeds
        ↓
Normalized + ranked hotel cards
        ↓
Select hotel
        ↓
Provider prebook / final availability check
        ↓
Razorpay customer payment (INR)
        ↓
Server-side signature verification
        ↓
Supplier booking
        ↓
Hotel confirmation
```

In LiteAPI sandbox, `ACC_CREDIT_CARD` is used only to simulate the supplier-side booking after Trust Trip's Razorpay test payment. In production, configure an enabled supplier payment path such as the account's wallet/credit arrangement; Razorpay is the customer-facing payment layer and is not silently treated as the supplier's funding method.
