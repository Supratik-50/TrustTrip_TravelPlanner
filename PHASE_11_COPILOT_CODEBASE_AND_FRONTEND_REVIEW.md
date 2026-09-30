# TrustTrip Phase 11 — Codebase and Previous Frontend Discovery

**Repository:** `N:\Documents\GitHub\TrustTrip_TravelPlanner`  
**Purpose:** factual data collection for a new frontend; no implementation changes were made.

## 1. Executive Summary

The backend is a single FastAPI application in [app.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/app.py), with PostgreSQL persistence, Pydantic domain models, provider adapters, Phase 8 research/RAG services, and the Phase 9 flight-booking lifecycle. It is designed for one application instance, one PostgreSQL database, and approximately 1–3 concurrent users.

The previous frontend is not a React/Vue/Angular project. It is a server-rendered [templates/index.html](N:/Documents/GitHub/TrustTrip_TravelPlanner/templates/index.html) page using [static/script.js](N:/Documents/GitHub/TrustTrip_TravelPlanner/static/script.js), [static/style.css](N:/Documents/GitHub/TrustTrip_TravelPlanner/static/style.css), and three CDN scripts (`marked`, Razorpay Checkout, and `html2pdf.js`). There is no `package.json`, frontend source tree, router, component framework, frontend build tool, or frontend test suite.

The previous UI is useful as a behavioral reference and contains working demonstrations for conversational planning, legacy flight search/booking, hotel search/prebook/payment, trip history, PDF download, and cancellation. It is not a complete client for the current backend: authentication UI, canonical trip/revision management, Phase 8 research/RAG, evidence presentation, and Phase 9 booking-intent APIs are absent. Several calls still use legacy `customer_ref` and transaction-oriented APIs rather than the authenticated canonical-trip and booking-intent contracts.

The new frontend should therefore be a focused rebuild of the client surface, while reusing visual ideas and proven display/formatting logic from the old vanilla UI. The backend should remain the source of truth.

## 2. Repository Structure

### Root and application files

- `app.py` — FastAPI application, lifespan, CORS, global exception handling, health/readiness, conversational planner and PDF routes.
- `backend.py` — planner orchestration and agent workflow.
- `auth_api.py`, `trips_api.py`, `flight_api.py`, `flight_booking_api.py`, `hotel_api.py`, `hotel_payment_api.py`, `payment_api.py`, `offers_api.py`, `research_api.py` — HTTP routers.
- `requirements.txt` — Python dependencies.
- `.env.example` — environment reference.
- `Dockerfile` — container configuration. No `docker-compose.yml` was found.
- `migrations/004_travel_intelligence.sql` through `007_flight_booking_lifecycle.sql` — authoritative SQL migrations.
- `templates/index.html`, `static/script.js`, `static/style.css` — previous frontend.
- `demo.excalidraw` — design/demo artifact.

### Backend directories

- `dependencies/` — FastAPI authentication dependencies.
- `domain/` — Pydantic domain and API models: user, trip, flight, hotel, search criteria, live offers, revisions, evidence, research, bookings, and payments.
- `services/` — persistence stores, configuration/database helpers, planner services, flight/hotel providers, Duffel adapters, booking lifecycle, payments, research/RAG, evidence, and PDF generation.
- `migrations/` — PostgreSQL schema.
- `tests/` — unit and integration tests across Phases 1–10.

### Frontend directories/files

There is no `frontend/`, `src/`, `public/`, `package.json`, `vite.config.*`, `next.config.*`, `tsconfig.json`, or frontend package-lock. The complete previous UI tree is:

```text
templates/
  index.html
static/
  script.js
  style.css
```

### Backend layers

- Entry point: `app:app`.
- API layer: router modules listed above.
- Authentication: [dependencies/auth.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/dependencies/auth.py), [auth_api.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/auth_api.py), `services/auth_service.py`, `services/auth_store.py`.
- Database layer: `services/db.py`, store-specific psycopg helpers, and SQL migrations.
- Domain/model layer: `domain/*.py`.
- Provider integrations: Duffel flight services/adapters, LiteAPI/Hotelbeds hotel services, Razorpay payment service, weather/search/LLM providers.
- Research/RAG: `services/research/`, `domain/research.py`, `research_api.py`, vector store and ingestion services.
- Booking: `domain/booking_lifecycle.py`, `flight_booking_api.py`, `services/flight_booking_lifecycle_service.py`, `services/duffel_booking_adapter.py`, migration 007.

## 3. Backend Architecture

The application starts through the FastAPI lifespan. It validates configuration, probes PostgreSQL, initializes existing auth/trip tables, validates required Phase 4–9 schema tables, then serves requests. `GET /health` is liveness; `GET /ready` probes PostgreSQL.

