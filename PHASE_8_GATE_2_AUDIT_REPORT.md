# TrustTrip Phase 8 Gate 2 Independent Audit

**Baseline:** `b25ecdc feat: complete phase 7 flexible live flight search`
**Scope:** Global Flight Research and RAG
**Audit type:** Independent security, architecture, implementation, and regression audit
**Source changes by auditor:** None

## 1. Executive summary

The implementation contains a credible Phase 8 skeleton: deterministic intent
routing, a Tavily adapter port, an SSRF-aware fetcher, immutable Pydantic
research models, tenant-filtered in-memory vector retrieval, a research API,
document ingestion, an evidence bridge, and isolated PostgreSQL migration
tests.

The independently grouped verification pass reproduced 283 passing tests:
41 Phase 8 unit tests, 8 Phase 8 PostgreSQL tests, 62 Phase 7 unit tests,
9 Phase 7 PostgreSQL/concurrency tests, 50 remaining flight/hotel/intelligence
tests, 92 remaining planner/lifecycle tests, and 21 Phase 6 PostgreSQL tests.
The two earlier 271-test full-suite results are historical and do not replace
this current grouped verification.

The implementation is **not safe to freeze**. The principal blockers are:

1. Research output is inserted into the downstream planner as ordinary human
   feedback, so the research prompt-injection boundary does not survive the
   planner handoff.
2. The default production RAG path uses `MemoryVectorStore` and a deterministic
   hashing embedding provider. There is no implemented `PostgresVectorStore`
   or pgvector persistence despite the architecture and public implementation
   surface implying production vector support.
3. Research evidence is bridged to Phase 6 with `revision_id=None`, and the
   bridge contains UUID fallbacks. Research evidence therefore is not
   revision-linked by the actual orchestration path and can create evidence
   records outside the required canonical revision lifecycle.
4. The research migration lacks foreign keys/tenant consistency for evidence
   and citations, immutability enforcement, query/cache identity fields, and
   revision applicability fields.

## 2. Exact diff reviewed

Tracked files modified against `b25ecdc`:

* `app.py`
* `backend.py`
* `domain/research.py`

Untracked Phase 8 files:

* `.vscode/settings.json`
* `PHASE_8_GATE_1_ARCHITECTURE.md`
* `migrations/006_research_and_rag.sql`
* `migrations/versions/006_research_and_rag.sql`
* `research_api.py`
* `services/research/`
* `tests/integration/test_phase8_postgres.py`
* `tests/test_phase8_research_rag.py`

`git diff --stat b25ecdc` reports only the three tracked files because all
Phase 8 implementation files are currently untracked. No dependency manifest
or Phase 1–7 migration was changed. No secrets were found in the diff. The
`.vscode/settings.json` file is an unrelated workspace configuration artifact
and should not be part of a Phase 8 implementation commit.

## 3. Architecture deviations

The architecture specifies a provider-neutral, production RAG design with a
vector-store port and pgvector as the preferred production implementation.
The actual API initializes:

```python
_vector_store = MemoryVectorStore()
_embedding_provider = DeterministicEmbeddingProvider()
```

The repository contains `MemoryVectorStore`, but no implemented
`PostgresVectorStore`. Uploaded documents are saved to PostgreSQL while their
embeddings remain in process memory. A new worker loses the index, workers do
not share it, and process restart loses all retrieval capability. The
deterministic embedding is a test-oriented hashing implementation and is the
default API provider rather than an explicit test-only dependency.

This is a production architecture/implementation mismatch.

## 4. Live-flight/research boundary

The deterministic router correctly recognizes common live-shopping phrases
such as “find flights,” fare/price language, and booking language before most
research rules. Research responses do not construct `OfferSnapshot` objects,
supplier offer IDs, live prices, or booking URLs.

