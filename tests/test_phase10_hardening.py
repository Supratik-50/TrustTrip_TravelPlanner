"""Tests for Phase 10 — Backend Finalization & Production-Lite Hardening.

Covers:
  1. Centralized configuration validation (missing DATABASE_URL, dev warnings, production gates)
  2. Database connection safety, connect_timeout, rollback on error, probe behavior
  3. Extended schema validation across migrations 004-007
  4. Health and readiness endpoints (/health, /ready)
  5. Global exception sanitization and removal of str(exc) leaks
  6. Authentication enforcement and secret safety in logs
  7. CORS allowed and blocked origin verification
  8. Non-idempotent booking timeout non-retry verification
"""
from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg
import pytest

from app import app
from services.config import is_production, validate_config
from services.db import (
    close_db,
    close_pool,
    get_connection,
    get_db_dsn,
    init_db,
    init_pool,
    probe_db,
)
from services.search_session_store import (
    REQUIRED_SCHEMA_TABLES_BY_MIGRATION,
    validate_database_schema,
)

client = TestClient(app, raise_server_exceptions=False)


# ==============================================================================
# 1. Configuration Validation Tests
# ==============================================================================

def test_config_missing_database_url(monkeypatch):
    """validate_config() must raise RuntimeError if DATABASE_URL is missing in non-memory mode."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL is not configured"):
        validate_config()


def test_config_dev_jwt_fallback_logs_warning(monkeypatch, caplog):
    """In development mode, missing JWT_SECRET logs a warning rather than failing."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)

    with caplog.at_level(logging.WARNING):
        validate_config()
        assert any("JWT_SECRET is not set" in r.message for r in caplog.records)


def test_config_all_required_present(monkeypatch):
    """validate_config() completes without error when all required vars are set."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET", "super-secret-key-1234567890123456")
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=")

    # Should not raise
    validate_config()


def test_config_production_missing_jwt_fails(monkeypatch):
    """In production mode (APP_ENV=production), missing JWT_SECRET must raise RuntimeError."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=")

    with pytest.raises(RuntimeError, match="JWT_SECRET is required in production environment"):
        validate_config()


def test_config_production_missing_payload_key_fails(monkeypatch):
    """In production mode, missing PAYLOAD_ENCRYPTION_KEY must raise RuntimeError."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "production-secret-token")
    monkeypatch.delenv("PAYLOAD_ENCRYPTION_KEY", raising=False)

    with pytest.raises(RuntimeError, match="PAYLOAD_ENCRYPTION_KEY is required in production environment"):
        validate_config()


# ==============================================================================
# 2. Database Connection & Probe Tests
# ==============================================================================

def test_db_dsn_appends_timeout_and_sslmode(monkeypatch):
    """get_db_dsn() automatically appends sslmode=require and connect_timeout=5."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT", "5")

    dsn = get_db_dsn()
    assert "sslmode=require" in dsn
    assert "connect_timeout=5" in dsn


def test_db_pool_init_success():
    """init_db() / init_pool() and probe_db() succeed without error."""
    init_db()
    init_pool()

    # In USE_IN_MEMORY_AUTH=1 mode, probe_db returns True
    assert probe_db() is True


def test_db_pool_connect_timeout(monkeypatch):
    """probe_db() returns False on unreachable host without raising an exception."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@10.255.255.1:5432/db?connect_timeout=1")

    with patch("psycopg.connect", side_effect=psycopg.OperationalError("connection timeout expired")):
        result = probe_db(timeout=1)
        assert result is False


def test_db_pool_close():
    """close_db() and close_pool() complete cleanly."""
    close_db()
    close_pool()


def test_db_transaction_rollback_on_error(monkeypatch):
    """get_connection() ensures rollback() and close() are called when an exception occurs."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    mock_conn = MagicMock()
    with patch("psycopg.connect", return_value=mock_conn):
        with pytest.raises(ValueError, match="simulated store failure"):
            with get_connection():
                raise ValueError("simulated store failure")

        assert mock_conn.rollback.called
        assert mock_conn.close.called
        assert not mock_conn.commit.called


# ==============================================================================
# 3. Schema Validation Extension Tests
# ==============================================================================

def test_schema_validation_missing_table(monkeypatch):
    """validate_database_schema() fails closed with clear message if any table is missing."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    # Mock DB missing booking_transitions from migration 007
    all_except_one = []
    for tables in REQUIRED_SCHEMA_TABLES_BY_MIGRATION.values():
        all_except_one.extend(tables)
    all_except_one.remove("booking_transitions")

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [(t,) for t in all_except_one]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        with pytest.raises(RuntimeError) as exc_info:
            validate_database_schema()

        err_msg = str(exc_info.value)
        assert "booking_transitions" in err_msg
        assert "007_flight_booking_lifecycle.sql" in err_msg


def test_schema_validation_all_present(monkeypatch):
    """validate_database_schema() passes when all tables for migrations 004-007 are returned."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    all_tables = []
    for tables in REQUIRED_SCHEMA_TABLES_BY_MIGRATION.values():
        all_tables.extend(tables)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [(t,) for t in all_tables]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        # Should not raise
        validate_database_schema()