Authentication is bearer JWT. Ownership is enforced by the authenticated `UserResponse` passed into stores/services. Canonical trip revisions and immutable offer snapshots are separate from the older transaction records still used by some legacy routes.

The current booking architecture has two distinct client-visible surfaces:

1. Legacy flight routes under `/api/flights` (`search`, `recheck`, `book`, `pay`).
2. Phase 9 booking-intent routes under `/api/bookings` (`prepare`, `revalidate`, `handoff`, `cancel-intent`, webhook/callback).

The new frontend should use the Phase 9 booking-intent surface for the approved flight workflow, not create direct provider orders or bypass revalidation.

## 4. Previous Frontend Architecture

### Framework and tooling

- HTML5 template served by FastAPI/Jinja.
- Plain browser JavaScript; no framework or state library.
- CSS is a single custom dark/glass theme.
- Browser state uses `localStorage` for `travel_thread_id` and older customer/transaction state.
- CDN dependencies:
  - `marked` for markdown rendering;
  - Razorpay Checkout;
  - `html2pdf.js`.
- API calls use direct `fetch()` with relative URLs.

### State and behavior

`script.js` stores global mutable variables such as `currentThreadId`, `latestPlanJson`, `selectedHotel`, `hotelOffers`, and selected flight/payment state. Loading is controlled by DOM button disabling and CSS classes. Errors are written into page-level boxes. There is no authenticated session store, route guard, refresh-token handling, normalized cache, or revision-aware client state.

### Reuse classification

- Visual theme and card layout: **REUSE WITH MODIFICATION**.
- Markdown rendering and flight/hotel display helpers: **REUSE WITH MODIFICATION** after verifying output escaping and current model fields.
- Global mutable state, inline `onclick` handlers, and transaction-oriented API calls: **REBUILD**.
- Whole page structure: **REBUILD** into pages/workspaces.

## 5. Previous Frontend Pages

There is one page, `index.html`, containing multiple sections rather than routes:

| Section | Purpose | Calls/behavior | Assessment |
|---|---|---|---|
| Hero/planner | Free-text trip planning | `POST /api/travel` | Reusable concept; lacks auth UI and canonical trip creation. |
| Workflow panel | Shows supervisor reasoning, selected agents, guardrail state | Consumes planner response fields | Useful demo visualization; provider/internal reasoning should be reviewed before exposing. |
| Chat flight recommendations | Renders live offers returned by planner | Uses planner response `live_flight_offers` and `selectFlight()` | Reuse card ideas; current objects are not the Phase 9 `FlightOfferView` contract. |
| Chat hotel recommendations | Renders planner hotel offers | Uses planner response `live_hotel_offers` | Reuse presentation only. |
| Manual flight search | Origin, destination, dates, passenger counts, cabin | Calls `/api/flights/search`, `/recheck`, `/refresh-selected-offer`, `/book`, `/pay` | Working legacy demonstration; replace booking portion with Phase 9 intent flow. |
| Manual hotel search | Destination, dates, occupancy, budget | Calls `/api/hotels/search`, `/prebook`, `/api/hotel-payments/*` | Reuse provider comparison UI; add authenticated ownership and canonical trip context. |
| Approval section | Approve/revise planner draft | Calls `/api/travel/approve` | Uses conversational thread approval, not canonical revision approval. |
| Trips/history | Lists and deletes records | Calls `/api/trips` and legacy refresh/delete/cancellation routes | Reuse history concept; rebuild around authenticated canonical trip and revision APIs. |
| Hotel payment | Razorpay Checkout | Creates/verifies hotel order and shows confirmation | Reuse only for existing hotel flow; never collect flight card data in TrustTrip. |

## 6. Previous Frontend Components

The old UI has functions rather than reusable framework components. Important functions include:

- `sendMessage`, `setPrompt`, `setLoading`, `showError`, `showWorkflow`.
- `renderAttractiveFlightCard`, `showChatFlightRecommendations`, flight selection/recheck/payment functions.
- Hotel search, prebook, guest form, Razorpay checkout, and hotel confirmation functions.
- Markdown rendering and PDF download helpers.
- Trips list, refresh, delete, refund, cancellation quote/confirmation.

There are no hooks, utilities module, typed client, route components, or shared API error abstraction. Inline HTML construction with `innerHTML` is common; any reuse must preserve escaping and avoid rendering untrusted provider/research text as HTML.

## 7. Backend API Inventory

The following inventory is from the actual route declarations. “Frontend use” identifies the appropriate future client role.

