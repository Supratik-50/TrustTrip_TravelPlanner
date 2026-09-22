from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PassengerComposition(BaseModel):
    """Canonical passenger counts for planning and provider requests."""

    model_config = ConfigDict(extra="forbid")

    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    infants: int = Field(default=0, ge=0)
    child_ages: list[int] = Field(default_factory=list)

    @field_validator("child_ages")
    @classmethod
    def validate_child_ages(cls, value: list[int]) -> list[int]:
        if any(age < 0 for age in value):
            raise ValueError("Child ages cannot be negative.")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.child_ages and len(self.child_ages) != self.children:
            raise ValueError("child_ages must contain one age for each child.")

    @property
    def passenger_count(self) -> int:
        return self.adults + self.children + self.infants
