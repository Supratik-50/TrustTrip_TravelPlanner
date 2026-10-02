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
    parent_revision_id: UUID | None = None
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


class PlanRevisionRejection(BaseModel):
    """Immutable HITL plan revision rejection record."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    rejection_id: UUID = Field(default_factory=uuid4)
    trip_id: UUID
    revision_id: UUID
    rejected_by_user_id: UUID
    rejected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str | None = None
    rejection_metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredDiffItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    label: str = ""
    old_value: Any = None
    new_value: Any = None
    value: Any = None
    description: str = ""


class StructuredDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: list[StructuredDiffItem] = Field(default_factory=list)
    removed: list[StructuredDiffItem] = Field(default_factory=list)
    changed: list[StructuredDiffItem] = Field(default_factory=list)


def derive_revision_status(
    has_approval: bool,
    has_rejection: bool,
) -> str:
    """Derives immutable review state: APPROVED, REJECTED, or PROPOSED."""
    if has_approval:
        return "APPROVED"
    if has_rejection:
        return "REJECTED"
    return "PROPOSED"


def compute_structured_diff(
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any] | None,
    feedback: str = "",
) -> StructuredDiff:
    """Compute concrete added, removed, and changed items between revision constraints or snapshots."""
    old_dict = dict(old_data or {})
    new_dict = dict(new_data or {})

    # Extract constraints if wrapped in snapshot
    if "constraints" in old_dict and isinstance(old_dict["constraints"], dict):
        old_dict = {**old_dict, **old_dict["constraints"]}
    if "constraints" in new_dict and isinstance(new_dict["constraints"], dict):
        new_dict = {**new_dict, **new_dict["constraints"]}

    added: list[StructuredDiffItem] = []
    removed: list[StructuredDiffItem] = []
    changed: list[StructuredDiffItem] = []

    # Human feedback / notes
    if feedback and feedback.strip():
        added.append(
            StructuredDiffItem(
                field="feedback",
                label="Human Guidance",
                value=feedback.strip(),
                description=f"Human Guidance: {feedback.strip()}",
            )
        )

    tracked_keys = [
        ("origin", "Departure Location"),
        ("destination", "Destination"),
        ("departure_date", "Departure Date"),
        ("start_date", "Start Date"),
        ("return_date", "Return Date"),
        ("end_date", "End Date"),
        ("duration_days", "Duration (Days)"),
        ("adults", "Adult Travelers"),
        ("passengers", "Passenger Count"),
        ("travelers_count", "Traveler Count"),
        ("budget_amount", "Budget Limit"),
        ("budget", "Budget Limit"),
        ("currency", "Currency"),
        ("travel_style", "Travel Style"),
        ("flight_preference", "Flight Preference"),
        ("cabin_class", "Cabin Class"),
        ("hotel_preference", "Hotel Preference"),
        ("min_star_rating", "Min Star Rating"),
        ("board_type", "Board Type"),
        ("selected_flight_snapshot_id", "Selected Flight"),
        ("selected_hotel_snapshot_id", "Selected Hotel"),
    ]

    for key, label in tracked_keys:
        old_val = old_dict.get(key)
        new_val = new_dict.get(key)

        # Normalize empty strings and None
        if old_val == "":
            old_val = None
        if new_val == "":
            new_val = None

        if old_val is None and new_val is not None:
            added.append(
                StructuredDiffItem(
                    field=key,
                    label=label,
                    value=new_val,
                    new_value=new_val,
                    description=f"Set {label}: {new_val}",
                )
            )
        elif old_val is not None and new_val is None:
            removed.append(
                StructuredDiffItem(
                    field=key,
                    label=label,
                    old_value=old_val,
                    value=old_val,
                    description=f"Removed {label} (was {old_val})",
                )
            )
        elif old_val is not None and new_val is not None and str(old_val) != str(new_val):
            changed.append(
                StructuredDiffItem(
                    field=key,
                    label=label,
                    old_value=old_val,
                    new_value=new_val,
                    description=f"{label} changed from {old_val} to {new_val}",
                )
            )

    # General arbitrary keys
    handled_keys = {k for k, _ in tracked_keys} | {"constraints", "preferences", "feedback"}
    remaining_keys = sorted((set(old_dict.keys()) | set(new_dict.keys())) - handled_keys)
    for key in remaining_keys:
        old_val = old_dict.get(key)
        new_val = new_dict.get(key)
        if old_val == "":
            old_val = None
        if new_val == "":
            new_val = None
        label = key.replace("_", " ").title()
        if old_val is None and new_val is not None:
            added.append(
                StructuredDiffItem(
                    field=key,
                    label=label,
                    value=new_val,
                    new_value=new_val,
                    description=f"Added {label}: {new_val}",
                )
            )
        elif old_val is not None and new_val is None:
            removed.append(
                StructuredDiffItem(
                    field=key,
                    label=label,
                    old_value=old_val,
                    value=old_val,
                    description=f"Removed {label} (was {old_val})",
                )
            )
        elif old_val is not None and new_val is not None and str(old_val) != str(new_val):
            changed.append(
                StructuredDiffItem(
                    field=key,
                    label=label,
                    old_value=old_val,
                    new_value=new_val,
                    description=f"{label} changed from {old_val} to {new_val}",
                )
            )

    # Preferences list comparison
    old_prefs = set(old_dict.get("preferences") or [])
    new_prefs = set(new_dict.get("preferences") or [])
    for p in sorted(new_prefs - old_prefs):
        added.append(
            StructuredDiffItem(
                field="preferences",
                label="Preference Added",
                value=p,
                new_value=p,
                description=f"Added preference: {p}",
            )
        )
    for p in sorted(old_prefs - new_prefs):
        removed.append(
            StructuredDiffItem(
                field="preferences",
                label="Preference Removed",
                old_value=p,
                value=p,
                description=f"Removed preference: {p}",
            )
        )

    return StructuredDiff(added=added, removed=removed, changed=changed)
