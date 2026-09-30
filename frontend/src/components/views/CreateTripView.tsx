import React, { useState, useEffect } from 'react';
import { ArrowLeft, Calendar, MapPin, Users, Wallet, Sparkles, Compass, Plane } from 'lucide-react';
import { Button } from '../common/Button';
import { api } from '../../lib/apiClient';
import { useNotification } from '../../context/NotificationContext';
import { useCurrency } from '../../context/CurrencyContext';
import {
  getDestinationImage,
  QUICK_PICKS_INDIA,
  QUICK_PICKS_INTERNATIONAL,
} from '../../lib/destinationImages';
import { INDIAN_DEPARTURE_AIRPORTS } from '../../lib/airports';
import { formatCurrency, SUPPORTED_CURRENCIES } from '../../lib/currency';

interface CreateTripViewProps {
  onBack: () => void;
  onTripCreated: (tripId: string) => void;
  initialDestination?: string;
  initialOrigin?: string;
}

export const CreateTripView: React.FC<CreateTripViewProps> = ({
  onBack,
  onTripCreated,
  initialDestination = '',
  initialOrigin = '',
}) => {
  const { currency: globalCurrency } = useCurrency();
  const [currency, setCurrency] = useState<string>(globalCurrency);
  const [hasManuallyChangedCurrency, setHasManuallyChangedCurrency] = useState(false);
  const [budget, setBudget] = useState<string>(globalCurrency === 'INR' ? '45000' : '500');

  // Synchronize with global currency preference if not overridden manually
  useEffect(() => {
    if (!hasManuallyChangedCurrency) {
      setCurrency(globalCurrency);
      if (globalCurrency === 'INR' && budget === '500') {
        setBudget('45000');
      } else if (globalCurrency !== 'INR' && budget === '45000') {
        setBudget('500');
      }
    }
  }, [globalCurrency, hasManuallyChangedCurrency]);

  const [destination, setDestination] = useState(initialDestination || 'Goa, India');
  const [origin, setOrigin] = useState(initialOrigin || '');
  const [startDate, setStartDate] = useState('2026-10-14');
  const [endDate, setEndDate] = useState('2026-10-24');
  const [travelersCount, setTravelersCount] = useState<number>(2);
  const [selectedPrefs, setSelectedPrefs] = useState<string[]>([
    'Boutique Stays',
    'Local Culinary',
    'Cultural Landmarks',
  ]);
  const [notes, setNotes] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const { notify } = useNotification();

  // Dynamic preview photo
  const destMeta = getDestinationImage(destination);

  const availablePreferences = [
    'Boutique Stays',
    'Local Culinary',
    'Cultural Landmarks',
    'Scenic Nature',
    'Slow Pacing',
    'Rail Transit',
    'Heritage Havelis',
    'Backwaters & Lakes',
  ];

  const togglePreference = (pref: string) => {
    setSelectedPrefs((prev) =>
      prev.includes(pref) ? prev.filter((p) => p !== pref) : [...prev, pref]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);

    if (!destination.trim()) {
      setValidationError('Please specify where you are traveling.');
      return;
    }
    if (!origin.trim()) {
      setValidationError('Please specify your departure origin.');
      return;
    }

    setIsLoading(true);
    try {
      const newTrip = await api.createTrip({
        origin: origin.trim(),
        destination: destination.trim(),
        start_date: startDate,
        end_date: endDate,
        travelers_count: Number(travelersCount) || 1,
        budget: budget ? Number(budget) : undefined,
        currency,
        preferences: selectedPrefs,
        notes: notes.trim() || undefined,
      });

      notify(
        `Created trip workspace for ${newTrip.destination}.`,
        'success',
        'Trip Created'
      );
      onTripCreated(newTrip.trip_id);
    } catch (err: any) {
      const msg = err.message || 'Failed to create trip workspace.';
      setValidationError(msg);
      notify(msg, 'error', 'Creation Error');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer"
      >
        <ArrowLeft className="w-4 h-4" />
        <span>Back to Dashboard</span>
      </button>

      {/* Visual Header / Destination Live Preview */}
      <div className="relative rounded-3xl overflow-hidden min-h-[220px] sm:min-h-[260px] border border-[var(--border)] shadow-md flex flex-col justify-end p-6 sm:p-8 text-white">
        <img
          src={destMeta.imageUrl}
          alt={destMeta.name}
          className="absolute inset-0 w-full h-full object-cover object-center transition-all duration-700"
          referrerPolicy="no-referrer"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/40 to-black/10" />

        <div className="relative z-10 space-y-1 max-w-xl">
          <span className="text-[11px] font-mono tracking-widest text-[#E7A07F] uppercase">
            {destMeta.region ? `${destMeta.region}, ` : ''}{destMeta.country} · {destMeta.mood}
          </span>
          <h1 className="text-3xl sm:text-4xl font-bold font-editorial text-white">
            Plan Your Journey to {destMeta.name}
          </h1>
          <p className="text-xs sm:text-sm text-[#E9EEF0] font-light line-clamp-1">
            {destMeta.tagline}
          </p>
        </div>
      </div>

      {/* Main Travel-Oriented Form */}
      <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-10 shadow-xs space-y-8">
        {validationError && (
          <div className="p-3 bg-[var(--status-danger)]/10 border border-[var(--status-danger)]/30 rounded-lg text-xs text-[var(--status-danger)] font-medium">
            {validationError}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-8">
          {/* Section 1: Where are you going? */}
          <div className="space-y-4">
            <label className="text-sm font-bold text-[var(--text-primary)] font-editorial flex items-center gap-2">
              <MapPin className="w-4 h-4 text-[var(--primary)]" />
              <span>Where would you like to go?</span>
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <span className="text-xs text-[var(--text-secondary)] font-medium">Destination City or Region</span>
                <input
                  type="text"
                  value={destination}
                  onChange={(e) => setDestination(e.target.value)}
                  placeholder="e.g. Goa, India or Paris, France"
                  className="w-full text-sm p-3 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)] font-medium"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <span className="text-xs text-[var(--text-secondary)] font-medium">Departure Point</span>
                <input
                  type="text"
                  value={origin}
                  onChange={(e) => setOrigin(e.target.value)}
                  placeholder="Select departure city or airport (e.g. Kolkata, Delhi)"
                  className="w-full text-sm p-3 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)] font-medium"
                  required
                />
              </div>
            </div>

            {/* Indian Departure Airport Chips */}
            <div className="space-y-1 pt-1">
              <span className="text-[11px] font-mono text-[var(--text-secondary)] flex items-center gap-1">
                <Plane className="w-3 h-3 text-[var(--primary)]" />
                Popular Departure Hubs:
              </span>
              <div className="flex flex-wrap items-center gap-1.5">
                {INDIAN_DEPARTURE_AIRPORTS.slice(0, 6).map((ap) => (
                  <button
                    key={ap.code}
                    type="button"
                    onClick={() => setOrigin(ap.display)}
                    className={`text-[11px] px-2 py-1 rounded-md border transition-colors cursor-pointer ${
                      origin === ap.display
                        ? 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)] font-semibold'
                        : 'bg-[var(--background)] text-[var(--text-secondary)] border-[var(--border)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {ap.display}
                  </button>
                ))}
              </div>
            </div>

            {/* Quick Destination Inspiration suggestions */}
            <div className="space-y-2 pt-2 border-t border-[var(--border-subtle)]">
              {/* Popular in India */}
              <div className="space-y-1.5">
                <span className="text-[11px] font-semibold text-[var(--primary)] uppercase tracking-wider font-mono">
                  Popular in India:
                </span>
                <div className="flex flex-wrap items-center gap-1.5">
                  {QUICK_PICKS_INDIA.map((city) => (
                    <button
                      key={city}
                      type="button"
                      onClick={() => setDestination(city)}
                      className={`text-[11px] px-2.5 py-1 rounded-lg border transition-colors cursor-pointer ${
                        destination === city
                          ? 'bg-[var(--primary)] text-white border-[var(--primary)] font-semibold shadow-2xs'
                          : 'bg-[var(--background)] text-[var(--text-secondary)] border-[var(--border)] hover:border-[var(--primary)]/60 hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {city}
                    </button>
                  ))}
                </div>
              </div>

              {/* International */}
              <div className="space-y-1.5 pt-1">
                <span className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider font-mono">
                  International:
                </span>
                <div className="flex flex-wrap items-center gap-1.5">
                  {QUICK_PICKS_INTERNATIONAL.map((city) => (
                    <button
                      key={city}
                      type="button"
                      onClick={() => setDestination(city)}
                      className={`text-[11px] px-2.5 py-1 rounded-lg border transition-colors cursor-pointer ${
                        destination === city
                          ? 'bg-[var(--primary)] text-white border-[var(--primary)] font-semibold shadow-2xs'
                          : 'bg-[var(--background)] text-[var(--text-secondary)] border-[var(--border)] hover:border-[var(--primary)]/60 hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {city}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: When & Who? */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t border-[var(--border-subtle)]">
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5 text-[var(--primary)]" />
                <span>Departure Date</span>
              </label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full text-xs sm:text-sm p-2.5 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)] font-mono"
                required
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5 text-[var(--primary)]" />
                <span>Return Date</span>
              </label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full text-xs sm:text-sm p-2.5 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)] font-mono"
                required
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                <Users className="w-3.5 h-3.5 text-[var(--primary)]" />
                <span>Who&apos;s Going?</span>
              </label>
              <select
                value={travelersCount}
                onChange={(e) => setTravelersCount(Number(e.target.value))}
                className="w-full text-xs sm:text-sm p-2.5 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)] font-medium"
              >
                <option value={1}>Solo Traveler (1)</option>
                <option value={2}>Couple / 2 Travelers</option>
                <option value={3}>Small Group (3 Travelers)</option>
                <option value={4}>Family / Friends (4 Travelers)</option>
                <option value={6}>Group (6 Travelers)</option>
              </select>
            </div>
          </div>

          {/* Section 3: Budget & Travel Style */}
          <div className="space-y-4 pt-4 border-t border-[var(--border-subtle)]">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-[var(--text-primary)] flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <Wallet className="w-3.5 h-3.5 text-[var(--primary)]" />
                    Target Budget (Optional)
                  </span>
                  {budget && (
                    <span className="text-[11px] font-mono text-[var(--primary)] font-semibold">
                      Formatted: {formatCurrency(budget, currency)}
                    </span>
                  )}
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                    placeholder="e.g. 45000"
                    className="flex-1 text-sm p-2.5 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)] font-mono"
                  />
                  <select
                    value={currency}
                    onChange={(e) => {
                      setCurrency(e.target.value);
                      setHasManuallyChangedCurrency(true);
                    }}
                    className="w-28 text-sm p-2.5 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] font-medium cursor-pointer"
                  >
                    {SUPPORTED_CURRENCIES.map((c) => (
                      <option key={c.code} value={c.code}>
                        {c.code} ({c.symbol})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-[var(--terracotta)]" />
                  <span>Travel Notes / Special Requests</span>
                </label>
                <input
                  type="text"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. Vegetarian dining, river view room, heritage stay"
                  className="w-full text-sm p-2.5 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] focus:outline-hidden focus:ring-2 focus:ring-[var(--primary)]"
                />
              </div>
            </div>

            {/* Travel Preferences Chips */}
            <div className="space-y-2 pt-2">
              <span className="text-xs text-[var(--text-secondary)] font-medium">
                Pacing & Atmosphere Preferences
              </span>
              <div className="flex flex-wrap gap-2">
                {availablePreferences.map((pref) => {
                  const isSelected = selectedPrefs.includes(pref);
                  return (
                    <button
                      key={pref}
                      type="button"
                      onClick={() => togglePreference(pref)}
                      className={`text-xs px-3 py-1.5 rounded-full border transition-all cursor-pointer ${
                        isSelected
                          ? 'bg-[var(--primary)] text-white border-[var(--primary)] font-medium shadow-2xs'
                          : 'bg-[var(--background)] text-[var(--text-secondary)] border-[var(--border)] hover:border-[var(--primary)]/60'
                      }`}
                    >
                      {pref}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="pt-4 border-t border-[var(--border-subtle)] flex items-center justify-end gap-3">
            <Button type="button" variant="outline" size="md" onClick={onBack}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="md"
              isLoading={isLoading}
              className="px-8 shadow-xs"
            >
              Create Trip Workspace
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
};
