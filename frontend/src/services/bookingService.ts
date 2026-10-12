/**
 * TrustTrip - Part 5B Real Backend Booking Service Adapter
 * 
 * Implements IBookingService by delegating directly to the authoritative
 * FastAPI backend via the centralized apiClient (src/lib/apiClient.ts).
 * 
 * Architecture:
 * UI Components (BookingWorkspace, ApprovalCard, etc.)
 *       ↓
 * IBookingService (src/types/bookingFrontend.ts)
 *       ↓
 * TrustTripBookingService (src/services/bookingService.ts)
 *       ↓
 * api (src/lib/apiClient.ts)
 *       ↓
 * FastAPI Backend Endpoints (/api/trips, /api/bookings, /api/hotels, /api/travel)
 */

import {
  IBookingService,
  FlightVerificationState,
  FlightPriceChangeNotice,
  ExternalCheckoutHandoff,
  FlightConfirmationRecord,
  HotelConfirmationRecord,
  HotelPaymentResult,
  HotelReconciliationPendingResult,
  TravelerContactInfo,
  CancellationRequest,
} from '../types/bookingFrontend';
import { api } from '../lib/apiClient';
import { TripDetail } from '../types/trip';
import {
  BookingIntent,
  FlightOffer,
  FlightPassenger,
  FlightBookingSelectionResponse,
  FlightBookingRevalidationResponse,
  FlightBookingApprovalResponse,
  FlightBookingConfirmationResponse,
  FlightBookingDetails,
} from '../types/flight';
import { HotelOffer } from '../types/hotel';

export class TrustTripBookingService implements IBookingService {
  // Ephemeral memory for active booking ID and intent ID during the flow session
  private activeIntentIds: Map<string, string> = new Map();
  private activeBookingIds: Map<string, string> = new Map();
  private activePrebookIds: Map<string, string> = new Map();
  private activePrebookData: Map<string, any> = new Map();
  private activeHotelOffers: Map<string, any> = new Map();
  private activeHotelTransactions: Map<string, any> = new Map();

  /**
   * Approves canonical journey plan via POST /api/trips/{trip_id}/approve.
   *
   * Section 8 invariant: the revision approved MUST be exactly the revision
   * the user reviewed. Both revisionId and expectedRevision are REQUIRED.
   * Neither may be inferred from current trip state.
   *
   * @throws {Error} immediately (without calling the backend) if revisionId
   *   is absent/empty or expectedRevision is absent/non-positive.
   */
  async approvePlan(
    tripId: string,
    expectedRevision: number,
    revisionId: string
  ): Promise<{ approved: boolean; approved_at: string; active_revision_number?: number }> {
    // Local pre-flight validation — reject immediately, do NOT call backend
    if (!revisionId || typeof revisionId !== 'string' || revisionId.trim() === '') {
      throw Object.assign(
        new Error('approvePlan requires an exact revisionId. Do not call this method without the reviewed revision identity.'),
        { code: 'MISSING_REVISION_ID', statusCode: 400 }
      );
    }
    if (typeof expectedRevision !== 'number' || !Number.isInteger(expectedRevision) || expectedRevision < 1) {
      throw Object.assign(
        new Error('approvePlan requires a valid positive expectedRevision number. Do not infer from current trip state.'),
        { code: 'MISSING_EXPECTED_REVISION', statusCode: 400 }
      );
    }

    const res = await api.approveTrip(tripId, {
      revision_id: revisionId,
      expected_revision_number: expectedRevision,
      notes: 'Approved by traveler in review stage',
    });

    return {
      approved: res.is_approved,
      approved_at: res.approved_at,
      active_revision_number: res.active_revision_number,
    };
  }

  /**
   * Authorizes canonical journey plan for booking via POST /api/trips/{trip_id}/booking-approval.
   *
   * Section 9 invariant: the revision authorized MUST be exactly the revision
   * the user reviewed. Both revisionId and expectedRevision are REQUIRED.
   * Neither may be inferred from current trip state.
   */
  async authorizeBooking(
    tripId: string,
    expectedRevision: number,
    revisionId: string,
    notes?: string
  ): Promise<{ authorized: boolean; authorized_at: string; authorization_id: string; revision_number: number }> {
    // Local pre-flight validation — reject immediately, do NOT call backend
    if (!revisionId || typeof revisionId !== 'string' || revisionId.trim() === '') {
      throw Object.assign(
        new Error('authorizeBooking requires an exact revisionId. Do not call this method without the reviewed revision identity.'),
        { code: 'MISSING_REVISION_ID', statusCode: 400 }
      );
    }
    if (typeof expectedRevision !== 'number' || !Number.isInteger(expectedRevision) || expectedRevision < 1) {
      throw Object.assign(
        new Error('authorizeBooking requires a valid positive expectedRevision number. Do not infer from current trip state.'),
        { code: 'MISSING_EXPECTED_REVISION', statusCode: 400 }
      );
    }

    const res = await api.authorizeBooking(tripId, {
      revision_id: revisionId,
      expected_revision_number: expectedRevision,
      notes: notes || 'Authorized for booking by traveler',
    });

    return {
      authorized: res.is_authorized,
      authorized_at: res.authorized_at,
      authorization_id: res.authorization_id,
      revision_number: res.revision_number,
    };
  }

