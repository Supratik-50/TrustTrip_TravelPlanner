"""Phase 6 Evidence Lifecycle Bridge for TrustTrip Phase 8 Research.

CRITICAL INVARIANT (Guardrail 1):
Research evidence attached to a trip or plan revision bridges directly into
Phase 6 EvidenceRecord and PlanRevisionEvidenceLink.
This guarantees identical immutability, tenancy, provenance, and revision lifecycle
semantics without creating an independent, disconnected evidence ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from domain.evidence import (
    EvidenceRecord,
    EvidenceType,
    FreshnessKind,
    PlanRevisionEvidenceLink,
    UsageState,
)
from domain.research import ResearchEvidence, SourceTrustClass
from services.evidence_store import EvidenceRevisionStore


class ResearchEvidenceBridge:
    """Bridges Phase 8 ResearchEvidence into Phase 6 EvidenceRecord and PlanRevisionEvidenceLink."""

    def __init__(self, evidence_store: EvidenceRevisionStore | None = None) -> None:
        self.evidence_store = evidence_store or EvidenceRevisionStore()

    def bridge_research_evidence(
        self,
        research_ev: ResearchEvidence,
        trip_id: UUID,
        user_id: UUID,
        revision_id: UUID | None = None,
    ) -> tuple[EvidenceRecord, PlanRevisionEvidenceLink | None]:
        """Convert ResearchEvidence to Phase 6 EvidenceRecord and persist via EvidenceRevisionStore.

        CRITICAL INVARIANT (Blocker 3):
        Trip-linked research MUST have an authoritative revision_id.
        If revision_id is missing when trip_id is present, fails closed immediately.
        """
        if trip_id is not None and revision_id is None:
            raise ValueError(f"Trip-linked evidence requires an authoritative revision_id for trip {trip_id}.")

        now = datetime.now(timezone.utc)
        freshness_kind = (
            FreshnessKind.LIVE_SUPPLIER
            if research_ev.trust_class == SourceTrustClass.OFFICIAL
            else FreshnessKind.CACHED_PORT_DATA
        )

        applicability_key = {
            "source_id": str(research_ev.source_id),
            "canonical_url": research_ev.canonical_url,
            "domain": research_ev.domain,
            "trust_class": research_ev.trust_class.value,
        }

        payload = {
            "evidence_id": str(research_ev.evidence_id),
            "source_id": str(research_ev.source_id),
            "canonical_url": research_ev.canonical_url,
            "title": research_ev.title,
            "domain": research_ev.domain,
            "trust_class": research_ev.trust_class.value,
            "claims": research_ev.claims,
            "snippet": research_ev.snippet,
            "locator": research_ev.locator,
            "relevance_score": research_ev.relevance_score,
            "retrieved_at": research_ev.retrieved_at.isoformat(),
        }

        ev_record = EvidenceRecord(
            evidence_id=research_ev.evidence_id,
            trip_id=trip_id,
            user_id=user_id,
            evidence_type=EvidenceType.ITINERARY,  # Research informs trip itinerary & advisory
            applicability_key=applicability_key,
            freshness_kind=freshness_kind,
            freshness_ttl_seconds=86400,  # 24 hour research validity
            produced_at=research_ev.retrieved_at,
            source_agent="research_agent",
            payload=payload,
            created_at=now,
        )

        # Persist using Phase 6 EvidenceRevisionStore
        self.evidence_store.create_evidence(ev_record)

        # If a specific revision_id is supplied, link it atomically
        link: PlanRevisionEvidenceLink | None = None
        if revision_id:
            link = self.create_revision_link(
                research_ev=research_ev,
                revision_id=revision_id,
                trip_id=trip_id,
                user_id=user_id,
            )

        return ev_record, link

    def bridge_evidence(
        self,
        research_ev: ResearchEvidence,
        trip_id: UUID | None = None,
        user_id: UUID | None = None,
        revision_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> EvidenceRecord:
        """Convenience method bridging ResearchEvidence to EvidenceRecord.

        Fails closed if user_id or trip_id is missing (zero random fallback).
        Trip-linked evidence requires a valid revision_id.
        """
        t_id = trip_id or getattr(research_ev, "trip_id", None)
        u_id = user_id or getattr(research_ev, "user_id", None)
        r_id = revision_id or getattr(research_ev, "revision_id", None)
        if not u_id:
            raise ValueError("bridge_evidence requires an authenticated user_id; random fallback is prohibited.")
        if not t_id:
            raise ValueError("bridge_evidence requires a valid trip_id; random fallback is prohibited.")
        if not r_id:
            raise ValueError(f"Trip-linked evidence requires an authoritative revision_id for trip {t_id}.")

        rec, _ = self.bridge_research_evidence(
            research_ev=research_ev,
            trip_id=t_id,
            user_id=u_id,
            revision_id=r_id,
        )
        return rec

    def create_revision_link(
        self,
        research_ev: ResearchEvidence,
        revision_id: UUID,
        revision_number: int | None = None,
        trip_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> PlanRevisionEvidenceLink:
        """Link research evidence to a plan revision.

        Fails closed if user_id or trip_id is missing (zero random fallback).
        """
        t_id = trip_id or getattr(research_ev, "trip_id", None)
        u_id = user_id or getattr(research_ev, "user_id", None)
        if not u_id:
            raise ValueError("create_revision_link requires an authenticated user_id; random fallback is prohibited.")
        if not t_id:
            raise ValueError("create_revision_link requires a valid trip_id; random fallback is prohibited.")

        now = datetime.now(timezone.utc)
        from uuid import uuid4
        link = PlanRevisionEvidenceLink(
            link_id=uuid4(),
            revision_id=revision_id,
            evidence_id=research_ev.evidence_id,
            trip_id=t_id,
            user_id=u_id,
            usage_state=UsageState.FRESH,
            linked_at=now,
        )
        import os
        if hasattr(self.evidence_store, "_IN_MEMORY_LINKS") and (getattr(self.evidence_store, "use_in_memory", False) or not os.getenv("DATABASE_URL")):
            self.evidence_store._IN_MEMORY_LINKS.append(link)
        elif hasattr(self.evidence_store, "_connect"):
            try:
                with self.evidence_store._connect() as conn:
                    with conn.transaction():
                        conn.execute(
                            """
                            INSERT INTO plan_revision_evidence_links (
                                link_id, revision_id, evidence_id, trip_id, user_id, usage_state, linked_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                str(link.link_id),
                                str(link.revision_id),
                                str(link.evidence_id),
                                str(link.trip_id),
                                str(link.user_id),
                                link.usage_state.value,
                                link.linked_at,
                            ),
                        )
            except Exception:
                # If table insert fails, do not swallow in production
                raise
        return link
