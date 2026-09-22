"""PostgreSQL transaction store for Trust Trip MAS payment/booking state."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _dsn() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is missing.")
    if "sslmode=" not in value:
        value += ("&" if "?" in value else "?") + "sslmode=require"
    return value


class TransactionStore:
    def __init__(self) -> None:
        self.dsn = _dsn()
        self.ensure_schema()

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trust_trip_transactions (
                    id UUID PRIMARY KEY,
                    customer_ref TEXT,
                    offer_id TEXT NOT NULL,
                    duffel_order_id TEXT,
                    booking_reference TEXT,
                    razorpay_order_id TEXT UNIQUE,
                    razorpay_payment_id TEXT UNIQUE,
                    amount NUMERIC NOT NULL,
                    currency TEXT DEFAULT 'INR',
                    provider_amount NUMERIC,
                    provider_currency TEXT,
                    fx_rate NUMERIC,
                    fx_rate_date TEXT,
                    fx_source TEXT,
                    payment_status TEXT DEFAULT 'pending',
                    booking_status TEXT DEFAULT 'pending',
                    refund_status TEXT DEFAULT 'none',
                    passenger_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            # Safe migrations for databases created by earlier project versions.
            migrations = [
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS customer_ref TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS offer_id TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS razorpay_order_id TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS razorpay_payment_id TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS amount NUMERIC",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'INR'",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS provider_amount NUMERIC",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS provider_currency TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS fx_rate NUMERIC",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS fx_rate_date TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS fx_source TEXT",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS payment_status TEXT DEFAULT 'pending'",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS booking_status TEXT DEFAULT 'pending'",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS refund_status TEXT DEFAULT 'none'",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS passenger_json JSONB NOT NULL DEFAULT '[]'::jsonb",
                "ALTER TABLE trust_trip_transactions ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
            ]
            for sql in migrations:
                conn.execute(sql)

    def create(self, **data: Any) -> str:
        tx_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO trust_trip_transactions
                (id, customer_ref, offer_id, duffel_order_id, booking_reference,
                 razorpay_order_id, razorpay_payment_id, amount, currency,
                 provider_amount, provider_currency, fx_rate, fx_rate_date, fx_source,
                 payment_status, booking_status, passenger_json, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
            """, (
                tx_id,
                data.get("customer_ref"),
                data.get("offer_id"),
                data.get("duffel_order_id"),
                data.get("booking_reference"),
                data.get("razorpay_order_id"),
                data.get("razorpay_payment_id"),
                data.get("amount"),
                data.get("currency", "INR"),
                data.get("provider_amount"),
                data.get("provider_currency"),
                data.get("fx_rate"),
                data.get("fx_rate_date"),
                data.get("fx_source"),
                data.get("payment_status", "pending"),
                data.get("booking_status", "pending"),
                json.dumps(data.get("passengers", [])),
                json.dumps(data.get("metadata", {})),
            ))
        return tx_id

    def update(self, tx_id: str, **fields: Any) -> None:
        allowed = {
            "customer_ref", "offer_id", "duffel_order_id", "booking_reference",
            "razorpay_order_id", "razorpay_payment_id", "amount", "currency",
            "provider_amount", "provider_currency", "fx_rate", "fx_rate_date", "fx_source",
            "payment_status", "booking_status", "refund_status", "passenger_json",
            "metadata",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        encoded = {}
        for key, value in fields.items():
            if key in {"passenger_json", "metadata"}:
                encoded[key] = json.dumps(value)
            else:
                encoded[key] = value
        sets = ", ".join(f"{k}=%s" for k in encoded)
        values = list(encoded.values()) + [tx_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE trust_trip_transactions SET {sets}, updated_at=now() WHERE id=%s", values)

    def claim_booking(self, tx_id: str) -> bool:
        """Atomically claim one paid transaction for supplier booking."""
        with self._connect() as conn:
            row = conn.execute("""
                UPDATE trust_trip_transactions
                   SET booking_status='booking_pending', updated_at=now()
                 WHERE id=%s
                   AND payment_status='captured'
                   AND booking_status IN ('awaiting_payment', 'payment_captured')
                RETURNING id
            """, (tx_id,)).fetchone()
            return bool(row)

    def list_recent(self, limit: int = 20, customer_ref: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if customer_ref:
                return conn.execute(
                    "SELECT * FROM trust_trip_transactions WHERE customer_ref=%s ORDER BY created_at DESC LIMIT %s",
                    (customer_ref, limit),
                ).fetchall()
            return conn.execute(
                "SELECT * FROM trust_trip_transactions ORDER BY created_at DESC LIMIT %s", (limit,)
            ).fetchall()

    def update_metadata(self, tx_id: str, **updates: Any) -> None:
        """Merge non-secret booking metadata without replacing existing metadata."""
        with self._connect() as conn:
            row = conn.execute("SELECT metadata FROM trust_trip_transactions WHERE id=%s", (tx_id,)).fetchone()
            if not row:
                return
            current = row.get("metadata") or {}
            if isinstance(current, str):
                try:
                    current = json.loads(current)
                except json.JSONDecodeError:
                    current = {}
            current.update(updates)
            conn.execute(
                "UPDATE trust_trip_transactions SET metadata=%s::jsonb, updated_at=now() WHERE id=%s",
                (json.dumps(current), tx_id),
            )

    def delete(self, tx_id: str, customer_ref: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "DELETE FROM trust_trip_transactions WHERE id=%s AND customer_ref=%s RETURNING id",
                (tx_id, customer_ref),
            ).fetchone()
            return bool(row)

    def get(self, tx_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM trust_trip_transactions WHERE id=%s", (tx_id,)).fetchone()

    def get_by_razorpay_order(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM trust_trip_transactions WHERE razorpay_order_id=%s", (order_id,)).fetchone()
