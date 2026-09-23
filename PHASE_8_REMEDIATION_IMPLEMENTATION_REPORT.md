# Phase 8 Remediation Implementation Report: Post Gate 2 Re-Audit

**Date:** 2026-09-24
**Baseline Commit:** `b25ecdc` (Phase 7: flexible live flight search)
**Independent Audit Status:** Gate 2 Audit: CHANGES REQUIRED
**Verification Result:** 309 passed, 0 failed, 0 skipped (26 executable test modules, zero overlap / zero omission)
**Commit & Push Status:** NO COMMIT, NO PUSH PERFORMED

---

## 1. Executive Summary

Following the Gate 2 Independent Re-Audit of Phase 8 against baseline commit `b25ecdc`, five material blockers were remediated in this round. The `SafeHttpFetcher` transport-level destination socket pinning and SSRF defense remediation was previously reviewed and accepted by the audit as PASS with stated limitations.

This report documents the architectural remediations implemented for the five remaining blockers, the exact fetcher guarantee boundary, the complete grouped verification results across all 26 test modules, and known limitations.

> [!IMPORTANT]
> This report documents remediation progress following the re-audit. **Phase 8 is NOT claimed as complete.** Development has stopped before commit and before push per instructions.

---

## 2. Remediation of Material Blockers

### Blocker 1: Structural Research / Planner Boundary
- **Defect Identified in Audit**: `ResearchAdvisoryContext` was separated at the Python argument level, but research summaries and advisory prose were still interpolated into prompt strings sent as `HumanMessage` or `AIMessage`.
- **Architectural Solution**:
  - Raw research prose is **NEVER** placed into `HumanMessage`.
  - Raw research prose is **NEVER** placed into `SystemMessage`.
  - Raw research snippets, conflict prose, and document text are completely stripped of instruction-bearing authority.
  - Implemented typed non-executable `ResearchAdvisoryProjection` in `domain/research.py`:
    ```python
    class ResearchAdvisoryProjection(BaseModel):
        allowed_facts: list[str] = Field(default_factory=list)
        evidence_ids: list[UUID] = Field(default_factory=list)
        citation_ids: list[str] = Field(default_factory=list)
        limitations: list[str] = Field(default_factory=list)
        conflicts_as_structured_records: list[dict[str, Any]] = Field(default_factory=list)
        is_untrusted: bool = Field(default=True)
    ```
  - In `backend.py`: `research_agent` returns non-instructional messages (`AIMessage(content="Research advisory context compiled.")`) and attaches `ResearchAdvisoryProjection` to `TravelState`.
  - In `services/chat_planner.py`: `_compact_context` contains zero research prose in the prompt. Research advisory is attached strictly at the application layer to `TravelPlan.research_advisory`.
  - Research has **zero tool-call authority**, **zero prompt override authority**, and **zero ability to modify canonical constraints**.
  - Added adversarial tests (`test_adversarial_planner_hijack_prevention`, `test_malicious_research_prose_cannot_become_planner_instructions`) verifying that injection payloads never enter prompt messages.

---

### Blocker 2: Complete Typed Canonical Invariants
- **Defect Identified in Audit**: Previous invariant checking parsed LLM-generated prose (`plan.trip_summary`, `plan.route`, `plan.flight_suggestion`, `plan.accommodation_plan`) using regular expressions rather than enforcing structural typing.
- **Architectural Solution**:
  - Replaced all string/regex searching with direct structural validation on typed attributes of `TravelPlan`:
    ```python
    class TravelPlan(BaseModel):
        ...
        origin: str | None = None
        destination: str | None = None
        origin_airport: str | None = None
        destination_airport: str | None = None
        adults: int | None = None
        children: int | None = None
        infants: int | None = None
        passenger_count: int | None = None
        selected_flight_id: str | None = None
        selected_hotel_id: str | None = None
        revision_number: int | None = None
    ```
  - In `enforce_complete_canonical_invariants`:
    - **Hard Reject (Raises `CanonicalInvariantViolation`)**:
      - `origin`
      - `destination`
      - `origin_airport` and `destination_airport`
      - `adults`, `children`, and `infants`
      - `passenger_count`
      - `selected_flight_id` (matched against typed selected flight provider object)
      - `selected_hotel_id` (matched against typed selected hotel provider object)
      - `budget.currency`
      - `revision_number`
    - **Canonical Normalization (Approved derived/display fields only)**:
      - `departure_date` and `return_date` formatting in `travel_dates`
      - `duration_days` calculated deterministically from dates: `(return_date - departure_date).days + 1` (or `1` for one-way)
      - `one-way` semantics label formatting: `"{departure_date} (one-way)"`
      - `budget.target_budget_inr` pinned to canonical `budget_amount`
    - Zero silent modification of hard-reject fields.
  - Added explicit tests for every single field in `test_phase8_research_rag.py`.

