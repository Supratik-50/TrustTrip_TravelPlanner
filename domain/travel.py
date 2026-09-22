from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TravelPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str | None = None
    activities: list[str] = Field(default_factory=list)
    pace: str | None = None
    sightseeing_preferences: list[str] = Field(default_factory=list)
