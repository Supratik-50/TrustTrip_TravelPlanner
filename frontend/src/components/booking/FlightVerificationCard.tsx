import React, { useState, useEffect } from 'react';
import {
  Plane,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  ArrowRight,
  ExternalLink,
  ShieldCheck,
  TrendingUp,
  AlertCircle,
  FileCheck,
  Lock,
} from 'lucide-react';
import { FlightOffer } from '../../types/flight';
import {
  FlightVerificationState,
  FlightPriceChangeNotice,
  ExternalCheckoutHandoff,
  FlightConfirmationRecord,
} from '../../types/bookingFrontend';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface FlightVerificationCardProps {
  flight?: FlightOffer;
  origin?: string;
  destination?: string;
  travelersCount?: number;
  verificationState: FlightVerificationState;
  priceChange?: FlightPriceChangeNotice;
  currentAmount?: number;
  currency?: string;
  confirmation?: FlightConfirmationRecord;
  isApproved?: boolean;
  bookingId?: string;
  bookingReference?: string;
  orderId?: string;
  pnr?: string;
  eTicket?: string;
  errorMessage?: string;
  finalConfirmationChecked?: boolean;
  onFinalConfirmationChange?: (checked: boolean) => void;
  onVerify: () => Promise<void>;
  onAcceptPriceChange: () => Promise<void>;
  onApproveFlight?: () => Promise<void>;
  onConfirmBooking?: () => Promise<void>;
  onSelectOtherFlight: () => void;
  onStartCheckoutHandoff?: () => void;
  onSimulatePriceChange?: () => void;
  onSimulateUnavailable?: () => void;
}

