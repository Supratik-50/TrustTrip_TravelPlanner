"""Tests for Travel Intelligence Migration Lifecycle and Zero-DDL Guarantees."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from services.search_session_store import (
    REQUIRED_TRAVEL_INTELLIGENCE_TABLES,
    SearchSessionStore,
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
