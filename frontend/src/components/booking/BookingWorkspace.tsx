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
} from '../../mock/bookingMockData';
import { useNotification } from '../../context/NotificationContext';
import { useAuth } from '../../context/AuthContext';
import { FlightPassenger } from '../../types/flight';
import { BookingProgressBar } from './BookingProgressBar';
import { BookingReviewPanel } from './BookingReviewPanel';
import { ApprovalCard } from './ApprovalCard';
import { FlightVerificationCard } from './FlightVerificationCard';
import { CheckoutHandoffModal } from './CheckoutHandoffModal';
import { HotelBookingPanel } from './HotelBookingPanel';
import { PaymentReviewModal } from './PaymentReviewModal';
import { TravelerModal } from './TravelerModal';
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
  const [hotelState, setHotelState] = useState<HotelBookingState | 'idle' | 'preparing' | 'payment_required' | 'reconciliation_pending' | 'confirmed'>('idle');
  const [hotelConfirmation, setHotelConfirmation] = useState<HotelConfirmationRecord | undefined>(undefined);
  const [pendingHotelTransactionId, setPendingHotelTransactionId] = useState<string | undefined>(undefined);

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

  // Wave 2: Real Traveler & Passenger State
  const { user: currentUser } = useAuth();
  const travelersCount = Math.max(1, Math.min(9, trip.travelers_count || 1));
  const [travelers, setTravelers] = useState<FlightPassenger[]>([]);
  const [isTravelerModalOpen, setIsTravelerModalOpen] = useState(false);
  const [isCompletingPayment, setIsCompletingPayment] = useState(false);

  // Traveler validation helper
  const isTravelerValid = (p?: FlightPassenger): boolean => {
    if (!p) return false;
    return Boolean(
      p.given_name?.trim() &&
      p.family_name?.trim() &&
      p.born_on?.trim() &&
      /^\d{4}-\d{2}-\d{2}$/.test(p.born_on) &&
      p.gender &&
      p.title
    );
  };

  const isAllTravelersComplete = (list: FlightPassenger[], count: number): boolean => {
    if (!list || list.length !== count) return false;
    return list.every(isTravelerValid);
  };

  // Derive lead contact info from traveler 0 (precedence: explicit traveler email > currentUser email)
  const leadTraveler = travelers[0];
  const derivedContact: TravelerContactInfo = {
    first_name: leadTraveler?.given_name || '',
    last_name: leadTraveler?.family_name || '',
    email: (leadTraveler?.email && leadTraveler.email.trim() !== '') ? leadTraveler.email : (currentUser?.email || ''),
    phone: leadTraveler?.phone_number || '',
  };

  // Active view section when approved: 'stages' vs 'confirmation'
  const [currentStage, setCurrentStage] = useState<'review' | 'flight_stage' | 'hotel_stage' | 'confirmed'>(
    'review'
  );

  const selectedFlight = trip.selected_flight_offer as FlightOffer | undefined;
  const selectedHotel = (trip.selected_hotel_offer || (trip.metadata as any)?.selected_hotel) as HotelOffer | undefined;

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
    // Initialize non-confirmed temporary UI state from localStorage
    setFlightState(saved.flightState === 'confirmed' ? 'idle' : (saved.flightState || 'idle'));
    setFlightConfirmation(undefined);
    setHotelState(saved.hotelState === 'confirmed' ? 'idle' : (saved.hotelState || 'idle'));
    setHotelConfirmation(undefined);

    // Rehydrate authoritative hotel prebook from trip metadata for seamless continuity after refresh
    const prebookMeta = (trip.metadata as any)?.hotel_prebook;
    if ((prebookMeta && prebookMeta.prebook_id) || (bookingService.hasActiveHotelPrebook && bookingService.hasActiveHotelPrebook(trip.trip_id, trip.metadata))) {
      if (prebookMeta) {
        bookingService.setPrebookData?.(trip.trip_id, prebookMeta, selectedHotel);
      }
      if (!saved.hotelState || saved.hotelState === 'idle') {
        setHotelState('payment_required');
      }
    }

    // Authoritative Server-Driven Rehydration (Wave 3B)
    bookingService.getTripBookings(trip.trip_id)
      .then((res: any) => {
        if (!res) return;

        let hasConfirmedFlight = false;
        let hasConfirmedHotel = false;

        if (res.flight) {
          if (res.flight.is_booked || res.flight.status === 'completed') {
            hasConfirmedFlight = true;
            setIsFlightApproved(true);
            setFlightState('confirmed');
            const seg = res.flight.slices?.[0]?.segments?.[0];
            const flightConf: FlightConfirmationRecord = {
              booking_id: res.flight.booking_id,
              booking_reference: res.flight.booking_reference || res.flight.pnr || '',
              carrier_code: seg?.operating_carrier?.name?.slice(0, 2)?.toUpperCase() || 'TT',
              carrier_name: seg?.operating_carrier?.name || seg?.marketing_carrier?.name || 'Confirmed Airline',
              flight_number: seg?.marketing_carrier_flight_number || seg?.flight_number || '',
              origin_code: seg?.origin?.iata_code || seg?.origin_iata || trip.origin?.slice(0, 3)?.toUpperCase() || '',
              origin_city: trip.origin?.split('(')[0]?.trim() || '',
              destination_code: seg?.destination?.iata_code || seg?.destination_iata || trip.destination?.slice(0, 3)?.toUpperCase() || '',
              destination_city: trip.destination?.split('(')[0]?.trim() || '',
              departure_time: seg?.departing_at || seg?.departure_time || '',
              arrival_time: seg?.arriving_at || seg?.arrival_time || '',
              cabin_class: 'economy',
              travelers_count: res.flight.passengers?.length || trip.travelers_count || 1,
              confirmed_at: res.flight.completed_at || res.flight.created_at || '',
              status: 'ticketed',
            };
            setFlightConfirmation(flightConf);
          } else if (res.flight.status === 'ready_for_booking' || res.flight.status === 'approved') {
            setIsFlightApproved(true);
            setFlightState('ready');
          } else if (res.flight.in_progress || res.flight.status === 'handed_off') {
            setFlightState('booking_in_progress');
          }
        }

        if (res.hotel) {
          if (res.hotel.is_booked || res.hotel.status === 'booked') {
            hasConfirmedHotel = true;
            setHotelState('confirmed');
            const hotelConf: HotelConfirmationRecord = {
              transaction_id: res.hotel.transaction_id,
              hotel_name: res.hotel.hotel_name || 'Confirmed Hotel',
              property_name: res.hotel.hotel_name || 'Confirmed Hotel',
              confirmation_code: res.hotel.confirmation_code || res.hotel.transaction_id,
              room_name: res.hotel.room_name || 'Standard Room',
              check_in_date: res.hotel.check_in || trip.start_date || '',
              check_out_date: res.hotel.check_out || trip.end_date || '',
              check_in: res.hotel.check_in || trip.start_date || '',
              check_out: res.hotel.check_out || trip.end_date || '',
              total_price: res.hotel.amount_inr || 0,
              currency: res.hotel.currency || 'INR',
              confirmed_at: res.hotel.created_at || '',
              status: 'voucher_issued',
            };
            setHotelConfirmation(hotelConf);
          } else if (res.hotel.status === 'reconciliation_pending' || res.hotel.reconciliation_pending) {
            setHotelState('reconciliation_pending');
            setPendingHotelTransactionId(res.hotel.transaction_id);
          } else if (res.hotel.in_progress || res.hotel.status === 'booking_in_progress') {
            setHotelState('booking_in_progress');
          }
        } else if (bookingService.checkPendingHotelTransaction) {
          bookingService.checkPendingHotelTransaction(trip.trip_id).then((pTx: any) => {
            if (pTx) {
              if (pTx.booking_status === 'booked' && pTx.booking) {
                setHotelState('confirmed');
                setHotelConfirmation(pTx.booking);
              } else if (pTx.reconciliation_pending || pTx.booking_status === 'reconciliation_pending') {
                setHotelState('reconciliation_pending');
                setPendingHotelTransactionId(pTx.transaction_id);
              }
            }
          }).catch(() => {});
        }

        if (hasConfirmedFlight && hasConfirmedHotel) {
          setCurrentStage('confirmed');
        } else if (hasConfirmedFlight) {
          setCurrentStage('hotel_stage');
        } else if (saved.isApproved || trip.is_approved) {
          setCurrentStage('flight_stage');
        }
      })
      .catch((_err) => {
        // Fall back to non-authoritative local state only if backend is unreachable
        if (saved.flightState && saved.flightState !== 'idle') {
          setFlightState(saved.flightState);
          if (saved.flightConfirmation) setFlightConfirmation(saved.flightConfirmation);
        }
        if (saved.hotelState && saved.hotelState !== 'idle') {
          setHotelState(saved.hotelState);
          if (saved.hotelConfirmation) setHotelConfirmation(saved.hotelConfirmation);
        }
      });

    // Sync saved travelers if count matches, otherwise initialize array of size travelersCount
    if (saved.travelers && saved.travelers.length === travelersCount) {
      setTravelers(saved.travelers);
    } else {
      setTravelers((prev) => {
        if (prev.length === travelersCount && prev.some((p) => p.given_name)) {
          return prev;
        }
        const initial: FlightPassenger[] = [];
        for (let i = 0; i < travelersCount; i++) {
          initial.push({
            given_name: '',
            family_name: '',
            born_on: '',
            title: 'mr',
            gender: 'm',
            email: i === 0 && currentUser?.email ? currentUser.email : '',
            phone_number: '',
            type: 'adult',
          });
        }
        return initial;
      });
    }

    if (saved.isApproved || trip.is_approved) {
      setCurrentStage('flight_stage');
    }
  }, [trip, travelersCount, currentUser?.email]);

  // Synchronize traveler slots when travelersCount or auth email updates
  useEffect(() => {
    setTravelers((prev) => {
      if (prev.length === travelersCount) return prev;
      const updated: FlightPassenger[] = [];
      for (let i = 0; i < travelersCount; i++) {
        const existing = prev[i];
        updated.push({
          given_name: existing?.given_name || '',
          family_name: existing?.family_name || '',
          born_on: existing?.born_on || '',
          title: existing?.title || 'mr',
          gender: existing?.gender || 'm',
          email: existing?.email !== undefined ? existing.email : (i === 0 && currentUser?.email ? currentUser.email : ''),
          phone_number: existing?.phone_number || '',
          type: existing?.type || 'adult',
        });
      }
      return updated;
    });
  }, [travelersCount, currentUser?.email]);

  // Handle saving travelers from modal (with stale booking intent invalidation)
  const handleSaveTravelers = (updatedTravelers: FlightPassenger[]) => {
    const hadBooking = Boolean(bookingId || bookingService.getActiveBookingId(trip.trip_id));
    setTravelers(updatedTravelers);
    setIsTravelerModalOpen(false);

    const stored = getStoredBookingState(trip.trip_id);
    stored.travelers = updatedTravelers;
    saveStoredBookingState(trip.trip_id, stored);

    if (hadBooking) {
      setBookingId(undefined);
      bookingService.setActiveBookingId(trip.trip_id, '');
      setIsFlightApproved(false);
      setIsFinalConfirmationChecked(false);
      setFlightState('idle');
      notify(
        'Traveler details updated. Active flight booking intent invalidated; please re-verify flight for updated passengers.',
        'info',
        'Booking Intent Invalidated'
      );
    } else {
      notify('Traveler details saved successfully.', 'success', 'Travelers Updated');
    }
  };

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
        // Authoritative server-derived check: only treat as approved if current revision is actually approved on server
        try {
          const canon: any = bookingService.getCanonicalTrip ? await bookingService.getCanonicalTrip(trip.trip_id) : undefined;
          const canonRevId = canon?.active_revision_id;
          const isCanonApproved = Boolean(canon?.is_approved || canon?.status === 'approved');
          if (canon && isCanonApproved && canonRevId === reviewedRevisionId) {
            setIsPlanApproved(true);
            if (canon.approved_at) {
              setApprovedAt(canon.approved_at);
            }
            setApprovalConflict(null);
            notify('Journey plan is already approved for the current revision.', 'info', 'Plan Approved');
            return;
          }
        } catch (refreshErr) {
          console.error('Failed to verify canonical revision approval on 409:', refreshErr);
        }
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
    if (!isAllTravelersComplete(travelers, travelersCount)) {
      setIsTravelerModalOpen(true);
      notify(
        `Please complete passenger details for all ${travelersCount} traveler(s) before verifying flight.`,
        'warning',
        'Passenger Details Required'
      );
      setFlightState('idle');
      return;
    }

    setFlightState('verifying');
    setIsFinalConfirmationChecked(false);
    setErrorMessage(undefined);
    try {
      const offerId = selectedFlight?.offer_id || selectedFlight?.id || 'fl_off_101';
      let bId = bookingId || bookingService.getActiveBookingId(trip.trip_id);

      // Select offer if no booking intent exists yet
      if (!bId) {
        try {
          const sel = await bookingService.selectFlightOffer(trip.trip_id, offerId, travelers);
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
    if (!isAllTravelersComplete(travelers, travelersCount)) {
      setIsTravelerModalOpen(true);
      notify(
        `Please complete passenger details for all ${travelersCount} traveler(s) before approving flight.`,
        'warning',
        'Passenger Details Required'
      );
      return;
    }

    let bId = bookingId || bookingService.getActiveBookingId(trip.trip_id);
    if (!bId) {
      const offerId = selectedFlight?.offer_id || selectedFlight?.id || 'fl_off_101';
      const sel = await bookingService.selectFlightOffer(trip.trip_id, offerId, travelers);
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
      if (err.statusCode === 409) {
        try {
          const bookingDetails = await bookingService.getFlightBooking(bId);
          if (bookingDetails && (bookingDetails.status === 'ready_for_booking' || bookingDetails.status === 'approved' || bookingDetails.status === 'completed')) {
            setIsFlightApproved(true);
            setIsFinalConfirmationChecked(false);
            setFlightState(bookingDetails.status === 'completed' ? 'confirmed' : 'ready');
            notify('Flight booking is already approved.', 'info', 'Flight Approved');
            return;
          }
        } catch (queryErr) {
          console.error('Failed to verify flight booking approval on 409:', queryErr);
        }
      }
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
      if (!res.booking_reference) {
        throw new Error('Supplier confirmation reference is not available yet.');
      }
      setBookingReference(res.booking_reference || undefined);
      setOrderId(res.order_id || undefined);
      setPnr(res.pnr || undefined);
      setETicket(res.e_ticket || undefined);
      setFlightState('confirmed');

      // ISSUE 1 FIX: Truthful confirmation record without fabricated random fallback numbers
      const confRecord: FlightConfirmationRecord = {
        booking_reference: res.booking_reference,
        ticket_number: res.e_ticket || res.pnr || undefined,
        carrier_code: selectedFlight?.carrier_code || '',
        carrier_name: selectedFlight?.carrier_name || '',
        flight_number: selectedFlight?.slices?.[0]?.segments?.[0]?.flight_number || '',
        origin_code: selectedFlight?.slices?.[0]?.origin || '',
        origin_city: trip.origin?.split('(')[0]?.trim() || '',
        destination_code: selectedFlight?.slices?.[0]?.destination || '',
        destination_city: trip.destination?.split('(')[0]?.trim() || '',
        departure_time: selectedFlight?.slices?.[0]?.departure_time || '',
        arrival_time: selectedFlight?.slices?.[0]?.arrival_time || '',
        cabin_class: selectedFlight?.cabin_class || '',
        travelers_count: trip.travelers_count || 0,
        confirmed_at: '',
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
          // A duplicate conflict is not confirmation evidence. The UI must wait
          // until a persisted supplier booking result can be retrieved.
          setFlightState('booking_in_progress');
          setErrorMessage('This booking is already being processed. Refresh its status before treating it as confirmed.');
          notify('Booking status is still being verified by the supplier.', 'info', 'Verification Pending');
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

  // Prepare Hotel Booking
  const handlePrepareHotel = async () => {
    const curHotel = (trip.selected_hotel_offer || (trip.metadata as any)?.selected_hotel) as HotelOffer | undefined;
    if (!curHotel) {
      const err = new Error('No hotel selected for this trip. Please select a stay from the Stays tab first.');
      notify(err.message, 'warning', 'Stay Required');
      throw err;
    }

    const lead = travelers[0];
    if (!lead || !lead.given_name?.trim() || !lead.family_name?.trim()) {
      setIsTravelerModalOpen(true);
      const err = new Error('Please enter passenger details before preparing the hotel reservation.');
      notify(err.message, 'warning', 'Traveler Details Required');
      throw err;
    }

    const guestInfo = {
      firstName: lead.given_name.trim(),
      lastName: lead.family_name.trim(),
      email: (lead.email && lead.email.trim() !== '') ? lead.email.trim() : (currentUser?.email || ''),
      phone: lead.phone_number?.trim() || '',
    };

    const snapId = curHotel.snapshot_id || (trip.metadata as any)?.selected_hotel_snapshot_id || curHotel.id || '';

    const offerPayload = {
      ...curHotel,
      check_in_date: curHotel.check_in_date || (curHotel as any).checkin || trip.start_date,
      check_out_date: curHotel.check_out_date || (curHotel as any).checkout || trip.end_date,
      snapshot_id: snapId,
    };

    try {
      await bookingService.prepareHotelBooking(
        trip.trip_id,
        snapId,
        offerPayload,
        guestInfo
      );
      setHotelState('payment_required');
      notify('Stay reservation prepared. Ready for payment confirmation.', 'info', 'Reservation Prepared');
    } catch (err: any) {
      notify(err.message || 'Failed to prepare hotel reservation with supplier.', 'error', 'Reservation Error');
      throw err;
    }
  };

  // Initiate Hotel Payment Order (Razorpay)
  const handleInitiateHotelOrder = async () => {
    if (hotelState === 'reconciliation_pending' || pendingHotelTransactionId) {
      const pendingErr = new Error('A payment for this hotel stay has already been captured and is pending verification. Please check confirmation status before initiating another order.');
      notify(pendingErr.message, 'warning', 'Verification In Progress');
      throw pendingErr;
    }

    if (
      !activeAuthorization ||
      !activeAuthorization.revision_id ||
      typeof activeAuthorization.revision_number !== 'number' ||
      activeAuthorization.revision_number < 1
    ) {
      const authErr = new Error('Active booking authorization is required before processing hotel payment. Please authorize the plan revision first.');
      notify(authErr.message, 'error', 'Authorization Required');
      throw authErr;
    }

    if (!bookingService.initiateHotelPaymentOrder) {
      throw new Error('Hotel payment initiation is not supported by booking service.');
    }

    // Continuity: If an authoritative hotel prebook is not active in this session,
    // prepare it before creating the Razorpay order.
    if (!bookingService.hasActiveHotelPrebook?.(trip.trip_id, trip.metadata)) {
      await handlePrepareHotel();
    }

    return await bookingService.initiateHotelPaymentOrder(
      trip.trip_id,
      derivedContact,
      activeAuthorization.revision_id,
      activeAuthorization.revision_number,
      travelers.length > 0 ? travelers : undefined
    );
  };

  // Process Hotel Payment
  const handleProcessHotelPayment = async (
    method: 'card' | 'upi' | 'netbanking' | 'pay_at_property',
    travelerContact: TravelerContactInfo,
    paymentDetails?: { transaction_id?: string; razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string }
  ) => {
    if (
      !activeAuthorization ||
      !activeAuthorization.revision_id ||
      typeof activeAuthorization.revision_number !== 'number' ||
      activeAuthorization.revision_number < 1
    ) {
      const authErr = new Error('Active booking authorization is required before processing hotel payment. Please authorize the plan revision first.');
      notify(authErr.message, 'error', 'Authorization Required');
      throw authErr;
    }

    const revId = activeAuthorization.revision_id;
    const revNum = activeAuthorization.revision_number;
    const outcome = await bookingService.processHotelPayment(
      trip.trip_id,
      method,
      travelerContact,
      revId,
      revNum,
      paymentDetails,
      travelers.length > 0 ? travelers : undefined
    );

    if ((outcome as any)?.status === 'reconciliation_pending' || (outcome as any)?.reconciliation_pending) {
      setHotelState('reconciliation_pending');
      setPendingHotelTransactionId((outcome as any).transaction_id);
      notify('Payment received — reservation verification in progress with supplier.', 'warning', 'Verification Pending');
      return outcome;
    }

    const confirmRec = outcome as HotelConfirmationRecord;
    setHotelConfirmation(confirmRec);
    setHotelState('confirmed');
    setPendingHotelTransactionId(undefined);
    notify(`Stay reserved! Voucher code: ${confirmRec.confirmation_code}`, 'success', 'Stay Confirmed');

    // Both confirmed
    if (flightState === 'confirmed') {
      setCurrentStage('confirmed');
    }
    return confirmRec;
  };

  // Reconcile Hotel Payment
  const handleReconcileHotelPayment = async (transactionId: string) => {
    if (bookingService.reconcileHotelPayment) {
      const outcome = await bookingService.reconcileHotelPayment(trip.trip_id, transactionId);
      if ((outcome as any)?.status === 'reconciliation_pending' || (outcome as any)?.reconciliation_pending) {
        setHotelState('reconciliation_pending');
        return outcome;
      }
      const confirmRec = outcome as HotelConfirmationRecord;
      setHotelConfirmation(confirmRec);
      setHotelState('confirmed');
      setPendingHotelTransactionId(undefined);
      notify(`Stay confirmed! Voucher code: ${confirmRec.confirmation_code}`, 'success', 'Stay Confirmed');
      if (flightState === 'confirmed') {
        setCurrentStage('confirmed');
      }
      return confirmRec;
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
    setTravelers([]);
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
          contactEmail={derivedContact.email || currentUser?.email || ''}
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
            contact={derivedContact}
            travelers={travelers}
            isApproved={isPlanApproved}
            onEditSection={(section) => onNavigateTab(section)}
            onEditTravelers={() => setIsTravelerModalOpen(true)}
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

          {/* Wave 2: Passenger / Traveler Information Bar */}
          <div className="p-4 rounded-2xl bg-[var(--surface)] border border-[var(--border)] flex flex-wrap items-center justify-between gap-3 shadow-xs">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
                <Users className="w-4 h-4" />
              </div>
              <div>
                <div className="text-xs font-mono font-bold uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-2">
                  <span>Passenger Information ({travelersCount} traveler{travelersCount > 1 ? 's' : ''})</span>
                  {isAllTravelersComplete(travelers, travelersCount) ? (
                    <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 text-[10px] font-semibold border border-emerald-500/20 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> Ready
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 text-[10px] font-semibold border border-amber-500/20 flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" /> Details Incomplete
                    </span>
                  )}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-light mt-0.5">
                  {isAllTravelersComplete(travelers, travelersCount)
                    ? travelers.map((t) => `${t.title ? `${t.title.toUpperCase()} ` : ''}${t.given_name} ${t.family_name}`).join(', ')
                    : 'Please enter passenger names and birthdates for ticketing.'}
                </div>
              </div>
            </div>
            <button
              type="button"
              data-testid="edit-travelers-btn"
              onClick={() => setIsTravelerModalOpen(true)}
              className="px-3 py-1.5 rounded-xl text-xs font-mono font-medium bg-[var(--primary)]/10 text-[var(--primary)] hover:bg-[var(--primary)]/20 transition-colors cursor-pointer"
            >
              {isAllTravelersComplete(travelers, travelersCount) ? 'Edit Passengers' : 'Enter Passenger Details'}
            </button>
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
                isBookingAuthorized={isBookingAuthorized}
                canAuthorizeBooking={canAuthorizeBooking}
                onAuthorizeBooking={handleAuthorizeBooking}
                onPrepareBooking={handlePrepareHotel}
                onOpenPaymentModal={() => setIsPaymentModalOpen(true)}
                onSelectOtherHotel={() => onNavigateTab('hotels')}
                onEditTravelers={() => setIsTravelerModalOpen(true)}
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
                isBookingAuthorized ? (
                  <Button
                    variant="primary"
                    size="md"
                    isLoading={isCompletingPayment}
                    disabled={isCompletingPayment}
                    onClick={async () => {
                      if (isCompletingPayment) return;

                      // 1. If already pending reconciliation, open modal to inspect status
                      if (hotelState === 'reconciliation_pending' || pendingHotelTransactionId) {
                        setIsPaymentModalOpen(true);
                        return;
                      }

                      // 2. Validate passenger / traveler details
                      if (!isAllTravelersComplete(travelers, travelersCount)) {
                        notify('Please enter complete passenger details before proceeding to payment.', 'warning', 'Traveler Details Required');
                        setIsTravelerModalOpen(true);
                        return;
                      }

                      // 3. Ensure authoritative hotel prebook is active
                      setIsCompletingPayment(true);
                      try {
                        if (!bookingService.hasActiveHotelPrebook?.(trip.trip_id, trip.metadata)) {
                          await handlePrepareHotel();
                        }
                        setIsPaymentModalOpen(true);
                      } catch (err: any) {
                        notify(err.message || 'Unable to prepare hotel reservation for payment.', 'error', 'Payment Preparation Failed');
                      } finally {
                        setIsCompletingPayment(false);
                      }
                    }}
                    rightIcon={<CreditCard className="w-4 h-4" />}
                    className="shadow-xs shrink-0"
                  >
                    Complete hotel payment
                  </Button>
                ) : (
                  <div className="flex flex-col sm:flex-row items-end sm:items-center gap-2">
                    <span className="text-xs text-amber-500 font-medium">
                      Booking authorization required before hotel payment
                    </span>
                    <Button
                      variant="primary"
                      size="md"
                      disabled={!canAuthorizeBooking}
                      onClick={handleAuthorizeBooking}
                      rightIcon={<ShieldCheck className="w-4 h-4" />}
                      className="shadow-xs shrink-0 bg-amber-600 hover:bg-amber-700"
                    >
                      Authorize for Booking
                    </Button>
                  </div>
                )
              ) : null}
            </div>
          )}
        </div>
      )}

      {/* External Checkout Handoff Modal (Simulated Duffel) */}
      <CheckoutHandoffModal
        isOpen={isHandoffModalOpen}
        onClose={() => setIsHandoffModalOpen(false)}
        origin={trip.origin || ''}
        destination={trip.destination || ''}
        date={trip.start_date || ''}
        amount={verifiedFlightAmount}
        currency={selectedFlight?.currency || trip.currency || 'USD'}
      />

      {/* Hotel Payment Modal (Official Razorpay Checkout) */}
      <PaymentReviewModal
        isOpen={isPaymentModalOpen}
        onClose={() => setIsPaymentModalOpen(false)}
        hotelName={selectedHotel?.name || selectedHotel?.hotel_name || ''}
        roomName={selectedHotel?.rooms?.[0]?.name || selectedHotel?.room_name || ''}
        startDate={trip.start_date || ''}
        endDate={trip.end_date || ''}
        guestsCount={trip.travelers_count || 0}
        roomAmount={selectedHotel ? Number(selectedHotel.total_amount || selectedHotel.price || 0) : 0}
        currency={selectedHotel?.currency || trip.currency || 'USD'}
        priceInr={selectedHotel?.price_inr}
        exchangeRate={selectedHotel?.exchange_rate}
        contact={derivedContact}
        initialTransactionId={pendingHotelTransactionId}
        initialReconciliationPending={hotelState === 'reconciliation_pending'}
        onInitiateOrder={handleInitiateHotelOrder}
        onProcessPayment={handleProcessHotelPayment}
        onReconcilePayment={handleReconcileHotelPayment}
      />

      {/* Traveler / Passenger Details Modal */}
      <TravelerModal
        isOpen={isTravelerModalOpen}
        onClose={() => setIsTravelerModalOpen(false)}
        travelers={travelers}
        travelersCount={travelersCount}
        onSave={handleSaveTravelers}
        defaultEmail={currentUser?.email || ''}
      />
    </div>
  );
};
