"""Domain models, state machine, and error taxonomy for Flight Booking Lifecycle.

Strictly preserves Phase 1-8 invariants:
- Zero URL fabrication.
- Authoritative TrustTrip identity (UUID), supplier references secondary.
- Fail-closed state transitions.
- Append-only snapshot immutability.
- Strict tenant and revision locking.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================================
# 1. ENUMS
# ============================================================================

class BookingProvider(StrEnum):
    """Supported upstream flight inventory and booking providers."""
    DUFFEL = "duffel"
    AMADEUS = "amadeus"
    TEST_STUB = "test_stub"


class BookingMode(StrEnum):
    """Execution mode for flight checkout and order creation."""
    DUFFEL_LINK = "duffel_link"                  # Provider-hosted checkout session (Production)
    DUFFEL_API_ORDER = "duffel_api_order"        # Direct API order (Sandbox balance or direct)
    DIRECT_AIRLINE_VERIFIED = "direct_airline_verified"  # Verified carrier URL handoff


class BookingStatus(StrEnum):
    """Formal lifecycle states of a TrustTrip flight booking intent."""
    DRAFT = "DRAFT"
    SELECTED = "SELECTED"
    REVALIDATING = "REVALIDATING"
    EXPIRED = "EXPIRED"
    PRICE_CHANGED = "PRICE_CHANGED"
    UNAVAILABLE = "UNAVAILABLE"
    READY_FOR_BOOKING = "READY_FOR_BOOKING"  # Synonymous with VALID
    HANDED_OFF = "HANDED_OFF"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


# ============================================================================
# 2. STATE MACHINE TRANSITIONS & GUARDS
# ============================================================================

LEGAL_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.DRAFT: {
        BookingStatus.SELECTED,
        BookingStatus.ABANDONED,
    },
    BookingStatus.SELECTED: {
        BookingStatus.REVALIDATING,
        BookingStatus.ABANDONED,
        BookingStatus.EXPIRED,
    },
    BookingStatus.REVALIDATING: {
        BookingStatus.READY_FOR_BOOKING,
        BookingStatus.PRICE_CHANGED,
        BookingStatus.UNAVAILABLE,
        BookingStatus.EXPIRED,
        BookingStatus.FAILED,
    },
    BookingStatus.PRICE_CHANGED: {
        BookingStatus.SELECTED,
        BookingStatus.ABANDONED,
    },
    BookingStatus.READY_FOR_BOOKING: {
        BookingStatus.HANDED_OFF,
        BookingStatus.REVALIDATING,
        BookingStatus.ABANDONED,
        BookingStatus.EXPIRED,
        BookingStatus.UNAVAILABLE,
        BookingStatus.PRICE_CHANGED,
    },
    BookingStatus.HANDED_OFF: {
        BookingStatus.COMPLETED,
        BookingStatus.FAILED,
        BookingStatus.ABANDONED,
    },
    # Terminal states have NO outgoing transitions
    BookingStatus.EXPIRED: set(),
    BookingStatus.UNAVAILABLE: set(),
    BookingStatus.COMPLETED: set(),
    BookingStatus.FAILED: set(),
    BookingStatus.ABANDONED: set(),
}

TERMINAL_STATES: frozenset[BookingStatus] = frozenset({
    BookingStatus.EXPIRED,
    BookingStatus.UNAVAILABLE,
    BookingStatus.COMPLETED,
    BookingStatus.FAILED,
    BookingStatus.ABANDONED,
})


def validate_booking_transition(from_status: BookingStatus, to_status: BookingStatus) -> None:
    """Validate whether transitioning from `from_status` to `to_status` is legal.

    Raises InvalidBookingStateTransitionError if the transition is illegal or
    if attempting to transition out of a terminal state.
    """
    if from_status in TERMINAL_STATES:
        raise InvalidBookingStateTransitionError(
            f"Cannot transition from terminal state '{from_status}' to '{to_status}'."
        )

    allowed = LEGAL_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise InvalidBookingStateTransitionError(
            f"Illegal state transition from '{from_status}' to '{to_status}'. "
            f"Permitted target states: {sorted([s.value for s in allowed]) or 'None (Terminal)'}."
        )


# ============================================================================
# 3. STRUCTURED DOMAIN EXCEPTIONS
# ============================================================================

class BookingLifecycleException(Exception):
    """Base exception for flight booking lifecycle operations."""

    def __init__(self, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class InvalidBookingStateTransitionError(BookingLifecycleException):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("INVALID_BOOKING_STATE", message, status_code=400, details=details)


class OfferExpiredException(BookingLifecycleException):
    def __init__(self, message: str = "The selected flight offer has expired.", details: dict[str, Any] | None = None) -> None:
        super().__init__("OFFER_EXPIRED", message, status_code=410, details=details)


class OfferChangedException(BookingLifecycleException):
    def __init__(self, message: str = "Flight schedule or routing details changed.", details: dict[str, Any] | None = None) -> None:
        super().__init__("OFFER_CHANGED", message, status_code=409, details=details)


class OfferDataInvalidException(BookingLifecycleException):
    def __init__(self, message: str = "Required provider offer verification fields are missing or malformed.", details: dict[str, Any] | None = None) -> None:
        super().__init__("OFFER_DATA_INVALID", message, status_code=400, details=details)


class FareChangedException(BookingLifecycleException):
    def __init__(
        self,
        original_amount: Decimal,
        new_amount: Decimal,
        currency: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        base_msg = f"Flight fare changed from {currency} {original_amount} to {currency} {new_amount}."
        msg = f"{base_msg} {message}" if message else base_msg
        merged_details = {
            "original_amount": str(original_amount),
            "new_amount": str(new_amount),
            "currency": currency,
            **(details or {}),
        }
        super().__init__("PRICE_CHANGED", msg, status_code=409, details=merged_details)


class OfferUnavailableException(BookingLifecycleException):
    def __init__(self, message: str = "The selected flight offer is no longer available from the supplier.", details: dict[str, Any] | None = None) -> None:
        super().__init__("OFFER_UNAVAILABLE", message, status_code=410, details=details)


class StaleRevisionException(BookingLifecycleException):
    def __init__(self, message: str = "Trip requirements modified since selection; revision is stale.", details: dict[str, Any] | None = None) -> None:
        super().__init__("REVISION_STALE", message, status_code=409, details=details)


class BookingLockedException(BookingLifecycleException):
    def __init__(self, message: str = "A booking operation is currently in progress for this intent.", details: dict[str, Any] | None = None) -> None:
        super().__init__("BOOKING_ALREADY_IN_PROGRESS", message, status_code=409, details=details)


class BookingCompletedException(BookingLifecycleException):
    def __init__(self, message: str = "This booking has already been completed.", details: dict[str, Any] | None = None) -> None:
        super().__init__("BOOKING_ALREADY_COMPLETED", message, status_code=409, details=details)


class BookingProviderTimeout(BookingLifecycleException):
    def __init__(self, message: str = "Upstream flight provider timed out. Please retry.", details: dict[str, Any] | None = None) -> None:
        super().__init__("PROVIDER_TIMEOUT", message, status_code=504, details=details)


class BookingProviderUnavailable(BookingLifecycleException):
    def __init__(self, message: str = "Flight booking provider is temporarily unavailable.", details: dict[str, Any] | None = None) -> None:
        super().__init__("PROVIDER_UNAVAILABLE", message, status_code=502, details=details)


class BookingProviderRateLimited(BookingLifecycleException):
    def __init__(self, message: str = "Upstream flight provider rate limit exceeded (HTTP 429).", details: dict[str, Any] | None = None) -> None:
        super().__init__("PROVIDER_RATE_LIMITED", message, status_code=429, details=details)


class OwnershipViolationException(BookingLifecycleException):
    def __init__(self, message: str = "Access denied: Resource does not belong to the authenticated user.", details: dict[str, Any] | None = None) -> None:
        super().__init__("OWNERSHIP_VIOLATION", message, status_code=403, details=details)


class IdempotencyKeyRequiredException(BookingLifecycleException):
    def __init__(self, message: str = "Idempotency-Key header is required for all mutating booking endpoints.", details: dict[str, Any] | None = None) -> None:
        super().__init__("IDEMPOTENCY_KEY_REQUIRED", message, status_code=400, details=details)


class IdempotencyConflictException(BookingLifecycleException):
    def __init__(self, message: str = "Idempotency key has already been used with a different request payload.", details: dict[str, Any] | None = None) -> None:
        super().__init__("IDEMPOTENCY_CONFLICT", message, status_code=409, details=details)


class BookingInProgressException(BookingLifecycleException):
    def __init__(self, message: str = "A booking operation is currently in progress for this request key.", details: dict[str, Any] | None = None) -> None:
        super().__init__("BOOKING_IN_PROGRESS", message, status_code=409, details=details)


class BookingExpiredException(BookingLifecycleException):
    def __init__(self, message: str = "This booking intent has expired.", details: dict[str, Any] | None = None) -> None:
        super().__init__("BOOKING_EXPIRED", message, status_code=410, details=details)


class InvalidCallbackException(BookingLifecycleException):
    def __init__(self, message: str = "The provider callback payload or event format is invalid.", details: dict[str, Any] | None = None) -> None:
        super().__init__("INVALID_CALLBACK", message, status_code=400, details=details)


class UnauthorizedCallbackException(BookingLifecycleException):
    def __init__(self, message: str = "Provider callback signature verification failed.", details: dict[str, Any] | None = None) -> None:
        super().__init__("UNAUTHORIZED_CALLBACK", message, status_code=401, details=details)


def validate_idempotency_key(key: str | None) -> str:
    """Validate that an Idempotency-Key is present and conforms to RFC guidelines."""
    import re
    if not key or not isinstance(key, str):
        raise IdempotencyKeyRequiredException("Idempotency-Key header is required for all mutating booking endpoints.")
    trimmed = key.strip()
    if not trimmed:
        raise IdempotencyKeyRequiredException("Idempotency-Key cannot be empty or blank.")
    if len(trimmed) > 128:
        raise IdempotencyKeyRequiredException("Idempotency-Key must not exceed 128 characters.")
    if not re.match(r"^[A-Za-z0-9_\-\.:]+$", trimmed):
        raise IdempotencyKeyRequiredException(
            "Idempotency-Key contains invalid characters. Use alphanumeric, dash, colon, dot, or underscore."
        )
    return trimmed


class SandboxBalancePaymentModel(BaseModel):
    """Strict allowlisted schema for test-only sandbox balance order payments.

    Zero cardholder PII permitted.
    """
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="balance", pattern="^balance$")
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal | None = None
    account_id: str | None = Field(default=None, max_length=64)


class ProviderWebhookPayload(BaseModel):
    """Structured Duffel webhook/callback payload."""
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    created_at: datetime | str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 4. DOMAIN ENTITIES & VALUE OBJECTS
# ============================================================================

class PassengerBookingModel(BaseModel):
    """Clean passenger representation for booking intent and handoff."""
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    given_name: str = Field(min_length=1, max_length=40)
    family_name: str = Field(min_length=1, max_length=40)
    born_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=2, max_length=4)
    gender: str = Field(min_length=1, max_length=1)
    email: str | None = None
    phone_number: str | None = None
    passenger_type: str = "adult"

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in {"mr", "mrs", "ms", "miss", "mx", "dr"}:
            raise ValueError("Passenger title must be mr, mrs, ms, miss, mx, or dr.")
        return cleaned

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in {"m", "f", "x"}:
            raise ValueError("Passenger gender must be m, f, or x.")
        return cleaned


def compute_passenger_hash(passengers: list[PassengerBookingModel]) -> str:
    """Compute deterministic SHA-256 hash of passenger composition."""
    normalized = []
    for p in passengers:
        normalized.append({
            "given_name": p.given_name.strip().lower(),
            "family_name": p.family_name.strip().lower(),
            "born_on": p.born_on.strip(),
            "gender": p.gender.lower(),
            "title": p.title.lower(),
        })
    raw = json.dumps(sorted(normalized, key=lambda x: (x["family_name"], x["given_name"])), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BookingTransition(BaseModel):
    """Immutable audit record representing a verified state transition."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    transition_id: UUID = Field(default_factory=uuid4)
    intent_id: UUID
    from_status: BookingStatus
    to_status: BookingStatus
    trigger: str
    actor_id: str
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OfferRevalidationResult(BaseModel):
    """Detailed result of pre-handoff revalidation."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    is_valid: bool
    status: BookingStatus
    original_snapshot_id: UUID
    fresh_snapshot_id: UUID | None = None
    price_changed: bool = False
    original_amount: Decimal
    current_amount: Decimal
    currency: str
    divergent_fields: list[str] = Field(default_factory=list)
    revalidated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: str = ""


class BookingIntent(BaseModel):
    """Authoritative TrustTrip flight booking intent entity."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    user_id: UUID
    trip_id: UUID
    revision_id: UUID
    selected_offer_snapshot_id: UUID
    current_revalidation_snapshot_id: UUID | None = None
    provider: BookingProvider = BookingProvider.DUFFEL
    booking_mode: BookingMode = BookingMode.DUFFEL_LINK
    status: BookingStatus = BookingStatus.DRAFT
    idempotency_key: str
    provider_reference: str | None = None
    handoff_url: str | None = None
    handoff_expires_at: datetime | None = None
    passenger_hash: str
    expected_amount: Decimal
    currency: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