The boundary is not complete in the planner integration. `backend.py` takes
the resulting `research_summary` and appends it to `effective_feedback`; the
planner then places that string in the LLM prompt as human feedback. A
malicious source can therefore influence the planning model as if it were a
user instruction. The planner must receive research as typed, explicitly
untrusted evidence, followed by server-side validation of any generated
canonical plan.

## 5. Intent routing

The router is deterministic and has explicit live, research, document, hybrid,
and ambiguous outcomes. It does not delegate privileged tool selection to an
LLM. However:

* explicit `mode="web"` can make otherwise vague text a web-research request;
* explicit document IDs are not independently authorized before orchestration;
* the planner creates research-agent context using `uuid4()` when a trip/user
  identity cannot be parsed, which is not an acceptable production identity
  fallback;
* query routing is tested mainly at the unit/service level, not through every
  public planner/API path with adversarial mixed wording.

## 6. Web research security

`ResearchProviderPort` exists and Tavily uses async `httpx`. The adapter has a
semaphore, bounded result count, retries, cancellation propagation, and
duplicate URL suppression.

Limitations:

* the adapter keeps a reusable `AsyncClient` without an application lifecycle
  close hook;
* retry sleeps are not bounded against one request-wide deadline;
* search results are snippets only; the orchestrator does not use
  `SafeHttpFetcher` to fetch and validate source content before treating the
  snippet as evidence;
* provider error strings include upstream response text in some paths and are
  logged by the API with `exc_info=True`.

## 7. SSRF analysis

The standalone `SafeHttpFetcher` rejects HTTPS violations, localhost,
loopback, RFC1918, link-local, multicast, reserved, unspecified, IPv4-mapped
IPv6, userinfo, and disallowed internal hostnames. It revalidates redirects,
limits redirect depth, checks DNS results, enforces a content-type allowlist,
and caps response bytes.

The implementation has important limitations:

* it calls `response = await client.get(...)` without streaming, so the HTTP
  client may fully buffer a large response before the post-read
  `max_bytes` check; this is not a true streamed size-abort control;
* DNS is resolved before the request but the resolved address is not pinned to
  the connection, leaving a practical DNS-rebinding window;
* decimal/octal/alternate host representations depend on parser behavior and
  are not covered by the supplied adversarial tests;
* this fetcher is not wired into the Tavily search result path, so its SSRF
  controls do not protect all research-source retrieval.

## 8. Document security

Upload endpoints require authentication and enforce a 10 MB byte limit.
Document, chunk, and vector reads are user-scoped. Filename values are stored
as metadata and are not used as filesystem paths, so the reviewed path does
not directly expose traversal.

The parser accepts content based largely on filename or declared MIME type,
does not verify magic bytes, reads the entire upload into memory, and parses
PDF streams with regex/zlib without explicit decompression-ratio, page-count,
or extraction-output limits. Malformed and adversarial PDFs can therefore
consume disproportionate CPU/memory. Temporary-file cleanup is not relevant
to the current in-memory parser, but the design has no isolated parser worker
or timeout.

## 9. Prompt-injection analysis

The research synthesizer uses a separate system instruction and an evidence
envelope with limited delimiter replacement. That is useful within the
synthesizer, but it is not the complete security boundary:

* source snippets and source titles are inserted into conflict notices outside
  the evidence envelope;
* the synthesized `research_summary` is passed to the planner as human
  feedback;
* downstream planner instructions do not structurally mark it as untrusted;
* there is no post-LLM canonical-trip invariant check proving that dates,
  passengers, route, budget, and selected offers were unchanged.

This is a medium-severity prompt-injection/tool-integrity defect and blocks
approval because uploaded or web-controlled text can influence privileged
planning behavior.

## 10. RAG analysis

The in-memory vector store filters by `user_id` and optional document IDs and
supports deletion. The deterministic embedding has stable dimensions and
normalization. The reranking and hybrid merge paths are bounded in the tested
cases.

The production path is incomplete:

* no PostgreSQL/pgvector vector store implementation exists;
* embeddings are not persisted in migration 006;
* `top_k` is accepted by the vector store without enforcing a hard upper bound
  at the store boundary;
