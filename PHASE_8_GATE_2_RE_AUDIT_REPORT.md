# Phase 8 Gate 2 Final Independent Re-Audit

**Baseline:** `b25ecdc`
**Scope:** current working tree after deterministic-fallback remediation
**Audit constraints:** no source changes, no commit, no push

## Final conclusion

The previously material Phase 8 blockers are resolved in the inspected
production paths. The deterministic planner fallback is now guarded by all
three required conditions: a positively verified explicit test environment,
an explicit `allow_deterministic_test_fallback=True` argument, and rejection
of every recognized production-like environment. The environment variable
`ALLOW_DETERMINISTIC_TEST_FALLBACK` is not read as authorization.

Current verification reproduces the claimed result: **309 passed, 0 failed,
0 skipped across 26 executable test modules**.

## 1. LLM outage policy

### Actual call-site audit

Search of the current tree found:

* `_deterministic_fallback` is defined once and called only from
  `generate_travel_plan` in [`services/chat_planner.py`](services/chat_planner.py).
* `allow_deterministic_test_fallback` is accepted by that function and passed
  only to `is_test_fallback_authorized`.
* `ALLOW_DETERMINISTIC_TEST_FALLBACK` appears only in the authorization
  documentation/tests; it is not consulted as a positive authorization flag.

`is_test_fallback_authorized` first rejects
`ENVIRONMENT` or `APP_ENV` values in:
`production`, `prod`, `staging`, `stage`, `live`, and `deployment`.
It then requires an explicit test marker (`ENVIRONMENT=test`,
`APP_ENV=test`, or `TESTING=1/true`) and finally requires the explicit
function argument to be true.

| Scenario | Actual result |
|---|---|
| Production + environment variable | Fallback impossible; returns unavailable |
| Production + explicit parameter | Fallback impossible; production guard wins |
| `prod`, `staging`, `stage`, `live`, `deployment`, or corresponding `APP_ENV` | Fallback impossible |
| Unset environment + environment-variable flag | Fallback impossible; no verified test environment and the variable is not authorization |
| Explicit test environment + explicit parameter | Fallback allowed |
| Explicit test environment without explicit parameter | Normal outage behavior |

When both LLM providers fail without verified test authorization,
`_build_unavailable_plan` returns:

* `provider="unavailable"`;
* `status="unavailable"`;
* `planning_unavailable=True`;
* `daily_plan=[]`;
* preserved live flight, hotel, weather, and research-advisory data.

`raise_on_outage=True` raises `PlannerOutageError` carrying the structured
unavailable plan. [`backend.py`](backend.py) preserves the plan/evidence,
sets `planning_blocked=True`, and does not route the result to approval.
Successful provider execution still returns the typed normal `TravelPlan`.

**LLM outage blocker: PASS.**

## 2. Original Phase 8 blocker audit

| # | Blocker | Verdict | Concrete implementation evidence | Remaining limitation |
|---|---|---|---|---|
| 1 | Structural research/planner separation | **PASS** | [`backend.py`](backend.py) keeps `research_advisory` in a separate state field and passes it as a separate typed argument. [`services/chat_planner.py`](services/chat_planner.py) excludes advisory prose from `_compact_context`, `HumanMessage`, and `SystemMessage`; it is attached as result data only. | This is an application boundary; it does not claim an LLM is incapable of being influenced by other user-controlled fields. |
| 2 | Typed canonical invariant enforcement | **PASS** | `enforce_complete_canonical_invariants` is applied to successful provider output, deterministic test output, and unavailable output. It validates/normalizes route, dates/duration, passenger data, selected offers, currency, and revision identity from server-side context. | Provider prose remains an input to typed parsing, but canonical values are checked against server context before return. |
| 3 | Revision-safe evidence persistence | **PASS** | [`services/research/evidence_bridge.py`](services/research/evidence_bridge.py) rejects missing user/trip/revision identity for trip-linked evidence. The orchestrator resolves the authoritative revision and fails closed when it cannot. Research evidence stores exact trip/revision identity. | Research persistence and Phase 6 bridge persistence are separate operations; database constraints prevent invalid rows, while cross-store atomicity depends on the surrounding transaction/orchestration boundary. |
| 4 | Production vector-store construction | **PASS** | [`services/research/rag/vector_store.py`](services/research/rag/vector_store.py) uses PostgreSQL through `get_vector_store()` and fails closed when persistence is unavailable. Memory storage remains an explicit test/development implementation. | No semantic limitation material to the stated production guarantee was found. |
| 5 | SafeHttpFetcher source boundary | **PASS** | [`services/research/orchestrator.py`](services/research/orchestrator.py) routes arbitrary provider-returned source retrieval through `SafeHttpFetcher.fetch`. No alternate arbitrary research URL path was found. Fixed provider API clients are intentionally outside this boundary. | Fixed provider/weather/currency clients use their own fixed endpoints, consistent with the documented scope. |
| 6 | SSRF/DNS rebinding controls | **PASS** | [`services/research/safe_fetcher.py`](services/research/safe_fetcher.py) validates DNS answers, rejects private/reserved/loopback/link-local/metadata and alternate IP encodings, passes the validated IP to `anyio.connect_tcp`, preserves hostname authority for TLS SNI/certificate verification and Host, and manually revalidates every redirect hop. Decompressed streaming output is bounded and overflow closes the response. | Tests are deterministic transport simulations, not live external-network proof. The inspected code path provides the claimed transport guarantee. |
| 7 | Claim-to-evidence verification | **PASS** | Research synthesis performs bounded claim-support checks for contradictions, dates, numerical values, and lexical support, quarantining unsupported/contradicted claims. | This is explicitly a bounded heuristic, not semantic truth or formal entailment. |
| 8 | Database tenant/lifecycle integrity | **PASS** | [`migrations/006_research_and_rag.sql`](migrations/006_research_and_rag.sql) defines tenant-scoped FKs, composite trip/revision/user checks, mandatory revision linkage, uniqueness/range constraints, cascade rules, and immutability triggers. | No material bypass was found in the tested production schema. |
| 9 | Single migration authority | **PASS** | The top-level Phase 8 migration is authoritative; [`migrations/versions/README.md`](migrations/versions/README.md) marks the legacy directory reference-only. The PostgreSQL migration-authority test passes. | The working tree contains uncommitted remediation/report files by instruction; this is not a migration-authority failure. |
| 10 | Upload/error security | **PASS** | [`research_api.py`](research_api.py) streams uploads in bounded chunks, enforces the 10 MB cap before unbounded buffering, sanitizes filenames, invokes bounded ingestion, logs internal errors without returning exception details, and returns generic client errors. PDF magic-byte, decompression, text, and chunk limits execute on the ingestion path. | JSON parsing has less explicit depth/CPU bounding than PDF parsing; no bypass of the stated upload/PDF protections was found. |

