import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Compass,
  RefreshCw,
  AlertCircle,
  Plane,
  Building2,
  Calendar,
  Sparkles,
  CheckCircle2,
  Layers,
  CreditCard,
  ArrowRight,
  TrendingUp,
  BookOpen,
  History,
  AlertTriangle,
} from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { FlightOffer, FlightSearchRequest } from '../../types/flight';
import { HotelOffer, HotelSearchRequest, HotelRoom } from '../../types/hotel';
import { api } from '../../lib/apiClient';
import { bookingService } from '../../services/bookingService';
import { useNotification } from '../../context/NotificationContext';
import { TripHero } from '../travel/TripHero';
import { PlanningWorkspace } from '../planner/PlanningWorkspace';
import { FlightSearchForm } from '../travel/FlightSearchForm';
import { FlightCard } from '../travel/FlightCard';
import {
  FlightFilterBar,
  FlightSortOption,
  FlightStopsFilter,
} from '../travel/FlightFilterBar';
import { HotelSearchForm } from '../travel/HotelSearchForm';
import { HotelCard } from '../travel/HotelCard';
import {
  HotelFilterBar,
  HotelSortOption,
  HotelStarFilter,
} from '../travel/HotelFilterBar';
import { BookingHandoffCard } from '../travel/BookingHandoffCard';
import { BookingWorkspace } from '../booking/BookingWorkspace';
import { TripRevisionManager } from '../travel/TripRevisionManager';
import { ResearchView } from './ResearchView';
import { Modal } from '../common/Modal';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';
import { Input } from '../common/Input';

interface TripDetailViewProps {
  tripId: string;
  onBack: () => void;
  initialTab?: TripTab;
}

type TripTab = 'itinerary' | 'flights' | 'hotels' | 'research' | 'revisions' | 'booking';