* embedding dimensions are not validated on upsert;
* document IDs are filtered by user at retrieval but are not prevalidated for
  ownership or trip scope at the API boundary;
* stale/deleted persisted chunks and in-memory vectors do not share a
  transactional deletion lifecycle.

## 11. Evidence bridge analysis

`ResearchEvidenceBridge` can construct an immutable Phase 6
`EvidenceRecord`, but the actual orchestrator calls it with
`revision_id=None`. The bridge then persists the evidence without a
`PlanRevisionEvidenceLink`.

The convenience methods also generate random trip/user IDs when context is
missing. That creates fabricated ownership context instead of failing closed.
The research evidence applicability key contains source metadata but omits the
research query, provider policy, freshness policy, and revision context needed
to distinguish incompatible research results.

The bridge is therefore not equivalent to the frozen Phase 6 evidence
lifecycle in the actual production path.

## 12. Citation analysis

The synthesizer extracts `[cite:N]` tokens and maps them to retrieved evidence
by list position. It does not prove that every substantive claim has a
citation; one citation is enough to mark the result complete. It also accepts
LLM-generated citation tokens based on position rather than requiring a
claim-to-evidence validation pass.

The no-LLM path returns citations from real evidence, which is safe, but
`ResearchSynthesizer.synthesize()` includes exception text directly in the
returned answer. That can expose upstream/provider details to callers of that
method even though the main orchestrator catches only its dedicated
`LLMProviderUnavailableError`.

## 13. Cache/freshness analysis

No implemented research cache was found. The actual query model has no
provider-policy version, retrieval criteria hash, cache key, source freshness
policy, or document-scope cache semantics. This is safer than unsafe reuse,
but it does not satisfy the designed cache/freshness contract and leaves
repeated requests uncached and difficult to invalidate deterministically.

Phase 7 `OfferSnapshot` data is not reused as research evidence.

## 14. Tenancy and IDOR analysis

The API requires authentication. PostgreSQL repositories scope sessions,
documents, queries, evidence, and citations by user in their direct lookup
methods. The memory vector store filters by user.

Database-level enforcement is incomplete:

* `research_evidence.source_id` has no foreign key;
* `research_citations.evidence_id` has no foreign key;
* there are no composite tenant-consistency constraints tying query,
  evidence, citation, source, document, and chunk owners together;
* research evidence has no `trip_id` or revision column in migration 006;
* the API accepts arbitrary requested document IDs and relies on retrieval
  filtering rather than explicit authorization failure semantics.

The supplied tests cover basic tenant filtering but do not prove all
cross-user citation, deletion, vector, and session paths against real
PostgreSQL.

## 15. Database and migration analysis

Migration 006 uses schema-aware enum checks and was successfully applied in
the supplied isolated-schema tests. It has reasonable user foreign keys and
document/chunk deletion cascades.

It is not a complete production evidence schema:

* evidence and citations lack foreign keys to their referenced rows;
* no immutable append-only triggers protect research evidence/citations;
* no query cache identity or source freshness columns exist;
* no embedding/vector storage exists;
* no revision linkage exists on research evidence;
* `research_queries.intent` and related fields are free-form text despite
  enum types being created;
* no uniqueness constraints prevent duplicate source/evidence/citation rows;
* no request-time DDL was observed.

The duplicate migration copy in `migrations/versions/006_research_and_rag.sql`
must be reconciled with the repository's migration execution convention to
avoid applying two independently tracked versions.

## 16. LangGraph analysis

The research agent is conditionally selected, and the state stores bounded
summary/reference fields rather than raw documents. However, the agent:

* falls back to random UUIDs for missing/unparseable identity;
* calls `_run_async` through a `ThreadPoolExecutor`, which is unnecessary for
  an async research service and complicates cancellation;
* catches all exceptions and embeds exception text into `research_summary`;
* passes that summary into planner feedback without structural untrusted-data
  isolation.

