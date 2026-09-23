# TrustTrip Phase 8 Gate 1 Architecture

**Scope:** Global Flight Research and RAG-based Deep Travel Research
**Baseline:** `b25ecdc feat: complete phase 7 flexible live flight search`
**Status:** Gate 1 architecture; zero source-code changes
**Decision:** `GATE 1 VERDICT: APPROVED`

## 1. Executive summary

Phase 8 adds an evidence-grounded research capability without changing the
Phase 7 transactional flight path. Research answers explain airlines,
airports, routes, policies, destinations, and travel guidance. Live prices,
availability, schedules, supplier inventory, and booking links remain
exclusively Phase 7 concerns.

The design uses:

* a deterministic, policy-controlled research intent router;
* provider ports for web search, page fetching, embeddings, reranking, and
  synthesis;
* immutable, tenant-aware research evidence linked to existing evidence
  lifecycle concepts;
* a bounded web retrieval pipeline and a document ingestion pipeline;
* hybrid retrieval with explicit conflict detection;
* citations attached to every substantive answer claim;
* isolated untrusted content, SSRF defenses, and prompt-injection controls;
* references rather than large documents in LangGraph state.

Gate 2 may implement this design behind feature flags. It must not modify the
Phase 7 flight, selection, revalidation, or booking contracts.

## 2. Goals and non-goals

### Goals

1. Answer general flight and travel research questions with source attribution.
2. Support private uploaded documents and trusted public sources.
3. Offer web, RAG, and combined research modes with graceful degradation.
4. Preserve evidence immutability, provenance, tenancy, and revision binding.
5. Make provider, model, vector store, and fetcher implementations replaceable.
6. Bound cost, latency, result counts, document sizes, and concurrency.

### Non-goals

* No research-derived live fare, availability, schedule, or booking URL.
* No automatic mutation of canonical `TripRequest`.
* No use of research cache as an `OfferSnapshot` cache.
* No arbitrary URL fetching, redirects, tool invocation, or credential access
  from retrieved content.
* No global rewrite of the existing LangGraph supervisor.
* No mandatory vector-database dependency in the first migration.

## 3. Hard boundary

```text
LIVE TRANSACTIONAL FLIGHT DATA
criteria -> Phase 7 Duffel -> OfferSnapshot -> selection
         -> revalidation -> Phase 5 booking handoff

RESEARCH INFORMATION
query -> intent policy -> web/RAG retrieval -> evidence validation
      -> conflict-aware synthesis -> answer + citations
```

The research domain may say that a route is commonly cheaper on a weekday, but
it may never turn that statement into a TrustTrip price or offer. A request for
current inventory is classified as live shopping and routed to the Phase 7
capability. Research responses contain no provider offer IDs or raw supplier
payloads.

## 4. Architecture

```text
Authenticated API
      |
ResearchRequest
      |
Deterministic ResearchIntentRouter
      |-------------------|
      v                   v
WebResearchService   RagResearchService
      |                   |
SearchProviderPort    DocumentStorePort
Fetch/Normalize       ChunkStorePort
      |               EmbeddingPort
      |               VectorStorePort
      |-------------------|
               v
EvidenceValidator -> EvidenceMerger -> ConflictDetector
               v
       CitationAwareSynthesizer
               v
ResearchResult {answer, citations, evidence metadata}
```

The application service owns deadlines, authorization, limits, cancellation,
and error mapping. Adapters own vendor protocols. Domain models do not import
Tavily, Groq, HTTP clients, FAISS, or a particular vector database.

## 5. Research intent router

```text
GLOBAL_FLIGHT_RESEARCH
DESTINATION_RESEARCH
AIRPORT_RESEARCH
AIRLINE_RESEARCH
ROUTE_RESEARCH
POLICY_RESEARCH
TRAVEL_ADVISORY_RESEARCH
DOCUMENT_RAG_RESEARCH
COMBINED_WEB_RAG_RESEARCH
LIVE_FLIGHT_SEARCH
AMBIGUOUS_RESEARCH
```

The router is deterministic and policy-first:

1. Explicit live-shopping signals (`price`, `availability`, `book`, exact
   dates plus fare search) route to `LIVE_FLIGHT_SEARCH`.