export const TripDetailView: React.FC<TripDetailViewProps> = ({
  tripId,
  onBack,
  initialTab = 'itinerary',
}) => {
  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<TripTab>(initialTab);
  const { notify } = useNotification();

  // In-trip Flight search state
  const [flightOffers, setFlightOffers] = useState<FlightOffer[]>([]);
  const [isSearchingFlights, setIsSearchingFlights] = useState(false);
  const [flightSearchError, setFlightSearchError] = useState<string | null>(null);
  const [flightAvailabilityState, setFlightAvailabilityState] = useState<'available' | 'empty' | 'unavailable' | 'idle'>('idle');
  const [flightSortBy, setFlightSortBy] = useState<FlightSortOption>('recommended');
  const [flightStopsFilter, setFlightStopsFilter] = useState<FlightStopsFilter>('all');
  const [flightRefundableOnly, setFlightRefundableOnly] = useState(false);
  const [flightCarrier, setFlightCarrier] = useState<string>('all');
  const [isSelectingFlight, setIsSelectingFlight] = useState(false);

  // In-trip Hotel search state
  const [hotelOffers, setHotelOffers] = useState<HotelOffer[]>([]);
  const [isSearchingHotels, setIsSearchingHotels] = useState(false);
  const [hotelSearchError, setHotelSearchError] = useState<string | null>(null);
  const [hotelSortBy, setHotelSortBy] = useState<HotelSortOption>('rating');
  const [hotelStarFilter, setHotelStarFilter] = useState<HotelStarFilter>('all');
  const [hotelFreeCancellation, setHotelFreeCancellation] = useState(false);
  const [isSelectingHotel, setIsSelectingHotel] = useState(false);

  // Edit form state
  const [editTravelers, setEditTravelers] = useState<number>(2);
  const [editBudget, setEditBudget] = useState<string>('');
  const [editStartDate, setEditStartDate] = useState('');
  const [editEndDate, setEditEndDate] = useState('');
  const [isSavingConstraints, setIsSavingConstraints] = useState(false);

  const fetchTrip = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await api.getTrip(tripId);
      setTrip(data);
      setEditTravelers(data.travelers_count || 1);
      setEditBudget(data.budget ? String(data.budget) : '');
      setEditStartDate(data.start_date || '');
      setEditEndDate(data.end_date || '');
    } catch (err: any) {
      const msg = err.message || 'Unable to load trip workspace.';
      setError(msg);
      notify(msg, 'error', 'Trip Load Error');
    } finally {
      setIsLoading(false);
    }
  }, [tripId, notify]);

  useEffect(() => {
    fetchTrip();
  }, [fetchTrip]);

  // Perform initial search for flights when user switches to flights tab
  const handleSearchFlights = useCallback(
    async (request: FlightSearchRequest) => {
      setIsSearchingFlights(true);
      setFlightSearchError(null);
      try {
        const payload: FlightSearchRequest = {
          ...request,
          trip_id: tripId,
        };
        const results = await api.searchFlights(payload);
        setFlightOffers(results);
        if (results.length === 0) {
          setFlightAvailabilityState('empty');
        } else {
          setFlightAvailabilityState('available');
        }
      } catch (err: any) {
        const msg = err.message || 'Unable to load flights.';
        setFlightSearchError(msg);
        setFlightAvailabilityState('unavailable');
        notify(msg, 'error', 'Flight Search Notice');
      } finally {
        setIsSearchingFlights(false);
      }
    },
    [tripId, notify]
  );

  useEffect(() => {
    if (activeTab === 'flights' && flightOffers.length === 0 && trip) {
      handleSearchFlights({
        trip_id: trip.trip_id,
        origin: trip.origin || 'San Francisco (SFO)',
        destination: trip.destination || 'Osaka / Kyoto (KIX)',
        departure_date: trip.start_date || '2026-10-14',
        return_date: trip.end_date || '2026-10-24',
        passengers: trip.travelers_count || 2,
        cabin_class: 'economy',
      });
    }
  }, [activeTab, flightOffers.length, trip, handleSearchFlights]);

  // Perform search for hotels using canonical trip data
  const handleSearchHotels = useCallback(
    async (_request?: Partial<HotelSearchRequest>) => {
      if (
        !tripId ||
        !trip?.destination ||
        !trip?.start_date ||
        !trip?.end_date ||
        trip?.travelers_count == null ||
        Number(trip.travelers_count) < 1
      ) {
        setHotelSearchError('Missing canonical trip details for accommodation search.');
        return;
      }

      setIsSearchingHotels(true);
      setHotelSearchError(null);
      try {
        const payload: HotelSearchRequest = {
          trip_id: tripId,
          destination: trip.destination,
          city: trip.destination,
          checkin: trip.start_date,
          checkout: trip.end_date,
          check_in_date: trip.start_date,
          check_out_date: trip.end_date,
          adults: Number(trip.travelers_count),
          guests: Number(trip.travelers_count),
        };
        const results = await api.searchHotels(payload);
        setHotelOffers(results);
      } catch (err: any) {
        const msg = err.message || 'Unable to load stays.';
        setHotelSearchError(msg);
        notify(msg, 'error', 'Hotel Search Notice');
      } finally {
        setIsSearchingHotels(false);
      }
    },
    [tripId, trip, notify]
  );

  useEffect(() => {
    if (activeTab === 'hotels' && hotelOffers.length === 0 && trip) {
      if (
        !trip.trip_id ||
        !trip.destination ||
        !trip.start_date ||
        !trip.end_date ||
        trip.travelers_count == null ||
        trip.travelers_count < 1
      ) {
        return;
      }

      handleSearchHotels();
    }
  }, [activeTab, hotelOffers.length, trip, handleSearchHotels]);

  // Canonical trip night count
  const canonicalNights = useMemo(() => {
    if (!trip?.start_date || !trip?.end_date) return undefined;
    const start = new Date(trip.start_date.split('T')[0]);
    const end = new Date(trip.end_date.split('T')[0]);
    const diff = Math.round((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24));
    return diff > 0 ? diff : undefined;
  }, [trip?.start_date, trip?.end_date]);

  // Select canonical flight offer
  const handleSelectFlightOffer = async (offer: FlightOffer) => {
    if (!trip) return;
    setIsSelectingFlight(true);
    try {
      const offerId = offer.offer_id || offer.id;
      const updated = await api.selectTripOffer(trip.trip_id, {
        offer_type: 'flight',
        offer_id: offerId,
        snapshot_id: offer.snapshot_id || offerId,
        metadata: offer,
      });
      setTrip({
        ...updated,
        selected_flight_offer: offer,
      });

      notify(
        `Selected ${offer.carrier_name} (${offer.slices[0]?.origin} ➔ ${offer.slices[0]?.destination}). Canonical snapshot locked.`,
        'success',
        'Flight Selected'
      );
    } catch (err: any) {
      notify(err.message || 'Failed to select flight offer.', 'error', 'Selection Error');
    } finally {
      setIsSelectingFlight(false);
    }
  };

  // Select canonical hotel offer
  const handleSelectHotelOffer = async (hotel: HotelOffer, selectedRoom?: HotelRoom) => {
    if (!trip) return;
    setIsSelectingHotel(true);
    try {
      const snapId = hotel.snapshot_id || hotel.id;
      const updated = await api.selectTripOffer(trip.trip_id, {
        offer_type: 'hotel',
        offer_id: snapId,
        snapshot_id: snapId,
        room_id: selectedRoom?.id,
        metadata: {
          ...hotel,
          selected_room: selectedRoom ? {
            id: selectedRoom.id,
            name: selectedRoom.name,
            description: selectedRoom.description,
            price_per_night: selectedRoom.price_per_night,
            total_price: selectedRoom.total_price,
            currency: selectedRoom.currency,
            capacity: selectedRoom.capacity,
            beds: selectedRoom.beds,
          } : undefined,
        },
      });
      setTrip(updated);
      notify(
        `Selected ${hotel.name || hotel.hotel_name}. Stay preferences linked to trip workspace.`,
        'success',
        'Stay Selected'
      );
    } catch (err: any) {
      notify(err.message || 'Failed to select stay.', 'error', 'Selection Error');
    } finally {
      setIsSelectingHotel(false);
    }
  };

  const handleSaveConstraints = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!trip) return;

    setIsSavingConstraints(true);
    try {
      const budgetNum = editBudget ? Number(editBudget) : undefined;
      const travelersNum = Number(editTravelers) || 1;
      const updatedConstraints: Record<string, any> = {
        budget: budgetNum,
        budget_amount: budgetNum,
        travelers_count: travelersNum,
        adults: travelersNum,
        children: 0,
        rooms: 1,
        start_date: editStartDate,
        end_date: editEndDate,
        departure_date: editStartDate,
        return_date: editEndDate,
      };

      const res = await api.createTripRevision(trip.trip_id, {
        updated_constraints: updatedConstraints,
        feedback: 'Updated trip constraints and parameters',
        expected_base_revision: trip.active_revision_number ?? trip.current_revision?.revision_number,
        expected_base_revision_id: trip.active_revision_id ?? trip.current_revision?.revision_id,
      });

      // Refetch the authoritative trip from backend
      const refreshedTrip = await api.getTrip(trip.trip_id);
      setTrip(refreshedTrip);
      setIsEditModalOpen(false);
      notify(
        `Trip parameters saved as Revision ${res.revision_number || refreshedTrip.active_revision_number || 1} (awaiting review).`,
        'success',
        'Constraints Saved'
      );
    } catch (err: any) {
      notify(
        err.message || 'Failed to save trip constraints.',
        'error',
        'Save Error'
      );
    } finally {
      setIsSavingConstraints(false);
    }
  };

  if (isLoading) {
    return (
      <div className="py-24 text-center space-y-4">
        <div className="w-10 h-10 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
          <Compass className="w-5 h-5" />
        </div>
        <p className="text-xs text-[var(--text-secondary)] font-mono">
          Loading canonical journey {tripId}...
        </p>
      </div>
    );
  }

  if (error || !trip) {
    return (
      <div className="py-20 text-center max-w-md mx-auto space-y-4">
        <div className="w-12 h-12 rounded-full bg-[var(--status-danger)]/10 text-[var(--status-danger)] mx-auto flex items-center justify-center">
          <AlertCircle className="w-6 h-6" />
        </div>
        <h3 className="text-lg font-bold text-[var(--text-primary)] font-editorial">
          We couldn&apos;t find this journey.
        </h3>
        <p className="text-xs text-[var(--text-secondary)]">
          {error || 'This journey does not exist or does not belong to your account.'}
        </p>
        <div className="pt-2 flex items-center justify-center gap-3">
          <Button variant="outline" size="sm" onClick={onBack}>
            Back to My Trips
          </Button>
          <Button variant="primary" size="sm" onClick={fetchTrip} leftIcon={<RefreshCw className="w-3.5 h-3.5" />}>
            Try Again
          </Button>
        </div>
      </div>
    );
  }

  const selectedFlight = trip.selected_flight_offer as FlightOffer | undefined;
  const selectedHotel = (trip.selected_hotel_offer || (trip.metadata as any)?.selected_hotel) as HotelOffer | undefined;

  // Filtered flights for in-trip tab
  const availableCarriers = Array.from(new Set(flightOffers.map((o) => o.carrier_name)));
  const filteredFlightOffers = flightOffers
    .filter((o) => {
      if (flightStopsFilter === 'nonstop' && o.slices[0]?.stops !== 0) return false;
      if (flightStopsFilter === 'one_stop' && (o.slices[0]?.stops || 0) > 1) return false;
      if (flightRefundableOnly && !o.refundable) return false;
      if (flightCarrier !== 'all' && o.carrier_name !== flightCarrier) return false;
      return true;
    })
    .sort((a, b) => {
      if (flightSortBy === 'price_low') return Number(a.total_amount) - Number(b.total_amount);
      if (flightSortBy === 'duration') {
        return (a.slices[0]?.total_duration_minutes || 0) - (b.slices[0]?.total_duration_minutes || 0);
      }
      if (flightSortBy === 'departure_early') {
        return (a.slices[0]?.departure_time || '').localeCompare(b.slices[0]?.departure_time || '');
      }
      return 0;
    });

  // Filtered hotels for in-trip tab
  const filteredHotelOffers = hotelOffers
    .filter((h) => {
      if (hotelStarFilter === '4plus' && (h.stars == null || h.stars < 4)) return false;
      if (hotelStarFilter === '5only' && (h.stars == null || h.stars < 5)) return false;
      if (hotelFreeCancellation && !h.cancellation_policy?.toLowerCase().includes('free')) {
        return false;
      }
      return true;
    })
    .sort((a, b) => {
      if (hotelSortBy === 'rating') return (b.rating ?? 0) - (a.rating ?? 0);
      if (hotelSortBy === 'price_low') return a.total_amount - b.total_amount;
      if (hotelSortBy === 'price_high') return b.total_amount - a.total_amount;
      if (hotelSortBy === 'stars_high') return (b.stars ?? 0) - (a.stars ?? 0);
      return 0;
    });

  // Multicurrency budget commitment calculation
  const tripCurrency = (trip?.currency || 'INR').toUpperCase();

  const getOfferCommittedAmount = (
    offer: { total_amount?: string | number; currency?: string; price_inr?: number | null } | undefined
  ): { amount: number; isAvailable: boolean; isConverted: boolean } => {
    if (!offer) {
      return { amount: 0, isAvailable: true, isConverted: false };
    }
    const offerCurrency = (offer.currency || tripCurrency).toUpperCase();
    const rawAmount = Number(offer.total_amount);

    if (offerCurrency === tripCurrency) {
      if (isNaN(rawAmount) || rawAmount < 0) {
        return { amount: 0, isAvailable: false, isConverted: false };
      }
      return { amount: rawAmount, isAvailable: true, isConverted: false };
    }

    // Offer currency differs from trip currency
    if (tripCurrency === 'INR') {
      const priceInr = offer.price_inr != null ? Number(offer.price_inr) : NaN;
      if (!isNaN(priceInr) && priceInr > 0) {
        return { amount: priceInr, isAvailable: true, isConverted: true };
      }
      // Fails closed if foreign currency offer lacks authoritative INR conversion
      return { amount: 0, isAvailable: false, isConverted: false };
    }

    // If trip currency is not INR and differs from offer currency, without a known conversion rate, fail closed
    return { amount: 0, isAvailable: false, isConverted: false };
  };

  const flightCommitment = getOfferCommittedAmount(selectedFlight);
  const hotelCommitment = getOfferCommittedAmount(selectedHotel);

  const committedBudgetAvailable = flightCommitment.isAvailable && hotelCommitment.isAvailable;
  const totalCommitted = committedBudgetAvailable ? flightCommitment.amount + hotelCommitment.amount : 0;
  const hasMulticurrencyCommitment = flightCommitment.isConverted || hotelCommitment.isConverted;

  return (
    <div className="space-y-8 pb-16">
      {/* Visual Trip Hero */}
      <TripHero
        trip={trip}
        onBack={onBack}
        onEditConstraints={() => setIsEditModalOpen(true)}
      />

      {/* Canonical Snapshots Quick-Strip */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Flight Snapshot Pill */}
        <div
          onClick={() => setActiveTab('flights')}
          className={`p-4 rounded-2xl border transition-all cursor-pointer ${
            selectedFlight
              ? 'bg-[var(--surface)] border-[var(--primary)]/50 hover:border-[var(--primary)] shadow-xs'
              : 'bg-[var(--surface)] border-dashed border-[var(--border)] hover:border-[var(--primary)]/60'
          }`}
        >
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--primary)] font-mono flex items-center gap-1.5">
              <Plane className="w-3.5 h-3.5" />
              Flight Snapshot
            </span>
            {selectedFlight ? (
              <span className="inline-flex items-center gap-1 text-[10px] text-[var(--status-success)] font-medium">
                <CheckCircle2 className="w-3 h-3" />
                Active
              </span>
            ) : (
              <span className="text-[10px] text-[var(--terracotta)] font-medium">
                Pending selection
              </span>
            )}
          </div>
          {selectedFlight ? (
            <div>
              <div className="font-semibold text-sm text-[var(--text-primary)] truncate">
                {selectedFlight.carrier_name || 'Selected Flight'}
              </div>
              <div className="text-xs text-[var(--text-secondary)] font-mono flex items-center justify-between mt-1">
                <span>
                  {selectedFlight.slices && selectedFlight.slices.length > 1
                    ? `${selectedFlight.slices[0].origin} ⇄ ${selectedFlight.slices[0].destination}`
                    : selectedFlight.slices?.[0]?.origin
                    ? `${selectedFlight.slices[0].origin} ➔ ${selectedFlight.slices[0].destination}`
                    : 'Active Flight Snapshot'}
                </span>
                <div className="text-right">
                  <span className="font-bold text-[var(--text-primary)]">
                    {selectedFlight.total_amount ? formatCurrency(selectedFlight.total_amount, selectedFlight.currency) : ''}
                  </span>
                  {selectedFlight.currency?.toUpperCase() !== 'INR' && selectedFlight.price_inr != null && (
                    <div className="text-[10px] text-[var(--text-muted)] font-mono">
                      ≈ {formatCurrency(selectedFlight.price_inr, 'INR')}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-[var(--text-muted)] line-clamp-1">
              Select flight offer for {trip.destination} ➔
            </p>
          )}
        </div>

        {/* Stay Snapshot Pill */}
        <div
          onClick={() => setActiveTab('hotels')}
          className={`p-4 rounded-2xl border transition-all cursor-pointer ${
            selectedHotel
              ? 'bg-[var(--surface)] border-[var(--primary)]/50 hover:border-[var(--primary)] shadow-xs'
              : 'bg-[var(--surface)] border-dashed border-[var(--border)] hover:border-[var(--primary)]/60'
          }`}
        >
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--primary)] font-mono flex items-center gap-1.5">
              <Building2 className="w-3.5 h-3.5" />
              Stay Snapshot
            </span>
            {selectedHotel ? (
              <span className="inline-flex items-center gap-1 text-[10px] text-[var(--status-success)] font-medium">
                <CheckCircle2 className="w-3 h-3" />
                Active
              </span>
            ) : (
              <span className="text-[10px] text-[var(--terracotta)] font-medium">
                Pending selection
              </span>
            )}
          </div>
          {selectedHotel ? (
            <div>
              <div className="font-semibold text-sm text-[var(--text-primary)] truncate">
                {selectedHotel.name}
              </div>
              <div className="text-xs text-[var(--text-secondary)] font-mono flex items-center justify-between mt-1">
                {selectedHotel.stars != null && selectedHotel.stars > 0 ? (
                  <span data-testid="stay-stars-label">{selectedHotel.stars}-Star Stay</span>
                ) : null}
                <div className="text-right">
                  <span className="font-bold text-[var(--text-primary)]">
                    {formatCurrency(selectedHotel.total_amount, selectedHotel.currency)}
                  </span>
                  {selectedHotel.currency?.toUpperCase() !== 'INR' && selectedHotel.price_inr != null && (
                    <div className="text-[10px] text-[var(--text-muted)] font-mono">
                      ≈ {formatCurrency(selectedHotel.price_inr, 'INR')}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-[var(--text-muted)] line-clamp-1">
              Select boutique stay for dates ➔
            </p>
          )}
        </div>

        {/* Budget Commitment Metric */}
        <div
          onClick={() => setActiveTab('booking')}
          className="p-4 rounded-2xl border border-[var(--border)] bg-[var(--surface)] hover:border-[var(--primary)]/60 transition-all cursor-pointer shadow-xs"
        >
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono flex items-center gap-1.5">
              <CreditCard className="w-3.5 h-3.5 text-[var(--terracotta)]" />
              Budget Tracking
            </span>
            <span className="text-[10px] font-mono text-[var(--text-muted)]">
              Limit: {trip.budget ? formatCurrency(trip.budget, trip.currency) : 'Flexible'}
            </span>
          </div>
          <div className="flex items-baseline justify-between">
            {committedBudgetAvailable ? (
              <div className="text-base font-bold font-editorial text-[var(--text-primary)]">
                {formatCurrency(totalCommitted, trip.currency)}{' '}
                <span className="text-xs font-normal text-[var(--text-muted)] font-mono">
                  committed{hasMulticurrencyCommitment ? ' (conv.)' : ''}
                </span>
              </div>
            ) : (
              <div className="text-xs font-semibold font-mono text-amber-600 dark:text-amber-400 flex items-center gap-1">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                <span>Conversion Pending</span>
              </div>
            )}
            <span className="text-xs font-medium text-[var(--primary)] flex items-center gap-1">
              Handoff <ArrowRight className="w-3 h-3" />
            </span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-[var(--surface-muted)] mt-2 overflow-hidden">
            <div
              className="h-full bg-[var(--primary)] transition-all duration-500 rounded-full"
              style={{
                width: committedBudgetAvailable && trip.budget ? `${Math.min(100, Math.round((totalCommitted / trip.budget) * 100))}%` : '0%',
              }}
            />
          </div>
        </div>
      </div>

      {/* Stale Travel Selection Warning Banner */}
      {trip.current_revision?.stale_warnings && trip.current_revision.stale_warnings.length > 0 && (
        <div className="p-4 sm:p-5 rounded-3xl bg-[var(--status-warning)]/10 border border-[var(--status-warning)]/30 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-bold text-[var(--status-warning)] font-mono uppercase tracking-wider flex items-center gap-1.5">
              <AlertTriangle className="w-4 h-4" />
              Travel Options May Need Refresh
            </span>
            <span className="text-[11px] font-mono text-[var(--text-secondary)]">
              Latest Revision: #{trip.active_revision_number || 1}
            </span>
          </div>
          <p className="text-xs text-[var(--text-primary)] font-light leading-relaxed">
            Your trip parameters were recently updated. Selected flights or accommodation snapshots may need revalidation.
          </p>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setActiveTab('flights')}
              className="border-[var(--status-warning)]/40 text-[var(--text-primary)] text-xs h-8"
            >
              Check Flight Snapshot
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setActiveTab('hotels')}
              className="border-[var(--status-warning)]/40 text-[var(--text-primary)] text-xs h-8"
            >
              Verify Stay Dates
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setActiveTab('revisions')}
              className="border-[var(--status-warning)]/40 text-[var(--text-primary)] text-xs h-8"
            >
              Review Revisions
            </Button>
          </div>
        </div>
      )}

      {/* Research Insights Strip */}
      <div className="p-4 rounded-2xl bg-[var(--surface)] border border-[var(--border)] shadow-xs flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center shrink-0">
            <BookOpen className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold font-mono text-[var(--primary)] uppercase tracking-wider">
                Research Insights & Sources
              </span>
              <span className="text-[11px] text-[var(--text-muted)] font-mono">
                {trip.destination}
              </span>
            </div>
            <p className="text-xs text-[var(--text-secondary)]">
              Regional transit rules, authentic kaiseki dining, and private notes verified for this journey.
            </p>
          </div>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={() => setActiveTab('research')}
          rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
          className="text-xs"
        >
          Open Research Notebook
        </Button>
      </div>

      {/* Primary Workspace Navigation Tabs */}
      <div className="flex items-center gap-1.5 p-1.5 bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-xs overflow-x-auto text-xs sm:text-sm">
        <button
          type="button"
          onClick={() => setActiveTab('itinerary')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'itinerary'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <Layers className="w-4 h-4" />
          <span>Itinerary</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('flights')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'flights'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <Plane className="w-4 h-4" />
          <span>Flights</span>
          {selectedFlight && (
            <span className="w-2 h-2 rounded-full bg-[var(--status-success)] inline-block" />
          )}
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('hotels')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'hotels'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <Building2 className="w-4 h-4" />
          <span>Stay</span>
          {selectedHotel && (
            <span className="w-2 h-2 rounded-full bg-[var(--status-success)] inline-block" />
          )}
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('research')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'research'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <Compass className="w-4 h-4" />
          <span>Research</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('revisions')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'revisions'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <History className="w-4 h-4" />
          <span>Revisions</span>
          <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-[var(--surface-muted)] text-[var(--primary)] font-mono">
            Rev {trip.active_revision_number || 1}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('booking')}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'booking'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <CreditCard className="w-4 h-4" />
          <span>Booking</span>
        </button>
      </div>

      {/* TAB CONTENT 1: ITINERARY WORKSPACE */}
      {activeTab === 'itinerary' && (
        <PlanningWorkspace
          trip={trip}
          onUpdateTrip={(updated) => setTrip(updated)}
        />
      )}

      {/* TAB CONTENT 2: FLIGHT SEARCH & SNAPSHOT SELECTION */}
      {activeTab === 'flights' && (
        <div className="space-y-6">
          <div className="space-y-1">
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              Flight Options for {trip.destination}
            </h3>
            <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light">
              Origin: {trip.origin} • Dates: {trip.start_date} to {trip.end_date} • {trip.travelers_count || 1} Travelers
            </p>
          </div>

          <FlightSearchForm
            initialOrigin={trip.origin}
            initialDestination={trip.destination}
            initialDepartureDate={trip.start_date}
            initialReturnDate={trip.end_date}
            initialPassengers={trip.travelers_count || 2}
            onSearch={handleSearchFlights}
            isLoading={isSearchingFlights}
          />

          {flightOffers.length > 0 && (
            <FlightFilterBar
              totalResults={filteredFlightOffers.length}
              sortBy={flightSortBy}
              onSortChange={setFlightSortBy}
              stopsFilter={flightStopsFilter}
              onStopsFilterChange={setFlightStopsFilter}
              refundableOnly={flightRefundableOnly}
              onRefundableOnlyChange={setFlightRefundableOnly}
              selectedCarrier={flightCarrier}
              onCarrierChange={setFlightCarrier}
              availableCarriers={availableCarriers}
            />
          )}

          {isSearchingFlights && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
              <div className="w-10 h-10 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
                <Plane className="w-5 h-5 rotate-45" />
              </div>
              <p className="text-xs text-[var(--text-secondary)]">
                Querying carrier inventory and cabin availability for {trip.destination}...
              </p>
            </div>
          )}

          {!isSearchingFlights && filteredFlightOffers.length > 0 && (
            <div className="space-y-4">
              {filteredFlightOffers.map((offer) => (
                <FlightCard
                  key={offer.id}
                  offer={offer}
                  isSelected={
                    selectedFlight?.id === offer.id ||
                    selectedFlight?.snapshot_id === offer.id ||
                    (Boolean(offer.snapshot_id) && selectedFlight?.id === offer.snapshot_id) ||
                    (Boolean(offer.snapshot_id) && selectedFlight?.snapshot_id === offer.snapshot_id)
                  }
                  onSelect={handleSelectFlightOffer}
                  isSelecting={isSelectingFlight}
                  tripContext={{ tripId: trip.trip_id, destinationName: trip.destination }}
                />
              ))}
            </div>
          )}

          {/* EMPTY STATE */}
          {!isSearchingFlights && !flightSearchError && flightOffers.length === 0 && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-dashed border-[var(--border)] space-y-3">
              <div className="w-12 h-12 rounded-full bg-[var(--surface-muted)] text-[var(--text-muted)] mx-auto flex items-center justify-center">
                <Plane className="w-6 h-6 rotate-45" />
              </div>
              <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
                No flights found for this route
              </h4>
              <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">
                The carrier partners returned no matching flights for {trip.origin} → {trip.destination} on these dates. Try adjusting departure or return dates.
              </p>
            </div>
          )}

          {/* UNAVAILABLE / ERROR STATE */}
          {!isSearchingFlights && flightSearchError && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--status-danger)]/30 space-y-3">
              <div className="w-12 h-12 rounded-full bg-[var(--status-danger)]/10 text-[var(--status-danger)] mx-auto flex items-center justify-center">
                <AlertCircle className="w-6 h-6" />
              </div>
              <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
                Flight search unavailable
              </h4>
              <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">
                {flightSearchError}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  handleSearchFlights({
                    trip_id: trip.trip_id,
                    origin: trip.origin,
                    destination: trip.destination,
                    departure_date: trip.start_date,
                    return_date: trip.end_date,
                    passengers: trip.travelers_count || 1,
                  })
                }
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Retry Search
              </Button>
            </div>
          )}
        </div>
      )}

      {/* TAB CONTENT 3: HOTEL SEARCH & SNAPSHOT SELECTION */}
      {activeTab === 'hotels' && (
        <div className="space-y-6">
          <div className="space-y-1">
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              Curated Accommodations in {trip.destination}
            </h3>
            <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light">
              Check-in: {trip.start_date} • Check-out: {trip.end_date}{trip.travelers_count ? ` • ${trip.travelers_count} Guests` : ''}
            </p>
          </div>

          <HotelSearchForm
            initialCity={trip.destination}
            initialCheckIn={trip.start_date}
            initialCheckOut={trip.end_date}
            initialGuests={trip.travelers_count}
            onSearch={handleSearchHotels}
            isLoading={isSearchingHotels}
          />

          {hotelOffers.length > 0 && (
            <HotelFilterBar
              totalResults={filteredHotelOffers.length}
              sortBy={hotelSortBy}
              onSortChange={setHotelSortBy}
              starFilter={hotelStarFilter}
              onStarFilterChange={setHotelStarFilter}
              freeCancellationOnly={hotelFreeCancellation}
              onFreeCancellationOnlyChange={setHotelFreeCancellation}
            />
          )}

          {isSearchingHotels && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
              <div className="w-10 h-10 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
                <Building2 className="w-5 h-5" />
              </div>
              <p className="text-xs text-[var(--text-secondary)]">
                Curating authentic stays matching your travel style in {trip.destination}...
              </p>
            </div>
          )}

          {!isSearchingHotels && filteredHotelOffers.length > 0 && (
            <div className="space-y-6">
              {filteredHotelOffers.map((hotel) => (
                <HotelCard
                  key={hotel.id}
                  hotel={hotel}
                  nightsCount={canonicalNights || hotel.nights}
                  isSelected={
                    selectedHotel?.id === hotel.id ||
                    selectedHotel?.snapshot_id === hotel.id ||
                    (Boolean(hotel.snapshot_id) && selectedHotel?.id === hotel.snapshot_id) ||
                    (Boolean(hotel.snapshot_id) && selectedHotel?.snapshot_id === hotel.snapshot_id)
                  }
                  onSelect={handleSelectHotelOffer}
                  isSelecting={isSelectingHotel}
                />
              ))}
            </div>
          )}

          {/* EMPTY STATE */}
          {!isSearchingHotels && !hotelSearchError && hotelOffers.length === 0 && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-dashed border-[var(--border)] space-y-3">
              <div className="w-12 h-12 rounded-full bg-[var(--surface-muted)] text-[var(--text-muted)] mx-auto flex items-center justify-center">
                <Building2 className="w-6 h-6" />
              </div>
              <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
                No accommodations found for these dates
              </h4>
              <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">
                No matching properties were returned for {trip.destination} on {trip.start_date} to {trip.end_date}. Try adjusting travel dates or filters.
              </p>
            </div>
          )}

          {/* ERROR STATE */}
          {!isSearchingHotels && hotelSearchError && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--status-danger)]/30 space-y-3">
              <div className="w-12 h-12 rounded-full bg-[var(--status-danger)]/10 text-[var(--status-danger)] mx-auto flex items-center justify-center">
                <AlertCircle className="w-6 h-6" />
              </div>
              <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
                Hotel search unavailable
              </h4>
              <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">
                {hotelSearchError}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (
                    !trip.trip_id ||
                    !trip.destination ||
                    !trip.start_date ||
                    !trip.end_date ||
                    trip.travelers_count == null ||
                    trip.travelers_count < 1
                  ) {
                    setHotelSearchError('Missing canonical trip details for accommodation search.');
                    return;
                  }
                  handleSearchHotels();
                }}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Retry Search
              </Button>
            </div>
          )}
        </div>
      )}

      {/* TAB CONTENT 4: RESEARCH & EVIDENCE DISCOVERY */}
      {activeTab === 'research' && (
        <ResearchView
          tripIdOverride={trip.trip_id}
          onBackOverride={() => setActiveTab('itinerary')}
        />
      )}

      {/* TAB CONTENT 5: REVISIONS & STORY (HUMAN-IN-THE-LOOP) */}
      {activeTab === 'revisions' && (
        <TripRevisionManager
          trip={trip}
          onTripUpdated={(updated) => setTrip(updated)}
          onNavigateTab={(tab) => setActiveTab(tab)}
        />
      )}

      {/* TAB CONTENT 6: BOOKING, APPROVAL & CONFIRMATION (PART 5A) */}
      {activeTab === 'booking' && (
        <BookingWorkspace
          trip={trip}
          onNavigateTab={(tab) => {
            if (tab === 'itinerary' || tab === 'flights' || tab === 'hotels' || tab === 'research') {
              setActiveTab(tab);
            }
          }}
          onBackToTrips={onBack}
        />
      )}

      {/* Edit Constraints Modal */}
      <Modal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        title="Edit Trip Parameters"
        subtitle={`Adjust dates and travelers for ${trip.destination}. Creates a fresh revision state.`}
        maxWidth="md"
      >
        <form onSubmit={handleSaveConstraints} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Departure Date"
              type="date"
              value={editStartDate}
              onChange={(e) => setEditStartDate(e.target.value)}
              required
            />
            <Input
              label="Return Date"
              type="date"
              value={editEndDate}
              onChange={(e) => setEditEndDate(e.target.value)}
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Travelers"
              type="number"
              min="1"
              max="20"
              value={editTravelers}
              onChange={(e) => setEditTravelers(Number(e.target.value))}
              required
            />
            <Input
              label="Budget Limit"
              type="number"
              value={editBudget}
              onChange={(e) => setEditBudget(e.target.value)}
              placeholder="e.g. 4000"
            />
          </div>

          <div className="pt-3 border-t border-[var(--border)] flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setIsEditModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isLoading={isSavingConstraints}
              disabled={isSavingConstraints}
            >
              Save Constraints
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
