import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Building2, Sparkles, AlertCircle, RefreshCw, PlusCircle, ArrowRight } from 'lucide-react';
import { HotelOffer, HotelSearchRequest, HotelRoom } from '../../types/hotel';
import { TripSummary } from '../../types/trip';
import { api } from '../../lib/apiClient';
import { useAuth } from '../../context/AuthContext';
import { useNotification } from '../../context/NotificationContext';
import { HotelSearchForm } from '../travel/HotelSearchForm';
import { HotelCard } from '../travel/HotelCard';
import {
  HotelFilterBar,
  HotelSortOption,
  HotelStarFilter,
} from '../travel/HotelFilterBar';
import { Modal } from '../common/Modal';
import { Button } from '../common/Button';

export const HotelsView: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { notify } = useNotification();

  const [hotels, setHotels] = useState<HotelOffer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filter & sort state
  const [sortBy, setSortBy] = useState<HotelSortOption>('rating');
  const [starFilter, setStarFilter] = useState<HotelStarFilter>('all');
  const [freeCancellationOnly, setFreeCancellationOnly] = useState(false);

  // Attach to trip modal state
  const [userTrips, setUserTrips] = useState<TripSummary[]>([]);
  const [selectedHotelToAttach, setSelectedHotelToAttach] = useState<{
    hotel: HotelOffer;
    room?: HotelRoom;
  } | null>(null);
  const [isAttachModalOpen, setIsAttachModalOpen] = useState(false);
  const [isAttaching, setIsAttaching] = useState(false);
  const [selectedTripId, setSelectedTripId] = useState('');
  const selectedTrip = userTrips.find((trip) => trip.trip_id === selectedTripId);

  const handleSearch = useCallback(
    async (request: HotelSearchRequest) => {
      if (!selectedTrip) {
        setHotels([]);
        setError('Select a trip before searching for hotels.');
        return;
      }

      setIsLoading(true);
      setError(null);
      try {
        const results = await api.searchHotels({
          ...request,
          trip_id: selectedTrip.trip_id,
          destination: selectedTrip.destination,
          checkin: selectedTrip.start_date,
          checkout: selectedTrip.end_date,
          adults: request.guests ?? request.adults ?? selectedTrip.travelers_count ?? 1,
          children: request.children ?? 0,
          rooms: request.rooms ?? 1,
        });
        setHotels(results);
      } catch (err: any) {
        const msg = err.message || 'Unable to retrieve hotel listings.';
        setError(msg);
        notify(msg, 'error', 'Hotel Search Error');
      } finally {
        setIsLoading(false);
      }
    },
    [notify, selectedTrip]
  );

  useEffect(() => {
    if (!isAuthenticated) {
      setUserTrips([]);
      setSelectedTripId('');
      return;
    }

    let isActive = true;
    api.getTrips()
      .then((trips) => {
        if (!isActive) return;
        setUserTrips(trips);
        setSelectedTripId((current) =>
          trips.some((trip) => trip.trip_id === current) ? current : ''
        );
      })
      .catch((err: any) => {
        if (!isActive) return;
        const msg = err.message || 'Unable to load trips.';
        setError(msg);
        notify(msg, 'error', 'Trip Loading Error');
      });

    return () => {
      isActive = false;
    };
  }, [isAuthenticated, notify]);

  // In Section 2: Trips attachment data integration will occur in Section 3.
  // trips are defaulted to empty array for now.

  const handleOpenAttachModal = (hotel: HotelOffer, room?: HotelRoom) => {
    setSelectedHotelToAttach({ hotel, room });
    setIsAttachModalOpen(true);
  };

  const handleAttachHotelToTrip = async (tripId: string) => {
    if (!selectedHotelToAttach) return;

    setIsAttaching(true);
    try {
      await api.selectTripOffer(tripId, {
        offer_type: 'hotel',
        offer_id: selectedHotelToAttach.hotel.id,
        snapshot_id: selectedHotelToAttach.hotel.id,
        room_id: selectedHotelToAttach.room?.id,
        metadata: {
          ...selectedHotelToAttach.hotel,
          selected_room: selectedHotelToAttach.room ? {
            id: selectedHotelToAttach.room.id,
            name: selectedHotelToAttach.room.name,
            description: selectedHotelToAttach.room.description,
            price_per_night: selectedHotelToAttach.room.price_per_night,
            total_price: selectedHotelToAttach.room.total_price,
            currency: selectedHotelToAttach.room.currency,
            capacity: selectedHotelToAttach.room.capacity,
            beds: selectedHotelToAttach.room.beds,
          } : undefined,
        },
      });

      notify(
        `Stay (${selectedHotelToAttach.hotel.name}) attached to trip as canonical snapshot.`,
        'success',
        'Accommodation Linked'
      );
      setIsAttachModalOpen(false);
      navigate(`/trips/${tripId}`);
    } catch (err: any) {
      notify(err.message || 'Failed to attach accommodation.', 'error', 'Attachment Error');
    } finally {
      setIsAttaching(false);
    }
  };

  const handlePlanTripWithHotel = (hotel: HotelOffer) => {
    navigate('/trips/new', {
      state: {
        destination: hotel.city,
      },
    });
  };

  // Filtered and sorted hotels
  const filteredHotels = hotels
    .filter((h) => {
      if (starFilter === '4plus' && (h.stars == null || h.stars < 4)) return false;
      if (starFilter === '5only' && (h.stars == null || h.stars < 5)) return false;
      if (
        freeCancellationOnly &&
        !h.cancellation_policy?.toLowerCase().includes('free')
      ) {
        return false;
      }
      return true;
    })
    .sort((a, b) => {
      if (sortBy === 'rating') return (b.rating ?? 0) - (a.rating ?? 0);
      if (sortBy === 'price_low') return a.total_amount - b.total_amount;
      if (sortBy === 'price_high') return b.total_amount - a.total_amount;
      if (sortBy === 'stars_high') return (b.stars ?? 0) - (a.stars ?? 0);
      return 0;
    });

  return (
    <div className="space-y-8 pb-16">
      {/* Editorial Page Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="px-3 py-1 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
            Boutique & Heritage Stays
          </span>
          <span className="text-xs text-[var(--text-muted)] font-mono">
            POST /api/hotels/search
          </span>
        </div>
        <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold font-editorial text-[var(--text-primary)]">
          Discover Handpicked Accommodations
        </h1>
        <p className="text-sm sm:text-base text-[var(--text-secondary)] max-w-2xl font-light">
          From tranquil garden ryokans to cliffside Mediterranean terraces, explore verified
          properties matched to your travel aesthetic.
        </p>
      </div>

      {/* Hotel Search Form */}
      <div className="space-y-3">
        <label className="block text-sm font-medium text-[var(--text-primary)]" htmlFor="hotel-trip">
          Search within trip
        </label>
        <select
          id="hotel-trip"
          value={selectedTripId}
          onChange={(event) => {
            setSelectedTripId(event.target.value);
            setHotels([]);
            setError(null);
          }}
          className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm"
          disabled={isLoading || userTrips.length === 0}
        >
          <option value="">
            {userTrips.length === 0 ? 'No trips available' : 'Select a trip'}
          </option>
          {userTrips.map((trip) => (
            <option key={trip.trip_id} value={trip.trip_id}>
              {trip.title || trip.destination}
            </option>
          ))}
        </select>
        <HotelSearchForm
          key={selectedTripId || 'no-trip'}
          initialCity={selectedTrip?.destination || ''}
          initialCheckIn={selectedTrip?.start_date || ''}
          initialCheckOut={selectedTrip?.end_date || ''}
          initialGuests={selectedTrip?.travelers_count || 1}
          onSearch={handleSearch}
          isLoading={isLoading}
          disabled={!selectedTripId}
        />
      </div>

      {/* Filter and Sort Controls */}
      {hotels.length > 0 && (
        <HotelFilterBar
          totalResults={filteredHotels.length}
          sortBy={sortBy}
          onSortChange={setSortBy}
          starFilter={starFilter}
          onStarFilterChange={setStarFilter}
          freeCancellationOnly={freeCancellationOnly}
          onFreeCancellationOnlyChange={setFreeCancellationOnly}
        />
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="py-20 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-4">
          <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
            <Building2 className="w-6 h-6" />
          </div>
          <div className="space-y-1">
            <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
              Curating premier stays...
            </h4>
            <p className="text-xs text-[var(--text-secondary)]">
              Checking room categories, private bath amenities & verified guest ratings.
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
              Unable to load accommodation listings.
            </h4>
            <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">{error}</p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => selectedTripId && handleSearch({ trip_id: selectedTripId })}
            disabled={!selectedTripId}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          >
            Retry Search
          </Button>
        </div>
      )}

      {/* Hotel Cards List */}
      {!isLoading && !error && filteredHotels.length > 0 && (
        <div className="space-y-6">
          {filteredHotels.map((hotel) => (
            <HotelCard
              key={hotel.id}
              hotel={hotel}
              nightsCount={hotel.nights}
              onPlanTripWithHotel={handlePlanTripWithHotel}
              onSelect={
                isAuthenticated
                  ? (h, room) => handleOpenAttachModal(h, room)
                  : undefined
              }
            />
          ))}
        </div>
      )}

      {/* No matching results from filters */}
      {!isLoading && !error && hotels.length > 0 && filteredHotels.length === 0 && (
        <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            No properties match your active filters.
          </p>
          <p className="text-xs text-[var(--text-secondary)]">
            Try adjusting star ratings or clearing cancellation restrictions.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setStarFilter('all');
              setFreeCancellationOnly(false);
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
        title="Attach Stay to Trip"
        subtitle={
          selectedHotelToAttach
            ? `Link ${selectedHotelToAttach.hotel.name} to your canonical trip workspace.`
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
                  if (selectedHotelToAttach) {
                    handlePlanTripWithHotel(selectedHotelToAttach.hotel);
                  }
                }}
                leftIcon={<PlusCircle className="w-3.5 h-3.5" />}
              >
                Create New Trip with This Stay
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
                  onClick={() => handleAttachHotelToTrip(t.trip_id)}
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