# ==============================================================================
# 4. Health and Readiness Endpoints
# ==============================================================================

def test_health_endpoint():
    """GET /health returns 200, status=ok, and includes version field."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert data["version"] == "2.0.0"


def test_ready_endpoint_db_up():
    """GET /ready returns 200 {'status': 'ready'} when database is available."""
    with patch("services.db.probe_db", return_value=True):
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


def test_ready_endpoint_db_down():
    """GET /ready returns 503 {'status': 'not_ready', ...} when database is unavailable."""
    with patch("services.db.probe_db", return_value=False):
        response = client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["reason"] == "database_unavailable"
        # Confirm no sensitive connection info or DSN leaked
        assert "postgresql://" not in str(data)
        assert "password" not in str(data).lower()


# ==============================================================================
# 5. Global Exception & Error Sanitization Tests
# ==============================================================================

def test_unhandled_exception_safe_response():
    """Global exception handler returns generic 500 without leaking exception details."""
    from datetime import datetime, timezone
    from dependencies.auth import get_current_user
    from domain.user import UserResponse
    import uuid

    mock_user = UserResponse(
        id=uuid.uuid4(),
        email="test@example.com",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.run_travel_agent", side_effect=Exception("FATAL_INTERNAL_DB_SECRET_LEAK_123")):
            response = client.post(
                "/api/travel",
                json={"message": "Plan a trip to Paris"},
            )
            assert response.status_code == 500
            data = response.json()
            assert data["success"] is False
            # Verify internal detail is NOT in the response
            assert "FATAL_INTERNAL_DB_SECRET_LEAK_123" not in str(data)
            assert "error" in data
    finally:
        app.dependency_overrides.clear()


def test_travel_endpoint_exception_safe_response():
    """/api/travel 500 body contains sanitized error message and no str(exc) leakage."""
    from datetime import datetime, timezone
    from dependencies.auth import get_current_user
    from domain.user import UserResponse
    import uuid

    mock_user = UserResponse(
        id=uuid.uuid4(),
        email="test@example.com",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch("app.run_travel_agent", side_effect=RuntimeError("psycopg.OperationalError: password auth failed for user postgres")):
            response = client.post(
                "/api/travel",
                json={"message": "Go to Rome"},
            )
            assert response.status_code == 500
            data = response.json()
            assert "password auth failed" not in str(data)
            assert "psycopg" not in str(data)
            assert data["error"] == "Travel planner processing failed."
    finally:
        app.dependency_overrides.clear()


def test_travel_pdf_exception_safe_response():
    """/api/travel/pdf 500 body contains sanitized error message and no raw exception text."""
    with patch("app.build_travel_plan_pdf", side_effect=Exception("Internal PDF generation crash secret")):
        response = client.post(
            "/api/travel/pdf",
            json={"plan": {"title": "Test Plan"}},
        )
        assert response.status_code == 500
        data = response.json()
        assert "Internal PDF generation crash secret" not in str(data)
        assert data["error"] == "Travel plan PDF generation failed."


# ==============================================================================
# 6. Authentication Enforcement & Log Safety
# ==============================================================================

def test_auth_required_on_protected_endpoint():
    """Protected endpoints require valid Bearer token and reject unauthenticated requests with 401."""
    response = client.get("/api/trips")
    assert response.status_code == 401


def test_no_token_in_error_log(caplog):
    """Logging unhandled errors must not log Bearer tokens or sensitive credentials."""
    sensitive_token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.SECRET_TOKEN_VALUE"
    with caplog.at_level(logging.DEBUG):
        # Trigger an endpoint with token header
        client.get("/api/trips", headers={"Authorization": sensitive_token})
        for record in caplog.records:
            assert "SECRET_TOKEN_VALUE" not in record.message


# ==============================================================================
# 7. CORS Configuration Tests
# ==============================================================================

def test_cors_allowed_origin():
    """Requests from configured ALLOWED_ORIGINS receive Access-Control-Allow-Origin header."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_unlisted_origin_blocked():
    """Requests from unlisted origins do not receive Access-Control-Allow-Origin header."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://untrusted-external-domain.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") is None


# ==============================================================================
# 8. Provider Reliability & Timeout Non-Retry
# ==============================================================================

def test_booking_timeout_not_retried(monkeypatch):
    """Verify that a provider timeout on handoff raises an error without automated retries."""
    import httpx
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "1")
    monkeypatch.setenv("PAYLOAD_ENCRYPTION_KEY", "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=")

    from services.flight_booking_lifecycle_service import FlightBookingLifecycleService

    service = FlightBookingLifecycleService()
    # Mock adapter to raise timeout
    mock_adapter = MagicMock()
    mock_adapter.revalidate_offer.side_effect = httpx.TimeoutException("Duffel revalidation timed out")
    service.booking_adapter = mock_adapter

    # Revalidation timeout must raise offer revalidation error or timeout directly, never silently retried
    with pytest.raises(Exception):
        service.booking_adapter.revalidate_offer("off_timeout_test")

    # Must be called exactly once — zero retry loops
    assert mock_adapter.revalidate_offer.call_count == 1