2. Upload/document references route to `DOCUMENT_RAG_RESEARCH`.
3. Policy, baggage, airline, airport, route, destination, and advisory
   vocabulary selects the corresponding research intent.
4. A request containing both private-document context and current-web signals
   routes to `COMBINED_WEB_RAG_RESEARCH`.
5. Ties return `AMBIGUOUS_RESEARCH` with a clarification request; the LLM
   cannot select tools or elevate capabilities.

Each decision contains `intent`, bounded `confidence`, matched rule IDs,
required sources, and whether live-shopping handoff is required. Low
confidence never silently performs a broader or more privileged operation.

## 6. Web research architecture

```python
class ResearchProviderPort(Protocol):
    async def search(self, query, filters, deadline) -> list[ResearchSource]: ...
    async def fetch(self, source, deadline) -> FetchedSource: ...
    def normalize(self, source) -> NormalizedSource: ...
    def extract_content(self, source) -> ExtractedContent: ...
```

Implementations include `TavilyResearchProvider`, future providers, and a
deterministic `TestResearchProvider`. The service, not the adapter, enforces:

* at most 3 search calls and 5 fetched pages per request;
* at most 3 concurrent outbound operations;
* a 20-second request budget and shorter per-operation deadlines;
* two retries only for transient failures, with bounded jittered backoff;
* no retry for invalid URLs, authorization failures, or malformed content;
* cancellation propagation and awaited task cleanup;
* URL canonicalization and duplicate suppression;
* response-size, content-type, and extraction limits.

A failed provider may be replaced by a configured provider fallback. Provider
errors are mapped to stable safe categories and never expose credentials,
headers, raw payloads, or stack traces.

## 7. RAG architecture

### Ingestion

```text
upload/source registration
 -> size/type/signature validation
 -> malware and decompression-bomb checks
 -> text extraction in an isolated worker
 -> normalization and hidden-text policy
 -> deterministic chunking
 -> metadata enrichment
 -> embedding batch
 -> vector index
```

Documents are immutable by content hash. Chunk IDs include document hash and
chunk ordinal. The initial implementation uses PostgreSQL metadata and a
`VectorStorePort`; pgvector is the preferred production implementation when
available, while a bounded test/in-memory implementation is permitted only in
tests. No giant document or embedding vector is placed in graph state.

### Query

```text
normalize query
 -> authorize document scope
 -> lexical and vector retrieval
 -> metadata/tenant filtering
 -> duplicate removal
 -> optional rerank
 -> top-k context construction
 -> citation-aware synthesis
```

Initial limits: `top_k=8`, rerank input at most 20 chunks, context at most
12,000 tokens, minimum similarity threshold configured per source type, and no
more than 6 chunks from one document. Retrieval returns no evidence below the
threshold rather than inventing an answer.

Ports:

* `EmbeddingProviderPort.embed(texts, deadline)`
* `VectorStorePort.upsert/query/delete`
* `RerankerPort.rank(query, passages, deadline)`
* `LLMProviderPort.synthesize(prompt, evidence, deadline)`

## 8. Hybrid retrieval and conflicts

Web and RAG evidence are normalized into the same internal evidence envelope,
then deduplicated by canonical URL/content hash/claim identity. Evidence is
never silently merged when claims conflict.

Conflict resolution is explicit:

1. retain both immutable records;
2. compare source authority classification;
3. prefer newer information only when authority is equal and publication or
   retrieval dates are reliable;
4. mark unresolved conflicts;
5. surface the conflict and citations in the answer.

An official current airline policy may supersede an older secondary guide, but
the older source remains visible as historical/conflicting evidence.

## 9. Evidence and citation model

Reuse the existing evidence lifecycle for ownership, immutable records,
provenance, and revision links, with a research-specific evidence taxonomy
and metadata rather than reusing flight `OfferSnapshot`.

Canonical research evidence fields:

```text
evidence_id, tenant/user scope, trip/revision scope (optional)
source_id, source_url, canonical_url, title, publisher/domain
source_type, trust_class, retrieved_at, published_at
content_hash, extracted_claims, locator, snippet reference
relevance_score, credibility metadata, freshness state
immutable payload reference, created_at
```

Citation objects expose only:

```text
 citation_id, evidence_id (opaque public reference), title, publisher,
 canonical_url, published_at, retrieved_at, locator, cited_claim_ids
```