| Method | Route | Auth | Request | Response/purpose | Frontend use |
|---|---|---|---|---|---|
| POST | `/api/auth/signup` | No | `email`, `password` | `TokenResponse` with bearer token and user | Sign-up |
| POST | `/api/auth/login` | No | `email`, `password` | `TokenResponse` | Login |
| GET | `/api/auth/me` | Bearer | None | Current user | Session validation |
| POST | `/api/travel` | Bearer | `message`, optional `thread_id` | Planner result wrapper | Conversational planning |
| POST | `/api/travel/approve` | Bearer | `thread_id`, `approved`, optional feedback | Planner result wrapper | Legacy conversational approval |
| POST | `/api/travel/pdf` | Not declared | `plan` object | PDF bytes | Plan export; should be protected by client policy |
| GET | `/api/health` | No | None | Liveness object | Deployment health |
| GET | `/api/ready` | No | None | Ready or 503 database status | Deployment readiness |
| GET | `/api/trips` | Bearer | None | Owned trips plus legacy records | Dashboard |
| POST | `/api/trips` | Bearer | title, origin, destination, status, metadata | Created canonical trip | Trip creation |
| GET | `/api/trips/{trip_id}` | Bearer | path UUID | Owned trip | Trip workspace |
| POST | `/api/trips/{trip_id}/threads` | Bearer | optional thread ID/metadata | Planner thread | Attach conversation |
| POST | `/api/trips/{trip_id}/revisions` | Bearer | feedback, `updated_constraints`, optional base revision | New revision/status | HITL edit/replan |
| GET | `/api/trips/{trip_id}/revisions` | Bearer | path UUID | Revision list/current revision | Revision history/staleness |
| POST | `/api/trips/{trip_id}/approve` | Bearer | route only | Approval/revision/trip status | Final canonical approval |
| POST | `/api/trips/{trip_id}/revoke-approval` | Bearer | route only | Awaiting approval | Undo approval |
| POST | `/api/trips/{trip_id}/select-offer` | Bearer | `offer_type`, `snapshot_id` | Selected snapshot/trip | Bind selected flight/hotel |
| GET | `/api/flights/config` | No | None | Safe provider/mode metadata | Environment indicator |
| POST | `/api/flights/search` | Route currently not declared protected | slices, passenger counts, cabin, connections | Legacy offers/search context | Legacy shopping only |
| POST | `/api/flights/recheck` | Route currently not declared protected | `offer_id` | Rechecked offer | Legacy flow |
| POST | `/api/flights/refresh-selected-offer` | Route currently not declared protected | offer ID/search context | Fresh offer | Legacy flow |
| POST | `/api/flights/book` | Route currently not declared protected | offer, passengers, final confirmation/payment mode | Legacy booking result | Do not use for new Phase 9 UI |
| POST | `/api/flights/pay` | Route currently not declared protected | order ID/final confirmation | Sandbox hold payment | Demo-only legacy flow |
| POST | `/api/flights/search-flexible` | Route depends on router implementation | flexible search criteria | `FlexibleFlightSearchResponse` | Flexible-date shopping |
| POST | `/api/bookings/flight/prepare` | Bearer | trip/snapshot/passengers/idempotency | `BookingIntent` | Create intent |
| POST | `/api/bookings/flight/{id}/revalidate` | Bearer + idempotency | intent request/idempotency | `OfferRevalidationResult` | Confirm live offer |
| POST | `/api/bookings/flight/{id}/handoff` | Bearer + idempotency | handoff request/idempotency | checkout destination | Open Duffel Link |
| GET | `/api/bookings/{id}` | Bearer | intent UUID | `BookingIntent` | Poll/display status |
| POST | `/api/bookings/{id}/cancel-intent` | Bearer + idempotency | cancel request | `BookingIntent` | Cancel active intent |
| POST | `/api/bookings/flight/webhook` | Provider signature | provider event | accepted/idempotent event | Backend only |
| GET | `/api/bookings/flight/callback` | Provider callback | provider query values | pending/callback result | Return page only; not completion authority |
| GET | `/api/hotels/config` | No | None | provider/payment availability | Environment indicator |
| POST | `/api/hotels/search` | Current route has no auth dependency | destination/dates/occupancy/budget | normalized hotel offers | Hotel shopping |
| POST | `/api/hotels/prebook` | Current route has no auth dependency | offer dictionary | prebook result | Hotel quote validation |
| POST | `/api/hotel-payments/create-order-from-snapshot` | Inspect route before use | snapshot/payment data | Razorpay order | Hotel payment |
| POST | `/api/hotel-payments/verify-and-book` | Inspect route before use | Razorpay/provider booking data | hotel booking result | Hotel confirmation |
| POST | `/api/research/query` | Bearer | query, mode, trip/document IDs, locale, max sources | `ResearchResult` with sources/evidence/citations | Research workspace |
| POST | `/api/research/documents/upload` | Bearer | multipart file, optional trip/title | document/chunk count | Private RAG upload |
| GET | `/api/research/documents` | Bearer | None | owned documents | Research library |
| DELETE | `/api/research/documents/{doc_id}` | Bearer | document UUID | deletion result | Research library |
| GET | `/api/research/sessions/{session_id}` | Bearer | session UUID | owned research session | Research history |
| POST | `/api/offers/revalidate` | Router endpoint | offer/revalidation request model | `RevalidationResult` | Legacy offer revalidation if needed |
| GET/POST | `/api/payments/*` | Varies by route | Razorpay order/payment/refund/webhook models | payment results | Existing hotel/legacy payment only |

