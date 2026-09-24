-- Migration: 007_flight_booking_lifecycle.sql
-- Description: Flight booking intents, attempts, transitions, and state machine triggers.

-- ========================================================
-- PART 1: ENUMS
-- ========================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'booking_provider_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE booking_provider_enum AS ENUM ('duffel', 'amadeus', 'test_stub');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'booking_mode_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE booking_mode_enum AS ENUM ('duffel_link', 'duffel_api_order', 'direct_airline_verified');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'booking_status_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE booking_status_enum AS ENUM (
            'DRAFT', 'SELECTED', 'REVALIDATING', 'EXPIRED', 'PRICE_CHANGED',
            'UNAVAILABLE', 'READY_FOR_BOOKING', 'HANDED_OFF', 'COMPLETED',
            'FAILED', 'ABANDONED'
        );
    END IF;
END$$;

-- Ensure trips table has unique constraint on (id, user_id)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_trips_id_user'
    ) THEN
        ALTER TABLE trips ADD CONSTRAINT uq_trips_id_user UNIQUE (id, user_id);
    END IF;
END$$;

-- ========================================================
-- PART 2: CORE BOOKING INTENTS TABLE
-- ========================================================
CREATE TABLE IF NOT EXISTS booking_intents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    trip_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    selected_offer_snapshot_id UUID NOT NULL,
    current_revalidation_snapshot_id UUID NULL,
    provider booking_provider_enum NOT NULL DEFAULT 'duffel',
    booking_mode booking_mode_enum NOT NULL DEFAULT 'duffel_link',
    status booking_status_enum NOT NULL DEFAULT 'DRAFT',
    idempotency_key VARCHAR(128) NOT NULL,
    provider_reference VARCHAR(128) NULL,
    handoff_url TEXT NULL,
    handoff_expires_at TIMESTAMPTZ NULL,
    passenger_hash VARCHAR(64) NOT NULL,
    expected_amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,

    -- Multi-column tenancy foreign keys
    CONSTRAINT fk_booking_intents_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_booking_intents_revision FOREIGN KEY (revision_id, trip_id, user_id)
        REFERENCES plan_revisions(revision_id, trip_id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_booking_intents_selected_snapshot FOREIGN KEY (selected_offer_snapshot_id)
        REFERENCES offer_snapshots(id) ON DELETE RESTRICT,
    CONSTRAINT fk_booking_intents_reval_snapshot FOREIGN KEY (current_revalidation_snapshot_id)
        REFERENCES offer_snapshots(id) ON DELETE SET NULL,

    -- Composite unique constraints
    CONSTRAINT uq_booking_intents_composite UNIQUE (id, user_id, trip_id),
    CONSTRAINT uq_booking_intents_user_idempotency UNIQUE (user_id, idempotency_key),
    CONSTRAINT chk_positive_amount CHECK (expected_amount >= 0)
);

-- Indexes for high-performance query execution
CREATE INDEX IF NOT EXISTS idx_booking_intents_lookup 
    ON booking_intents (user_id, trip_id, status);
CREATE INDEX IF NOT EXISTS idx_booking_intents_provider_ref 
    ON booking_intents (provider, provider_reference) WHERE provider_reference IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_booking_intents_expiry 
    ON booking_intents (status, expires_at);

-- ========================================================
-- PART 3: BOOKING ATTEMPTS (IDEMPOTENCY & AUDIT)
-- ========================================================
CREATE TABLE IF NOT EXISTS booking_attempts (
    attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intent_id UUID NULL,
    user_id UUID NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL,
    attempt_number INT NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL,
    request_hash VARCHAR(64) NULL,
    request_payload JSONB NULL,
    response_payload JSONB NULL,
    error_code VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_booking_attempts_intent FOREIGN KEY (intent_id)
        REFERENCES booking_intents(id) ON DELETE CASCADE,
    CONSTRAINT uq_attempts_user_key_action UNIQUE (user_id, idempotency_key, action)
);

CREATE INDEX IF NOT EXISTS idx_booking_attempts_intent 
    ON booking_attempts (intent_id, created_at DESC);

