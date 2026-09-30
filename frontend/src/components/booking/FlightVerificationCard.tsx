import React, { useState } from 'react';
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
} from 'lucide-react';
import { FlightOffer } from '../../types/flight';
import {
  FlightVerificationState,
  FlightPriceChangeNotice,
  ExternalCheckoutHandoff,
  FlightConfirmationRecord,
} from '../../types/bookingFrontend';
import { Button } from '../common/Button';

interface FlightVerificationCardProps {
  flight?: FlightOffer;
  origin?: string;
  destination?: string;
  travelersCount?: number;
  verificationState: FlightVerificationState;
  priceChange?: FlightPriceChangeNotice;
  currentAmount?: number;
  confirmation?: FlightConfirmationRecord;
  onVerify: () => Promise<void>;
  onAcceptPriceChange: () => Promise<void>;
  onSelectOtherFlight: () => void;
  onStartCheckoutHandoff: () => void;
  onSimulatePriceChange?: () => void;
  onSimulateUnavailable?: () => void;
}

export const FlightVerificationCard: React.FC<FlightVerificationCardProps> = ({
  flight,
  origin = 'Kolkata (CCU)',
  destination = 'Paris (CDG)',
  travelersCount = 2,
  verificationState,
  priceChange,
  currentAmount,
  confirmation,
  onVerify,
  onAcceptPriceChange,
  onSelectOtherFlight,
  onStartCheckoutHandoff,
  onSimulatePriceChange,
  onSimulateUnavailable,
}) => {
  const [isAccepting, setIsAccepting] = useState(false);

  const displayPrice = currentAmount || (flight ? Number(flight.total_amount) : 860);

  // 1. CONFIRMED STATE
  if (verificationState === 'confirmed' && confirmation) {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-success)]/40 shadow-xs space-y-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-5 h-5 text-[var(--status-success)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-success)] font-bold">
              FLIGHT CONFIRMED & TICKETED
            </span>
          </div>
          <span className="text-xs font-mono text-[var(--text-muted)]">
            Reference: {confirmation.booking_reference}
          </span>
        </div>

        <div className="space-y-1">
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
            {confirmation.origin_city} → {confirmation.destination_city}
          </h3>
          <p className="text-xs text-[var(--text-secondary)] font-light">
            {confirmation.carrier_name} · Flight {confirmation.flight_number} · {confirmation.cabin_class} · {confirmation.travelers_count} Travelers
          </p>
        </div>

        <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/60 border border-[var(--border)] text-xs flex flex-wrap items-center justify-between gap-3">
          <div>
            <span className="text-[var(--text-muted)] block">Electronic Ticket</span>
            <span className="font-mono text-[var(--text-primary)] font-medium">
              {confirmation.ticket_number}
            </span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block">Confirmed At</span>
            <span className="font-mono text-[var(--text-primary)]">
              {new Date(confirmation.confirmed_at).toLocaleDateString([], {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block">Status</span>
            <span className="text-[var(--status-success)] font-semibold font-mono">Guaranteed</span>
          </div>
        </div>
      </div>
    );
  }

  // 2. VERIFYING (LOADING) STATE
  if (verificationState === 'verifying') {
    return (
      <div className="p-8 sm:p-12 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs text-center space-y-4">
        <div className="w-12 h-12 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
          <Plane className="w-6 h-6" />
        </div>
        <div className="space-y-1">
          <h3 className="text-xl font-bold font-editorial text-[var(--text-primary)]">
            Checking your flight
          </h3>
          <p className="text-xs text-[var(--text-secondary)] font-light">
            Checking current seat availability and fare classes with airline reservation systems...
          </p>
        </div>
        <div className="text-[11px] font-mono text-[var(--text-muted)] pt-2">
          {flight?.slices?.[0]?.origin || origin} → {flight?.slices?.[0]?.destination || destination} · {travelersCount} travelers
        </div>
      </div>
    );
  }

  // 3. PRICE CHANGED STATE
  if (verificationState === 'price_changed' && priceChange) {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-warning)]/40 shadow-xs space-y-6">
        <div className="space-y-2 border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-[var(--status-warning)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-warning)] font-bold">
              YOUR FLIGHT PRICE CHANGED
            </span>
          </div>
          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            The fare available earlier is no longer available at the same price.
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light">
            Airlines dynamically adjust bucket inventory. The carrier has updated this route's live fare.
          </p>
        </div>

        {/* Price Difference Table */}
        <div className="grid grid-cols-2 gap-4 p-4 rounded-2xl bg-[var(--surface-muted)] border border-[var(--border)] text-xs sm:text-sm">
          <div>
            <span className="text-[var(--text-muted)] block text-xs">Previous Fare</span>
            <span className="text-lg sm:text-xl font-bold font-editorial text-[var(--text-secondary)] line-through">
              ${priceChange.previous_amount.toLocaleString()}
            </span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block text-xs">Current Live Fare</span>
            <span className="text-xl sm:text-2xl font-bold font-editorial text-[var(--status-warning)]">
              ${priceChange.current_amount.toLocaleString()}{' '}
              <span className="text-xs font-normal font-mono text-[var(--text-muted)]">
                {priceChange.currency}
              </span>
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
            Accept new price (${priceChange.current_amount})
          </Button>
        </div>
      </div>
    );
  }

  // 4. UNAVAILABLE STATE
  if (verificationState === 'unavailable') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-danger)]/40 shadow-xs space-y-6">
        <div className="space-y-2 border-b border-[var(--border)] pb-4">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-[var(--status-danger)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-danger)] font-bold">
              THIS FLIGHT IS NO LONGER AVAILABLE
            </span>
          </div>
          <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
            The selected flight can no longer be booked at the moment.
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
            The airline has closed inventory or reached seat capacity for this cabin tier. Please pick
            an alternative scheduled departure for your travel dates.
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

  // 5. READY STATE (Verified and ready for handoff)
  if (verificationState === 'ready' || verificationState === 'handed_off') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--primary)]/40 shadow-xs space-y-6">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-[var(--primary)]" />
              <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
                READY FOR SECURE CHECKOUT
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {flight?.slices?.[0]?.origin || origin} → {flight?.slices?.[0]?.destination || destination}
            </h3>
          </div>

          <div className="text-right">
            <span className="text-xs text-[var(--text-muted)] block font-mono">Verified Fare</span>
            <span className="text-xl font-bold font-editorial text-[var(--primary)]">
              ${displayPrice.toLocaleString()}
            </span>
          </div>
        </div>

        <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
          You'll complete your flight booking on our secure airline partner checkout. Your ticket and
          booking reference will be synchronized back to your TrustTrip journey automatically.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <span className="text-xs text-[var(--text-muted)] font-mono flex items-center gap-1">
            <Clock className="w-3.5 h-3.5 text-[var(--primary)]" />
            Fare hold locked for 20 minutes
          </span>

          <Button
            variant="primary"
            size="lg"
            onClick={onStartCheckoutHandoff}
            rightIcon={<ExternalLink className="w-4 h-4" />}
            className="w-full sm:w-auto shadow-xs"
          >
            Continue to secure checkout
          </Button>
        </div>

        {/* Developer / Testing Simulation Controls */}
        <div className="pt-3 border-t border-[var(--border)]/70 flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="text-[10px] font-mono text-[var(--text-muted)] uppercase tracking-wider">
            Prototype Simulation Triggers:
          </span>
          <div className="flex items-center gap-2">
            {onSimulatePriceChange && (
              <button
                type="button"
                onClick={onSimulatePriceChange}
                className="text-[11px] text-[var(--status-warning)] hover:underline font-mono cursor-pointer"
              >
                Test Price Change
              </button>
            )}
            {onSimulateUnavailable && (
              <button
                type="button"
                onClick={onSimulateUnavailable}
                className="text-[11px] text-[var(--status-danger)] hover:underline font-mono cursor-pointer"
              >
                Test Unavailable
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // 6. IDLE STATE: Initial prompt to verify flight
  return (
    <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs space-y-6">
      <div className="space-y-2 border-b border-[var(--border)] pb-4">
        <div className="flex items-center gap-2">
          <Plane className="w-4 h-4 text-[var(--primary)]" />
          <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
            AIRLINE INVENTORY VERIFICATION
          </span>
        </div>
        <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
          Checking your flight
        </h3>
        <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
          Verify live seat inventory and fare rules with the carrier partner before proceeding to checkout.
        </p>
      </div>

      <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/60 border border-[var(--border)] flex items-center justify-between text-xs">
        <div>
          <span className="text-[var(--text-muted)] block">Selected Route</span>
          <span className="font-semibold text-[var(--text-primary)]">
            {flight?.slices?.[0]?.origin || origin} → {flight?.slices?.[0]?.destination || destination}
          </span>
        </div>
        <div>
          <span className="text-[var(--text-muted)] block">Current Quote</span>
          <span className="font-mono font-bold text-[var(--text-primary)]">
            ${displayPrice.toLocaleString()}
          </span>
        </div>
      </div>

      <div className="flex justify-end">
        <Button
          variant="primary"
          size="md"
          onClick={onVerify}
          leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          className="shadow-xs"
        >
          Check current availability
        </Button>
      </div>
    </div>
  );
};
