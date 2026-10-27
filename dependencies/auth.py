"""FastAPI Authentication Dependencies for Trust Trip MAS.

Extracts and validates session cookies (trusttrip_access_token) or Bearer JWT access tokens.
Never trusts client-supplied query/body user_id.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from domain.user import UserResponse
from services.auth_service import decode_access_token
from services.auth_store import RevocationStoreError, TokenRevocationStore, UserStore
from services.config import COOKIE_NAME, get_allowed_origins

security_bearer = HTTPBearer(auto_error=False)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CUSTOM_CSRF_HEADERS = [
    "x-requested-with",
    "x-csrf-token",
    "x-trusttrip-csrf",
]


def validate_csrf(request: Request) -> None:
    """Validate CSRF protection for cookie-authenticated state-changing requests.

    Enforces origin whitelisting and custom header presence to prevent cross-origin
    ambient credential abuse.
    """
    if request.method in SAFE_METHODS:
        return

    # 1. Enforce origin if present
    origin = request.headers.get("origin")
    if origin:
        allowed = get_allowed_origins()
        norm_origin = origin.strip().rstrip("/").lower()
        norm_allowed = [o.strip().rstrip("/").lower() for o in allowed]
        if norm_origin not in norm_allowed and "*" not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed: origin not allowed.",
            )

    # 2. Reject cross-site Sec-Fetch-Site
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site and sec_fetch_site.lower() == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed: cross-site request rejected.",
        )

    # 3. Require a custom header (which standard cross-origin HTML forms cannot send)
    has_custom_header = any(
        h in request.headers for h in CUSTOM_CSRF_HEADERS
    )
    if not has_custom_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed: custom request header missing for cookie authentication.",
        )


def get_user_store() -> UserStore:
    return UserStore()


def get_revocation_store() -> TokenRevocationStore:
    return TokenRevocationStore()


async def get_current_user(
    request: Request = None,
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
    user_store: UserStore = Depends(get_user_store),
    revocation_store: TokenRevocationStore = Depends(get_revocation_store),
) -> UserResponse:
    """Validate session cookie or Bearer JWT and return authenticated UserResponse.

    Cookie is accepted for browser requests; Authorization: Bearer is retained for
    API clients during migration. If conflicting credentials are provided on browser
    requests, raises HTTP 401. If cookie authentication is used for state-changing
    requests, validates CSRF defenses. Token revocation status is verified centrally
    for all authentication channels.
    """
    cookie_token = request.cookies.get(COOKIE_NAME) if request is not None else None
    bearer_token = credentials.credentials if (credentials and credentials.credentials) else None

    # Deterministic credential resolution
    if cookie_token and bearer_token:
        if cookie_token != bearer_token:
            # Check if this is a browser/CSRF-aware request with conflicting credentials
            has_browser_indicators = False
            if request is not None:
                has_browser_indicators = bool(
                    request.headers.get("origin")
                    or request.headers.get("sec-fetch-site")
                    or any(h in request.headers for h in CUSTOM_CSRF_HEADERS)
                )
            if has_browser_indicators:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Conflicting authentication credentials provided.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            # For non-browser legacy API clients with ambient cookie in client jar,
            # honor the explicitly supplied Authorization: Bearer token
            token = bearer_token
            auth_source = "bearer"
        else:
            token = bearer_token
            auth_source = "bearer"
    elif bearer_token:
        token = bearer_token
        auth_source = "bearer"
    elif cookie_token:
        token = cookie_token
        auth_source = "cookie"
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Decode token and verify signature & expiration
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

    # 1b. Check server-side token revocation centrally
    try:
        if revocation_store.is_token_revoked(token, payload):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication token has been revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable; revocation status cannot be verified.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # 2. Verify user identity & existence in store
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

    # 3. CSRF protection: validate state-changing requests when authenticated via cookie
    if auth_source == "cookie" and request is not None and request.method not in SAFE_METHODS:
        validate_csrf(request)

    return UserResponse(
        id=user_record["id"],
        email=user_record["email"],
        is_active=user_record["is_active"],
        created_at=user_record["created_at"],
    )


async def get_optional_current_user(
    request: Request = None,
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
    user_store: UserStore = Depends(get_user_store),
    revocation_store: TokenRevocationStore = Depends(get_revocation_store),
) -> UserResponse | None:
    """Return authenticated UserResponse if valid cookie or Bearer token is present, else None.

    HTTP 401 responses (missing, expired, revoked, or invalid credentials) are treated as
    "no authenticated user" and return None, preserving optional-auth semantics.

    Non-401 responses (HTTP 403 CSRF failure, deactivated account, HTTP 503 revocation-store
    unavailable) are re-raised unconditionally so security enforcement is never silently bypassed.
    """
    cookie_token = request.cookies.get(COOKIE_NAME) if request is not None else None
    bearer_token = credentials.credentials if (credentials and credentials.credentials) else None
    if not cookie_token and not bearer_token:
        return None
    try:
        return await get_current_user(request, credentials, user_store, revocation_store)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            # Re-raise: CSRF failures, deactivated accounts, and store availability errors
            # must not be silently coerced into anonymous access.
            raise
        # HTTP 401: credentials absent, expired, or revoked — treat as unauthenticated.
        return None


