"""Authentication API Router for Trust Trip MAS.

Implements /api/auth/signup, /api/auth/login, and /api/auth/me.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from domain.user import (
    TokenResponse,
    UserLoginRequest,
    UserResponse,
    UserSignUpRequest,
)
from dependencies.auth import get_current_user
from services.auth_service import create_access_token, hash_password, verify_password
from services.auth_store import UserStore

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _store() -> UserStore:
    return UserStore()


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def signup(
    request: UserSignUpRequest,
    store: UserStore = Depends(_store),
):
    email = request.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A valid email address is required.",
        )

    try:
        pw_hash = hash_password(request.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        user_record = store.create_user(email=email, password_hash=pw_hash)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        ) from exc

    user_resp = UserResponse(
        id=user_record["id"],
        email=user_record["email"],
        is_active=user_record["is_active"],
        created_at=user_record["created_at"],
    )
    token = create_access_token(user_id=str(user_resp.id), email=user_resp.email)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=user_resp,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate an existing user",
)
async def login(
    request: UserLoginRequest,
    store: UserStore = Depends(_store),
):
    email = request.email.strip().lower()
    user_record = store.get_by_email(email)
    if not user_record or not verify_password(request.password, user_record["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user_record.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )

    user_resp = UserResponse(
        id=user_record["id"],
        email=user_record["email"],
        is_active=user_record["is_active"],
        created_at=user_record["created_at"],
    )
    token = create_access_token(user_id=str(user_resp.id), email=user_resp.email)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=user_resp,
    )


@router.get(
    "/me",
    summary="Get profile of currently authenticated user",
)
async def me(current_user: UserResponse = Depends(get_current_user)):
    return {
        "success": True,
        "user": current_user.model_dump(mode="json"),
    }
