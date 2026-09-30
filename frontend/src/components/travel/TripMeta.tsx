import React from 'react';
import { MapPin, Calendar, Users, Wallet, Sparkles } from 'lucide-react';
import { TripSummary } from '../../types/trip';
import { formatCurrency } from '../../lib/currency';

interface TripMetaProps {
  trip: TripSummary;
  className?: string;
  showAll?: boolean;
}

export const TripMeta: React.FC<TripMetaProps> = ({ trip, className = '', showAll = false }) => {
  const formatDateRange = (start?: string, end?: string) => {
    if (!start) return '';
    try {
      const s = new Date(start).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const e = end ? new Date(end).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
      return `${s} — ${e}`;
    } catch {
      return `${start} - ${end}`;
    }
  };

  return (
    <div className={`flex flex-wrap items-center gap-y-1.5 gap-x-3 text-xs text-[var(--text-secondary)] ${className}`}>
      {trip.origin && (
        <span className="flex items-center gap-1">
          <MapPin className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
          <span className="font-medium text-[var(--text-primary)]">{trip.origin}</span>
          <span className="text-[var(--text-secondary)]">➔</span>
          <span className="font-medium text-[var(--text-primary)]">{trip.destination}</span>
        </span>
      )}

      {trip.origin && <span aria-hidden="true" className="opacity-50">·</span>}

      <span className="flex items-center gap-1">
        <Calendar className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
        <span>{formatDateRange(trip.start_date, trip.end_date)}</span>
      </span>

      <span aria-hidden="true" className="opacity-50">·</span>

      <span className="flex items-center gap-1">
        <Users className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
        <span>
          {trip.travelers_count || 1} {Number(trip.travelers_count || 1) === 1 ? 'traveler' : 'travelers'}
        </span>
      </span>

      {showAll && trip.budget && (
        <>
          <span aria-hidden="true" className="opacity-50">·</span>
          <span className="flex items-center gap-1">
            <Wallet className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
            <span className="font-mono tabular-nums">
              {formatCurrency(trip.budget, trip.currency)}
            </span>
          </span>
        </>
      )}

      {showAll && trip.preferences && trip.preferences.length > 0 && (
        <>
          <span aria-hidden="true" className="opacity-50">·</span>
          <span className="flex items-center gap-1 text-[var(--text-secondary)]">
            <Sparkles className="w-3.5 h-3.5 text-[var(--coral)] shrink-0" />
            <span>{trip.preferences.slice(0, 2).join(', ')}</span>
          </span>
        </>
      )}
    </div>
  );
};
