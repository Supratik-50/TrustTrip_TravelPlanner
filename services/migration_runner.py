"""Deterministic Migration Runner & History Tracker for TrustTrip PostgreSQL Databases.

Discovers and executes SQL migrations in strict ascending numeric order:
  004_travel_intelligence.sql
  005_evidence_revision_lifecycle.sql
  006_research_and_rag.sql
  007_flight_booking_lifecycle.sql
  008_research_evidence_nullable_metadata.sql

Features:
1. Tracks executed migrations in a minimal 'schema_migrations' table.
2. Baselines existing databases without re-running DDL for already-created tables.
3. Fully idempotent: safe to execute multiple times.
4. Fails clearly on migration syntax/execution errors with rollback.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

MIGRATION_FILENAME_PATTERN = re.compile(r"^(\d{3})_.*\.sql$")

class IncompleteMigrationSchemaError(RuntimeError):
    """Raised when an existing database has only a partial or incomplete schema for a historical migration."""
    pass


# Complete schema verification contracts for historical un-tracked migrations.
# Every listed table and all of its required columns must be present in the schema for baselining.
HISTORICAL_MIGRATION_SCHEMA_CONTRACTS: dict[str, dict[str, set[str]]] = {
    "004_travel_intelligence.sql": {
        "search_sessions": {"id", "user_id", "trip_id", "search_type", "criteria_hash", "status", "created_at", "session_reuse_expires_at"},
        "offer_snapshots": {"id", "session_id", "provider", "provider_offer_id", "offer_type", "price_total", "currency", "normalized_offer", "raw_payload_encrypted"},
        "revalidation_history": {"id", "original_snapshot_id", "status", "verified_at", "original_price", "current_price", "currency"},
    },
    "005_evidence_revision_lifecycle.sql": {
        "plan_revisions": {"revision_id", "trip_id", "user_id", "revision_number", "change_set", "plan_snapshot", "is_published", "published_at", "created_at"},
        "evidence_records": {"evidence_id", "trip_id", "user_id", "evidence_type", "applicability_hash", "content_hash", "freshness_kind", "freshness_ttl_seconds", "created_at"},
        "evidence_provenance": {"evidence_id", "trip_id", "user_id", "created_at"},
        "plan_revision_evidence_links": {"link_id", "revision_id", "evidence_id", "trip_id", "user_id", "usage_state", "linked_at"},
        "plan_approvals": {"approval_id", "trip_id", "revision_id", "approved_by_user_id", "approved_at", "approval_metadata"},
    },
    "006_research_and_rag.sql": {
        "research_sessions": {"id", "user_id", "mode", "status", "created_at", "expires_at"},
        "research_queries": {"id", "session_id", "user_id", "query_text", "normalized_query", "intent", "mode"},
        "research_sources": {"id", "user_id", "url", "canonical_url", "title", "domain", "source_type", "trust_class", "content_hash"},
        "research_evidence": {"id", "query_id", "user_id", "source_id", "canonical_url", "title", "domain", "trust_class", "claims", "snippet", "immutable_hash"},
        "research_citations": {"id", "query_id", "citation_token", "evidence_id", "canonical_url", "domain"},
        "rag_documents": {"id", "user_id", "title", "filename", "content_type", "file_size_bytes", "content_hash"},
        "rag_chunks": {"id", "document_id", "user_id", "chunk_index", "text_content", "content_hash"},
    },
    "007_flight_booking_lifecycle.sql": {
        "booking_intents": {"id", "user_id", "trip_id", "revision_id", "selected_offer_snapshot_id", "provider", "status", "idempotency_key", "passenger_hash", "expected_amount", "currency", "expires_at"},
        "booking_attempts": {"attempt_id", "user_id", "idempotency_key", "action", "attempt_number", "status"},
        "booking_transitions": {"transition_id", "intent_id", "from_status", "to_status", "trigger", "actor_id"},
    },
}


def get_existing_schema_tables_and_columns(cur: psycopg.Cursor) -> dict[str, set[str]]:
    """Retrieve existing tables and their column names in the current schema."""
    cur.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema();
    """)
    table_columns: dict[str, set[str]] = {}
    for t_name, c_name in cur.fetchall():
        table_columns.setdefault(t_name, set()).add(c_name)
    return table_columns


def get_migrations_dir() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "migrations"


def discover_migrations(migrations_dir: Path | None = None) -> list[Path]:
    mdir = migrations_dir or get_migrations_dir()
    if not mdir.exists():
        raise FileNotFoundError(f"Migrations directory does not exist: {mdir}")
    files = [
        f for f in mdir.glob("*.sql")
        if MIGRATION_FILENAME_PATTERN.match(f.name)
    ]
    return sorted(files, key=lambda f: f.name)


