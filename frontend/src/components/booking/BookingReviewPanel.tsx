import React from 'react';
import {
  Plane,
  Building2,
  Calendar,
  Users,
  CreditCard,
  CheckCircle2,
  AlertCircle,
  FileText,
  ShieldCheck,
  Luggage,
  Clock,
  Sparkles,
} from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { FlightOffer } from '../../types/flight';
import { HotelOffer } from '../../types/hotel';
import { TravelerContactInfo } from '../../types/bookingFrontend';
import { BookingStatusBadge } from './BookingStatusBadge';
import { formatCurrency } from '../../lib/currency';

interface BookingReviewPanelProps {
  trip: TripDetail;
  flight?: FlightOffer;
  hotel?: HotelOffer;
  contact: TravelerContactInfo;
  isApproved: boolean;
  onEditSection?: (section: 'flights' | 'hotels' | 'itinerary') => void;
}

export const BookingReviewPanel: React.FC<BookingReviewPanelProps> = ({
  trip,
  flight,
  hotel,
  contact,
  isApproved,
  onEditSection,
}) => {
  const flightAmount = flight ? Number(flight.total_amount) : 0;
  const hotelAmount = hotel ? Number(hotel.total_amount) : 0;
  const subtotal = flightAmount + hotelAmount;
  const estimatedTaxes = Math.round(subtotal * 0.08);
  const grandTotal = subtotal + estimatedTaxes;
  const bookingCurrency = trip.currency || flight?.currency || hotel?.currency || 'INR';

  const calculateDays = () => {
    if (!trip.start_date || !trip.end_date) return '6 days';
    try {
      const s = new Date(trip.start_date).getTime();
      const e = new Date(trip.end_date).getTime();
      const diff = Math.ceil((e - s) / (1000 * 60 * 60 * 24));
      return `${Math.max(1, diff)} days`;
    } catch {
      return '6 days';
    }
  };

  return (
    <div className="space-y-8">
      {/* 1. Header: READY FOR YOUR JOURNEY? */}
      <div className="space-y-2 border-b border-[var(--border)] pb-6">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
            READY FOR YOUR JOURNEY?
          </span>
          <span className="text-xs text-[var(--text-muted)] font-mono">· Step 1 of Booking</span>
        </div>

        <h2 className="text-3xl sm:text-4xl lg:text-5xl font-bold font-editorial text-[var(--text-primary)]">
          {trip.destination}
        </h2>

        <div className="flex flex-wrap items-center gap-3 text-xs sm:text-sm text-[var(--text-secondary)] font-light pt-1">
          <span className="flex items-center gap-1.5">
            <Calendar className="w-4 h-4 text-[var(--primary)]" />
            {trip.start_date} — {trip.end_date}
          </span>
          <span aria-hidden="true">·</span>
          <span className="flex items-center gap-1.5">
            <Users className="w-4 h-4 text-[var(--terracotta)]" />
            {trip.travelers_count || 2} travelers
          </span>
          <span aria-hidden="true">·</span>
          <span>{calculateDays()} itinerary</span>
        </div>
      </div>

      {/* 2. YOUR PLAN: Flights, Stay, Itinerary, Budget */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--text-secondary)] font-mono">
            YOUR PLAN
          </h3>
          <span className="text-xs font-mono text-[var(--text-muted)]">
            Revision #{trip.active_revision_number || 1}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Flight Card */}
          <div className="p-5 rounded-3xl border border-[var(--border)] bg-[var(--surface)] shadow-xs space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--primary)] font-mono flex items-center gap-1.5">
                <Plane className="w-3.5 h-3.5" />
                Flight
              </span>
              <BookingStatusBadge status={flight ? 'selected' : 'pending'} />
            </div>

            {flight ? (
              <div className="space-y-2">
                <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
                  {flight.slices?.[0]?.origin || trip.origin} → {flight.slices?.[0]?.destination || trip.destination}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-light">
                  {flight.carrier_name} · Flight {flight.slices?.[0]?.segments?.[0]?.flight_number || 'Direct'} · {flight.cabin_class || 'Economy'}
                </div>
                <div className="text-xs text-[var(--text-muted)] font-mono flex items-center justify-between pt-1 border-t border-[var(--border)]/60">
                  <span>Departure: {trip.start_date}</span>
                  <span className="font-bold text-[var(--text-primary)] font-editorial text-sm">
                    ${Number(flight.total_amount).toLocaleString()}
                  </span>
                </div>
              </div>
            ) : (
              <div className="py-3 text-xs text-[var(--text-muted)]">
                No flight snapshot selected. You can select one from the Flights tab.
              </div>
            )}

            {onEditSection && (
              <button
                type="button"
                onClick={() => onEditSection('flights')}
                className="text-xs text-[var(--primary)] hover:underline font-medium cursor-pointer"
              >
                {flight ? 'Change selected flight' : 'Choose flight'}
              </button>
            )}
          </div>

          {/* Stay Card */}
          <div className="p-5 rounded-3xl border border-[var(--border)] bg-[var(--surface)] shadow-xs space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--terracotta)] font-mono flex items-center gap-1.5">
                <Building2 className="w-3.5 h-3.5" />
                Stay
              </span>
              <BookingStatusBadge status={hotel ? 'selected' : 'pending'} />
            </div>

            {hotel ? (
              <div className="space-y-2">
                <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
                  {hotel.name}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-light">
                  {hotel.stars}-Star property · {hotel.address || trip.destination}
                </div>
                <div className="text-xs text-[var(--text-muted)] font-mono flex items-center justify-between pt-1 border-t border-[var(--border)]/60">
                  <span>{trip.start_date} — {trip.end_date}</span>
                  <span className="font-bold text-[var(--text-primary)] font-editorial text-sm">
                    ${Number(hotel.total_amount).toLocaleString()}
                  </span>
                </div>
              </div>
            ) : (
              <div className="py-3 text-xs text-[var(--text-muted)]">
                No hotel snapshot selected. You can select one from the Stay tab.
              </div>
            )}

            {onEditSection && (
              <button
                type="button"
                onClick={() => onEditSection('hotels')}
                className="text-xs text-[var(--primary)] hover:underline font-medium cursor-pointer"
              >
                {hotel ? 'Change selected stay' : 'Choose stay'}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 3. IMPORTANT DETAILS: Traveler info, Conditions, Cancellation, Price Summary */}
      <div className="space-y-4">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--text-secondary)] font-mono">
          IMPORTANT DETAILS
        </h3>

        <div className="p-6 rounded-3xl border border-[var(--border)] bg-[var(--surface)] space-y-6 text-xs sm:text-sm">
          {/* Traveler Information */}
          <div className="space-y-2">
            <span className="font-semibold font-mono text-[var(--text-primary)] uppercase tracking-wider text-xs flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-[var(--primary)]" />
              Primary Traveler & Contact
            </span>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 p-4 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] text-xs">
              <div>
                <span className="text-[var(--text-muted)] block">Full Name</span>
                <span className="font-semibold text-[var(--text-primary)]">
                  {contact.first_name} {contact.last_name}
                </span>
              </div>
              <div>
                <span className="text-[var(--text-muted)] block">Email Address</span>
                <span className="font-mono text-[var(--text-primary)]">{contact.email}</span>
              </div>
              <div>
                <span className="text-[var(--text-muted)] block">Contact Mobile</span>
                <span className="font-mono text-[var(--text-primary)]">{contact.phone}</span>
              </div>
            </div>
          </div>

          {/* Conditions Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-[var(--border)]">
            {/* Flight Conditions */}
            <div className="space-y-2">
              <span className="font-semibold font-mono text-[var(--text-primary)] uppercase tracking-wider text-xs flex items-center gap-1.5">
                <Luggage className="w-3.5 h-3.5 text-[var(--primary)]" />
                Flight Conditions
              </span>
              <ul className="space-y-1.5 text-xs text-[var(--text-secondary)] font-light">
                <li className="flex items-start gap-1.5">
                  <span className="text-[var(--primary)]">•</span>
                  <span>Standard carry-on (1 personal item + 1 rollaboard included per traveler)</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <span className="text-[var(--primary)]">•</span>
                  <span>Airline ticket will be issued via Duffel verified partner handoff</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <span className="text-[var(--primary)]">•</span>
                  <span>Seat selection open upon carrier ticket issuance</span>
                </li>
              </ul>
            </div>

            {/* Cancellation & Refund Terms */}
            <div className="space-y-2">
              <span className="font-semibold font-mono text-[var(--text-primary)] uppercase tracking-wider text-xs flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-[var(--terracotta)]" />
                Stay & Cancellation Policies
              </span>
              <ul className="space-y-1.5 text-xs text-[var(--text-secondary)] font-light">
                <li className="flex items-start gap-1.5">
                  <span className="text-[var(--terracotta)]">•</span>
                  <span>
                    {hotel?.cancellation_policy || 'Complimentary cancellation up to 48 hours before check-in'}
                  </span>
                </li>
                <li className="flex items-start gap-1.5">
                  <span className="text-[var(--terracotta)]">•</span>
                  <span>Government photo ID and credit/debit card required at property check-in</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <span className="text-[var(--terracotta)]">•</span>
                  <span>Local city tourism tax included in verified quote</span>
                </li>
              </ul>
            </div>
          </div>

          {/* Transparent Price Summary */}
          <div className="p-5 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] space-y-3">
            <span className="font-semibold font-mono text-[var(--text-primary)] uppercase tracking-wider text-xs flex items-center justify-between">
              <span>Price Summary</span>
              <span className="text-[var(--text-muted)]">Verified Quote</span>
            </span>

            <div className="space-y-2 text-xs">
              <div className="flex justify-between text-[var(--text-secondary)]">
                <span>Flight Transportation ({trip.travelers_count || 2} travelers):</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(flightAmount, bookingCurrency)}
                </span>
              </div>
              <div className="flex justify-between text-[var(--text-secondary)]">
                <span>Boutique Accommodation ({calculateDays()}):</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(hotelAmount, bookingCurrency)}
                </span>
              </div>
              <div className="flex justify-between text-[var(--text-secondary)]">
                <span>Taxes & Partner Booking Fees (est. 8%):</span>
                <span className="font-mono text-[var(--text-primary)]">
                  {formatCurrency(estimatedTaxes, bookingCurrency)}
                </span>
              </div>

              <div className="pt-2 border-t border-[var(--border)] flex justify-between items-baseline font-bold">
                <span className="text-sm font-editorial text-[var(--text-primary)]">Total Commitment:</span>
                <span className="text-xl font-editorial text-[var(--primary)] font-bold">
                  {formatCurrency(grandTotal, bookingCurrency)}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
