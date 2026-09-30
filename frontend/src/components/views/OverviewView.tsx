import React, { useState } from 'react';
import { Compass, Calendar, MapPin, Users, ArrowRight, Sparkles, Plane, Building2, SearchCheck, CheckCircle2, ShieldCheck } from 'lucide-react';
import { Button } from '../common/Button';
import { useAuth } from '../../context/AuthContext';
import { NavTab } from '../common/Header';
import { INDIAN_DEPARTURE_AIRPORTS } from '../../lib/airports';
import { QUICK_PICKS_INDIA, QUICK_PICKS_INTERNATIONAL } from '../../lib/destinationImages';

interface OverviewViewProps {
  onSelectTab: (tab: NavTab) => void;
  onOpenAuth: (mode?: 'login' | 'signup') => void;
  onStartPlanning: (initialData?: { destination: string; origin: string }) => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  onSelectTab,
  onOpenAuth,
  onStartPlanning,
}) => {
  const { isAuthenticated, user } = useAuth();
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('Goa, India');
  const [dates, setDates] = useState('Oct 14 - Oct 24, 2026');
  const [travelers, setTravelers] = useState('2 Travelers');

  const featuredDestinations = [
    {
      title: 'Darjeeling & Kanchenjunga',
      country: 'India',
      tagline: 'Misty tea estates beneath the snow-crowned Himalayas',
      highlights: 'Autumn peak · Heritage mountain rail · Tea gardens',
      bgGradient: 'from-[#172D29] via-[#1F4B43] to-[#C96F52]',
      accentColor: '#E9DFC9',
      destinationQuery: 'Darjeeling, India',
    },
    {
      title: 'Kerala Backwaters & Munnar',
      country: 'India',
      tagline: 'Silent thatch-roof houseboats and emerald Nilgiri tea hills',
      highlights: 'Vembanad lagoons · Cardamom trails · Ayurvedic stays',
      bgGradient: 'from-[#132A24] via-[#1E5642] to-[#DCCDB5]',
      accentColor: '#E7A07F',
      destinationQuery: 'Kerala, India',
    },
    {
      title: 'Royal Jaipur & Udaipur',
      country: 'India',
      tagline: 'Lake Pichola marble palaces and amber desert fortresses',
      highlights: 'Living Mewari palaces · Marwar crafts · Sunset boat cruises',
      bgGradient: 'from-[#281E19] via-[#75442C] to-[#E7A07F]',
      accentColor: '#F0E8D9',
      destinationQuery: 'Jaipur, India',
    },
    {
      title: 'Srinagar & Dal Lake',
      country: 'India',
      tagline: 'Dawn shikara glides, floating flower markets and pine valleys',
      highlights: 'Cedar wood houseboats · Mughal terraced gardens · Alpine passes',
      bgGradient: 'from-[#15272F] via-[#215A6B] to-[#DCCDB5]',
      accentColor: '#E9EEF0',
      destinationQuery: 'Srinagar (Kashmir), India',
    },
    {
      title: 'Kyoto & Kansai',
      country: 'Japan',
      tagline: 'Temple gardens, morning tea rituals & autumn maple foliage',
      highlights: 'Autumn peak · Rail-connected · Cultural heritage',
      bgGradient: 'from-[#14213D] via-[#1C315E] to-[#0F766E]',
      accentColor: '#E97A6A',
      destinationQuery: 'Kyoto, Japan',
    },
    {
      title: 'Amalfi & Ravello',
      country: 'Italy',
      tagline: 'Cliffside coastal lemon groves, quiet Mediterranean bays',
      highlights: 'Coastal paths · Historic villas · Ferry routes',
      bgGradient: 'from-[#0B3C49] via-[#0F766E] to-[#27866A]',
      accentColor: '#E9DFC9',
      destinationQuery: 'Amalfi Coast, Italy',
    },
  ];

  const handleHeroSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const effectiveOrigin = origin.trim() || 'Kolkata (CCU)';
    if (!isAuthenticated) {
      onOpenAuth('login');
      return;
    }
    onStartPlanning({ origin: effectiveOrigin, destination });
  };

  return (
    <div className="space-y-16 pb-16">
      {/* Editorial Hero Section */}
      <section className="relative pt-6 sm:pt-12">
        <div className="max-w-4xl mx-auto text-center space-y-4 px-4">
          <div className="inline-flex items-center gap-2 text-xs font-semibold text-[var(--primary)] bg-[var(--primary-soft)] px-3 py-1 rounded-full border border-[var(--primary)]/20">
            <Sparkles className="w-3.5 h-3.5" />
            <span>TrustTrip TravelPlanner</span>
          </div>

          <h1 className="text-3xl sm:text-5xl font-bold tracking-tight text-[var(--text-primary)] font-editorial max-w-3xl mx-auto leading-tight text-balance">
            Plan your complete journey with calm, verifiable confidence.
          </h1>

          <p className="text-base sm:text-lg text-[var(--text-secondary)] max-w-2xl mx-auto leading-relaxed">
            Thoughtful travel planning for journeys across India and the world.
            Canonical airfare, curated stays, and verified regional insight.
          </p>
        </div>

        {/* Quick Journey Starter Bar */}
        <div className="max-w-5xl mx-auto mt-10 px-4">
          <form
            onSubmit={handleHeroSubmit}
            className="bg-[var(--surface)] rounded-2xl shadow-xs border border-[var(--border)] p-3 sm:p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3"
          >
            <div className="p-2.5 rounded-lg bg-[var(--background)] border border-transparent hover:border-[var(--border)] transition-colors">
              <label className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider flex items-center gap-1.5">
                <MapPin className="w-3 h-3 text-[var(--primary)]" />
                From
              </label>
              <input
                type="text"
                value={origin}
                onChange={(e) => setOrigin(e.target.value)}
                className="w-full bg-transparent text-sm font-medium text-[var(--text-primary)] focus:outline-hidden mt-1"
                placeholder="Select departure city or airport"
              />
            </div>

            <div className="p-2.5 rounded-lg bg-[var(--background)] border border-transparent hover:border-[var(--border)] transition-colors">
              <label className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider flex items-center gap-1.5">
                <MapPin className="w-3 h-3 text-[var(--coral)]" />
                To
              </label>
              <input
                type="text"
                value={destination}
                onChange={(e) => setDestination(e.target.value)}
                className="w-full bg-transparent text-sm font-medium text-[var(--text-primary)] focus:outline-hidden mt-1"
                placeholder="Where would you like to go?"
              />
            </div>

            <div className="p-2.5 rounded-lg bg-[var(--background)] border border-transparent hover:border-[var(--border)] transition-colors">
              <label className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider flex items-center gap-1.5">
                <Calendar className="w-3 h-3 text-[var(--primary)]" />
                Dates
              </label>
              <input
                type="text"
                value={dates}
                onChange={(e) => setDates(e.target.value)}
                className="w-full bg-transparent text-sm font-medium text-[var(--text-primary)] focus:outline-hidden mt-1"
                placeholder="Approximate dates"
              />
            </div>

            <div className="flex items-center gap-2">
              <div className="flex-1 p-2.5 rounded-lg bg-[var(--background)] border border-transparent hover:border-[var(--border)] transition-colors">
                <label className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider flex items-center gap-1.5">
                  <Users className="w-3 h-3 text-[var(--primary)]" />
                  Travelers
                </label>
                <input
                  type="text"
                  value={travelers}
                  onChange={(e) => setTravelers(e.target.value)}
                  className="w-full bg-transparent text-sm font-medium text-[var(--text-primary)] focus:outline-hidden mt-1"
                />
              </div>

              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="h-full px-5"
                rightIcon={<ArrowRight className="w-4 h-4" />}
              >
                {isAuthenticated ? 'Plan' : 'Start'}
              </Button>
            </div>
          </form>

          {/* Quick Departure Hub Chips */}
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5 px-1 text-xs text-[var(--text-secondary)]">
            <span className="text-[11px] font-mono text-[var(--text-muted)] flex items-center gap-1">
              <Plane className="w-3 h-3 text-[var(--primary)]" />
              Quick Departures:
            </span>
            {INDIAN_DEPARTURE_AIRPORTS.slice(0, 5).map((ap) => (
              <button
                key={ap.code}
                type="button"
                onClick={() => setOrigin(ap.display)}
                className={`text-[11px] px-2 py-0.5 rounded-md border transition-colors cursor-pointer ${
                  origin === ap.display
                    ? 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)] font-semibold'
                    : 'bg-[var(--surface)] text-[var(--text-secondary)] border-[var(--border)] hover:border-[var(--primary)]/50'
                }`}
              >
                {ap.city}
              </button>
            ))}
          </div>

          {/* Quick status bar */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--text-secondary)] px-2">
            <div className="flex items-center gap-3">
              <span className="font-medium text-[var(--text-primary)]">
                {isAuthenticated ? `Signed in as ${user?.email}` : 'Guest Preview Mode'}
              </span>
              <span aria-hidden="true" className="opacity-40">·</span>
              <span>FastAPI Architecture</span>
              <span aria-hidden="true" className="opacity-40">·</span>
              <span>PostgreSQL Datastore</span>
            </div>
            {!isAuthenticated && (
              <button
                type="button"
                onClick={() => onOpenAuth('login')}
                className="text-[var(--primary)] font-medium hover:underline cursor-pointer"
              >
                Sign in to save trips & access research history →
              </button>
            )}
          </div>
        </div>
      </section>

      {/* Featured Travel Inspirations */}
      <section className="max-w-6xl mx-auto px-4 sm:px-6">
        <div className="flex items-baseline justify-between mb-6">
          <div>
            <h2 className="text-xl font-bold text-[var(--text-primary)]">Curated Travel Inspirations</h2>
            <p className="text-xs text-[var(--text-secondary)] mt-0.5">
              Handcrafted itineraries across Indian highlands, coastlines, and world cultural capitals.
            </p>
          </div>
          <button
            type="button"
            onClick={() => onSelectTab('trips')}
            className="text-xs font-semibold text-[var(--primary)] hover:underline flex items-center gap-1 cursor-pointer"
          >
            Explore all trips
            <ArrowRight className="w-3 h-3" />
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {featuredDestinations.map((dest, idx) => (
            <div
              key={idx}
              className="group bg-[var(--surface)] rounded-2xl border border-[var(--border)] overflow-hidden hover:shadow-md transition-all duration-200 flex flex-col"
            >
              <div className={`h-44 bg-gradient-to-br ${dest.bgGradient} p-5 flex flex-col justify-between text-white relative overflow-hidden`}>
                <div className="absolute inset-0 bg-black/10 mix-blend-overlay" />
                <div className="relative z-10 flex items-center justify-between text-xs">
                  <span className="font-mono text-[11px] tracking-wider uppercase opacity-90">{dest.country}</span>
                  <span className="text-[10px] bg-white/20 backdrop-blur-xs px-2 py-0.5 rounded text-white font-medium">
                    Curated
                  </span>
                </div>
                <div className="relative z-10">
                  <h3 className="text-xl font-bold tracking-tight font-editorial">{dest.title}</h3>
                  <p className="text-xs text-white/85 line-clamp-1 mt-0.5">{dest.tagline}</p>
                </div>
              </div>

              <div className="p-4 flex-1 flex flex-col justify-between space-y-3">
                <div className="text-xs text-[var(--text-secondary)] leading-relaxed">
                  {dest.highlights}
                </div>

                <div className="pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between">
                  <span className="text-xs font-medium text-[var(--text-primary)]">10-Day Plan</span>
                  <button
                    type="button"
                    onClick={() => {
                      setDestination(dest.destinationQuery);
                      if (isAuthenticated) {
                        onStartPlanning({ origin: origin || 'Kolkata (CCU)', destination: dest.destinationQuery });
                      } else {
                        onOpenAuth('login');
                      }
                    }}
                    className="text-xs font-semibold text-[var(--primary)] hover:text-[var(--primary-hover)] inline-flex items-center gap-1 cursor-pointer"
                  >
                    Select route
                    <ArrowRight className="w-3 h-3" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Core Architectural Pillars */}
      <section className="max-w-6xl mx-auto px-4 sm:px-6">
        <div className="bg-[var(--surface)] rounded-2xl border border-[var(--border)] p-8 sm:p-10 space-y-8">
          <div className="max-w-2xl">
            <h2 className="text-2xl font-bold text-[var(--text-primary)] font-editorial">
              Engineered for Real-World Travel Decisions
            </h2>
            <p className="text-sm text-[var(--text-secondary)] mt-2 leading-relaxed">
              TrustTrip pairs canonical travel provider structures with human-in-the-loop revisions.
              Every flight and hotel is verifiable; every change keeps a traceable audit history.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            <div className="p-4 rounded-xl bg-[var(--background)] border border-[var(--border)] space-y-2.5">
              <div className="w-8 h-8 rounded-lg bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center">
                <Compass className="w-4 h-4" />
              </div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">Local & Global Reach</h3>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                Authentic coverage for Indian states, hill stations, and heritage circuits alongside world travel capitals.
              </p>
            </div>

            <div className="p-4 rounded-xl bg-[var(--background)] border border-[var(--border)] space-y-2.5">
              <div className="w-8 h-8 rounded-lg bg-[var(--terracotta-soft)] text-[var(--terracotta)] flex items-center justify-center">
                <Plane className="w-4 h-4" />
              </div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">Verified Transport</h3>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                Real airfare snapshots with transparent carrier policies, baggage rules, and cancellation terms.
              </p>
            </div>

            <div className="p-4 rounded-xl bg-[var(--background)] border border-[var(--border)] space-y-2.5">
              <div className="w-8 h-8 rounded-lg bg-[var(--surface-muted)] text-[var(--primary)] flex items-center justify-center">
                <Building2 className="w-4 h-4" />
              </div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">Curated Stays</h3>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                Tea bungalows, heritage havelis, and boutique city hotels with real-time rate verification.
              </p>
            </div>

            <div className="p-4 rounded-xl bg-[var(--background)] border border-[var(--border)] space-y-2.5">
              <div className="w-8 h-8 rounded-lg bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center">
                <SearchCheck className="w-4 h-4" />
              </div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">Verifiable Evidence</h3>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                Research notebooks backed by official authority regulations, seasonal advisories, and local notes.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
};