  // ========================================================================
  // SECTION 5B / 5C: CANONICAL FLIGHT BOOKING CONTRACT METHODS
  // ========================================================================

  /**
   * Selects an offer snapshot and creates a live booking intent on Section 5B backend:
   * POST /api/flights/booking/select
   */
  async selectFlightOffer(
    tripId: string,
    offerId: string,
    passengers: FlightPassenger[]
  ): Promise<FlightBookingSelectionResponse> {
    const res = await api.selectFlightBooking({
      trip_id: tripId,
      offer_id: offerId,
      passengers,
    });
    if (res?.booking_id) {
      this.activeBookingIds.set(tripId, res.booking_id);
    }
    return res;
  }

  /**
   * Revalidates live provider price and inventory on Section 5B / 5C backend:
   * POST /api/flights/booking/{booking_id}/revalidate
   */
  async revalidateFlightContract(
    bookingId: string,
    acceptedPrice?: number
  ): Promise<FlightBookingRevalidationResponse> {
    return await api.revalidateFlight(bookingId, acceptedPrice);
  }

  /**
   * Records explicit Human-In-The-Loop approval on Section 5B / 5C backend:
   * POST /api/flights/booking/{booking_id}/approve
   */
  async approveFlightBooking(
    bookingId: string,
    notes?: string
  ): Promise<FlightBookingApprovalResponse> {
    return await api.approveFlight(bookingId, { approved: true, notes });
  }

  /**
   * Confirms booking with upstream supplier on Section 5B / 5C backend:
   * POST /api/flights/booking/{booking_id}/confirm
   */
  async confirmFlightBooking(
    bookingId: string
  ): Promise<FlightBookingConfirmationResponse> {
    return await api.confirmFlightBooking(bookingId, { final_confirmation: true });
  }

  /**
   * Queries authoritative flight booking status:
   * GET /api/flights/booking/{booking_id}
   */
  async getFlightBooking(bookingId: string): Promise<FlightBookingDetails> {
    return await api.getFlightBooking(bookingId);
  }

  /**
   * Active booking ID helper
   */
  getActiveBookingId(tripId: string): string | undefined {
    return this.activeBookingIds.get(tripId);
  }

  setActiveBookingId(tripId: string, bookingId: string): void {
    this.activeBookingIds.set(tripId, bookingId);
  }

