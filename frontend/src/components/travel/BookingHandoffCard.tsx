import React, { useState, useEffect } from 'react';
import {
  ShieldCheck,
  Plane,
  Building2,
  Clock,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Sparkles,
  Calendar,
  Lock,
} from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { FlightOffer, BookingIntent } from '../../types/flight';
import { HotelOffer } from '../../types/hotel';
import { api } from '../../lib/apiClient';
import { useNotification } from '../../context/NotificationContext';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface BookingHandoffCardProps {
  trip: TripDetail;
  onNavigateTab: (tab: 'flights' | 'hotels') => void;
}

export const BookingHandoffCard: React.FC<BookingHandoffCardProps> = ({
  trip,
  onNavigateTab,
}) => {
  const [bookingIntent, setBookingIntent] = useState<BookingIntent | null>(null);
  const [isPreparing, setIsPreparing] = useState(false);
  const [isRevalidating, setIsRevalidating] = useState(false);
  const [timeLeft, setTimeLeft] = useState<string>('20:00');
  const { notify } = useNotification();

  const flight = trip.selected_flight_offer as FlightOffer | undefined;
  const hotel = trip.selected_hotel_offer as HotelOffer | undefined;

  // Calculate pricing breakdown
  const flightAmount = flight ? Number(flight.total_amount) : 0;
  const hotelAmount = hotel ? Number(hotel.total_amount) : 0;
  const subtotal = flightAmount + hotelAmount;
  const estimatedTaxes = Math.round(subtotal * 0.08);
  const grandTotal = subtotal + estimatedTaxes;
  const bookingCurrency = trip.currency || flight?.currency || hotel?.currency || 'INR';

  // Countdown timer for prepared intent
  useEffect(() => {
    if (!bookingIntent?.expires_at) return;

    const interval = setInterval(() => {
      const now = new Date().getTime();
      const expires = new Date(bookingIntent.expires_at).getTime();
      const diff = expires - now;

      if (diff <= 0) {
        setTimeLeft('Expired');
        clearInterval(interval);
      } else {
        const mins = Math.floor(diff / (1000 * 60));
        const secs = Math.floor((diff % (1000 * 60)) / 1000);
        setTimeLeft(`${mins}:${secs < 10 ? '0' : ''}${secs}`);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [bookingIntent?.expires_at]);

  const handlePrepareBooking = async () => {
    if (!flight) {
      notify('Please select a canonical flight snapshot first.', 'warning', 'Flight Required');
      return;
    }

    setIsPreparing(true);
    try {
      const intent = await api.prepareFlightBooking({
        trip_id: trip.trip_id,
        offer_id: flight.id,
        snapshot_id: flight.snapshot_id || `fl_snap_${flight.id}`,
        amount: String(flightAmount),
        currency: flight.currency || 'USD',
      });

      setBookingIntent(intent);
      notify(
        'Flight fare and seat inventory locked with airline partner. Price guaranteed.',
        'success',
        'Booking Intent Prepared'
      );
    } catch (err: any) {
      notify(err.message || 'Unable to prepare booking intent.', 'error', 'Booking Preparation Failed');
    } finally {
      setIsPreparing(false);
    }
  };

  const handleRevalidateBooking = async () => {
    if (!bookingIntent) return;

    setIsRevalidating(true);
    try {
      const updated = await api.revalidateFlightBooking(
        bookingIntent.intent_id,
        bookingIntent.offer_id,
        bookingIntent.snapshot_id
      );

      setBookingIntent(updated);
      notify('Fare, taxes, and baggage inventory revalidated with live GDS.', 'info', 'Fare Revalidated');
    } catch (err: any) {
      notify(err.message || 'Revalidation failed.', 'error', 'Revalidation Notice');
    } finally {
      setIsRevalidating(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Banner: Canonical Snapshot Status */}
      <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-[var(--border)]">
          <div>
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
                Phase 9 Foundation
              </span>
              <span className="text-xs text-[var(--text-muted)] font-mono">
                Revision #{trip.active_revision_number || 1}
              </span>
            </div>
            <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)] mt-1.5">
              Trip Booking & Snapshot Handoff
            </h3>
            <p className="text-xs sm:text-sm text-[var(--text-secondary)] mt-1 font-light">
              Review canonical snapshots before ticket issuance and accommodation guarantee.
            </p>
          </div>

          {bookingIntent && (
            <div className="p-3.5 rounded-2xl bg-[var(--status-success)]/10 border border-[var(--status-success)]/30 flex items-center gap-3 shrink-0">
              <div className="w-10 h-10 rounded-xl bg-[var(--status-success)] text-white flex items-center justify-center">
                <Clock className="w-5 h-5 animate-pulse" />
              </div>
              <div>
                <div className="text-[11px] font-semibold text-[var(--status-success)] uppercase font-mono tracking-wider">
                  Fare Locked
                </div>
                <div className="text-lg font-bold font-mono text-[var(--text-primary)]">
                  {timeLeft} remaining
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Selected Services Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-6">
          {/* Flight Snapshot Card */}
          <div
            className={`p-5 rounded-2xl border transition-all ${
              flight
                ? 'bg-[var(--surface-muted)]/40 border-[var(--primary)]/40'
                : 'bg-[var(--surface-muted)]/20 border-dashed border-[var(--border-strong)]'
            }`}
          >
            <div className="flex items-center justify-between gap-2 mb-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--primary)] font-mono flex items-center gap-1.5">
                <Plane className="w-3.5 h-3.5" />
                Flight Snapshot
              </span>
              {flight ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-[var(--status-success)] font-medium">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Confirmed Offer
                </span>
              ) : (
                <span className="text-[11px] text-[var(--terracotta)] font-medium">
                  Not selected
                </span>
              )}
            </div>

            {flight ? (
              <div className="space-y-2">
                <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
                  {flight.carrier_name}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-mono">
                  {flight.slices && flight.slices.length > 1
                    ? `${flight.slices[0]?.origin} ⇄ ${flight.slices[0]?.destination}`
                    : `${flight.slices[0]?.origin} ➔ ${flight.slices[0]?.destination}`} • {flight.cabin_class?.toUpperCase()}
                </div>
                <div className="flex items-center justify-between pt-2 border-t border-[var(--border)] text-xs">
                  <span className="font-mono text-[11px] text-[var(--text-muted)]">
                    Snapshot: {flight.snapshot_id || flight.id}
                  </span>
                  <span className="font-bold text-sm text-[var(--text-primary)] font-mono">
                    {formatCurrency(flight.total_amount, flight.currency)}
                  </span>
                </div>
              </div>
            ) : (
              <div className="text-center py-6 space-y-2">
                <p className="text-xs text-[var(--text-secondary)]">
                  No flight selected for this trip yet.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onNavigateTab('flights')}
                >
                  Search Flights for Trip
                </Button>
              </div>
            )}
          </div>

          {/* Hotel Snapshot Card */}
          <div
            className={`p-5 rounded-2xl border transition-all ${
              hotel
                ? 'bg-[var(--surface-muted)]/40 border-[var(--primary)]/40'
                : 'bg-[var(--surface-muted)]/20 border-dashed border-[var(--border-strong)]'
            }`}
          >
            <div className="flex items-center justify-between gap-2 mb-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--primary)] font-mono flex items-center gap-1.5">
                <Building2 className="w-3.5 h-3.5" />
                Accommodation Snapshot
              </span>
              {hotel ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-[var(--status-success)] font-medium">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Confirmed Stay
                </span>
              ) : (
                <span className="text-[11px] text-[var(--terracotta)] font-medium">
                  Not selected
                </span>
              )}
            </div>

            {hotel ? (
              <div className="space-y-2">
                <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
                  {hotel.name}
                </div>
                <div className="text-xs text-[var(--text-secondary)]">
                  {hotel.address} • {hotel.stars}-Star Residence
                </div>
                <div className="flex items-center justify-between pt-2 border-t border-[var(--border)] text-xs">
                  <span className="font-mono text-[11px] text-[var(--text-muted)]">
                    Stay ID: {hotel.id}
                  </span>
                  <span className="font-bold text-sm text-[var(--text-primary)] font-mono">
                    {formatCurrency(hotel.total_amount, hotel.currency)}
                  </span>
                </div>
              </div>
            ) : (
              <div className="text-center py-6 space-y-2">
                <p className="text-xs text-[var(--text-secondary)]">
                  No boutique stay attached yet.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onNavigateTab('hotels')}
                >
                  Explore Stays for Trip
                </Button>
              </div>
            )}
          </div>
        </div>

        {/* Pricing Summary & Handoff Action */}
        <div className="mt-8 pt-6 border-t border-[var(--border)] grid grid-cols-1 md:grid-cols-12 gap-6 items-end">
          <div className="md:col-span-7 space-y-2 text-xs">
            <h5 className="font-semibold text-[var(--text-primary)] font-mono uppercase tracking-wider">
              Transparent Fare Calculation
            </h5>
            <div className="space-y-1.5 max-w-sm text-[var(--text-secondary)]">
              <div className="flex justify-between">
                <span>Flight Transportation ({trip.travelers_count || 1} pax):</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(flightAmount, bookingCurrency)}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Curated Lodging & Taxes:</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(hotelAmount, bookingCurrency)}
                </span>
              </div>
              <div className="flex justify-between">
                <span>Estimated Local Tourism Taxes & Facility Fees:</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(estimatedTaxes, bookingCurrency)}
                </span>
              </div>
              <div className="flex justify-between pt-2 border-t border-[var(--border)] text-sm font-bold text-[var(--text-primary)]">
                <span>Total Estimated Trip Commitment:</span>
                <span className="font-editorial text-lg text-[var(--primary)] font-mono">
                  {formatCurrency(grandTotal, bookingCurrency)}
                </span>
              </div>
            </div>
          </div>

          <div className="md:col-span-5 flex flex-col items-end gap-3">
            {!bookingIntent ? (
              <Button
                variant="primary"
                size="lg"
                onClick={handlePrepareBooking}
                isLoading={isPreparing}
                disabled={!flight}
                rightIcon={<ArrowRight className="w-4 h-4" />}
                className="w-full sm:w-auto shadow-md"
              >
                Prepare Flight Booking (Phase 9)
              </Button>
            ) : (
              <div className="flex flex-col sm:flex-row items-center gap-2 w-full sm:w-auto">
                <Button
                  variant="outline"
                  size="md"
                  onClick={handleRevalidateBooking}
                  isLoading={isRevalidating}
                  leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
                >
                  Revalidate Fare
                </Button>
                <Button
                  variant="accent"
                  size="md"
                  onClick={() =>
                    notify(
                      'Ready for Phase 9 flight passenger details & final payment handoff.',
                      'success',
                      'Handoff Ready'
                    )
                  }
                  rightIcon={<Lock className="w-4 h-4" />}
                >
                  Proceed to Payment
                </Button>
              </div>
            )}

            <span className="text-[11px] text-[var(--text-muted)] flex items-center gap-1 text-right">
              <ShieldCheck className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
              Direct GDS token authorization with PCI DSS certified partner
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
