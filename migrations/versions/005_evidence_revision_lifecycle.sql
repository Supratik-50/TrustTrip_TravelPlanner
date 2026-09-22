-- Migration: 005_evidence_revision_lifecycle.sql
-- Description: Deterministic revision lifecycle, immutable evidence, two-way approval invariant, attach-once provenance.

-- ========================================================
-- PART A: ENUMS & ANCHOR CONSTRAINTS
-- ========================================================

-- 1. Enums
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'evidence_type_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE evidence_type_enum AS ENUM (
            'FLIGHT', 'HOTEL', 'WEATHER', 'BUDGET', 'ITINERARY', 'ACTIVITY'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'usage_state_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE usage_state_enum AS ENUM (
            'FRESH', 'REUSED', 'INVALID_FOR_REVISION'
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_type
        WHERE typname = 'freshness_kind_enum'
          AND typnamespace = to_regnamespace(current_schema())
    ) THEN
        CREATE TYPE freshness_kind_enum AS ENUM (
            'LIVE_SUPPLIER', 'CACHED_PORT_DATA', 'SYNTHETIC_AGENT', 'STATIC_DATA'
        );
    END IF;
END$$;

-- 2. Anchor Trips Tenancy
ALTER TABLE trips ADD CONSTRAINT uq_trips_id_user UNIQUE (id, user_id);

-- 3. Freeze Provider Provenance Identity on Phase 4 Tables
CREATE OR REPLACE FUNCTION fn_freeze_search_sessions_identity()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.trip_id != NEW.trip_id OR OLD.user_id != NEW.user_id THEN
        RAISE EXCEPTION 'search_sessions tenancy identity (trip_id, user_id) is strictly immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_freeze_search_sessions_identity ON search_sessions;
CREATE TRIGGER trg_freeze_search_sessions_identity
    BEFORE UPDATE OF trip_id, user_id ON search_sessions
    FOR EACH ROW
    EXECUTE FUNCTION fn_freeze_search_sessions_identity();

CREATE OR REPLACE FUNCTION fn_freeze_offer_snapshots_provenance()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.session_id != NEW.session_id THEN
        RAISE EXCEPTION 'offer_snapshots session provenance (session_id) is strictly immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_freeze_offer_snapshots_provenance ON offer_snapshots;
CREATE TRIGGER trg_freeze_offer_snapshots_provenance
    BEFORE UPDATE OF session_id ON offer_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION fn_freeze_offer_snapshots_provenance();

-- ========================================================
-- PART B: PHASE 6 CORE TABLES & IMMUTABILITY TRIGGERS
-- ========================================================

