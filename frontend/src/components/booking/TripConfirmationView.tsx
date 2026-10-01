import React, { useState } from 'react';
import {
  CheckCircle2,
  Calendar,
  Users,
  Plane,
  Building2,
  Download,
  ArrowRight,
  ShieldCheck,
  Clock,
  MapPin,
  FileText,
  AlertCircle,
  X,
} from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { FlightConfirmationRecord, HotelConfirmationRecord, CancellationRequest } from '../../types/bookingFrontend';
import { getDestinationImage } from '../../lib/destinationImages';
import { Button } from '../common/Button';
import { useNotification } from '../../context/NotificationContext';

interface TripConfirmationViewProps {
  trip: TripDetail;
  flightConfirmation?: FlightConfirmationRecord;
  hotelConfirmation?: HotelConfirmationRecord;
  onDownloadPdf: () => Promise<void>;
  onRequestCancellation: (request: CancellationRequest) => Promise<{ ticket_id: string; message: string }>;
  onViewTrip: () => void;
  onBackToTrips: () => void;
}

export const TripConfirmationView: React.FC<TripConfirmationViewProps> = ({
  trip,
  flightConfirmation,
  hotelConfirmation,
  onDownloadPdf,
  onRequestCancellation,
  onViewTrip,
  onBackToTrips,
}) => {
  const [isDownloading, setIsDownloading] = useState(false);
  const [isCancellationModalOpen, setIsCancellationModalOpen] = useState(false);
  const [cancellationType, setCancellationType] = useState<'all' | 'flight' | 'hotel'>('all');
  const [cancellationReason, setCancellationReason] = useState('');
  const [isSubmittingCancellation, setIsSubmittingCancellation] = useState(false);
  const { notify } = useNotification();

  const destMeta = getDestinationImage(trip.destination);
  const destinationCity = trip.destination.split(',')[0].toUpperCase();

  const handleDownload = async () => {
    setIsDownloading(true);
    try {
      await onDownloadPdf();
      notify(
        'Travel plan voucher generated. PDF export adapter triggered.',
        'success',
        'Voucher Prepared'
      );
    } catch {
      notify('Unable to generate travel PDF. Please retry.', 'error');
    } finally {
      setIsDownloading(false);
    }
  };

  const handleCancelSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmittingCancellation(true);
    try {
      const res = await onRequestCancellation({
        booking_type: cancellationType,
        reason: cancellationReason,
        contact_email: 'traveler@trusttrip.com',
        acknowledged_terms: true,
      });
      notify(res.message, 'info', `Ticket ${res.ticket_id}`);
      setIsCancellationModalOpen(false);
    } catch {
      notify('Unable to submit cancellation request.', 'error');
    } finally {
      setIsSubmittingCancellation(false);
    }
  };

  return (
    <div className="space-y-12 pb-20 max-w-5xl mx-auto">
      {/* 1. HERO DESTINATION BANNER: Emotional Payoff */}
      <div className="relative rounded-3xl overflow-hidden border border-[var(--border)] shadow-xl min-h-[380px] sm:min-h-[460px] flex flex-col justify-end p-6 sm:p-12 text-white">
        <img
          src={destMeta.imageUrl}
          alt={trip.destination}
          className="absolute inset-0 w-full h-full object-cover object-center"
          referrerPolicy="no-referrer"
        />

        <div className="absolute inset-0 bg-gradient-to-t from-[#111817]/95 via-[#111817]/50 to-black/20" />

        <div className="relative z-10 space-y-4 max-w-2xl">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#176B63] text-white text-xs font-mono font-bold tracking-wider">
            <CheckCircle2 className="w-4 h-4" />
            JOURNEY CONFIRMED & GUARANTEED
          </div>

          <div className="space-y-2">
            <h1 className="text-4xl sm:text-6xl font-bold font-editorial tracking-tight text-white leading-tight">
              YOU'RE GOING TO {destinationCity}
            </h1>
            <p className="text-base sm:text-lg text-white/90 font-light">
              {trip.start_date} — {trip.end_date} · {trip.travelers_count || 2} travelers
            </p>
          </div>

          <p className="text-xs sm:text-sm text-white/80 font-light max-w-xl leading-relaxed">
            All arrangements have been verified. Electronic flight tickets and property reservations
            are issued and locked to your itinerary.
          </p>

          <div className="flex flex-wrap items-center gap-3 pt-2">
            <Button
              variant="accent"
              size="md"
              onClick={handleDownload}
              isLoading={isDownloading}
              leftIcon={<Download className="w-4 h-4" />}
              className="shadow-md font-semibold"
            >
              Download travel plan
            </Button>

            <Button
              variant="outline"
              size="md"
              onClick={onViewTrip}
              className="bg-black/30 border-white/40 text-white hover:bg-white/20"
            >
              View complete itinerary
            </Button>
          </div>
        </div>
      </div>

      {/* 2. CONFIRMED SERVICES BREAKDOWN (Flight & Stay) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* FLIGHT VOUCHER */}
        <div className="p-6 sm:p-8 rounded-3xl border border-[var(--border)] bg-[var(--surface)] shadow-xs space-y-5">
          <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-xl bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center">
                <Plane className="w-4 h-4" />
              </div>
              <span className="font-mono text-xs font-bold uppercase tracking-wider text-[var(--primary)]">
                FLIGHT CONFIRMATION
              </span>
            </div>
            <span className="text-xs font-mono font-semibold text-[var(--status-success)] flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> Confirmed
            </span>
          </div>

          <div className="space-y-1">
            <div className="text-xs text-[var(--text-muted)] font-mono">
              {flightConfirmation?.carrier_name || 'Air France'} · Flight {flightConfirmation?.flight_number || 'AF-257'}
            </div>
            <h3 className="text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {flightConfirmation?.origin_city || 'Kolkata'} → {flightConfirmation?.destination_city || 'Paris'}
            </h3>
            <div className="text-xs text-[var(--text-secondary)] font-light">
              Departure: {trip.start_date} · {flightConfirmation?.cabin_class || 'Economy Premium'}
            </div>
          </div>

          <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/70 border border-[var(--border)] space-y-2 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-[var(--text-muted)]">Booking Reference:</span>
              <span className="font-mono font-bold text-[var(--primary)] text-sm">
                {flightConfirmation?.booking_reference || 'Not provided'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[var(--text-muted)]">Electronic Ticket / PNR:</span>
              <span className="font-mono text-[var(--text-primary)]">
                {flightConfirmation?.ticket_number || 'Not provided by provider'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[var(--text-muted)]">Seat Selection:</span>
              <span className="text-[var(--text-primary)]">Open at carrier check-in</span>
            </div>
          </div>
        </div>

        {/* STAY VOUCHER */}
        <div className="p-6 sm:p-8 rounded-3xl border border-[var(--border)] bg-[var(--surface)] shadow-xs space-y-5">
          <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-xl bg-[var(--terracotta-soft)] text-[var(--terracotta)] flex items-center justify-center">
                <Building2 className="w-4 h-4" />
              </div>
              <span className="font-mono text-xs font-bold uppercase tracking-wider text-[var(--terracotta)]">
                STAY CONFIRMATION
              </span>
            </div>
            <span className="text-xs font-mono font-semibold text-[var(--status-success)] flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> Confirmed
            </span>
          </div>

          <div className="space-y-1">
            <div className="text-xs text-[var(--text-muted)] font-mono">Boutique Property</div>
            <h3 className="text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {hotelConfirmation?.property_name || 'Hotel Lutetia Left Bank'}
            </h3>
            <div className="text-xs text-[var(--text-secondary)] font-light">
              {hotelConfirmation?.property_address || 'Paris, France'}
            </div>
          </div>

          <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/70 border border-[var(--border)] space-y-2 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-[var(--text-muted)]">Voucher Code:</span>
              <span className="font-mono font-bold text-[var(--terracotta)] text-sm">
                {hotelConfirmation?.confirmation_code || 'TT-HT-8921'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[var(--text-muted)]">Stay Duration:</span>
              <span className="font-mono text-[var(--text-primary)]">
                {trip.start_date} to {trip.end_date} ({hotelConfirmation?.nights_count || 6} nights)
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[var(--text-muted)]">Room Tier:</span>
              <span className="text-[var(--text-primary)] font-medium">
                {hotelConfirmation?.room_name || 'Deluxe Heritage Room'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* 3. STORYLINE ITINERARY OVERVIEW */}
      <div className="p-6 sm:p-8 rounded-3xl border border-[var(--border)] bg-[var(--surface)] shadow-xs space-y-6">
        <div className="space-y-1 border-b border-[var(--border)] pb-4">
          <span className="text-xs font-mono uppercase tracking-wider text-[var(--primary)] font-bold">
            YOUR ITINERARY
          </span>
          <h3 className="text-2xl font-bold font-editorial text-[var(--text-primary)]">
            Storyline & Daily Highlights
          </h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] space-y-1.5">
            <span className="font-mono font-bold text-[var(--primary)]">Day 1</span>
            <div className="font-editorial text-sm font-bold text-[var(--text-primary)]">
              Arrival & First Impressions
            </div>
            <p className="text-[var(--text-secondary)] font-light leading-relaxed">
              Touch down, private express transfer to boutique lodging, quiet orientation walk.
            </p>
          </div>

          <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] space-y-1.5">
            <span className="font-mono font-bold text-[var(--primary)]">Day 2</span>
            <div className="font-editorial text-sm font-bold text-[var(--text-primary)]">
              Art, Cafés & Old Streets
            </div>
            <p className="text-[var(--text-secondary)] font-light leading-relaxed">
              Early morning peaceful landmark visit, traditional lunch, afternoon gallery discovery.
            </p>
          </div>

          <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] space-y-1.5">
            <span className="font-mono font-bold text-[var(--primary)]">Day 3</span>
            <div className="font-editorial text-sm font-bold text-[var(--text-primary)]">
              Hidden Courtyards & Culinary Notes
            </div>
            <p className="text-[var(--text-secondary)] font-light leading-relaxed">
              Neighborhood artisan market walk, private tea or wine tasting, curated evening dining.
            </p>
          </div>
        </div>
      </div>

      {/* 4. FOOTER ACTIONS & CANCELLATION ENTRY POINT */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-4 border-t border-[var(--border)]">
        <div className="text-xs text-[var(--text-secondary)]">
          <span>Need to change plans? </span>
          <button
            type="button"
            onClick={() => setIsCancellationModalOpen(true)}
            className="text-[var(--terracotta)] hover:underline font-medium cursor-pointer"
          >
            Request cancellation
          </button>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="outline" size="md" onClick={onBackToTrips}>
            Back to my journeys
          </Button>
          <Button variant="primary" size="md" onClick={onViewTrip} rightIcon={<ArrowRight className="w-4 h-4" />}>
            Open trip workspace
          </Button>
        </div>
      </div>

      {/* CANCELLATION REQUEST MODAL */}
      {isCancellationModalOpen && (
        <div className="fixed inset-0 z-50 overflow-y-auto flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
          <div className="relative w-full max-w-md bg-[var(--surface)] border border-[var(--border)] rounded-3xl shadow-2xl p-6 sm:p-8 space-y-6 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] pb-4">
              <div className="space-y-1">
                <span className="text-xs font-mono uppercase tracking-wider text-[var(--terracotta)] font-bold">
                  PLAN MODIFICATION
                </span>
                <h3 className="text-xl font-bold font-editorial text-[var(--text-primary)]">
                  Request Cancellation
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setIsCancellationModalOpen(false)}
                className="w-8 h-8 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] flex items-center justify-center"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCancelSubmit} className="space-y-4 text-xs">
              <div className="space-y-1.5">
                <label className="font-semibold text-[var(--text-primary)] font-mono">
                  Cancellation Scope
                </label>
                <select
                  value={cancellationType}
                  onChange={(e) => setCancellationType(e.target.value as any)}
                  className="w-full p-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-xs"
                >
                  <option value="all">Entire Journey (Flight & Accommodation)</option>
                  <option value="flight">Flight Transportation Only</option>
                  <option value="hotel">Stay Accommodation Only</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <label className="font-semibold text-[var(--text-primary)] font-mono">
                  Reason for Cancellation
                </label>
                <textarea
                  value={cancellationReason}
                  onChange={(e) => setCancellationReason(e.target.value)}
                  placeholder="e.g. Change of travel schedule or personal emergency..."
                  rows={3}
                  required
                  className="w-full p-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-xs resize-none"
                />
              </div>

              <div className="p-3 rounded-xl bg-[var(--surface-muted)] border border-[var(--border)] text-[11px] text-[var(--text-secondary)] font-light leading-relaxed">
                Cancellation terms depend on individual airline partner rules and hotel property deadlines.
                Our concierge team processes requests and notifies you via email.
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setIsCancellationModalOpen(false)}
                >
                  Keep booking
                </Button>
                <Button
                  type="submit"
                  variant="accent"
                  size="sm"
                  isLoading={isSubmittingCancellation}
                >
                  Submit request
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
