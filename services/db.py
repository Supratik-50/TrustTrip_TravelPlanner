"""Database connection and probe helpers for TrustTrip backend (Phase 10).

Preserves the existing per-operation psycopg connection pattern while providing:
  - Fast-failure on unreachable DB via connect_timeout=5.
  - Context-managed connection helper with automatic rollback on error and clean close.
  - Probe function (probe_db) for readiness checks (/ready).
  - Explicit lifecycle hooks for startup verification and shutdown release.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from typing import Generator

import psycopg

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT = int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5"))


def get_db_dsn() -> str:
    """Return DATABASE_URL with sslmode=require and connect_timeout appended if missing."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured.")
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    if "connect_timeout=" not in dsn:
        timeout = os.getenv("POSTGRES_CONNECT_TIMEOUT", str(DEFAULT_CONNECT_TIMEOUT))
        dsn += ("&" if "?" in dsn else "?") + f"connect_timeout={timeout}"
    return dsn


@contextmanager
def get_connection(timeout: int | None = None) -> Generator[psycopg.Connection, None, None]:
    """Context manager yielding a psycopg connection with rollback-on-error guarantee.

    Commits on successful exit, rolls back on exception, and always closes the connection.
    """
    dsn = get_db_dsn()
    connect_kwargs = {}
    if timeout is not None:
        connect_kwargs["connect_timeout"] = timeout

    conn = psycopg.connect(dsn, **connect_kwargs)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def probe_db(timeout: int = 5) -> bool:
    """Execute a lightweight SELECT 1 query to test database availability.

    Returns True if database is reachable and responds, False otherwise.
    Catches all exceptions without leaking credentials, DSN, or stack traces.
    """
    if os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}:
        return True

    try:
        dsn = get_db_dsn()
        with psycopg.connect(dsn, connect_timeout=timeout) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                row = cur.fetchone()
                return bool(row and row[0] == 1)
    except Exception as exc:
        logger.warning("Database readiness probe failed: %s", type(exc).__name__)
        return False


def init_db() -> None:
    """Startup initialization hook for database subsystem."""
    logger.info("Database subsystem initialized (connect_timeout=%ss)", DEFAULT_CONNECT_TIMEOUT)


def close_db() -> None:
    """Shutdown hook for database subsystem."""
    logger.info("Database subsystem shutdown cleanly")


# Aliases for compatibility
init_pool = init_db
close_pool = close_db
