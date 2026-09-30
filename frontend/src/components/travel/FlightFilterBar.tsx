import React from 'react';
import { ArrowUpDown, Filter, ShieldCheck, Check } from 'lucide-react';

export type FlightSortOption = 'recommended' | 'price_low' | 'duration' | 'departure_early';
export type FlightStopsFilter = 'all' | 'nonstop' | 'one_stop';

interface FlightFilterBarProps {
  totalResults: number;
  sortBy: FlightSortOption;
  onSortChange: (sort: FlightSortOption) => void;
  stopsFilter: FlightStopsFilter;
  onStopsFilterChange: (stops: FlightStopsFilter) => void;
  refundableOnly: boolean;
  onRefundableOnlyChange: (refundable: boolean) => void;
  selectedCarrier: string;
  onCarrierChange: (carrier: string) => void;
  availableCarriers: string[];
}

export const FlightFilterBar: React.FC<FlightFilterBarProps> = ({
  totalResults,
  sortBy,
  onSortChange,
  stopsFilter,
  onStopsFilterChange,
  refundableOnly,
  onRefundableOnlyChange,
  selectedCarrier,
  onCarrierChange,
  availableCarriers,
}) => {
  return (
    <div className="bg-[var(--surface)] rounded-2xl border border-[var(--border)] p-4 shadow-xs flex flex-wrap items-center justify-between gap-4 text-xs">
      {/* Left: Results count and stops filter */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-[var(--text-primary)] font-mono">
          {totalResults} {totalResults === 1 ? 'Flight Offer' : 'Flight Offers'} Found
        </span>

        <span className="text-[var(--border-strong)] hidden sm:inline">|</span>

        {/* Stops filter pill group */}
        <div className="flex items-center gap-1 bg-[var(--surface-muted)] p-1 rounded-xl border border-[var(--border)]">
          <button
            type="button"
            onClick={() => onStopsFilterChange('all')}
            className={`px-2.5 py-1 rounded-lg transition-all ${
              stopsFilter === 'all'
                ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            All Stops
          </button>
          <button
            type="button"
            onClick={() => onStopsFilterChange('nonstop')}
            className={`px-2.5 py-1 rounded-lg transition-all ${
              stopsFilter === 'nonstop'
                ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            Non-stop Only
          </button>
          <button
            type="button"
            onClick={() => onStopsFilterChange('one_stop')}
            className={`px-2.5 py-1 rounded-lg transition-all ${
              stopsFilter === 'one_stop'
                ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            1 Stop Max
          </button>
        </div>

        {/* Refundable toggle */}
        <button
          type="button"
          onClick={() => onRefundableOnlyChange(!refundableOnly)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border transition-all ${
            refundableOnly
              ? 'bg-[var(--status-success)]/10 text-[var(--status-success)] border-[var(--status-success)]/30 font-semibold'
              : 'border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          <span>Refundable Fares</span>
          {refundableOnly && <Check className="w-3 h-3" />}
        </button>
      </div>

      {/* Right: Carrier dropdown & Sort selector */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Carrier filter */}
        {availableCarriers.length > 1 && (
          <div className="flex items-center gap-1.5">
            <Filter className="w-3.5 h-3.5 text-[var(--text-muted)]" />
            <select
              aria-label="Filter by airline"
              value={selectedCarrier}
              onChange={(e) => onCarrierChange(e.target.value)}
              className="px-2.5 py-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] focus:outline-hidden focus:border-[var(--primary)]"
            >
              <option value="all">All Airlines</option>
              {availableCarriers.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Sort by dropdown */}
        <div className="flex items-center gap-1.5">
          <ArrowUpDown className="w-3.5 h-3.5 text-[var(--text-muted)]" />
          <span className="text-[var(--text-secondary)]">Sort by:</span>
          <select
            aria-label="Sort flight results by"
            value={sortBy}
            onChange={(e) => onSortChange(e.target.value as FlightSortOption)}
            className="px-2.5 py-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] font-medium focus:outline-hidden focus:border-[var(--primary)]"
          >
            <option value="recommended">Best Recommended</option>
            <option value="price_low">Price: Low to High</option>
            <option value="duration">Fastest Flight Time</option>
            <option value="departure_early">Earliest Departure</option>
          </select>
        </div>
      </div>
    </div>
  );
};
