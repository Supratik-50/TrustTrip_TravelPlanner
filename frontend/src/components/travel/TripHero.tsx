import React, { useState } from 'react';
import { ArrowLeft, Edit3, Calendar, Users, MapPin, Wallet, Sparkles, Compass } from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { getDestinationImage } from '../../lib/destinationImages';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface TripHeroProps {
  trip: TripDetail;
  onBack: () => void;
  onEditConstraints: () => void;
}

export const TripHero: React.FC<TripHeroProps> = ({
  trip,
  onBack,
  onEditConstraints,
}) => {
  const [imgError, setImgError] = useState(false);
  const destMeta = getDestinationImage(trip.destination);

  const calculateDays = () => {
    if (!trip.start_date || !trip.end_date) return '6 days';
    try {
      const s = new Date(trip.start_date).getTime();
      const e = new Date(trip.end_date).getTime();
      const diff = Math.ceil((e - s) / (1000 * 60 * 60 * 24));
      return `${Math.max(1, diff)} days`;
    } catch {
      return 'Journey';
    }
  };

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
    <div className="space-y-4">
      {/* Top Bar Back & Actions */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Trips</span>
        </button>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onEditConstraints}
            leftIcon={<Edit3 className="w-3.5 h-3.5" />}
          >
            Edit Trip Parameters
          </Button>
        </div>
      </div>

      {/* Hero Visual Card */}
      <div className="relative rounded-3xl overflow-hidden border border-[var(--border)] shadow-md min-h-[300px] sm:min-h-[360px] flex flex-col justify-end p-6 sm:p-10 text-white">
        {!imgError ? (
          <img
            src={destMeta.imageUrl}
            alt={trip.destination}
            onError={() => setImgError(true)}
            className="absolute inset-0 w-full h-full object-cover object-center"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className={`absolute inset-0 bg-gradient-to-tr ${destMeta.fallbackGradient}`} />
        )}

        <div className="absolute inset-0 bg-gradient-to-t from-[#111817]/95 via-[#111817]/40 to-black/10" />

        <div className="relative z-10 space-y-3 max-w-2xl">
          <div className="flex items-center gap-2 text-xs">
            <span className="bg-[#176B63] px-2.5 py-0.5 rounded text-[11px] font-mono tracking-wider text-white font-semibold">
              Your latest plan · Revision {trip.active_revision_number || 1}
            </span>
            <span aria-hidden="true" className="text-white/40">·</span>
            <span className="text-[#E7A07F] font-mono text-[11px] flex items-center gap-1">
              <Compass className="w-3.5 h-3.5" />
              Verified Plan
            </span>
          </div>

          <div>
            <h1 className="text-4xl sm:text-6xl font-bold font-editorial tracking-tight text-white leading-none">
              {trip.destination}
            </h1>
            <p className="text-sm sm:text-base text-[#E9EEF0] font-light mt-1.5">
              {trip.origin} ➔ {trip.destination} · {calculateDays()}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-y-2 gap-x-3 text-xs text-[#E9EEF0]/90 pt-1">
            <span className="flex items-center gap-1.5 bg-black/35 backdrop-blur-md px-3 py-1 rounded-lg">
              <Calendar className="w-3.5 h-3.5 text-[#E7A07F]" />
              <span>{formatDateRange(trip.start_date, trip.end_date)}</span>
            </span>

            <span className="flex items-center gap-1.5 bg-black/35 backdrop-blur-md px-3 py-1 rounded-lg">
              <Users className="w-3.5 h-3.5 text-[#E7A07F]" />
              <span>{trip.travelers_count || 1} travelers</span>
            </span>

            {trip.budget && (
              <span className="flex items-center gap-1.5 bg-black/35 backdrop-blur-md px-3 py-1 rounded-lg font-mono tabular-nums">
                <Wallet className="w-3.5 h-3.5 text-[#E7A07F]" />
                <span>Budget: {formatCurrency(trip.budget, trip.currency)}</span>
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
