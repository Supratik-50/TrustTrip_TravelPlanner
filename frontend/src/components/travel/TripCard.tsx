import React, { useState } from 'react';
import { ArrowRight, Calendar, Users, MapPin } from 'lucide-react';
import { TripSummary } from '../../types/trip';
import { getDestinationImage } from '../../lib/destinationImages';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface TripCardProps {
  trip: TripSummary;
  onOpen: (tripId: string) => void;
  onContinuePlanning?: (tripId: string) => void;
  featured?: boolean;
}

export const TripCard: React.FC<TripCardProps> = ({
  trip,
  onOpen,
  onContinuePlanning,
  featured = false,
}) => {
  const [imgError, setImgError] = useState(false);
  const destMeta = getDestinationImage(trip.destination);

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

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'approved':
      case 'booked':
        return { label: 'Approved Plan', color: 'text-[var(--status-success)]' };
      case 'planning':
        return { label: 'Planning Workspace', color: 'text-[var(--primary)]' };
      default:
        return { label: 'Draft Itinerary', color: 'text-[var(--text-secondary)]' };
    }
  };

  const statusInfo = getStatusBadge(trip.status);

  if (featured) {
    return (
      <div className="group relative rounded-3xl overflow-hidden border border-[var(--border)] shadow-md min-h-[360px] sm:min-h-[400px] flex flex-col justify-end p-6 sm:p-10 transition-all duration-300">
        {/* Background Image */}
        {!imgError ? (
          <img
            src={destMeta.imageUrl}
            alt={trip.destination}
            onError={() => setImgError(true)}
            className="absolute inset-0 w-full h-full object-cover object-center group-hover:scale-[1.02] transition-transform duration-700 ease-out"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className={`absolute inset-0 bg-gradient-to-tr ${destMeta.fallbackGradient}`} />
        )}

        {/* Contrast Scrim */}
        <div className="absolute inset-0 bg-gradient-to-t from-[#111817]/95 via-[#111817]/45 to-black/10" />

        {/* Content */}
        <div className="relative z-10 flex flex-col md:flex-row md:items-end justify-between gap-6 text-white">
          <div className="space-y-3 max-w-xl">
            <div className="flex items-center gap-2 text-xs">
              <span className="bg-white/20 backdrop-blur-md px-2.5 py-0.5 rounded text-[11px] font-mono tracking-wider uppercase text-[#E7A07F]">
                Active Trip
              </span>
              <span aria-hidden="true" className="text-white/40">·</span>
              <span className="text-white/80 font-mono text-[11px]">Rev {trip.active_revision_number || 1}</span>
            </div>

            <div>
              <h3 className="text-3xl sm:text-5xl font-bold font-editorial tracking-tight text-white leading-none">
                {trip.destination.toUpperCase()}
              </h3>
              <p className="text-sm sm:text-base text-[#E9EEF0] font-light mt-2 line-clamp-1">
                {trip.title || `${trip.destination} Journey`}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-y-1 gap-x-3 text-xs text-[#E9EEF0]/90 pt-1">
              <span className="flex items-center gap-1.5 bg-black/30 backdrop-blur-xs px-2.5 py-1 rounded">
                <Calendar className="w-3.5 h-3.5 text-[#E7A07F]" />
                <span>{formatDateRange(trip.start_date, trip.end_date)}</span>
              </span>
              <span className="flex items-center gap-1.5 bg-black/30 backdrop-blur-xs px-2.5 py-1 rounded">
                <Users className="w-3.5 h-3.5 text-[#E7A07F]" />
                <span>{trip.travelers_count || 1} travelers</span>
              </span>
              {trip.budget && (
                <span className="bg-black/30 backdrop-blur-xs px-2.5 py-1 rounded font-mono tabular-nums">
                  {formatCurrency(trip.budget, trip.currency)}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            {onContinuePlanning && (
              <Button
                variant="outline"
                size="md"
                onClick={() => onContinuePlanning(trip.trip_id)}
                className="bg-white/10 hover:bg-white/20 text-white border-white/30 backdrop-blur-md"
              >
                Continue Planning
              </Button>
            )}
            <Button
              variant="primary"
              size="md"
              onClick={() => onOpen(trip.trip_id)}
              rightIcon={<ArrowRight className="w-4 h-4" />}
            >
              Open Trip
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // Standard Visual Trip Card
  return (
    <div
      onClick={() => onOpen(trip.trip_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onOpen(trip.trip_id)}
      className="group bg-[var(--surface)] rounded-2xl border border-[var(--border)] overflow-hidden shadow-xs hover:shadow-md transition-all duration-300 flex flex-col justify-between cursor-pointer"
    >
      {/* Visual Header Image */}
      <div className="relative h-44 sm:h-48 w-full overflow-hidden">
        {!imgError ? (
          <img
            src={destMeta.imageUrl}
            alt={trip.destination}
            onError={() => setImgError(true)}
            className="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500 ease-out"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className={`w-full h-full bg-gradient-to-tr ${destMeta.fallbackGradient}`} />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />

        <div className="absolute inset-0 p-4 flex flex-col justify-between text-white z-10">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono uppercase tracking-wider text-[#E7A07F] bg-black/40 backdrop-blur-xs px-2 py-0.5 rounded">
              Rev {trip.active_revision_number || 1}
            </span>
            <span className="text-[11px] font-medium text-white/90 bg-black/40 backdrop-blur-xs px-2 py-0.5 rounded capitalize">
              {trip.status}
            </span>
          </div>

          <div>
            <h4 className="text-2xl font-bold font-editorial text-white tracking-tight leading-none group-hover:text-[#E7A07F] transition-colors">
              {trip.destination}
            </h4>
            <p className="text-xs text-white/80 line-clamp-1 mt-1">
              {trip.title || `From ${trip.origin}`}
            </p>
          </div>
        </div>
      </div>

      {/* Metadata & Footer */}
      <div className="p-4 space-y-3 flex-1 flex flex-col justify-between">
        <div className="space-y-1 text-xs text-[var(--text-secondary)]">
          <div className="flex items-center gap-1.5">
            <Calendar className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
            <span>{formatDateRange(trip.start_date, trip.end_date)}</span>
          </div>
          <div className="flex items-center justify-between text-[11px] pt-1">
            <span className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
              <span>{trip.travelers_count || 1} travelers</span>
            </span>
            {trip.budget && (
              <span className="font-mono tabular-nums text-[var(--text-secondary)]">
                {formatCurrency(trip.budget, trip.currency)}
              </span>
            )}
          </div>
        </div>

        <div className="pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between text-xs font-semibold text-[var(--primary)]">
          <span className="group-hover:translate-x-0.5 transition-transform inline-flex items-center gap-1">
            Open trip
            <ArrowRight className="w-3.5 h-3.5" />
          </span>

          {onContinuePlanning && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onContinuePlanning(trip.trip_id);
              }}
              className="text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors px-2 py-1 rounded hover:bg-black/5 dark:hover:bg-white/5"
            >
              Plan Itinerary
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
