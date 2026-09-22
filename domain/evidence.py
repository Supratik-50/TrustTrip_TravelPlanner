from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Legacy backwards compatibility
class EvidenceSourceType(StrEnum):
    LIVE_API = "live_api"
    WEB = "web"
    RAG = "rag"
    DATABASE = "database"
    LLM = "llm"


class EvidenceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source: str
    source_type: EvidenceSourceType
    created_at: datetime
    expires_at: datetime | None = None
    invalidated: bool = False
    related_search_id: str | None = None


# Phase 6 Core Enums
class EvidenceType(StrEnum):
    FLIGHT = "FLIGHT"
    HOTEL = "HOTEL"
    WEATHER = "WEATHER"
    BUDGET = "BUDGET"
    ITINERARY = "ITINERARY"
    ACTIVITY = "ACTIVITY"


class UsageState(StrEnum):
    FRESH = "FRESH"
    REUSED = "REUSED"
    INVALID_FOR_REVISION = "INVALID_FOR_REVISION"


class FreshnessKind(StrEnum):
    LIVE_SUPPLIER = "LIVE_SUPPLIER"
    CACHED_PORT_DATA = "CACHED_PORT_DATA"
    SYNTHETIC_AGENT = "SYNTHETIC_AGENT"
    STATIC_DATA = "STATIC_DATA"


def canonical_hash(data: Any) -> str:
    """Compute deterministic SHA-256 hash of canonical JSON."""
    canonical_str = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


class EvidenceRecord(BaseModel):
    """Unconditionally immutable point-in-time travel observation."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID = Field(default_factory=uuid4)
    trip_id: UUID
    user_id: UUID
    evidence_type: EvidenceType
    applicability_key: dict[str, Any]
    applicability_hash: str = ""
    content_hash: str = ""
    freshness_kind: FreshnessKind
    freshness_ttl_seconds: int = Field(gt=0)
    produced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    supplier_expires_at: datetime | None = None
    source_agent: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def populate_hashes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("applicability_hash") and data.get("applicability_key") is not None:
                data["applicability_hash"] = canonical_hash(data["applicability_key"])
            if not data.get("content_hash") and data.get("payload") is not None:
                data["content_hash"] = canonical_hash(data["payload"])
        return data

    def is_fresh(self, now: datetime | None = None) -> bool:
        """Dual freshness evaluation: TTL check AND supplier expiry check."""
        if now is None:
            now = datetime.now(timezone.utc)
        if self.produced_at + timedelta(seconds=self.freshness_ttl_seconds) <= now:
            return False
        if self.supplier_expires_at is not None and self.supplier_expires_at <= now:
            return False
        return True


class EvidenceProvenance(BaseModel):
    """Provider-session/snapshot attachment (attach-once, detachable only upon upstream deletion)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID
    trip_id: UUID
    user_id: UUID
    supplier_session_id: UUID | None = None
    supplier_snapshot_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanRevisionEvidenceLink(BaseModel):
    """Link tying an immutable EvidenceRecord to a specific PlanRevision."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    link_id: UUID = Field(default_factory=uuid4)
    revision_id: UUID
    evidence_id: UUID
    trip_id: UUID
    user_id: UUID
    usage_state: UsageState
    linked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
