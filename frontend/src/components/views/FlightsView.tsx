import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plane, Compass, Sparkles, AlertCircle, RefreshCw, PlusCircle, ArrowRight } from 'lucide-react';
import { FlightOffer, FlightSearchRequest } from '../../types/flight';
import { TripSummary } from '../../types/trip';
import { api } from '../../lib/apiClient';
import { useAuth } from '../../context/AuthContext';
import { useNotification } from '../../context/NotificationContext';
import { FlightSearchForm } from '../travel/FlightSearchForm';
import { FlightCard } from '../travel/FlightCard';
import {
  FlightFilterBar,
  FlightSortOption,
  FlightStopsFilter,
} from '../travel/FlightFilterBar';
import { Modal } from '../common/Modal';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

export const FlightsView: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { notify } = useNotification();

  const [offers, setOffers] = useState<FlightOffer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filter & sort state
  const [sortBy, setSortBy] = useState<FlightSortOption>('recommended');
  const [stopsFilter, setStopsFilter] = useState<FlightStopsFilter>('all');
  const [refundableOnly, setRefundableOnly] = useState(false);
  const [selectedCarrier, setSelectedCarrier] = useState<string>('all');

  // Attach to trip modal state
  const [userTrips, setUserTrips] = useState<TripSummary[]>([]);
  const [selectedOfferToAttach, setSelectedOfferToAttach] = useState<FlightOffer | null>(null);
  const [isAttachModalOpen, setIsAttachModalOpen] = useState(false);
  const [isAttaching, setIsAttaching] = useState(false);

  // Initial search on mount
  const handleSearch = useCallback(
    async (request: FlightSearchRequest) => {
      setIsLoading(true);
      setError(null);
      try {
        const results = await api.searchFlights(request);
        setOffers(results);
      } catch (err: any) {
        const msg = err.message || 'Unable to retrieve flight options.';
        setError(msg);
        notify(msg, 'error', 'Flight Search Error');
      } finally {
        setIsLoading(false);
      }
    },
    [notify]
  );

  useEffect(() => {
    handleSearch({
      origin: 'Delhi (DEL)',
      destination: 'Srinagar (SXR)',
      departure_date: '2026-10-14',
      return_date: '2026-10-24',
      passengers: 2,
      cabin_class: 'economy',
    });
  }, [handleSearch]);

  // In Section 2: Trips attachment data integration will occur in Section 3.
  // trips are defaulted to empty array for now.

  const handleOpenAttachModal = (offer: FlightOffer) => {
    setSelectedOfferToAttach(offer);
    setIsAttachModalOpen(true);
  };

  const handleAttachOfferToTrip = async (tripId: string) => {
    if (!selectedOfferToAttach) return;

    setIsAttaching(true);
    try {
      await api.selectTripOffer(tripId, {
        offer_type: 'flight',
        offer_id: selectedOfferToAttach.id,
        snapshot_id: selectedOfferToAttach.snapshot_id || `fl_snap_${selectedOfferToAttach.id}`,
        metadata: selectedOfferToAttach,
      });

      notify(
        `Flight (${selectedOfferToAttach.carrier_name}) attached to trip as canonical snapshot.`,
        'success',
        'Flight Linked'
      );
      setIsAttachModalOpen(false);
      navigate(`/trips/${tripId}`);
    } catch (err: any) {
      notify(err.message || 'Failed to attach flight offer.', 'error', 'Attachment Error');
    } finally {
      setIsAttaching(false);
    }
  };

  const handlePlanTripWithFlight = (offer: FlightOffer) => {
    const dest = offer.slices[0]?.destination || 'Srinagar, Kashmir';
    const orig = offer.slices[0]?.origin || 'Delhi (DEL)';
    navigate('/trips/new', {
      state: {
        destination: dest,
        origin: orig,
      },
    });
  };

  // Extract unique carriers for filter
  const availableCarriers = Array.from(new Set(offers.map((o) => o.carrier_name)));

  // Filtered and sorted offers
  const filteredOffers = offers
    .filter((o) => {
      if (stopsFilter === 'nonstop' && o.slices[0]?.stops !== 0) return false;
      if (stopsFilter === 'one_stop' && (o.slices[0]?.stops || 0) > 1) return false;
      if (refundableOnly && !o.refundable) return false;
      if (selectedCarrier !== 'all' && o.carrier_name !== selectedCarrier) return false;
      return true;
    })
    .sort((a, b) => {
      if (sortBy === 'price_low') {
        return Number(a.total_amount) - Number(b.total_amount);
      }
      if (sortBy === 'duration') {
        return (a.slices[0]?.total_duration_minutes || 0) - (b.slices[0]?.total_duration_minutes || 0);
      }
      if (sortBy === 'departure_early') {
        return (a.slices[0]?.departure_time || '').localeCompare(b.slices[0]?.departure_time || '');
      }
      return 0; // recommended default
    });

  return (
    <div className="space-y-8 pb-16">
      {/* Editorial Page Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="px-3 py-1 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
            Flight Search & Discovery
          </span>
          <span className="text-xs text-[var(--text-muted)] font-mono">
            POST /api/flights/search-flexible
          </span>
        </div>
        <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold font-editorial text-[var(--text-primary)]">
          Search Real Flight Schedules
        </h1>
        <p className="text-sm sm:text-base text-[var(--text-secondary)] max-w-2xl font-light">
          Compare authentic airline routes, verified baggage inclusions, and flexible fare classes.
          Select an offer to lock into your canonical trip workspace.
        </p>
      </div>

      {/* Flight Search Form */}
      <FlightSearchForm onSearch={handleSearch} isLoading={isLoading} />

      {/* Filter and Sort Controls */}
      {offers.length > 0 && (
        <FlightFilterBar
          totalResults={filteredOffers.length}
          sortBy={sortBy}
          onSortChange={setSortBy}
          stopsFilter={stopsFilter}
          onStopsFilterChange={setStopsFilter}
          refundableOnly={refundableOnly}
          onRefundableOnlyChange={setRefundableOnly}
          selectedCarrier={selectedCarrier}
          onCarrierChange={setSelectedCarrier}
          availableCarriers={availableCarriers}
        />
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="py-20 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-4">
          <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
            <Plane className="w-6 h-6 rotate-45" />
          </div>
          <div className="space-y-1">
            <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
              Fetching flight routes...
            </h4>
            <p className="text-xs text-[var(--text-secondary)]">
              Querying carrier schedules, checked baggage inclusions & live seat categories.
            </p>
          </div>
        </div>
      )}

      {/* Error State */}
      {!isLoading && error && (
        <div className="p-8 text-center bg-[var(--surface)] rounded-3xl border border-[var(--status-danger)]/30 space-y-4">
          <div className="w-12 h-12 rounded-full bg-[var(--status-danger)]/10 text-[var(--status-danger)] mx-auto flex items-center justify-center">
            <AlertCircle className="w-6 h-6" />
          </div>
          <div className="space-y-1">
            <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
              Unable to find matching flight routes.
            </h4>
            <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">{error}</p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              handleSearch({
                origin: 'Delhi (DEL)',
                destination: 'Srinagar (SXR)',
                departure_date: '2026-10-14',
                return_date: '2026-10-24',
                passengers: 2,
                cabin_class: 'economy',
              })
            }
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          >
            Retry Search
          </Button>
        </div>
      )}

      {/* Offers Results List */}
      {!isLoading && !error && filteredOffers.length > 0 && (
        <div className="space-y-4">
          {filteredOffers.map((offer) => (
            <FlightCard
              key={offer.id}
              offer={offer}
              onPlanTripWithFlight={handlePlanTripWithFlight}
              onSelect={isAuthenticated ? () => handleOpenAttachModal(offer) : undefined}
            />
          ))}
        </div>
      )}

      {/* No matching results from filters */}
      {!isLoading && !error && offers.length > 0 && filteredOffers.length === 0 && (
        <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            No flight offers match your active filters.
          </p>
          <p className="text-xs text-[var(--text-secondary)]">
            Try adjusting stops or toggling off refundable filters to view available options.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setStopsFilter('all');
              setRefundableOnly(false);
              setSelectedCarrier('all');
            }}
          >
            Reset Filters
          </Button>
        </div>
      )}

      {/* Attach to Existing Trip Modal */}
      <Modal
        isOpen={isAttachModalOpen}
        onClose={() => setIsAttachModalOpen(false)}
        title="Attach Flight to Trip"
        subtitle={
          selectedOfferToAttach
            ? `Link ${selectedOfferToAttach.carrier_name} (${formatCurrency(selectedOfferToAttach.total_amount, selectedOfferToAttach.currency)}) to your canonical trip.`
            : 'Select a destination workspace'
        }
        maxWidth="md"
      >
        <div className="space-y-4">
          {userTrips.length === 0 ? (
            <div className="text-center py-6 space-y-3">
              <p className="text-xs text-[var(--text-secondary)]">
                You do not have any active trip workspaces yet.
              </p>
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setIsAttachModalOpen(false);
                  if (selectedOfferToAttach) {
                    handlePlanTripWithFlight(selectedOfferToAttach);
                  }
                }}
                leftIcon={<PlusCircle className="w-3.5 h-3.5" />}
              >
                Create New Trip with This Flight
              </Button>
            </div>
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
              <span className="text-xs font-medium text-[var(--text-secondary)]">
                Select an existing trip:
              </span>
              {userTrips.map((t) => (
                <div
                  key={t.trip_id}
                  onClick={() => handleAttachOfferToTrip(t.trip_id)}
                  className="p-3.5 rounded-2xl border border-[var(--border)] hover:border-[var(--primary)] bg-[var(--surface)] hover:bg-[var(--surface-muted)] cursor-pointer transition-all flex items-center justify-between gap-3 shadow-xs"
                >
                  <div>
                    <div className="font-semibold text-sm text-[var(--text-primary)] font-editorial">
                      {t.title}
                    </div>
                    <div className="text-xs text-[var(--text-secondary)]">
                      {t.destination} • {t.start_date}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    isLoading={isAttaching}
                    rightIcon={<ArrowRight className="w-3 h-3" />}
                  >
                    Select
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
};