## 3. Fetcher transport assessment

The arbitrary research-source path has deterministic code-path proof for:

1. validated DNS/IP data being passed to the TCP connection;
2. no second unconstrained DNS lookup being used for the socket destination;
3. original hostname retained for TLS SNI and certificate hostname checking;
4. original hostname authority retained for Host behavior;
5. redirect rejection/manual follow with validation and repinning at each hop;
6. rejection of private, reserved, loopback, link-local, metadata, mapped IPv6,
   decimal, hexadecimal, octal, and abbreviated IPv4 forms;
7. streaming decompressed-byte limits and response closure on overflow.

This is deterministic transport/code-path proof, not proof from a live external
Internet/DNS experiment. The documented scope is arbitrary research-source
fetching, not every fixed provider API client in the repository.

## 4. Current disjoint verification matrix

`tests/**/*.py` was enumerated. Excluding the two `__init__.py` files leaves
**26 executable modules**, and each appears exactly once below.

| Group | Modules | Passed | Failed | Skipped | Duration |
|---|---|---:|---:|---:|---:|
| 1 | `tests/test_phase8_research_rag.py` | 64 | 0 | 0 | 19.16s |
| 2 | `tests/integration/test_phase8_postgres.py` | 11 | 0 | 0 | 59.51s |
| 3 | `tests/test_phase7_flexible_flight_search.py`; `tests/integration/test_phase7_postgres.py` | 71 | 0 | 0 | 219.09s |
| 4 | `tests/test_phase6_evidence_lifecycle.py`; `tests/integration/test_phase6_postgres.py` | 44 | 0 | 0 | 184.92s |
| 5A | 11 baseline regression modules (domain, flight, hotel, intelligence) | 50 | 0 | 0 | 64.65s |
| 5B | 9 baseline regression modules (migration, planner, provider, session) | 69 | 0 | 0 | 68.69s |
| **Total** | **26 modules** | **309** | **0** | **0** | **616.02s** |

Additional checks:

| Command | Result |
|---|---|
| `.\venv\python.exe -m pytest --strict-markers -q` | **309 passed**, 1 deprecation warning, 513.41s |
| `.\venv\python.exe -m compileall . -q` | Passed |
| `git diff --check b25ecdc` | Passed; Git reported only LF/CRLF conversion warnings |
| `git status --short --untracked-files=all` | Expected remediation/source/test/migration/report modifications; no commit or push |

No test module was omitted or intentionally skipped.

## 5. Residual limitations

* Claim support is heuristic and bounded, not semantic entailment.
* SafeHttpFetcher verification is deterministic and test-simulated rather than
  a live external-network experiment.
* Cross-store research/Phase 6 persistence is not a distributed transaction,
  although invalid tenant/revision rows are rejected by code and database
  constraints.
* JSON ingestion has weaker explicit parser resource limits than the hardened
  upload/PDF path.

These limitations do not leave a material unresolved blocker under the
requirements of this audit.

## Final verdict

GATE 2 AUDIT: PASS
