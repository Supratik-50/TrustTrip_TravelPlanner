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
