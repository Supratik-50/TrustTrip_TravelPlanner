import React, { useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Compass, Sparkles } from 'lucide-react';
import {
  DESTINATION_CATALOG,
  INDIAN_FEATURED_DESTINATIONS,
  INTERNATIONAL_FEATURED_DESTINATIONS,
  DestinationImageMeta,
} from '../../lib/destinationImages';
import { DestinationCard } from './DestinationCard';

interface DestinationRailProps {
  onSelectDestination: (destination: DestinationImageMeta) => void;
  destinations?: DestinationImageMeta[];
  title?: string;
  subtitle?: string;
  categoryBadge?: string;
  showTabs?: boolean;
}

export const DestinationRail: React.FC<DestinationRailProps> = ({
  onSelectDestination,
  destinations: customDestinations,
  title = 'Where will you go next?',
  subtitle = 'Curated journeys across India and iconic international landscapes.',
  categoryBadge = 'Curated Discoveries',
  showTabs = true,
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [activeTab, setActiveTab] = useState<'all' | 'india' | 'world'>('all');

  // Determine which destinations to display
  let displayedDestinations: DestinationImageMeta[];

  if (customDestinations) {
    displayedDestinations = customDestinations;
  } else if (activeTab === 'india') {
    displayedDestinations = INDIAN_FEATURED_DESTINATIONS;
  } else if (activeTab === 'world') {
    displayedDestinations = INTERNATIONAL_FEATURED_DESTINATIONS;
  } else {
    // Interleaved curated list starting with top Indian destinations followed by world icons
    displayedDestinations = [
      DESTINATION_CATALOG.goa,
      DESTINATION_CATALOG.darjeeling,
      DESTINATION_CATALOG.srinagar,
      DESTINATION_CATALOG.kyoto,
      DESTINATION_CATALOG.kerala,
      DESTINATION_CATALOG.jaipur,
      DESTINATION_CATALOG.paris,
      DESTINATION_CATALOG.gangtok,
      DESTINATION_CATALOG.cherrapunji,
      DESTINATION_CATALOG.udaipur,
      DESTINATION_CATALOG.andaman,
      DESTINATION_CATALOG.tokyo,
      DESTINATION_CATALOG.amalfi,
      DESTINATION_CATALOG.kolkata,
    ];
  }

  const handleScroll = (direction: 'left' | 'right') => {
    if (scrollRef.current) {
      const scrollAmount = direction === 'left' ? -340 : 340;
      scrollRef.current.scrollBy({ left: scrollAmount, behavior: 'smooth' });
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <div className="text-xs font-semibold text-[var(--primary)] uppercase tracking-wider font-mono flex items-center gap-1.5">
            <Compass className="w-3.5 h-3.5" />
            <span>{categoryBadge}</span>
          </div>
          <h2 className="text-2xl sm:text-3xl font-bold text-[var(--text-primary)] font-editorial tracking-tight mt-1">
            {title}
          </h2>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-0.5">
            {subtitle}
          </p>
        </div>

        {/* Tab Switcher & Navigation Controls */}
        <div className="flex items-center gap-3">
          {showTabs && !customDestinations && (
            <div className="flex items-center gap-1 p-1 bg-[var(--surface)] border border-[var(--border)] rounded-xl text-xs">
              <button
                type="button"
                onClick={() => setActiveTab('all')}
                className={`px-3 py-1 rounded-lg font-medium transition-all cursor-pointer ${
                  activeTab === 'all'
                    ? 'bg-[var(--primary)] text-white shadow-2xs font-semibold'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                All Destinations
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('india')}
                className={`px-3 py-1 rounded-lg font-medium transition-all cursor-pointer ${
                  activeTab === 'india'
                    ? 'bg-[var(--primary)] text-white shadow-2xs font-semibold'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                Explore India
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('world')}
                className={`px-3 py-1 rounded-lg font-medium transition-all cursor-pointer ${
                  activeTab === 'world'
                    ? 'bg-[var(--primary)] text-white shadow-2xs font-semibold'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                Explore the World
              </button>
            </div>
          )}

          {/* Scroll Arrows for Desktop */}
          <div className="hidden sm:flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => handleScroll('left')}
              aria-label="Scroll left"
              className="p-2 rounded-full border border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--primary)] transition-colors cursor-pointer"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={() => handleScroll('right')}
              aria-label="Scroll right"
              className="p-2 rounded-full border border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--primary)] transition-colors cursor-pointer"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Horizontal Scroll Rail */}
      <div
        ref={scrollRef}
        className="flex items-center gap-5 overflow-x-auto pb-4 pt-1 no-scrollbar scroll-smooth"
      >
        {displayedDestinations.map((dest) => (
          <DestinationCard
            key={`${dest.name}-${dest.region}`}
            destination={dest}
            onClick={() => onSelectDestination(dest)}
          />
        ))}
      </div>
    </section>
  );
};
