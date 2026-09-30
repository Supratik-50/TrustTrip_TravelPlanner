import React from 'react';
import { ArrowLeft, Edit3, Compass, CheckCircle2, ShieldCheck } from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { Button } from '../common/Button';
import { TripMeta } from './TripMeta';

interface TripHeaderProps {
  trip: TripDetail;
  onBack: () => void;
  onEditConstraints: () => void;
}

export const TripHeader: React.FC<TripHeaderProps> = ({
  trip,
  onBack,
  onEditConstraints,
}) => {
  return (
    <div className="space-y-4 pb-6 border-b border-[var(--border)]">
      <div className="flex items-center justify-between gap-4">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Trips</span>
        </button>

        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={onEditConstraints}
            leftIcon={<Edit3 className="w-3.5 h-3.5" />}
          >
            Edit Constraints
          </Button>
        </div>
      </div>

      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold text-[var(--primary)] capitalize flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-[var(--primary)]" />
              {trip.status} Trip
            </span>
            <span aria-hidden="true" className="text-[var(--text-secondary)] opacity-40">·</span>
            <span className="text-[var(--text-secondary)] font-mono text-[11px]">
              Your latest plan · Revision {trip.active_revision_number || 1}
            </span>
            <span aria-hidden="true" className="text-[var(--text-secondary)] opacity-40">·</span>
            <span className="text-[var(--text-secondary)] font-mono text-[11px]">
              Thread: {trip.thread_id || 'Canonical'}
            </span>
          </div>

          <h1 className="text-3xl sm:text-4xl font-bold text-[var(--text-primary)] font-editorial tracking-tight">
            {trip.destination}
          </h1>

          <TripMeta trip={trip} showAll className="pt-1" />
        </div>
      </div>
    </div>
  );
};
