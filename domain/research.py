"""Research Domain Models for TrustTrip Phase 8.

Pydantic v2 immutable domain models representing:
- Research intents, modes, source types, and trust classifications.
- Canonical queries, sources, evidence, citations, and results.
- RAG document and chunk entities.
All models enforce extra="forbid" and strict typing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class TripIntent(str, Enum):
    """High-level user trip intent for supervisor / planner (Phase 3 compatibility)."""
    NEW_TRIP = "new_trip"
    FOLLOW_UP = "follow_up"
    GLOBAL_FLIGHT_RESEARCH = "global_flight_research"
    BOOKING_OPERATION = "booking_operation"


class ResearchIntent(str, Enum):
    """Deterministic research intents."""
    GLOBAL_FLIGHT_RESEARCH = "GLOBAL_FLIGHT_RESEARCH"
    DESTINATION_RESEARCH = "DESTINATION_RESEARCH"
    AIRPORT_RESEARCH = "AIRPORT_RESEARCH"
    AIRLINE_RESEARCH = "AIRLINE_RESEARCH"
    ROUTE_RESEARCH = "ROUTE_RESEARCH"
    POLICY_RESEARCH = "POLICY_RESEARCH"
    TRAVEL_ADVISORY_RESEARCH = "TRAVEL_ADVISORY_RESEARCH"
    DOCUMENT_RAG_RESEARCH = "DOCUMENT_RAG_RESEARCH"
    COMBINED_WEB_RAG_RESEARCH = "COMBINED_WEB_RAG_RESEARCH"
    LIVE_FLIGHT_SEARCH = "LIVE_FLIGHT_SEARCH"
    AMBIGUOUS_RESEARCH = "AMBIGUOUS_RESEARCH"


class ResearchMode(str, Enum):
    """Execution mode for research pipeline."""
    AUTO = "auto"
    WEB = "web"
    RAG = "rag"
    HYBRID = "hybrid"


class SourceType(str, Enum):
    """Type of research source."""
    WEB_PAGE = "WEB_PAGE"
    OFFICIAL_PORTAL = "OFFICIAL_PORTAL"
    AIRLINE_POLICY = "AIRLINE_POLICY"
    GOVERNMENT_ADVISORY = "GOVERNMENT_ADVISORY"
    UPLOADED_DOCUMENT = "UPLOADED_DOCUMENT"
    LOCAL_RAG = "LOCAL_RAG"


class SourceTrustClass(str, Enum):
    """Descriptive trust classification of the source."""
    OFFICIAL = "OFFICIAL"
    TRUSTED_SECONDARY = "TRUSTED_SECONDARY"
    USER_PROVIDED = "USER_PROVIDED"
    UNKNOWN = "UNKNOWN"


class ResearchFreshnessState(str, Enum):
    """Freshness evaluation of research content."""
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class EvidenceStatus(str, Enum):
    """Completeness status of research evidence."""
    COMPLETE = "complete"
    PARTIAL = "partial"
    CONFLICTED = "conflicted"
    NOT_FOUND = "not_found"


class ResearchQuery(BaseModel):
    """Structured representation of a research request."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    query_text: str = Field(min_length=1, max_length=1000)
    normalized_query: str = Field(default="")
    intent: ResearchIntent = ResearchIntent.AMBIGUOUS_RESEARCH
    mode: ResearchMode = ResearchMode.AUTO
    trip_id: UUID | None = None
    revision_number: int | None = None
    document_ids: list[UUID] = Field(default_factory=list)
    locale: str = Field(default="en-US", max_length=20)
    max_sources: int = Field(default=5, ge=1, le=20)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchSource(BaseModel):
    """Normalized research source metadata."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: UUID = Field(default_factory=uuid4)
    url: str = Field(default="", max_length=2000)
    canonical_url: str = Field(default="", max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    domain: str = Field(default="", max_length=255)
    source_type: SourceType = SourceType.WEB_PAGE
    trust_class: SourceTrustClass = SourceTrustClass.UNKNOWN
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = Field(default="", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchEvidence(BaseModel):
    """Immutable piece of research evidence extracted from a source."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID = Field(default_factory=uuid4)
    query_id: UUID
    user_id: UUID | None = None
    source_id: UUID
    canonical_url: str = Field(default="", max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    domain: str = Field(default="", max_length=255)
    trust_class: SourceTrustClass = SourceTrustClass.UNKNOWN
    claims: list[str] = Field(default_factory=list)
    snippet: str | None = Field(default=None, max_length=4000)
    locator: str | None = Field(default=None, max_length=500)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    freshness_state: ResearchFreshnessState = ResearchFreshnessState.UNKNOWN
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    immutable_hash: str = Field(default="", max_length=64)
    trip_id: UUID | None = None
    revision_id: UUID | None = None
    revision_number: int | None = None
    citation_token: str | None = Field(default=None, max_length=100)

    @property
    def stable_token(self) -> str:
        return str(self.evidence_id)


class ResearchCitation(BaseModel):
    """Public projection of research citation for frontend display."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str = Field(description="Unique canonical citation identifier e.g. [cite:<uuid>]")
    evidence_id: UUID
    title: str | None = None
    domain: str = ""
    canonical_url: str = ""
    published_at: datetime | None = None
    retrieved_at: datetime
    locator: str | None = None
    cited_claims: list[str] = Field(default_factory=list)
    source_id: UUID | None = None


class ResearchFact(BaseModel):
    """Provenance-linked fact connecting a verified claim to supporting evidence and citation."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: UUID = Field(default_factory=uuid4)
    claim: str = Field(min_length=1, max_length=2000)
    evidence_id: UUID
    citation_id: str
    source_id: UUID | None = None


class ResearchConflict(BaseModel):
    """Detected conflict between multiple research sources."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_id: UUID = Field(default_factory=uuid4)
    topic: str
    claim_a: str
    source_a_title: str
    source_a_trust: SourceTrustClass
    claim_b: str
    source_b_title: str
    source_b_trust: SourceTrustClass
    resolution_summary: str
    resolved: bool = False


class ResearchResult(BaseModel):
    """Complete grounded research answer with evidence and citations."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    research_session_id: UUID
    query_id: UUID
    query_text: str = ""
    intent: ResearchIntent
    mode: ResearchMode
    answer: str
    citations: list[ResearchCitation] = Field(default_factory=list)
    facts: list[ResearchFact] = Field(default_factory=list)
    evidence_status: EvidenceStatus = EvidenceStatus.COMPLETE
    conflicts: list[ResearchConflict] = Field(default_factory=list)
    stale: bool = False
    evidence_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchSession(BaseModel):
    """Persistent session tracking a sequence of research queries."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    trip_id: UUID | None = None
    mode: ResearchMode = ResearchMode.AUTO
    status: str = Field(default="active", max_length=50)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime


class RagDocument(BaseModel):
    """Uploaded private document metadata for RAG."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    trip_id: UUID | None = None
    title: str = Field(max_length=500)
    filename: str = Field(max_length=500)
    content_type: str = Field(max_length=100)
    file_size_bytes: int = Field(ge=0, le=10 * 1024 * 1024)  # 10MB limit
    content_hash: str = Field(max_length=64)
    chunk_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RagChunk(BaseModel):
    """Chunk of an ingested RAG document."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    user_id: UUID
    chunk_index: int = Field(ge=0)
    text_content: str = Field(min_length=1, max_length=4000)
    token_count: int = Field(ge=0)
    content_hash: str = Field(max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchAdvisoryContext(BaseModel):
    """Structured advisory context passed from Research Agent to Planner.

    CRITICAL (Blocker 1):
    This object is treated as DATA only, never as executable instructions or
    human feedback. It cannot invoke tools, alter user identity, or mutate
    canonical TripRequest fields.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(default="", max_length=4000)
    facts: list[ResearchFact] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    source_ids: list[UUID] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    is_untrusted: bool = Field(default=True)
    status: str = Field(default="complete")


class ResearchAdvisoryProjection(BaseModel):
    """Typed non-executable advisory projection.

    Zero tool-call authority, zero prompt override authority, zero ability
    to modify canonical constraints. Raw research prose is NEVER placed into
    HumanMessage or SystemMessage.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(default="", max_length=4000)
    allowed_facts: list[str] = Field(default_factory=list)
    facts: list[ResearchFact] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    source_ids: list[UUID] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conflicts_as_structured_records: list[dict[str, Any]] = Field(default_factory=list)
    is_untrusted: bool = Field(default=True)
    status: str = Field(default="complete")
