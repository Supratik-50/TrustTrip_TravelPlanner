"""Authentication service for Trust Trip MAS.

Provides secure password hashing (scrypt with salt) and signed JWT tokens.
Plaintext passwords are never stored. Secrets are loaded from environment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
import jwt

JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRE_HOURS = 24


MIN_JWT_SECRET_BYTES = 32


def get_auth_secret() -> str:
    """Load the server-side authentication secret from environment.

    Enforces a dedicated JWT secret with at least 32 bytes (256 bits) for HS256.
    Never falls back to payment gateway credentials (e.g. RAZORPAY_KEY_SECRET).
    In production (APP_ENV=production), fails fast if missing or weak.
    In development/testing, generates a 32-byte process-stable secret if unset.
    """
    secret = os.getenv("JWT_SECRET") or os.getenv("AUTH_SECRET") or os.getenv("AUTH_SECRET_KEY")
    if not secret:
        if os.getenv("APP_ENV", "development").lower() == "production":
            raise RuntimeError(
                "JWT_SECRET is required in production environment (APP_ENV=production)."
            )
        # Generate a process-stable secret for local development/testing (32 bytes = 64 hex chars)
        secret = os.environ.setdefault("_DEV_AUTH_SECRET", secrets.token_hex(32))

    if len(secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
        raise ValueError(
            f"JWT secret must be at least {MIN_JWT_SECRET_BYTES} bytes long for {JWT_ALGORITHM}."
        )
    return secret



def hash_password(password: str) -> str:
    """Hash a password securely using scrypt with a unique random 16-byte salt.
    
    Format: scrypt$16384$8$1$<salt_hex>$<hash_hex>
    """
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters long.")
    salt = secrets.token_bytes(16)
    n, r, p = 16384, 8, 1
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=0x2000000,
    )
    salt_hex = salt.hex()
    hash_hex = key.hex()
    return f"scrypt${n}${r}${p}${salt_hex}${hash_hex}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against the stored scrypt hash string."""
    if not password or not password_hash:
        return False
    parts = password_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected_hash = bytes.fromhex(parts[5])
        computed = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=0x2000000,
        )
        return hmac.compare_digest(computed, expected_hash)
    except Exception:
        return False


def create_access_token(
    user_id: str,
    email: str,
    expires_delta: timedelta | None = None,
    jti: str | None = None,
) -> str:
    """Create a signed JWT access token for the authenticated user."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=DEFAULT_TOKEN_EXPIRE_HOURS)
    )
    token_jti = jti or str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email.strip().lower(),
        "jti": token_jti,
        "exp": int(expire.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    secret = get_auth_secret()
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token.
    
    Raises jwt.PyJWTError on invalid or expired token.
    """
    secret = get_auth_secret()
    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
