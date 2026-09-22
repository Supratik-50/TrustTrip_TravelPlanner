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
from datetime import datetime, timezone, timedelta
from typing import Any
import jwt

JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRE_HOURS = 24


def get_auth_secret() -> str:
    """Load the server-side authentication secret from environment."""
    secret = os.getenv("JWT_SECRET") or os.getenv("AUTH_SECRET") or os.getenv("AUTH_SECRET_KEY")
    if not secret:
        # Fallback to RAZORPAY_KEY_SECRET or a generated stable secret if not set
        secret = os.getenv("RAZORPAY_KEY_SECRET")
        if not secret:
            # Generate a process-stable secret for local development/testing
            secret = os.environ.setdefault("_DEV_AUTH_SECRET", secrets.token_hex(32))
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
) -> str:
    """Create a signed JWT access token for the authenticated user."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(hours=DEFAULT_TOKEN_EXPIRE_HOURS)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email.strip().lower(),
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
