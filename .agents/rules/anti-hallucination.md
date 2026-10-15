# Anti-Hallucination Rules

Strict guidelines to prevent factual drift, fabricated contracts, and false assumptions.

## 1. Verify Before Claiming
- Always locate the source file, inspect the class/function, trace callers, trace API boundaries, and inspect database/test fixtures before declaring how an endpoint, service, or model behaves.
- If information cannot be verified directly from the repository, explicitly report:
  - `UNKNOWN:` <missing information>
  - `EVIDENCE:` <what was actually verified>
  - `NEXT CHECK:` <what should be inspected>

## 2. Never Invent API Contracts
- Do not assume an API accepts or returns parameters not declared in FastAPI routers or Pydantic schemas.
- Check actual request models (e.g. `PaymentOrderRequest`, `PrepareFlightBookingRequest`, `HotelPaymentOrderRequest`).

## 3. Never Invent Database Fields
- Only reference columns defined in database migrations (`migrations/*.sql`) or table initializations in store classes.
- Do not assume schema fields exist without checking migration history.

## 4. Never Invent IDs or Credentials
- Never introduce fake UUIDs, fake revision IDs, fake approval IDs, fake booking IDs, fake PNRs, or fake confirmation numbers in production logic.
- Mocks and stubs are permissible strictly within isolated test suites and mock fallback boundaries.

## 5. Never Invent Provider Behavior
- Duffel API and LiteAPI / Hotel providers behave according to their official schemas and our adapter layers (`duffel_provider.py`, `hotel_api.py`). Do not assume third-party APIs accept arbitrary keys or formats.

## 6. Distinguish Fixtures from Production
- Test helpers (such as `_auth_client()`, in-memory stores, or mock responses) represent testing convenience, not production architecture. Always verify how the production code executes with PostgreSQL and live HTTP clients.

## 7. Explicit Classification
- In every technical discussion and audit, classify findings as:
  - **VERIFIED:** Supported by direct repository code or test output.
  - **INFERRED:** Deduce from related code, awaiting runtime confirmation.
  - **UNKNOWN:** Not present or unverified in the codebase.