The exact request/response schemas must be generated from the route models during implementation rather than inferred from old DOM objects.

## 8. Authentication Flow

1. `POST /api/auth/signup` or `/api/auth/login` returns:
   - `access_token`;
   - `token_type: "bearer"`;
   - `user` with UUID, email, active flag, and creation time.
2. Store the token in the frontend session state. The backend expects `Authorization: Bearer <token>`.
3. Call `GET /api/auth/me` on application load to validate the session.
4. Attach the header to every protected request.
5. There is no logout endpoint and no refresh-token endpoint. Logout is client-side token removal.
6. JWT expiration is configured in `services/auth_service.py`; the frontend should treat 401 as session expiry and return to login.
7. The ownership key is the authenticated user ID. The client must not use a user-entered `customer_ref` as an ownership mechanism.

For a demo, an in-memory access token store is simple, but an HttpOnly cookie would reduce browser token exposure if the deployment configuration supports it. The existing API contract is bearer-header based and must be honored.

## 9. Trip/Planning Flow

### Canonical trip flow

`POST /api/trips` creates the owned canonical trip with title, origin, destination, status, and metadata. The response contains the trip record and ID. `POST /api/trips/{trip_id}/threads` can attach a planner thread.

`POST /api/travel` accepts a natural-language `message` and optional thread ID. It returns a planner result with fields produced by `run_travel_agent`, including generated itinerary content, selected agents, reasoning/guardrail fields, live flight/hotel recommendations, and weather/budget outputs when available. The exact result is agent-shaped rather than a single typed `Plan` response.

For a robust new UI, create/select a canonical trip first, associate a thread, then send planning messages with the thread ID and preserve the returned trip/revision identifiers when present. The old frontend currently starts from free text and persists only `travel_thread_id`.

### UI states

- idle form;
- submitting/long-running planner;
- draft result;
- provider partial/unavailable result;
- approval required;
- replanning;
- generic sanitized error;
- retry.

Planner execution can involve several provider calls and should be presented as a long-running operation. Do not assume a fixed short duration.

## 10. Flight Flow

### Current models

`FlightOfferView` exposes `snapshot_id`, provider, price breakdown, stops, segments, passenger count, seats, timestamps, freshness, availability, limitations, recommendation fields, and candidate dates. The provider offer ID/raw payload are intentionally not client-facing.

`FlexibleFlightSearchResponse` exposes `session_id`, candidate-date summaries, `offers_by_date`, cheapest/recommended offer, and errors. A `FlightSegmentModel` includes origin/destination IATA, departure/arrival, carrier, flight number, duration, and cabin.

### Approved Phase 9 client flow

1. Search using `/api/flights/search-flexible` or the canonical planner result.
2. Display normalized offers and explicit expiry/availability/limitations.
3. Bind the selected `snapshot_id` to the canonical trip with `POST /api/trips/{trip_id}/select-offer` (`offer_type: "flight"`).
4. Collect validated passenger data using `PassengerBookingModel` fields required by the booking route.
5. `POST /api/bookings/flight/prepare` with an `Idempotency-Key`.
6. Display the created `BookingIntent` and status (`DRAFT` initially).
7. Revalidate with `POST /api/bookings/flight/{intent_id}/revalidate`, again with an idempotency key.
8. Handle `READY_FOR_BOOKING`, `PRICE_CHANGED`, `UNAVAILABLE`, `EXPIRED`, and `FAILED` explicitly. Price changes require user acknowledgement; unavailable/expired offers must not proceed.
9. On an approved fresh result, call `POST /api/bookings/flight/{intent_id}/handoff` with an idempotency key.
10. Open the returned Duffel Link/external checkout URL. TrustTrip does not collect flight card credentials.
11. Show `HANDED_OFF`/pending state and rely on verified provider webhook for completion. Browser redirect is not completion authority.

The old UI instead uses `/api/flights/book` and `/api/flights/pay`, direct legacy transaction IDs, and a sandbox balance payment route. Those calls must not be used for the new approved flight lifecycle.

## 11. Hotel Flow

The old UI calls `/api/hotels/search`, renders normalized LiteAPI/Hotelbeds offers, calls `/api/hotels/prebook`, and invokes Razorpay Checkout through `/api/hotel-payments/*`. Hotel offers include provider, hotel identity/name/city/address, room/board/refundability, price/nightly price, occupancy, freshness, expiry, availability, and recommendation fields.

