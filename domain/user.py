"""User and Authentication Domain Models for Trust Trip MAS.

Enforces email validation, secure schema definition, and guarantees
password_hash is never exposed through API response models.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class User(BaseModel):
    """Canonical User domain entity."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    email: str = Field(min_length=3, max_length=255)
    password_hash: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserResponse(BaseModel):
    """Public User representation. Never exposes password_hash."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    is_active: bool
    created_at: datetime


class UserSignUpRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=128)


class UserLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str | None = None
    token_type: str | None = "bearer"
    user: UserResponse
