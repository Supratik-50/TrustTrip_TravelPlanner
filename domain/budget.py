from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BudgetMode(StrEnum):
    FIXED = "fixed"
    FLEXIBLE = "flexible"
    CHEAPER = "cheaper"
    LUXURY = "luxury"


class BudgetConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    mode: BudgetMode = BudgetMode.FLEXIBLE

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()
