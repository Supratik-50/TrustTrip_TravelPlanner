from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class PlanApproval(BaseModel):
    """Immutable HITL plan approval record."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: UUID = Field(default_factory=uuid4)
    trip_id: UUID
    revision_id: UUID
    approved_by_user_id: UUID
    approved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approval_metadata: dict[str, Any] = Field(default_factory=dict)
