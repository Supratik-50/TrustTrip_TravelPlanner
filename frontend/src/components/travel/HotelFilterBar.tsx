import React from 'react';
import { ArrowUpDown, Star, ShieldCheck, Check } from 'lucide-react';

export type HotelSortOption = 'rating' | 'price_low' | 'price_high' | 'stars_high';
export type HotelStarFilter = 'all' | '4plus' | '5only';

interface HotelFilterBarProps {
  totalResults: number;
  sortBy: HotelSortOption;
  onSortChange: (sort: HotelSortOption) => void;
  starFilter: HotelStarFilter;
  onStarFilterChange: (stars: HotelStarFilter) => void;
  freeCancellationOnly: boolean;
  onFreeCancellationOnlyChange: (enabled: boolean) => void;
}

export const HotelFilterBar: React.FC<HotelFilterBarProps> = ({
  totalResults,
  sortBy,
  onSortChange,
  starFilter,
  onStarFilterChange,
  freeCancellationOnly,
  onFreeCancellationOnlyChange,
}) => {
  return (
    <div className="bg-[var(--surface)] rounded-2xl border border-[var(--border)] p-4 shadow-xs flex flex-wrap items-center justify-between gap-4 text-xs">
      {/* Left: Results count and stars filter */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-[var(--text-primary)] font-mono">
          {totalResults} {totalResults === 1 ? 'Stay' : 'Stays'} Available
        </span>

        <span className="text-[var(--border-strong)] hidden sm:inline">|</span>

        {/* Stars filter buttons */}
        <div className="flex items-center gap-1 bg-[var(--surface-muted)] p-1 rounded-xl border border-[var(--border)]">
          <button
            type="button"
            onClick={() => onStarFilterChange('all')}
            className={`px-2.5 py-1 rounded-lg transition-all ${
              starFilter === 'all'
                ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            All Ratings
          </button>
          <button
            type="button"
            onClick={() => onStarFilterChange('4plus')}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg transition-all ${
              starFilter === '4plus'
                ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            <Star className="w-3 h-3 text-amber-500 fill-amber-500" />
            4★ & Higher
          </button>
          <button
            type="button"
            onClick={() => onStarFilterChange('5only')}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg transition-all ${
              starFilter === '5only'
                ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
          >
            <Star className="w-3 h-3 text-amber-500 fill-amber-500" />
            5★ Luxury
          </button>
        </div>

        {/* Free cancellation filter */}
        <button
          type="button"
          onClick={() => onFreeCancellationOnlyChange(!freeCancellationOnly)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border transition-all ${
            freeCancellationOnly
              ? 'bg-[var(--status-success)]/10 text-[var(--status-success)] border-[var(--status-success)]/30 font-semibold'
              : 'border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <ShieldCheck className="w-3.5 h-3.5" />
          <span>Free Cancellation Only</span>
          {freeCancellationOnly && <Check className="w-3 h-3" />}
        </button>
      </div>

      {/* Right: Sort selector */}
      <div className="flex items-center gap-2">
        <ArrowUpDown className="w-3.5 h-3.5 text-[var(--text-muted)]" />
        <span className="text-[var(--text-secondary)]">Sort by:</span>
        <select
          aria-label="Sort hotel results by"
          value={sortBy}
          onChange={(e) => onSortChange(e.target.value as HotelSortOption)}
          className="px-2.5 py-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] font-medium focus:outline-hidden focus:border-[var(--primary)]"
        >
          <option value="rating">Guest Rating & Reviews</option>
          <option value="price_low">Price: Low to High</option>
          <option value="price_high">Price: High to Low</option>
          <option value="stars_high">Hotel Stars (5 to 1)</option>
        </select>
      </div>
    </div>
  );
};
