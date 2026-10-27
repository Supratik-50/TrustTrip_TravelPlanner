# TrustTrip — AI Travel Planner & Booking Platform

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18.3-61DAFB.svg)](https://react.dev/)
[![TypeScript 5.6](https://img.shields.io/badge/TypeScript-5.6-3178C6.svg)](https://www.typescriptlang.org/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791.svg)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose%20v2-2496ED.svg)](https://www.docker.com/)

TrustTrip is an intelligent, full-stack travel planning and booking platform. It combines a multi-agent supervisor system, retrieval-augmented generation (RAG) powered by PostgreSQL `pgvector`, and live inventory provider integrations (Duffel for flights, LiteAPI and Hotelbeds for hotels). TrustTrip supports human-in-the-loop (HITL) revision and approval workflows, budget constraint enforcement, Razorpay payment processing, HttpOnly cookie authentication, and official PDF ticket/receipt generation.

---

## Table of Contents

1. [What TrustTrip Does](#what-trusttrip-does)
2. [Key Features](#key-features)
3. [System Architecture](#system-architecture)
4. [Technology Stack](#technology-stack)
5. [Prerequisites & Supported Versions](#prerequisites--supported-versions)
6. [Repository Directory Structure](#repository-directory-structure)
7. [Installation & Local Development](#installation--local-development)
8. [Environment Configuration Reference](#environment-configuration-reference)
9. [Running Locally](#running-locally)
   - [Workflow A: Multi-Container Docker Compose (Recommended)](#workflow-a-multi-container-docker-compose-recommended)
   - [Workflow B: Local Process Development (PowerShell)](#workflow-b-local-process-development-powershell)
10. [Database Setup, Migrations & Persistence](#database-setup-migrations--persistence)
11. [Authentication & Security Model](#authentication--security-model)
12. [API Overview & Endpoints](#api-overview--endpoints)
13. [End-to-End User Workflow](#end-to-end-user-workflow)
14. [Flight & Hotel Provider Configuration](#flight--hotel-provider-configuration)
15. [Payments, Settlement & Receipt Veracity](#payments-settlement--receipt-veracity)
16. [Testing & Quality Checks](#testing--quality-checks)
17. [Troubleshooting](#troubleshooting)
18. [Deployment Architecture](#deployment-architecture)
19. [Known Limitations & Roadmap](#known-limitations--roadmap)
20. [Contributing & Development Guidelines](#contributing--development-guidelines)
21. [License & Acknowledgements](#license--acknowledgements)

---

## What TrustTrip Does

TrustTrip automates the travel planning lifecycle while keeping the traveler in full control:

1. **Understands Natural Intent:** Accepts freeform travel criteria (origin, destination, dates, traveler count, target budget, and personal preferences).
2. **Gathers Verified Evidence:** Queries real-time flight inventory (Duffel), hotel accommodations (LiteAPI/Hotelbeds), live weather, and contextual destination research (Tavily Search + pgvector RAG).
3. **Synthesizes Cohesive Itineraries:** Generates structured, day-by-day travel plans using high-performance primary LLMs (Groq `gpt-oss-120b`) with automatic fallback to secondary LLMs (Google Gemini `2.5-flash-lite`).
4. **Enforces Human-in-the-Loop Revisions:** Allows travelers to propose modifications, inspect structured added/removed diffs between revisions, and strictly enforces canonical budget constraints before approval.
5. **Coordinates Bookings & Payments:** Supports flight seat reservations and hotel bookings with rate revalidation, Razorpay Indian Rupee (INR) customer payments, and supplier settlement tracking.
6. **Delivers Official Documents:** Produces downloadable PDF itineraries, flight e-tickets, hotel check-in vouchers, and truthful financial receipts.

---

## Key Features

### 1. Multi-Agent AI Travel Planner
- **Supervisor Orchestration:** Coordinates specialized tool execution (Flight, Hotel, Weather, Budget, Itinerary) without hallucinations.
- **Fail-Closed Fallback Routing:** Primary inference executes against Groq (`openai/gpt-oss-120b`). If rate-limited or unavailable, requests automatically fall back to Google Gemini (`gemini-2.5-flash-lite`).
- **Strict Pydantic Validation:** All LLM outputs are validated against structured schemas; unparseable models fail closed before reaching the client.

### 2. Evidence-Backed Research & pgvector RAG
- **Live Destination Intelligence:** Retrieves destination points of interest, transit advice, and accommodation options via Tavily Web Search.
- **Vector Search:** Persists embeddings in PostgreSQL using `pgvector` (`rag_documents`, `rag_chunks`) for context retrieval across planning turns.
- **Citation Tracking:** Surfaces sources in an interactive `EvidenceDrawer` component in the frontend.

### 3. Trip Revision & Version Control
- **Monotonically Numbered Revisions:** Every itinerary change creates an immutable proposal (`plan_revisions`) linked to its base revision.
- **Structured Diff Comparison:** Visualizes added, removed, and modified activities, hotel stays, and transportation legs between any two revisions.
- **Budget Guardrails:** Approvals are blocked if an itinerary exceeds the canonical trip budget, requiring explicit user budget-override confirmation.

### 4. Flight & Hotel Provider Integrations
- **Flight Shopping (Duffel API):** Real-time flight search, multi-passenger selection, baggage and cabin class parsing, immediate pre-booking fare rechecks, and Duffel sandbox/provider settlement.
- **Dual-Provider Accommodation (LiteAPI & Hotelbeds):** LiteAPI Nuitee Connect integration for sandbox and production rate lookups; Hotelbeds adapter with mutual TLS (mTLS) configuration guardrails.
- **Prebook Rate Lock:** Guarantees accommodation rate validity before opening the customer checkout flow.

### 5. Truthful Financial & Document Settlement
- **Razorpay Payment Gateway:** Integrated Razorpay standard checkout in INR, server-side HMAC-SHA256 signature verification, and atomic database transaction claiming.
- **Settlement Decoupling:** Explicitly distinguishes customer payment capture (Razorpay) from supplier booking fulfillment (Duffel balance / LiteAPI credit).
- **Payment Receipt Veracity:** Generates ReportLab PDF receipts that only claim Razorpay capture when verified `pay_...` identifiers exist. Direct provider bookings truthfully display `Direct Provider Settlement` without fabricated payment gateway lines.

### 6. Production-Grade Authentication & Session Security
- **HttpOnly Secure Cookies:** Authentication access tokens are delivered via `SameSite=Lax`, `Path=/api`, `HttpOnly`, and `Secure` cookies. Zero JWTs are stored in browser `localStorage`.
- **CSRF Protection:** State-changing requests enforce origin checks against `ALLOWED_ORIGINS` and require custom `X-Requested-With: XMLHttpRequest` or `X-CSRF-Token` headers.
- **Cryptographic Secret Separation:** Dedicated `JWT_SECRET` isolated from payment credentials, preventing cross-domain token forge attacks.
- **Server-Side Token Revocation:** Explicit logout invalidates tokens immediately in a persistent PostgreSQL `revoked_tokens` table.

---

## System Architecture

TrustTrip operates as a multi-tier containerized system with strict boundaries between presentation, application logic, persistence, and external providers.

```mermaid
flowchart TD
    subgraph Client["Browser (React 18 SPA)"]
        UI["React UI / Tailwind / Lucide"]
        AuthCtx["AuthContext (Cookie-First)"]
        ApiClient["Axios (withCredentials: true, X-Requested-With)"]
    end

    subgraph Ingress["Web & Reverse Proxy"]
        Nginx["Nginx Reverse Proxy (Port 80)"]
    end

    subgraph Backend["FastAPI Application (Port 8000, 1 Worker)"]
        App["app.py / Lifespan & Middlewares"]
        AuthApi["auth_api.py (HttpOnly Cookies & CSRF)"]
        TripsApi["trips_api.py (Plans, Diffs, Approvals)"]
        FlightApi["flight_api.py (Duffel Provider)"]
        HotelApi["hotel_api.py (LiteAPI & Hotelbeds)"]

        Planner["services.chat_planner / PlannerService"]
        AuthStore["services.auth_store (UserStore & TokenRevocationStore)"]
        TripStore["services.trip_store (PostgreSQL Revisions)"]
        PDFGen["services.payment_receipt_pdf (ReportLab PDFs)"]
    end

    subgraph Storage["PostgreSQL 16 + pgvector (Port 5432/5433)"]
        BaseDB[("Base Tables: users, trips, threads")]
        LineageDB[("011: trust_trip_transactions, hotel_transactions")]
        VectorDB[("006: rag_documents, rag_chunks, vector")]
        RevokeDB[("016: revoked_tokens")]
    end

    subgraph External["External APIs & Intelligence"]
        Groq["Groq API (Primary: gpt-oss-120b)"]
        Gemini["Google Gemini (Fallback: 2.5-flash-lite)"]
        Tavily["Tavily Search API (Evidence)"]
        Duffel["Duffel API (Flight Inventory & Orders)"]
        LiteAPI["LiteAPI (Hotel Sandbox / Production)"]
        Razorpay["Razorpay (Payment Gateway & Orders)"]
    end

    UI --> AuthCtx --> ApiClient
    ApiClient -->|HTTP Requests| Nginx
    Nginx -->|Static Assets| UI
    Nginx -->|/api/* & /ready Proxy| App

    App --> AuthApi
    App --> TripsApi
    App --> FlightApi
    App --> HotelApi

    AuthApi --> AuthStore
    TripsApi --> Planner
    TripsApi --> TripStore
    TripsApi --> PDFGen
    FlightApi --> Duffel
    HotelApi --> LiteAPI
    HotelApi --> Razorpay

    Planner --> Groq
    Planner -.->|Fallback on 429/Error| Gemini
    Planner --> Tavily

    AuthStore --> BaseDB
    AuthStore --> RevokeDB
    TripStore --> BaseDB
    TripStore --> LineageDB
    Planner --> VectorDB
```

---

## Technology Stack

| Layer | Technologies | Purpose |
| :--- | :--- | :--- |
| **Frontend** | React 18, TypeScript 5.6, Vite 6, Tailwind CSS, Lucide Icons | Responsive single-page application, interactive revision diffs, booking panels |
| **Frontend Testing** | Vitest, Testing Library | Unit and component testing for auth, currency truthfulness, and forms |
| **Backend Framework** | Python 3.11+, FastAPI, Starlette, Pydantic v2, Uvicorn | Async REST API, middleware security, schema validation |
| **Backend Testing** | Pytest, Pytest-Asyncio, HTTPX / Starlette TestClient | Unit, regression, lifecycle, and security test suites (314+ passing in test catalog) |
| **Database & Search** | PostgreSQL 16, `pgvector` extension, `asyncpg`, `psycopg` | Relational persistence, transactional revisions, vector embeddings |
| **AI / LLM Orchestration** | LangGraph, LangChain, Groq SDK, Google Generative AI | Multi-agent planning, fallback synthesis, Pydantic guardrails |
| **Travel Providers** | Duffel API, LiteAPI (Nuitee), Hotelbeds API, Tavily API | Flight shopping, hotel booking, destination web research |
| **Payments & Documents** | Razorpay Python SDK, ReportLab | Payment order initialization, HMAC verification, PDF document generation |
| **Container & Proxy** | Docker, Docker Compose, Nginx (Alpine), Cloudflared | Container orchestration, reverse proxy routing, secure tunnel ingress |

---

## Prerequisites & Supported Versions

Before running TrustTrip, ensure your environment meets the following requirements:

- **Operating System:** Windows 10/11 (with PowerShell 5.1 or PowerShell 7+), macOS 13+, or Ubuntu 22.04/24.04 LTS.
- **Python:** Version `3.11` or `3.12` installed.
- **Node.js:** Version `18.x` or `20.x` LTS with `npm 9+`.
- **Docker:** Docker Desktop v24+ with Docker Compose v2.20+.
- **PostgreSQL (for non-Docker execution):** PostgreSQL 15+ with `pgvector` compiled and available.
- **Git:** Git 2.35+.

---

## Repository Directory Structure

```text
TrustTrip_TravelPlanner/
├── .dockerignore                       # Exclusions for backend Docker builds
├── .env.example                        # Template environment variables (placeholders only)
├── .gitignore                          # Narrowly scoped ignore patterns (credentials, dumps, db)
├── Dockerfile                          # Production backend Dockerfile (FastAPI, 1 worker)
├── docker-compose.yml                  # Production stack (postgres, backend, web, cloudflared)
├── requirements.txt                    # Pinned Python backend dependencies
├── app.py                              # FastAPI application entrypoint, lifespan, CORS, CSRF
├── backend.py                          # Core database lifecycle and connection pools
├── auth_api.py                         # Authentication endpoints (HttpOnly cookies, login, logout)
├── trips_api.py                        # Trip management, revision plans, approvals, receipts
├── flight_api.py                       # Flight search, recheck, booking with Duffel
├── hotel_api.py                        # Hotel search, prebook, Razorpay checkout, booking
│
├── dependencies/
│   └── auth.py                         # Cookie-first authentication dependency & JWT validation
│
├── domain/
│   ├── user.py                         # User serialization and schema models
│   ├── trip.py                         # Trip and revision entity models
│   └── booking.py                      # Booking intent and transaction models
│
├── services/
│   ├── auth_service.py                 # Password hashing, JWT minting, cookie configuration
│   ├── auth_store.py                   # UserStore and persistent TokenRevocationStore
│   ├── chat_planner.py                 # Multi-agent LLM planning and fallback logic
│   ├── planner_service.py              # Itinerary generation stability and duration engine
│   ├── config.py                       # Strict production configuration validation
│   ├── duffel_provider.py              # Duffel flight supplier integration
│   ├── payment_receipt_pdf.py          # Truthful ReportLab payment receipt PDF generation
│   ├── search_session_store.py         # Session caching and database schema verification
│   ├── trip_store.py                   # PostgreSQL trip and plan revision persistence
│   └── migration_runner.py             # Automatic sequential SQL migration runner
│
├── migrations/                         # Idempotent sequential SQL schema migrations
│   ├── 004_travel_intelligence.sql
│   ├── 005_evidence_revision_lifecycle.sql
│   ├── 006_research_and_rag.sql
│   ├── 007_flight_booking_lifecycle.sql
│   ├── 008_research_evidence_nullable_metadata.sql
│   ├── 011_payment_transaction_lineage.sql
│   └── 016_jwt_revocation.sql
│
├── frontend/                           # React 18 + Vite frontend
│   ├── Dockerfile                      # Multi-stage production build (Node build -> Nginx)
│   ├── nginx.conf                      # Nginx reverse proxy routing /api to backend:8000
│   ├── package.json                    # Frontend dependencies (React, Lucide, Vitest)
│   ├── vite.config.ts                  # Vite development configuration & dev proxy
│   └── src/
│       ├── components/travel/          # UI components (RevisionManager, EvidenceDrawer, Cards)
│       ├── context/AuthContext.tsx     # Cookie authentication provider
│       ├── lib/apiClient.ts            # Axios client withCredentials: true & CSRF headers
│       └── services/bookingService.ts  # Booking and payment API client
│
└── tests/                              # Automated test suites (unit, integration, and security suites)
    ├── test_cookie_auth_lifecycle.py
    ├── test_jwt_revocation.py
    ├── test_jwt_secret_separation.py
    ├── test_payment_receipt_veracity.py
    ├── test_itinerary_duration_and_fallback_remediation.py
    └── test_release_readiness_audit.py
```

---

## Installation & Local Development

### 1. Clone the Repository
Open Windows PowerShell and clone the project:

```powershell
git clone https://github.com/Supratik-50/TrustTrip_TravelPlanner.git
cd TrustTrip_TravelPlanner
```

### 2. Set Up Python Virtual Environment
Create and activate an isolated virtual environment:

```powershell
# Create virtual environment
python -m venv venv

# Activate on Windows PowerShell
.\venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Set Up Frontend Dependencies
Install npm packages in the `frontend` directory:

```powershell
cd frontend
npm install
cd ..
```

### 4. Create Local Configuration
Copy `.env.example` to `.env`. Keep `.env` strictly local:

```powershell
Copy-Item .env.example .env
```

---

## Environment Configuration Reference

The following table documents all runtime variables defined in `.env.example`:

| Variable | Required? | Default / Example | Purpose |
| :--- | :---: | :--- | :--- |
| `APP_ENV` | **Yes** | `development` / `production` | Set to `production` for live deployments to enforce persistent secrets. |
| `JWT_SECRET` | **Yes** | Min 32 characters random string | Secret key for signing user authentication JWT tokens. |
| `PAYLOAD_ENCRYPTION_KEY` | **Yes** | 32-byte base64 string | Key used for encrypting search session payloads in the database. |
| `DATABASE_URL` | **Yes** | `postgresql://user:pass@host:5432/db` | PostgreSQL connection string with `pgvector` enabled. |
| `ALLOWED_ORIGINS` | Optional | `http://localhost:3000,http://localhost:8000` | Comma-separated list of allowed CORS / CSRF browser origins. |
| `LLM_PROVIDER` | Optional | `groq` | Primary LLM provider (`groq`). |
| `GROQ_API_KEY` | Optional | `gsk_...` | API key for Groq Cloud inference. |
| `GROQ_MODEL` | Optional | `openai/gpt-oss-120b` | Primary high-parameter planning model. |
| `GROQ_FALLBACK_MODEL` | Optional | `qwen/qwen3.8-27b` | Secondary Groq fallback model. |
| `GEMINI_API_KEY` | Optional | `AIzaSy...` | Fallback API key for Google Gemini inference. |
| `GEMINI_MODEL` | Optional | `gemini-2.5-flash-lite` | Fallback model used when Groq is throttled. |
| `TAVILY_API_KEY` | Optional | `tvly-...` | Web search API key for destination research and RAG context. |
| `DUFFEL_API_TOKEN` | Optional | `duffel_test_...` | Duffel access token for flight shopping. |
| `DUFFEL_MODE` | Optional | `test` | Duffel mode (`test` or `live`). |
| `RAZORPAY_KEY_ID` | Optional | `rzp_test_...` | Razorpay public key ID for customer checkout orders. |
| `RAZORPAY_KEY_SECRET` | Optional | `...` | Razorpay secret key for server-side signature verification. |
| `LITEAPI_SANDBOX_API_KEY`| Optional | `...` | LiteAPI Nuitee Connect hotel sandbox key. |
| `LITEAPI_MODE` | Optional | `sandbox` | LiteAPI mode (`sandbox` or `production`). |
| `TUNNEL_TOKEN` | Optional | `...` | Cloudflare Named Tunnel token for zero-ingress deployments. |

> [!TIP]
> Generate a secure `PAYLOAD_ENCRYPTION_KEY` on PowerShell:
> ```powershell
> python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
> ```

---

## Running Locally

### Workflow A: Multi-Container Docker Compose (Recommended)

Docker Compose starts PostgreSQL, the FastAPI backend, and the Nginx web container as a coordinated stack:

1. **Start Stack:**
   ```powershell
   docker compose up -d --build
   ```
2. **Verify Container Health:**
   ```powershell
   docker compose ps
   ```
   All three core containers will report `(healthy)`:
   - `trusttrip-web`: Accessible at `http://localhost:80` (or `http://127.0.0.1:80`).
   - `trusttrip-backend`: Accessible internally over `trusttrip-net:8000` and proxied at `http://localhost:80/api/`.
   - `trusttrip-postgres`: Running on container port `5432` and exposed to the host at `127.0.0.1:5433`.
3. **Inspect Logs:**
   ```powershell
   docker compose logs -f backend
   ```
4. **Stop Stack (Preserving Database Volume):**
   ```powershell
   docker compose down
   ```

### Workflow B: Local Process Development (PowerShell)

If developing locally without Docker:

1. **Ensure Local PostgreSQL with pgvector is Running:**
   Set `DATABASE_URL` in `.env` to your local instance (e.g. `postgresql://postgres:postgres@localhost:5432/trusttrip`).
2. **Start Backend (Terminal 1):**
   ```powershell
   .\venv\Scripts\Activate.ps1
   uvicorn app:app --host 127.0.0.1 --port 8000 --reload --workers 1
   ```
3. **Start Frontend (Terminal 2):**
   ```powershell
   cd frontend
   npm run dev
   ```
   Open `http://localhost:3000` in your browser. Requests to `/api/*` are automatically proxied to `http://localhost:8000` via `frontend/vite.config.ts`.

---

## Database Setup, Migrations & Persistence

### 1. The pgvector Requirement
TrustTrip utilizes PostgreSQL vector embeddings for destination research and RAG chunks. The database must have the `vector` extension available:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 2. Automatic Migration Runner
The application includes a built-in migration runner (`services/migration_runner.py`). Migrations execute sequentially on startup or can be executed manually:

```powershell
.\venv\Scripts\Activate.ps1
python -m services.migration_runner
```

#### Migration Sequence:
- `004_travel_intelligence.sql`: Search sessions, offer snapshots, revalidation history.
- `005_evidence_revision_lifecycle.sql`: Plan revisions, evidence records, revision links, approvals.
- `006_research_and_rag.sql`: Vector extension, research sessions, RAG documents and chunks.
- `007_flight_booking_lifecycle.sql`: Booking intents, attempts, and state transitions.
- `008_research_evidence_nullable_metadata.sql`: Nullable metadata enhancements.
- `011_payment_transaction_lineage.sql`: Transaction revision lineage and payment idempotency keys.
- `016_jwt_revocation.sql`: Persistent token revocation tracking (`revoked_tokens`).

### 3. Safe Database Backup & Disaster Recovery
TrustTrip's authoritative PostgreSQL database must be backed up using PostgreSQL custom-format (`-Fc`):

```powershell
# Non-destructive backup of running container database
docker exec -i trusttrip-postgres pg_dump -U trusttrip -d trusttrip -Fc > backups/trusttrip_backup_manual.dump
```
Verify the backup file integrity:
```powershell
docker exec -i trusttrip-postgres pg_restore --list backups/trusttrip_backup_manual.dump
```

---

## Authentication & Security Model

TrustTrip implements a defense-in-depth security architecture:

1. **HttpOnly Cookie Authentication:**
   - On successful `/api/auth/login` or `/api/auth/signup`, the backend sets a `trusttrip_access_token` cookie with `HttpOnly=True`, `SameSite=Lax`, `Path=/api`, and `Secure=True` in production.
   - Browser JavaScript cannot access the session token, preventing Cross-Site Scripting (XSS) credential theft.
2. **CSRF Protection:**
   - All state-changing requests (POST, PUT, DELETE, PATCH) enforce browser Origin verification against `ALLOWED_ORIGINS`.
   - Mutating requests must supply custom headers (`X-Requested-With: XMLHttpRequest` or `X-CSRF-Token`), which browsers prohibit cross-origin requests from forging without explicit CORS preflight permission.
3. **Server-Side Token Revocation:**
   - `/api/auth/logout` extracts the token's JTI and inserts it into the `revoked_tokens` table.
   - All subsequent requests with that token are rejected with `HTTP 401 Unauthorized`.
4. **Secret Separation:**
   - `JWT_SECRET` is maintained independently from `RAZORPAY_KEY_SECRET` and supplier keys, eliminating token forgery risks if a third-party key is compromised.

---

## API Overview & Endpoints

All primary API routes are prefixed with `/api`. OpenAPI documentation is available in development mode at `http://localhost:8000/docs`.

### Health & Readiness Probes
- `GET /health`: Basic process liveness and version info.
- `GET /api/health`: Proxied liveness probe.
- `GET /ready`: Authoritative database readiness check (performs `SELECT 1` without modifying schema).

### Authentication (`/api/auth`)
- `POST /api/auth/signup`: Register new account and issue `trusttrip_access_token` session cookie.
- `POST /api/auth/login`: Authenticate user credentials and issue session cookie.
- `POST /api/auth/logout`: Invalidate token JTI in `revoked_tokens` table and clear cookie.
- `GET /api/auth/me`: Retrieve current authenticated user profile.

### Travel Planning & Conversational Supervisor (`/api/travel`, `/api/trips`)
- `POST /api/travel`: Interactive multi-turn travel planning agent with supervisor coordination.
- `POST /api/travel/pdf`: Generate and download comprehensive day-by-day itinerary PDF (`{"trip_id": ...}` or `{"plan": ...}`).
- `GET /api/trips`: List user's active trips, booked flights, and hotel stays.
- `GET /api/trips/{trip_id}`: Retrieve trip details, constraints, and revision history.
- `POST /api/trips/{trip_id}/revisions`: Propose a new structured plan revision.
- `POST /api/trips/{trip_id}/revisions/{revision_id}/plan`: Generate or recompute itinerary for revision.
- `POST /api/trips/{trip_id}/approve`: Approve revision (with budget override validation).
- `POST /api/trips/{trip_id}/revisions/{revision_id}/reject`: Reject current revision with structured feedback notes.
- `POST /api/trips/{trip_id}/booking-approval`: Authorize flight/hotel bookings for approved plan.
- `GET /api/trips/{trip_id}/booking-eligibility`: Validate whether booking criteria are satisfied.

### Flight Inventory & Booking (`/api/flights`)
- `POST /api/flights/search`: Search flight offers across routes and dates via Duffel.
- `POST /api/flights/search-flexible`: Search flexible date ranges.
- `POST /api/flights/recheck`: Revalidate offer price and seat availability before checkout.
- `POST /api/flights/book`: Execute supplier booking (requires `final_confirmation=true`).
- `POST /api/flights/booking/select`: Select offer for booking workflow.
- `POST /api/flights/booking/{booking_id}/confirm`: Confirm flight reservation.

### Hotel Inventory & Prebooking (`/api/hotels`)
- `GET /api/hotels/config`: Inspect hotel provider runtime modes.
- `POST /api/hotels/search`: Search accommodations across destinations.
- `POST /api/hotels/select`: Select hotel offer.
- `POST /api/hotels/prebook`: Lock hotel rate and obtain authoritative `prebook_id`.

### Payments & Financial Checkout (`/api/payments`, `/api/hotel-payments`)
- `GET /api/payments/config`: Check Razorpay gateway configuration status.
- `POST /api/payments/create-order`: Create Razorpay INR payment order for flight booking.
- `POST /api/payments/verify-and-book`: Verify Razorpay signature and commit flight booking.
- `POST /api/hotel-payments/create-order`: Create Razorpay INR order for prebooked hotel stay.
- `POST /api/hotel-payments/verify-and-book`: Verify payment signature and complete hotel supplier booking.
- `POST /api/payments/webhook`: Webhook endpoint for Razorpay postback verification.

### Document Delivery (`/api/trips`, `/api/travel`)
- `GET /api/trips/{transaction_id}/ticket.pdf`: Download verified flight e-ticket PDF.
- `GET /api/trips/{transaction_id}/hotel.pdf`: Download verified hotel check-in voucher PDF.
- `GET /api/trips/{transaction_id}/receipt.pdf`: Download truthful payment receipt PDF.
- `POST /api/travel/pdf`: Download comprehensive day-by-day itinerary PDF.

---

## End-to-End User Workflow

```text
1. Sign Up / Login
   └── Secure session established via HttpOnly cookie.

2. Trip Inception (/api/travel)
   └── User: "Plan a 5-day culture trip to Kyoto under ₹70,000 for 2 travelers."
   └── Supervisor Agent coordinates:
       ├── Duffel Flight Tool (Finds SFO -> KIX options)
       ├── LiteAPI Hotel Tool (Locates ryokan/hotel rates)
       ├── Tavily Search + RAG (Extracts temple hours and seasonal advice)
       └── Groq LLM Synthesizer (Generates structured Day 1 - Day 5 plan)

3. Review & Revision (/trips/:id)
   └── Traveler reviews draft in TripRevisionManager.
   └── Requests changes: "Remove Day 3 temple tour, add Arashiyama bamboo grove."
   └── New Revision (Rev 2) created; UI displays added/removed diff.

4. Human-in-the-Loop Approval
   └── System verifies estimated cost against target budget.
   └── If over budget, traveler must check "Confirm Budget Override".
   └── Revision approved and marked active.

5. Booking & Payment Execution
   └── Flight: Selected offer is rechecked for live price stability.
   └── Hotel: Prebook locks room rate; Razorpay order initialized in INR.
   └── Traveler completes Razorpay Checkout.
   └── Backend verifies HMAC signature and confirms supplier reservation.

6. Document Download (/my-trips)
   └── Download Flight E-Ticket (with confirmed Duffel PNR).
   └── Download Hotel Voucher (with supplier confirmation reference).
   └── Download Truthful Payment Receipt (detailing payment status and gateway).
```

---

## Flight & Hotel Provider Configuration

TrustTrip connects to external travel inventory providers with strict fail-closed safeguards:

### Duffel Flights
- **Test Mode (`DUFFEL_MODE=test`):** Uses predictable sandbox routes. Bookings are completed using Duffel's sandbox test balance (`sandbox_balance`). No real money is charged, and no airline tickets are issued.
- **Live Mode (`DUFFEL_MODE=live`):** Requires an accredited agency agreement and a funded Duffel balance. The application blocks live bookings if test tokens are detected.

### LiteAPI Accommodations
- **Sandbox Mode (`LITEAPI_MODE=sandbox`):** Queries live inventory pricing while bookings are simulated via test billing methods (`ACC_CREDIT_CARD`).
- **Production Mode:** Requires approved supplier merchant accounts and enabled credit facilities.

### Hotelbeds Accommodations
- Requires mutual TLS (mTLS) certificates (`HOTELBEDS_CLIENT_CERT`, `HOTELBEDS_CLIENT_KEY`) in addition to API keys. The application fails closed with a descriptive configuration error if certificates are missing.

---

## Payments, Settlement & Receipt Veracity

TrustTrip enforces strict separation between customer payments and supplier fulfillment:

```text
[ Traveler ] ──── (1) INR Payment ────> [ Razorpay Gateway ]
                                               │
                                       (2) Signature Verified
                                               │
                                               ▼
[ TrustTrip Backend ] ── (3) Supplier Balance ──> [ Duffel / LiteAPI ]
                                                         │
                                                 (4) Confirmed PNR
```

1. **Independent Transaction Legs:**
   - A customer payment captured on Razorpay is **not** an airline or hotel confirmation.
   - Supplier bookings are completed separately using the agency's commercial provider balance.
2. **Truthful Receipt Presentation:**
   - If an order has a verified `razorpay_payment_id`, the receipt reports `Payment Status: CAPTURED` via `Razorpay Payment Gateway (Secured)`.
   - If a flight was booked via direct Duffel balance or sandbox balance without Razorpay checkout, the receipt states `Settlement Method: Direct Provider Settlement` with `Status: SETTLED DIRECT VIA SUPPLIER`. It never prints false Razorpay order lines or claims.

---

## Testing & Quality Checks

TrustTrip maintains a comprehensive automated testing suite across backend and frontend:

### 1. Backend Pytest Suites
Run the full backend test suite:
```powershell
.\venv\Scripts\Activate.ps1
python -m pytest -v
```

Execute focused core and regression test suites:
```powershell
python -m pytest `
  tests/test_payment_receipt_veracity.py `
  tests/test_cookie_auth_lifecycle.py `
  tests/test_jwt_revocation.py `
  tests/test_jwt_secret_separation.py `
  tests/test_itinerary_duration_and_fallback_remediation.py `
  tests/test_section10_booking_payment.py `
  tests/test_hotel_booking_integration.py `
  tests/test_section8_hitl_revisions.py `
  tests/test_migration_lifecycle.py `
  tests/test_release_readiness_audit.py -v
```
*(Recorded audit runs verify core flows across the test catalog, including authentication lifecycle, JWT revocation, itinerary duration invariants, and booking state machines; over 314+ backend unit and integration tests recorded passing).*

### 2. Frontend Vitest Suites
Run all React component and unit test suites:
```powershell
cd frontend
npm test -- --run
cd ..
```
*(Recorded test output: 18 test suites passing in `frontend/src/__tests__/`, covering cookie auth, passenger validation, revision client diffs, and currency truthfulness).*

### 3. Frontend TypeScript & Production Build
Validate types and compile production assets:
```powershell
cd frontend
npx tsc --noEmit
npm run build
cd ..
```

### 4. Docker Compose Validation
Verify multi-container syntax and service definitions:
```powershell
docker compose config --quiet
```

---

## Troubleshooting

### 1. Database Connection Refused
- **Symptom:** `asyncpg.exceptions.CannotConnectNowError` or `Connection refused on port 5432`.
- **Cause:** When running the backend on the host while PostgreSQL is inside Docker, Docker maps the database port to host port `5433` (not `5432`).
- **Fix:** Update `.env` to `DATABASE_URL=postgresql://trusttrip:trusttrip_secure_pass@localhost:5433/trusttrip`. (Inside Docker Compose, services connect via `postgres:5432`).

### 2. CSRF Validation Failed: Origin Not Allowed
- **Symptom:** API returns `403 Forbidden` with `"CSRF validation failed: origin not allowed"`.
- **Cause:** The browser `Origin` or `Referer` does not match the configured `ALLOWED_ORIGINS`.
- **Fix:** Add your browser URL to `ALLOWED_ORIGINS` in `.env` (e.g. `ALLOWED_ORIGINS=http://localhost:3000,http://localhost:80,http://127.0.0.1`). Ensure requests include the `X-Requested-With: XMLHttpRequest` header.

### 3. Missing Mandatory Production Secret
- **Symptom:** Backend crashes on startup with `RuntimeError: Mandatory production security variables missing`.
- **Cause:** `APP_ENV=production` is set, but `JWT_SECRET` or `PAYLOAD_ENCRYPTION_KEY` is empty.
- **Fix:** Supply persistent keys in `.env` as documented in [Environment Configuration Reference](#environment-configuration-reference).

### 4. Duffel / LiteAPI 502 Bad Gateway
- **Symptom:** Booking or search returns `HTTP 502` or `HTTP 504`.
- **Cause:** Upstream supplier sandbox latency or expired offer token.
- **Fix:** Search offers again to obtain a fresh `offer_id`. The application automatically surfaces upstream provider error details in the UI.

---

## Deployment Architecture

### Docker Compose on AWS EC2 with Cloudflare Tunnel

For production hosting, the recommended architecture utilizes Docker Compose on an AWS EC2 instance connected to a Cloudflare Named Tunnel:

```text
+-------------------------------------------------------------+
|                      CLOUDFLARE EDGE                        |
|  - DDoS Shield & Automatic HTTPS Termination               |
|  - Cloudflare Named Tunnel Routing                         |
+------------------------------+------------------------------+
                               | Outbound TLS (Port 7844/443)
                               v
+-------------------------------------------------------------+
|                      AWS EC2 INSTANCE                       |
|  Security Group: ZERO INBOUND PORTS OPEN FROM INTERNET     |
|                                                             |
|  +-- Docker Network (trusttrip-net) ---------------------+  |
|  |                                                       |  |
|  |  cloudflared (Connector) ──> web (Nginx Reverse Proxy)|  |
|  |                                   │                   |  |
|  |                                   ├──> /api/ ──> backend  |
|  |                                   └──> / ────--> SPA HTML|
|  |                                                     │    |
|  |                                              postgres:5432
|  +-------------------------------------------------------+  |
+-------------------------------------------------------------+
```

- **Zero Open Inbound Ports:** The EC2 Security Group requires **0 inbound rules** from the internet. `cloudflared` initiates outbound connections to Cloudflare Edge.
- **Same-Origin Architecture:** Nginx serves compiled static files from `frontend/dist/` and internally reverse-proxies `/api/` to `backend:8000`, completely eliminating cross-origin browser issues.
- **Single-Worker Backend:** The backend runs `--workers 1` to guarantee consistent in-memory session protection and lock coordination across background tasks.

---

## Known Limitations & Roadmap

- **Single-Worker Concurrency:** The FastAPI backend is configured with a single Uvicorn worker (`--workers 1`). Multi-worker deployments require migrating in-memory thread caches to an external Redis cluster.
- **Hotelbeds Production mTLS:** Hotelbeds booking requires mutual TLS certificates. Real bookings default to LiteAPI until client certificates are provisioned.
- **Webhook Postback Verification:** Razorpay webhooks are supported at `/api/payments/webhook`. In local development, webhooks are disabled by default (`WEBHOOKS_ENABLED=false`) and execute via synchronous client signature verification.

---

## Contributing & Development Guidelines

1. **Follow the Safety-First Protocol:** Never run destructive commands (`git reset --hard`, `git clean -fdx`).
2. **Credential Hygiene:** Never commit `.env`, private keys, or API tokens. Always verify diffs before staging.
3. **Verify Before Proposing:** Run backend pytest suites (`python -m pytest`) and frontend typechecks (`npx tsc --noEmit`) before proposing commits.
4. **Preserve Database Lineage:** Never manually edit applied migration files; create a new numbered SQL migration in `migrations/`.

---

## License & Acknowledgements

- **License:** Distributed under the terms of the [Apache License 2.0](LICENSE).
- **Acknowledgements:**
  - [Groq](https://groq.com/) for low-latency LPU inference.
  - [Google DeepMind / Gemini](https://ai.google.dev/) for robust fallback intelligence.
  - [Duffel](https://duffel.com/) for comprehensive flight API inventory.
  - [LiteAPI](https://www.liteapi.travel/) for global accommodation data.
  - [Razorpay](https://razorpay.com/) for Indian Rupee payment gateway services.
  - [ReportLab](https://www.reportlab.com/) for programmatic PDF document generation.
