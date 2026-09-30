import React, { useState } from 'react';
import { Calendar, Users, ArrowRight, MapPin, Sparkles } from 'lucide-react';
import { Button } from '../common/Button';
import { DestinationImageMeta } from '../../lib/destinationImages';

interface DestinationHeroProps {
  destination: DestinationImageMeta;
  titleOverride?: string;
  subtitleOverride?: string;
  dateRange?: string;
  travelersCount?: number;
  onActionClick: () => void;
  actionLabel?: string;
}

export const DestinationHero: React.FC<DestinationHeroProps> = ({
  destination,
  titleOverride,
  subtitleOverride,
  dateRange = '14 Oct — 24 Oct 2026',
  travelersCount = 2,
  onActionClick,
  actionLabel = 'Plan this trip',
}) => {
  const [imgError, setImgError] = useState(false);

  return (
    <div className="relative w-full rounded-3xl overflow-hidden shadow-md border border-[var(--border)] min-h-[420px] sm:min-h-[480px] flex flex-col justify-end p-6 sm:p-12 transition-all duration-300">
      {/* Background Image & Scrim */}
      {!imgError ? (
        <img
          src={destination.imageUrl}
          alt={destination.name}
          onError={() => setImgError(true)}
          className="absolute inset-0 w-full h-full object-cover object-center transform hover:scale-[1.02] transition-transform duration-700 ease-out"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div className={`absolute inset-0 bg-gradient-to-tr ${destination.fallbackGradient}`} />
      )}

      {/* Measured Contrast Scrim: Ensures WCAG AAA Legibility without dimming photography completely */}
      <div className="absolute inset-0 bg-gradient-to-t from-[#111817]/90 via-[#111817]/40 to-black/10" />

      {/* Hero Content */}
      <div className="relative z-10 space-y-4 max-w-2xl text-white">
        <div className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[#E7A07F] font-mono">
          <MapPin className="w-3.5 h-3.5" />
          <span>{destination.country}</span>
          <span aria-hidden="true">·</span>
          <span>{destination.mood}</span>
        </div>

        <div>
          <h1 className="text-4xl sm:text-6xl font-bold tracking-tight font-editorial leading-none text-white drop-shadow-sm">
            {titleOverride || destination.name.toUpperCase()}
          </h1>
          <p className="text-base sm:text-xl text-[#E9EEF0] font-light mt-2 max-w-lg leading-relaxed text-balance">
            {subtitleOverride || destination.tagline}
          </p>
        </div>

        {/* Travel Metadata Chips */}
        <div className="flex flex-wrap items-center gap-y-2 gap-x-4 text-xs text-[#E9EEF0]/90 pt-1">
          <span className="flex items-center gap-1.5 bg-white/15 backdrop-blur-md px-3 py-1 rounded-lg">
            <Calendar className="w-3.5 h-3.5 text-[#E7A07F]" />
            <span>{dateRange}</span>
          </span>

          <span className="flex items-center gap-1.5 bg-white/15 backdrop-blur-md px-3 py-1 rounded-lg">
            <Users className="w-3.5 h-3.5 text-[#E7A07F]" />
            <span>{travelersCount} travelers</span>
          </span>

          <span className="hidden sm:inline-flex items-center gap-1.5 bg-white/15 backdrop-blur-md px-3 py-1 rounded-lg">
            <Sparkles className="w-3.5 h-3.5 text-[#E7A07F]" />
            <span>Curated Timeline</span>
          </span>
        </div>

        <div className="pt-2">
          <Button
            variant="accent"
            size="lg"
            onClick={onActionClick}
            className="shadow-lg hover:shadow-xl font-semibold"
            rightIcon={<ArrowRight className="w-4 h-4" />}
          >
            {actionLabel}
          </Button>
        </div>
      </div>
    </div>
  );
};
