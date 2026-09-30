import React, { useState, useEffect, useCallback } from 'react';
import {
  ShieldCheck,
  Plane,
  Building2,
  CheckCircle2,
  AlertTriangle,
  RotateCcw,
  Sparkles,
  ArrowRight,
  ArrowLeft,
  Calendar,
  Users,
  CreditCard,
} from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { FlightOffer } from '../../types/flight';
import { HotelOffer } from '../../types/hotel';
import {
  FlightVerificationState,
  HotelBookingState,
  FlightPriceChangeNotice,
  ExternalCheckoutHandoff,
  FlightConfirmationRecord,
  HotelConfirmationRecord,
  TravelerContactInfo,
  CancellationRequest,
} from '../../types/bookingFrontend';
import { bookingService } from '../../services/bookingService';
import {
  getStoredBookingState,
  saveStoredBookingState,
  clearStoredBookingState,
  DEFAULT_TRAVELER_CONTACT,
} from '../../mock/bookingMockData';
import { useNotification } from '../../context/NotificationContext';
import { BookingProgressBar } from './BookingProgressBar';
import { BookingReviewPanel } from './BookingReviewPanel';
import { ApprovalCard } from './ApprovalCard';
import { FlightVerificationCard } from './FlightVerificationCard';
import { CheckoutHandoffModal } from './CheckoutHandoffModal';
import { HotelBookingPanel } from './HotelBookingPanel';
import { PaymentReviewModal } from './PaymentReviewModal';
import { TripConfirmationView } from './TripConfirmationView';
import { Button } from '../common/Button';

interface BookingWorkspaceProps {
  trip: TripDetail;
  onNavigateTab: (tab: 'itinerary' | 'flights' | 'hotels' | 'research') => void;
  onBackToTrips: () => void;
  initialSubStep?: 'review' | 'flight' | 'hotel' | 'confirmation';
}

