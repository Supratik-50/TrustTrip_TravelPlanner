-- Migration: 004_travel_intelligence.sql
-- Description: Establish search sessions, immutable offer snapshots, and revalidation history.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'freshness_status_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE freshness_status_enum AS ENUM ('fresh', 'stale', 'expired');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'availability_status_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE availability_status_enum AS ENUM ('available', 'limited_inventory', 'sold_out', 'unavailable');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'revalidation_status_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE revalidation_status_enum AS ENUM ('pending', 'confirmed_unchanged', 'price_changed', 'schedule_changed', 'offer_withdrawn');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'offer_type_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE offer_type_enum AS ENUM ('flight', 'hotel');
    END IF;
END$$;

-- 1. Search Sessions (Scoped strictly to user and trip)
CREATE TABLE IF NOT EXISTS search_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trip_id UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
    trip_revision INT NOT NULL DEFAULT 0,
    search_type VARCHAR(32) NOT NULL,
    criteria_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_reuse_expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_trip ON search_sessions(user_id, trip_id);
CREATE INDEX IF NOT EXISTS idx_sessions_criteria ON search_sessions(criteria_hash);

-- 2. Offer Snapshots (Immutable point-in-time observations)
CREATE TABLE IF NOT EXISTS offer_snapshots (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    provider_offer_id VARCHAR(255) NOT NULL,
    offer_type offer_type_enum NOT NULL,
    price_total NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    normalized_offer JSONB NOT NULL,
    raw_payload_encrypted BYTEA NOT NULL,
    freshness freshness_status_enum NOT NULL DEFAULT 'fresh',
    availability availability_status_enum NOT NULL DEFAULT 'available',
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NULL,
    predecessor_snapshot_id UUID REFERENCES offer_snapshots(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_session ON offer_snapshots(session_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_provider ON offer_snapshots(provider, provider_offer_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_expires_at ON offer_snapshots(expires_at) WHERE expires_at IS NOT NULL;

-- 3. Revalidation History (Immutable append-only audit trail)
CREATE TABLE IF NOT EXISTS revalidation_history (
    id UUID PRIMARY KEY,
    original_snapshot_id UUID NOT NULL REFERENCES offer_snapshots(id) ON DELETE CASCADE,
    subsequent_snapshot_id UUID REFERENCES offer_snapshots(id) ON DELETE SET NULL,
    status revalidation_status_enum NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_changed BOOLEAN NOT NULL DEFAULT FALSE,
    original_price NUMERIC(12, 2) NOT NULL,
    current_price NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    schedule_changed BOOLEAN NOT NULL DEFAULT FALSE,
    details TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_revalidation_original ON revalidation_history(original_snapshot_id);