These behaviors weaken the stated planner boundary.

## 17. LLM/provider analysis

Groq uses native async HTTP, an external API key/model, timeout, and safe
failure categories. It does not retry, enforce a global request deadline, or
validate response shape beyond the first choice/content lookup. Error
messages include response text and are logged by callers.

The deterministic test LLM is not selected by the default orchestrator, which
is correct, but the default deterministic embedding provider remains selected
in the production API.

## 18. Test-quality analysis

The 33 Phase 8 tests are useful unit/service tests, but many security
properties are mock-level:

* SSRF tests patch DNS/HTTP behavior rather than exercising a real redirecting
  server and rebinding scenario;
* prompt-injection tests stop at the research synthesizer and do not exercise
  the planner handoff;
* vector tests use `MemoryVectorStore`;
* citation tests validate token extraction rather than complete
  claim-to-citation semantics;
* migration tests verify table creation/basic tenant queries but not evidence
  immutability, foreign-key consistency, concurrent duplicate source
  insertion, or citation integrity.

The Phase 8 PostgreSQL tests are real isolated-schema tests for migration,
basic document/chunk cascade, tenant filtering, and user deletion, but do not
cover all DB-critical invariants requested by the Gate 2 specification.

## 19. Phase 1–7 regression

The targeted Phase 7 suite passed. No direct changes to the Phase 7 flight
provider implementation were found in the tracked diff. The research API does
not directly create flight snapshots or booking handoffs.

The planner integration is still a regression risk because untrusted research
text is injected into the planner feedback channel. This weakens the frozen
provider-isolation and canonical-trip integrity expectations even though the
Phase 7 unit tests remain green.

## 20. Findings by severity

| # | Severity | Location | Finding | Confidence |
|---|---|---|---|---|
| 1 | HIGH | `backend.py` research-agent/planner handoff; `services/chat_planner.py` | Untrusted web/document research is inserted as ordinary human feedback and can influence privileged planning instructions. | 9/10 |
| 2 | HIGH | `services/research/orchestrator.py`, `services/research/evidence_bridge.py`, `migrations/006_research_and_rag.sql` | Research evidence is not revision-linked in the actual path; bridge permits fabricated fallback identity and migration lacks revision/tenant integrity fields. | 9/10 |
| 3 | HIGH | `research_api.py`, `services/research/rag/vector_store.py` | Default API RAG uses process-local memory and deterministic test embeddings; no production persistent vector store is implemented. | 9/10 |
| 4 | MEDIUM | `services/research/safe_fetcher.py` | Response-size enforcement buffers the full response before checking size, and DNS validation is not connection-pinned; fetcher is not wired into all source retrieval. | 8/10 |
| 5 | MEDIUM | `services/research/rag/ingestion.py` | MIME/content mismatch, PDF decompression/output/page limits, and parser isolation are insufficient for hostile documents. | 8/10 |
| 6 | MEDIUM | `services/research/synthesizer.py`, `services/research/security.py` | Citation completeness is not actually proven; conflict text is outside the evidence envelope, and one failure helper leaks exception text. | 8/10 |
| 7 | MEDIUM | `migrations/006_research_and_rag.sql` | Evidence/citation references lack foreign keys, immutability protection, uniqueness, and revision applicability. | 9/10 |
| 8 | LOW | `.vscode/settings.json` | Unrelated untracked workspace configuration is present in the Phase 8 tree. | 10/10 |

## 21. Remediation required

Before freeze:

1. Pass research to the planner through a dedicated typed untrusted-evidence
   field, never human-feedback text; validate generated canonical trip fields
   server-side.
2. Fail closed on missing identity and require the actual trip/user/revision
   context for evidence bridging.
3. Make the Phase 6 bridge revision-aware and persist the revision link in the
   same lifecycle transaction.
4. Implement and select a persistent tenant-filtered vector store, or remove
   production claims and disable private RAG outside a clearly test-only mode.