Internal provider IDs, storage keys, credentials, encrypted payloads, and
private document paths are never serialized. Every substantive claim must have
one or more citation IDs. If coverage is incomplete, the answer states
`Evidence not found` or `Evidence is insufficient` rather than guessing.

## 10. Source trust classification

Trust labels are descriptive, not numeric quality claims:

* `OFFICIAL`: airline, airport, government, regulator, or destination authority.
* `TRUSTED_SECONDARY`: established publisher with identifiable editorial
  ownership.
* `USER_PROVIDED`: uploaded or explicitly supplied by the user.
* `UNKNOWN`: source identity or authority is insufficiently established.

Classification informs conflict policy and display; it does not guarantee
truth, freshness, or safety.

## 11. Cache and freshness model

Research identity and applicability are separate:

```text
query_hash = normalized query + locale + intent
retrieval_hash = query_hash + source filters + document scope + top-k policy
cache_key = mode + retrieval_hash + provider-policy version
```

Public informational results may use a normalized-query cache only when no
private document, trip, or user data is involved. Private and trip-linked
research is tenant-scoped and never enters a public cache. Cache entries store
retrieved evidence references, source hashes, provider/model versions, and
timestamps—not an assertion that the answer is currently true.

Suggested TTLs: 15 minutes for current advisories, 6 hours for general web
research, and 24 hours for stable explanatory content. Official source
freshness metadata and explicit invalidation override TTL. A stale cache may
be displayed only as stale historical evidence, never as current fact. Research
cache is completely separate from Phase 7 sessions and snapshots.

## 12. Prompt-injection threat model

Retrieved pages and documents are untrusted data. They are placed in a
delimited evidence structure with no tool-call, system-message, or instruction
semantics. The synthesizer receives immutable application instructions first,
then structured claims and citations as data.

Controls:

* never execute instructions found in content;
* no retrieved content may select tools, providers, credentials, or URLs;
* strip or escape HTML/Markdown active content for display;
* ignore hidden text and suspicious instruction patterns as claims, not rules;
* isolate PDF extraction and enforce page, byte, decompression, and timeout
  limits;
* validate every citation against the evidence record actually retrieved;
* require claim-to-citation coverage before returning an answer.

## 13. SSRF and URL safety

Research fetchers allow only `https` (and optionally `http` under an explicit
development policy). They reject localhost, loopback, link-local, multicast,
private, reserved, and unspecified IP literals and hostnames. DNS is resolved
and checked immediately before connection; redirects are limited and each
redirect is revalidated against the same policy. Maximum response size,
content-type allowlist, decompression limit, and deadline are enforced.

Booking URL validation remains in the existing Phase 5 handoff and is not
shared with or weakened by research fetching. Research URLs are never returned
as booking destinations.

## 14. Tenancy and authorization

Research sessions, queries, saved results, uploaded documents, chunks,
embeddings, citations, and trip-linked evidence carry `user_id`. Every read
filters by authenticated tenant before lookup. A trip link additionally
requires ownership and current revision applicability.

Public cache entries are eligible only for requests with no private scope and
contain no user-specific output. A user cannot infer private document
existence from cache hits, result counts, or timing. Deletion revokes access to
all derived chunks, embeddings, citations, and saved results.

## 15. LangGraph integration

Add a Research Agent specialist to the existing supervisor topology; do not
redesign the graph:

```text
START -> Supervisor
          |-> Flight/Hotel/Weather/Budget/Itinerary
          |-> Research Agent -> Web/RAG/Hybrid
       -> Evidence Layer -> Plan or Research Response
```

Research runs only when the deterministic router requires it, when a user
explicitly asks for research, or when a planning policy declares a missing
non-transactional fact. It does not run on every request. State additions are
references: `research_session_id`, `research_query_id`, `evidence_ids`,
`source_ids`, and a bounded `research_summary`. Research may inform planning
but cannot silently mutate dates, passengers, destination, budget, or selected
offers. Such changes use the existing revision/HITL workflow.

## 16. Planner integration

Planner prompts receive citation-bearing research summaries as advisory
context. The planner must preserve the canonical `TripRequest`; research
cannot write flight, hotel, approval, or booking fields. A user-confirmed
change becomes an ordinary revision with normal invalidation rules.

