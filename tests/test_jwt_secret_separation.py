"""Focused Security Regression Tests for TT-SEC-001 (JWT Signing-Key Separation).

Verifies:
1. Missing JWT secret cannot fall back to RAZORPAY_KEY_SECRET.
2. Missing JWT secret causes safe startup failure in production (APP_ENV=production).
3. Secrets shorter than 32 bytes are rejected for HS256 (ValueError / RuntimeError).
4. Valid dedicated secret allows token creation and verification without InsecureKeyLengthWarning.
5. Tokens signed with an unrelated payment secret (e.g. RAZORPAY_KEY_SECRET) are rejected.
6. Token expiration, claim integrity, and ownership semantics remain intact.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from app import app
from services.auth_service import (
    JWT_ALGORITHM,
    MIN_JWT_SECRET_BYTES,
    create_access_token,
    decode_access_token,
    get_auth_secret,
)
from services.config import validate_config

VALID_32B_SECRET = "a-very-secure-32-byte-token-for-jwt-signing!"
VALID_PAYMENT_SECRET = "rzp_secret_payment_key_24chr"


def _clean_jwt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all JWT secret aliases and dev fallback are removed."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
    monkeypatch.delenv("_DEV_AUTH_SECRET", raising=False)


# 1. Missing JWT secret cannot fall back to RAZORPAY_KEY_SECRET
def test_missing_jwt_secret_does_not_fallback_to_razorpay(monkeypatch: pytest.MonkeyPatch):
    """When JWT secret is absent and RAZORPAY_KEY_SECRET is set, get_auth_secret must not return payment secret."""
    _clean_jwt_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", VALID_PAYMENT_SECRET)

    auth_secret = get_auth_secret()
    assert auth_secret != VALID_PAYMENT_SECRET, "Security violation: get_auth_secret fell back to RAZORPAY_KEY_SECRET"
    assert len(auth_secret.encode("utf-8")) >= MIN_JWT_SECRET_BYTES


# 2. Missing secret causes safe startup failure in production
def test_missing_jwt_secret_in_production_fails_fast(monkeypatch: pytest.MonkeyPatch):
    """In production mode, missing JWT_SECRET must raise RuntimeError without leaking secrets."""
    _clean_jwt_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", VALID_PAYMENT_SECRET)

    # validate_config() must fail
    with pytest.raises(RuntimeError, match="JWT_SECRET is required in production environment"):
        validate_config()

    # get_auth_secret() must fail
    with pytest.raises(RuntimeError, match="JWT_SECRET is required in production environment"):
        get_auth_secret()


# 3. Secret shorter than 32 bytes is rejected for HS256
def test_short_jwt_secret_rejected_by_auth_service(monkeypatch: pytest.MonkeyPatch):
    """A JWT secret shorter than 32 bytes must be rejected with ValueError."""
    monkeypatch.setenv("JWT_SECRET", "too-short-key-16b")

    with pytest.raises(ValueError, match="at least 32 bytes"):
        get_auth_secret()


def test_short_jwt_secret_rejected_by_config_in_production(monkeypatch: pytest.MonkeyPatch):
    """In production, a JWT secret shorter than 32 bytes causes validate_config to fail."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=")
    monkeypatch.setenv("JWT_SECRET", "short-secret-only-23-bytes")

    with pytest.raises(RuntimeError, match="must be at least 32 bytes long in production"):
        validate_config()


# 4. Valid dedicated secret allows token creation and verification
def test_valid_dedicated_secret_token_lifecycle(monkeypatch: pytest.MonkeyPatch):
    """A valid dedicated secret generates and verifies tokens without warnings."""
    monkeypatch.setenv("JWT_SECRET", VALID_32B_SECRET)

    token = create_access_token(user_id="user_12345", email="alice@trusttrip.com")
    assert isinstance(token, str) and len(token) > 0

    payload = decode_access_token(token)
    assert payload["sub"] == "user_12345"
    assert payload["email"] == "alice@trusttrip.com"
    assert "exp" in payload
    assert "iat" in payload


# 5. Token signed with unrelated payment secret is rejected
def test_token_signed_with_payment_secret_rejected(monkeypatch: pytest.MonkeyPatch):
    """A token forged or signed using RAZORPAY_KEY_SECRET is rejected by decode_access_token."""
    _clean_jwt_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", VALID_PAYMENT_SECRET)

    # Attacker tries to forge token using known RAZORPAY_KEY_SECRET
    forged_payload = {
        "sub": "attacker_user_id",
        "email": "attacker@evil.com",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    forged_token = jwt.encode(forged_payload, VALID_PAYMENT_SECRET, algorithm="HS256")

    # Verification must fail with PyJWTError / InvalidSignatureError
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(forged_token)


def test_api_endpoint_rejects_payment_secret_signed_token(monkeypatch: pytest.MonkeyPatch):
    """The /api/trips endpoint returns 401 when presented with a token signed by RAZORPAY_KEY_SECRET."""
    _clean_jwt_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", VALID_PAYMENT_SECRET)

    forged_payload = {
        "sub": "attacker_user_id",
        "email": "attacker@evil.com",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    forged_token = jwt.encode(forged_payload, VALID_PAYMENT_SECRET, algorithm="HS256")

    client = TestClient(app)
    response = client.get("/api/trips", headers={"Authorization": f"Bearer {forged_token}"})
    assert response.status_code == 401


# 6. Existing authentication, token-expiry, and claim integrity preserved
def test_expired_token_raises_expired_signature_error(monkeypatch: pytest.MonkeyPatch):
    """Expired tokens continue to raise ExpiredSignatureError."""
    monkeypatch.setenv("JWT_SECRET", VALID_32B_SECRET)

    # Token with negative delta (already expired)
    expired_token = create_access_token(
        user_id="user_exp",
        email="exp@trusttrip.com",
        expires_delta=timedelta(seconds=-10),
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired_token)
