-- Migration: 011_payment_transaction_lineage.sql
-- Description: ADR-010 Section 10 payment transaction lineage.
--   Extends trust_trip_transactions and trust_trip_hotel_transactions with:
--   - trip_id / revision_id / revision_number  (exact revision binding)
--   - idempotency_key                          (payment-order idempotency)
-- This migration is idempotent and transaction-safe.

BEGIN;

-- ====================================================================
-- PART A: FLIGHT PAYMENT TRANSACTIONS — REVISION LINEAGE + IDEMPOTENCY
-- ====================================================================

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE trust_trip_transactions
    ADD COLUMN IF NOT EXISTS trip_id      UUID,
    ADD COLUMN IF NOT EXISTS revision_id  UUID,
    ADD COLUMN IF NOT EXISTS revision_number INT,
    ADD COLUMN IF NOT EXISTS payment_idempotency_key TEXT;

-- Partial unique index: only one non-NULL idempotency key per use
-- (allows NULL for legacy rows created before this migration)
CREATE UNIQUE INDEX IF NOT EXISTS uq_trust_trip_transactions_idempotency_key
    ON trust_trip_transactions (payment_idempotency_key)
    WHERE payment_idempotency_key IS NOT NULL;

-- Index for fast lineage look-up
CREATE INDEX IF NOT EXISTS idx_trust_trip_transactions_trip_revision
    ON trust_trip_transactions (trip_id, revision_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_payment_tx_trip'
    ) THEN
        ALTER TABLE trust_trip_transactions
            ADD CONSTRAINT fk_payment_tx_trip
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE RESTRICT NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_payment_tx_revision_same_trip'
    ) THEN
        ALTER TABLE trust_trip_transactions
            ADD CONSTRAINT fk_payment_tx_revision_same_trip
            FOREIGN KEY (trip_id, revision_id)
            REFERENCES plan_revisions(trip_id, revision_id)
            ON DELETE RESTRICT NOT VALID;
    END IF;
END $$;

-- ====================================================================
-- PART B: HOTEL PAYMENT TRANSACTIONS — REVISION LINEAGE + IDEMPOTENCY
-- ====================================================================

CREATE TABLE IF NOT EXISTS trust_trip_hotel_transactions (
    id TEXT PRIMARY KEY,
    customer_ref TEXT,
    provider TEXT NOT NULL,
    hotel_name TEXT,
    offer_json JSONB NOT NULL,
    prebook_json JSONB NOT NULL,
    holder_json JSONB NOT NULL,
    guests_json JSONB NOT NULL,
    amount_inr NUMERIC(14,2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    payment_status TEXT NOT NULL DEFAULT 'pending',
    booking_status TEXT NOT NULL DEFAULT 'pending',
    booking_json JSONB,
    error TEXT,
    pdf_generated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE trust_trip_hotel_transactions
    ADD COLUMN IF NOT EXISTS trip_id      UUID,
    ADD COLUMN IF NOT EXISTS revision_id  UUID,
    ADD COLUMN IF NOT EXISTS revision_number INT,
    ADD COLUMN IF NOT EXISTS payment_idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_trust_trip_hotel_transactions_idempotency_key
    ON trust_trip_hotel_transactions (payment_idempotency_key)
    WHERE payment_idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_trust_trip_hotel_transactions_trip_revision
    ON trust_trip_hotel_transactions (trip_id, revision_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_hotel_payment_tx_trip'
    ) THEN
        ALTER TABLE trust_trip_hotel_transactions
            ADD CONSTRAINT fk_hotel_payment_tx_trip
            FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE RESTRICT NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_hotel_payment_tx_revision_same_trip'
    ) THEN
        ALTER TABLE trust_trip_hotel_transactions
            ADD CONSTRAINT fk_hotel_payment_tx_revision_same_trip
            FOREIGN KEY (trip_id, revision_id)
            REFERENCES plan_revisions(trip_id, revision_id)
            ON DELETE RESTRICT NOT VALID;
    END IF;
END $$;

-- NOTE: The migration runner automatically tracks applied migrations;
-- no manual INSERT into schema_migrations is needed here.

COMMIT;
