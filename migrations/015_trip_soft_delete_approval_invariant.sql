-- Migration: 015_trip_soft_delete_approval_invariant.sql
-- Description: Allow legitimate soft-deletion (status = 'deleted') of trips with active PlanApproval records while preserving historical approval audit trails.

-- STEP 1: Update fn_verify_trip_approval_two_way() to permit terminal 'deleted' status
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
        -- Terminal 'deleted' status is explicitly supported to preserve historical plan_approvals audit records on soft-deleted trips.
        IF v_has_approval AND v_trip.status != 'approved' AND v_trip.status != 'deleted' THEN
            RAISE EXCEPTION 'Approval invariant violation: trip % has an active PlanApproval for current_revision_id % but status is "%" instead of "approved"',
                v_trip.id, v_trip.current_revision_id, v_trip.status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- STEP 2: Update fn_verify_plan_approval_trip_two_way() to permit terminal 'deleted' status and reject new approvals on deleted trips
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
        -- Reject approving a plan on a trip that is already soft-deleted
        IF TG_OP = 'INSERT' AND v_trip.status = 'deleted' THEN
            RAISE EXCEPTION 'Cannot approve revision % for trip %: trip is already deleted',
                NEW.revision_id, v_trip.id
                USING ERRCODE = 'check_violation';
        END IF;

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

        -- Terminal 'deleted' status is explicitly supported to preserve historical plan_approvals audit records on soft-deleted trips.
        IF v_has_approval AND v_trip.status != 'approved' AND v_trip.status != 'deleted' THEN
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
