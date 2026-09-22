from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HotelPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    star_rating: int | None = Field(default=None, ge=1, le=5)
    rooms: int = Field(default=1, ge=1)
    style: str | None = None
    budget_preference: str | None = None
    cancellation_required: bool = False