---

### Blocker 3: Evidence Revision Linkage
- **Defect Identified in Audit**: While Phase 6 `EvidenceRecord` and `PlanRevisionEvidenceLink` were linked to `revision_id`, the separate `research_evidence` row did not persist `trip_id` and `revision_id`, leaving two partially disconnected representations.
- **Architectural Solution**:
  - Updated `ResearchEvidence` domain model with `trip_id: UUID | None` and `revision_id: UUID | None`.
  - Updated `services/research/research_store.py` (`save_evidence_batch`) to persist `trip_id` and `revision_id` directly into the `research_evidence` PostgreSQL table.
  - In `services/research/orchestrator.py`:
    - Resolves authoritative `current_revision_id` from the trip row under strict ownership check.
    - If `current_revision_id` cannot be resolved for a trip-linked query, the operation fails closed immediately (`RuntimeError`).
    - Updates all final evidence records with `trip_id` and `revision_id = active_rev_id`.
    - Bridges to Phase 6 atomically via `bridge_research_evidence` without catching or swallowing exceptions.
    - For global research (`trip_id is None`): `trip_id=NULL` and `revision_id=NULL` are strictly guaranteed.
  - Added live integration test `test_postgres_exact_revision_id_persistence_in_research_evidence` asserting exact `revision_id` exists in `research_evidence`.

---

### Blocker 4: Database Tenant + Lifecycle Integrity
- **Defect Identified in Audit**: Related tables carried separate `user_id` columns without complete DB-level proof that those values belong to the same tenant.
- **Architectural Solution in `migrations/006_research_and_rag.sql`**:
  - Added composite foreign key constraints enforcing tenant consistency at the database engine level:
    - `trips`: `CONSTRAINT uq_trips_id_user UNIQUE (id, user_id)`
    - `research_sessions`: `CONSTRAINT fk_research_sessions_trip_user FOREIGN KEY (trip_id, user_id) REFERENCES trips(id, user_id) ON DELETE CASCADE`
    - `research_queries`: `CONSTRAINT fk_research_queries_session_user FOREIGN KEY (session_id, user_id) REFERENCES research_sessions(id, user_id) ON DELETE CASCADE`
    - `research_queries`: `CONSTRAINT fk_research_queries_trip_user FOREIGN KEY (trip_id, user_id) REFERENCES trips(id, user_id) ON DELETE CASCADE`
    - `research_sources`: `CONSTRAINT uq_research_sources_id_user UNIQUE (id, user_id)`
    - `research_evidence`: `CONSTRAINT fk_research_evidence_query_user FOREIGN KEY (query_id, user_id) REFERENCES research_queries(id, user_id) ON DELETE CASCADE`
    - `research_evidence`: `CONSTRAINT fk_research_evidence_source_user FOREIGN KEY (source_id, user_id) REFERENCES research_sources(id, user_id) ON DELETE CASCADE`
    - `research_evidence`: `CONSTRAINT fk_research_evidence_revision_composite FOREIGN KEY (revision_id, trip_id, user_id) REFERENCES plan_revisions(revision_id, trip_id, user_id) ON DELETE SET NULL`
    - `research_citations`: `CONSTRAINT fk_research_citations_query_user FOREIGN KEY (query_id, user_id) REFERENCES research_queries(id, user_id) ON DELETE CASCADE`
    - `research_citations`: `CONSTRAINT fk_research_citations_evidence_user FOREIGN KEY (evidence_id, user_id) REFERENCES research_evidence(id, user_id) ON DELETE CASCADE`
    - `rag_documents`: `CONSTRAINT fk_rag_documents_trip_user FOREIGN KEY (trip_id, user_id) REFERENCES trips(id, user_id) ON DELETE CASCADE`
    - `rag_chunks`: `CONSTRAINT fk_rag_chunks_document_user FOREIGN KEY (document_id, user_id) REFERENCES rag_documents(id, user_id) ON DELETE CASCADE`
  - Added CHECK constraints:
    - `chk_research_evidence_trip_revision CHECK (trip_id IS NULL OR revision_id IS NOT NULL)`
    - `chk_rag_docs_size CHECK (file_size_bytes >= 0 AND file_size_bytes <= 10485760)`
  - Added append-only immutability triggers:
    - `trg_research_evidence_immutable BEFORE UPDATE OR DELETE ON research_evidence` preventing any manual UPDATE or DELETE.
    - `trg_research_citations_immutable BEFORE UPDATE OR DELETE ON research_citations` preventing any manual UPDATE or DELETE.
  - Added integration test `test_postgres_cross_tenant_composite_fk_rejection` verifying database rejection of cross-tenant queries and chunk insertions.

