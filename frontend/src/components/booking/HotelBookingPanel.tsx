import React, { useState } from 'react';
import {
  Building2,
  CheckCircle2,
  Calendar,
  Users,
  CreditCard,
  ArrowRight,
  ShieldCheck,
  Clock,
  MapPin,
  Sparkles,
} from 'lucide-react';
import { HotelOffer } from '../../types/hotel';
import { HotelBookingState, HotelConfirmationRecord } from '../../types/bookingFrontend';
import { Button } from '../common/Button';

interface HotelBookingPanelProps {
  hotel?: HotelOffer;
  destination?: string;
  startDate?: string;
  endDate?: string;
  travelersCount?: number;
  hotelState: HotelBookingState | 'idle' | 'preparing' | 'payment_required' | 'confirmed';
  confirmation?: HotelConfirmationRecord;
  onPrepareBooking: () => Promise<void>;
  onOpenPaymentModal: () => void;
  onSelectOtherHotel: () => void;
}

export const HotelBookingPanel: React.FC<HotelBookingPanelProps> = ({
  hotel,
  destination = '',
  startDate = '',
  endDate = '',
  travelersCount = 2,
  hotelState,
  confirmation,
  onPrepareBooking,
  onOpenPaymentModal,
  onSelectOtherHotel,
}) => {
  const [isPreparing, setIsPreparing] = useState(false);

  const roomPrice = hotel ? Number(hotel.total_amount) : 0;
  const taxes = Math.round(roomPrice * 0.1);
  const total = roomPrice + taxes;

  // 1. CONFIRMED STATE
  if (hotelState === 'confirmed' && confirmation) {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--status-success)]/40 shadow-xs space-y-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-5 h-5 text-[var(--status-success)]" />
            <span className="text-xs uppercase font-mono tracking-widest text-[var(--status-success)] font-bold">
              STAY RESERVED & CONFIRMED
            </span>
          </div>
          <span className="text-xs font-mono text-[var(--text-muted)]">
            Voucher Code: {confirmation.confirmation_code}
          </span>
        </div>

        <div className="space-y-1">
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
            {confirmation.property_name}
          </h3>
          <p className="text-xs text-[var(--text-secondary)] font-light flex items-center gap-1">
            <MapPin className="w-3.5 h-3.5 text-[var(--terracotta)]" />
            {confirmation.property_address}
          </p>
        </div>

        <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/60 border border-[var(--border)] text-xs grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <span className="text-[var(--text-muted)] block">Room Category</span>
            <span className="font-semibold text-[var(--text-primary)]">{confirmation.room_name || 'Standard'}</span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block">Dates & Nights</span>
            <span className="font-mono text-[var(--text-primary)]">
              {confirmation.check_in_date && confirmation.check_out_date
                ? `${confirmation.check_in_date} to ${confirmation.check_out_date} (${confirmation.nights_count != null ? `${confirmation.nights_count} nights` : '—'})`
                : 'Dates as confirmed on voucher'}
            </span>
          </div>
          <div>
            <span className="text-[var(--text-muted)] block">Status</span>
            <span className="text-[var(--status-success)] font-semibold font-mono">Guaranteed Stay</span>
          </div>
        </div>
      </div>
    );
  }

  // 2. PAYMENT REQUIRED STATE
  if (hotelState === 'payment_required') {
    return (
      <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--primary)]/40 shadow-xs space-y-6">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Building2 className="w-4 h-4 text-[var(--terracotta)]" />
              <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
                RESERVATION PREPARED
              </span>
            </div>
            <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
              {hotel?.name || 'Hotel Stay'}
            </h3>
          </div>

          <div className="text-right">
            <span className="text-xs text-[var(--text-muted)] block font-mono">Stay Commitment</span>
            <span className="text-xl font-bold font-editorial text-[var(--primary)]">
              ${total.toLocaleString()}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div className="space-y-1 text-[var(--text-secondary)]">
            <span className="text-[var(--text-muted)] block font-mono">Dates & Guests</span>
            <div className="font-semibold text-[var(--text-primary)]">
              {startDate} — {endDate} · {travelersCount} Guests
            </div>
            <div>{hotel?.rooms?.[0]?.name || 'Deluxe Heritage King with Garden View'}</div>
          </div>

          <div className="space-y-1 text-[var(--text-secondary)]">
            <span className="text-[var(--text-muted)] block font-mono">Cancellation Flexibility</span>
            <div className="text-[var(--status-success)] font-medium">
              {hotel?.cancellation_policy || 'Free cancellation until 48 hours before check-in'}
            </div>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2">
          <Button
            variant="outline"
            size="md"
            onClick={onSelectOtherHotel}
            className="w-full sm:w-auto"
          >
            Change stay
          </Button>

          <Button
            variant="primary"
            size="lg"
            onClick={onOpenPaymentModal}
            rightIcon={<CreditCard className="w-4 h-4" />}
            className="w-full sm:w-auto shadow-xs"
          >
            Continue to payment (${total.toLocaleString()})
          </Button>
        </div>
      </div>
    );
  }

  // 3. IDLE OR PREPARING STATE
  return (
    <div className="p-6 sm:p-8 rounded-3xl bg-[var(--surface)] border border-[var(--border)] shadow-xs space-y-6">
      <div className="space-y-2 border-b border-[var(--border)] pb-4">
        <div className="flex items-center gap-2">
          <Building2 className="w-4 h-4 text-[var(--terracotta)]" />
          <span className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
            BOUTIQUE STAY RESERVATION
          </span>
        </div>
        <h3 className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
          {hotel?.name || 'Curated Boutique Stay'}
        </h3>
        <p className="text-xs sm:text-sm text-[var(--text-secondary)] font-light leading-relaxed">
          Confirm room selection, verified check-in policies, and lock preferred property inventory.
        </p>
      </div>

      <div className="p-4 rounded-2xl bg-[var(--surface-muted)]/60 border border-[var(--border)] flex items-center justify-between text-xs">
        <div>
          <span className="text-[var(--text-muted)] block">Selected Property</span>
          <span className="font-semibold text-[var(--text-primary)]">
            {hotel?.name || 'Hotel Lutetia Left Bank'}
          </span>
        </div>
        <div>
          <span className="text-[var(--text-muted)] block">Estimated Total</span>
          <span className="font-mono font-bold text-[var(--text-primary)]">
            ${total.toLocaleString()}
          </span>
        </div>
      </div>

      <div className="flex justify-end">
        <Button
          variant="primary"
          size="md"
          onClick={async () => {
            setIsPreparing(true);
            try {
              await onPrepareBooking();
            } finally {
              setIsPreparing(false);
            }
          }}
          isLoading={isPreparing}
          rightIcon={<ArrowRight className="w-4 h-4" />}
          className="shadow-xs"
        >
          Prepare hotel reservation
        </Button>
      </div>
    </div>
  );
};