-- ========================================================
-- PART 4: BOOKING TRANSITIONS (IMMUTABLE STATE AUDIT)
-- ========================================================
CREATE TABLE IF NOT EXISTS booking_transitions (
    transition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intent_id UUID NOT NULL,
    from_status booking_status_enum NOT NULL,
    to_status booking_status_enum NOT NULL,
    trigger VARCHAR(64) NOT NULL,
    actor_id VARCHAR(64) NOT NULL,
    reason TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_booking_transitions_intent FOREIGN KEY (intent_id)
        REFERENCES booking_intents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_booking_transitions_intent 
    ON booking_transitions (intent_id, created_at ASC);

-- Enforce strict immutability on booking_transitions
CREATE OR REPLACE FUNCTION fn_prevent_booking_transition_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'booking_transitions records are strictly immutable and cannot be updated or deleted'
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_booking_transition_mutation ON booking_transitions;
CREATE TRIGGER trg_prevent_booking_transition_mutation
    BEFORE UPDATE OR DELETE ON booking_transitions
    FOR EACH ROW
    EXECUTE FUNCTION fn_prevent_booking_transition_mutation();

-- ========================================================
-- PART 5: IMMUTABILITY & LIFECYCLE TRIGGERS
-- ========================================================

-- 1. Freeze tenancy identity on booking_intents
CREATE OR REPLACE FUNCTION fn_freeze_booking_intent_identity()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.user_id != NEW.user_id OR OLD.trip_id != NEW.trip_id OR OLD.revision_id != NEW.revision_id THEN
        RAISE EXCEPTION 'booking_intent tenancy and revision identity (user_id, trip_id, revision_id) is immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_freeze_booking_intent_identity ON booking_intents;
CREATE TRIGGER trg_freeze_booking_intent_identity
    BEFORE UPDATE OF user_id, trip_id, revision_id ON booking_intents
    FOR EACH ROW
    EXECUTE FUNCTION fn_freeze_booking_intent_identity();

-- 2. Snapshot Tenancy Verification (Blocker 5)
CREATE OR REPLACE FUNCTION fn_verify_booking_intent_snapshot_tenancy()
RETURNS TRIGGER AS $$
DECLARE
    v_snap_user_id UUID;
    v_snap_trip_id UUID;
BEGIN
    -- Verify selected_offer_snapshot_id
    SELECT s.user_id, s.trip_id
    INTO v_snap_user_id, v_snap_trip_id
    FROM offer_snapshots o
    JOIN search_sessions s ON s.id = o.session_id
    WHERE o.id = NEW.selected_offer_snapshot_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Selected offer snapshot % does not exist', NEW.selected_offer_snapshot_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF v_snap_user_id != NEW.user_id OR v_snap_trip_id != NEW.trip_id THEN
        RAISE EXCEPTION 'Selected offer snapshot % belongs to user % / trip %, but booking intent belongs to user % / trip %',
            NEW.selected_offer_snapshot_id, v_snap_user_id, v_snap_trip_id, NEW.user_id, NEW.trip_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- Verify current_revalidation_snapshot_id if present
    IF NEW.current_revalidation_snapshot_id IS NOT NULL THEN
        SELECT s.user_id, s.trip_id
        INTO v_snap_user_id, v_snap_trip_id
        FROM offer_snapshots o
        JOIN search_sessions s ON s.id = o.session_id
        WHERE o.id = NEW.current_revalidation_snapshot_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Revalidation offer snapshot % does not exist', NEW.current_revalidation_snapshot_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;

        IF v_snap_user_id != NEW.user_id OR v_snap_trip_id != NEW.trip_id THEN
            RAISE EXCEPTION 'Revalidation offer snapshot % belongs to user % / trip %, but booking intent belongs to user % / trip %',
                NEW.current_revalidation_snapshot_id, v_snap_user_id, v_snap_trip_id, NEW.user_id, NEW.trip_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_verify_booking_intent_snapshot_tenancy ON booking_intents;
CREATE TRIGGER trg_verify_booking_intent_snapshot_tenancy
    BEFORE INSERT OR UPDATE OF selected_offer_snapshot_id, current_revalidation_snapshot_id, user_id, trip_id ON booking_intents
    FOR EACH ROW
    EXECUTE FUNCTION fn_verify_booking_intent_snapshot_tenancy();

-- 3. Database State Machine Enforcement (Blocker 6)
CREATE OR REPLACE FUNCTION fn_enforce_booking_intent_state_machine()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    -- Terminal states have NO outgoing transitions
    IF OLD.status IN ('EXPIRED', 'UNAVAILABLE', 'COMPLETED', 'FAILED', 'ABANDONED') THEN
        RAISE EXCEPTION 'Illegal state transition: Cannot transition from terminal state % to %', OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Legal transitions graph
    IF OLD.status = 'DRAFT' AND NEW.status NOT IN ('SELECTED', 'ABANDONED') THEN
        RAISE EXCEPTION 'Illegal state transition from DRAFT to %', NEW.status USING ERRCODE = 'restrict_violation';
    ELSIF OLD.status = 'SELECTED' AND NEW.status NOT IN ('REVALIDATING', 'ABANDONED', 'EXPIRED') THEN
        RAISE EXCEPTION 'Illegal state transition from SELECTED to %', NEW.status USING ERRCODE = 'restrict_violation';
    ELSIF OLD.status = 'REVALIDATING' AND NEW.status NOT IN ('READY_FOR_BOOKING', 'PRICE_CHANGED', 'UNAVAILABLE', 'EXPIRED', 'FAILED') THEN
        RAISE EXCEPTION 'Illegal state transition from REVALIDATING to %', NEW.status USING ERRCODE = 'restrict_violation';
    ELSIF OLD.status = 'PRICE_CHANGED' AND NEW.status NOT IN ('SELECTED', 'ABANDONED') THEN
        RAISE EXCEPTION 'Illegal state transition from PRICE_CHANGED to %', NEW.status USING ERRCODE = 'restrict_violation';
    ELSIF OLD.status = 'READY_FOR_BOOKING' AND NEW.status NOT IN ('HANDED_OFF', 'REVALIDATING', 'ABANDONED', 'EXPIRED', 'UNAVAILABLE', 'PRICE_CHANGED') THEN
        RAISE EXCEPTION 'Illegal state transition from READY_FOR_BOOKING to %', NEW.status USING ERRCODE = 'restrict_violation';
    ELSIF OLD.status = 'HANDED_OFF' AND NEW.status NOT IN ('COMPLETED', 'FAILED', 'ABANDONED') THEN
        RAISE EXCEPTION 'Illegal state transition from HANDED_OFF to %', NEW.status USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_booking_intent_state_machine ON booking_intents;
CREATE TRIGGER trg_enforce_booking_intent_state_machine
    BEFORE UPDATE OF status ON booking_intents
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_booking_intent_state_machine();

-- 4. Single Authoritative Transition Audit Logging (Blocker 6)
CREATE OR REPLACE FUNCTION fn_audit_booking_transition()
RETURNS TRIGGER AS $$
DECLARE
    v_already_exists BOOLEAN;
BEGIN
    IF OLD.status != NEW.status THEN
        -- Check if service code already inserted an explicit audit row with rich metadata
        SELECT EXISTS (
            SELECT 1 FROM booking_transitions
            WHERE intent_id = NEW.id
              AND from_status = OLD.status
              AND to_status = NEW.status
              AND created_at >= (now() - interval '1 second')
        ) INTO v_already_exists;

        IF NOT v_already_exists THEN
            INSERT INTO booking_transitions (
                intent_id, from_status, to_status, trigger, actor_id, reason, metadata
            ) VALUES (
                NEW.id, OLD.status, NEW.status, 'DB_TRIGGER', NEW.user_id::text,
                'State transition enforced by database', jsonb_build_object('updated_at', NEW.updated_at)
            );
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_booking_transition ON booking_intents;
CREATE TRIGGER trg_audit_booking_transition
    AFTER UPDATE OF status ON booking_intents
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_booking_transition();

-- 5. Invalidate active booking intents on trip revision change (Blocker 10)
CREATE OR REPLACE FUNCTION fn_invalidate_booking_intents_on_revision_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.current_revision_id IS DISTINCT FROM NEW.current_revision_id THEN
        IF (OLD.metadata->>'selected_flight_snapshot_id' IS DISTINCT FROM NEW.metadata->>'selected_flight_snapshot_id')
           OR (OLD.metadata->'constraints'->>'departure_date' IS DISTINCT FROM NEW.metadata->'constraints'->>'departure_date')
           OR (OLD.metadata->'constraints'->>'return_date' IS DISTINCT FROM NEW.metadata->'constraints'->>'return_date')
           OR (OLD.metadata->'constraints'->>'passengers' IS DISTINCT FROM NEW.metadata->'constraints'->>'passengers')
           OR (OLD.origin IS DISTINCT FROM NEW.origin)
           OR (OLD.destination IS DISTINCT FROM NEW.destination) THEN
            UPDATE booking_intents
            SET status = 'EXPIRED',
                updated_at = now()
            WHERE trip_id = NEW.id
              AND revision_id = OLD.current_revision_id
              AND status IN ('DRAFT', 'SELECTED', 'REVALIDATING', 'PRICE_CHANGED', 'READY_FOR_BOOKING');
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_invalidate_booking_intents_on_revision_change ON trips;
CREATE TRIGGER trg_invalidate_booking_intents_on_revision_change
    AFTER UPDATE OF current_revision_id ON trips
    FOR EACH ROW
    EXECUTE FUNCTION fn_invalidate_booking_intents_on_revision_change();
