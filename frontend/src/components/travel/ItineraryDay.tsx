import React from 'react';
import { Plane, Building2, Utensils, Compass, Footprints, Clock, MapPin, Camera } from 'lucide-react';
import { ItineraryDayData, ItineraryActivity } from '../../types/trip';

interface ItineraryDayProps {
  day: ItineraryDayData;
  dayHeroImage?: string;
}

export const ItineraryDay: React.FC<ItineraryDayProps> = ({ day, dayHeroImage }) => {
  const getActivityIcon = (type?: string) => {
    switch (type) {
      case 'flight':
      case 'transit':
        return <Plane className="w-3.5 h-3.5 text-[var(--primary)]" />;
      case 'hotel':
        return <Building2 className="w-3.5 h-3.5 text-[var(--terracotta)]" />;
      case 'meal':
        return <Utensils className="w-3.5 h-3.5 text-[var(--sunset)]" />;
      case 'sightseeing':
        return <Compass className="w-3.5 h-3.5 text-[var(--primary)]" />;
      default:
        return <Footprints className="w-3.5 h-3.5 text-[var(--text-secondary)]" />;
    }
  };

  return (
    <div className="space-y-4">
      {/* Editorial Day Header */}
      <div className="border-b border-[var(--border)] pb-3 pt-2">
        <div className="flex items-baseline justify-between gap-4">
          <div className="space-y-0.5">
            <span className="text-xs font-mono font-bold tracking-widest text-[var(--primary)] uppercase">
              DAY {day.day_number}
            </span>
            <h4 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {day.title}
            </h4>
          </div>

          {day.date && (
            <span className="text-xs font-mono text-[var(--text-secondary)]">
              {new Date(day.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            </span>
          )}
        </div>

        {day.theme && (
          <p className="text-xs text-[var(--text-secondary)] italic mt-1 font-light">
            &ldquo;{day.theme}&rdquo;
          </p>
        )}
      </div>

      {/* Visual Variation: Optional Day Scenic Highlight */}
      {dayHeroImage && (
        <div className="relative h-44 rounded-2xl overflow-hidden border border-[var(--border)] shadow-2xs">
          <img
            src={dayHeroImage}
            alt={day.title}
            className="w-full h-full object-cover"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent" />
          <div className="absolute bottom-3 left-4 text-xs font-medium text-white flex items-center gap-1.5">
            <Camera className="w-3.5 h-3.5 text-[#E7A07F]" />
            <span>Day {day.day_number} Atmosphere</span>
          </div>
        </div>
      )}

      {/* Varied Timeline Activities */}
      <div className="relative pl-6 space-y-4 border-l-2 border-[var(--sand)]/80 ml-3.5 my-3">
        {day.activities.map((act: ItineraryActivity, idx: number) => {
          const isHighlight = act.type === 'sightseeing' || act.type === 'meal';

          return (
            <div key={idx} className="relative group">
              {/* Timeline Pin Node */}
              <div className="absolute -left-[33px] top-2 w-4 h-4 rounded-full bg-[var(--surface)] border-2 border-[var(--primary)] flex items-center justify-center shadow-2xs">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--primary)]" />
              </div>

              {/* Activity Body */}
              <div
                className={`p-4 rounded-2xl transition-all duration-200 ${
                  isHighlight
                    ? 'bg-[var(--surface-elevated)] border border-[var(--border)] shadow-xs hover:border-[var(--primary)]/60'
                    : 'bg-[var(--surface)]/70 border border-[var(--border-subtle)] hover:bg-[var(--surface)]'
                }`}
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 pb-1">
                  <div className="flex items-center gap-2">
                    <span className="p-1 rounded-md bg-[var(--primary-soft)] shrink-0">
                      {getActivityIcon(act.type)}
                    </span>
                    <span className="text-xs font-bold font-mono text-[var(--text-primary)] tabular-nums">
                      {act.time || 'Flexible'}
                    </span>
                    <span aria-hidden="true" className="text-[var(--text-secondary)] opacity-40">·</span>
                    <span className="text-sm font-semibold text-[var(--text-primary)]">
                      {act.title}
                    </span>
                  </div>

                  {act.estimated_cost && (
                    <span className="text-[11px] font-mono text-[var(--text-secondary)] tabular-nums shrink-0 pl-7 sm:pl-0">
                      {act.estimated_cost}
                    </span>
                  )}
                </div>

                {act.description && (
                  <p className="text-xs text-[var(--text-secondary)] leading-relaxed pl-7 pt-0.5 font-light">
                    {act.description}
                  </p>
                )}

                {act.location && (
                  <div className="flex items-center gap-1.5 text-[11px] text-[var(--primary)] font-medium pl-7 pt-1.5">
                    <MapPin className="w-3 h-3 shrink-0" />
                    <span>📍 {act.location}</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
