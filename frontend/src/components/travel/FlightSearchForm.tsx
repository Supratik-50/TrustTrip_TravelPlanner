import React, { useState } from 'react';
import { Search, Plane, Calendar, Users, ArrowRightLeft, Sparkles, MapPin, Globe } from 'lucide-react';
import { FlightSearchRequest } from '../../types/flight';
import { Button } from '../common/Button';
import {
  INDIAN_DEPARTURE_AIRPORTS,
  DOMESTIC_INDIA_ROUTES,
  INTERNATIONAL_ROUTES,
} from '../../lib/airports';

interface FlightSearchFormProps {
  initialOrigin?: string;
  initialDestination?: string;
  initialDepartureDate?: string;
  initialReturnDate?: string;
  initialPassengers?: number;
  initialCabinClass?: 'economy' | 'premium_economy' | 'business' | 'first';
  onSearch: (request: FlightSearchRequest) => void;
  isLoading?: boolean;
}

export const FlightSearchForm: React.FC<FlightSearchFormProps> = ({
  initialOrigin = '',
  initialDestination = '',
  initialDepartureDate = '2026-10-14',
  initialReturnDate = '2026-10-24',
  initialPassengers = 2,
  initialCabinClass = 'economy',
  onSearch,
  isLoading = false,
}) => {
  const [origin, setOrigin] = useState(initialOrigin);
  const [destination, setDestination] = useState(initialDestination);
  const [departureDate, setDepartureDate] = useState(initialDepartureDate);
  const [returnDate, setReturnDate] = useState(initialReturnDate);
  const [passengers, setPassengers] = useState(initialPassengers);
  const [cabinClass, setCabinClass] = useState<'economy' | 'premium_economy' | 'business' | 'first'>(
    initialCabinClass
  );
  const [isRoundTrip, setIsRoundTrip] = useState(true);
  const [routeCategory, setRouteCategory] = useState<'domestic' | 'international'>('domestic');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSearch({
      origin: origin || 'Delhi (DEL)',
      destination: destination || 'Goa (GOI)',
      departure_date: departureDate,
      return_date: isRoundTrip ? returnDate : undefined,
      passengers,
      cabin_class: cabinClass,
    });
  };

  const handleSwapAirports = () => {
    const tmp = origin;
    setOrigin(destination);
    setDestination(tmp);
  };

  return (
    <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs">
      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Trip Type & Cabin Class Controls */}
        <div className="flex flex-wrap items-center justify-between gap-4 pb-2 border-b border-[var(--border)]/70">
          <div className="flex items-center gap-1.5 p-1 bg-[var(--surface-muted)] rounded-xl border border-[var(--border)] text-xs">
            <button
              type="button"
              onClick={() => setIsRoundTrip(true)}
              className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                isRoundTrip
                  ? 'bg-[var(--surface)] text-[var(--text-primary)] shadow-xs font-semibold'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              Round Trip
            </button>
            <button
              type="button"
              onClick={() => setIsRoundTrip(false)}
              className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                !isRoundTrip
                  ? 'bg-[var(--surface)] text-[var(--text-primary)] shadow-xs font-semibold'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              One Way
            </button>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
              <Users className="w-3.5 h-3.5 text-[var(--primary)]" />
              <select
                aria-label="Passengers count"
                value={passengers}
                onChange={(e) => setPassengers(Number(e.target.value))}
                className="bg-transparent font-medium text-[var(--text-primary)] focus:outline-hidden cursor-pointer"
              >
                {[1, 2, 3, 4, 5, 6].map((n) => (
                  <option key={n} value={n}>
                    {n} {n === 1 ? 'Traveler' : 'Travelers'}
                  </option>
                ))}
              </select>
            </div>

            <span className="text-[var(--border-strong)]">•</span>

            <select
              aria-label="Cabin class"
              value={cabinClass}
              onChange={(e) => setCabinClass(e.target.value as any)}
              className="bg-transparent text-xs font-medium text-[var(--text-primary)] focus:outline-hidden cursor-pointer"
            >
              <option value="economy">Economy</option>
              <option value="premium_economy">Premium Economy</option>
              <option value="business">Business Class</option>
              <option value="first">First Class</option>
            </select>
          </div>
        </div>

        {/* Origin / Destination and Dates Grid */}
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-center">
          {/* Origin */}
          <div className="md:col-span-3 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Plane className="w-3.5 h-3.5 text-[var(--primary)] rotate-45" />
              Departure Airport
            </label>
            <input
              type="text"
              value={origin}
              onChange={(e) => setOrigin(e.target.value)}
              placeholder="Select departure city or airport"
              required
              className="w-full px-4 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all placeholder:text-[var(--text-muted)]"
            />
          </div>

          {/* Swap Button */}
          <div className="md:col-span-1 flex justify-center pt-5 sm:pt-0">
            <button
              type="button"
              onClick={handleSwapAirports}
              title="Swap Departure and Destination"
              className="w-8 h-8 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] hover:bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--primary)] flex items-center justify-center transition-all shadow-xs"
            >
              <ArrowRightLeft className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Destination */}
          <div className="md:col-span-3 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Plane className="w-3.5 h-3.5 text-[var(--primary)]" />
              Arrival Destination
            </label>
            <input
              type="text"
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder="e.g. Goa (GOI), Kyoto (KIX)"
              required
              className="w-full px-4 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all placeholder:text-[var(--text-muted)]"
            />
          </div>

          {/* Departure Date */}
          <div className="md:col-span-2 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-[var(--primary)]" />
              Departure
            </label>
            <input
              type="date"
              value={departureDate}
              onChange={(e) => setDepartureDate(e.target.value)}
              required
              className="w-full px-3 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all"
            />
          </div>

          {/* Return Date */}
          <div className="md:col-span-2 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-[var(--primary)]" />
              Return
            </label>
            <input
              type="date"
              value={returnDate}
              onChange={(e) => setReturnDate(e.target.value)}
              disabled={!isRoundTrip}
              required={isRoundTrip}
              className={`w-full px-3 py-2.5 rounded-xl border border-[var(--border)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all ${
                isRoundTrip
                  ? 'bg-[var(--surface)] text-[var(--text-primary)]'
                  : 'bg-[var(--surface-muted)] text-[var(--text-muted)] cursor-not-allowed opacity-60'
              }`}
            />
          </div>

          {/* Search Button */}
          <div className="md:col-span-1 pt-5">
            <Button
              type="submit"
              variant="primary"
              size="md"
              isLoading={isLoading}
              className="w-full h-[42px] px-0 flex items-center justify-center"
              title="Search Flights"
            >
              <Search className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Indian Departure Hub Suggestions */}
        <div className="pt-2 border-t border-[var(--border)]/60 space-y-2">
          <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
            <MapPin className="w-3.5 h-3.5 text-[var(--primary)]" />
            <span className="font-semibold text-[var(--text-primary)]">Indian Departure Hubs:</span>
            <span className="text-[11px] text-[var(--text-muted)]">Quick tap to set your origin airport</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {INDIAN_DEPARTURE_AIRPORTS.map((apt) => (
              <button
                key={apt.code}
                type="button"
                onClick={() => setOrigin(apt.display)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors border ${
                  origin === apt.display
                    ? 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)] font-semibold'
                    : 'bg-[var(--surface-muted)] hover:bg-[var(--surface)] text-[var(--text-secondary)] border-[var(--border)]'
                }`}
              >
                {apt.city} ({apt.code})
              </button>
            ))}
          </div>
        </div>

        {/* Quick Route Discovery Presets */}
        <div className="pt-2 border-t border-[var(--border)]/60 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Sparkles className="w-3.5 h-3.5 text-[var(--terracotta)]" />
              <span className="text-xs font-semibold text-[var(--text-primary)]">Route Discovery Presets:</span>
              <div className="inline-flex rounded-lg border border-[var(--border)] p-0.5 bg-[var(--surface-muted)] text-[11px]">
                <button
                  type="button"
                  onClick={() => setRouteCategory('domestic')}
                  className={`px-2 py-0.5 rounded-md font-medium transition-all ${
                    routeCategory === 'domestic'
                      ? 'bg-[var(--surface)] text-[var(--primary)] font-semibold shadow-xs'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  India Domestic
                </button>
                <button
                  type="button"
                  onClick={() => setRouteCategory('international')}
                  className={`px-2 py-0.5 rounded-md font-medium transition-all ${
                    routeCategory === 'international'
                      ? 'bg-[var(--surface)] text-[var(--primary)] font-semibold shadow-xs'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  International
                </button>
              </div>
            </div>
            <span className="text-[11px] text-[var(--text-muted)] font-mono">
              Presets for itinerary discovery • Live schedules queried on search
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {(routeCategory === 'domestic' ? DOMESTIC_INDIA_ROUTES : INTERNATIONAL_ROUTES).map(
              (r, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => {
                    setOrigin(r.origin);
                    setDestination(r.destination);
                  }}
                  className="px-2.5 py-1.5 rounded-xl bg-[var(--surface-muted)] hover:bg-[var(--primary-soft)] hover:text-[var(--primary)] text-xs font-medium transition-all border border-[var(--border)] flex items-center gap-1.5 group"
                >
                  <span>{r.label}</span>
                  {r.description && (
                    <span className="text-[10px] text-[var(--text-muted)] group-hover:text-[var(--primary)]/70">
                      • {r.description}
                    </span>
                  )}
                </button>
              )
            )}
          </div>
        </div>
      </form>
    </div>
  );
};
