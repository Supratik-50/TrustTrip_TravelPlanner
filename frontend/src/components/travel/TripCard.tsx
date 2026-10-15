import React, { useState } from 'react';
import { ArrowRight, Calendar, Users, MapPin, Download, CheckCircle2, Trash2 } from 'lucide-react';
import { TripSummary } from '../../types/trip';
import { getDestinationImage } from '../../lib/destinationImages';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';
import { bookingService } from '../../services/bookingService';
import { api } from '../../lib/apiClient';

interface TripCardProps {
  trip: TripSummary;
  onOpen: (tripId: string) => void;
  onContinuePlanning?: (tripId: string) => void;
  onDelete?: (tripId: string) => void;
  featured?: boolean;
}

export const TripCard: React.FC<TripCardProps> = ({
  trip,
  onOpen,
  onContinuePlanning,
  onDelete,
  featured = false,
}) => {
  const [imgError, setImgError] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const destMeta = getDestinationImage(trip.destination);

  const isOngoing = (() => {
    if (!trip.start_date || !trip.end_date) return false;
    if (trip.status !== 'approved' && trip.status !== 'active') return false;
    try {
      const today = new Date().toISOString().slice(0, 10);
      return trip.start_date <= today && today <= trip.end_date;
    } catch {
      return false;
    }
  })();

  const hasConfirmedFlights = Boolean(
    trip.flights &&
      trip.flights.some((f: any) => f && (f.booking_status === 'booked' || f.booking_status === 'completed'))
  );

  const hasConfirmedHotels = Boolean(
    trip.hotels &&
      trip.hotels.some((h: any) => h && (h.booking_status === 'booked' || h.booking_status === 'confirmed'))
  );

  const ineligibilityReason = isOngoing
    ? 'Cannot delete an ongoing trip.'
    : hasConfirmedFlights || hasConfirmedHotels
    ? 'Cannot delete trip with confirmed bookings.'
    : null;

  const handleDeleteClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (isDeleting) return;

    setIsDeleting(true);
    try {
      // 1. Authoritative server-side deletion eligibility check
      const check = await api.getTripDeletionEligibility(trip.trip_id);
      if (!check.eligible) {
        alert(check.reason || 'This trip is not eligible for deletion under current business rules.');
        return;
      }

      // 2. User confirmation dialog
      const tripName = trip.title || trip.destination || 'this journey';
      if (!window.confirm(`Are you sure you want to delete "${tripName}"? This draft itinerary will be removed.`)) {
        return;
      }

      // 3. Safe atomic deletion
      const res = await api.deleteTrip(trip.trip_id);
      if (res.success && onDelete) {
        onDelete(trip.trip_id);
      }
    } catch (err: any) {
      const msg = err.message || err.detail || 'Failed to delete trip.';
      alert(msg);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleDownloadTicket = async (e: React.MouseEvent, flightId: string, bookingRef?: string) => {
    e.stopPropagation();
    e.preventDefault();
    setDownloadingId(flightId);
    try {
      await bookingService.downloadTicketPdf(flightId, bookingRef);
    } catch (err) {
      console.error('Failed to download flight ticket PDF:', err);
    } finally {
      setDownloadingId(null);
    }
  };

  const handleDownloadVoucher = async (e: React.MouseEvent, hotelId: string, bookingRef?: string) => {
    e.stopPropagation();
    e.preventDefault();
    setDownloadingId(hotelId);
    try {
      if (bookingService.downloadHotelVoucherPdf) {
        await bookingService.downloadHotelVoucherPdf(hotelId, bookingRef);
      }
    } catch (err) {
      console.error('Failed to download hotel voucher PDF:', err);
    } finally {
      setDownloadingId(null);
    }
  };

  const handleDownloadReceipt = async (e: React.MouseEvent, transactionId: string, bookingRef?: string) => {
    e.stopPropagation();
    e.preventDefault();
    setDownloadingId(transactionId);
    try {
      if (bookingService.downloadPaymentReceiptPdf) {
        await bookingService.downloadPaymentReceiptPdf(transactionId, bookingRef);
      }
    } catch (err) {
      console.error('Failed to download payment receipt PDF:', err);
    } finally {
      setDownloadingId(null);
    }
  };

  const formatDateRange = (start?: string, end?: string) => {
    if (!start) return '';
    try {
      const s = new Date(start).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const e = end ? new Date(end).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
      return `${s} — ${e}`;
    } catch {
      return `${start} - ${end}`;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'approved':
      case 'booked':
        return { label: 'Approved Plan', color: 'text-[var(--status-success)]' };
      case 'planning':
        return { label: 'Planning Workspace', color: 'text-[var(--primary)]' };
      default:
        return { label: 'Draft Itinerary', color: 'text-[var(--text-secondary)]' };
    }
  };

  const statusInfo = getStatusBadge(trip.status);

  if (featured) {
    return (
      <div className="group relative rounded-3xl overflow-hidden border border-[var(--border)] shadow-md min-h-[360px] sm:min-h-[400px] flex flex-col justify-end p-6 sm:p-10 transition-all duration-300">
        {/* Background Image */}
        {!imgError ? (
          <img
            src={destMeta.imageUrl}
            alt={trip.destination}
            onError={() => setImgError(true)}
            className="absolute inset-0 w-full h-full object-cover object-center group-hover:scale-[1.02] transition-transform duration-700 ease-out"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className={`absolute inset-0 bg-gradient-to-tr ${destMeta.fallbackGradient}`} />
        )}

        {/* Contrast Scrim */}
        <div className="absolute inset-0 bg-gradient-to-t from-[#111817]/95 via-[#111817]/45 to-black/10" />

        {/* Content */}
        <div className="relative z-10 flex flex-col md:flex-row md:items-end justify-between gap-6 text-white">
          <div className="space-y-3 max-w-xl">
            <div className="flex items-center gap-2 text-xs">
              <span className="bg-white/20 backdrop-blur-md px-2.5 py-0.5 rounded text-[11px] font-mono tracking-wider uppercase text-[#E7A07F]">
                Active Trip
              </span>
              <span aria-hidden="true" className="text-white/40">·</span>
              <span className="text-white/80 font-mono text-[11px]">Rev {trip.active_revision_number || 1}</span>
            </div>

            <div>
              <h3 className="text-3xl sm:text-5xl font-bold font-editorial tracking-tight text-white leading-none">
                {trip.destination.toUpperCase()}
              </h3>
              <p className="text-sm sm:text-base text-[#E9EEF0] font-light mt-2 line-clamp-1">
                {trip.title || `${trip.destination} Journey`}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-y-1 gap-x-3 text-xs text-[#E9EEF0]/90 pt-1">
              <span className="flex items-center gap-1.5 bg-black/30 backdrop-blur-xs px-2.5 py-1 rounded">
                <Calendar className="w-3.5 h-3.5 text-[#E7A07F]" />
                <span>{formatDateRange(trip.start_date, trip.end_date)}</span>
              </span>
              <span className="flex items-center gap-1.5 bg-black/30 backdrop-blur-xs px-2.5 py-1 rounded">
                <Users className="w-3.5 h-3.5 text-[#E7A07F]" />
                <span>{trip.travelers_count || 1} travelers</span>
              </span>
              {trip.budget && (
                <span className="bg-black/30 backdrop-blur-xs px-2.5 py-1 rounded font-mono tabular-nums">
                  {formatCurrency(trip.budget, trip.currency)}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-3 shrink-0">
            {onContinuePlanning && (
              <Button
                variant="outline"
                size="md"
                onClick={() => onContinuePlanning(trip.trip_id)}
                className="bg-white/10 hover:bg-white/20 text-white border-white/30 backdrop-blur-md"
              >
                Continue Planning
              </Button>
            )}
            <Button
              variant="primary"
              size="md"
              onClick={() => onOpen(trip.trip_id)}
              rightIcon={<ArrowRight className="w-4 h-4" />}
            >
              Open Trip
            </Button>
            <button
              type="button"
              disabled={isDeleting}
              onClick={handleDeleteClick}
              title={isDeleting ? 'Deleting trip...' : `Delete ${trip.title || trip.destination}`}
              aria-label={`Delete trip ${trip.title || trip.destination}`}
              className={`p-2 rounded-xl transition-all cursor-pointer ${
                isDeleting
                  ? 'bg-black/20 text-white/30 cursor-not-allowed opacity-40'
                  : 'bg-black/40 hover:bg-red-500/30 text-white/80 hover:text-red-300 backdrop-blur-md'
              }`}
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Standard Visual Trip Card
  return (
    <div
      onClick={() => onOpen(trip.trip_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onOpen(trip.trip_id)}
      className="group bg-[var(--surface)] rounded-2xl border border-[var(--border)] overflow-hidden shadow-xs hover:shadow-md transition-all duration-300 flex flex-col justify-between cursor-pointer"
    >
      {/* Visual Header Image */}
      <div className="relative h-44 sm:h-48 w-full overflow-hidden">
        {!imgError ? (
          <img
            src={destMeta.imageUrl}
            alt={trip.destination}
            onError={() => setImgError(true)}
            className="w-full h-full object-cover object-center group-hover:scale-105 transition-transform duration-500 ease-out"
            loading="lazy"
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className={`w-full h-full bg-gradient-to-tr ${destMeta.fallbackGradient}`} />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />

        <div className="absolute inset-0 p-4 flex flex-col justify-between text-white z-10">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono uppercase tracking-wider text-[#E7A07F] bg-black/40 backdrop-blur-xs px-2 py-0.5 rounded">
              Rev {trip.active_revision_number || 1}
            </span>
            <span className="text-[11px] font-medium text-white/90 bg-black/40 backdrop-blur-xs px-2 py-0.5 rounded capitalize">
              {trip.status}
            </span>
          </div>

          <div>
            <h4 className="text-2xl font-bold font-editorial text-white tracking-tight leading-none group-hover:text-[#E7A07F] transition-colors">
              {trip.destination}
            </h4>
            <p className="text-xs text-white/80 line-clamp-1 mt-1">
              {trip.title || `From ${trip.origin}`}
            </p>
          </div>
        </div>
      </div>

      {/* Metadata & Footer */}
      <div className="p-4 space-y-3 flex-1 flex flex-col justify-between">
        <div className="space-y-1 text-xs text-[var(--text-secondary)]">
          <div className="flex items-center gap-1.5">
            <Calendar className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
            <span>{formatDateRange(trip.start_date, trip.end_date)}</span>
          </div>
          <div className="flex items-center justify-between text-[11px] pt-1">
            <span className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-[var(--primary)] shrink-0" />
              <span>{trip.travelers_count || 1} travelers</span>
            </span>
            {trip.budget && (
              <span className="font-mono tabular-nums text-[var(--text-secondary)]">
                {formatCurrency(trip.budget, trip.currency)}
              </span>
            )}
          </div>

          {/* Canonical Wave 3B/3D Booking Badges */}
          {((trip.flights && trip.flights.length > 0) || (trip.hotels && trip.hotels.length > 0)) && (
            <div className="pt-2 space-y-1 border-t border-[var(--border-subtle)]" onClick={(e) => e.stopPropagation()}>
              {trip.flights?.filter((f: any) => f && (f.booking_status === 'booked' || f.booking_status === 'completed')).map((f: any) => {
                const isConfirmed = true;
                return (
                  <div key={f.id} className="flex items-center justify-between text-[11px] bg-[var(--surface-subtle)] px-2 py-1 rounded">
                    <span className="flex items-center gap-1 font-mono text-[var(--status-success)]">
                      <CheckCircle2 className="w-3 h-3" />
                      <span>Flight: {f.booking_reference || 'Confirmed'}</span>
                    </span>
                    {isConfirmed && f.id && (
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={(e) => handleDownloadTicket(e, f.id, f.booking_reference)}
                          disabled={downloadingId === f.id}
                          data-href={`/api/trips/${encodeURIComponent(f.id)}/ticket.pdf`}
                          className="text-[10px] text-[var(--primary)] hover:underline inline-flex items-center gap-0.5 cursor-pointer disabled:opacity-50"
                          title="Download flight ticket PDF (/ticket.pdf)"
                        >
                          <Download className="w-3 h-3" />
                          {downloadingId === f.id ? 'Downloading...' : 'Ticket'}
                        </button>
                        <button
                          type="button"
                          onClick={(e) => handleDownloadReceipt(e, f.id, f.booking_reference)}
                          disabled={downloadingId === f.id}
                          data-href={`/api/trips/${encodeURIComponent(f.id)}/receipt.pdf`}
                          className="text-[10px] text-[var(--text-secondary)] hover:underline inline-flex items-center gap-0.5 cursor-pointer disabled:opacity-50"
                          title="Download payment receipt PDF (/receipt.pdf)"
                        >
                          <Download className="w-3 h-3" />
                          Receipt
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
              {trip.hotels?.filter((h: any) => !h.booking_status || h.booking_status === 'booked').map((h: any) => (
                <div key={h.id} className="flex items-center justify-between text-[11px] bg-[var(--surface-subtle)] px-2 py-1 rounded">
                  <span className="flex items-center gap-1 font-mono text-[var(--status-success)]">
                    <CheckCircle2 className="w-3 h-3" />
                    <span>Hotel: {h.booking_reference || 'Booked'}</span>
                  </span>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={(e) => handleDownloadVoucher(e, h.id, h.booking_reference)}
                      disabled={downloadingId === h.id}
                      data-href={`/api/trips/${encodeURIComponent(h.id)}/hotel.pdf`}
                      className="text-[10px] text-[var(--primary)] hover:underline inline-flex items-center gap-0.5 cursor-pointer disabled:opacity-50"
                      title="Download hotel voucher PDF (/hotel.pdf)"
                    >
                      <Download className="w-3 h-3" />
                      {downloadingId === h.id ? 'Downloading...' : 'Voucher'}
                    </button>
                    <button
                      type="button"
                      onClick={(e) => handleDownloadReceipt(e, h.id, h.booking_reference)}
                      disabled={downloadingId === h.id}
                      data-href={`/api/trips/${encodeURIComponent(h.id)}/receipt.pdf`}
                      className="text-[10px] text-[var(--text-secondary)] hover:underline inline-flex items-center gap-0.5 cursor-pointer disabled:opacity-50"
                      title="Download payment receipt PDF (/receipt.pdf)"
                    >
                      <Download className="w-3 h-3" />
                      Receipt
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between text-xs font-semibold text-[var(--primary)]">
          <span className="group-hover:translate-x-0.5 transition-transform inline-flex items-center gap-1">
            Open trip
            <ArrowRight className="w-3.5 h-3.5" />
          </span>

          <div className="flex items-center gap-2">
            {onContinuePlanning && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onContinuePlanning(trip.trip_id);
                }}
                className="text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors px-2 py-1 rounded hover:bg-black/5 dark:hover:bg-white/5 cursor-pointer"
              >
                Plan Itinerary
              </button>
            )}

            <button
              type="button"
              disabled={isDeleting}
              onClick={handleDeleteClick}
              title={isDeleting ? 'Deleting trip...' : `Delete ${trip.title || trip.destination}`}
              aria-label={`Delete trip ${trip.title || trip.destination}`}
              className={`p-1.5 rounded-lg transition-colors cursor-pointer text-[var(--text-secondary)] hover:text-[var(--status-danger)] hover:bg-[var(--status-danger)]/10 ${
                isDeleting ? 'opacity-40 cursor-not-allowed' : ''
              }`}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
