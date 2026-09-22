"""FastAPI Authentication Dependencies for Trust Trip MAS.

Extracts and validates Bearer JWT access tokens.
Never trusts client-supplied query/body user_id.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from domain.user import UserResponse
from services.auth_service import decode_access_token
from services.auth_store import UserStore

security_bearer = HTTPBearer(auto_error=False)


def get_user_store() -> UserStore:
    return UserStore()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
    user_store: UserStore = Depends(get_user_store),
) -> UserResponse:
    """Validate Bearer JWT and return the authenticated UserResponse.
    
    Raises HTTP 401 if token is missing, expired, or invalid, or if the user is disabled.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing user identity.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_record = user_store.get_by_id(user_id)
    if not user_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this token was not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user_record.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )

    return UserResponse(
        id=user_record["id"],
        email=user_record["email"],
        is_active=user_record["is_active"],
        created_at=user_record["created_at"],
    )


async def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
    user_store: UserStore = Depends(get_user_store),
) -> UserResponse | None:
    """Return authenticated UserResponse if valid Bearer token is present, else None."""
    if not credentials or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials, user_store)
    except HTTPException:
        return None
