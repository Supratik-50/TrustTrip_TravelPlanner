import React, { useState } from 'react';
import { ArrowRight, MapPin } from 'lucide-react';
import { DestinationImageMeta } from '../../lib/destinationImages';

interface DestinationCardProps {
  destination: DestinationImageMeta;
  onClick: () => void;
}

export const DestinationCard: React.FC<DestinationCardProps> = ({ destination, onClick }) => {
  const [imgError, setImgError] = useState(false);
  const [imgLoaded, setImgLoaded] = useState(false);

  // Region / State display logic:
  // e.g., "Darjeeling, West Bengal" or "Goa, India" or "Srinagar, Jammu & Kashmir"
  const locationSubtitle =
    destination.country === 'India'
      ? destination.region && destination.region !== destination.name
        ? destination.region
        : 'India'
      : destination.country;

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
      className="group relative flex-shrink-0 w-64 sm:w-72 h-84 rounded-2xl overflow-hidden cursor-pointer border border-[var(--border)] shadow-xs hover:shadow-md transition-all duration-300"
    >
      {/* Background Image / Graceful Fallback */}
      {!imgError && destination.imageUrl ? (
        <>
          <img
            src={destination.imageUrl}
            alt={destination.name}
            onError={() => setImgError(true)}
            onLoad={() => setImgLoaded(true)}
            className={`absolute inset-0 w-full h-full object-cover object-center group-hover:scale-105 transition-all duration-700 ease-out ${
              imgLoaded ? 'opacity-100' : 'opacity-0'
            }`}
            referrerPolicy="no-referrer"
            loading="lazy"
          />
          {/* While loading, show soft gradient placeholder */}
          {!imgLoaded && (
            <div
              className={`absolute inset-0 bg-gradient-to-tr ${destination.fallbackGradient} animate-pulse flex items-center justify-center p-4 text-center`}
            >
              <span className="font-editorial text-xl font-bold text-white/60">
                {destination.name}
              </span>
            </div>
          )}
        </>
      ) : (
        /* Fallback on error: soft travel-colored gradient + destination name */
        <div
          className={`absolute inset-0 bg-gradient-to-tr ${destination.fallbackGradient} flex flex-col items-center justify-center p-6 text-center text-white`}
        >
          <MapPin className="w-8 h-8 text-[var(--primary)] mb-2 opacity-80" />
          <span className="font-editorial text-2xl font-bold tracking-tight">
            {destination.name}
          </span>
          <span className="text-xs text-white/80 mt-1 uppercase font-mono tracking-wider">
            {locationSubtitle}
          </span>
        </div>
      )}

      {/* Subtle Scrim — contrast protection only behind text */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/35 to-transparent group-hover:from-black/90 transition-colors" />

      {/* Content */}
      <div className="absolute inset-0 p-5 flex flex-col justify-between text-white z-10">
        {/* Top Badges */}
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-mono font-medium tracking-wider uppercase text-[#E7A07F] bg-black/40 backdrop-blur-xs px-2.5 py-0.5 rounded-md border border-white/10">
            {locationSubtitle}
          </span>
          <span className="text-[10px] text-white/85 uppercase font-mono bg-black/30 backdrop-blur-xs px-2 py-0.5 rounded-md">
            {destination.landscape}
          </span>
        </div>

        {/* Bottom Destination Details */}
        <div className="space-y-1.5">
          <h3 className="text-2xl font-bold font-editorial text-white tracking-tight leading-none group-hover:text-[#E7A07F] transition-colors">
            {destination.name}
          </h3>
          <p className="text-xs text-white/85 line-clamp-2 leading-relaxed font-light">
            {destination.tagline}
          </p>

          <div className="pt-2 flex items-center justify-between text-xs font-semibold text-[#E7A07F]">
            <span className="group-hover:translate-x-1 transition-transform inline-flex items-center gap-1">
              Start planning
              <ArrowRight className="w-3.5 h-3.5" />
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
