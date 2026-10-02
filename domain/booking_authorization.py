from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class BookingEligibilityError(Exception):
    """Raised when trip revision does not satisfy booking eligibility preconditions."""
    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.reasons = reasons or [message]


class BookingAuthorization(BaseModel):
    """Immutable explicit booking authorization record."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_id: UUID = Field(default_factory=uuid4)
    trip_id: UUID
    revision_id: UUID
    user_id: UUID
    revision_number: int = Field(ge=0)
    authorized_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    authorization_metadata: dict[str, Any] = Field(default_factory=dict)


class BookingEligibilityResult(BaseModel):
    """Runtime-derived booking eligibility status."""
    model_config = ConfigDict(extra="forbid")

    is_eligible: bool
    trip_id: UUID
    revision_id: UUID | None = None
    revision_number: int | None = None
    is_approved: bool = False
    is_rejected: bool = False
    is_authorized: bool = False
    authorized_at: datetime | None = None
    reasons: list[str] = Field(default_factory=list)
