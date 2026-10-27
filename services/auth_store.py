"""Database persistence for User authentication and ownership.

Manages the 'users' table in PostgreSQL. Schema initialization is executed once,
never on every HTTP request.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
import psycopg
from psycopg.rows import dict_row

_SCHEMA_INITIALIZED = False


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for user persistence.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


def init_auth_tables() -> None:
    """Initialize authentication and ownership tables once at startup."""
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return

    if os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}:
        _SCHEMA_INITIALIZED = True
        return

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return

    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti TEXT PRIMARY KEY,
                user_id TEXT NULL,
                token_hash TEXT NULL,
                revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires_at ON revoked_tokens (expires_at);
            CREATE INDEX IF NOT EXISTS idx_revoked_tokens_token_hash ON revoked_tokens (token_hash);
        """)
    _SCHEMA_INITIALIZED = True


class UserStore:
    """Repository for User accounts."""
    _IN_MEMORY_USERS: dict[str, dict[str, Any]] = {}

    def __init__(self) -> None:
        self.use_in_memory = bool(os.getenv("USE_IN_MEMORY_AUTH"))
        self._memory_users = UserStore._IN_MEMORY_USERS
        if not self.use_in_memory and os.getenv("DATABASE_URL"):
            init_auth_tables()

    def _connect(self):
        return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)

    def create_user(self, email: str, password_hash: str) -> dict[str, Any]:
        """Create a new user. Raises ValueError if email already exists."""
        email_clean = email.strip().lower()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            for u in self._memory_users.values():
                if u["email"] == email_clean:
                    raise ValueError(f"User with email '{email_clean}' already exists.")
            record = {
                "id": user_id,
                "email": email_clean,
                "password_hash": password_hash,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            self._memory_users[str(user_id)] = record
            return record

        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, true, %s, %s)
                    RETURNING id, email, password_hash, is_active, created_at, updated_at
                    """,
                    (user_id, email_clean, password_hash, now, now),
                ).fetchone()
                return dict(row)
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"User with email '{email_clean}' already exists.") from exc

    def get_by_email(self, email: str) -> dict[str, Any] | None:
        """Fetch a user record by email."""
        email_clean = email.strip().lower()
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            for u in self._memory_users.values():
                if u["email"] == email_clean:
                    return dict(u)
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = %s",
                (email_clean,),
            ).fetchone()
            return dict(row) if row else None

    def get_by_id(self, user_id: str | uuid.UUID) -> dict[str, Any] | None:
        """Fetch a user record by user ID."""
        uid_str = str(user_id)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            u = self._memory_users.get(uid_str)
            return dict(u) if u else None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = %s",
                (uid_str,),
            ).fetchone()
            return dict(row) if row else None


class RevocationStoreError(RuntimeError):
    """Raised when token revocation status cannot be verified due to store failure."""
    pass


class TokenRevocationStore:
    """Repository for server-side revoked JWT access tokens.

    Supports:
    - Unique jti claim tracking for modern tokens.
    - Token SHA-256 hash tracking for legacy tokens without jti.
    - In-memory fallback when USE_IN_MEMORY_AUTH is active.
    - PostgreSQL persistence with safe concurrent access (ON CONFLICT DO NOTHING).
    - Expiration-aware records cleanup.
    """
    _IN_MEMORY_REVOKED: dict[str, dict[str, Any]] = {}

    def __init__(self) -> None:
        self.use_in_memory = bool(os.getenv("USE_IN_MEMORY_AUTH"))
        self._memory_revoked = TokenRevocationStore._IN_MEMORY_REVOKED
        if not self.use_in_memory and os.getenv("DATABASE_URL"):
            init_auth_tables()

    def _connect(self):
        return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)

    def revoke_token(
        self,
        token: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Revoke an access token.

        Uses jti if present; falls back to SHA-256 hash of token for legacy tokens.
        """
        now = datetime.now(timezone.utc)
        payload = payload or {}
        jti = payload.get("jti")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        key = jti if jti else f"legacy:{token_hash}"
        user_id = str(payload.get("sub")) if payload.get("sub") else None

        exp_ts = payload.get("exp")
        if exp_ts:
            expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
        else:
            expires_at = now + timedelta(hours=24)

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            self._memory_revoked[key] = {
                "jti": key,
                "user_id": user_id,
                "token_hash": token_hash,
                "revoked_at": now,
                "expires_at": expires_at,
            }
            return True

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO revoked_tokens (jti, user_id, token_hash, revoked_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (jti) DO NOTHING;
                    """,
                    (key, user_id, token_hash, now, expires_at),
                )
                return True
        except Exception as exc:
            raise RevocationStoreError(f"Failed to record token revocation: {exc}") from exc

    def is_token_revoked(
        self,
        token: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Check whether a token is revoked.

        Fails closed by raising RevocationStoreError on database failure.
        """
        now = datetime.now(timezone.utc)
        payload = payload or {}
        jti = payload.get("jti")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        key = jti if jti else f"legacy:{token_hash}"

        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            record = self._memory_revoked.get(key)
            if record:
                return record["expires_at"] > now
            for r in self._memory_revoked.values():
                if r.get("token_hash") == token_hash and r["expires_at"] > now:
                    return True
            return False

        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT 1 FROM revoked_tokens
                    WHERE (jti = %s OR token_hash = %s)
                      AND expires_at > %s
                    LIMIT 1;
                    """,
                    (key, token_hash, now),
                ).fetchone()
                return bool(row)
        except Exception as exc:
            raise RevocationStoreError(f"Failed to check token revocation: {exc}") from exc

    def cleanup_expired_revocations(self) -> int:
        """Purge revocation records whose expiration has passed."""
        now = datetime.now(timezone.utc)
        if self.use_in_memory or not os.getenv("DATABASE_URL"):
            expired_keys = [
                k for k, v in self._memory_revoked.items()
                if v["expires_at"] <= now
            ]
            for k in expired_keys:
                del self._memory_revoked[k]
            return len(expired_keys)

        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM revoked_tokens WHERE expires_at <= %s RETURNING jti;",
                    (now,),
                )
                deleted = cur.fetchall()
                return len(deleted)
        except Exception as exc:
            raise RevocationStoreError(f"Failed to clean up expired revocations: {exc}") from exc