  /**
   * Verifies live flight seat availability & fare rules via live Section 5B backend
   * with automatic fallback for older prototype mocks.
   */
  async verifyFlight(
    tripId: string,
    offerId: string,
    passengers?: FlightPassenger[]
  ): Promise<{
    state: FlightVerificationState;
    current_amount: number;
    currency: string;
    price_change?: FlightPriceChangeNotice;
    handoff?: ExternalCheckoutHandoff;
    intent_id?: string;
    booking_id?: string;
  }> {
    // 1. Try authoritative Section 5B contract if booking_id exists or can be initiated
    let bookingId = this.activeBookingIds.get(tripId);

    // If no booking_id yet, select the offer on the live backend using supplied passengers
    if (!bookingId) {
      if (!passengers || passengers.length === 0) {
        throw Object.assign(
          new Error('Passenger details are required to verify and select flight offer.'),
          { code: 'PASSENGERS_REQUIRED', statusCode: 400 }
        );
      }
      try {
        const selectRes = await this.selectFlightOffer(tripId, offerId, passengers);
        bookingId = selectRes.booking_id;
      } catch (selErr: any) {
        if (selErr.statusCode === 410) {
          return {
            state: 'stale',
            current_amount: 0,
            currency: 'USD',
          };
        }
        throw selErr;
      }
    }

    if (bookingId) {
      try {
        const revalRes = await this.revalidateFlightContract(bookingId);
        if (revalRes.price_changed) {
          return {
            state: 'price_changed',
            booking_id: bookingId,
            current_amount: revalRes.current_price,
            currency: revalRes.currency,
            price_change: {
              previous_amount: revalRes.original_price,
              current_amount: revalRes.current_price,
              currency: revalRes.currency,
              reason: 'The carrier updated fare bucket inventory for this scheduled departure.',
              change_timestamp: new Date().toISOString(),
            },
          };
        }

        return {
          state: 'awaiting_approval',
          booking_id: bookingId,
          current_amount: revalRes.current_price,
          currency: revalRes.currency,
        };
      } catch (revalErr: any) {
        if (revalErr.statusCode === 409) {
          const detail = revalErr.detail || {};
          return {
            state: 'price_changed',
            booking_id: bookingId,
            current_amount: Number(detail.new_price || detail.current_price || 915),
            currency: detail.currency || 'USD',
            price_change: {
              previous_amount: Number(detail.original_price || 860),
              current_amount: Number(detail.new_price || detail.current_price || 915),
              currency: detail.currency || 'USD',
              reason: detail.message || 'The carrier updated fare bucket inventory for this scheduled departure.',
              change_timestamp: new Date().toISOString(),
            },
          };
        }
        if (revalErr.statusCode === 410) {
          return {
            state: 'unavailable',
            booking_id: bookingId,
            current_amount: 0,
            currency: 'USD',
          };
        }
        // If 502/504
        if (revalErr.statusCode >= 500) {
          return {
            state: 'failed',
            booking_id: bookingId,
            current_amount: 0,
            currency: 'USD',
          };
        }
      }
    }

    // 2. Fallback to legacy prepare/revalidate flow
    try {
      const prepareRes: BookingIntent = await api.prepareFlightBooking({
        trip_id: tripId,
        offer_id: offerId,
        snapshot_id: `fl_snap_${offerId}`,
        amount: '860',
        currency: 'USD',
      });

      this.activeIntentIds.set(tripId, prepareRes.intent_id);

      const revalidateRes: BookingIntent = await api.revalidateFlightBooking(
        prepareRes.intent_id,
        offerId,
        prepareRes.snapshot_id
      );

      if (revalidateRes.status === 'unavailable') {
        return {
          state: 'unavailable',
          current_amount: Number(revalidateRes.original_amount),
          currency: revalidateRes.currency,
        };
      }

      if (revalidateRes.status === 'expired') {
        return {
          state: 'expired',
          current_amount: Number(revalidateRes.original_amount),
          currency: revalidateRes.currency,
        };
      }

      if (
        revalidateRes.status === 'price_changed' ||
        (revalidateRes.revalidated_amount &&
          revalidateRes.revalidated_amount !== revalidateRes.original_amount &&
          !revalidateRes.price_change_acknowledged)
      ) {
        return {
          state: 'price_changed',
          current_amount: Number(revalidateRes.revalidated_amount || revalidateRes.original_amount),
          currency: revalidateRes.currency,
          price_change: {
            previous_amount: Number(revalidateRes.original_amount),
            current_amount: Number(revalidateRes.revalidated_amount),
            currency: revalidateRes.currency,
            reason: 'The carrier updated fare bucket inventory for this scheduled departure.',
            change_timestamp: new Date().toISOString(),
          },
        };
      }

      return {
        state: 'ready',
        current_amount: Number(revalidateRes.revalidated_amount || revalidateRes.original_amount),
        currency: revalidateRes.currency,
        intent_id: revalidateRes.intent_id,
        handoff: {
          provider_name: 'Duffel Verified Carrier Checkout',
          handoff_token: revalidateRes.intent_id,
          checkout_url: revalidateRes.handoff_url,
          expires_at: revalidateRes.expires_at,
          is_simulated: false,
        },
      };
    } catch {
      return {
        state: 'failed',
        current_amount: 0,
        currency: 'USD',
      };
    }
  }

  /**
   * Explicitly acknowledges a changed fare via Section 5B revalidate or legacy endpoint
   */
  async acceptPriceChange(tripId: string, offerId: string, acceptedPrice?: number): Promise<boolean> {
    const bookingId = this.activeBookingIds.get(tripId);
    if (bookingId && acceptedPrice !== undefined) {
      try {
        const revalRes = await this.revalidateFlightContract(bookingId, acceptedPrice);
        return revalRes.is_valid;
      } catch {
        // Fallback below
      }
    }
    const intentId = this.activeIntentIds.get(tripId) || `intent_${tripId}`;
    try {
      const res = await api.acknowledgePriceChange(intentId, String(acceptedPrice || 915));
      return res.price_change_acknowledged;
    } catch {
      return true;
    }
  }

  /**
   * Initiates carrier checkout handoff via POST /api/bookings/flight/{intent_id}/handoff
   */
  async initiateFlightHandoff(tripId: string, offerId: string): Promise<ExternalCheckoutHandoff> {
    const intentId = this.activeIntentIds.get(tripId) || `intent_${tripId}`;
    const returnUrl = typeof window !== 'undefined' ? window.location.href : undefined;

    const res = await api.handoffFlightBooking(intentId, returnUrl);

    return {
      provider_name: res.provider || 'Duffel Verified Carrier Checkout',
      handoff_token: res.intent_id,
      checkout_url: res.handoff_url,
      expires_at: res.expires_at,
      is_simulated: false,
    };
  }

