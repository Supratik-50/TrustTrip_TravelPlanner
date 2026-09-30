import React, { useState, useEffect, useCallback } from 'react';
import { Compass, Plus, Search, RefreshCw, AlertCircle } from 'lucide-react';
import { TripSummary } from '../../types/trip';
import { Button } from '../common/Button';
import { TripCard } from '../travel/TripCard';
import { EmptyState } from '../ui/EmptyState';
import { api } from '../../lib/apiClient';

interface TripsViewProps {
  onOpenTrip: (tripId: string) => void;
  onCreateTrip: () => void;
}

export const TripsView: React.FC<TripsViewProps> = ({ onOpenTrip, onCreateTrip }) => {
  const [trips, setTrips] = useState<TripSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const loadTrips = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.getTrips();
      setTrips(data);
    } catch (err: any) {
      const msg = err.message || 'Unable to retrieve your travel journeys.';
      setError(msg);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTrips();
  }, [loadTrips]);

  const filteredTrips = trips.filter((trip) => {
    const matchesStatus = filterStatus === 'all' || trip.status === filterStatus;
    const matchesSearch =
      trip.destination.toLowerCase().includes(searchQuery.toLowerCase()) ||
      trip.origin.toLowerCase().includes(searchQuery.toLowerCase()) ||
      trip.title?.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesStatus && matchesSearch;
  });

  return (
    <div className="space-y-8 pb-20">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <div className="text-xs font-semibold text-[var(--primary)] uppercase tracking-wider font-mono">
            Personal Journeys
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold text-[var(--text-primary)] font-editorial tracking-tight mt-1">
            My Trips & Workspaces
          </h1>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-1 font-light">
            All your active itineraries, saved drafts, and revision histories in one place.
          </p>
        </div>

        <div className="flex items-center gap-2.5 shrink-0">
          <Button
            variant="outline"
            size="md"
            onClick={loadTrips}
            isLoading={isLoading}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            title="Refresh trips"
          >
            Refresh
          </Button>
          <Button
            variant="accent"
            size="md"
            onClick={onCreateTrip}
            leftIcon={<Plus className="w-4 h-4" />}
            className="shadow-sm font-semibold"
          >
            Create New Trip
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 p-2 bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-2xs">
        <div className="flex items-center gap-1.5 w-full sm:w-auto">
          {['all', 'planning', 'draft', 'approved'].map((status) => (
            <button
              key={status}
              type="button"
              onClick={() => setFilterStatus(status)}
              className={`px-3.5 py-1.5 text-xs font-medium rounded-xl transition-colors capitalize cursor-pointer ${
                filterStatus === status
                  ? 'bg-[var(--primary)] text-white shadow-2xs'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-black/5 dark:hover:bg-white/5'
              }`}
            >
              {status}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-72">
          <Search className="w-3.5 h-3.5 absolute left-3.5 top-3 text-[var(--text-secondary)]" />
          <input
            type="text"
            placeholder="Search destination or origin..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full text-xs pl-9 pr-3.5 py-2 rounded-xl bg-[var(--background)] border border-[var(--border)] text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]/60 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
          />
        </div>
      </div>

      {/* Error State */}
      {error && !isLoading && (
        <div className="p-4 rounded-2xl bg-[var(--status-danger)]/10 border border-[var(--status-danger)]/30 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-[var(--status-danger)] shrink-0" />
            <p className="text-xs text-[var(--status-danger)] font-medium">
              {error}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={loadTrips}
            leftIcon={<RefreshCw className="w-3 h-3" />}
            className="shrink-0 text-xs"
          >
            Retry
          </Button>
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="space-y-6">
          <div className="text-center py-6 space-y-2">
            <div className="w-8 h-8 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
              <Compass className="w-4 h-4" />
            </div>
            <p className="text-xs text-[var(--text-secondary)] font-medium">
              Retrieving your travel itineraries...
            </p>
          </div>
          {/* Travel Skeletons */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[1, 2, 3].map((n) => (
              <div
                key={n}
                className="bg-[var(--surface)] rounded-2xl border border-[var(--border)] overflow-hidden shadow-xs animate-pulse"
              >
                <div className="h-44 sm:h-48 bg-[var(--border-subtle)]/60" />
                <div className="p-4 space-y-3">
                  <div className="h-4 bg-[var(--border-subtle)]/80 rounded w-2/3" />
                  <div className="h-3 bg-[var(--border-subtle)]/50 rounded w-1/2" />
                  <div className="pt-2 border-t border-[var(--border-subtle)] flex justify-between">
                    <div className="h-3 bg-[var(--border-subtle)]/50 rounded w-1/4" />
                    <div className="h-3 bg-[var(--border-subtle)]/50 rounded w-1/4" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : filteredTrips.length === 0 ? (
        <EmptyState
          title={searchQuery ? 'No matching journeys found.' : 'YOUR NEXT JOURNEY STARTS HERE'}
          description={
            searchQuery
              ? 'Adjust your search filters or start planning a new destination.'
              : 'Choose somewhere to go and start shaping your journey.'
          }
          actionText="Create a journey"
          onAction={onCreateTrip}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredTrips.map((trip) => (
            <TripCard
              key={trip.trip_id}
              trip={trip}
              onOpen={onOpenTrip}
              onContinuePlanning={onOpenTrip}
            />
          ))}
        </div>
      )}
    </div>
  );
};