export const FlightVerificationCard: React.FC<FlightVerificationCardProps> = ({
  flight,
  origin = 'Origin Airport',
  destination = 'Destination Airport',
  travelersCount = 1,
  verificationState,
  priceChange,
  currentAmount,
  currency = 'USD',
  confirmation,
  isApproved = false,
  bookingId,
  bookingReference,
  orderId,
  pnr,
  eTicket,
  errorMessage,
  finalConfirmationChecked,
  onFinalConfirmationChange,
  onVerify,
  onAcceptPriceChange,
  onApproveFlight,
  onConfirmBooking,
  onSelectOtherFlight,
  onStartCheckoutHandoff,
  onSimulatePriceChange,
  onSimulateUnavailable,
}) => {
  const [isAccepting, setIsAccepting] = useState(false);
  const [isApproving, setIsApproving] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [hasConfirmedCheckbox, setHasConfirmedCheckbox] = useState(false);

  const displayPrice = currentAmount || (flight ? Number(flight.total_amount) : 860);
  const displayCurrency = priceChange?.currency || currency || flight?.currency || 'USD';
  const routeOrigin = flight?.slices?.[0]?.origin || origin;
  const routeDestination = flight?.slices?.[0]?.destination || destination;
  const carrierName = flight?.carrier_name || confirmation?.carrier_name || 'Carrier Partner';

  // Synchronize with controlled prop if provided
  useEffect(() => {
    if (finalConfirmationChecked !== undefined) {
      setHasConfirmedCheckbox(finalConfirmationChecked);
    }
  }, [finalConfirmationChecked]);

  // ISSUE 2 FIX: Reset checkbox whenever booking leaves approved ready state
  // (e.g. price_changed, price acceptance, fresh revalidation, approval reset, unavailable, stale)
  useEffect(() => {
    if (verificationState !== 'ready' || !isApproved) {
      setHasConfirmedCheckbox(false);
      onFinalConfirmationChange?.(false);
    }
  }, [verificationState, isApproved, onFinalConfirmationChange]);

  // Reset checkbox whenever fare amount or price change notice changes
  useEffect(() => {
    setHasConfirmedCheckbox(false);
    onFinalConfirmationChange?.(false);
  }, [displayPrice, priceChange, onFinalConfirmationChange]);

  const isCheckboxChecked = finalConfirmationChecked !== undefined ? finalConfirmationChecked : hasConfirmedCheckbox;

  const handleCheckboxToggle = (checked: boolean) => {
    setHasConfirmedCheckbox(checked);
    onFinalConfirmationChange?.(checked);
  };

  // =========================================================================
  // 1. COMPLETED / CONFIRMED STATE
  // =========================================================================
  if (verificationState === 'confirmed' || verificationState === 'completed') {
    // ISSUE 1 FIX: Truthful PNR and e-ticket display without fabricated random numbers
    const finalRef = bookingReference || confirmation?.booking_reference || 'Not provided';
    const finalPnr = pnr;
    const finalTicket = eTicket || (!pnr ? confirmation?.ticket_number : undefined);

    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-success)]/40 shadow-xs space-y-5">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-5 h-5 text-[var(--status-success)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-success)] font-bold">
              FLIGHT COMPLETED & TICKETED
            </span>
          </div>
          <span className="text-xs font-mono text-[var(--text-muted)]">
            Reference: <strong className="text-[var(--text-primary)]">{finalRef}</strong>
          </span>
        </div>

        <div className="space-y-1">
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
            {routeOrigin} → {routeDestination}
          </h3>
          <p className="text-xs text-[var(--text-secondary)] font-light">
            {carrierName} · {travelersCount} Traveler{travelersCount > 1 ? 's' : ''} · Cabin: {flight?.cabin_class || 'Economy'}
          </p>
        </div>

        <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/70 border border-[var(--border)] text-xs grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div>
            <span className="text-[var(--text-muted)] block text-[11px]">Booking Reference</span>
            <span className="font-mono text-[var(--text-primary)] font-bold">{finalRef}</span>
          </div>
          {finalPnr && (
            <div>
              <span className="text-[var(--text-muted)] block text-[11px]">Airline PNR</span>
              <span className="font-mono text-[var(--text-primary)] font-medium">{finalPnr}</span>
            </div>
          )}
          {finalTicket && (
            <div>
              <span className="text-[var(--text-muted)] block text-[11px]">E-Ticket</span>
              <span className="font-mono text-[var(--text-primary)] font-medium">{finalTicket}</span>
            </div>
          )}
          {!finalPnr && !finalTicket && (
            <div>
              <span className="text-[var(--text-muted)] block text-[11px]">PNR / E-Ticket</span>
              <span className="font-mono text-[var(--text-muted)] italic text-[11px]">Not provided by provider</span>
            </div>
          )}
          {orderId && (
            <div>
              <span className="text-[var(--text-muted)] block text-[11px]">Order Confirmation</span>
              <span className="font-mono text-[var(--text-primary)]">{orderId}</span>
            </div>
          )}
          <div>
            <span className="text-[var(--text-muted)] block text-[11px]">Confirmed Fare</span>
            <span className="font-mono text-[var(--status-success)] font-bold">
              {formatCurrency(displayPrice, displayCurrency)}
            </span>
          </div>
        </div>

        <div className="text-[11px] text-[var(--text-muted)] font-mono flex items-center gap-1.5 pt-1">
          <ShieldCheck className="w-3.5 h-3.5 text-[var(--status-success)]" />
          Authoritative booking status synchronized. Duffel provider ID shielded for security.
        </div>
      </div>
    );
  }

  // =========================================================================
  // 2. BOOKING IN PROGRESS STATE
  // =========================================================================
  if (verificationState === 'booking_in_progress') {
    return (
      <div className="p-8 sm:p-12 rounded-3xl bg-[var(--surface)] border border-[var(--primary)]/40 shadow-xs text-center space-y-4">
        <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
          <Plane className="w-6 h-6" />
        </div>
        <div className="space-y-1">
          <span className="px-3 py-1 rounded-full text-[10px] font-mono font-bold uppercase tracking-wider bg-[var(--primary-soft)] text-[var(--primary)]">
            Booking in Progress
          </span>
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)] pt-2">
            Confirming flight with carrier
          </h3>
          <p className="text-xs text-[var(--text-secondary)] font-light max-w-md mx-auto">
            Submitting your booking confirmation and generating electronic ticket numbers. Please do not close this window...
          </p>
        </div>
        <div className="text-[11px] font-mono text-[var(--text-muted)] pt-2">
          Route: {routeOrigin} → {routeDestination} · {formatCurrency(displayPrice, displayCurrency)}
        </div>
      </div>
    );
  }

  // =========================================================================
  // 3. AWAITING APPROVAL (HITL GATE)
  // =========================================================================
  if (verificationState === 'awaiting_approval' || (verificationState === 'ready' && !isApproved)) {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--primary)]/50 shadow-xs space-y-6">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[var(--primary)]" />
              <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
                AWAITING HUMAN APPROVAL (HITL)
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {routeOrigin} → {routeDestination}
            </h3>
          </div>

          <div className="text-right">
            <span className="text-xs text-[var(--text-muted)] block font-mono">Verified Fare</span>
            <span className="text-xl font-bold font-editorial text-[var(--primary)]">
              {formatCurrency(displayPrice, displayCurrency)}
            </span>
          </div>
        </div>

        <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/70 border border-[var(--border)] text-xs space-y-2">
          <div className="flex items-center gap-2 text-[var(--primary)] font-medium font-mono">
            <CheckCircle2 className="w-4 h-4 text-[var(--status-success)]" />
            <span>Seats & carrier inventory revalidated successfully</span>
          </div>
          <p className="text-[var(--text-secondary)] leading-relaxed">
            In compliance with TrustTrip safety guardrails, <strong>no automatic booking is ever initiated</strong>.
            A human must explicitly approve this journey before final ticketing can be unlocked.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <Button
            variant="outline"
            size="md"
            onClick={onSelectOtherFlight}
            className="w-full sm:w-auto"
          >
            Choose another flight
          </Button>

          <Button
            variant="primary"
            size="lg"
            onClick={async () => {
              if (!onApproveFlight) return;
              setIsApproving(true);
              try {
                await onApproveFlight();
              } finally {
                setIsApproving(false);
              }
            }}
            isLoading={isApproving}
            rightIcon={<ArrowRight className="w-4 h-4" />}
            className="w-full sm:w-auto shadow-xs"
          >
            Approve Flight Booking
          </Button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // 4. APPROVED / READY FOR FINAL CONFIRMATION
  // =========================================================================
  if (verificationState === 'ready' && isApproved) {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-success)]/40 shadow-xs space-y-6">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-[var(--status-success)]" />
              <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-success)] font-bold">
                HUMAN APPROVAL GRANTED · READY TO CONFIRM
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {routeOrigin} → {routeDestination}
            </h3>
          </div>

          <div className="text-right">
            <span className="text-xs text-[var(--text-muted)] block font-mono">Approved Fare</span>
            <span className="text-xl font-bold font-editorial text-[var(--text-primary)]">
              {formatCurrency(displayPrice, displayCurrency)}
            </span>
          </div>
        </div>

        {/* Final Confirmation Checkbox Gate */}
        <label className="flex items-start gap-3 p-4 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] cursor-pointer text-xs select-none">
          <input
            type="checkbox"
            checked={isCheckboxChecked}
            onChange={(e) => handleCheckboxToggle(e.target.checked)}
            className="mt-0.5 rounded text-[var(--primary)] focus:ring-[var(--primary)]"
          />
          <span className="text-[var(--text-secondary)] leading-relaxed">
            I confirm the current fare of <strong>{formatCurrency(displayPrice, displayCurrency)}</strong>, travel dates ({flight?.slices?.[0]?.departure_time ? new Date(flight.slices[0].departure_time).toLocaleDateString() : 'selected dates'}),
            flight route ({routeOrigin} → {routeDestination}), passenger details, and terms for final booking with {carrierName}.
          </span>
        </label>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <span className="text-xs text-[var(--text-muted)] font-mono flex items-center gap-1">
            <Lock className="w-3.5 h-3.5 text-[var(--primary)]" />
            Final human confirmation required for current fare
          </span>

          <Button
            variant="primary"
            size="lg"
            disabled={!isCheckboxChecked || !isApproved || verificationState !== 'ready'}
            onClick={async () => {
              if (!onConfirmBooking) return;
              setIsConfirming(true);
              try {
                await onConfirmBooking();
              } finally {
                setIsConfirming(false);
              }
            }}
            isLoading={isConfirming}
            rightIcon={<ArrowRight className="w-4 h-4" />}
            className="w-full sm:w-auto shadow-xs"
          >
            Confirm & Complete Flight Booking
          </Button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // 5. PRICE CHANGED STATE (HTTP 409)
  // =========================================================================
  if (verificationState === 'price_changed' && priceChange) {
    const diff = priceChange.current_amount - priceChange.previous_amount;

    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-warning)]/40 shadow-xs space-y-6">
        <div className="space-y-2 border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-[var(--status-warning)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-warning)] font-bold">
              PRICE CHANGED · REVALIDATION NOTICE
            </span>
          </div>
          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            Carrier fare updated during revalidation
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light">
            Airline inventory bucket adjusted. In accordance with safety rules, any previous approval is invalidated.
            Please review the fare adjustment before re-approving.
          </p>
        </div>

        {/* Price Difference Table */}
        <div className="grid grid-cols-3 gap-4 p-4 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] text-xs sm:text-sm">
          <div>
            <span className="text-[var(--text-muted)] block text-xs">Original Quote</span>
            <span className="text-lg sm:text-xl font-bold font-editorial text-[var(--text-secondary)] line-through">
              {formatCurrency(priceChange.previous_amount, priceChange.currency)}
            </span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block text-xs">Current Live Fare</span>
            <span className="text-xl sm:text-2xl font-bold font-editorial text-[var(--status-warning)]">
              {formatCurrency(priceChange.current_amount, priceChange.currency)}
            </span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block text-xs">Difference</span>
            <span className={`text-base font-bold font-mono ${diff > 0 ? 'text-[var(--status-warning)]' : 'text-[var(--status-success)]'}`}>
              {diff > 0 ? `+${formatCurrency(diff, priceChange.currency)}` : formatCurrency(diff, priceChange.currency)}
            </span>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <Button
            variant="outline"
            size="md"
            onClick={onSelectOtherFlight}
            className="w-full sm:w-auto"
          >
            Choose another flight
          </Button>

          <Button
            variant="primary"
            size="md"
            onClick={async () => {
              setIsAccepting(true);
              try {
                await onAcceptPriceChange();
              } finally {
                setIsAccepting(false);
              }
            }}
            isLoading={isAccepting}
            rightIcon={<ArrowRight className="w-4 h-4" />}
            className="w-full sm:w-auto"
          >
            Accept New Price ({formatCurrency(priceChange.current_amount, priceChange.currency)})
          </Button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // 6. STALE OFFER STATE (HTTP 410 OFFER STALE)
  // =========================================================================
  if (verificationState === 'stale') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-warning)]/40 shadow-xs space-y-6">
        <div className="space-y-2 border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-[var(--status-warning)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-warning)] font-bold">
              OFFER STALE / EXPIRED
            </span>
          </div>
          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            Flight snapshot has expired
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
            The supplier's holding window for this offer has lapsed. To guarantee accurate seat availability
            and fare classes, please perform a fresh search.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <span className="text-xs font-mono text-[var(--text-muted)]">
            Stale snapshot protected: No charges incurred.
          </span>

          <Button
            variant="primary"
            size="md"
            onClick={onSelectOtherFlight}
            rightIcon={<ArrowRight className="w-4 h-4" />}
            className="w-full sm:w-auto"
          >
            Search fresh departures
          </Button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // 7. UNAVAILABLE STATE (HTTP 410 OFFER UNAVAILABLE)
  // =========================================================================
  if (verificationState === 'unavailable') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-danger)]/40 shadow-xs space-y-6">
        <div className="space-y-2 border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-[var(--status-danger)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-danger)] font-bold">
              FLIGHT NO LONGER AVAILABLE
            </span>
          </div>
          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            Carrier inventory closed
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
            The airline has closed booking inventory or seats in this fare bucket have sold out.
            Please pick an alternative flight option for your journey.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <span className="text-xs font-mono text-[var(--text-muted)]">
            No booking charges have been incurred.
          </span>

          <Button
            variant="primary"
            size="md"
            onClick={onSelectOtherFlight}
            rightIcon={<ArrowRight className="w-4 h-4" />}
            className="w-full sm:w-auto"
          >
            Search other flights
          </Button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // 8. FAILED STATE (HTTP 502 / 504 / ERROR)
  // =========================================================================
  if (verificationState === 'failed') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-danger)]/40 shadow-xs space-y-6">
        <div className="space-y-2 border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[var(--status-danger)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-danger)] font-bold">
              FLIGHT VERIFICATION FAILED
            </span>
          </div>
          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            Unable to communicate with airline provider
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
            {errorMessage || 'The upstream carrier partner reservation gateway returned an error or timed out. Please try verifying again.'}
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <Button
            variant="outline"
            size="md"
            onClick={onSelectOtherFlight}
            className="w-full sm:w-auto"
          >
            Choose another flight
          </Button>

          <Button
            variant="primary"
            size="md"
            onClick={onVerify}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            className="w-full sm:w-auto"
          >
            Retry Verification
          </Button>
        </div>
      </div>
    );
  }

  // =========================================================================
  // 9. VERIFYING (LOADING) STATE
  // =========================================================================
  if (verificationState === 'verifying') {
    return (
      <div className="p-8 sm:p-12 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs text-center space-y-4">
        <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
          <Plane className="w-6 h-6" />
        </div>
        <div className="space-y-1">
          <h3 className="text-xl font-bold font-editorial text-[var(--text-primary)]">
            Revalidating flight with supplier
          </h3>
          <p className="text-xs text-[var(--text-secondary)] font-light">
            Checking real-time seat availability and fare rules with the airline reservation system...
          </p>
        </div>
        <div className="text-[11px] font-mono text-[var(--text-muted)] pt-2">
          {routeOrigin} → {routeDestination} · {travelersCount} traveler{travelersCount > 1 ? 's' : ''}
        </div>
      </div>
    );
  }

  // =========================================================================
  // 10. EMPTY STATE
  // =========================================================================
  if (verificationState === 'empty') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-dashed border-[var(--border)] text-center space-y-4">
        <div className="w-12 h-12 rounded-full bg-[var(--surface-muted)] text-[var(--text-muted)] mx-auto flex items-center justify-center">
          <Plane className="w-6 h-6" />
        </div>
        <div className="space-y-1">
          <h3 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
            No flight offer selected
          </h3>
          <p className="text-xs text-[var(--text-secondary)]">
            Select a flight option to initiate live airline verification and booking.
          </p>
        </div>
        <Button variant="primary" size="md" onClick={onSelectOtherFlight}>
          Browse flight options
        </Button>
      </div>
    );
  }

  // =========================================================================
  // 11. IDLE / AVAILABLE STATE: Offer selected, ready for revalidation
  // =========================================================================
  return (
    <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs space-y-6">
      <div className="space-y-2 border-b border-[var(--border)] pb-4">
        <div className="flex items-center gap-2">
          <Plane className="w-4 h-4 text-[var(--primary)]" />
          <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
            FLIGHT OFFER SELECTED · AVAILABLE FOR REVALIDATION
          </span>
        </div>
        <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
          {routeOrigin} → {routeDestination}
        </h3>
        <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
          Verify real-time seat inventory and ticket fare rules with {carrierName} before human approval and booking.
        </p>
      </div>

      <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/60 border border-[var(--border)] flex items-center justify-between text-xs">
        <div>
          <span className="text-[var(--text-muted)] block">Carrier & Cabin</span>
          <span className="font-semibold text-[var(--text-primary)]">
            {carrierName} · {flight?.cabin_class || 'Economy'}
          </span>
        </div>
        <div>
          <span className="text-[var(--text-muted)] block">Initial Quote</span>
          <span className="font-mono font-bold text-[var(--text-primary)]">
            {formatCurrency(displayPrice, displayCurrency)}
          </span>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
        <Button
          variant="outline"
          size="md"
          onClick={onSelectOtherFlight}
          className="w-full sm:w-auto"
        >
          Change flight
        </Button>

        <Button
          variant="primary"
          size="md"
          onClick={onVerify}
          leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          className="w-full sm:w-auto shadow-xs"
        >
          Check live availability & fare
        </Button>
      </div>

      {/* Developer testing simulation hooks */}
      <div className="pt-3 border-t border-[var(--border)]/70 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-[10px] font-mono text-[var(--text-muted)] uppercase tracking-wider">
          Demo Test Simulation:
        </span>
        <div className="flex items-center gap-3">
          {onSimulatePriceChange && (
            <button
              type="button"
              onClick={onSimulatePriceChange}
              className="text-[11px] text-[var(--status-warning)] hover:underline font-mono cursor-pointer"
            >
              Test Price Change (409)
            </button>
          )}
          {onSimulateUnavailable && (
            <button
              type="button"
              onClick={onSimulateUnavailable}
              className="text-[11px] text-[var(--status-danger)] hover:underline font-mono cursor-pointer"
            >
              Test Unavailable (410)
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