-- 4. Plan Revisions Table (Strictly Published-Only & Immutable)
CREATE TABLE IF NOT EXISTS plan_revisions (
    revision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id UUID NOT NULL,
    user_id UUID NOT NULL,
    revision_number INT NOT NULL,
    change_set JSONB NOT NULL,
    plan_snapshot JSONB NOT NULL,
    is_published BOOLEAN NOT NULL DEFAULT TRUE,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_plan_revisions_composite UNIQUE (revision_id, trip_id, user_id),
    CONSTRAINT uq_plan_revisions_trip_revision UNIQUE (trip_id, revision_id),
    CONSTRAINT uq_trip_revision_number UNIQUE (trip_id, revision_number),
    CONSTRAINT chk_revision_number_non_negative CHECK (revision_number >= 0),
    CONSTRAINT chk_plan_revision_is_published CHECK (is_published = TRUE),
    CONSTRAINT fk_revisions_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION fn_enforce_plan_revision_immutability()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'plan_revisions is immutable and never updated; modifications are prohibited'
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_plan_revision_immutability ON plan_revisions;
CREATE TRIGGER trg_plan_revision_immutability
    BEFORE UPDATE ON plan_revisions
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_plan_revision_immutability();

-- 4.1 Plan Revision Deletion Trigger (Trip-First Lock Order)
CREATE OR REPLACE FUNCTION fn_lock_trip_before_plan_revision_delete()
RETURNS TRIGGER AS $$
DECLARE
    v_trip RECORD;
BEGIN
    -- Acquire parent trip row lock FOR UPDATE before revision deletion and approval cascade
    SELECT id, user_id, current_revision_id, status
    INTO v_trip
    FROM trips
    WHERE id = OLD.trip_id
    FOR UPDATE;

    -- If deleting current revision, disengage current_revision_id, current_revision_number, and demote status
    IF v_trip.current_revision_id = OLD.revision_id THEN
        UPDATE trips
        SET current_revision_id = NULL,
            current_revision_number = 0,
            status = 'awaiting_approval',
            updated_at = now()
        WHERE id = OLD.trip_id;
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_lock_trip_before_plan_revision_delete ON plan_revisions;
CREATE TRIGGER trg_lock_trip_before_plan_revision_delete
    BEFORE DELETE ON plan_revisions
    FOR EACH ROW
    EXECUTE FUNCTION fn_lock_trip_before_plan_revision_delete();

-- 5. Evidence Records Table (Unconditionally Immutable Observation Content)
CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id UUID NOT NULL,
    user_id UUID NOT NULL,
    evidence_type evidence_type_enum NOT NULL,
    applicability_key JSONB NOT NULL,
    applicability_hash VARCHAR(64) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    freshness_kind freshness_kind_enum NOT NULL,
    freshness_ttl_seconds INT NOT NULL,
    produced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    supplier_expires_at TIMESTAMPTZ NULL,
    source_agent VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_evidence_records_composite UNIQUE (evidence_id, trip_id, user_id),
    CONSTRAINT chk_ttl_positive CHECK (freshness_ttl_seconds > 0),
    CONSTRAINT fk_evidence_trip_user FOREIGN KEY (trip_id, user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION fn_enforce_evidence_record_immutability()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'evidence_records table is strictly immutable: updates are prohibited'
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_evidence_record_immutability ON evidence_records;
CREATE TRIGGER trg_evidence_record_immutability
    BEFORE UPDATE ON evidence_records
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_evidence_record_immutability();

-- 6. Evidence Provenance Table (Detachable Provider Provenance, Attach-Once)
CREATE TABLE IF NOT EXISTS evidence_provenance (
    evidence_id UUID PRIMARY KEY,
    trip_id UUID NOT NULL,
    user_id UUID NOT NULL,
    supplier_session_id UUID NULL,
    supplier_snapshot_id UUID NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_provenance_evidence FOREIGN KEY (evidence_id, trip_id, user_id)
        REFERENCES evidence_records(evidence_id, trip_id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_provenance_supplier_session FOREIGN KEY (supplier_session_id)
        REFERENCES search_sessions(id) ON DELETE SET NULL,
    CONSTRAINT fk_provenance_supplier_snapshot FOREIGN KEY (supplier_snapshot_id)
        REFERENCES offer_snapshots(id) ON DELETE SET NULL
);

CREATE OR REPLACE FUNCTION fn_enforce_evidence_provenance_lifecycle()
RETURNS TRIGGER AS $$
DECLARE
    v_session_trip_id UUID;
    v_session_user_id UUID;
    v_snap_trip_id UUID;
    v_snap_user_id UUID;
    v_snap_session_id UUID;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- Identity columns are strictly immutable
        IF OLD.evidence_id != NEW.evidence_id OR OLD.trip_id != NEW.trip_id OR OLD.user_id != NEW.user_id THEN
            RAISE EXCEPTION 'evidence_provenance identity (evidence_id, trip_id, user_id) is immutable'
                USING ERRCODE = 'restrict_violation';
        END IF;

        -- 1. Session Provenance Lifecycle Rules
        IF OLD.supplier_session_id IS NULL AND NEW.supplier_session_id IS NOT NULL THEN
            RAISE EXCEPTION 'evidence_provenance is attach-once: cannot reattach supplier_session_id after creation'
                USING ERRCODE = 'restrict_violation';
        ELSIF OLD.supplier_session_id IS NOT NULL AND NEW.supplier_session_id IS NOT NULL
              AND OLD.supplier_session_id != NEW.supplier_session_id THEN
            RAISE EXCEPTION 'evidence_provenance is attach-once: cannot reassign supplier_session_id'
                USING ERRCODE = 'restrict_violation';
        ELSIF OLD.supplier_session_id IS NOT NULL AND NEW.supplier_session_id IS NULL THEN
            IF EXISTS (SELECT 1 FROM search_sessions WHERE id = OLD.supplier_session_id) THEN
                RAISE EXCEPTION 'Cannot manually detach supplier_session_id while search_session % still exists',
                    OLD.supplier_session_id USING ERRCODE = 'restrict_violation';
            END IF;
        END IF;

        -- 2. Snapshot Provenance Lifecycle Rules
        IF OLD.supplier_snapshot_id IS NULL AND NEW.supplier_snapshot_id IS NOT NULL THEN
            RAISE EXCEPTION 'evidence_provenance is attach-once: cannot reattach supplier_snapshot_id after creation'
                USING ERRCODE = 'restrict_violation';
        ELSIF OLD.supplier_snapshot_id IS NOT NULL AND NEW.supplier_snapshot_id IS NOT NULL
              AND OLD.supplier_snapshot_id != NEW.supplier_snapshot_id THEN
            RAISE EXCEPTION 'evidence_provenance is attach-once: cannot reassign supplier_snapshot_id'
                USING ERRCODE = 'restrict_violation';
        ELSIF OLD.supplier_snapshot_id IS NOT NULL AND NEW.supplier_snapshot_id IS NULL THEN
            IF EXISTS (SELECT 1 FROM offer_snapshots WHERE id = OLD.supplier_snapshot_id) THEN
                RAISE EXCEPTION 'Cannot manually detach supplier_snapshot_id while offer_snapshot % still exists',
                    OLD.supplier_snapshot_id USING ERRCODE = 'restrict_violation';
            END IF;
        END IF;

        RETURN NEW;
    END IF;

    -- INSERT branch: validate tenant ownership and snapshot-session lineage
    IF NEW.supplier_session_id IS NOT NULL THEN
        SELECT trip_id, user_id INTO v_session_trip_id, v_session_user_id
        FROM search_sessions WHERE id = NEW.supplier_session_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Foreign key violation: supplier_session_id (%) not found',
                NEW.supplier_session_id USING ERRCODE = 'foreign_key_violation';
        END IF;

        IF v_session_trip_id != NEW.trip_id OR v_session_user_id != NEW.user_id THEN
            RAISE EXCEPTION 'Tenancy violation: supplier_session_id (%) belongs to trip (%) / user (%), expected trip (%) / user (%)',
                NEW.supplier_session_id, v_session_trip_id, v_session_user_id, NEW.trip_id, NEW.user_id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;

    IF NEW.supplier_snapshot_id IS NOT NULL THEN
        SELECT s.trip_id, s.user_id, o.session_id
        INTO v_snap_trip_id, v_snap_user_id, v_snap_session_id
        FROM offer_snapshots o
        JOIN search_sessions s ON s.id = o.session_id
        WHERE o.id = NEW.supplier_snapshot_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Foreign key violation: supplier_snapshot_id (%) not found',
                NEW.supplier_snapshot_id USING ERRCODE = 'foreign_key_violation';
        END IF;

        IF v_snap_trip_id != NEW.trip_id OR v_snap_user_id != NEW.user_id THEN
            RAISE EXCEPTION 'Tenancy violation: supplier_snapshot_id (%) belongs to trip (%) / user (%), expected trip (%) / user (%)',
                NEW.supplier_snapshot_id, v_snap_trip_id, v_snap_user_id, NEW.trip_id, NEW.user_id
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.supplier_session_id IS NOT NULL THEN
            IF v_snap_session_id != NEW.supplier_session_id THEN
                RAISE EXCEPTION 'Lineage violation: supplier_snapshot_id (%) belongs to session (%), but supplier_session_id is (%)',
                    NEW.supplier_snapshot_id, v_snap_session_id, NEW.supplier_session_id
                    USING ERRCODE = 'restrict_violation';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_evidence_provenance_lifecycle ON evidence_provenance;
CREATE TRIGGER trg_enforce_evidence_provenance_lifecycle
    BEFORE INSERT OR UPDATE ON evidence_provenance
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_evidence_provenance_lifecycle();

-- 7. Plan Revision Evidence Links
CREATE TABLE IF NOT EXISTS plan_revision_evidence_links (
    link_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    revision_id UUID NOT NULL,
    evidence_id UUID NOT NULL,
    trip_id UUID NOT NULL,
    user_id UUID NOT NULL,
    usage_state usage_state_enum NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_revision_evidence UNIQUE (revision_id, evidence_id),
    CONSTRAINT fk_link_revision FOREIGN KEY (revision_id, trip_id, user_id)
        REFERENCES plan_revisions(revision_id, trip_id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_link_evidence FOREIGN KEY (evidence_id, trip_id, user_id)
        REFERENCES evidence_records(evidence_id, trip_id, user_id) ON DELETE CASCADE
);

-- 8. Plan Approvals Table (Cascade Pruned on Revision Deletion & Strictly Immutable)
CREATE TABLE IF NOT EXISTS plan_approvals (
    approval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    approved_by_user_id UUID NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approval_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_approval_trip_revision UNIQUE (trip_id, revision_id),
    CONSTRAINT fk_approvals_trip_user FOREIGN KEY (trip_id, approved_by_user_id)
        REFERENCES trips(id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_approvals_trip_revision FOREIGN KEY (trip_id, revision_id)
        REFERENCES plan_revisions(trip_id, revision_id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION fn_enforce_plan_approval_immutability()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'plan_approvals table is strictly immutable: updates are prohibited'
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_plan_approval_immutability ON plan_approvals;
CREATE TRIGGER trg_plan_approval_immutability
    BEFORE UPDATE ON plan_approvals
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_plan_approval_immutability();

-- 8.1 Direct Plan Approval Deletion Trigger (BEFORE DELETE: Trip Row Lock + Demotion)
CREATE OR REPLACE FUNCTION fn_on_plan_approval_deleted()
RETURNS TRIGGER AS $$
DECLARE
    v_trip RECORD;
BEGIN
    -- 1. Acquire parent trip row lock FOR UPDATE BEFORE approval deletion occurs
    SELECT id, user_id, current_revision_id, status
    INTO v_trip
    FROM trips
    WHERE id = OLD.trip_id
    FOR UPDATE;

    -- 2. If deleted approval belongs to current revision and trip is approved, demote status
    IF FOUND THEN
        IF v_trip.current_revision_id = OLD.revision_id AND v_trip.status = 'approved' THEN
            UPDATE trips
            SET status = 'awaiting_approval',
                updated_at = now()
            WHERE id = OLD.trip_id;
        END IF;
    END IF;

    -- 3. Return OLD to permit deletion
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_on_plan_approval_deleted ON plan_approvals;
CREATE TRIGGER trg_on_plan_approval_deleted
    BEFORE DELETE ON plan_approvals
    FOR EACH ROW
    EXECUTE FUNCTION fn_on_plan_approval_deleted();

-- ========================================================
-- PART C: REVISION LIFECYCLE ON TRIPS (STRICT ORDER)
-- ========================================================

-- STEP 1: Add current_revision_id and current_revision_number
ALTER TABLE trips ADD COLUMN IF NOT EXISTS current_revision_id UUID NULL;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS current_revision_number INT NOT NULL DEFAULT 0;

-- STEP 2: Legacy-data normalization/backfill (Option C & Revision Number Sync)
-- 2a. Demote unpointed or unapproved trips lacking authoritative PlanApproval
UPDATE trips
SET status = 'awaiting_approval',
    updated_at = now()
WHERE status = 'approved'
  AND (
      current_revision_id IS NULL
      OR NOT EXISTS (
          SELECT 1 FROM plan_approvals a
          WHERE a.trip_id = trips.id
            AND a.revision_id = trips.current_revision_id
            AND a.approved_by_user_id = trips.user_id
      )
  );

-- 2b. Synchronize current_revision_number from referenced plan_revisions when current_revision_id is set
UPDATE trips t
SET current_revision_number = r.revision_number
FROM plan_revisions r
WHERE r.revision_id = t.current_revision_id
  AND r.trip_id = t.id
  AND r.user_id = t.user_id;

-- 2c. Reset current_revision_number to 0 when current_revision_id is NULL
UPDATE trips
SET current_revision_number = 0
WHERE current_revision_id IS NULL;

-- STEP 3: Explicit SQL verification gate for invalid legacy state and revision number sync
DO $$
DECLARE
    v_invalid_count INT;
    v_mismatched_rev_count INT;
BEGIN
    -- Check for invalid approved trips
    SELECT COUNT(*)
    INTO v_invalid_count
    FROM trips
    WHERE status = 'approved'
      AND (
          current_revision_id IS NULL
          OR NOT EXISTS (
              SELECT 1 FROM plan_approvals a
              WHERE a.trip_id = trips.id
                AND a.revision_id = trips.current_revision_id
                AND a.approved_by_user_id = trips.user_id
          )
      );

    IF v_invalid_count > 0 THEN
        RAISE EXCEPTION 'Migration abort: % trips remain in "approved" status without valid current_revision_id or authoritative PlanApproval',
            v_invalid_count
            USING ERRCODE = 'check_violation';
    END IF;

    -- Check for mismatched current_revision_number
    SELECT COUNT(*)
    INTO v_mismatched_rev_count
    FROM trips t
    JOIN plan_revisions r ON r.revision_id = t.current_revision_id
    WHERE t.current_revision_id IS NOT NULL
      AND t.current_revision_number != r.revision_number;

    IF v_mismatched_rev_count > 0 THEN
        RAISE EXCEPTION 'Migration abort: % trips have current_revision_number out of sync with referenced plan revision',
            v_mismatched_rev_count
            USING ERRCODE = 'check_violation';
    END IF;
END$$;

-- STEP 4: Add FK and check constraints
ALTER TABLE trips DROP CONSTRAINT IF EXISTS fk_trips_current_revision;
ALTER TABLE trips ADD CONSTRAINT fk_trips_current_revision
    FOREIGN KEY (current_revision_id)
    REFERENCES plan_revisions(revision_id)
    ON DELETE SET NULL;

ALTER TABLE trips DROP CONSTRAINT IF EXISTS chk_approved_requires_current_revision;
ALTER TABLE trips ADD CONSTRAINT chk_approved_requires_current_revision
    CHECK (status != 'approved' OR current_revision_id IS NOT NULL);

-- STEP 5: Create normalization/invariant triggers on trips
CREATE OR REPLACE FUNCTION fn_trips_before_write()
RETURNS TRIGGER AS $$
DECLARE
    v_is_published BOOLEAN;
BEGIN
    -- Normalization: when current_revision_id is NULL, reset revision number and demote approved status
    IF NEW.current_revision_id IS NULL THEN
        NEW.current_revision_number := 0;
        IF NEW.status = 'approved' THEN
            NEW.status := 'awaiting_approval';
        END IF;
    ELSE
        -- When current_revision_id is set, sync revision_number and verify validity & publication
        SELECT revision_number, is_published
        INTO NEW.current_revision_number, v_is_published
        FROM plan_revisions
        WHERE revision_id = NEW.current_revision_id
          AND trip_id = NEW.id
          AND user_id = NEW.user_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Foreign key violation: current_revision_id (%) does not belong to trip (%) and user (%)',
                NEW.current_revision_id, NEW.id, NEW.user_id
                USING ERRCODE = 'foreign_key_violation';
        END IF;

        IF NOT v_is_published THEN
            RAISE EXCEPTION 'Integrity violation: current_revision_id (%) points to an unpublished revision (is_published = FALSE)',
                NEW.current_revision_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_trips_before_write ON trips;
CREATE TRIGGER trg_trips_before_write
    BEFORE INSERT OR UPDATE OF current_revision_id, current_revision_number, status ON trips
    FOR EACH ROW
    EXECUTE FUNCTION fn_trips_before_write();

-- STEP 6: Create deferred approval triggers (COMMIT-time consistency validation)
CREATE OR REPLACE FUNCTION fn_verify_trip_approval_two_way()
RETURNS TRIGGER AS $$
DECLARE
    v_trip RECORD;
    v_has_approval BOOLEAN;
BEGIN
    SELECT id, user_id, current_revision_id, status
    INTO v_trip
    FROM trips
    WHERE id = NEW.id;

    IF FOUND THEN
        IF v_trip.current_revision_id IS NOT NULL THEN
            SELECT EXISTS (
                SELECT 1 FROM plan_approvals
                WHERE trip_id = v_trip.id
                  AND revision_id = v_trip.current_revision_id
                  AND approved_by_user_id = v_trip.user_id
            ) INTO v_has_approval;
        ELSE
            v_has_approval := FALSE;
        END IF;

        -- Invariant 1: approved => matching owner approval exists
        IF v_trip.status = 'approved' AND NOT v_has_approval THEN
            RAISE EXCEPTION 'Approval invariant violation: trip % has status "approved" but lacks a matching PlanApproval for current_revision_id % and owner %',
                v_trip.id, v_trip.current_revision_id, v_trip.user_id
                USING ERRCODE = 'check_violation';
        END IF;

        -- Invariant 2: matching current-revision owner approval exists => status = 'approved'
        IF v_has_approval AND v_trip.status != 'approved' THEN
            RAISE EXCEPTION 'Approval invariant violation: trip % has an active PlanApproval for current_revision_id % but status is "%" instead of "approved"',
                v_trip.id, v_trip.current_revision_id, v_trip.status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_verify_trip_approval_two_way ON trips;
CREATE CONSTRAINT TRIGGER trg_verify_trip_approval_two_way
    AFTER INSERT OR UPDATE OF status, current_revision_id ON trips
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION fn_verify_trip_approval_two_way();

CREATE OR REPLACE FUNCTION fn_verify_plan_approval_trip_two_way()
RETURNS TRIGGER AS $$
DECLARE
    v_trip RECORD;
    v_has_approval BOOLEAN;
BEGIN
    SELECT id, user_id, current_revision_id, status
    INTO v_trip
    FROM trips
    WHERE id = COALESCE(NEW.trip_id, OLD.trip_id);

    IF FOUND THEN
        IF v_trip.current_revision_id IS NOT NULL THEN
            SELECT EXISTS (
                SELECT 1 FROM plan_approvals
                WHERE trip_id = v_trip.id
                  AND revision_id = v_trip.current_revision_id
                  AND approved_by_user_id = v_trip.user_id
            ) INTO v_has_approval;
        ELSE
            v_has_approval := FALSE;
        END IF;

        IF v_has_approval AND v_trip.status != 'approved' THEN
            RAISE EXCEPTION 'Approval invariant violation: PlanApproval exists for current revision % of trip % but trip status is "%"',
                v_trip.current_revision_id, v_trip.id, v_trip.status
                USING ERRCODE = 'check_violation';
        END IF;

        IF v_trip.status = 'approved' AND NOT v_has_approval THEN
            RAISE EXCEPTION 'Approval invariant violation: trip % has status "approved" but lacks matching PlanApproval for current revision',
                v_trip.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_verify_plan_approval_trip_two_way ON plan_approvals;
CREATE CONSTRAINT TRIGGER trg_verify_plan_approval_trip_two_way
    AFTER INSERT OR DELETE ON plan_approvals
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION fn_verify_plan_approval_trip_two_way();