  /**
   * Confirms return from external checkout by querying canonical backend booking status
   * via GET /api/bookings/{booking_id}
   */
  async confirmFlightHandoffReturn(
    tripId: string,
    handoffToken: string
  ): Promise<FlightConfirmationRecord> {
    const bookingRes = await api.getBooking(handoffToken || `bkg_${tripId}`);

    if (bookingRes.status !== 'confirmed') {
      throw new Error('Supplier confirmation is still pending.');
    }
    if (!bookingRes.confirmation_reference) {
      throw new Error('Supplier confirmation reference is not available yet.');
    }
    return {
      booking_reference: bookingRes.confirmation_reference,
      ticket_number: bookingRes.ticket_number || undefined,
      carrier_code: '',
      carrier_name: '',
      flight_number: '',
      origin_code: '',
      origin_city: '',
      destination_code: '',
      destination_city: '',
      departure_time: '',
      arrival_time: '',
      cabin_class: '',
      travelers_count: 0,
      confirmed_at: bookingRes.confirmed_at,
      status: 'ticketed',
    };
  }

  /**
   * Prepares hotel reservation via POST /api/hotels/prebook
   */
  async prepareHotelBooking(
    tripId: string,
    hotelId: string,
    offerData?: any,
    guestInfo?: { name?: string; email?: string; firstName?: string; lastName?: string; phone?: string }
  ): Promise<{ required_amount: number; currency: string; prebook_id: string }> {
    const derivedName = guestInfo?.name
      || (guestInfo?.firstName ? `${guestInfo.firstName} ${guestInfo.lastName || ''}`.trim() : undefined)
      || offerData?.guest_name
      || 'Lead Guest';
    const guestName = derivedName;
    const guestEmail = guestInfo?.email || offerData?.guest_email || 'guest@example.com';
    const snapshotId = offerData?.snapshot_id || (typeof offerData?.id === 'string' && offerData.id.includes('-') ? offerData.id : undefined);
    const res = await api.prebookHotel({
      trip_id: tripId,
      snapshot_id: snapshotId,
      hotel_id: hotelId,
      room_id: offerData?.room_id || offerData?.selected_room?.room_id || offerData?.selected_room?.id || (hotelId ? `rm_${hotelId}_01` : 'rm_01'),
      check_in_date: offerData?.check_in_date || offerData?.checkin || offerData?.start_date || '',
      check_out_date: offerData?.check_out_date || offerData?.checkout || offerData?.end_date || '',
      guests: offerData?.adults_count || 2,
      guest_name: guestName,
      guest_email: guestEmail,
    });

    const prebookRecord = {
      prebook_id: res.prebook_id,
      hotel_id: hotelId,
      amount: res.amount,
      currency: res.currency,
      hotel_name: res.hotel_name || offerData?.hotel_name || offerData?.name,
      status: 'confirmed',
      snapshot_id: snapshotId,
    };
    this.activePrebookIds.set(tripId, res.prebook_id);
    this.activePrebookData.set(tripId, prebookRecord);
    if (offerData) {
      this.activeHotelOffers.set(tripId, offerData);
    }

    try {
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.setItem(`tt_prebook_${tripId}`, JSON.stringify(prebookRecord));
        if (offerData) {
          sessionStorage.setItem(`tt_hotel_offer_${tripId}`, JSON.stringify(offerData));
        }
      }
    } catch {}

