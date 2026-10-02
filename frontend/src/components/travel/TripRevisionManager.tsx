import React, { useState } from 'react';
import {
  History,
  GitCommit,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  RefreshCw,
  ArrowRight,
  Sparkles,
  Calendar,
  Users,
  Wallet,
  Compass,
  FileEdit,
  Layers,
  PlusCircle,
  MinusCircle,
  Clock,
  ShieldCheck,
} from 'lucide-react';
import { TripDetail, TripRevision, TripChangeSet, CreateRevisionRequest } from '../../types/trip';
import { api } from '../../lib/apiClient';
import { useNotification } from '../../context/NotificationContext';
import { Button } from '../common/Button';
import { Input } from '../common/Input';
import { formatCurrency } from '../../lib/currency';

interface TripRevisionManagerProps {
  trip: TripDetail;
  onTripUpdated: (updatedTrip: TripDetail) => void;
  onNavigateTab: (tab: 'itinerary' | 'flights' | 'hotels') => void;
}

export const TripRevisionManager: React.FC<TripRevisionManagerProps> = ({
  trip,
  onTripUpdated,
  onNavigateTab,
}) => {
  const { notify } = useNotification();

  // Proposed change state
  const [feedback, setFeedback] = useState('');
  const [startDate, setStartDate] = useState(trip.start_date || '');
  const [endDate, setEndDate] = useState(trip.end_date || '');
  const [travelers, setTravelers] = useState<number>(trip.travelers_count || 2);
  const [budget, setBudget] = useState<string>(trip.budget ? String(trip.budget) : '');
  const [preferencesStr, setPreferencesStr] = useState(
    trip.preferences ? trip.preferences.join(', ') : ''
  );

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDeciding, setIsDeciding] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [rejectionReason, setRejectionReason] = useState('');

  const [conflictError, setConflictError] = useState<{
    message: string;
    serverRevision: number;
  } | null>(null);

  // Selected revision for comparison
  const revisions = trip.revisions || [];
  const currentRevisionNumber = trip.active_revision_number || revisions.length || 1;
  const currentRevision = revisions.find((r) => r.revision_number === currentRevisionNumber) || trip.current_revision;

  const [selectedHistoricalRev, setSelectedHistoricalRev] = useState<TripRevision | null>(
    revisions.length > 1 ? revisions[revisions.length - 2] : null
  );

  // Quick feedback suggestions
  const feedbackPresets = [
    'I want a slower pace with fewer daily stops.',
    'Focus more on traditional dining and neighborhood markets.',
    'Add more open leisure time in the late afternoons.',
    'Avoid rushed morning departures.',
  ];

  // Stale detection logic
  const willAlterInventory =
    startDate !== trip.start_date ||
    endDate !== trip.end_date ||
    travelers !== trip.travelers_count;

  const handleCreateRevision = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setConflictError(null);

    const changes: TripChangeSet = {
      start_date: startDate,
      end_date: endDate,
      travelers_count: Number(travelers) || 1,
      passengers: Number(travelers) || 1,
      budget: budget ? Number(budget) : undefined,
      preferences: preferencesStr
        .split(',')
        .map((p) => p.trim())
        .filter(Boolean),
      feedback: feedback.trim() || undefined,
      expected_base_revision: currentRevisionNumber,
      expected_base_revision_id: currentRevision?.revision_id,
    };

    const request: CreateRevisionRequest = {
      expected_base_revision: currentRevisionNumber,
      expected_base_revision_id: currentRevision?.revision_id,
      changes,
      change_summary: feedback.trim() || 'Updated travel dates & trip constraints',
    };

    try {
      const createdRev = await api.createTripRevision(trip.trip_id, request);
      notify(
        `Revision ${createdRev.revision_number} created successfully. Travel constraints updated.`,
        'success',
        'Revision Applied'
      );

      // Refresh the full trip state from server
      const freshTrip = await api.getTrip(trip.trip_id);
      onTripUpdated(freshTrip);
      setFeedback('');
    } catch (err: any) {
      if (err.statusCode === 409 || err.status === 409 || err.message?.includes('conflict') || err.message?.includes('409')) {
        setConflictError({
          message:
            'This trip was updated elsewhere. Your baseline revision does not match the active plan.',
          serverRevision: err.current_revision || currentRevisionNumber + 1,
        });
        notify(
          'Revision conflict detected. Please refresh the latest trip before applying changes.',
          'error',
          'Revision Conflict'
        );
      } else {
        notify(err.message || 'Unable to create revision.', 'error', 'Revision Error');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleApprove = async () => {
    if (!currentRevision?.revision_id) {
      notify('No active revision found to approve.', 'error');
      return;
    }

    setIsDeciding(true);
    setConflictError(null);

    try {
      await api.approveTrip(trip.trip_id, {
        revision_id: currentRevision.revision_id,
        expected_revision_number: currentRevisionNumber,
      });

      notify(
        `Revision #${currentRevisionNumber} approved successfully. This plan revision is accepted.`,
        'success',
        'Revision Approved'
      );

      const freshTrip = await api.getTrip(trip.trip_id);
      onTripUpdated(freshTrip);
    } catch (err: any) {
      if (err.statusCode === 409 || err.status === 409 || err.message?.includes('conflict') || err.message?.includes('409')) {
        setConflictError({
          message: 'The plan was updated or already decided. Please refresh the latest plan before taking action.',
          serverRevision: currentRevisionNumber,
        });
        notify('Revision conflict detected (HTTP 409). Please refresh.', 'error', 'Approval Conflict');
      } else {
        notify(err.message || 'Unable to approve revision.', 'error');
      }
    } finally {
      setIsDeciding(false);
    }
  };

  const handleReject = async () => {
    if (!currentRevision?.revision_id) {
      notify('No active revision found to reject.', 'error');
      return;
    }

    setIsDeciding(true);
    setConflictError(null);

    try {
      await api.rejectTripRevision(trip.trip_id, currentRevision.revision_id, {
        expected_revision_number: currentRevisionNumber,
        reason: rejectionReason.trim() || undefined,
      });

      notify(
        `Revision #${currentRevisionNumber} rejected. You can now request adjustments.`,
        'info',
        'Revision Rejected'
      );

      setShowRejectModal(false);
      setRejectionReason('');

      const freshTrip = await api.getTrip(trip.trip_id);
      onTripUpdated(freshTrip);
    } catch (err: any) {
      if (err.statusCode === 409 || err.status === 409 || err.message?.includes('conflict') || err.message?.includes('409')) {
        setConflictError({
          message: 'The plan was updated or already decided. Please refresh the latest plan before taking action.',
          serverRevision: currentRevisionNumber,
        });
        notify('Revision conflict detected (HTTP 409). Please refresh.', 'error', 'Rejection Conflict');
      } else {
        notify(err.message || 'Unable to reject revision.', 'error');
      }
    } finally {
      setIsDeciding(false);
    }
  };

  const handleRefreshTrip = async () => {
    try {
      const fresh = await api.getTrip(trip.trip_id);
      onTripUpdated(fresh);
      setStartDate(fresh.start_date || '');
      setEndDate(fresh.end_date || '');
      setTravelers(fresh.travelers_count || 2);
      setBudget(fresh.budget ? String(fresh.budget) : '');
      setConflictError(null);
      notify('Trip workspace synchronized with latest revision.', 'info', 'Trip Refreshed');
    } catch (err: any) {
      notify('Failed to refresh trip.', 'error');
    }
  };

  // Derived current review status
  const currentStatus = currentRevision?.status || (trip.is_approved ? 'APPROVED' : 'PROPOSED');

  return (
    <div className="space-y-8">
      {/* Active Proposal Review Header / HITL Banner */}
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
                Active Plan Proposal
              </span>
              <span className="font-mono text-xs font-bold text-[var(--text-primary)]">
                Revision #{currentRevisionNumber}
              </span>
              {currentRevision?.parent_revision_id && (
                <span className="font-mono text-xs text-[var(--text-muted)]">
                  (Parent: {currentRevision.parent_revision_id.slice(0, 8)}...)
                </span>
              )}
            </div>
            <h2 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              Proposal Review & Human-in-the-Loop Decision
            </h2>
          </div>

          {/* Status Badge */}
          <div className="flex items-center gap-3">
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold font-mono uppercase tracking-wider flex items-center gap-1.5 ${
                currentStatus === 'APPROVED'
                  ? 'bg-[var(--status-success)]/15 text-[var(--status-success)] border border-[var(--status-success)]/30'
                  : currentStatus === 'REJECTED'
                  ? 'bg-[var(--status-danger)]/15 text-[var(--status-danger)] border border-[var(--status-danger)]/30'
                  : 'bg-[var(--primary-soft)] text-[var(--primary)] border border-[var(--primary)]/30'
              }`}
            >
              {currentStatus === 'APPROVED' && <CheckCircle2 className="w-3.5 h-3.5" />}
              {currentStatus === 'REJECTED' && <XCircle className="w-3.5 h-3.5" />}
              {currentStatus === 'PROPOSED' && <Clock className="w-3.5 h-3.5" />}
              {currentStatus}
            </span>
          </div>
        </div>

        {/* Structured Concrete Diff (Added, Removed, Changed) */}
        {currentRevision?.structured_diff && (
          <div className="space-y-3 pt-1">
            <h4 className="text-xs font-semibold text-[var(--text-secondary)] font-mono uppercase tracking-wider">
              Structured Concrete Changes In This Proposal
            </h4>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {/* Added Items */}
              <div className="p-3.5 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs font-bold text-[var(--status-success)] font-mono uppercase">
                  <PlusCircle className="w-3.5 h-3.5" /> Added ({currentRevision.structured_diff.added.length})
                </div>
                {currentRevision.structured_diff.added.length === 0 ? (
                  <p className="text-xs text-[var(--text-muted)] italic">No items added</p>
                ) : (
                  <ul className="text-xs space-y-1 text-[var(--text-primary)]">
                    {currentRevision.structured_diff.added.map((item, idx) => (
                      <li key={idx} className="font-mono text-[11px]">
                        • {item.label || item.field}: <span className="font-semibold">{String(item.value || item.new_value)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Removed Items */}
              <div className="p-3.5 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs font-bold text-[var(--status-danger)] font-mono uppercase">
                  <MinusCircle className="w-3.5 h-3.5" /> Removed ({currentRevision.structured_diff.removed.length})
                </div>
                {currentRevision.structured_diff.removed.length === 0 ? (
                  <p className="text-xs text-[var(--text-muted)] italic">No items removed</p>
                ) : (
                  <ul className="text-xs space-y-1 text-[var(--text-primary)]">
                    {currentRevision.structured_diff.removed.map((item, idx) => (
                      <li key={idx} className="font-mono text-[11px]">
                        • {item.label || item.field} (was {String(item.old_value || item.value)})
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Changed Items */}
              <div className="p-3.5 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs font-bold text-[var(--primary)] font-mono uppercase">
                  <GitCommit className="w-3.5 h-3.5" /> Changed ({currentRevision.structured_diff.changed.length})
                </div>
                {currentRevision.structured_diff.changed.length === 0 ? (
                  <p className="text-xs text-[var(--text-muted)] italic">No items modified</p>
                ) : (
                  <ul className="text-xs space-y-1 text-[var(--text-primary)]">
                    {currentRevision.structured_diff.changed.map((item, idx) => (
                      <li key={idx} className="font-mono text-[11px]">
                        • {item.label || item.field}: <span className="text-[var(--text-muted)] line-through">{String(item.old_value)}</span> ➔ <span className="font-semibold text-[var(--primary)]">{String(item.new_value)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}

        {/* HITL Decision Controls */}
        <div className="pt-2 flex flex-wrap items-center justify-between gap-4 border-t border-[var(--border)]">
          <div className="text-xs text-[var(--text-secondary)] font-light max-w-md">
            <span className="font-semibold text-[var(--text-primary)]">Notice:</span> Approving this proposal accepts this exact itinerary and trip revision. It does not authorize bookings, issue flight tickets, reserve hotel rooms, or trigger payments.
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {currentStatus === 'PROPOSED' && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowRejectModal(true)}
                  disabled={isDeciding}
                  className="border-[var(--status-danger)]/40 text-[var(--status-danger)] hover:bg-[var(--status-danger)]/10"
                >
                  Reject Proposal
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleApprove}
                  isLoading={isDeciding}
                  leftIcon={<ShieldCheck className="w-4 h-4" />}
                >
                  Accept this trip revision
                </Button>
              </>
            )}

            {currentStatus === 'REJECTED' && (
              <div className="text-xs font-mono text-[var(--status-danger)]">
                Proposal rejected. Submit feedback below to create a new child revision.
              </div>
            )}

            {currentStatus === 'APPROVED' && (
              <div className="text-xs font-mono text-[var(--status-success)] flex items-center gap-1.5 font-bold">
                <CheckCircle2 className="w-4 h-4" /> This proposal is verified and accepted.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Reject Modal / Confirmation */}
      {showRejectModal && (
        <div className="p-6 rounded-3xl bg-[var(--surface)] border border-[var(--status-danger)]/40 shadow-md space-y-4">
          <div className="flex items-center gap-2 text-sm font-bold text-[var(--status-danger)] font-mono uppercase tracking-wider">
            <XCircle className="w-4 h-4" /> Reject Revision #{currentRevisionNumber}
          </div>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)]">
            Rejecting this proposal keeps it in history as a rejected milestone and lets you request a new child proposal.
          </p>
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-[var(--text-primary)] font-mono">
              Rejection Reason (Optional):
            </label>
            <input
              type="text"
              value={rejectionReason}
              onChange={(e) => setRejectionReason(e.target.value)}
              placeholder="e.g. Budget is too high, prefer 4-star hotel instead."
              className="w-full px-4 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--status-danger)] focus:ring-1 focus:ring-[var(--status-danger)] outline-hidden"
            />
          </div>
          <div className="flex items-center justify-end gap-3 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowRejectModal(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleReject}
              isLoading={isDeciding}
              className="bg-[var(--status-danger)] hover:bg-[var(--status-danger)]/90 text-white"
            >
              Confirm Rejection
            </Button>
          </div>
        </div>
      )}

      {/* Stale Travel Selection Notice Banner */}
      {trip.current_revision?.stale_warnings && trip.current_revision.stale_warnings.length > 0 && (
        <div className="p-6 rounded-3xl bg-[var(--status-warning)]/10 border border-[var(--status-warning)]/30 space-y-3">
          <div className="flex items-center gap-2 text-sm font-bold text-[var(--status-warning)] font-mono uppercase tracking-wider">
            <AlertTriangle className="w-4 h-4" />
            Travel Inventory Verification Required
          </div>
          <p className="text-xs sm:text-sm text-[var(--text-primary)] font-light leading-relaxed">
            Your trip parameters were recently updated in Revision {trip.active_revision_number}.
            Previously locked travel snapshots may require revalidation or updated dates.
          </p>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onNavigateTab('flights')}
              className="border-[var(--status-warning)]/40 text-[var(--text-primary)]"
            >
              Revalidate Flight Offer
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onNavigateTab('hotels')}
              className="border-[var(--status-warning)]/40 text-[var(--text-primary)]"
            >
              Refresh Stay Dates
            </Button>
          </div>
        </div>
      )}

      {/* Conflict Notice Banner (409) */}
      {conflictError && (
        <div className="p-6 rounded-3xl bg-[var(--status-danger)]/10 border border-[var(--status-danger)]/30 space-y-3">
          <div className="flex items-center gap-2 text-sm font-bold text-[var(--status-danger)] font-mono uppercase tracking-wider">
            <AlertTriangle className="w-4 h-4" />
            Revision Conflict (HTTP 409)
          </div>
          <p className="text-xs sm:text-sm text-[var(--text-primary)]">
            {conflictError.message} Active server revision is #{conflictError.serverRevision}.
          </p>
          <Button
            variant="primary"
            size="sm"
            onClick={handleRefreshTrip}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          >
            Refresh Latest Trip Plan
          </Button>
        </div>
      )}

      {/* Grid: Human-in-the-Loop Revision Editor (Left) & Revision History (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* LEFT COLUMN: Human-in-the-Loop Revision Proposer (7 cols) */}
        <div className="lg:col-span-7 bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs space-y-6">
          <div className="border-b border-[var(--border)] pb-4">
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
                Request Another Revision
              </span>
              <span className="text-xs text-[var(--text-muted)] font-mono">
                Base Revision: #{currentRevisionNumber}
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)] mt-1.5">
              Refine Journey Preferences
            </h3>
            <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light mt-0.5">
              Request itinerary pacing adjustments, change travel dates, or adjust passenger counts.
              Submitting creates a new immutable child revision linked to Revision #{currentRevisionNumber}.
            </p>
          </div>

          <form onSubmit={handleCreateRevision} className="space-y-5">
            {/* Feedback / Instructions Prompt */}
            <div className="space-y-2">
              <label className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5 font-mono uppercase tracking-wider">
                <FileEdit className="w-3.5 h-3.5 text-[var(--primary)]" />
                Human Guidance / Pace Feedback
              </label>
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="e.g. I don't want museums every day. I prefer slower mornings and more authentic food experiences."
                rows={3}
                className="w-full p-4 rounded-2xl border border-[var(--border)] bg-[var(--surface)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all shadow-2xs resize-none"
              />

              {/* Feedback Presets */}
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                <span className="text-[11px] text-[var(--text-muted)] font-mono">Quick ideas:</span>
                {feedbackPresets.map((preset, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => setFeedback(preset)}
                    className="text-[11px] px-2.5 py-1 rounded-lg bg-[var(--surface-muted)] hover:bg-[var(--primary-soft)] hover:text-[var(--primary)] text-[var(--text-secondary)] transition-colors text-left"
                  >
                    {preset}
                  </button>
                ))}
              </div>
            </div>

            {/* Travel Dates */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Input
                label="Departure Date"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                required
              />
              <Input
                label="Return Date"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                required
              />
            </div>

            {/* Passengers & Budget */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Input
                label="Travelers Count"
                type="number"
                min="1"
                max="20"
                value={travelers}
                onChange={(e) => setTravelers(Number(e.target.value))}
                required
              />
              <Input
                label={`Budget Limit (${trip.currency || 'INR'})`}
                type="number"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="e.g. 4500"
              />
            </div>

            {/* Preferences Tags */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-[var(--text-secondary)] font-mono">
                Travel Style & Focus Tags (Comma-separated)
              </label>
              <input
                type="text"
                value={preferencesStr}
                onChange={(e) => setPreferencesStr(e.target.value)}
                placeholder="Traditional Machiya, Kaiseki, Quiet Gardens, High-speed Rail"
                className="w-full px-4 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all"
              />
            </div>

            {/* Inventory Staleness Warning if dates altered */}
            {willAlterInventory && (
              <div className="p-3.5 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] text-xs text-[var(--text-secondary)] flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-[var(--terracotta)] shrink-0" />
                <span>
                  Modifying travel dates or passenger count will require revalidating existing flight
                  and stay snapshots.
                </span>
              </div>
            )}

            {/* Submission Button */}
            <div className="pt-3 border-t border-[var(--border)] flex items-center justify-between">
              <span className="text-xs text-[var(--text-muted)] font-mono">
                Will create Revision #{currentRevisionNumber + 1}
              </span>
              <Button
                type="submit"
                variant="primary"
                size="md"
                isLoading={isSubmitting}
                rightIcon={<ArrowRight className="w-4 h-4" />}
              >
                Create New Revision
              </Button>
            </div>
          </form>
        </div>

        {/* RIGHT COLUMN: Your Journey's Story (Revision History) (5 cols) */}
        <div className="lg:col-span-5 bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs space-y-6">
          <div className="border-b border-[var(--border)] pb-4">
            <div className="flex items-center gap-2">
              <History className="w-4 h-4 text-[var(--primary)]" />
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono">
                Revision Audit Trail
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)] mt-1">
              Your Journey's Story
            </h3>
            <p className="text-xs text-[var(--text-secondary)] font-light mt-0.5">
              Every revision and HITL decision is immutably preserved.
            </p>
          </div>

          {/* Timeline List */}
          <div className="space-y-4">
            {revisions
              .slice()
              .reverse()
              .map((rev) => {
                const isCurrent = rev.revision_number === currentRevisionNumber;
                const revStatus = rev.status || (rev.is_approved ? 'APPROVED' : (rev.is_rejected ? 'REJECTED' : 'PROPOSED'));
                return (
                  <div
                    key={rev.revision_id}
                    onClick={() => setSelectedHistoricalRev(rev)}
                    className={`p-4 rounded-2xl border transition-all cursor-pointer ${
                      isCurrent
                        ? 'border-[var(--primary)] bg-[var(--primary-soft)]/20 shadow-xs'
                        : 'border-[var(--border)] hover:border-[var(--border-strong)] bg-[var(--surface-muted)]/30'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-2">
                        <span
                          className={`font-mono text-xs font-bold px-2 py-0.5 rounded ${
                            isCurrent
                              ? 'bg-[var(--primary)] text-white'
                              : 'bg-[var(--surface)] text-[var(--text-secondary)] border border-[var(--border)]'
                          }`}
                        >
                          Rev {rev.revision_number}
                        </span>
                        <span
                          className={`text-[10px] font-bold font-mono px-2 py-0.5 rounded-full ${
                            revStatus === 'APPROVED'
                              ? 'bg-[var(--status-success)]/15 text-[var(--status-success)]'
                              : revStatus === 'REJECTED'
                              ? 'bg-[var(--status-danger)]/15 text-[var(--status-danger)]'
                              : 'bg-[var(--primary-soft)] text-[var(--primary)]'
                          }`}
                        >
                          {revStatus}
                        </span>
                        {isCurrent && (
                          <span className="text-[11px] font-semibold text-[var(--primary)] font-mono">
                            Latest
                          </span>
                        )}
                      </div>

                      <span className="text-[10px] text-[var(--text-muted)] font-mono">
                        {new Date(rev.created_at).toLocaleDateString([], {
                          month: 'short',
                          day: 'numeric',
                        })}
                      </span>
                    </div>

                    <p className="text-xs font-semibold text-[var(--text-primary)] font-editorial line-clamp-2">
                      {rev.change_summary}
                    </p>

                    {rev.rejection_reason && (
                      <p className="text-[11px] text-[var(--status-danger)] font-mono mt-1">
                        Reason: {rev.rejection_reason}
                      </p>
                    )}

                    <div className="flex items-center justify-between text-[11px] text-[var(--text-secondary)] font-mono pt-2 mt-2 border-t border-[var(--border)]/60">
                      <span>{rev.start_date} to {rev.end_date}</span>
                      <span>{rev.travelers_count || 2} pax</span>
                    </div>
                  </div>
                );
              })}
          </div>

          {/* Revision Comparison Inspector (when historical rev selected) */}
          {selectedHistoricalRev && selectedHistoricalRev.revision_number !== currentRevisionNumber && (
            <div className="p-4 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] space-y-3 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-[var(--text-primary)] font-mono uppercase tracking-wider">
                  Comparing Rev {selectedHistoricalRev.revision_number} vs Current Rev {currentRevisionNumber}
                </span>
              </div>

              <div className="space-y-1.5 text-[var(--text-secondary)]">
                <div className="flex justify-between">
                  <span>Previously:</span>
                  <span className="font-mono text-[var(--text-primary)]">
                    {selectedHistoricalRev.start_date} ({selectedHistoricalRev.travelers_count || 1} pax)
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Current:</span>
                  <span className="font-mono text-[var(--primary)] font-bold">
                    {trip.start_date} ({trip.travelers_count || 1} pax)
                  </span>
                </div>
                {selectedHistoricalRev.budget !== trip.budget && (
                  <div className="flex justify-between pt-1 border-t border-[var(--border)]/70">
                    <span>Budget Shift:</span>
                    <span className="font-mono">
                      {formatCurrency(selectedHistoricalRev.budget || 0, trip.currency || 'INR')} ➔ {formatCurrency(trip.budget || 0, trip.currency || 'INR')}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