def init_migration_tracking(cur: psycopg.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)


def get_applied_migrations(cur: psycopg.Cursor) -> set[str]:
    init_migration_tracking(cur)
    cur.execute("SELECT filename FROM schema_migrations;")
    return {row[0] for row in cur.fetchall()}


def run_migrations(
    migrations_dir: str | Path | None = None,
    conn: psycopg.Connection | None = None,
    schema: str | None = None,
) -> list[str]:
    """Execute all pending database migrations in strict numeric order.

    Returns the list of migration filenames applied during this invocation.
    """
    if conn is None and os.getenv("USE_IN_MEMORY_AUTH", "").lower() in {"1", "true", "yes"}:
        logger.info("USE_IN_MEMORY_AUTH=1; skipping database migrations.")
        return []

    mdir = Path(migrations_dir) if migrations_dir else get_migrations_dir()
    migration_files = discover_migrations(mdir)

    own_conn = False
    if conn is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is required to run migrations.")
        if "sslmode=" not in dsn:
            dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
        conn = psycopg.connect(dsn, autocommit=False)
        own_conn = True

    applied_in_this_run: list[str] = []
    try:
        with conn.cursor() as cur:
            if schema:
                cur.execute(f"SET search_path TO {schema}, public;")

            init_migration_tracking(cur)
            applied_set = get_applied_migrations(cur)
            schema_state = get_existing_schema_tables_and_columns(cur)

            for mfile in migration_files:
                fname = mfile.name

                if fname in applied_set:
                    logger.debug("Migration %s already applied; skipping.", fname)
                    continue

                # Baseline existing historical un-tracked migrations with complete schema verification
                contract = HISTORICAL_MIGRATION_SCHEMA_CONTRACTS.get(fname)
                if contract:
                    required_tables = set(contract.keys())
                    existing_req_tables = required_tables.intersection(schema_state.keys())

                    if len(existing_req_tables) == 0:
                        # None of the tables exist: brand new schema, execute migration normally
                        pass
                    elif existing_req_tables == required_tables:
                        # All tables exist: verify all required columns
                        missing_cols: dict[str, set[str]] = {}
                        for t_name, req_cols in contract.items():
                            actual_cols = schema_state.get(t_name, set())
                            missing = req_cols - actual_cols
                            if missing:
                                missing_cols[t_name] = missing

                        if not missing_cols:
                            # Schema is 100% complete: baseline safely
                            logger.info(
                                "Verified complete existing schema for %s; baselining without re-executing.",
                                fname,
                            )
                            cur.execute(
                                "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT (filename) DO NOTHING;",
                                (fname,),
                            )
                            applied_set.add(fname)
                            continue
                        else:
                            # Incomplete columns: fail-closed, refuse to baseline
                            conn.rollback()
                            err_msg = (
                                f"Migration {fname} cannot be baselined: required columns missing in existing schema: {missing_cols}. "
                                "Refusing to insert schema_migrations success row for incomplete migration."
                            )
                            logger.error(err_msg)
                            raise IncompleteMigrationSchemaError(err_msg)
                    else:
                        # Incomplete tables: fail-closed, refuse to baseline
                        missing_tables = required_tables - existing_req_tables
                        conn.rollback()
                        err_msg = (
                            f"Migration {fname} cannot be baselined: partial schema detected. "
                            f"Found tables: {existing_req_tables}, missing tables: {missing_tables}. "
                            "Refusing to insert schema_migrations success row for incomplete migration."
                        )
                        logger.error(err_msg)
                        raise IncompleteMigrationSchemaError(err_msg)

                # Execute migration SQL
                logger.info("Executing migration: %s", fname)
                sql_content = mfile.read_text(encoding="utf-8")
                try:
                    cur.execute(sql_content)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT (filename) DO NOTHING;",
                        (fname,),
                    )
                    applied_set.add(fname)
                    applied_in_this_run.append(fname)
                    schema_state = get_existing_schema_tables_and_columns(cur)
                    logger.info("Successfully applied migration: %s", fname)
                except Exception as exc:
                    conn.rollback()
                    logger.error("Migration %s failed: %s", fname, exc)
                    raise RuntimeError(f"Migration {fname} failed: {exc}") from exc

            conn.commit()
    finally:
        if own_conn:
            conn.close()

    return applied_in_this_run


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    applied = run_migrations()
    print(f"Migrations run completed. Applied {len(applied)} migrations: {applied}")