# ============================================================================
# 5. API REQUEST & RESPONSE MODELS
# ============================================================================

class PrepareFlightBookingRequest(BaseModel):
    """Client request to prepare a flight booking intent.

    Section 10: exact revision identity is mandatory for every booking operation.
    """
    model_config = ConfigDict(extra="forbid")

    trip_id: UUID
    snapshot_id: UUID
    revision_id: UUID
    expected_revision_number: int = Field(ge=1)
    passengers: list[PassengerBookingModel] = Field(min_length=1, max_length=9)
    booking_mode: BookingMode = BookingMode.DUFFEL_LINK


class RevalidateFlightBookingRequest(BaseModel):
    """Client request to revalidate or accept updated fare for a booking intent."""
    model_config = ConfigDict(extra="forbid")

    accepted_amount: Decimal | None = None
    revision_id: UUID
    expected_revision_number: int = Field(ge=1)


class HandoffFlightBookingRequest(BaseModel):
    """Client request to obtain checkout handoff destination."""
    model_config = ConfigDict(extra="forbid")

    booking_mode: BookingMode | None = None
    accepted_amount: Decimal | None = None
    payment: SandboxBalancePaymentModel | None = None  # Used exclusively for sandbox balance demo
    revision_id: UUID
    expected_revision_number: int = Field(ge=1)


class CancelFlightBookingRequest(BaseModel):
    """Client request to abort/abandon a booking intent."""
    model_config = ConfigDict(extra="forbid")

    reason: str = "User requested cancellation"


class FlightHandoffDestinationResponse(BaseModel):
    """Server-minted, verified external checkout destination."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: UUID
    status: BookingStatus
    booking_mode: BookingMode
    redirect_url: str | None
    provider_reference: str | None
    handoff_expires_at: datetime | None
    airline_name: str = ""
    flight_number: str = ""
    price_total: Decimal
    currency: str
    instructions: str
