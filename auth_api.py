"""Authentication API Router for Trust Trip MAS.

Implements /api/auth/signup, /api/auth/login, /api/auth/me, and /api/auth/logout.
Issues secure HttpOnly session cookies alongside backward-compatible Bearer tokens.
"""
from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from domain.user import (
    TokenResponse,
    UserLoginRequest,
    UserResponse,
    UserSignUpRequest,
)
from dependencies.auth import (
    CUSTOM_CSRF_HEADERS,
    get_current_user,
    get_revocation_store,
    security_bearer,
    validate_csrf,
)
from services.auth_service import (
    JWT_ALGORITHM,
    create_access_token,
    get_auth_secret,
    hash_password,
    verify_password,
)
from services.auth_store import RevocationStoreError, TokenRevocationStore, UserStore
from services.config import (
    COOKIE_NAME,
    DEFAULT_COOKIE_MAX_AGE,
    DEFAULT_COOKIE_PATH,
    DEFAULT_COOKIE_SAMESITE,
    get_allowed_origins,
    get_secure_cookie_setting,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _store() -> UserStore:
    return UserStore()


def is_browser_cookie_mode(request: Request | None) -> bool:
    """Determine whether the client requested browser cookie response mode.

    Response-mode contract:
    - Explicit override for bearer token: `X-Auth-Mode: token` / `X-Auth-Mode: bearer`
      or query param `response_mode=token` / `response_mode=bearer`.
    - Explicit cookie mode: `X-Auth-Mode: cookie` or `X-Response-Mode: cookie`
      or query param `response_mode=cookie`.
    - Browser client signature: `X-Requested-With: XMLHttpRequest` or
      `Sec-Fetch-Mode` present (e.g., standard browser fetch / XHR).

    Note: This is strictly a response formatting / mode negotiation contract.
    It NEVER serves as proof of caller identity, privileges, or authorization.
    """
    if not request:
        return False

    auth_mode = request.headers.get("x-auth-mode", "").strip().lower()
    if auth_mode in ("token", "bearer"):
        return False
    if auth_mode == "cookie":
        return True

    resp_mode = request.headers.get("x-response-mode", "").strip().lower()
    if resp_mode in ("token", "bearer"):
        return False
    if resp_mode == "cookie":
        return True

    query_mode = request.query_params.get("response_mode", "").strip().lower()
    if query_mode in ("token", "bearer"):
        return False
    if query_mode == "cookie":
        return True

    # Browser client signature (e.g. XMLHttpRequest from frontend apiClient)
    x_requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if x_requested_with == "xmlhttprequest":
        return True

    sec_fetch_mode = request.headers.get("sec-fetch-mode", "").strip().lower()
    if sec_fetch_mode in ("cors", "navigate", "same-origin"):
        return True

    return False


def validate_auth_endpoint_csrf(request: Request | None) -> None:
    """Validate CSRF defenses on auth endpoints (login, signup).

    Protects against login CSRF and cross-site form registration attacks.
    """
    if not request:
        return
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
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site and sec_fetch_site.lower() == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed: cross-site request rejected.",
        )


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def signup(
    request: UserSignUpRequest,
    http_request: Request = None,
    response: Response = None,
    store: UserStore = Depends(_store),
):
    validate_auth_endpoint_csrf(http_request)

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

    if response is not None:
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=DEFAULT_COOKIE_MAX_AGE,
            httponly=True,
            secure=get_secure_cookie_setting(),
            samesite=DEFAULT_COOKIE_SAMESITE,
            path=DEFAULT_COOKIE_PATH,
        )

    if is_browser_cookie_mode(http_request):
        return TokenResponse(
            access_token=None,
            token_type=None,
            user=user_resp,
        )

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
    http_request: Request = None,
    response: Response = None,
    store: UserStore = Depends(_store),
):
    validate_auth_endpoint_csrf(http_request)

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

    if response is not None:
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=DEFAULT_COOKIE_MAX_AGE,
            httponly=True,
            secure=get_secure_cookie_setting(),
            samesite=DEFAULT_COOKIE_SAMESITE,
            path=DEFAULT_COOKIE_PATH,
        )

    if is_browser_cookie_mode(http_request):
        return TokenResponse(
            access_token=None,
            token_type=None,
            user=user_resp,
        )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=user_resp,
    )



@router.post(
    "/logout",
    summary="Log out the current user and invalidate the authentication token",
)
async def logout(
    request: Request = None,
    response: Response = None,
    credentials: HTTPAuthorizationCredentials | None = Security(security_bearer),
    revocation_store: TokenRevocationStore = Depends(get_revocation_store),
):
    """Clear the authentication cookie and record server-side token revocation.

    Invalidates presented cookie session and/or Bearer token persistently across
    all API and browser endpoints. Subsequent requests using the revoked token
    will be rejected with HTTP 401. Duplicate/repeated logouts succeed idempotently.
    """
    cookie_token = request.cookies.get(COOKIE_NAME) if request is not None else None
    bearer_token = credentials.credentials if (credentials and credentials.credentials) else None

    has_browser_indicators = False
    if request is not None:
        has_browser_indicators = bool(
            request.headers.get("origin")
            or request.headers.get("sec-fetch-site")
            or any(h in request.headers for h in CUSTOM_CSRF_HEADERS)
        )

    # Validate CSRF for cookie-authenticated browser requests. If a non-browser API client
    # passes an explicit Bearer token with an ambient cookie in its jar, honor Bearer without CSRF.
    if request is not None and cookie_token:
        if not bearer_token or has_browser_indicators:
            validate_csrf(request)

    secret = get_auth_secret()

    def _revoke_presented_token(tok: str) -> None:
        try:
            payload = jwt.decode(
                tok,
                secret,
                algorithms=[JWT_ALGORITHM],
                options={"verify_exp": False},
            )
        except Exception:
            payload = {}
        revocation_store.revoke_token(tok, payload)

    try:
        if bearer_token:
            _revoke_presented_token(bearer_token)
            if cookie_token and has_browser_indicators:
                _revoke_presented_token(cookie_token)
        elif cookie_token:
            _revoke_presented_token(cookie_token)
    except RevocationStoreError as exc:
        outage_response = JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Logout failed: revocation store unavailable."},
        )
        if cookie_token and (not bearer_token or has_browser_indicators):
            outage_response.delete_cookie(
                key=COOKIE_NAME,
                path=DEFAULT_COOKIE_PATH,
                httponly=True,
                secure=get_secure_cookie_setting(),
                samesite=DEFAULT_COOKIE_SAMESITE,
            )
        return outage_response

    if response is not None:
        response.delete_cookie(
            key=COOKIE_NAME,
            path=DEFAULT_COOKIE_PATH,
            httponly=True,
            secure=get_secure_cookie_setting(),
            samesite=DEFAULT_COOKIE_SAMESITE,
        )

    return {
        "success": True,
        "message": "Successfully logged out. Authentication cookie cleared.",
    }



@router.get(
    "/me",
    summary="Get profile of currently authenticated user",
)
async def me(current_user: UserResponse = Depends(get_current_user)):
    return {
        "success": True,
        "user": current_user.model_dump(mode="json"),
    }
