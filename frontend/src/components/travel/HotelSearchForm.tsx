import React, { useState } from 'react';
import { Search, Building2, Calendar, Users, Sparkles } from 'lucide-react';
import { HotelSearchRequest } from '../../types/hotel';
import { Button } from '../common/Button';

interface HotelSearchFormProps {
  initialCity?: string;
  initialCheckIn?: string;
  initialCheckOut?: string;
  initialGuests?: number;
  onSearch: (request: HotelSearchRequest) => void;
  isLoading?: boolean;
  disabled?: boolean;
}

export const HotelSearchForm: React.FC<HotelSearchFormProps> = ({
  initialCity = '',
  initialCheckIn = '',
  initialCheckOut = '',
  initialGuests = 1,
  onSearch,
  isLoading = false,
  disabled = false,
}) => {
  const [city, setCity] = useState(initialCity);
  const [checkIn, setCheckIn] = useState(initialCheckIn);
  const [checkOut, setCheckOut] = useState(initialCheckOut);
  const [guests, setGuests] = useState(initialGuests);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSearch({
      city,
      check_in_date: checkIn,
      check_out_date: checkOut,
      guests,
    });
  };

  const quickCities = [
    { label: 'Kyoto, Japan', city: 'Kyoto, Japan' },
    { label: 'Amalfi Coast, Italy', city: 'Amalfi Coast, Italy' },
    { label: 'Tokyo, Japan', city: 'Tokyo, Japan' },
    { label: 'Paris, France', city: 'Paris, France' },
  ];

  return (
    <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs">
      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-center">
          {/* City / Destination */}
          <div className="md:col-span-5 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Building2 className="w-3.5 h-3.5 text-[var(--primary)]" />
              Destination City or Area
            </label>
            <input
              type="text"
              value={city}
              onChange={(e) => setCity(e.target.value)}
              disabled={disabled}
              placeholder="Enter a destination"
              required
              className="w-full px-4 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all"
            />
          </div>

          {/* Check-in Date */}
          <div className="md:col-span-3 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-[var(--primary)]" />
              Check-in
            </label>
            <input
              type="date"
              value={checkIn}
              onChange={(e) => setCheckIn(e.target.value)}
              disabled={disabled}
              required
              className="w-full px-3 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all"
            />
          </div>

          {/* Check-out Date */}
          <div className="md:col-span-3 space-y-1.5">
            <label className="text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-[var(--primary)]" />
              Check-out
            </label>
            <input
              type="date"
              value={checkOut}
              onChange={(e) => setCheckOut(e.target.value)}
              disabled={disabled}
              required
              className="w-full px-3 py-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all"
            />
          </div>

          {/* Search Button */}
          <div className="md:col-span-1 pt-5">
            <Button
              type="submit"
              variant="primary"
              size="md"
              isLoading={isLoading}
              disabled={disabled}
              className="w-full h-[42px] px-0 flex items-center justify-center"
              title="Search Stays"
            >
              <Search className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Guests selector & Quick city tags */}
        <div className="flex flex-wrap items-center justify-between gap-4 pt-1 border-t border-[var(--border)]/70 text-xs">
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1 text-[var(--text-muted)] font-mono text-[11px]">
              <Sparkles className="w-3 h-3 text-[var(--terracotta)]" />
              Popular Destinations:
            </span>
            {quickCities.map((c, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setCity(c.city)}
                disabled={disabled}
                className="px-2.5 py-1 rounded-lg bg-[var(--surface-muted)] hover:bg-[var(--primary-soft)] hover:text-[var(--primary)] text-[11px] font-medium transition-colors"
              >
                {c.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Users className="w-3.5 h-3.5 text-[var(--primary)]" />
            <span className="text-[var(--text-secondary)]">Guests:</span>
            <select
              aria-label="Guests count"
              value={guests}
              onChange={(e) => setGuests(Number(e.target.value))}
              disabled={disabled}
              className="font-medium text-[var(--text-primary)] bg-transparent focus:outline-hidden cursor-pointer"
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n} {n === 1 ? 'Guest' : 'Guests'}
                </option>
              ))}
            </select>
          </div>
        </div>
      </form>
    </div>
  );
};
