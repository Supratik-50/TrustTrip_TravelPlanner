import React from 'react';
import { Check, Plane, Building2, ShieldCheck } from 'lucide-react';
import { FlightVerificationState, HotelBookingState } from '../../types/bookingFrontend';

interface BookingProgressBarProps {
  isPlanApproved: boolean;
  flightState: FlightVerificationState;
  hotelState: HotelBookingState | 'idle' | 'preparing' | 'payment_required' | 'confirmed';
  className?: string;
}

export const BookingProgressBar: React.FC<BookingProgressBarProps> = ({
  isPlanApproved,
  flightState,
  hotelState,
  className = '',
}) => {
  const isFlightConfirmed = flightState === 'confirmed';
  const isHotelConfirmed = hotelState === 'confirmed';
  const isFullyConfirmed = isFlightConfirmed && isHotelConfirmed;

  const steps = [
    {
      id: 'approval',
      label: 'Journey Approved',
      subtitle: isPlanApproved ? 'Plan finalized' : 'Review & confirm',
      isComplete: isPlanApproved,
      isActive: !isPlanApproved,
    },
    {
      id: 'flight',
      label: 'Flight Verification',
      subtitle: isFlightConfirmed
        ? 'Verified & confirmed'
        : flightState === 'handed_off'
        ? 'Awaiting checkout'
        : flightState === 'verifying'
        ? 'Verifying with airline...'
        : flightState === 'price_changed'
        ? 'Price update notice'
        : 'Fare lock & seat',
      isComplete: isFlightConfirmed,
      isActive: isPlanApproved && !isFlightConfirmed,
    },
    {
      id: 'hotel',
      label: 'Stay Reservation',
      subtitle: isHotelConfirmed
        ? 'Reserved & guaranteed'
        : hotelState === 'payment_required' || hotelState === 'payment_processing'
        ? 'Payment confirmation'
        : 'Boutique stay check',
      isComplete: isHotelConfirmed,
      isActive: isFlightConfirmed && !isHotelConfirmed,
    },
    {
      id: 'confirmed',
      label: 'Journey Confirmed',
      subtitle: isFullyConfirmed ? 'Travel documents ready' : 'Final voucher handoff',
      isComplete: isFullyConfirmed,
      isActive: isFullyConfirmed,
    },
  ];

  return (
    <div className={`p-4 sm:p-5 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs ${className}`}>
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--primary)] font-mono">
            Booking Progress
          </span>
          <span className="text-xs text-[var(--text-muted)] font-mono hidden sm:inline">
            · Travel Verification Pipeline
          </span>
        </div>

        <div className="text-xs font-mono text-[var(--text-secondary)]">
          {isFullyConfirmed ? (
            <span className="text-[var(--status-success)] font-semibold flex items-center gap-1">
              <Check className="w-3.5 h-3.5" /> All Services Confirmed
            </span>
          ) : isFlightConfirmed ? (
            <span className="text-[var(--primary)]">Flight Confirmed · Stay Pending</span>
          ) : isPlanApproved ? (
            <span className="text-[var(--primary)]">Plan Approved · Ready for Booking</span>
          ) : (
            <span>Step 1 of 4</span>
          )}
        </div>
      </div>

      {/* Progress Track */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 pt-2">
        {steps.map((step, idx) => (
          <div
            key={step.id}
            className={`p-3 rounded-2xl border transition-all ${
              step.isComplete
                ? 'border-[var(--primary)]/40 bg-[var(--primary-soft)]/20'
                : step.isActive
                ? 'border-[var(--primary)] bg-[var(--surface-muted)] shadow-xs'
                : 'border-[var(--border)] bg-[var(--surface)] opacity-70'
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              <div
                className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-mono font-bold transition-colors ${
                  step.isComplete
                    ? 'bg-[var(--primary)] text-white'
                    : step.isActive
                    ? 'bg-[var(--text-primary)] text-[var(--background)]'
                    : 'bg-[var(--surface-muted)] text-[var(--text-muted)]'
                }`}
              >
                {step.isComplete ? <Check className="w-3 h-3" /> : idx + 1}
              </div>
              <span
                className={`text-xs font-semibold truncate ${
                  step.isComplete
                    ? 'text-[var(--primary)]'
                    : step.isActive
                    ? 'text-[var(--text-primary)]'
                    : 'text-[var(--text-muted)]'
                }`}
              >
                {step.label}
              </span>
            </div>
            <p className="text-[11px] text-[var(--text-secondary)] font-light truncate pl-7">
              {step.subtitle}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};
