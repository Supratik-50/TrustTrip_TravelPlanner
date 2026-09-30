import React, { useState } from 'react';
import { Sparkles, ArrowRight, MapPin } from 'lucide-react';
import { TRAVEL_MOODS, TRAVEL_LANDSCAPES } from '../../lib/destinationImages';

interface TravelInspirationProps {
  onSelectMood: (moodId: string, moodLabel: string, defaultDestination?: string) => void;
  onSelectLandscape: (landscapeId: string, landscapeLabel: string, defaultDestination?: string) => void;
}

export const TravelInspiration: React.FC<TravelInspirationProps> = ({
  onSelectMood,
  onSelectLandscape,
}) => {
  const [failedImages, setFailedImages] = useState<Record<string, boolean>>({});

  const handleImageError = (id: string) => {
    setFailedImages((prev) => ({ ...prev, [id]: true }));
  };

  return (
    <div className="space-y-12 py-4">
      {/* Travel Moods Section */}
      <section className="space-y-4">
        <div>
          <div className="text-xs font-semibold text-[var(--primary)] uppercase tracking-wider font-mono">
            Travel Moods & Regions
          </div>
          <h2 className="text-2xl sm:text-3xl font-bold text-[var(--text-primary)] font-editorial tracking-tight mt-1">
            Find Your Travel Rhythm
          </h2>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-0.5">
            Discover destinations shaped around the way you want to feel.
          </p>
        </div>

        {/* Mood Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5 sm:gap-4">
          {TRAVEL_MOODS.map((mood) => {
            const hasError = failedImages[mood.id];
            const defaultDest = mood.places?.[0] || 'Goa, India';

            return (
              <div
                key={mood.id}
                onClick={() => onSelectMood(mood.id, mood.label, defaultDest)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && onSelectMood(mood.id, mood.label, defaultDest)}
                className="group relative h-48 sm:h-56 rounded-2xl overflow-hidden cursor-pointer border border-[var(--border)] shadow-2xs hover:shadow-md transition-all duration-300"
              >
                {!hasError ? (
                  <img
                    src={mood.image}
                    alt={mood.label}
                    onError={() => handleImageError(mood.id)}
                    className="absolute inset-0 w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500 ease-out"
                    loading="lazy"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <div className="absolute inset-0 bg-gradient-to-tr from-[#16272B] via-[#2C7A78] to-[#C96F52] flex items-center justify-center p-4 text-center">
                    <span className="font-editorial text-lg text-white font-bold">{mood.label}</span>
                  </div>
                )}
                <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/35 to-transparent group-hover:from-black/90 transition-colors" />

                <div className="absolute inset-0 p-4 flex flex-col justify-end text-white z-10 space-y-1">
                  <span className="text-[10px] font-mono text-[#E7A07F] uppercase tracking-wider font-semibold">
                    {mood.label}
                  </span>
                  <h4 className="text-base font-bold font-editorial leading-snug text-white group-hover:text-[#E7A07F] transition-colors">
                    {mood.label}
                  </h4>
                  <p className="text-[11px] text-white/80 line-clamp-2 leading-relaxed font-light">
                    {mood.count}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Explore by Landscape Strip */}
      <section className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--border)] space-y-4 shadow-xs">
        <div className="flex items-baseline justify-between">
          <div>
            <h3 className="text-sm font-bold text-[var(--text-primary)] uppercase tracking-wider font-mono">
              Explore By Terrain & Atmosphere
            </h3>
            <p className="text-xs text-[var(--text-secondary)] mt-0.5">
              Filter by landscape to find destinations matching your travel preferences.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
          {TRAVEL_LANDSCAPES.map((land) => (
            <button
              key={land.id}
              type="button"
              onClick={() => onSelectLandscape(land.id, land.label, land.destination)}
              className="group flex items-center gap-3.5 p-3 rounded-2xl bg-[var(--background)] hover:bg-[var(--primary-soft)] border border-[var(--border)] transition-all text-left cursor-pointer shadow-2xs hover:shadow-xs"
            >
              <img
                src={land.image}
                alt={land.label}
                className="w-12 h-12 rounded-xl object-cover shrink-0 border border-[var(--border)]"
                loading="lazy"
                referrerPolicy="no-referrer"
              />
              <div className="min-w-0">
                <div className="text-sm font-semibold text-[var(--text-primary)] group-hover:text-[var(--primary)] truncate transition-colors">
                  {land.label}
                </div>
                <div className="text-xs text-[var(--text-secondary)] truncate">
                  {land.destination} →
                </div>
              </div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
};