The hotel flow is distinct from flight booking:

`search → select/prebook → create Razorpay order → browser payment → verify-and-book → confirmation/PDF`.

The new UI must preserve the existing hotel provider/payment contract and display provider errors, stale quotes, failed verification, booking-in-progress, and final confirmation. It must never reuse hotel payment fields for flight booking.

## 12. Research/RAG Flow

`POST /api/research/query` supports `auto`, `web`, `rag`, and `hybrid`; it accepts a query, optional owned trip ID, optional owned document IDs, locale, and source limit. The result contains research session/query/source/evidence/citation data from `domain/research.py`.

Users can upload PDF/TXT/MD/JSON documents (10 MB limit), list/delete owned documents, and retrieve owned research sessions. Trip/document ownership is checked server-side.

Academic/demo-visible information:

- research question and selected mode;
- session ID;
- source title/URL/publisher metadata;
- evidence excerpts and confidence/freshness;
- citations and conflicts;
- whether content came from web, private RAG, or hybrid retrieval.

Do not expose raw credentials, internal embeddings, database records, or provider secrets. There are no standalone public evidence CRUD routes; evidence is exposed through research/revision projections.

## 13. HITL/Revision Flow

`POST /api/trips/{trip_id}/revisions` accepts `feedback`, `updated_constraints`, and optional `expected_base_revision`. It creates a `TripChangeSet`, advances the canonical revision atomically, marks the trip as awaiting approval/replanning as implemented, and invalidates affected flight intents when flight-relevant constraints change.

`GET /api/trips/{trip_id}/revisions` returns immutable revisions, current revision ID/number, and revision records. A revision includes revision ID, trip/user IDs, number, change set, plan snapshot, publication flag, publication time, and creation time.

The UI should:

1. show current published revision;
2. open an edit form for destination, origin, dates, passengers, flight/hotel constraints, and feedback;
3. submit with the current revision number as optimistic base;
4. show replanning/loading;
5. replace stale offer/plan data with the new revision;
6. warn that old flight offers/intents may be expired;
7. require new selection/revalidation for the new revision.

Hotel-only changes should not unnecessarily discard an unchanged flight slice, but the UI should always follow the returned revision/intent status rather than infer validity locally.

## 14. Approval and Booking Flow

Canonical approval is `POST /api/trips/{trip_id}/approve` and returns trip/revision/approval IDs and `approved` status. Revocation returns the trip to awaiting approval. The old conversational approval is `POST /api/travel/approve`, requiring a thread ID and feedback when rejecting.

Flight:

```text
canonical trip
→ selected flight snapshot
→ BookingIntent prepare
→ fresh revalidation
→ user acknowledgement if price changed
→ fresh handoff
→ Duffel Link/external checkout
→ verified webhook completion
```

Hotel:

```text
hotel search
→ prebook/fresh quote
→ Razorpay order
→ external payment
→ verified payment/provider booking
→ confirmation/PDF
```

The UI must distinguish intent statuses (`DRAFT`, `SELECTED`, `REVALIDATING`, `READY_FOR_BOOKING`, `PRICE_CHANGED`, `UNAVAILABLE`, `EXPIRED`, `HANDED_OFF`, `COMPLETED`, `FAILED`, `ABANDONED`) from hotel payment/booking statuses and from canonical trip status.

## 15. Frontend Data Models

| Model | Important fields | Frontend meaning |
|---|---|---|
| `UserResponse` | `id`, `email`, `is_active`, `created_at` | Session/user identity; never display password hash. |
| `TokenResponse` | `access_token`, `token_type`, `user` | Login/signup result. |
| `TripRequest` | IDs, origin/destination, dates, duration, passengers, budget, preferences, intent, revision, status | Planner/canonical trip state. |
| `Trip` persistence record | trip ID, user ID, title, origin, destination, status, metadata, revision pointers | Dashboard/workspace identity. |
| `PlanRevision` | revision ID, trip/user IDs, revision number, change set, plan snapshot, published state/timestamps | Immutable plan version. |
| `FlightOfferView` | snapshot ID, provider, `PriceBreakdown`, segments, stops, passenger count, freshness/availability, expiry, limitations, recommendation | Safe flight card model. |
| `HotelOfferView` | snapshot ID, provider, hotel/room/board/refundability, occupancy, pricing, freshness/availability, expiry, recommendation | Safe hotel card model. |
| `SearchSession` | session ID, criteria/hash, ownership, timestamps | Search result grouping and stale-data context. |
| `OfferSnapshot` | snapshot ID, session/trip/revision/provider references, normalized offer, expiry/freshness | Selection and booking binding identity. |
| `ResearchResult` | session/query, sources, evidence, citations, conflicts, answer/projection fields | Research evidence UI. |
| `EvidenceRecord` | evidence ID, type, applicability/content hashes, freshness, source agent, payload | Evidence provenance display. |
| `BookingIntent` | intent/user/trip/revision/snapshot IDs, provider reference, status, price/currency, timestamps, handoff fields | Flight lifecycle state. |
| `OfferRevalidationResult` | status, price/currency, changed fields, successor snapshot/revalidation data | Revalidation outcome and user decision. |
| `PassengerBookingModel` | passenger identity, DOB, title, gender, contact/type | Flight booking form. |
| `TripChangeSet` | changed constraints/fields and feedback | HITL edit payload. |
| `PlanApproval` | approval/revision/trip/user IDs, timestamp, metadata | Approval audit/result. |

