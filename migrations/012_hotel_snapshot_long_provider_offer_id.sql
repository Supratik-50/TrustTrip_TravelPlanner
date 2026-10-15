-- Migration: 012_hotel_snapshot_long_provider_offer_id.sql
-- Description: Expand offer_snapshots.provider_offer_id from VARCHAR(255) to TEXT
--   to support long compound provider rate/offer keys (e.g., LiteAPI, Hotelbeds tokens).
-- This migration is idempotent and transaction-safe.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'offer_snapshots'
          AND column_name = 'provider_offer_id'
    ) THEN
        ALTER TABLE offer_snapshots ALTER COLUMN provider_offer_id TYPE TEXT;
    END IF;
END $$;

COMMIT;
