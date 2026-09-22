"""Database persistence for User authentication and ownership.

Manages the 'users' table in PostgreSQL. Schema initialization is executed once,
never on every HTTP request.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
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
