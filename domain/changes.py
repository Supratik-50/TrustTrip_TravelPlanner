from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .trip import TripRequest


class TripChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: UUID = Field(default_factory=uuid4)
    changed_fields: list[str] = Field(default_factory=list)
    old_values: dict[str, Any] = Field(default_factory=dict)
    new_values: dict[str, Any] = Field(default_factory=dict)
    affected_agents: list[str] = Field(default_factory=list)
    invalidated_evidence: list[str] = Field(default_factory=list)
    requires_live_search: bool = False
    requires_itinerary_regeneration: bool = True


def apply_trip_change_set(
    existing_trip: TripRequest,
    change_set: TripChangeSet,
) -> TripRequest:
    """Apply only declared top-level changes and preserve all other fields."""
    updates: dict[str, Any] = {}
    for field_name in change_set.changed_fields:
        if field_name not in change_set.new_values:
            raise ValueError(f"Missing new value for changed field: {field_name}")
        if field_name not in TripRequest.model_fields:
            raise ValueError(f"Unsupported trip field: {field_name}")
        updates[field_name] = change_set.new_values[field_name]

    values = existing_trip.model_dump(mode="python")
    values.update(updates)
    return TripRequest.model_validate(values)