export const BookingWorkspace: React.FC<BookingWorkspaceProps> = ({
  trip,
  onNavigateTab,
  onBackToTrips,
  initialSubStep = 'review',
}) => {
  const { notify } = useNotification();

  // Booking persistent state
  const [isPlanApproved, setIsPlanApproved] = useState(false);
  const [approvedAt, setApprovedAt] = useState<string | undefined>(undefined);
  const [flightState, setFlightState] = useState<FlightVerificationState>('idle');
  const [flightConfirmation, setFlightConfirmation] = useState<FlightConfirmationRecord | undefined>(undefined);
  const [hotelState, setHotelState] = useState<HotelBookingState | 'idle' | 'preparing' | 'payment_required' | 'confirmed'>('idle');
  const [hotelConfirmation, setHotelConfirmation] = useState<HotelConfirmationRecord | undefined>(undefined);

  // Price change / verification details
  const [priceChange, setPriceChange] = useState<FlightPriceChangeNotice | undefined>(undefined);
  const [verifiedFlightAmount, setVerifiedFlightAmount] = useState<number>(860);
  const [approvalConflict, setApprovalConflict] = useState<string | null>(null);

  // Modals & Handoff
  const [isHandoffModalOpen, setIsHandoffModalOpen] = useState(false);
  const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
  const [contact, setContact] = useState<TravelerContactInfo>(DEFAULT_TRAVELER_CONTACT);

  // Active view section when approved: 'stages' vs 'confirmation'
  const [currentStage, setCurrentStage] = useState<'review' | 'flight_stage' | 'hotel_stage' | 'confirmed'>(
    initialSubStep === 'confirmation' ? 'confirmed' : 'review'
  );

  const selectedFlight = trip.selected_flight_offer as FlightOffer | undefined;
  const selectedHotel = trip.selected_hotel_offer as HotelOffer | undefined;

  // Load canonical state on mount & synchronize from backend trip
  const syncFromStorage = useCallback(() => {
    // 1. Check canonical trip backend fields
    if (trip.is_approved || trip.status === 'approved') {
      setIsPlanApproved(true);
      if (trip.current_revision?.approved_at) {
        setApprovedAt(trip.current_revision.approved_at);
      }
    }

    const saved = getStoredBookingState(trip.trip_id);
    if (saved.isApproved) {
      setIsPlanApproved(true);
      if (saved.approvedAt) setApprovedAt(saved.approvedAt);
    }
    setFlightState(saved.flightState);
    setFlightConfirmation(saved.flightConfirmation);
    setHotelState(saved.hotelState);
    setHotelConfirmation(saved.hotelConfirmation);

    if (saved.flightState === 'confirmed' && saved.hotelState === 'confirmed') {
      setCurrentStage('confirmed');
    } else if (saved.isApproved || trip.is_approved) {
      if (saved.flightState !== 'confirmed') {
        setCurrentStage('flight_stage');
      } else {
        setCurrentStage('hotel_stage');
      }
    }
  }, [trip]);

  useEffect(() => {
    syncFromStorage();
  }, [syncFromStorage]);

  // Handle Plan Approval with 409 conflict detection
  const handleApprovePlan = async () => {
    setApprovalConflict(null);
    try {
      const res = await bookingService.approvePlan(trip.trip_id, trip.active_revision_number);
      setIsPlanApproved(true);
      setApprovedAt(res.approved_at);
      notify('Journey plan approved! Ready for service verification.', 'success', 'Plan Approved');
    } catch (err: any) {
      if (err.statusCode === 409 || err.code === 'REVISION_CONFLICT') {
        setApprovalConflict('Your trip has changed since this review. Active server revision does not match your baseline plan.');
        notify('Trip changed since review. Please refresh.', 'error', 'Revision Conflict (409)');
      } else {
        notify(err.message || 'Unable to approve journey plan.', 'error');
      }
    }
  };

  // Handle Flight Verification
  const handleVerifyFlight = async () => {
    setFlightState('verifying');
    try {
      const res = await bookingService.verifyFlight(trip.trip_id, selectedFlight?.id || 'fl_off_101');
      setFlightState(res.state);
      setVerifiedFlightAmount(res.current_amount);
      if (res.price_change) {
        setPriceChange(res.price_change);
        notify('Carrier fare has updated. Please review price adjustment.', 'warning', 'Fare Updated');
      } else if (res.state === 'unavailable') {
        notify('This flight is no longer available.', 'error', 'Inventory Closed');
      } else {
        notify('Flight seats verified and fare hold locked for 20 minutes.', 'success', 'Flight Ready');
      }
    } catch {
      setFlightState('failed');
      notify('Unable to verify flight availability. Please retry.', 'error');
    }
  };

  // Accept Price Change
  const handleAcceptPriceChange = async () => {
    await bookingService.acceptPriceChange(trip.trip_id, selectedFlight?.id || 'fl_off_101');
    setPriceChange(undefined);
    setFlightState('ready');
    notify('Updated carrier fare accepted. Ready for secure checkout.', 'info', 'Price Accepted');
  };

  // External Checkout Simulation
  const handleCompleteCheckoutHandoff = async () => {
    const confirmRec = await bookingService.confirmFlightHandoffReturn(trip.trip_id, 'token_123');
    setFlightConfirmation(confirmRec);
    setFlightState('confirmed');
    notify(`Flight ticket issued! Reference: ${confirmRec.booking_reference}`, 'success', 'Flight Confirmed');

    // If hotel is already confirmed, advance to full confirmation
    if (hotelState === 'confirmed') {
      setCurrentStage('confirmed');
    } else {
      setCurrentStage('hotel_stage');
    }
  };

  // Prepare Hotel Booking
  const handlePrepareHotel = async () => {
    await bookingService.prepareHotelBooking(trip.trip_id, selectedHotel?.id || 'ht_off_101');
    setHotelState('payment_required');
    notify('Stay reservation prepared. Ready for payment confirmation.', 'info', 'Reservation Prepared');
  };

  // Process Hotel Payment
  const handleProcessHotelPayment = async (
    method: 'card' | 'upi' | 'netbanking' | 'pay_at_property',
    travelerContact: TravelerContactInfo
  ) => {
    const confirmRec = await bookingService.processHotelPayment(trip.trip_id, method, travelerContact);
    setHotelConfirmation(confirmRec);
    setHotelState('confirmed');
    notify(`Stay reserved! Voucher code: ${confirmRec.confirmation_code}`, 'success', 'Stay Confirmed');

    // Both confirmed
    if (flightState === 'confirmed') {
      setCurrentStage('confirmed');
    }
  };

  // Cancellation Handler
  const handleRequestCancellation = async (request: CancellationRequest) => {
    return await bookingService.requestCancellation(trip.trip_id, request);
  };

  // PDF Download Handler
  const handleDownloadPdf = async () => {
    return await bookingService.downloadTravelPlanPdf(trip.trip_id);
  };

  // Reset Booking Simulation
  const handleResetSimulation = () => {
    clearStoredBookingState(trip.trip_id);
    setIsPlanApproved(false);
    setApprovedAt(undefined);
    setFlightState('idle');
    setFlightConfirmation(undefined);
    setHotelState('idle');
    setHotelConfirmation(undefined);
    setPriceChange(undefined);
    setCurrentStage('review');
    notify('Booking flow reset to Step 1 for interactive testing.', 'info', 'Flow Reset');
  };

  // Trigger test states
  const handleTriggerPriceChange = () => {
    const s = getStoredBookingState(trip.trip_id);
    s.hasPriceChanged = true;
    s.isFlightUnavailable = false;
    saveStoredBookingState(trip.trip_id, s);
    handleVerifyFlight();
  };

  const handleTriggerUnavailable = () => {
    const s = getStoredBookingState(trip.trip_id);
    s.isFlightUnavailable = true;
    s.hasPriceChanged = false;
    saveStoredBookingState(trip.trip_id, s);
    handleVerifyFlight();
  };

  // Full Confirmed View
  if (currentStage === 'confirmed' || (flightState === 'confirmed' && hotelState === 'confirmed')) {
    return (
      <div className="space-y-6">
        {/* Reset / Testing Bar */}
        <div className="flex items-center justify-between p-3 rounded-2xl bg-[var(--surface)] border border-[var(--border)] text-xs">
          <span className="font-mono text-[var(--status-success)] flex items-center gap-1.5 font-bold">
            <CheckCircle2 className="w-4 h-4" />
            Confirmed Journey Milestone
          </span>
          <button
            type="button"
            onClick={handleResetSimulation}
            className="text-[var(--text-muted)] hover:text-[var(--text-primary)] flex items-center gap-1 font-mono transition-colors cursor-pointer"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset flow for demo
          </button>
        </div>

        <TripConfirmationView
          trip={trip}
          flightConfirmation={flightConfirmation}
          hotelConfirmation={hotelConfirmation}
          onDownloadPdf={handleDownloadPdf}
          onRequestCancellation={handleRequestCancellation}
          onViewTrip={() => onNavigateTab('itinerary')}
          onBackToTrips={onBackToTrips}
        />
      </div>
    );
  }

  return (
    <div className="space-y-8 pb-16">
      {/* Progress Milestone Bar */}
      <BookingProgressBar
        isPlanApproved={isPlanApproved}
        flightState={flightState}
        hotelState={hotelState}
      />

      {/* STAGE 1: REVIEW & APPROVAL */}
      {!isPlanApproved ? (
        <div className="space-y-8">
          <BookingReviewPanel
            trip={trip}
            flight={selectedFlight}
            hotel={selectedHotel}
            contact={contact}
            isApproved={isPlanApproved}
            onEditSection={(section) => onNavigateTab(section)}
          />

          <ApprovalCard
            isApproved={isPlanApproved}
            approvedAt={approvedAt}
            onApprove={handleApprovePlan}
            onContinueToVerification={() => setCurrentStage('flight_stage')}
            onBackToPlanning={() => onNavigateTab('itinerary')}
          />
        </div>
      ) : (
        /* STAGE 2: FLIGHT VERIFICATION & STAY PAYMENT */
        <div className="space-y-8">
          {/* Approval Banner */}
          <div className="p-4 rounded-2xl bg-[var(--surface)] border border-[var(--border)] flex flex-wrap items-center justify-between gap-3 shadow-xs">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-[var(--status-success)]" />
              <span className="text-xs font-mono font-bold uppercase tracking-wider text-[var(--primary)]">
                Plan Approved
              </span>
              <span className="text-xs text-[var(--text-muted)] font-mono">
                {approvedAt && new Date(approvedAt).toLocaleDateString([], { month: 'short', day: 'numeric' })}
              </span>
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={handleResetSimulation}
                className="text-xs font-mono text-[var(--text-muted)] hover:text-[var(--text-primary)] flex items-center gap-1 cursor-pointer"
              >
                <RotateCcw className="w-3 h-3" />
                Reset demo
              </button>
            </div>
          </div>

          {/* Service Verification Panels */}
          <div className="space-y-6">
            {/* 1. Flight Panel */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wider font-mono text-[var(--primary)] flex items-center gap-1.5">
                  <Plane className="w-4 h-4" />
                  Service 1: Flight Transportation
                </span>
                {flightState === 'confirmed' ? (
                  <span className="text-xs font-mono font-semibold text-[var(--status-success)] flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Ticket Issued
                  </span>
                ) : (
                  <span className="text-xs font-mono text-[var(--text-muted)]">Step 1 of 2</span>
                )}
              </div>

              <FlightVerificationCard
                flight={selectedFlight}
                origin={trip.origin}
                destination={trip.destination}
                travelersCount={trip.travelers_count || 2}
                verificationState={flightState}
                priceChange={priceChange}
                currentAmount={verifiedFlightAmount}
                confirmation={flightConfirmation}
                onVerify={handleVerifyFlight}
                onAcceptPriceChange={handleAcceptPriceChange}
                onSelectOtherFlight={() => onNavigateTab('flights')}
                onStartCheckoutHandoff={() => setIsHandoffModalOpen(true)}
                onSimulatePriceChange={handleTriggerPriceChange}
                onSimulateUnavailable={handleTriggerUnavailable}
              />
            </div>

            {/* 2. Stay Panel */}
            <div className="space-y-3 pt-4 border-t border-[var(--border)]">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold uppercase tracking-wider font-mono text-[var(--terracotta)] flex items-center gap-1.5">
                  <Building2 className="w-4 h-4" />
                  Service 2: Boutique Stay Reservation
                </span>
                {hotelState === 'confirmed' ? (
                  <span className="text-xs font-mono font-semibold text-[var(--status-success)] flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Stay Guaranteed
                  </span>
                ) : (
                  <span className="text-xs font-mono text-[var(--text-muted)]">Step 2 of 2</span>
                )}
              </div>

              <HotelBookingPanel
                hotel={selectedHotel}
                destination={trip.destination}
                startDate={trip.start_date}
                endDate={trip.end_date}
                travelersCount={trip.travelers_count || 2}
                hotelState={hotelState}
                confirmation={hotelConfirmation}
                onPrepareBooking={handlePrepareHotel}
                onOpenPaymentModal={() => setIsPaymentModalOpen(true)}
                onSelectOtherHotel={() => onNavigateTab('hotels')}
              />
            </div>
          </div>

          {/* Partial Booking Progress Action */}
          {(flightState === 'confirmed' || hotelState === 'confirmed') && (
            <div className="p-6 rounded-3xl bg-[var(--surface-muted)]/70 border border-[var(--border)] flex flex-col sm:flex-row items-center justify-between gap-4">
              <div className="space-y-1 text-center sm:text-left">
                <div className="font-editorial text-lg font-bold text-[var(--text-primary)]">
                  {flightState === 'confirmed' && hotelState === 'confirmed'
                    ? "Both services confirmed! Your journey is ready."
                    : flightState === 'confirmed'
                    ? "Flight is ticketed! Continue to hotel payment."
                    : "Stay is confirmed! Complete flight verification."}
                </div>
                <p className="text-xs text-[var(--text-secondary)] font-light">
                  TrustTrip preserves every confirmed milestone. You never lose your reserved options.
                </p>
              </div>

              {flightState === 'confirmed' && hotelState === 'confirmed' ? (
                <Button
                  variant="primary"
                  size="lg"
                  onClick={() => setCurrentStage('confirmed')}
                  rightIcon={<ArrowRight className="w-4 h-4" />}
                  className="shadow-xs shrink-0"
                >
                  View complete confirmation
                </Button>
              ) : flightState === 'confirmed' ? (
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => setIsPaymentModalOpen(true)}
                  rightIcon={<CreditCard className="w-4 h-4" />}
                  className="shadow-xs shrink-0"
                >
                  Complete hotel payment
                </Button>
              ) : null}
            </div>
          )}
        </div>
      )}

      {/* External Checkout Handoff Modal (Simulated Duffel) */}
      <CheckoutHandoffModal
        isOpen={isHandoffModalOpen}
        onClose={() => setIsHandoffModalOpen(false)}
        origin={trip.origin || 'Kolkata'}
        destination={trip.destination || 'Paris'}
        date={trip.start_date || '2026-10-12'}
        amount={verifiedFlightAmount}
        currency={selectedFlight?.currency || trip.currency || 'USD'}
        onSimulateComplete={handleCompleteCheckoutHandoff}
      />

      {/* Hotel Payment Modal (Simulated Razorpay) */}
      <PaymentReviewModal
        isOpen={isPaymentModalOpen}
        onClose={() => setIsPaymentModalOpen(false)}
        hotelName={selectedHotel?.name || 'Hotel Lutetia Left Bank'}
        roomName={selectedHotel?.rooms?.[0]?.name || 'Deluxe Heritage King'}
        startDate={trip.start_date || '2026-10-12'}
        endDate={trip.end_date || '2026-10-18'}
        guestsCount={trip.travelers_count || 2}
        roomAmount={selectedHotel ? Number(selectedHotel.total_amount) : 720}
        taxesAmount={Math.round((selectedHotel ? Number(selectedHotel.total_amount) : 720) * 0.1)}
        currency={selectedHotel?.currency || trip.currency || 'USD'}
        contact={contact}
        onProcessPayment={handleProcessHotelPayment}
      />
    </div>
  );
};
