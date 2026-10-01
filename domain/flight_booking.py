"""Flight Booking Domain Models and Exceptions for Section 5B.

Defines client-facing schemas for:
- Offer selection
- Pre-booking revalidation
- Human-in-the-loop (HITL) approval
- Booking confirmation
- Structured domain exceptions
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================================
# 1. DOMAIN EXCEPTIONS
# ============================================================================

class FlightBookingException(Exception):
    """Base exception for flight booking operations."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str = "BOOKING_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details or {}


class BookingNotFoundError(FlightBookingException):
    def __init__(self, message: str = "Booking intent or offer not found.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=404, code="NOT_FOUND", details=details)


class BookingPermissionError(FlightBookingException):
    def __init__(self, message: str = "Access denied: Resource does not belong to you.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=403, code="PERMISSION_DENIED", details=details)


class TripMismatchError(FlightBookingException):
    def __init__(self, message: str = "Offer snapshot was not generated for this trip.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=400, code="TRIP_MISMATCH", details=details)


class OfferStaleError(FlightBookingException):
    def __init__(self, message: str = "The selected flight offer has expired or is stale.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=410, code="OFFER_STALE", details=details)


class ApprovalRequiredError(FlightBookingException):
    def __init__(self, message: str = "Booking blocked: Human approval is mandatory before booking.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=400, code="APPROVAL_REQUIRED", details=details)


class FinalConfirmationRequiredError(FlightBookingException):
    def __init__(self, message: str = "Final user confirmation is required before booking.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=400, code="CONFIRMATION_REQUIRED", details=details)


class PriceChangedError(FlightBookingException):
    def __init__(
        self,
        original_price: float,
        new_price: float,
        currency: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        msg = message or f"Flight fare changed from {currency} {original_price:.2f} to {currency} {new_price:.2f}."
        d = {
            "original_price": original_price,
            "new_price": new_price,
            "currency": currency,
            **(details or {}),
        }
        super().__init__(msg, status_code=409, code="PRICE_CHANGED", details=d)


class OfferUnavailableError(FlightBookingException):
    def __init__(self, message: str = "The selected flight offer is no longer available from the supplier.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=410, code="OFFER_UNAVAILABLE", details=details)


class DuplicateBookingError(FlightBookingException):
    def __init__(self, message: str = "Duplicate confirmation blocked: Booking has already been completed.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=409, code="DUPLICATE_BOOKING", details=details)


class ProviderBookingError(FlightBookingException):
    def __init__(self, message: str = "Flight provider booking failed.", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, status_code=502, code="PROVIDER_ERROR", details=details)


# ============================================================================
# 2. PASSENGER & ENTITY SCHEMAS
# ============================================================================

class FlightPassenger(BaseModel):
    """Clean passenger representation for flight booking."""
    model_config = ConfigDict(extra="ignore")

    given_name: str = Field(min_length=1, max_length=40)
    family_name: str = Field(min_length=1, max_length=40)
    born_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(default="mr", min_length=2, max_length=4)
    gender: str = Field(default="m", min_length=1, max_length=1)
    email: str | None = None
    phone_number: str | None = None
    type: str = "adult"

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


# ============================================================================
# 3. REQUEST & RESPONSE MODELS
# ============================================================================

class FlightBookingSelectionRequest(BaseModel):
    """Client request to select an existing persisted flight offer snapshot."""
    model_config = ConfigDict(extra="ignore")

    trip_id: str = Field(description="Canonical trip UUID")
    offer_id: str = Field(description="Internal persisted offer snapshot UUID")
    passengers: list[FlightPassenger] = Field(min_length=1, max_length=9)


class FlightBookingSelectionResponse(BaseModel):
    """Response returned upon selecting a valid flight offer."""
    model_config = ConfigDict(extra="ignore")

    success: bool = True
    booking_id: str
    trip_id: str
    offer_id: str
    status: str = "SELECTED"
    price: float
    currency: str
    price_inr: float | None = None
    passenger_count: int = 1
    human_approval_required: bool = True
    approved: bool = False
    message: str = "Flight offer selected. Revalidation and human approval are required before booking."


class FlightBookingRevalidationRequest(BaseModel):
    """Client request to revalidate a selected booking offer."""
    model_config = ConfigDict(extra="ignore")

    booking_id: str | None = None
    accepted_price: float | None = None


class FlightBookingRevalidationResponse(BaseModel):
    """Response returned upon revalidating the selected offer with upstream provider."""
    model_config = ConfigDict(extra="ignore")

    success: bool
    booking_id: str
    status: str  # "READY_FOR_BOOKING", "PRICE_CHANGED", "UNAVAILABLE"
    is_valid: bool
    price_changed: bool = False
    original_price: float
    current_price: float
    currency: str
    human_approval_required: bool = True
    approved: bool = False
    message: str | None = None
    error: str | None = None


class FlightBookingApprovalRequest(BaseModel):
    """Client request to record human-in-the-loop (HITL) approval."""
    model_config = ConfigDict(extra="ignore")

    booking_id: str | None = None
    approved: bool = True
    notes: str | None = None


class FlightBookingApprovalResponse(BaseModel):
    """Response returned after recording human approval."""
    model_config = ConfigDict(extra="ignore")

    success: bool = True
    booking_id: str
    status: str
    approved: bool
    approved_at: str
    message: str = "Human approval recorded successfully."


class FlightBookingConfirmationRequest(BaseModel):
    """Client request to confirm booking with provider."""
    model_config = ConfigDict(extra="ignore")

    booking_id: str | None = None
    final_confirmation: bool = False
    payment: dict[str, Any] | None = None
    payment_mode: str = "auto"


class FlightBookingConfirmationResponse(BaseModel):
    """Response returned after executing booking."""
    model_config = ConfigDict(extra="ignore")

    success: bool
    booked: bool = False
    booking_id: str
    status: str  # "COMPLETED", "FAILED", "PRICE_CHANGED", "UNAVAILABLE"
    booking_reference: str | None = None
    order_id: str | None = None
    pnr: str | None = None
    e_ticket: str | None = None
    price: float | None = None
    currency: str | None = None
    message: str | None = None
    error: str | None = None
    duplicate_attempt: bool = False
    requires_final_confirmation: bool = False
    approval_required: bool = False
