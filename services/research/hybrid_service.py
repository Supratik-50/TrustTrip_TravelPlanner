"""Hybrid Web + RAG Retrieval and Conflict Detection for TrustTrip Phase 8.

Merges evidence from web sources and uploaded documents.
Detects contradictory claims and resolves authority using explicit deterministic rules.
Never silently suppresses contradictory policy claims.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import uuid4
from domain.research import (
    ResearchConflict,
    ResearchEvidence,
    SourceTrustClass,
)


# Priority rank for trust classes (higher is more authoritative)
_TRUST_PRIORITY: dict[SourceTrustClass, int] = {
    SourceTrustClass.OFFICIAL: 3,
    SourceTrustClass.USER_PROVIDED: 2,
    SourceTrustClass.TRUSTED_SECONDARY: 1,
    SourceTrustClass.UNKNOWN: 0,
}


def detect_claim_conflicts(
    evidence_list: list[ResearchEvidence],
) -> list[ResearchConflict]:
    """Scan extracted claims across evidence items for numerical, temporal, or policy contradictions."""
    conflicts: list[ResearchConflict] = []
    if len(evidence_list) < 2:
        return conflicts

    # Numerical metrics and policy statements extraction
    patterns = [
        (r"(\d+)\s*(kg|kilos?|lbs?|pounds?)\b", "weight_limit"),
        (r"(\d+)\s*(months?|days?|weeks?)\s*(validity|valid)?\b", "validity_duration"),
        (r"(\$|€|£|inr)\s*(\d+)", "monetary_fee"),
        (r"\b(visa required|no visa required|visa-free|entry permit required)\b", "visa_requirement"),
        (r"\b(passport valid for \d+ months|passport validity \d+ months)\b", "passport_validity"),
    ]

    extracted_metrics: list[dict[str, Any]] = []
    for ev in evidence_list:
        combined_text = f"{ev.title} {ev.snippet} {' '.join(ev.claims)}"
        for pat, topic in patterns:
            for match in re.finditer(pat, combined_text, re.IGNORECASE):
                val_str = match.group(0).lower().strip()
                extracted_metrics.append({
                    "evidence": ev,
                    "topic": topic,
                    "value": val_str,
                    "raw_match": match.group(0),
                })

    # Group by topic and find divergent values
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in extracted_metrics:
        by_topic.setdefault(item["topic"], []).append(item)

    for topic, items in by_topic.items():
        if len(items) < 2:
            continue
        first = items[0]
        for other in items[1:]:
            if first["value"] != other["value"] and first["evidence"].source_id != other["evidence"].source_id:
                ev_a = first["evidence"]
                ev_b = other["evidence"]

                rank_a = _TRUST_PRIORITY.get(ev_a.trust_class, 0)
                rank_b = _TRUST_PRIORITY.get(ev_b.trust_class, 0)

                resolved = False
                res_summary = ""

                if rank_a > rank_b:
                    resolved = True
                    res_summary = (
                        f"Resolved in favor of Authoritative source {ev_a.title} ({ev_a.trust_class.value}) "
                        f"over {ev_b.title} ({ev_b.trust_class.value})."
                    )
                elif rank_b > rank_a:
                    resolved = True
                    res_summary = (
                        f"Resolved in favor of Authoritative source {ev_b.title} ({ev_b.trust_class.value}) "
                        f"over {ev_a.title} ({ev_a.trust_class.value})."
                    )
                else:
                    # Compare publication/retrieval dates if available
                    date_a = ev_a.published_at or ev_a.retrieved_at
                    date_b = ev_b.published_at or ev_b.retrieved_at
                    if date_a > date_b:
                        resolved = True
                        res_summary = f"Resolved in favor of more recent source {ev_a.title} ({date_a.date()})."
                    elif date_b > date_a:
                        resolved = True
                        res_summary = f"Resolved in favor of more recent source {ev_b.title} ({date_b.date()})."
                    else:
                        resolved = False
                        res_summary = (
                            f"Unresolved contradiction between {ev_a.title} ('{first['value']}') "
                            f"and {ev_b.title} ('{other['value']}'). Both sources surfaced."
                        )

                conflict = ResearchConflict(
                    conflict_id=uuid4(),
                    topic=topic,
                    claim_a=f"{ev_a.title}: {first['value']}",
                    source_a_title=ev_a.title,
                    source_a_trust=ev_a.trust_class,
                    claim_b=f"{ev_b.title}: {other['value']}",
                    source_b_title=ev_b.title,
                    source_b_trust=ev_b.trust_class,
                    resolution_summary=res_summary,
                    resolved=resolved,
                )
                conflicts.append(conflict)
                break

    return conflicts


def merge_and_deduplicate_evidence(
    web_evidence: list[ResearchEvidence],
    rag_evidence: list[ResearchEvidence],
) -> tuple[list[ResearchEvidence], list[ResearchConflict]]:
    """Combine Web and RAG evidence, deduplicating by URL/hash and flagging contradictions."""
    seen_hashes: set[str] = set()
    merged: list[ResearchEvidence] = []

    # Priority to official web evidence and authenticated RAG documents
    for ev in web_evidence + rag_evidence:
        key = ev.canonical_url or ev.immutable_hash or str(ev.evidence_id)
        if key in seen_hashes:
            continue
        seen_hashes.add(key)
        merged.append(ev)

    # Detect conflicts across merged set
    conflicts = detect_claim_conflicts(merged)
    return merged, conflicts


class HybridResearchService:
    """Service coordinating concurrent Web + RAG retrieval, evidence merging, and conflict detection."""

    def __init__(
        self,
        web_provider: Any = None,
        vector_store: Any = None,
        embedding_provider: Any = None,
        reranker: Any = None,
    ) -> None:
        self.web_provider = web_provider
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.reranker = reranker

    def detect_conflicts(self, evidence_list: list[ResearchEvidence]) -> list[ResearchConflict]:
        return detect_claim_conflicts(evidence_list)

    async def retrieve(
        self,
        query: Any,
    ) -> tuple[list[ResearchEvidence], list[ResearchConflict]]:
        web_evidence: list[ResearchEvidence] = []
        rag_evidence: list[ResearchEvidence] = []

        if self.web_provider:
            sources = await self.web_provider.search(query.query_text, max_results=query.max_sources)
            for s in sources:
                web_evidence.append(
                    ResearchEvidence(
                        query_id=query.query_id,
                        user_id=query.user_id,
                        source_id=s.source_id,
                        canonical_url=s.canonical_url,
                        title=s.title,
                        domain=s.domain,
                        trust_class=s.trust_class,
                        snippet=s.metadata.get("snippet", ""),
                        published_at=s.published_at,
                    )
                )

        if self.vector_store and self.embedding_provider:
            q_vecs = self.embedding_provider.embed_texts([query.query_text])
            if q_vecs:
                scored_chunks = self.vector_store.query(
                    query_embedding=q_vecs[0],
                    user_id=query.user_id,
                    top_k=query.max_sources,
                )
                for sc in scored_chunks:
                    c = sc.chunk
                    rag_evidence.append(
                        ResearchEvidence(
                            query_id=query.query_id,
                            user_id=query.user_id,
                            source_id=c.chunk_id,
                            canonical_url=f"local://doc/{c.document_id}",
                            title=f"Document Chunk #{c.chunk_index}",
                            domain="local_rag",
                            trust_class=SourceTrustClass.USER_PROVIDED,
                            snippet=c.text_content,
                        )
                    )

        return merge_and_deduplicate_evidence(web_evidence, rag_evidence)
