import React, { useState, useEffect } from 'react';
import { Compass, Plus, ArrowRight, Sparkles, Plane, Building2, BookOpen, ArrowUpRight, Globe, MapPin, UserCheck } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { TripSummary } from '../../types/trip';
import { api } from '../../lib/apiClient';
import { TripCard } from '../travel/TripCard';
import {
  DESTINATION_CATALOG,
  INDIAN_FEATURED_DESTINATIONS,
  INTERNATIONAL_FEATURED_DESTINATIONS,
  DestinationImageMeta,
} from '../../lib/destinationImages';
import { DestinationHero } from '../travel/DestinationHero';
import { DestinationRail } from '../travel/DestinationRail';
import { IndiaTravelStories } from '../travel/IndiaTravelStories';
import { TravelInspiration } from '../travel/TravelInspiration';
import { Button } from '../common/Button';
import { NavTab } from '../common/Header';

interface DashboardViewProps {
  onOpenTrip: (tripId: string) => void;
  onCreateTrip: (prefillDestination?: string) => void;
  onNavigateTab: (tab: NavTab) => void;
}

export const DashboardView: React.FC<DashboardViewProps> = ({
  onOpenTrip,
  onCreateTrip,
  onNavigateTab,
}) => {
  const { user } = useAuth();
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const [isLoadingTrips, setIsLoadingTrips] = useState(true);

  useEffect(() => {
    let mounted = true;
    api.getTrips()
      .then((data) => {
        if (mounted) setTrips(data);
      })
      .catch(() => {
        if (mounted) setTrips([]);
      })
      .finally(() => {
        if (mounted) setIsLoadingTrips(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  // Featured hero destination: Srinagar & Kashmir (fallback when zero trips)
  const heroDestination: DestinationImageMeta = DESTINATION_CATALOG.srinagar;
  const activeUpcomingTrip = trips.length > 0 ? trips[0] : null;

  return (
    <div className="space-y-16 pb-20">
      {/* Authenticated Traveler Status Banner */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 sm:p-5 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs">
        <div className="flex items-center gap-3.5">
          <div className="w-10 h-10 rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)] font-bold text-sm flex items-center justify-center border border-[var(--primary)]/20 font-editorial shrink-0">
            {user?.email ? user.email.charAt(0).toUpperCase() : 'T'}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--primary)] font-mono">
                Authenticated Travel Planner
              </span>
              <span className="inline-flex items-center gap-1 text-[10px] font-medium text-[var(--status-success)] bg-[var(--status-success)]/10 px-2 py-0.5 rounded-full">
                <UserCheck className="w-2.5 h-2.5" />
                Verified
              </span>
            </div>
            <div className="text-sm sm:text-base font-semibold text-[var(--text-primary)] font-editorial mt-0.5">
              Welcome back, <span className="font-mono text-xs font-medium text-[var(--text-secondary)]">{user?.email || 'Traveler'}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2.5 sm:gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onNavigateTab('trips')}
            leftIcon={<Compass className="w-3.5 h-3.5" />}
          >
            My Trips
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => onCreateTrip()}
            leftIcon={<Plus className="w-3.5 h-3.5" />}
          >
            Plan New Journey
          </Button>
        </div>
      </div>

      {/* 1. HERO DESTINATION AREA */}
      <section>
        {activeUpcomingTrip ? (
          <TripCard
            featured={true}
            trip={activeUpcomingTrip}
            onOpen={onOpenTrip}
            onContinuePlanning={onOpenTrip}
          />
        ) : (
          <DestinationHero
            destination={heroDestination}
            titleOverride="SRINAGAR & KASHMIR"
            subtitleOverride="Lakes, mountains and quiet valleys. Shikara dawns on Dal Lake, cedar houseboats and crisp pine trails."
            dateRange="14 Oct — 22 Oct 2026"
            travelersCount={2}
            onActionClick={() => onCreateTrip('Srinagar (Kashmir), India')}
            actionLabel="Plan Kashmir Journey"
          />
        )}
      </section>

      {/* 2. EXPLORE INDIA DISCOVERY RAIL */}
      <section className="space-y-4">
        <DestinationRail
          destinations={INDIAN_FEATURED_DESTINATIONS}
          title="Explore India"
          subtitle="Sunlit coastlines, high Himalayan valleys, royal palaces, and tranquil backwaters."
          categoryBadge="Domestic Journeys"
          showTabs={false}
          onSelectDestination={(dest) => {
            onCreateTrip(`${dest.name}, ${dest.country}`);
          }}
        />
      </section>

      {/* 3. EXPLORE THE WORLD DISCOVERY RAIL */}
      <section className="space-y-4">
        <DestinationRail
          destinations={INTERNATIONAL_FEATURED_DESTINATIONS}
          title="Explore the World"
          subtitle="Iconic global destinations curated for mindful pacing and deep cultural resonance."
          categoryBadge="International Discoveries"
          showTabs={false}
          onSelectDestination={(dest) => {
            onCreateTrip(`${dest.name}, ${dest.country}`);
          }}
        />
      </section>

      {/* 4. INDIA EDITORIAL TRAVEL STORIES */}
      <section>
        <IndiaTravelStories
          onSelectStory={(dest) => onCreateTrip(dest)}
        />
      </section>

      {/* 5. TRAVEL DISCOVERY ENGINES: Flights, Stays & Research */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Flights */}
        <div
          onClick={() => onNavigateTab('flights')}
          className="p-5 rounded-3xl border border-[var(--border)] bg-[var(--surface)] hover:border-[var(--primary)] transition-all cursor-pointer group shadow-xs flex flex-col justify-between"
        >
          <div className="space-y-2">
            <div className="w-10 h-10 rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center">
              <Plane className="w-5 h-5" />
            </div>
            <div className="font-editorial text-lg font-bold text-[var(--text-primary)] group-hover:text-[var(--primary)] transition-colors">
              Flights & Routes
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light leading-relaxed">
              Explore domestic Indian flight presets and global airfare routes. Lock canonical flight offers.
            </p>
          </div>
          <div className="pt-4 flex items-center justify-between text-xs font-semibold text-[var(--primary)]">
            <span>Explore Routes</span>
            <ArrowUpRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
          </div>
        </div>

        {/* Stays */}
        <div
          onClick={() => onNavigateTab('hotels')}
          className="p-5 rounded-3xl border border-[var(--border)] bg-[var(--surface)] hover:border-[var(--primary)] transition-all cursor-pointer group shadow-xs flex flex-col justify-between"
        >
          <div className="space-y-2">
            <div className="w-10 h-10 rounded-2xl bg-[var(--terracotta-soft)] text-[var(--terracotta)] flex items-center justify-center">
              <Building2 className="w-5 h-5" />
            </div>
            <div className="font-editorial text-lg font-bold text-[var(--text-primary)] group-hover:text-[var(--terracotta)] transition-colors">
              Curated Stays & Havelis
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light leading-relaxed">
              Heritage palaces, tea estate bungalows, beachside retreats, and boutique world properties.
            </p>
          </div>
          <div className="pt-4 flex items-center justify-between text-xs font-semibold text-[var(--terracotta)]">
            <span>Discover Stays</span>
            <ArrowUpRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
          </div>
        </div>

        {/* Research Notebook */}
        <div
          onClick={() => onNavigateTab('research')}
          className="p-5 rounded-3xl border border-[var(--border)] bg-[var(--surface)] hover:border-[var(--primary)] transition-all cursor-pointer group shadow-xs flex flex-col justify-between"
        >
          <div className="space-y-2">
            <div className="w-10 h-10 rounded-2xl bg-[var(--surface-muted)] text-[var(--primary)] flex items-center justify-center">
              <BookOpen className="w-5 h-5" />
            </div>
            <div className="font-editorial text-lg font-bold text-[var(--text-primary)] group-hover:text-[var(--primary)] transition-colors">
              Research & Evidence
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-light leading-relaxed">
              Verified regional transit permits, seasonal weather advisories, and cultural guidelines.
            </p>
          </div>
          <div className="pt-4 flex items-center justify-between text-xs font-semibold text-[var(--primary)]">
            <span>Open Notebook</span>
            <ArrowUpRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
          </div>
        </div>
      </section>

      {/* 6. YOUR JOURNEYS (MY TRIPS ENTRY) */}
      <section className="space-y-6">
        <div className="flex items-end justify-between">
          <div>
            <div className="text-xs font-semibold text-[var(--primary)] uppercase tracking-wider font-mono">
              Travel Portfolio
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-[var(--text-primary)] font-editorial tracking-tight mt-1">
              Your Journeys
            </h2>
            <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-0.5">
              Personalized itineraries, booking vouchers, and active trip workspaces.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onNavigateTab('trips')}
              leftIcon={<Compass className="w-3.5 h-3.5" />}
            >
              View My Trips
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onCreateTrip()}
              leftIcon={<Plus className="w-3.5 h-3.5" />}
            >
              New Journey
            </Button>
          </div>
        </div>

        {/* Real Trips Grid or Travel-First Empty / Start Planning State */}
        {trips.length > 0 ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {trips.slice(0, 3).map((trip) => (
                <TripCard
                  key={trip.trip_id}
                  trip={trip}
                  onOpen={onOpenTrip}
                  onContinuePlanning={onOpenTrip}
                />
              ))}
            </div>
            {trips.length > 3 && (
              <div className="pt-2 text-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onNavigateTab('trips')}
                  rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
                >
                  View All {trips.length} Journeys
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div className="relative rounded-3xl overflow-hidden border border-[var(--border)] p-8 sm:p-14 text-center bg-gradient-to-b from-[var(--surface)] to-[var(--background)] space-y-5">
            <div className="max-w-md mx-auto space-y-2">
              <span className="text-xs font-mono uppercase tracking-widest text-[var(--primary)] font-semibold">
                Your Next Journey Starts Here
              </span>
              <h3 className="text-3xl font-bold font-editorial text-[var(--text-primary)]">
                Where will your journey take you?
              </h3>
              <p className="text-xs sm:text-sm text-[var(--text-secondary)] leading-relaxed">
                Explore tranquil backwaters, high Himalayan passes, royal desert palaces, or global cities.
              </p>
            </div>

            <div className="pt-2 flex flex-col sm:flex-row items-center justify-center gap-3">
              <Button
                variant="primary"
                size="md"
                onClick={() => onCreateTrip()}
                rightIcon={<ArrowRight className="w-4 h-4" />}
              >
                Plan a New Trip
              </Button>
              <Button
                variant="outline"
                size="md"
                onClick={() => onCreateTrip('Darjeeling, India')}
              >
                Explore Darjeeling Plan
              </Button>
            </div>
          </div>
        )}
      </section>

      {/* 7. TRAVEL INSPIRATION (Moods & Landscapes) */}
      <section>
        <TravelInspiration
          onSelectMood={(_moodId, _label, defaultDestination) => {
            onCreateTrip(defaultDestination || 'Goa, India');
          }}
          onSelectLandscape={(_landId, _label, defaultDestination) => {
            onCreateTrip(defaultDestination || 'Darjeeling, India');
          }}
        />
      </section>
    </div>
  );
};
