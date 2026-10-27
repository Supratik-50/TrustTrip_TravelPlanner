"""Tests for Travel Intelligence Migration Lifecycle and Zero-DDL Guarantees."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from services.search_session_store import (
    REQUIRED_SCHEMA_TABLES_BY_MIGRATION,
    REQUIRED_TRAVEL_INTELLIGENCE_TABLES,
    SearchSessionStore,
    validate_database_schema,
    validate_travel_intelligence_schema,
)


def test_startup_with_migrated_schema_succeeds(monkeypatch):
    """Verify that startup schema validation passes when all required tables exist."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    mock_cursor = MagicMock()
    # Mock database returning all required tables
    mock_cursor.fetchall.return_value = [
        ("search_sessions",),
        ("offer_snapshots",),
        ("revalidation_history",),
    ]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn) as mock_connect:
        validate_travel_intelligence_schema()

        assert mock_connect.called
        # Verify query was a pure SELECT on information_schema, zero DDL
        call_sql = mock_cursor.execute.call_args[0][0]
        assert "information_schema.tables" in call_sql
        assert "CREATE" not in call_sql.upper()
        assert "ALTER" not in call_sql.upper()
        assert "DROP" not in call_sql.upper()


def test_startup_with_missing_tables_fails_closed(monkeypatch):
    """Verify that startup schema validation fails closed if required tables are missing."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    mock_cursor = MagicMock()
    # Mock only search_sessions exists, offer_snapshots and revalidation_history are missing
    mock_cursor.fetchall.return_value = [("search_sessions",)]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        with pytest.raises(RuntimeError, match="Travel intelligence database tables missing"):
            validate_travel_intelligence_schema()


def test_store_operations_never_execute_ddl(monkeypatch):
    """Verify that SearchSessionStore instantiation and queries never execute DDL statements."""
    import base64
    import secrets

    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
    monkeypatch.setenv(
        "PAYLOAD_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode("utf-8")
    )

    executed_queries: list[str] = []

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn

    def capture_execute(sql, *args, **kwargs):
        executed_queries.append(str(sql).strip())
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_cursor.fetchall.return_value = []
        return mock_cursor

    mock_conn.execute.side_effect = capture_execute

    with patch("psycopg.connect", return_value=mock_conn):
        store = SearchSessionStore()

        # Execute store query
        store.get_active_session_by_hash(uuid4(), uuid4(), "hash_abc")

    # Verify no DDL keywords exist in any executed statement
    ddl_keywords = ["CREATE ", "ALTER ", "DROP ", "TRUNCATE "]
    for query in executed_queries:
        for kw in ddl_keywords:
            assert kw not in query.upper(), f"Unexpected DDL statement found in request path: {query}"


def test_migration_file_exists_and_contains_full_schema():
    """Verify 004_travel_intelligence.sql exists and contains all required DDL."""
    migration_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "migrations", "004_travel_intelligence.sql"
    )
    assert os.path.exists(migration_file), "004_travel_intelligence.sql must exist"

    with open(migration_file, "r", encoding="utf-8") as f:
        content = f.read()

    for table in REQUIRED_TRAVEL_INTELLIGENCE_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in content


def test_startup_schema_validation_checks_016_revoked_tokens_missing(monkeypatch):
    """Verify validate_database_schema fails closed when migration 016 revoked_tokens is missing."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    all_except_revoked = []
    for mig_file, tables in REQUIRED_SCHEMA_TABLES_BY_MIGRATION.items():
        if "016" not in mig_file:
            all_except_revoked.extend(tables)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [(t,) for t in all_except_revoked]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        with pytest.raises(RuntimeError) as exc_info:
            validate_database_schema()

        err_msg = str(exc_info.value)
        assert "revoked_tokens" in err_msg
        assert "016_jwt_revocation.sql" in err_msg


def test_startup_schema_validation_checks_016_revoked_tokens_present(monkeypatch):
    """Verify validate_database_schema passes when all tables including revoked_tokens exist."""
    monkeypatch.setenv("USE_IN_MEMORY_AUTH", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")

    all_tables = [table for tables in REQUIRED_SCHEMA_TABLES_BY_MIGRATION.values() for table in tables]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [(t,) for t in all_tables]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("psycopg.connect", return_value=mock_conn):
        validate_database_schema()


def test_migration_016_file_exists_and_contains_full_schema():
    """Verify 016_jwt_revocation.sql exists and contains required DDL for revoked_tokens."""
    migration_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "migrations", "016_jwt_revocation.sql"
    )
    assert os.path.exists(migration_file), "016_jwt_revocation.sql must exist"

    with open(migration_file, "r", encoding="utf-8") as f:
        content = f.read()

    assert "CREATE TABLE IF NOT EXISTS revoked_tokens" in content
    assert "idx_revoked_tokens_expires_at" in content
    assert "idx_revoked_tokens_token_hash" in content
