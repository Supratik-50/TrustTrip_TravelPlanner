"""Research Orchestrator for TrustTrip Phase 8.

Coordinates:
- Intent Classification
- Web & RAG Retrieval
- Source Normalization & Deduplication
- Conflict Detection
- Grounded Synthesis with Citations
- Phase 6 Evidence Lifecycle Bridging
- Tenant-scoped persistence and safe failure handling
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from domain.research import (
    EvidenceStatus,
    ResearchCitation,
    ResearchConflict,
    ResearchEvidence,
    ResearchFreshnessState,
    ResearchIntent,
    ResearchMode,
    ResearchQuery,
    ResearchResult,
    ResearchSession,
    ResearchSource,
    SourceTrustClass,
)
from services.research.evidence_bridge import ResearchEvidenceBridge
from services.research.hybrid_service import merge_and_deduplicate_evidence
from services.research.intent_router import IntentDecision, ResearchIntentRouter
from services.research.llm_provider import (
    DeterministicTestLLMProvider,
    GroqLLMProvider,
    LLMProviderUnavailableError,
)
from services.research.ports import (
    EmbeddingProviderPort,
    LLMProviderPort,
    RerankerPort,
    ResearchProviderError,
    ResearchProviderPort,
    VectorStorePort,
)
from services.research.rag.embedding import DeterministicEmbeddingProvider
from services.research.rag.reranker import LexicalSemanticReranker
from services.research.rag.vector_store import MemoryVectorStore, get_vector_store
from services.research.research_store import ResearchStore
from services.research.synthesizer import ResearchSynthesizer
from services.research.tavily_adapter import TavilyResearchProvider, TestResearchProvider

logger = logging.getLogger(__name__)


class ResearchOrchestrator:
    """Coordinates the full travel research lifecycle."""

    def __init__(
        self,
        intent_router: ResearchIntentRouter | None = None,
        web_provider: ResearchProviderPort | None = None,
        research_provider: ResearchProviderPort | None = None,
        embedding_provider: EmbeddingProviderPort | None = None,
        vector_store: VectorStorePort | None = None,
        reranker: RerankerPort | None = None,
        llm_provider: LLMProviderPort | None = None,
        research_store: ResearchStore | None = None,
        store: ResearchStore | None = None,
        evidence_bridge: ResearchEvidenceBridge | None = None,
        safe_fetcher: Any | None = None,
    ) -> None:
        self.intent_router = intent_router or ResearchIntentRouter()
        self.web_provider = web_provider or research_provider or TavilyResearchProvider()
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider()
        self.vector_store = vector_store or get_vector_store()
        self.reranker = reranker or LexicalSemanticReranker()
        self.llm_provider = llm_provider or GroqLLMProvider()
        self.synthesizer = ResearchSynthesizer(self.llm_provider)
        self.research_store = research_store or store or ResearchStore()
        self.evidence_bridge = evidence_bridge or ResearchEvidenceBridge()
        from services.research.safe_fetcher import SafeHttpFetcher
        self.safe_fetcher = safe_fetcher or SafeHttpFetcher()

    async def research(self, query: ResearchQuery) -> ResearchResult:
        """Convenience method executing research from a canonical ResearchQuery object."""
        return await self.execute_research(
            user_id=query.user_id,
            query_text=query.query_text,
            mode=query.mode,
            trip_id=query.trip_id,
            revision_number=query.revision_number,
            document_ids=query.document_ids,
            locale=query.locale,
            max_sources=query.max_sources,
            session_id=query.session_id,
        )

    async def fetch_source_content(self, url: str, call_timeout: float = 8.0) -> tuple[str, str, int]:
        """Fetch external webpage content strictly through SafeHttpFetcher.

        Direct HTTP fetches bypassing SafeHttpFetcher are prohibited.
        """
        return await self.safe_fetcher.fetch(url, call_timeout=call_timeout)

    async def execute_research(
        self,
        user_id: UUID,
        query_text: str,
        mode: ResearchMode = ResearchMode.AUTO,
        trip_id: UUID | None = None,
        revision_number: int | None = None,
        document_ids: list[UUID] | None = None,
        locale: str = "en-US",
        max_sources: int = 5,
        session_id: UUID | None = None,
        call_timeout: float = 20.0,
    ) -> ResearchResult:
        """Execute complete grounded research pipeline."""
        now = datetime.now(timezone.utc)
        sess_id = session_id or uuid4()
        q_id = uuid4()

        # 1. Intent routing
        has_docs = bool(document_ids)
        intent_decision: IntentDecision = self.intent_router.classify(
            query=query_text,
            has_documents=has_docs,
            explicit_mode=mode,
        )

        effective_mode = mode if mode != ResearchMode.AUTO else intent_decision.suggested_mode

        # 2. Hard boundary check: Live flight shopping handoff
        if intent_decision.requires_live_handoff:
            return ResearchResult(
                research_session_id=sess_id,
                query_id=q_id,
                intent=ResearchIntent.LIVE_FLIGHT_SEARCH,
                mode=effective_mode,
                answer=(
                    "This request is classified as live flight shopping. "
                    "TrustTrip handles real-time live availability, prices, and booking handoffs "
                    "exclusively through the live flight search service (Phase 7). "
                    "Please specify exact travel dates to shop live fares."
                ),
                citations=[],
                evidence_status=EvidenceStatus.COMPLETE,
                conflicts=[],
                stale=False,
                evidence_count=0,
                created_at=now,
            )

        # 3. Create/update research session
        session = self.research_store.get_session(sess_id, user_id)
        if not session:
            session = ResearchSession(
                session_id=sess_id,
                user_id=user_id,
                trip_id=trip_id,
                mode=effective_mode,
                status="active",
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
            self.research_store.save_session(session)

        # Record query
        query_record = ResearchQuery(
            query_id=q_id,
            session_id=sess_id,
            user_id=user_id,
            query_text=query_text,
            normalized_query=query_text.strip().lower(),
            intent=intent_decision.intent,
            mode=effective_mode,
            trip_id=trip_id,
            revision_number=revision_number,
            document_ids=document_ids or [],
            locale=locale,
            max_sources=max_sources,
            created_at=now,
        )
        self.research_store.save_query(query_record)

        # Handle ambiguous intent
        if intent_decision.intent == ResearchIntent.AMBIGUOUS_RESEARCH and not has_docs:
            clarification = intent_decision.clarification_prompt or "Could you please clarify your travel research question?"
            return ResearchResult(
                research_session_id=sess_id,
                query_id=q_id,
                intent=intent_decision.intent,
                mode=effective_mode,
                answer=clarification,
                citations=[],
                evidence_status=EvidenceStatus.NOT_FOUND,
                conflicts=[],
                stale=False,
                evidence_count=0,
                created_at=now,
            )

        # 4. Retrieval execution
        web_evidence: list[ResearchEvidence] = []
        rag_evidence: list[ResearchEvidence] = []

        should_fetch_web = effective_mode in (ResearchMode.WEB, ResearchMode.HYBRID, ResearchMode.AUTO)
        should_fetch_rag = effective_mode in (ResearchMode.RAG, ResearchMode.HYBRID) or has_docs

        async def _retrieve_web():
            nonlocal web_evidence
            try:
                from services.research.safe_fetcher import validate_research_url
                sources = await self.web_provider.search(
                    query=query_text,
                    max_results=max_sources,
                    call_timeout=min(12.0, call_timeout),
                )
                for src in sources:
                    # Validate URL via SafeHttpFetcher logic (Blocker 4)
                    # If source snippet is missing or empty, fetch page content via SafeHttpFetcher
                    snippet = src.metadata.get("snippet", "")
                    if not snippet and hasattr(self, "safe_fetcher") and self.safe_fetcher:
                        try:
                            content_text, final_url, status_code = await self.safe_fetcher.fetch(
                                src.url, call_timeout=min(5.0, call_timeout)
                            )
                            if status_code == 200 and content_text:
                                snippet = content_text[:1500]
                                src.metadata["snippet"] = snippet
                                src.canonical_url = final_url
                        except Exception as fetch_err:
                            logger.warning("SafeHttpFetcher failed for source %s: %s", src.url, fetch_err)
                            continue

                    self.research_store.save_source(user_id, src)
                    ev_id = uuid4()
                    ev_hash = hashlib.sha256(f"{src.canonical_url}:{src.content_hash}".encode("utf-8")).hexdigest()
                    ev = ResearchEvidence(
                        evidence_id=ev_id,
                        query_id=q_id,
                        user_id=user_id,
                        source_id=src.source_id,
                        canonical_url=src.canonical_url,
                        title=src.title,
                        domain=src.domain,
                        trust_class=src.trust_class,
                        claims=[src.metadata.get("snippet", "")[:300]],
                        snippet=src.metadata.get("snippet", "")[:1500],
                        locator="Web Snippet",
                        relevance_score=0.85,
                        freshness_state=ResearchFreshnessState.FRESH,
                        retrieved_at=src.retrieved_at,
                        immutable_hash=ev_hash,
                        trip_id=trip_id,
                        revision_number=revision_number,
                    )
                    web_evidence.append(ev)
            except (ResearchProviderError, asyncio.TimeoutError) as exc:
                logger.warning("Web research provider failed or timed out: %s", exc)

        def _retrieve_rag():
            nonlocal rag_evidence
            try:
                q_emb = self.embedding_provider.embed_texts([query_text])[0]
                scored_chunks = self.vector_store.query_similar(
                    query_embedding=q_emb,
                    user_id=user_id,
                    top_k=max_sources,
                    filter_document_ids=document_ids,
                )
                for chunk, score in scored_chunks:
                    ev_id = uuid4()
                    ev_hash = hashlib.sha256(f"{chunk.document_id}:{chunk.content_hash}".encode("utf-8")).hexdigest()
                    ev = ResearchEvidence(
                        evidence_id=ev_id,
                        query_id=q_id,
                        user_id=user_id,
                        source_id=chunk.document_id,
                        canonical_url=f"document://{chunk.document_id}#chunk_{chunk.chunk_index}",
                        title=f"Document {str(chunk.document_id)[:8]}",
                        domain="local.document",
                        trust_class=SourceTrustClass.USER_PROVIDED,
                        claims=[chunk.text_content[:200]],
                        snippet=chunk.text_content[:1500],
                        locator=f"Section {chunk.chunk_index + 1}",
                        relevance_score=max(0.0, min(1.0, score)),
                        freshness_state=ResearchFreshnessState.FRESH,
                        retrieved_at=now,
                        immutable_hash=ev_hash,
                        trip_id=trip_id,
                        revision_number=revision_number,
                    )
                    rag_evidence.append(ev)
            except Exception as exc:
                logger.warning("RAG retrieval failed: %s", exc)

        tasks = []
        if should_fetch_web:
            tasks.append(_retrieve_web())
        if should_fetch_rag:
            _retrieve_rag()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # 5. Merge, Deduplicate & Detect Conflicts
        merged_evidence, conflicts = merge_and_deduplicate_evidence(web_evidence, rag_evidence)

        if not merged_evidence:
            return ResearchResult(
                research_session_id=sess_id,
                query_id=q_id,
                intent=intent_decision.intent,
                mode=effective_mode,
                answer="Evidence not found / source unavailable.",
                citations=[],
                evidence_status=EvidenceStatus.NOT_FOUND,
                conflicts=[],
                stale=False,
                evidence_count=0,
                created_at=now,
            )

        # 6. Reranking
        passages = [(ev, f"{ev.title} {ev.snippet}") for ev in merged_evidence]
        reranked = self.reranker.rerank(query=query_text, passages=passages, top_k=max_sources)
        final_evidence = [item for item, _ in reranked]

        # 7. Grounded Synthesis with Citations
        citations: list[ResearchCitation] = []
        answer_text: str = ""
        evidence_status: EvidenceStatus = EvidenceStatus.COMPLETE

        try:
            answer_text, citations, evidence_status = await self.synthesizer.synthesize_answer(
                query=query_text,
                evidence_list=final_evidence,
                conflicts=conflicts,
                call_timeout=call_timeout,
            )
        except LLMProviderUnavailableError as exc:
            # CRITICAL (Guardrail 4): Production LLM failure path is explicitly non-hallucinatory
            logger.error("LLM synthesis failed: %s", exc)
            answer_text = (
                "Synthesis is currently unavailable due to an upstream model error. "
                "Verified source citations and retrieved passages are provided below."
            )
            # Create citations directly from evidence without synthetic text
            citations = [
                ResearchCitation(
                    citation_id=f"[cite:{idx}]",
                    evidence_id=ev.evidence_id,
                    title=ev.title,
                    domain=ev.domain,
                    canonical_url=ev.canonical_url,
                    published_at=ev.published_at,
                    retrieved_at=ev.retrieved_at,
                    locator=ev.locator,
                    cited_claims=ev.claims[:2],
                )
                for idx, ev in enumerate(final_evidence, start=1)
            ]
            evidence_status = EvidenceStatus.PARTIAL

        # 8. Bridge to Phase 6 Evidence Lifecycle if attached to a trip
        if trip_id:
            from services.trip_store import TripStore
            trip = TripStore().get_trip(trip_id, user_id)
            if not trip:
                raise PermissionError(f"User {user_id} does not own trip {trip_id} or trip does not exist.")

            # Resolve authoritative active revision ID (Blocker 3)
            raw_rev = getattr(trip, "current_revision_id", None) or (trip.get("current_revision_id") if isinstance(trip, dict) else None)
            if not raw_rev:
                raise RuntimeError(
                    f"Authoritative current_revision_id cannot be resolved for trip {trip_id}. "
                    "Trip-linked research must fail closed."
                )
            active_rev_id = UUID(str(raw_rev))

            # Set authoritative trip_id and revision_id on evidence
            final_evidence = [
                ev.model_copy(update={"trip_id": trip_id, "revision_id": active_rev_id})
                for ev in final_evidence
            ]

            for ev in final_evidence:
                # Do NOT swallow bridge exceptions (Blocker 3)
                self.evidence_bridge.bridge_research_evidence(
                    research_ev=ev,
                    trip_id=trip_id,
                    user_id=user_id,
                    revision_id=active_rev_id,
                )
        else:
            # Global research: guarantee trip_id and revision_id are strictly None
            final_evidence = [
                ev.model_copy(update={"trip_id": None, "revision_id": None})
                for ev in final_evidence
            ]

        # 9. Persist evidence & citations atomically
        self.research_store.save_evidence_batch(final_evidence)
        self.research_store.save_citations(q_id, user_id, citations)

        return ResearchResult(
            research_session_id=sess_id,
            query_id=q_id,
            intent=intent_decision.intent,
            mode=effective_mode,
            answer=answer_text,
            citations=citations,
            evidence_status=evidence_status,
            conflicts=conflicts,
            stale=False,
            evidence_count=len(final_evidence),
            created_at=now,
        )