## 16. Current Problems

Facts observed in the previous frontend:

- no login/signup/me screen or bearer-header client;
- one page combines unrelated planner, flight, hotel, approval, payment, and history state;
- global mutable variables and inline handlers make stale-state coordination difficult;
- planner thread ID is persisted, but canonical trip ID/revision ID is not consistently managed;
- legacy calls use `customer_ref`, transaction IDs, and older `/api/flights/book`/payment paths;
- no Phase 9 booking-intent UI, price-change acknowledgement, or explicit status machine;
- no Phase 8 research/document/evidence UI;
- no revision history or canonical `TripChangeSet` editor;
- no route-level loading/error/empty states for separate workspaces;
- no typed API client or centralized 401/422/409/503 handling;
- `innerHTML`-based rendering requires careful escaping for provider/research content;
- responsive CSS exists but the single long page is not equivalent to mobile navigation/workspace structure;
- CDN scripts introduce external runtime dependencies and CSP/deployment considerations;
- the old PDF route is called without a declared authentication dependency;
- old hotel/legacy flight routes should be reviewed before exposing them as trusted canonical-booking actions.

## 17. Reuse vs Rebuild

| Area | Decision | Reason |
|---|---|---|
| Authentication | **REBUILD** | No previous auth UI or bearer client. |
| Dashboard | **REBUILD with reuse of history card styling** | Old history is partial and transaction-oriented. |
| Trip form | **REBUILD** | Old UI is free-text only; canonical trip fields/revisions need structured form. |
| Planning workspace | **REUSE WITH MODIFICATION** | Existing planner request/rendering is a useful demo, but must be trip/revision aware. |
| Flight cards | **REUSE WITH MODIFICATION** | Existing visual cards are useful; map to `FlightOfferView`/snapshot IDs. |
| Hotel cards | **REUSE WITH MODIFICATION** | Existing normalized display works, but payment/ownership state must be clearer. |
| Research view | **REBUILD** | No previous surface. |
| Evidence view | **REBUILD** | No previous surface. |
| HITL editor | **REBUILD** | No canonical revision editor. |
| Approval screen | **REBUILD/REUSE WITH MODIFICATION** | Existing button is thread approval, not complete canonical approval. |
| Booking screen | **REBUILD** | Phase 9 intent lifecycle and Duffel Link are absent. |
| Navigation | **REBUILD** | One-page sections have no route/workspace navigation. |
| Styling | **REUSE WITH MODIFICATION** | Dark/glass theme and cards are coherent, but accessibility/layout need page-level adaptation. |

## 18. Required New Screens

### MUST HAVE

1. Login and signup.
2. Authenticated dashboard with owned trips and booking/revision status.
3. Create/edit canonical trip form.
4. Planning workspace with planner loading, itinerary, budget/weather/hotel/flight summaries.
5. Flight offer results with expiry, freshness, availability, recommendation, and snapshot selection.
6. Flight passenger form and Phase 9 booking-intent status/revalidation/handoff screens.
7. Hotel results, prebook/payment, verification, and confirmation screens.
8. Research query/results/citations and private-document list/upload states.
9. Revision/HITL editor, revision history, stale-data warnings, and replanning state.
10. Approval screen showing exact trip/revision being approved.
11. Safe error/empty/offline/readiness states and logout/session-expiry behavior.

### USEFUL

- live health/readiness indicator;
- evidence provenance drawer;
- research source/citation details;
- PDF export/ticket/invoice download;
- booking history and cancellation/refund status;
- demo-mode/provider-mode indicator;
- mobile bottom navigation or compact workspace navigation.

### NOT NEEDED

- admin console;
- enterprise tenant-management UI;
- distributed-job monitoring;
- direct card-entry UI for flight booking;
- Redis/Kafka/Celery controls;
- internal vector embeddings or raw provider payload viewer.

## 19. Recommended Frontend Stack

The old page should be partially rebuilt rather than incrementally expanded. The smallest maintainable choice is a TypeScript React application with a lightweight router, a small centralized auth/query state layer, native `fetch` or a very small typed API wrapper, and the existing CSS theme carried into component styles. A full state framework is not required; server state can be request-scoped with a small context/store because the target is 1–3 users.

