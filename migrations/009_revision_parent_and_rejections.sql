-- Migration: 009_revision_parent_and_rejections.sql
-- Description: ADR-008 Revision hierarchy (parent linkage), immutable plan revision rejections, and HITL mutual exclusion.

-- ========================================================
-- PART A: PARENT REVISION LINKAGE ON plan_revisions
-- ========================================================

-- 1. Add parent_revision_id column
ALTER TABLE plan_revisions ADD COLUMN IF NOT EXISTS parent_revision_id UUID NULL;

-- 2. Self-parent check constraint
ALTER TABLE plan_revisions DROP CONSTRAINT IF EXISTS chk_plan_revision_no_self_parent;
ALTER TABLE plan_revisions ADD CONSTRAINT chk_plan_revision_no_self_parent
    CHECK (parent_revision_id IS NULL OR parent_revision_id != revision_id);

-- 3. Same-trip integrity foreign key
ALTER TABLE plan_revisions DROP CONSTRAINT IF EXISTS fk_plan_revisions_parent;
ALTER TABLE plan_revisions ADD CONSTRAINT fk_plan_revisions_parent
    FOREIGN KEY (trip_id, parent_revision_id)
    REFERENCES plan_revisions(trip_id, revision_id)
    ON DELETE RESTRICT;

-- 4. Backfill parent links only where unambiguous (initial revision parent = NULL)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_plan_revision_immutability'
          AND tgrelid = 'plan_revisions'::regclass
    ) THEN
        ALTER TABLE plan_revisions DISABLE TRIGGER trg_plan_revision_immutability;
    END IF;

    UPDATE plan_revisions curr
    SET parent_revision_id = prev.revision_id
    FROM plan_revisions prev
    WHERE curr.trip_id = prev.trip_id
      AND curr.revision_number > 0
      AND prev.revision_number = curr.revision_number - 1
      AND curr.parent_revision_id IS NULL;

    IF EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_plan_revision_immutability'
          AND tgrelid = 'plan_revisions'::regclass
    ) THEN
        ALTER TABLE plan_revisions ENABLE TRIGGER trg_plan_revision_immutability;
    END IF;
END $$;

-- ========================================================
-- PART B: IMMUTABLE PLAN REVISION REJECTIONS TABLE
-- ========================================================

CREATE TABLE IF NOT EXISTS plan_revision_rejections (
    rejection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    rejected_by_user_id UUID NOT NULL,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason TEXT NULL,
    rejection_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_rejection_trip_revision UNIQUE (trip_id, revision_id),
    CONSTRAINT fk_rejections_trip_user FOREIGN KEY (trip_id, rejected_by_user_id)
        REFERENCES trips(id, user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rejections_trip_revision FOREIGN KEY (trip_id, revision_id)
        REFERENCES plan_revisions(trip_id, revision_id) ON DELETE RESTRICT
);

-- Ensure RESTRICT constraints if table was already created with CASCADE
ALTER TABLE plan_revision_rejections DROP CONSTRAINT IF EXISTS fk_rejections_trip_user;
ALTER TABLE plan_revision_rejections ADD CONSTRAINT fk_rejections_trip_user
    FOREIGN KEY (trip_id, rejected_by_user_id)
    REFERENCES trips(id, user_id) ON DELETE RESTRICT;

ALTER TABLE plan_revision_rejections DROP CONSTRAINT IF EXISTS fk_rejections_trip_revision;
ALTER TABLE plan_revision_rejections ADD CONSTRAINT fk_rejections_trip_revision
    FOREIGN KEY (trip_id, revision_id)
    REFERENCES plan_revisions(trip_id, revision_id) ON DELETE RESTRICT;

-- Immutability: plan_revision_rejections is append-only (updates and deletions strictly prohibited)
CREATE OR REPLACE FUNCTION fn_enforce_plan_revision_rejection_immutability()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'plan_revision_rejections table is append-only: deletions are prohibited'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RAISE EXCEPTION 'plan_revision_rejections table is strictly immutable: updates are prohibited'
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_plan_revision_rejection_immutability ON plan_revision_rejections;
CREATE TRIGGER trg_plan_revision_rejection_immutability
    BEFORE UPDATE OR DELETE ON plan_revision_rejections
    FOR EACH ROW
    EXECUTE FUNCTION fn_enforce_plan_revision_rejection_immutability();

-- Drop obsolete delete trigger
DROP TRIGGER IF EXISTS trg_on_plan_rejection_deleted ON plan_revision_rejections;
DROP FUNCTION IF EXISTS fn_on_plan_rejection_deleted();

-- ========================================================
-- PART C: APPROVAL / REJECTION MUTUAL EXCLUSION
-- ========================================================

-- Cannot reject an already-approved revision
CREATE OR REPLACE FUNCTION fn_check_rejection_mutual_exclusion()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM plan_approvals
        WHERE trip_id = NEW.trip_id AND revision_id = NEW.revision_id
    ) THEN
        RAISE EXCEPTION 'Revision % is already approved; cannot reject', NEW.revision_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_rejection_mutual_exclusion ON plan_revision_rejections;
CREATE TRIGGER trg_check_rejection_mutual_exclusion
    BEFORE INSERT ON plan_revision_rejections
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_rejection_mutual_exclusion();

-- Cannot approve an already-rejected revision
CREATE OR REPLACE FUNCTION fn_check_approval_mutual_exclusion()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM plan_revision_rejections
        WHERE trip_id = NEW.trip_id AND revision_id = NEW.revision_id
    ) THEN
        RAISE EXCEPTION 'Revision % is already rejected; cannot approve', NEW.revision_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_approval_mutual_exclusion ON plan_approvals;
CREATE TRIGGER trg_check_approval_mutual_exclusion
    BEFORE INSERT ON plan_approvals
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_approval_mutual_exclusion();
