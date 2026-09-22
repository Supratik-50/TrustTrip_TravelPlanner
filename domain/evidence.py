from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