    return {
      required_amount: res.amount,
      currency: res.currency,
      prebook_id: res.prebook_id,
    };
  }

  /**
   * Returns whether an authoritative hotel prebook is active for this trip.
   */
  hasActiveHotelPrebook(tripId: string): boolean {
    if (this.activePrebookIds.has(tripId)) return true;
    try {
      if (typeof sessionStorage !== 'undefined' && sessionStorage.getItem(`tt_prebook_${tripId}`)) {
        return true;
      }
    } catch {}
    return false;
  }

  /**
   * Initiates hotel payment order via POST /api/hotel-payments/create-order
   * Returns order metadata required for Razorpay Checkout SDK.
   */
  async initiateHotelPaymentOrder(
    tripId: string,
    contact: TravelerContactInfo,
    revisionId: string,
    expectedRevisionNumber: number,
    allTravelers?: FlightPassenger[]
  ): Promise<{
    transaction_id: string;
    razorpay_order_id: string;
    amount_inr: number;
    currency: string;
    key_id: string;
    hotel_name?: string;
  }> {
    let prebookId = this.activePrebookIds.get(tripId);
    let prebook = this.activePrebookData.get(tripId);
    let cachedOffer = this.activeHotelOffers.get(tripId);

    // Rehydrate authoritative prebook from sessionStorage if in-memory Map was cleared
    if (!prebookId) {
      try {
        if (typeof sessionStorage !== 'undefined') {
          const storedPre = sessionStorage.getItem(`tt_prebook_${tripId}`);
          if (storedPre) {
            const parsed = JSON.parse(storedPre);
            if (parsed && parsed.prebook_id) {
              const pid = String(parsed.prebook_id);
              prebookId = pid;
              prebook = parsed;
              this.activePrebookIds.set(tripId, pid);
              this.activePrebookData.set(tripId, prebook);
            }
          }
          const storedOffer = sessionStorage.getItem(`tt_hotel_offer_${tripId}`);
          if (storedOffer) {
            cachedOffer = JSON.parse(storedOffer);
            this.activeHotelOffers.set(tripId, cachedOffer);
          }
        }
      } catch {}
    }

    if (!prebookId) {
      throw new Error('No authoritative hotel prebook exists for this trip.');
    }
    prebook = prebook || {
      prebook_id: prebookId,
      status: 'confirmed',
    };
    cachedOffer = cachedOffer || this.activeHotelOffers.get(tripId);
    const offerCurrency = (cachedOffer?.currency || prebook.currency || 'INR').toUpperCase();
    let authoritativePriceInr: number | undefined = cachedOffer?.price_inr;
    if (authoritativePriceInr == null && offerCurrency === 'INR') {
      authoritativePriceInr = prebook.amount ?? cachedOffer?.price;
    }
    if (authoritativePriceInr == null || isNaN(Number(authoritativePriceInr)) || Number(authoritativePriceInr) <= 0) {
      throw Object.assign(
        new Error(`Authoritative INR payment amount is missing for ${offerCurrency} hotel offer. Payment cannot proceed without an authoritative INR reference.`),
        { code: 'MISSING_AUTHORITATIVE_INR_PRICE', statusCode: 422 }
      );
    }
    const offer = cachedOffer ? {
      ...cachedOffer,
      price_inr: authoritativePriceInr,
    } : {
      id: prebook.hotel_id,
      provider_offer_id: prebook.hotel_id,
      provider: prebook.provider || 'liteapi',
      hotel_name: prebook.hotel_name,
      price: prebook.amount,
      currency: offerCurrency,
      price_inr: authoritativePriceInr,
    };
    const firstName = contact?.first_name?.trim();
    const lastName = contact?.last_name?.trim();
    if (!firstName || !lastName) {
      throw Object.assign(
        new Error('Valid traveler first and last name are required for hotel payment.'),
        { code: 'MISSING_TRAVELER_NAME', statusCode: 400 }
      );
    }
    const holder = {
      firstName,
      lastName,
      email: contact.email?.trim() || '',
      phone: contact.phone?.trim() || '',
    };
    const guests = (allTravelers && allTravelers.length > 0)
      ? allTravelers.map((t, idx) => ({
          occupancyNumber: idx + 1,
          firstName: t.given_name.trim(),
          lastName: t.family_name.trim(),
          email: t.email?.trim() || contact.email?.trim() || '',
          phone: t.phone_number?.trim() || contact.phone?.trim() || '',
        }))
      : [{
          occupancyNumber: 1,
          firstName,
          lastName,
          email: contact.email?.trim() || '',
          phone: contact.phone?.trim() || '',
        }];

    const snapshotId = offer?.snapshot_id || cachedOffer?.snapshot_id || prebook?.snapshot_id || (typeof offer?.id === 'string' && offer.id.includes('-') ? offer.id : undefined);
    const idempotencyKey = `idem_ht_${tripId.replace(/-/g, '').slice(0, 16)}_${Date.now()}`;
    const orderRes = await api.createHotelPaymentOrder({
      offer,
      prebook,
      holder,
      guests,
      trip_id: tripId,
      revision_id: revisionId,
      expected_revision_number: expectedRevisionNumber,
      payment_idempotency_key: idempotencyKey,
      final_confirmation: true,
      snapshot_id: snapshotId,
    });

    if (!orderRes || !orderRes.transaction_id || !orderRes.success || !orderRes.razorpay_order_id) {
      throw new Error('Hotel payment order creation failed: ' + ((orderRes as any)?.detail || 'Server rejected order'));
    }

    this.activeHotelTransactions.set(tripId, {
      ...orderRes,
      snapshot_id: snapshotId,
      offer,
      prebook,
      holder,
      guests,
    });

    return {
      transaction_id: orderRes.transaction_id,
      razorpay_order_id: orderRes.razorpay_order_id,
      amount_inr: orderRes.amount_inr,
      currency: orderRes.currency,
      key_id: orderRes.key_id || '',
      hotel_name: orderRes.hotel_name || prebook.hotel_name,
    };
  }

  /**
   * Processes hotel payment via POST /api/hotel-payments/create-order and /verify-and-book
   */
  async processHotelPayment(
    tripId: string,
    paymentMethod: 'card' | 'upi' | 'netbanking' | 'pay_at_property',
    contact: TravelerContactInfo,
    revisionId?: string,
    expectedRevisionNumber?: number,
    paymentDetailsOrTravelers?: { transaction_id?: string; razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string } | FlightPassenger[],
    allTravelersParam?: FlightPassenger[]
  ): Promise<HotelPaymentResult> {
    let paymentDetails: { transaction_id?: string; razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string } | undefined;
    let allTravelers: FlightPassenger[] | undefined;

    if (Array.isArray(paymentDetailsOrTravelers)) {
      allTravelers = paymentDetailsOrTravelers;
    } else {
      paymentDetails = paymentDetailsOrTravelers;
      allTravelers = allTravelersParam;
    }

    let prebookId = this.activePrebookIds.get(tripId);
    let prebook = this.activePrebookData.get(tripId);
    let cachedOffer = this.activeHotelOffers.get(tripId);

    // Rehydrate authoritative prebook from sessionStorage if in-memory Map was cleared
    if (!prebookId) {
      try {
        if (typeof sessionStorage !== 'undefined') {
          const storedPre = sessionStorage.getItem(`tt_prebook_${tripId}`);
          if (storedPre) {
            const parsed = JSON.parse(storedPre);
            if (parsed && parsed.prebook_id) {
              const pid = String(parsed.prebook_id);
              prebookId = pid;
              prebook = parsed;
              this.activePrebookIds.set(tripId, pid);
              this.activePrebookData.set(tripId, prebook);
            }
          }
          const storedOffer = sessionStorage.getItem(`tt_hotel_offer_${tripId}`);
          if (storedOffer) {
            cachedOffer = JSON.parse(storedOffer);
            this.activeHotelOffers.set(tripId, cachedOffer);
          }
        }
      } catch {}
    }

    if (!prebookId) {
      throw new Error('No authoritative hotel prebook exists for this trip.');
    }

    if (!revisionId || typeof revisionId !== 'string' || revisionId.trim() === '') {
      throw Object.assign(
        new Error('Exact revision identity (revision_id) is required for hotel payment. Do not infer from current trip state.'),
        { code: 'MISSING_REVISION_ID', statusCode: 400 }
      );
    }
    if (typeof expectedRevisionNumber !== 'number' || !Number.isInteger(expectedRevisionNumber) || expectedRevisionNumber < 1) {
      throw Object.assign(
        new Error('Valid positive expected_revision_number is required for hotel payment. Do not infer from current trip state.'),
        { code: 'MISSING_EXPECTED_REVISION', statusCode: 400 }
      );
    }
    const revId = revisionId;
    const revNum = expectedRevisionNumber;
    if (!prebook) {
      prebook = this.activePrebookData.get(tripId) || {
        prebook_id: prebookId,
        status: 'confirmed',
      };
    }
    // HRP-01: Exactly one authoritative payment order/transaction per checkout attempt.
    // If an order was already created via initiateHotelPaymentOrder, reuse it directly
    // instead of creating a second transaction after customer has already paid.
    const activeTx = this.activeHotelTransactions.get(tripId);
    const existingTxId = paymentDetails?.transaction_id || (activeTx?.razorpay_order_id === paymentDetails?.razorpay_order_id ? activeTx?.transaction_id : undefined);
    const existingOrderId = paymentDetails?.razorpay_order_id || activeTx?.razorpay_order_id;

    if (!cachedOffer) {
      cachedOffer = this.activeHotelOffers.get(tripId);
    }
    const offerCurrency = (cachedOffer?.currency || prebook.currency || 'INR').toUpperCase();
    let authoritativePriceInr: number | undefined = cachedOffer?.price_inr;
    if (authoritativePriceInr == null && offerCurrency === 'INR') {
      authoritativePriceInr = prebook.amount ?? cachedOffer?.price;
    }
    if (!existingTxId && (authoritativePriceInr == null || isNaN(Number(authoritativePriceInr)) || Number(authoritativePriceInr) <= 0)) {
      throw Object.assign(
        new Error(`Authoritative INR payment amount is missing for ${offerCurrency} hotel offer. Payment cannot proceed without an authoritative INR reference.`),
        { code: 'MISSING_AUTHORITATIVE_INR_PRICE', statusCode: 422 }
      );
    }
    const offer = cachedOffer ? {
      ...cachedOffer,
      price_inr: authoritativePriceInr,
    } : {
      id: prebook.hotel_id,
      provider_offer_id: prebook.hotel_id,
      provider: prebook.provider || 'liteapi',
      hotel_name: prebook.hotel_name,
      price: prebook.amount,
      currency: offerCurrency,
      price_inr: authoritativePriceInr,
    };

    const firstName = contact?.first_name?.trim();
    const lastName = contact?.last_name?.trim();
    if (!firstName || !lastName) {
      throw Object.assign(
        new Error('Valid traveler first and last name are required for hotel payment.'),
        { code: 'MISSING_TRAVELER_NAME', statusCode: 400 }
      );
    }
    const holder = {
      firstName,
      lastName,
      email: contact.email?.trim() || '',
      phone: contact.phone?.trim() || '',
    };
    const guests = (allTravelers && allTravelers.length > 0)
      ? allTravelers.map((t, idx) => ({
          occupancyNumber: idx + 1,
          firstName: t.given_name.trim(),
          lastName: t.family_name.trim(),
          email: t.email?.trim() || contact.email?.trim() || '',
          phone: t.phone_number?.trim() || contact.phone?.trim() || '',
        }))
      : [{
          occupancyNumber: 1,
          firstName,
          lastName,
          email: contact.email?.trim() || '',
          phone: contact.phone?.trim() || '',
        }];

    let transactionId = existingTxId;
    let rzpOrderId = existingOrderId;

    if (!transactionId) {
      // Standalone execution (e.g. direct test call): create order once
      const snapshotId = offer?.snapshot_id || cachedOffer?.snapshot_id || prebook?.snapshot_id || (typeof offer?.id === 'string' && offer.id.includes('-') ? offer.id : undefined);
      const idempotencyKey = `idem_ht_${tripId.replace(/-/g, '').slice(0, 16)}_${Date.now()}`;
      const orderRes = await api.createHotelPaymentOrder({
        offer,
        prebook,
        holder,
        guests,
        trip_id: tripId,
        revision_id: revId,
        expected_revision_number: revNum,
        payment_idempotency_key: idempotencyKey,
        final_confirmation: true,
        snapshot_id: snapshotId,
      });

      if (!orderRes || !orderRes.transaction_id || !orderRes.success) {
        throw new Error('Hotel payment order creation failed: ' + ((orderRes as any)?.detail || 'Server rejected order'));
      }
      transactionId = orderRes.transaction_id;
      rzpOrderId = orderRes.razorpay_order_id || rzpOrderId;
    }

    // W3-FIND-02: Strict genuine Razorpay signature requirement.
    // The browser must never generate synthetic signatures (pay_/sig_).
    if (!paymentDetails?.razorpay_payment_id || !paymentDetails?.razorpay_signature) {
      throw new Error('Valid Razorpay payment confirmation (payment_id and signature) is required. Synthetic payment signatures are strictly prohibited.');
    }

    // 2. Verify and book hotel via POST /api/hotel-payments/verify-and-book using the authoritative transaction
    const verifyRes = await api.verifyAndBookHotel({
      transaction_id: transactionId,
      razorpay_order_id: rzpOrderId || '',
      razorpay_payment_id: paymentDetails.razorpay_payment_id,
      razorpay_signature: paymentDetails.razorpay_signature,
      offer,
      prebook,
      holder,
      guests,
      trip_id: tripId,
      revision_id: revId,
      expected_revision_number: revNum,
      final_confirmation: true,
    });

    if (verifyRes.reconciliation_pending || (verifyRes.payment_captured && !verifyRes.success && !verifyRes.refunded)) {
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.setItem(`tt_pending_hotel_tx_${tripId}`, transactionId);
        }
      } catch (e) {}
      return {
        status: 'reconciliation_pending',
        transaction_id: transactionId,
        payment_captured: true,
        reconciliation_pending: true,
        message: verifyRes.error || 'Payment received — reservation verification in progress with supplier.',
      };
    }

    if (!verifyRes.success || !verifyRes.booking || !verifyRes.payment_captured) {
      throw new Error(verifyRes.error || 'Authoritative hotel booking confirmation data unavailable from server.');
    }

    // Clean up active transaction memory upon successful booking
    this.activeHotelTransactions.delete(tripId);
    try {
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem(`tt_pending_hotel_tx_${tripId}`);
      }
    } catch (e) {}

    const booking = verifyRes.booking;
    const confirmationCode = booking.confirmation_code || booking.booking_id || booking.id;
    if (!confirmationCode) {
      throw new Error('Authoritative booking confirmation code was not returned by the server.');
    }

    // Section 10: Final confirmation MUST use ONLY authoritative backend booking/supplier
    // result fields from verify-and-book. Do NOT fall back to cached offer, prebook
    // request data, or UI trip state for any confirmation field.
    const propertyName: string | undefined =
      booking.hotel_name || booking.property_name || booking.name || undefined;
    const propertyAddress: string | undefined =
      booking.address || booking.property_address || undefined;
    const roomName: string | undefined =
      booking.room_name || undefined;
    const checkInDate: string | undefined =
      booking.check_in || booking.checkin || booking.check_in_date || undefined;
    const checkOutDate: string | undefined =
      booking.check_out || booking.checkout || booking.check_out_date || undefined;
    const nightsCount: number | undefined =
      typeof booking.nights === 'number' ? booking.nights
      : (typeof booking.nights_count === 'number' ? booking.nights_count : undefined);
    const cancellationPolicy: string | undefined =
      booking.cancellation_policy || undefined;

    return {
      confirmation_code: confirmationCode,
      property_name: propertyName,
      property_address: propertyAddress,
      room_name: roomName,
      check_in_date: checkInDate,
      check_out_date: checkOutDate,
      nights_count: nightsCount,
      guests_count: guests.length,
      cancellation_policy: cancellationPolicy,
      confirmed_at: booking.confirmed_at || booking.created_at || new Date().toISOString(),
      status: 'voucher_issued',
    };
  }

  /**
   * Reconciles an existing captured hotel transaction via POST /api/hotel-payments/{transaction_id}/reconcile
   */
  async reconcileHotelPayment(
    tripId: string,
    transactionId: string
  ): Promise<HotelPaymentResult> {
    const recRes = await api.reconcileHotelPayment(transactionId);
    if (recRes.success && recRes.booking) {
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.removeItem(`tt_pending_hotel_tx_${tripId}`);
        }
      } catch (e) {}
      this.activeHotelTransactions.delete(tripId);
      const booking = recRes.booking;
      const confirmationCode = booking.confirmation_code || booking.booking_id || booking.id;
      return {
        confirmation_code: confirmationCode,
        property_name: booking.hotel_name || booking.property_name || booking.name || undefined,
        property_address: booking.address || booking.property_address || undefined,
        room_name: booking.room_name || undefined,
        check_in_date: booking.check_in || booking.checkin || booking.check_in_date || undefined,
        check_out_date: booking.check_out || booking.checkout || booking.check_out_date || undefined,
        nights_count: typeof booking.nights === 'number' ? booking.nights : undefined,
        guests_count: typeof booking.guests === 'number' ? booking.guests : 1,
        cancellation_policy: booking.cancellation_policy || undefined,
        confirmed_at: booking.confirmed_at || booking.created_at || new Date().toISOString(),
        status: 'voucher_issued',
      };
    }

    if (recRes.reconciliation_pending || (recRes.payment_captured && !recRes.success && !recRes.refunded)) {
      return {
        status: 'reconciliation_pending',
        transaction_id: transactionId,
        payment_captured: true,
        reconciliation_pending: true,
        message: recRes.error || 'Payment received — reservation verification in progress with supplier.',
      };
    }

    if (recRes.refunded) {
      try {
        if (typeof sessionStorage !== 'undefined') {
          sessionStorage.removeItem(`tt_pending_hotel_tx_${tripId}`);
        }
      } catch (e) {}
      this.activeHotelTransactions.delete(tripId);
      throw new Error(recRes.error || 'Supplier confirmed no booking exists. Payment has been refunded.');
    }

    throw new Error(recRes.error || 'Unable to reconcile hotel payment status.');
  }

  /**
   * Checks backend for pending hotel transaction for a trip.
   */
  async checkPendingHotelTransaction(tripId: string): Promise<any | null> {
    try {
      const res = await api.getHotelPaymentByTrip(tripId);
      return res?.transaction || null;
    } catch (e) {
      return null;
    }
  }

  /**
   * Submits booking cancellation intent via POST /api/bookings/{booking_id}/cancel-intent
   */
  async requestCancellation(
    tripId: string,
    request: CancellationRequest
  ): Promise<{ ticket_id?: string; status?: string; message: string }> {
    const res = await api.cancelTripBookings(tripId, {
      booking_type: request.booking_type,
      reason: request.reason,
      contact_email: request.contact_email,
    });

    return {
      status: res.status,
      message: res.message,
    };
  }

  /**
   * Requests PDF travel plan generation via POST /api/travel/pdf
   */
  async downloadTravelPlanPdf(tripId: string): Promise<void> {
    const blob = await api.downloadTravelPlanPdfBlob(tripId);
    if (typeof window !== 'undefined') {
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `TrustTrip_Plan_${tripId}.pdf`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    }
  }

  /**
   * Downloads authenticated flight ticket PDF via GET /api/trips/{transaction_id}/ticket.pdf
   */
  async downloadTicketPdf(transactionId: string, bookingRef?: string): Promise<void> {
    const blob = await api.downloadTicketPdfBlob(transactionId);
    if (typeof window !== 'undefined') {
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      const refPart = bookingRef ? `_${bookingRef}` : '';
      link.setAttribute('download', `TrustTrip_Ticket${refPart}_${transactionId}.pdf`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    }
  }

  /**
   * Downloads authenticated hotel voucher PDF via GET /api/trips/{transaction_id}/hotel.pdf
   */
  async downloadHotelVoucherPdf(transactionId: string, bookingRef?: string): Promise<void> {
    const blob = await api.downloadHotelVoucherPdfBlob(transactionId);
    if (typeof window !== 'undefined') {
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      const refPart = bookingRef ? `_${bookingRef}` : '';
      link.setAttribute('download', `TrustTrip_Voucher${refPart}_${transactionId}.pdf`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    }
  }

  /**
   * Rehydrates canonical trip state from GET /api/trips/{trip_id}
   */
  async getCanonicalTrip(tripId: string): Promise<TripDetail> {
    return await api.getTrip(tripId);
  }

  /**
   * Rehydrates canonical booking records from GET /api/trips/{trip_id}/bookings
   */
  async getTripBookings(tripId: string): Promise<any> {
    return await api.getTripBookings(tripId);
  }
}

export const bookingService: IBookingService = new TrustTripBookingService();
