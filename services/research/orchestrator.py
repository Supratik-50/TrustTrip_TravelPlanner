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
    SourceType,
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
from services.research.rag.embedding import DeterministicEmbeddingProvider, get_embedding_provider
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
        self.embedding_provider = embedding_provider or get_embedding_provider()
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
        if not has_docs and self.research_store:
            try:
                user_docs = self.research_store.list_documents(user_id)
                has_docs = bool(user_docs)
            except Exception:
                has_docs = False

        intent_decision: IntentDecision = self.intent_router.classify(
            query=query_text,
            has_documents=has_docs,
            explicit_mode=mode,
            document_ids=document_ids,
        )

        effective_mode = mode if mode != ResearchMode.AUTO else intent_decision.suggested_mode

        # 2. Hard boundary check: Live flight shopping handoff
        if intent_decision.requires_live_handoff:
            return ResearchResult(
                research_session_id=sess_id,
                query_id=q_id,
                query_text=query_text,
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
                query_text=query_text,
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
        web_error: Exception | None = None
        rag_error: Exception | None = None

        if effective_mode == ResearchMode.RAG:
            should_fetch_web = False
            should_fetch_rag = True
        elif effective_mode == ResearchMode.WEB:
            should_fetch_web = True
            should_fetch_rag = False
        elif effective_mode == ResearchMode.HYBRID:
            should_fetch_web = True
            should_fetch_rag = True
        else:
            should_fetch_web = True
            should_fetch_rag = has_docs

        async def _retrieve_web():
            nonlocal web_evidence, web_error
            try:
                from services.research.safe_fetcher import validate_research_url
                sources = await self.web_provider.search(
                    query=query_text,
                    max_results=max_sources,
                    call_timeout=min(12.0, call_timeout),
                )
                for src in sources:
                    # Validate URL via SafeHttpFetcher logic
                    if not src.url:
                        continue
                    try:
                        normalized_url = validate_research_url(
                            src.url,
                            allow_http=getattr(self.safe_fetcher, "allow_http", False),
                        )
                        src = src.model_copy(update={"canonical_url": normalized_url})
                    except Exception as url_err:
                        logger.warning("Rejecting invalid or unsafe research source URL '%s': %s", src.url, url_err)
                        continue

                    # Validate and extract substantive content
                    from services.research.source_normalizer import clean_web_snippet, is_substantive_content
                    snippet = src.metadata.get("snippet", "") if src.metadata else ""
                    if (not snippet or len(snippet.strip()) < 120 or not is_substantive_content(snippet, min_chars=80)) and hasattr(self, "safe_fetcher") and self.safe_fetcher:
                        try:
                            content_text, final_url, status_code = await self.safe_fetcher.fetch(
                                src.url, call_timeout=min(5.0, call_timeout)
                            )
                            if status_code == 200 and content_text:
                                cleaned = clean_web_snippet(content_text)
                                if is_substantive_content(cleaned, min_chars=80):
                                    snippet = cleaned[:2500]
                                    src.metadata["snippet"] = snippet
                                    src = src.model_copy(update={"canonical_url": final_url})
                        except Exception as fetch_err:
                            logger.warning("SafeHttpFetcher failed for source %s: %s", src.url, fetch_err)

                    if not is_substantive_content(snippet, min_chars=80):
                        logger.info("Dropping non-substantive or boilerplate research source: %s", src.url)
                        continue

                    try:
                        saved = self.research_store.save_source(user_id, src)
                        if isinstance(saved, ResearchSource):
                            src = saved
                    except Exception as s_err:
                        logger.warning("Failed to save source: %s", s_err)
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
                        claims=[snippet[:300]] if snippet else [],
                        snippet=snippet[:2500] if snippet else None,
                        locator="Web Snippet" if snippet else None,
                        relevance_score=None,  # Populated only when actual reranker scores it
                        freshness_state=ResearchFreshnessState.FRESH,
                        retrieved_at=src.retrieved_at,
                        published_at=src.published_at,
                        immutable_hash=ev_hash,
                        trip_id=trip_id,
                        revision_number=revision_number,
                        citation_token=str(ev_id),
                    )
                    web_evidence.append(ev)
            except (ResearchProviderError, asyncio.TimeoutError) as exc:
                logger.warning("Web research provider failed or timed out: %s", exc)
                web_error = exc
            except Exception as exc:
                logger.warning("Unexpected error during web research retrieval: %s", exc)
                web_error = exc

        def _retrieve_rag():
            nonlocal rag_evidence, rag_error
            try:
                if hasattr(self.embedding_provider, "embed_query"):
                    q_emb = self.embedding_provider.embed_query(query_text)
                else:
                    q_emb = self.embedding_provider.embed_texts([query_text])[0]
                filter_ids = document_ids if (document_ids and len(document_ids) > 0) else None
                scored_chunks = self.vector_store.query_similar(
                    query_embedding=q_emb,
                    user_id=user_id,
                    top_k=max_sources,
                    filter_document_ids=filter_ids,
                )
                doc_cache: dict[UUID, Any] = {}
                for chunk, score in scored_chunks:
                    if chunk.document_id not in doc_cache:
                        try:
                            d = self.research_store.get_document(chunk.document_id, user_id)
                            if d:
                                doc_cache[chunk.document_id] = d
                        except Exception:
                            pass
                    doc_meta = doc_cache.get(chunk.document_id)
                    doc_title = doc_meta.title if doc_meta and doc_meta.title else f"Document {str(chunk.document_id)[:8]}"
                    file_info = f" ({doc_meta.filename})" if doc_meta and doc_meta.filename else ""
                    doc_src = ResearchSource(
                        source_id=chunk.document_id,
                        url=f"document://{chunk.document_id}",
                        canonical_url=f"document://{chunk.document_id}",
                        title=doc_title,
                        domain="local.document",
                        source_type=SourceType.UPLOADED_DOCUMENT,
                        trust_class=SourceTrustClass.USER_PROVIDED,
                        content_hash=doc_meta.content_hash if doc_meta else chunk.content_hash,
                    )
                    self.research_store.save_source(user_id, doc_src)
                    ev_id = uuid4()
                    ev_hash = hashlib.sha256(f"{chunk.document_id}:{chunk.content_hash}".encode("utf-8")).hexdigest()
                    ev = ResearchEvidence(
                        evidence_id=ev_id,
                        query_id=q_id,
                        user_id=user_id,
                        source_id=chunk.document_id,
                        canonical_url=f"document://{chunk.document_id}#chunk_{chunk.chunk_index}",
                        title=doc_title,
                        domain="local.document",
                        trust_class=SourceTrustClass.USER_PROVIDED,
                        claims=[chunk.text_content[:200]],
                        snippet=chunk.text_content[:1500],
                        locator=f"Section {chunk.chunk_index + 1}{file_info}",
                        relevance_score=max(0.0, min(1.0, score)),
                        freshness_state=ResearchFreshnessState.FRESH,
                        retrieved_at=now,
                        immutable_hash=ev_hash,
                        trip_id=trip_id,
                        revision_number=revision_number,
                        citation_token=str(ev_id),
                    )
                    rag_evidence.append(ev)
            except Exception as exc:
                logger.warning("RAG retrieval failed: %s", exc)
                rag_error = exc

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
            has_error = (web_error is not None and should_fetch_web) or (rag_error is not None and should_fetch_rag)
            if has_error:
                if effective_mode == ResearchMode.WEB and web_error is not None:
                    answer_msg = f"Web research service unavailable: upstream provider error ({web_error})."
                elif effective_mode == ResearchMode.RAG and rag_error is not None:
                    answer_msg = f"Document research service unavailable: retrieval error ({rag_error})."
                else:
                    answer_msg = "Research service unavailable: upstream provider timeout or failure."
            else:
                if effective_mode == ResearchMode.RAG:
                    answer_msg = "Evidence not found: No relevant evidence was found in your uploaded documents for this research question."
                elif effective_mode == ResearchMode.WEB:
                    answer_msg = "Evidence not found: No relevant travel evidence was found from external web sources for this research question."
                else:
                    answer_msg = "Evidence not found: No relevant evidence was found from uploaded documents or external web sources for this research question."

            return ResearchResult(
                research_session_id=sess_id,
                query_id=q_id,
                query_text=query_text,
                intent=intent_decision.intent,
                mode=effective_mode,
                answer=answer_msg,
                citations=[],
                facts=[],
                evidence_status=EvidenceStatus.PARTIAL if has_error else EvidenceStatus.NOT_FOUND,
                conflicts=[],
                stale=False,
                evidence_count=0,
                created_at=now,
            )

        # 6. Reranking using actual reranker scores
        if effective_mode == ResearchMode.HYBRID and rag_evidence and web_evidence:
            rag_passages = [(ev, f"{ev.title or ''} {ev.snippet or ''}".strip()) for ev in rag_evidence]
            web_passages = [(ev, f"{ev.title or ''} {ev.snippet or ''}".strip()) for ev in web_evidence]
            rag_k = max(2, min(len(rag_evidence), max_sources // 2))
            web_k = max(2, min(len(web_evidence), max_sources - rag_k))
            top_rag = self.reranker.rerank(query=query_text, passages=rag_passages, top_k=rag_k)
            top_web = self.reranker.rerank(query=query_text, passages=web_passages, top_k=web_k)
            reranked = top_rag + top_web
        else:
            passages = [(ev, f"{ev.title or ''} {ev.snippet or ''}".strip()) for ev in merged_evidence]
            reranked = self.reranker.rerank(query=query_text, passages=passages, top_k=max_sources)

        final_evidence = []
        for item, score in reranked:
            norm_score = max(0.0, min(1.0, float(score)))
            final_evidence.append(item.model_copy(update={"relevance_score": norm_score}))

        # 7. Grounded Synthesis with Citations & Facts
        citations: list[ResearchCitation] = []
        facts: list[ResearchFact] = []
        answer_text: str = ""
        evidence_status: EvidenceStatus = EvidenceStatus.COMPLETE

        has_dual_sources = bool(rag_evidence) and bool(web_evidence)
        partial_provider = (
            (web_error is not None and should_fetch_web)
            or (rag_error is not None and should_fetch_rag)
            or (effective_mode == ResearchMode.HYBRID and not has_dual_sources)
        )

        try:
            answer_text, citations, facts, evidence_status = await self.synthesizer.synthesize_answer_with_facts(
                query=query_text,
                evidence_list=final_evidence,
                conflicts=conflicts,
                call_timeout=call_timeout,
            )
            if partial_provider and evidence_status == EvidenceStatus.COMPLETE:
                evidence_status = EvidenceStatus.PARTIAL

            if effective_mode == ResearchMode.HYBRID and not has_dual_sources:
                missing_src = "web sources" if not web_evidence else "private uploaded documents"
                available_src = "private uploaded documents" if not web_evidence else "web sources"
                if missing_src not in answer_text.lower():
                    answer_text = (
                        f"{answer_text.strip()}\n\n"
                        f"*Note on Combined Research: Only {available_src} yielded relevant evidence. "
                        f"External {missing_src} were unavailable or yielded no matching evidence for this query.*"
                    )
        except LLMProviderUnavailableError as exc:
            logger.error("LLM synthesis failed: %s", exc)
            answer_text = (
                "Synthesis is currently unavailable due to an upstream model error. "
                "Verified source citations and retrieved passages are provided below."
            )
            citations = [
                ResearchCitation(
                    citation_id=f"[cite:{ev.stable_token}]",
                    evidence_id=ev.evidence_id,
                    title=ev.title,
                    domain=ev.domain,
                    canonical_url=ev.canonical_url,
                    published_at=ev.published_at,
                    retrieved_at=ev.retrieved_at,
                    locator=ev.locator,
                    cited_claims=ev.claims[:2],
                    source_id=ev.source_id,
                    source_type="document" if (
                        ev.domain == "local.document"
                        or (bool(ev.canonical_url) and ev.canonical_url.startswith("document://"))
                        or ev.trust_class == SourceTrustClass.USER_PROVIDED
                    ) else "web",
                    snippet=ev.snippet,
                    url=ev.canonical_url,
                    publisher=ev.domain,
                )
                for ev in final_evidence
            ]
            facts = []
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
            query_text=query_text,
            intent=intent_decision.intent,
            mode=effective_mode,
            answer=answer_text,
            citations=citations,
            facts=facts,
            evidence_status=evidence_status,
            conflicts=conflicts,
            stale=False,
            evidence_count=len(final_evidence),
            created_at=now,
        )
