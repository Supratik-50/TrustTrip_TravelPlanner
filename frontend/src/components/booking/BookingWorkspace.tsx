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
import { TripDetail, BookingAuthorizationInfo } from '../../types/trip';
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
  // Section 9: Exact revision identity authorization state (never an independent unvalidated boolean)
  const activeRevId = trip.active_revision_id;
  const activeRevNum = trip.active_revision_number;

  const [activeAuthorization, setActiveAuthorization] = useState<BookingAuthorizationInfo | undefined>(
    trip.booking_authorization &&
    activeRevId &&
    activeRevNum &&
    trip.booking_authorization.revision_id === activeRevId &&
    trip.booking_authorization.revision_number === activeRevNum
      ? trip.booking_authorization
      : undefined
  );

  // Synchronize / reset active authorization when active revision identity changes
  useEffect(() => {
    if (
      trip.booking_authorization &&
      trip.active_revision_id &&
      trip.active_revision_number &&
      trip.booking_authorization.revision_id === trip.active_revision_id &&
      trip.booking_authorization.revision_number === trip.active_revision_number
    ) {
      setActiveAuthorization(trip.booking_authorization);
    } else {
      setActiveAuthorization(undefined);
    }
  }, [trip.active_revision_id, trip.active_revision_number, trip.booking_authorization]);

  const isPlanAccepted = Boolean(isPlanApproved || trip.is_approved || trip.status === 'approved');

  // Exact identity match check:
  // authorization.revision_id === active_revision_id AND authorization.revision_number === active_revision_number
  const candidateAuth = activeAuthorization || (
    trip.booking_authorization &&
    activeRevId &&
    activeRevNum &&
    trip.booking_authorization.revision_id === activeRevId &&
    trip.booking_authorization.revision_number === activeRevNum
      ? trip.booking_authorization
      : undefined
  );

  const isBookingAuthorized = Boolean(
    isPlanAccepted &&
    candidateAuth &&
    activeRevId &&
    activeRevNum &&
    candidateAuth.revision_id === activeRevId &&
    candidateAuth.revision_number === activeRevNum
  );

  const bookingAuthorizedAt = isBookingAuthorized ? candidateAuth?.authorized_at : undefined;

  // Stale revision check (e.g. current_revision != active_revision)
  const isStaleRevision = Boolean(
    (trip.current_revision?.revision_id && activeRevId && trip.current_revision.revision_id !== activeRevId) ||
    (trip.current_revision?.revision_number && activeRevNum && trip.current_revision.revision_number !== activeRevNum)
  );

  // Authorize for booking is allowed only when:
  // - active revision exists
  // - plan is accepted
  // - active revision is not stale
  const canAuthorizeBooking = Boolean(
    isPlanAccepted &&
    activeRevId &&
    activeRevNum &&
    activeRevNum > 0 &&
    !isStaleRevision
  );

  // Historical authorization: any authorization on the trip that belongs to a different revision/version
  const historicalAuthorization = Boolean(
    trip.booking_authorization &&
    (!activeRevId || !activeRevNum ||
     trip.booking_authorization.revision_id !== activeRevId ||
     trip.booking_authorization.revision_number !== activeRevNum)
  ) ? trip.booking_authorization : undefined;
  const [flightState, setFlightState] = useState<FlightVerificationState>('idle');
  const [flightConfirmation, setFlightConfirmation] = useState<FlightConfirmationRecord | undefined>(undefined);
  const [hotelState, setHotelState] = useState<HotelBookingState | 'idle' | 'preparing' | 'payment_required' | 'confirmed'>('idle');
  const [hotelConfirmation, setHotelConfirmation] = useState<HotelConfirmationRecord | undefined>(undefined);

  // Section 5B/5C Authoritative Booking State
  const [bookingId, setBookingId] = useState<string | undefined>(undefined);
  const [isFlightApproved, setIsFlightApproved] = useState(false);
  const [isFinalConfirmationChecked, setIsFinalConfirmationChecked] = useState(false);
  const [bookingReference, setBookingReference] = useState<string | undefined>(undefined);
  const [orderId, setOrderId] = useState<string | undefined>(undefined);
  const [pnr, setPnr] = useState<string | undefined>(undefined);
  const [eTicket, setETicket] = useState<string | undefined>(undefined);
  const [errorMessage, setErrorMessage] = useState<string | undefined>(undefined);

  // Price change / verification details
  const [priceChange, setPriceChange] = useState<FlightPriceChangeNotice | undefined>(undefined);
  const [verifiedFlightAmount, setVerifiedFlightAmount] = useState<number>(860);
  const [verifiedCurrency, setVerifiedCurrency] = useState<string>('USD');
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
    if (
      trip.booking_authorization &&
      trip.active_revision_id &&
      trip.active_revision_number &&
      trip.booking_authorization.revision_id === trip.active_revision_id &&
      trip.booking_authorization.revision_number === trip.active_revision_number
    ) {
      setActiveAuthorization(trip.booking_authorization);
    } else {
      setActiveAuthorization(undefined);
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
  // Section 8 invariant: ONLY trip.active_revision_id + trip.active_revision_number
  // may be used as approval identity. No fallback to current_revision is permitted.
  const handleApprovePlan = async () => {
    setApprovalConflict(null);

    // Read ONLY the active revision fields — no current_revision fallback allowed.
    const reviewedRevisionId = trip.active_revision_id;
    const reviewedRevisionNumber = trip.active_revision_number;

    // Validate both fields are present and well-formed before any service call.
    if (!reviewedRevisionId || typeof reviewedRevisionId !== 'string' || reviewedRevisionId.trim() === '') {
      notify('No active revision found. Please wait for a plan to be generated.', 'error');
      return;
    }
    if (!reviewedRevisionNumber || typeof reviewedRevisionNumber !== 'number' || reviewedRevisionNumber < 1) {
      notify('Active revision number is missing or invalid. Please refresh.', 'error');
      return;
    }

    try {
      const res = await bookingService.approvePlan(
        trip.trip_id,
        reviewedRevisionNumber,
        reviewedRevisionId
      );
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

  // Section 9 invariant: ONLY trip.active_revision_id + trip.active_revision_number
  // may be used as authorization identity. No fallback to current_revision is permitted.
  const handleAuthorizeBooking = async () => {
    const reviewedRevisionId = trip.active_revision_id;
    const reviewedRevisionNumber = trip.active_revision_number;

    if (!reviewedRevisionId || typeof reviewedRevisionId !== 'string' || reviewedRevisionId.trim() === '') {
      notify('No active revision found. Please wait for a plan to be generated.', 'error');
      return;
    }
    if (!reviewedRevisionNumber || typeof reviewedRevisionNumber !== 'number' || reviewedRevisionNumber < 1) {
      notify('Active revision number is missing or invalid. Please refresh.', 'error');
      return;
    }

    if (!isPlanApproved && !trip.is_approved && trip.status !== 'approved') {
      notify('Journey plan must be approved before booking authorization can be granted.', 'warning', 'Approval Required');
      return;
    }

    if (isStaleRevision) {
      notify('Active revision is stale. Please refresh and review latest plan revision.', 'error', 'Stale Revision');
      return;
    }

    try {
      if (bookingService.authorizeBooking) {
        const res = await bookingService.authorizeBooking(
          trip.trip_id,
          reviewedRevisionNumber,
          reviewedRevisionId
        );
        setActiveAuthorization({
          authorization_id: res.authorization_id,
          trip_id: trip.trip_id,
          revision_id: reviewedRevisionId,
          revision_number: reviewedRevisionNumber,
          authorized_at: res.authorized_at,
        });
      }
      notify('Revision explicitly authorized for booking!', 'success', 'Booking Authorized');
    } catch (err: any) {
      if (err.statusCode === 409) {
        notify(err.message || 'Revision conflict: unable to authorize booking.', 'error', 'Conflict (409)');
      } else {
        notify(err.message || 'Unable to authorize booking for this revision.', 'error');
      }
    }
  };

  // Section 5B / 5C: Handle Flight Verification & Revalidation
  const handleVerifyFlight = async () => {
    setFlightState('verifying');
    setIsFinalConfirmationChecked(false);
    setErrorMessage(undefined);
    try {
      const offerId = selectedFlight?.offer_id || selectedFlight?.id || 'fl_off_101';
      let bId = bookingId || bookingService.getActiveBookingId(trip.trip_id);

      // Select offer if no booking intent exists yet
      if (!bId) {
        try {
          const sel = await bookingService.selectFlightOffer(trip.trip_id, offerId, [
            {
              given_name: contact.first_name || 'Traveler',
              family_name: contact.last_name || 'User',
              born_on: '1990-01-01',
              title: 'mr',
              gender: 'm',
              email: contact.email || 'traveler@trusttrip.com',
              phone_number: contact.phone || '+1234567890',
            },
          ]);
          if (sel?.booking_id) {
            const activeBId = String(sel.booking_id);
            bId = activeBId;
            setBookingId(activeBId);
            bookingService.setActiveBookingId(trip.trip_id, activeBId);
          }
        } catch (selErr: any) {
          if (selErr.statusCode === 410) {
            setFlightState('stale');
            setIsFlightApproved(false);
            setIsFinalConfirmationChecked(false);
            notify('The selected flight snapshot is stale or expired.', 'error', 'Offer Stale');
            return;
          }
          if (selErr.statusCode === 404) {
            setFlightState('unavailable');
            setIsFlightApproved(false);
            setIsFinalConfirmationChecked(false);
            notify('Offer not found or unavailable.', 'error', 'Unavailable');
            return;
          }
        }
      }

      if (bId) {
        try {
          const reval = await bookingService.revalidateFlightContract(bId);
          setVerifiedFlightAmount(reval.current_price);
          setVerifiedCurrency(reval.currency);

          if (reval.price_changed) {
            setFlightState('price_changed');
            setIsFlightApproved(false);
            setIsFinalConfirmationChecked(false);
            setPriceChange({
              previous_amount: reval.original_price,
              current_amount: reval.current_price,
              currency: reval.currency,
              reason: 'Airline supplier updated fare classes during revalidation.',
              change_timestamp: new Date().toISOString(),
            });
            notify('Flight fare adjusted by carrier. Please review before approving.', 'warning', 'Fare Changed');
          } else {
            setFlightState('awaiting_approval');
            setIsFlightApproved(false);
            setIsFinalConfirmationChecked(false);
            notify('Flight availability revalidated. Awaiting human approval.', 'info', 'Awaiting Approval');
          }
          return;
        } catch (revalErr: any) {
          if (revalErr.statusCode === 409) {
            const d = revalErr.detail || {};
            setFlightState('price_changed');
            setIsFlightApproved(false);
            setIsFinalConfirmationChecked(false);
            setPriceChange({
              previous_amount: Number(d.original_price || verifiedFlightAmount),
              current_amount: Number(d.new_price || verifiedFlightAmount),
              currency: d.currency || 'USD',
              reason: d.message || 'Carrier fare adjusted.',
              change_timestamp: new Date().toISOString(),
            });
            notify('Fare has updated. Please review price adjustment.', 'warning', 'Fare Updated');
            return;
          }
          if (revalErr.statusCode === 410) {
            setFlightState('unavailable');
            setIsFlightApproved(false);
            setIsFinalConfirmationChecked(false);
            notify('The selected flight is no longer available from the supplier.', 'error', 'Inventory Closed');
            return;
          }
          if (revalErr.statusCode >= 500) {
            setFlightState('failed');
            setIsFinalConfirmationChecked(false);
            setErrorMessage(revalErr.message || 'Upstream provider error during revalidation.');
            notify('Supplier communication error. Please retry.', 'error');
            return;
          }
        }
      }

      // Fallback to legacy verifyFlight
      const res = await bookingService.verifyFlight(trip.trip_id, offerId);
      setFlightState(res.state);
      setVerifiedFlightAmount(res.current_amount);
      setIsFinalConfirmationChecked(false);
      if (res.price_change) {
        setPriceChange(res.price_change);
        setIsFlightApproved(false);
        notify('Carrier fare has updated. Please review price adjustment.', 'warning', 'Fare Updated');
      } else if (res.state === 'unavailable') {
        setIsFlightApproved(false);
        notify('This flight is no longer available.', 'error', 'Inventory Closed');
      } else {
        notify('Flight seats verified. Awaiting human approval.', 'success', 'Flight Ready');
      }
    } catch (err: any) {
      setFlightState('failed');
      setIsFinalConfirmationChecked(false);
      setErrorMessage(err.message || 'Unable to verify flight availability.');
      notify('Unable to verify flight availability. Please retry.', 'error');
    }
  };

  // Section 5B / 5C: Accept Price Change (resets approval and re-enters HITL flow)
  const handleAcceptPriceChange = async () => {
    if (!priceChange) return;
    try {
      const bId = bookingId || bookingService.getActiveBookingId(trip.trip_id);
      if (bId) {
        const reval = await bookingService.revalidateFlightContract(bId, priceChange.current_amount);
        setVerifiedFlightAmount(reval.current_price);
        setVerifiedCurrency(reval.currency);
      }
      setPriceChange(undefined);
      // Safety rule: price change clears approval, and resets final confirmation checkbox
      setIsFlightApproved(false);
      setIsFinalConfirmationChecked(false);
      setFlightState('awaiting_approval');
      notify('Updated price accepted. Please provide human approval before confirmation.', 'info', 'Price Accepted');
    } catch (err: any) {
      notify(err.message || 'Failed to accept price change.', 'error');
    }
  };

  // Section 5B / 5C: Record Explicit Human-In-The-Loop Approval
  const handleApproveFlight = async () => {
    let bId = bookingId || bookingService.getActiveBookingId(trip.trip_id);
    if (!bId) {
      const offerId = selectedFlight?.offer_id || selectedFlight?.id || 'fl_off_101';
      const sel = await bookingService.selectFlightOffer(trip.trip_id, offerId, [
        {
          given_name: contact.first_name || 'Traveler',
          family_name: contact.last_name || 'User',
          born_on: '1990-01-01',
          title: 'mr',
          gender: 'm',
          email: contact.email || 'traveler@trusttrip.com',
          phone_number: contact.phone || '+1234567890',
        },
      ]);
      if (sel?.booking_id) {
        const activeBId = String(sel.booking_id);
        bId = activeBId;
        setBookingId(activeBId);
        bookingService.setActiveBookingId(trip.trip_id, activeBId);
        await bookingService.revalidateFlightContract(activeBId);
      }
    }

    if (!bId) {
      notify('Unable to establish flight booking for approval.', 'error');
      return;
    }

    try {
      await bookingService.approveFlightBooking(bId, 'Human approval recorded in traveler workspace');
      setIsFlightApproved(true);
      // ISSUE 2 FIX: Fresh approval must NOT automatically check final confirmation
      setIsFinalConfirmationChecked(false);
      setFlightState('ready');
      notify('Human approval recorded. Please review and confirm the current fare before booking.', 'success', 'Flight Approved');
    } catch (err: any) {
      notify(err.message || 'Unable to record approval.', 'error');
    }
  };

  // Section 5B / 5C: Execute Final Confirmation (Never automatic)
  const handleConfirmFlightBooking = async () => {
    if (!isFlightApproved) {
      notify('Human approval is required before confirmation.', 'warning', 'Approval Required');
      return;
    }
    if (!isFinalConfirmationChecked) {
      notify('Please confirm the current fare before booking.', 'warning', 'Confirmation Required');
      return;
    }

    const bId = bookingId || bookingService.getActiveBookingId(trip.trip_id);
    if (!bId) {
      notify('No active booking intent found to confirm.', 'error');
      return;
    }

    setFlightState('booking_in_progress');
    try {
      const res = await bookingService.confirmFlightBooking(bId);
      setBookingReference(res.booking_reference || undefined);
      setOrderId(res.order_id || undefined);
      setPnr(res.pnr || undefined);
      setETicket(res.e_ticket || undefined);
      setFlightState('confirmed');

      // ISSUE 1 FIX: Truthful confirmation record without fabricated random fallback numbers
      const confRecord: FlightConfirmationRecord = {
        booking_reference: res.booking_reference || 'Not provided',
        ticket_number: res.e_ticket || res.pnr || undefined,
        carrier_code: selectedFlight?.carrier_code || 'TT',
        carrier_name: selectedFlight?.carrier_name || 'Airline Partner',
        flight_number: selectedFlight?.slices?.[0]?.segments?.[0]?.flight_number || 'FL-100',
        origin_code: selectedFlight?.slices?.[0]?.origin || trip.origin || 'ORIG',
        origin_city: trip.origin?.split('(')[0]?.trim() || 'Origin',
        destination_code: selectedFlight?.slices?.[0]?.destination || trip.destination || 'DEST',
        destination_city: trip.destination?.split('(')[0]?.trim() || 'Destination',
        departure_time: selectedFlight?.slices?.[0]?.departure_time || trip.start_date || new Date().toISOString(),
        arrival_time: selectedFlight?.slices?.[0]?.arrival_time || trip.end_date || new Date().toISOString(),
        cabin_class: selectedFlight?.cabin_class || 'Economy',
        travelers_count: trip.travelers_count || 1,
        confirmed_at: new Date().toISOString(),
        status: 'ticketed',
      };
      setFlightConfirmation(confRecord);

      // Save confirmed state
      const s = getStoredBookingState(trip.trip_id);
      s.flightState = 'confirmed';
      s.flightConfirmation = confRecord;
      saveStoredBookingState(trip.trip_id, s);

      notify(`Flight confirmed and ticketed! Reference: ${res.booking_reference}`, 'success', 'Booking Confirmed');

      if (hotelState === 'confirmed') {
        setCurrentStage('confirmed');
      } else {
        setCurrentStage('hotel_stage');
      }
    } catch (err: any) {
      if (err.statusCode === 409) {
        if (err.detail?.duplicate_attempt) {
          setFlightState('confirmed');
          notify('Booking was already confirmed.', 'info', 'Already Confirmed');
          return;
        }
        // Price changed during confirmation
        const d = err.detail || {};
        setFlightState('price_changed');
        setIsFlightApproved(false);
        setPriceChange({
          previous_amount: Number(d.original_price || verifiedFlightAmount),
          current_amount: Number(d.new_price || verifiedFlightAmount),
          currency: d.currency || 'USD',
          reason: d.message || 'Fare updated right before confirmation.',
          change_timestamp: new Date().toISOString(),
        });
        notify('Carrier fare adjusted before ticketing. Human re-approval is required.', 'warning', 'Fare Changed');
        return;
      }
      if (err.statusCode === 400 && err.detail?.approval_required) {
        setFlightState('awaiting_approval');
        setIsFlightApproved(false);
        notify('Human approval is required before booking can be completed.', 'error', 'Approval Required');
        return;
      }
      if (err.statusCode === 410) {
        setFlightState('unavailable');
        notify('This offer is no longer available from the carrier.', 'error', 'Unavailable');
        return;
      }

      setFlightState('failed');
      setErrorMessage(err.message || 'Flight supplier booking failed.');
      notify(err.message || 'Flight booking failed.', 'error', 'Booking Error');
    }
  };

  // External Checkout Simulation (Legacy fallback)
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
    setActiveAuthorization(undefined);
    setIsFlightApproved(false);
    setIsFinalConfirmationChecked(false);
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

              {isBookingAuthorized ? (
                <span data-testid="booking-authorized-badge" className="ml-3 px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 text-xs font-mono font-semibold border border-emerald-500/20 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" />
                  Booking Authorized
                </span>
              ) : (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    data-testid="authorize-booking-button"
                    disabled={!canAuthorizeBooking}
                    onClick={handleAuthorizeBooking}
                    className={`ml-3 px-2.5 py-1 rounded-lg text-xs font-mono font-medium transition-colors ${
                      canAuthorizeBooking
                        ? 'bg-[var(--primary)] text-white hover:bg-[var(--primary-hover)] cursor-pointer'
                        : 'bg-gray-700/50 text-gray-400 cursor-not-allowed'
                    }`}
                  >
                    Authorize for Booking
                  </button>
                  {historicalAuthorization && (
                    <span data-testid="historical-auth-badge" className="text-xs text-[var(--text-muted)] font-mono">
                      (Rev #{historicalAuthorization.revision_number} authorized historically)
                    </span>
                  )}
                </div>
              )}
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
                travelersCount={trip.travelers_count || 1}
                verificationState={flightState}
                priceChange={priceChange}
                currentAmount={verifiedFlightAmount}
                currency={verifiedCurrency}
                confirmation={flightConfirmation}
                isApproved={isFlightApproved}
                bookingId={bookingId}
                bookingReference={bookingReference}
                orderId={orderId}
                pnr={pnr}
                eTicket={eTicket}
                errorMessage={errorMessage}
                finalConfirmationChecked={isFinalConfirmationChecked}
                onFinalConfirmationChange={setIsFinalConfirmationChecked}
                onVerify={handleVerifyFlight}
                onAcceptPriceChange={handleAcceptPriceChange}
                onApproveFlight={handleApproveFlight}
                onConfirmBooking={handleConfirmFlightBooking}
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
