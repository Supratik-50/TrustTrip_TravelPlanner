-- Migration: 010_booking_authorizations.sql
-- Description: ADR-009 Explicit booking authorizations, immutable authorization records, and booking eligibility enforcement.

-- ========================================================
-- PART A: IMMUTABLE BOOKING AUTHORIZATIONS TABLE
-- ========================================================

CREATE TABLE IF NOT EXISTS booking_authorizations (
    authorization_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    user_id UUID NOT NULL,
    revision_number INT NOT NULL,
    authorized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    authorization_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_booking_authorization_trip_revision UNIQUE (trip_id, revision_id),
    CONSTRAINT chk_booking_auth_revision_number CHECK (revision_number >= 0),
    CONSTRAINT fk_booking_auth_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_booking_auth_trip_revision FOREIGN KEY (trip_id, revision_id)
        REFERENCES plan_revisions(trip_id, revision_id) ON DELETE RESTRICT
);

-- ========================================================
-- PART B: STRICT IMMUTABILITY (APPEND-ONLY)
-- ========================================================

CREATE OR REPLACE FUNCTION fn_enforce_booking_authorization_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'booking_authorizations table is append-only: deletions are prohibited'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RAISE EXCEPTION 'booking_authorizations table is strictly immutable: updates are prohibited'
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_booking_authorization_immutability ON booking_authorizations;
CREATE TRIGGER trg_booking_authorization_immutability
    BEFORE UPDATE OR DELETE ON booking_authorizations
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_booking_authorization_immutability();

-- ========================================================
-- PART C: PREREQUISITE GATES (APPROVAL REQUIRED & NO REJECTION)
-- ========================================================

CREATE OR REPLACE FUNCTION fn_check_booking_authorization_prerequisite()
RETURNS TRIGGER AS $$
BEGIN
    -- 1. Ensure revision is not rejected
    IF EXISTS (
        SELECT 1 FROM plan_revision_rejections
        WHERE trip_id = NEW.trip_id AND revision_id = NEW.revision_id
    ) THEN
        RAISE EXCEPTION 'Revision % is rejected; cannot authorize for booking', NEW.revision_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- 2. Ensure revision has explicit plan acceptance
    IF NOT EXISTS (
        SELECT 1 FROM plan_approvals
        WHERE trip_id = NEW.trip_id AND revision_id = NEW.revision_id
    ) THEN
        RAISE EXCEPTION 'Revision % must have explicit plan approval before booking authorization can be granted', NEW.revision_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_booking_authorization_prerequisite ON booking_authorizations;
CREATE TRIGGER trg_check_booking_authorization_prerequisite
    BEFORE INSERT ON booking_authorizations
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_booking_authorization_prerequisite();

-- ========================================================
-- PART D: INDEXES
-- ========================================================

CREATE INDEX IF NOT EXISTS idx_booking_authorizations_trip ON booking_authorizations(trip_id);
CREATE INDEX IF NOT EXISTS idx_booking_authorizations_revision ON booking_authorizations(revision_id);
CREATE INDEX IF NOT EXISTS idx_booking_authorizations_user_trip ON booking_authorizations(user_id, trip_id);
