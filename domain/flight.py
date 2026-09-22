from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FlightPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cabin_class: str = "economy"
    max_connections: int = Field(default=2, ge=0)
    direct_only: bool = False
    preferred_airlines: list[str] = Field(default_factory=list)
    flexible_dates: bool = False

    @field_validator("cabin_class")
    @classmethod
    def normalize_cabin_class(cls, value: str) -> str:
        return value.strip().lower()