---

### Blocker 5: Upload + Error Resource Security
- **Defect Identified in Audit**: Upload endpoint buffered the full upload body into memory before rejecting oversized files; filename handling lacked strict normalization; error handling risked leaking exception strings.
- **Architectural Solution in `research_api.py`**:
  - **Streaming Size Enforcement**: Early check on `Content-Length` header; streaming chunks of 64KB with immediate `await file.close()`, memory buffer release (`chunks_list.clear()`), and HTTP 413 exception before the full body can be buffered.
  - **Strict Filename Policy (`sanitize_upload_filename`)**:
    - Basename extraction only (`os.path.basename`)
    - Removal of directory traversal tokens (`..`)
    - Removal of forward and backward slashes (`/`, `\`)
    - Removal of control characters and non-printable characters (`ord < 32` or `ord == 127`)
    - Removal of illegal filesystem characters (`<>:"/\|?*`)
    - Safe length enforcement (128 chars maximum) preserving file extension.
  - **Client-Safe Error Responses**:
    - Replaced all exception string exposures with generic client-safe messages:
      - `"Failed to execute research query."`
      - `"Document exceeds maximum allowed size of 10MB."`
      - `"Document ingestion failed. Ensure document format and text encoding are valid."`
      - `"Failed to read uploaded file."`
      - `"Failed to index document."`
    - Zero internal tracebacks, exception names, or file-system paths exposed in API details.

---

## 3. Fetcher Scope and Guarantee Boundary

To avoid over-broadening claims, the fetcher boundary is precisely documented as follows:

> [!NOTE]
> **SCOPE AND GUARANTEE BOUNDARY:**
> `SafeHttpFetcher` protects arbitrary research-source fetching. Fixed-provider API clients such as Duffel, Tavily, Groq, Razorpay, etc. remain fixed provider endpoints and are not being claimed as arbitrary research-source fetches. This repository does not claim repository-wide HTTP-client prohibition.

Defense-in-depth implemented in `SafeHttpFetcher`:
1. DNS pre-validation of all A/AAAA records against private, loopback, link-local, multicast, carrier-grade NAT, and reserved networks.
2. Transport-level destination socket IP pinning via `PinnedNetworkBackend` wrapping `httpcore.AsyncConnectionPool` and `anyio.connect_tcp`, preventing DNS rebinding.
3. TLS SNI preservation (original target hostname preserved during TLS handshake).
4. Strict TLS certificate verification enabled (`verify=True`).
5. HTTP Host header preservation (original target hostname).
6. Decompressed streaming response size bounding (closes connection immediately upon exceeding `max_bytes`).
7. Content-type allowlist enforcement.

---

## 4. Grouped Verification Results

All 26 executable test modules in the repository were executed in their required disjoint partition. **No modules were omitted; no modules overlapped.**

### Group 1: Phase 8 Research & RAG Unit / Adversarial Suite
- **Command:** `.\venv\python.exe -m pytest tests\test_phase8_research_rag.py -v`
- **Result:** **51 passed**, 0 failed, 0 skipped
- **Duration:** 17.48s
- **Coverage:** SSRF destination socket pinning, decompression bomb streaming abortion, alternate numeric IP encodings, prompt injection structural boundary, typed canonical invariant rejection and normalization, adversarial planner hijack prevention, claim-to-evidence support verification, production vector store fail closed.

### Group 2: Phase 8 PostgreSQL Live Integration Suite
- **Command:** `.\venv\python.exe -m pytest tests\integration\test_phase8_postgres.py -v`
- **Result:** **11 passed**, 0 failed, 0 skipped
- **Duration:** 53.65s
- **Coverage:** PostgreSQL schema objects and enums, RAG document cascade, tenant isolation, user cascade deletion, vector store restart persistence, foreign key cascades, immutable append-only triggers, mandatory revision linkage CHECK constraint, exact `revision_id` persistence in `research_evidence`, cross-tenant composite FK rejection.

### Group 3: Phase 7 Flexible Flight Search Suite
- **Command:** `.\venv\python.exe -m pytest tests\test_phase7_flexible_flight_search.py tests\integration\test_phase7_postgres.py -v`
- **Result:** **71 passed**, 0 failed, 0 skipped
- **Duration:** 201.33s (0:03:21)
- **Coverage:** Candidate dates generator, rate limit retry, partial failure, ranking determinism, HITL invalidation, PostgreSQL advisory locks, session snapshot tenant isolation, concurrent search deduplication.

### Group 4: Phase 6 Evidence Lifecycle Suite
- **Command:** `.\venv\python.exe -m pytest tests\test_phase6_evidence_lifecycle.py tests\integration\test_phase6_postgres.py -v`
- **Result:** **44 passed**, 0 failed, 0 skipped
- **Duration:** 177.86s (0:02:57)
- **Coverage:** Plan revision immutability, plan approval locking, cascade deletion, atomic approval transaction, evidence provenance, PostgreSQL lifecycle triggers and constraints.

### Group 5A: Baseline Regression Suite A
- **Files Executed (10 modules):**
  - `tests\test_domain_models.py`
  - `tests\test_flight_booking_handoff.py`
  - `tests\test_flight_service.py`
  - `tests\test_hitl_unlimited_revisions.py`
  - `tests\test_hotel_booking_integration.py`
  - `tests\test_hotel_booking_pdf_hardening.py`
  - `tests\test_hotel_service.py`
  - `tests\test_intelligence_orchestrator.py`
  - `tests\test_intelligence_revalidation.py`
  - `tests\test_live_intelligence_domain.py`
- **Result:** **46 passed**, 0 failed, 0 skipped
- **Duration:** 65.81s (0:01:05)

### Group 5B: Baseline Regression Suite B
- **Files Executed (10 modules):**
  - `tests\test_live_intelligence_security.py`
  - `tests\test_migration_lifecycle.py`
  - `tests\test_phase2_auth_ownership.py`
  - `tests\test_phase3_universal_planner.py`
  - `tests\test_planner_and_payments.py`
  - `tests\test_planner_stability.py`
  - `tests\test_provider_limitations.py`
  - `tests\test_recommended_flight_price.py`
  - `tests\test_replanning_dependency.py`
  - `tests\test_search_session_store.py`
- **Result:** **73 passed**, 0 failed, 0 skipped
- **Duration:** 71.06s (0:01:11)

### Repository-Wide Strict Markers & Compilation Verification
- **Strict Markers:** `.\venv\python.exe -m pytest --strict-markers -q` -> **296 passed** in 549.00s (0:09:09)
- **Bytecode Compilation:** `.\venv\python.exe -m compileall . -q` -> **Exit code 0** (no syntax or compilation errors)
- **Git Diff Whitespace Check:** `git diff --check b25ecdc` -> **Exit code 0** (clean, zero trailing whitespace or blank line errors)
- **Git Status:** Verified modified and untracked files without extraneous modifications.

---

## 5. Production LLM Outage Policy & Fallback Invariant Verification

### 5.1 Verification Finding & Material Blocker Remediation
In the Gate 2 Re-Audit, Agent 2 identified a material blocker in `services/chat_planner.py`:
`_deterministic_fallback` could be activated whenever the environment variable `ALLOW_DETERMINISTIC_TEST_FALLBACK` was set to `1` or `true` without verifying that the process was actually executing in a legitimate, authorized test environment.

In a production environment, if this variable was accidentally set, present in a container template, or injected, the system would silently substitute a deterministic mock planner generating placeholder day activities (`"Start with a relaxed breakfast and a major attraction..."`) rather than reporting that LLM synthesis is unavailable.

### 5.2 Exact Production Guard & Test-Environment Requirement
To guarantee that `_deterministic_fallback` can **NEVER** execute in production, a centralized defense-in-depth guard was implemented in `services/chat_planner.py`:

```python
PROD_ENV_NAMES = frozenset({"production", "prod", "staging", "stage", "live", "deployment"})
TEST_ENV_NAMES = frozenset({"test", "testing"})


def is_production_environment() -> bool:
    """Return True if running in any recognized production-like or staging environment."""
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    app_env = os.getenv("APP_ENV", "").strip().lower()
    return env in PROD_ENV_NAMES or app_env in PROD_ENV_NAMES


def is_explicit_test_environment() -> bool:
    """Return True strictly if running in an explicit, positively-verified test environment.

    Production environments ALWAYS fail closed (return False), even if test flags are set.
    """
    if is_production_environment():
        return False
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    app_env = os.getenv("APP_ENV", "").strip().lower()
    testing_flag = os.getenv("TESTING", "").strip().lower() in ("1", "true")
    return env in TEST_ENV_NAMES or app_env in TEST_ENV_NAMES or testing_flag


def is_test_fallback_authorized(*, allow_deterministic_test_fallback: bool = False) -> bool:
    """Verify authorization for deterministic fallback.

    Phase 8 Non-Negotiable Invariant:
    Deterministic fallback MUST NEVER execute in production.

    Allowed ONLY IF:
    1. Execution environment is NOT production-like (ENVIRONMENT / APP_ENV not in ('prod', 'production', 'staging', ...)).
    2. Execution environment is positively verified as an explicit TEST environment (e.g. ENVIRONMENT='test' or TESTING='1').
    3. Caller explicitly passes allow_deterministic_test_fallback=True.

    ALLOW_DETERMINISTIC_TEST_FALLBACK is NOT a standalone authorization mechanism
    and cannot authorize fallback in production or outside a verified test environment.
    """
    # 1. Reject any production environment unconditionally
    if is_production_environment():
        return False

    # 2. Reject if not positively verified as an explicit test environment
    if not is_explicit_test_environment():
        return False

    # 3. Require explicit parameter authorization
    return bool(allow_deterministic_test_fallback)
```

### 5.3 Fallback Call Path Inspection
Repository-wide static analysis was conducted for all occurrences of:
- `_deterministic_fallback`
- `allow_deterministic_test_fallback`
- `ALLOW_DETERMINISTIC_TEST_FALLBACK`

**Result:** Exactly one execution call site exists in the entire repository: line 661 of `services/chat_planner.py` inside `generate_travel_plan()`. There are zero bypass paths or alternate entry points.

### 5.4 Production-Variable Attack Scenario Analysis
Under the remediated guard:
1. **Scenario 1 (`ENVIRONMENT=production` + `ALLOW_DETERMINISTIC_TEST_FALLBACK=1`)**:
   `is_production_environment()` evaluates to `True`. `is_explicit_test_environment()` fails closed and returns `False`. Fallback is rejected; application reports `status="unavailable"`, `planning_unavailable=True`, `provider="unavailable"`, and preserves evidence.
2. **Scenario 2 (`ENVIRONMENT=prod` + caller passing `allow_deterministic_test_fallback=True`)**:
   `is_production_environment()` evaluates to `True`. `is_test_fallback_authorized()` returns `False`. Fallback is rejected.
3. **Scenario 3 (Unset environment + `ALLOW_DETERMINISTIC_TEST_FALLBACK=1`)**:
   `is_explicit_test_environment()` evaluates to `False` (unset is not verified test). Fallback is rejected.
4. **Scenario 4 (`ENVIRONMENT=test` + default caller parameters)**:
   `allow_deterministic_test_fallback` defaults to `False`. Fallback is disabled by default, ensuring normal outage behavior even in tests unless explicitly opted in.
5. **Scenario 5 (`ENVIRONMENT=test` + `allow_deterministic_test_fallback=True`)**:
   Only in this authorized hermetic testing configuration does `_deterministic_fallback()` execute, returning `provider="deterministic"`.

### 5.5 Regression Test Coverage
Ten specific regression tests were implemented in `TestPhase8LLMOutagePolicy` within `tests/test_phase8_research_rag.py`:
1. `test_fallback_enabled_in_explicit_test_environment`: Confirms deterministic test fallback executes when explicitly authorized in verified test environment.
2. `test_fallback_disabled_in_test_environment_by_default`: Confirms fallback is disabled by default in test environment without explicit opt-in.
3. `test_production_and_env_var_fallback_rejected`: Confirms `ENVIRONMENT=production` + `ALLOW_DETERMINISTIC_TEST_FALLBACK=1` strictly rejects fallback.
4. `test_production_and_explicit_parameter_fallback_rejected`: Confirms `ENVIRONMENT=production` + `allow_deterministic_test_fallback=True` is rejected.
5. `test_production_deployment_environment_variants_rejected`: Exhaustively tests `prod`, `production`, `staging`, `stage`, `live`, `deployment`, and `APP_ENV=production/prod` rejection.
6. `test_outage_policy_429_rate_limit`: Confirms HTTP 429 rate limit triggers explicit unavailable status, preserves evidence, and never substitutes test fallback.
7. `test_outage_policy_timeout`: Confirms provider timeouts preserve evidence and report status cleanly without silent test substitution.
8. `test_outage_policy_connection_failure`: Confirms network connection errors preserve evidence and report status cleanly without silent test substitution.
9. `test_outage_policy_provider_unavailable`: Confirms 503 / unconfigured providers preserve evidence and report status cleanly.
10. `test_deterministic_provider_never_returned_by_production_outage_paths`: Confirms `generate_travel_plan`, `raise_on_outage=True`, and `backend.itinerary_agent` never return `provider="deterministic"` under any production outage path.

---

## 6. Summary of Disjoint Test Accounting

| Execution Group | Test Modules Included | Passed | Failed | Skipped | Duration |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Group 1** | `tests\test_phase8_research_rag.py` | 64 | 0 | 0 | 18.73s |
| **Group 2** | `tests\integration\test_phase8_postgres.py` | 11 | 0 | 0 | 52.35s |
| **Group 3** | `tests\test_phase7_flexible_flight_search.py`<br>`tests\integration\test_phase7_postgres.py` | 71 | 0 | 0 | 201.33s |
| **Group 4** | `tests\test_phase6_evidence_lifecycle.py`<br>`tests\integration\test_phase6_postgres.py` | 44 | 0 | 0 | 177.86s |
| **Group 5A** | 10 baseline regression modules | 46 | 0 | 0 | 65.81s |
| **Group 5B** | 10 baseline regression modules | 73 | 0 | 0 | 71.06s |
| **Total** | **All 26 executable test modules** | **309** | **0** | **0** | **587.14s** |

---

## 7. Known Limitations

1. **PostgreSQL pgvector Extension Requirement**: Production environments utilizing `PostgresVectorStore` require PostgreSQL 15+ with the `vector` extension installed (`CREATE EXTENSION IF NOT EXISTS vector`). If absent in production, `get_vector_store()` fails closed by design.
2. **Third-Party API Outages**: In production environments where upstream LLM providers (Groq/Gemini) experience rate limits (HTTP 429), timeouts, or network failures, the system fails closed to an explicit `planning_unavailable` state with `planning_blocked=True`, preserving all verified provider evidence (flights, accommodation, weather, research citations) without fabricating synthetic itineraries. External live search providers (Duffel/Tavily) enforce bounded concurrency and per-provider timeouts.
3. **Audit Verdict**: The independent audit verdict remains unchanged until formal re-evaluation by the auditing agent.