## 17. API contracts

Prefer one authenticated endpoint with explicit mode over three duplicated
pipelines:

`POST /api/research/query`

Request:

```json
{
  "query": "What is the baggage policy for ...?",
  "mode": "auto|web|rag|hybrid",
  "trip_id": "optional UUID",
  "document_ids": ["optional UUIDs"],
  "locale": "en-IN",
  "max_sources": 8
}
```

Response:

```json
{
  "research_session_id": "UUID",
  "query_id": "UUID",
  "intent": "POLICY_RESEARCH",
  "answer": "grounded answer",
  "citations": [],
  "evidence_status": "complete|partial|conflicted|not_found",
  "stale": false
}
```

Separate upload and saved-result endpoints may be added later. Requests are
authenticated, size- and rate-limited, and return stable 400/401/403/404/409/
429/502/504 errors without internal details. Raw provider payloads are never
returned.

## 18. State model

Persistent entities are references and immutable records:

```text
ResearchSession -> ResearchQuery -> ResearchEvidence -> Citation
                                  \-> ResearchResult
RagDocument -> RagChunk -> EmbeddingIndexRecord
```

Graph state limits: at most 32 evidence IDs, 16 source IDs, and a 4,000
character summary. Large content stays in storage and is retrieved by an
authorized service.

## 19. Database impact

Do not create all possible tables blindly. Gate 2 should add a schema-aware
migration with:

* `research_sessions` and `research_queries`;
* `research_sources` and immutable `research_evidence`;
* `research_citations`;
* `rag_documents`, `rag_chunks`, and `embedding_index_records` only when
  document RAG is enabled.

Every table has tenant scope, timestamps, content hash where applicable,
foreign keys, and indexes on `(user_id, created_at)`, query/retrieval hashes,
canonical URL, and document hash. Trip-linked rows include `trip_id` and
`revision_number` with ownership constraints. pgvector is an optional,
versioned deployment capability; migrations must detect extension availability
and fail before partial schema creation. No Phase 7 table or enum is changed.

## 20. Provider abstractions

```text
ResearchProviderPort      -> Tavily / future / test
FetcherPort               -> safe HTTP implementation
EmbeddingProviderPort     -> hosted / local / test
VectorStorePort           -> pgvector / bounded test
RerankerPort              -> provider / deterministic test
LLMProviderPort           -> Groq / future / test
```

Each port accepts a deadline and cancellation, has bounded retries, and
returns typed errors. Vendor credentials are adapter configuration only.
Provider selection is configuration/policy, never retrieved-content driven.

## 21. Error handling

* No results: successful response with `evidence_status=not_found`.
* Partial provider failure: return surviving evidence and `partial`.
* 429/timeout: retry within budget, then fallback or safe partial result.
* Invalid document/PDF: reject that document; do not poison the corpus.
* Embedding/vector/reranker outage: use remaining retrieval mode or safe
  `evidence unavailable`.
* LLM outage: return cited evidence excerpts or a stable unavailable response.
* Conflicting sources: preserve both and mark `conflicted`.
* No reliable evidence: never synthesize an uncited factual claim.

## 22. Performance, limits, and observability

Initial limits are 3 search calls, 5 fetched pages, 3 concurrent outbound
operations, 20 seconds per request, 10 MB fetched body, 25 MB uploaded
document, 500 pages, 10,000 chunks, 8 retrieved passages, 20 rerank
passages, and 12,000 synthesis-context tokens. All limits are configuration
values with safe upper bounds.

Metrics include request/provider latency, source and citation counts, cache
hits, retrieval/rerank/LLM latency, conflicts, citation coverage, token
counts, timeout/cancellation rates, and fallback rates. Logs include opaque
request IDs and error categories only; never API keys, auth headers, private
documents, raw payloads, or unnecessary user content.

## 23. Test matrix

### Unit and service tests

* every intent rule, confidence boundary, ambiguity, and live-shopping boundary;
* provider port isolation, normalization, URL deduplication, limits, retries,
  429, timeout, partial failure, cancellation, and fallback;
* citation creation and 100% substantive-claim coverage;
* chunking determinism, size limits, duplicate hashes, embedding failure,
  retrieval thresholds, filtering, reranking, and hybrid merge;
