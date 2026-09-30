import React from 'react';
import { MapPin, Navigation, Compass, ExternalLink } from 'lucide-react';

interface MapPreviewProps {
  destination: string;
  places?: { name: string; distance?: string; category?: string }[];
}

export const MapPreview: React.FC<MapPreviewProps> = ({
  destination,
  places = [
    { name: 'Historic Quarter & Old Promenade', distance: '1.2 km', category: 'Culture' },
    { name: 'Central Artisan Market & Cafés', distance: '2.4 km', category: 'Dining' },
    { name: 'Panoramic River Viewpoint', distance: '3.1 km', category: 'Scenic' },
  ],
}) => {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] overflow-hidden shadow-xs space-y-3">
      {/* Visual Stylized Cartographic Canvas */}
      <div className="relative h-36 bg-[var(--background)] p-4 flex flex-col justify-between overflow-hidden border-b border-[var(--border-subtle)]">
        {/* Subtle decorative grid/contour lines */}
        <div className="absolute inset-0 opacity-15 dark:opacity-10 bg-[radial-gradient(#176B63_1px,transparent_1px)] [background-size:16px_16px]" />
        <div className="absolute -right-8 -top-8 w-32 h-32 rounded-full border border-[var(--primary)]/20" />
        <div className="absolute -right-4 -top-4 w-24 h-24 rounded-full border border-[var(--primary)]/25" />

        <div className="relative z-10 flex items-center justify-between text-xs">
          <span className="font-semibold text-[var(--primary)] flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider">
            <Compass className="w-3.5 h-3.5" />
            <span>Map Preview · {destination}</span>
          </span>
          <span className="text-[10px] text-[var(--text-secondary)] font-mono">
            Spatial Anchor
          </span>
        </div>

        {/* Center Pin Indicator */}
        <div className="relative z-10 flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-[var(--primary)] text-white flex items-center justify-center shadow-md animate-bounce">
            <MapPin className="w-4 h-4" />
          </div>
          <div>
            <div className="text-xs font-bold text-[var(--text-primary)]">{destination}</div>
            <div className="text-[10px] text-[var(--text-secondary)]">Exploration Zone Center</div>
          </div>
        </div>
      </div>

      {/* Nearby Highlight Markers */}
      <div className="p-4 pt-1 space-y-2">
        <div className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
          Nearby Landmarks
        </div>
        <div className="space-y-1.5">
          {places.map((place, idx) => (
            <div
              key={idx}
              className="flex items-center justify-between text-xs py-1.5 px-2.5 rounded-lg bg-[var(--background)] hover:bg-[var(--primary-soft)] transition-colors"
            >
              <span className="flex items-center gap-2 font-medium text-[var(--text-primary)]">
                <Navigation className="w-3 h-3 text-[var(--primary)]" />
                <span>{place.name}</span>
              </span>
              <span className="text-[11px] font-mono text-[var(--text-secondary)] tabular-nums">
                {place.distance}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