5. Add migration foreign keys, tenant consistency, immutable evidence/citation
   protection, revision linkage, source/query identity, and embedding storage.
6. Stream fetch responses with a hard byte limit, revalidate/pin resolved
   addresses where practical, and wire safe fetching into source retrieval.
7. Harden document type detection and PDF resource limits/isolation.
8. Enforce claim-level citation coverage and redact all synthesis exception
   text.
9. Add real planner-handoff prompt-injection tests, persistent vector tests,
   evidence bridge/revision tests, concurrent source/citation tests, and
   live HTTP SSRF tests.
10. Remove the unrelated `.vscode` artifact before implementation commit and
    reconcile the duplicate migration location.

## 22. Recommended additional tests

* A malicious web source and malicious uploaded PDF attempting to mutate the
  canonical route, dates, passengers, budget, or flight selection through the
  planner.
* User A document queried/deleted/cited by User B through API and PostgreSQL.
* Two workers querying the same private document after one process restarts.
* Concurrent duplicate source/evidence/citation insertion.
* Evidence bridge with current and stale revisions, including rollback and
  deletion.
* Real local redirect chains to loopback/private addresses.
* Streaming response that exceeds the byte limit before completion.
* Encoded, decimal, IPv4-mapped, and Unicode/IDN SSRF targets.
* Malformed and highly compressed PDFs with bounded CPU/memory behavior.
* Citation coverage for every factual claim, conflicts, no evidence, and
  malformed LLM output.

## 23. Exact test results

Current grouped verification, using the same `.\venv\python.exe` environment:

| Group | Command | Result | Duration |
|---|---|---:|---:|
| Phase 8 unit/adversarial | `.\venv\python.exe -m pytest tests\test_phase8_research_rag.py -v` | **41 passed, 0 failed, 0 skipped** | 7.96s |
| Phase 8 PostgreSQL | `.\venv\python.exe -m pytest tests\integration\test_phase8_postgres.py -v` | **8 passed, 0 failed, 0 skipped** | 42.41s |
| Phase 7 unit/regression | `.\venv\python.exe -m pytest tests\test_phase7_flexible_flight_search.py -v` | **62 passed, 0 failed, 0 skipped** | 43.04s |
| Phase 7 PostgreSQL/concurrency | `.\venv\python.exe -m pytest tests\integration\test_phase7_postgres.py -v` | **9 passed, 0 failed, 0 skipped** | 204.71s |
| Remaining flight/hotel/intelligence | 11 remaining unit modules | **50 passed, 0 failed, 0 skipped** | 70.06s |
| Remaining planner/lifecycle | 10 remaining unit modules | **92 passed, 0 failed, 0 skipped** | 82.66s |
| Phase 6 PostgreSQL | `.\venv\python.exe -m pytest tests\integration\test_phase6_postgres.py -v` | **21 passed, 0 failed, 0 skipped** | 180.04s |

Grouped total: **283 passed, 0 failed, 0 skipped**. This is a complete
partition of the 283 collected repository tests; no tests were omitted to
reduce runtime. A new single-command full-suite run was not required for this
verification pass; the earlier historical full-suite runs reported 271 tests
before the additional Phase 8 tests were present.

Historical checks:
* `.\venv\python.exe -m pytest -q` — **271 passed** (first historical full pass)
* `.\venv\python.exe -m pytest -q` — **271 passed** (second historical full pass)
* `.\venv\python.exe -m pytest --strict-markers -q` — **271 passed**
* `.\venv\python.exe -m compileall . -q` — **passed**
* `git diff --check b25ecdc` — **passed**
* `git status --short` — tracked Phase 8 edits plus untracked Phase 8 implementation/tests/report and `.vscode/settings.json`
* `git diff --stat b25ecdc` — tracked diff is `app.py`, `backend.py`, and `domain/research.py`; untracked files are not included by Git's diff stat

## 24. Final verdict

GATE 2 AUDIT:
CHANGES REQUIRED