If minimizing dependencies is more important than component ergonomics, a multi-page TypeScript application using the existing FastAPI/Jinja serving model is also viable. The key requirement is typed route/model handling and page-level state separation, not a particular framework.

Do not add a frontend library solely for popularity. Select the framework only if the new frontend deployment already standardizes one; otherwise the existing no-build approach can be extended with modules, but it will require more manual state discipline.

## 20. State Management

Keep:

- authenticated user/token/session state;
- current canonical trip ID;
- current revision ID/number;
- planner thread ID;
- selected snapshot IDs;
- active booking intent and status;
- research session/document IDs;
- transient loading/error state.

Server state must be keyed by trip/revision/session IDs. On revision changes, invalidate cached offers and active booking intents according to returned status. Never infer that a snapshot remains valid solely because it is still in browser memory.

## 21. API Integration Strategy

Create one API module with:

- base URL configuration;
- bearer-header injection;
- JSON and multipart helpers;
- typed success/error parsing;
- 401 logout/session-expiry handling;
- 409 conflict and stale-revision handling;
- 422 form-error mapping;
- 502/503/504 provider/database messaging;
- idempotency-key generation and replay-safe request handling for Phase 9 mutations.

Use `snapshot_id`, `trip_id`, `revision_id`, `session_id`, and `intent_id` explicitly. Do not send provider offer IDs or raw payloads when the client contract intentionally exposes snapshots instead.

## 22. Environment Configuration

Current backend defaults:

- backend origin is the FastAPI origin serving the template, commonly `http://127.0.0.1:8000` or `http://localhost:8000`;
- old frontend uses relative URLs and therefore needs no frontend API base variable when served by FastAPI;
- CORS reads `ALLOWED_ORIGINS`, defaulting to `http://localhost:3000,http://localhost:8000`;
- a separately served frontend must be added to `ALLOWED_ORIGINS` by deployment configuration;
- requests use `Authorization: Bearer <access_token>`;
- provider secrets stay backend-only;
- the frontend should expose only a public API base URL and non-secret display configuration.

No frontend `.env`, package manifest, proxy, or dedicated local port was found in the repository. A new standalone frontend will need its own environment convention and either CORS configuration or a development proxy.

## 23. Demo User Journey

1. Login/signup with `/api/auth/signup` or `/api/auth/login`.
2. Validate session with `/api/auth/me`.
3. Create a canonical trip with `POST /api/trips`.
4. Attach a thread with `POST /api/trips/{trip_id}/threads`.
5. Generate a plan with `POST /api/travel`, retaining thread/trip/revision identifiers.
6. Display itinerary, budget, weather, hotel, and flight projections.
7. Search/review flights using flexible search or planner offers; select a snapshot with `/api/trips/{trip_id}/select-offer`.
8. Search/prebook a hotel and complete the existing Razorpay hotel path if the demo includes hotels.
9. Run `/api/research/query`, optionally upload a private document, and show citations/evidence.
10. Edit destination/dates/passengers/constraints with `/api/trips/{trip_id}/revisions`.
11. Refresh the current revision and replace stale offers.
12. Approve the exact revision with `/api/trips/{trip_id}/approve`.
13. Prepare a flight `BookingIntent`, revalidate, handle price/unavailable/expiry states, and acknowledge any price change.
14. Handoff to Duffel Link and show pending external checkout/completion status.
15. Display hotel confirmation/PDF separately and never confuse hotel payment completion with flight completion.

## 24. Build Strategy

Build in five logical parts, not one giant pass:

1. **Foundation and auth:** app shell, routing/navigation, API client, signup/login/me/logout, protected layout.
2. **Dashboard and canonical trip:** trip list/create/detail, planner thread association, basic planning workspace.
3. **Offers:** typed flight/hotel models, search/result cards, snapshot selection, expiry/loading/error states.
4. **Research and HITL:** research query/documents/citations, evidence panels, revision editor/history, stale-state replacement.
5. **Approval and booking:** canonical approval, Phase 9 booking intent/revalidation/price acknowledgement/Duffel handoff, hotel payment/confirmation, PDF downloads, mobile polish and end-to-end demo validation.

This order makes authentication and IDs available before building booking/revision UI and keeps provider/payment flows isolated.

## 25. Genuine Backend Gaps

These are client-blocking integration ambiguities or gaps, not theoretical architecture requests:

1. **Planner-to-canonical-trip response coupling:** `POST /api/travel` returns an agent-shaped result rather than a consistently documented typed plan envelope containing `trip_id`, `revision_id`, and snapshot IDs. The frontend must inspect actual returned fields or use explicit canonical trip/revision routes around it. Severity: **medium**.
2. **Legacy and Phase 9 booking surfaces coexist:** `/api/flights/book` and `/api/flights/pay` coexist with `/api/bookings/flight/*`. Without a frontend contract decision, it is easy to implement the wrong flow. Severity: **medium**; this report recommends Phase 9.
3. **Some legacy search/hotel routes do not declare authentication dependencies:** ownership is not represented uniformly at the route layer. This blocks a clean assumption that every search/prebook response is already bound to the current canonical trip. Severity: **medium** for the new UI; do not treat it as a request for architecture redesign.
4. **No frontend-facing route metadata/schema document:** exact response envelopes for agent output, hotel payment, and legacy transaction/history routes are not centralized. Severity: **low/medium**; the client should generate its own types from the actual models during implementation.
5. **No logout/refresh endpoint:** client-side logout is possible, but long-lived sessions require login again after expiry. Severity: **low** for the demo.
6. **Planner and legacy route contracts are less uniform than the Phase 9/10 contracts:** the previous frontend calls unauthenticated/transaction-oriented legacy surfaces while the canonical APIs use bearer ownership, snapshots, revisions, and idempotency. Severity: **medium** integration risk; this is handled by choosing the canonical APIs for the new frontend, not by redesigning the backend.

No missing backend feature prevents the academic/demo journey if the new frontend uses canonical trip/revision APIs, Phase 8 research APIs, and Phase 9 booking-intent APIs as documented above.

## 26. Security Requirements

- Keep bearer tokens out of URLs, logs, DOM text, analytics, and error messages.
- Attach `Authorization` only through the API client.
- Handle 401 by clearing session state and returning to login.
- Treat every trip, revision, snapshot, document, research session, and booking intent as server-owned; never trust IDs from another user.
- Do not expose provider tokens, raw encrypted payloads, internal database fields, embeddings, PAN/CVV, or payment secrets.
- Flight checkout must open the returned Duffel Link; TrustTrip must not collect flight card data.
- Use HTTPS in deployed environments and avoid storing tokens in persistent local storage if an HttpOnly-cookie deployment is introduced.
- Render planner, research, citation, hotel, and provider text safely; avoid unsanitized `innerHTML`.
- Treat external checkout URLs as untrusted navigation and display the provider/domain clearly.
- Do not use `customer_ref` as an authorization substitute.
- A local `.env` file exists in the worktree with 34 populated assignments and is ignored by Git. Its values were not copied into this report, but it must never be committed, bundled into frontend assets, or exposed through a client-side environment file; any real credentials should be rotated if they have been shared or committed elsewhere.

## 27. Files to Reuse

- [templates/index.html](N:/Documents/GitHub/TrustTrip_TravelPlanner/templates/index.html) — copy visual/content ideas, not as the final information architecture.
- [static/script.js](N:/Documents/GitHub/TrustTrip_TravelPlanner/static/script.js) — reuse selected formatting and provider-card logic only after adapting to current typed models.
- [static/style.css](N:/Documents/GitHub/TrustTrip_TravelPlanner/static/style.css) — dark/glass theme, buttons, cards, responsive patterns.
- [domain/live_offer.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/domain/live_offer.py) — authoritative client-facing offer model definitions.
- [domain/booking_lifecycle.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/domain/booking_lifecycle.py) — booking statuses/request/response models.
- [domain/research.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/domain/research.py) and [domain/evidence.py](N:/Documents/GitHub/TrustTrip_TravelPlanner/domain/evidence.py) — research/evidence contracts.

## 28. Files to Replace or Supersede in the New Frontend

- The monolithic [templates/index.html](N:/Documents/GitHub/TrustTrip_TravelPlanner/templates/index.html) page structure.
- Global mutable state and inline handlers in [static/script.js](N:/Documents/GitHub/TrustTrip_TravelPlanner/static/script.js).
- Legacy `customer_ref`/transaction-oriented browser calls.
- Direct legacy flight `/book` and `/pay` UI for the new approved flight journey.

## 29. Files to Leave Untouched

- Backend routers and services unless a genuine API contract defect is separately approved.
- [migrations/](N:/Documents/GitHub/TrustTrip_TravelPlanner/migrations/) and database lifecycle logic.
- Provider credentials and server-side configuration.
- Phase 1–10 domain behavior, ownership, revalidation, idempotency, webhook, and concurrency implementation.
- Backend tests and integration fixtures during frontend discovery.

## 30. Final Recommendation

Do not extend the old single-page script into another monolith. Build a small authenticated, route-based frontend around the existing backend contracts. Reuse the old visual language and offer-card presentation, but rebuild authentication, canonical trip/revision state, research/evidence, and Phase 9 booking-intent orchestration. Keep hotel payment separate from flight handoff. Use the existing API as the source of truth and validate every response against the actual Pydantic models and route behavior.

The repository inspection is complete.

# PHASE 11 DISCOVERY COMPLETE