* conflict preservation and explicit authority/freshness policy;
* prompt-injection, HTML/Markdown escaping, malicious PDF, oversized input,
  and no fabricated evidence;
* SSRF schemes, DNS/IP/redirect revalidation, response-size and content-type
  rejection;
* tenant/private-document/cache isolation and freshness invalidation;
* LLM failure and provider fallback;
* Phase 7 live-search and Phase 5 booking boundary regression;
* Phase 6 immutability, provenance, revision, approval, and HITL invariants;
* LangGraph routing and bounded state-size regression.

### Real integration tests

Use isolated PostgreSQL schemas for ownership, immutable evidence, revision
links, deletion cascades, cache scope, and concurrent save/query behavior.
Use local HTTP test servers for cancellation, redirect, SSRF, response-size,
and content-type behavior. Use a deterministic fake embedding/vector provider
for retrieval tests and a test LLM that can verify claim/citation mapping.
No test count alone is sufficient; tests must assert externally observable
ownership, persistence, concurrency, and cancellation behavior.

## 24. Migration, rollout, and rollback

Gate 2 rollout is feature-flagged and additive:

1. deploy code with disabled endpoints;
2. apply schema migration with preflight/rollback checks;
3. enable deterministic router in shadow mode;
4. enable web research for a small tenant cohort;
5. enable private RAG after isolation tests;
6. enable hybrid mode after conflict/citation metrics are healthy.

Rollback disables Phase 8 routes and graph edges while preserving immutable
evidence. Down migrations are avoided for populated evidence; data retention
and deletion are handled by an explicit, audited job. Phase 7 tables and
booking paths remain untouched throughout.

## 25. Architectural decisions / ADRs

### ADR-01 — Research vs transactional flight boundary

Research never creates or impersonates offers. Current inventory always routes
to Phase 7.

### ADR-02 — Web provider abstraction

Web vendors are adapters behind `ResearchProviderPort`; the domain owns policy,
limits, and evidence normalization.

### ADR-03 — RAG storage and retrieval

PostgreSQL metadata plus a `VectorStorePort` is the contract. pgvector is the
preferred production implementation, but is optional until enabled and never
leaks technology into the domain.

### ADR-04 — Evidence and citation lifecycle

Research evidence is an immutable research taxonomy linked to the existing
evidence lifecycle. Citations are public-safe projections; flight snapshots
are not reused.

### ADR-05 — Prompt-injection isolation

Retrieved content is data inside a structured, delimited context. It cannot
issue instructions, select tools, or access secrets.

### ADR-06 — Cache and freshness

Cache identity is separate from source freshness. Public cache is allowed only
for non-private normalized queries; private/trip research is tenant-scoped.

### ADR-07 — LLM provider abstraction

Groq is an adapter behind `LLMProviderPort`, with deadlines, bounded retries,
fallback, token limits, and a deterministic test provider.

## 26. Proposed Gate 2 files/modules

These are proposals only; they were not created in Gate 1:

```text
domain/research_models.py
domain/research_evidence.py
services/research/router.py
services/research/service.py
services/research/web_provider.py
services/research/fetcher.py
services/research/evidence_service.py
services/research/rag_service.py
services/research/embedding_port.py
services/research/vector_store.py
services/research/reranker.py
services/research/llm_port.py
services/research/safety.py
research_api.py
tests/test_phase8_research.py
tests/integration/test_phase8_postgres.py
migrations/006_research.sql
```

Exact names may be adjusted to existing project conventions during Gate 2.
No implementation should proceed until the Phase 7 frozen invariants and the
test matrix above are retained.

## 27. Phase 1–7 invariant preservation checklist

* tenancy and ownership remain mandatory on all private research records;
* provider IDs and credentials remain server-side;
* flight offers/evidence remain immutable and append-only;
* provenance remains attach-once;
* parent-trip and optimistic revision locks remain unchanged;
* approval is not required for Phase 5 booking handoff;
* no URL is fabricated or repurposed by research;
* HITL revisions remain unlimited and invalidate only affected evidence;
* provider isolation remains behind adapters;
* research cannot alter canonical planning inputs without a normal revision.

## Gate 1 verdict

GATE 1 VERDICT:
APPROVED
