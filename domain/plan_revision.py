from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanRevision(BaseModel):
    """Immutable published plan revision."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: UUID = Field(default_factory=uuid4)
    trip_id: UUID
    user_id: UUID
    revision_number: int = Field(ge=0)
    change_set: dict[str, Any]
    plan_snapshot: dict[str, Any]
    is_published: bool = True
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("is_published")
    @classmethod
    def validate_is_published(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("PlanRevision must always be published (is_published must be True)")
        return v
