/**
 * TrustTrip - Part 5A Centralized Mock Data & Service Layer
 * 
 * Contains realistic domain fixtures and simulated service adapters for:
 * - Journey Plan Approval
 * - Flight Verification & Duffel Handoff Simulation
 * - Hotel Prebooking & Razorpay Payment Simulation
 * - Confirmation Records and Travel Document Downloads
 * 
 * INTEGRATION NOTE FOR GITHUB COPILOT:
 * When wiring up real backend services, replace `mockBookingService`
 * with `RealTrustTripBookingService` adhering to `IBookingService`.
 */

import {
  IBookingService,
  FlightVerificationState,
  FlightPriceChangeNotice,
  ExternalCheckoutHandoff,
  FlightConfirmationRecord,
  HotelConfirmationRecord,
  TripConfirmationRecord,
  TravelerContactInfo,
  CancellationRequest,
} from '../types/bookingFrontend';

const STORAGE_PREFIX = 'trusttrip_booking_state_';

export interface TripBookingStateStore {
  isApproved: boolean;
  approvedAt?: string;
  flightState: FlightVerificationState;
  flightConfirmation?: FlightConfirmationRecord;
  hotelState: 'idle' | 'preparing' | 'payment_required' | 'confirmed';
  hotelConfirmation?: HotelConfirmationRecord;
  hasPriceChanged: boolean;
  isFlightUnavailable: boolean;
}

export const DEFAULT_TRAVELER_CONTACT: TravelerContactInfo = {
  first_name: 'Ananya',
  last_name: 'Roy',
  email: 'ananya.roy@example.travel',
  phone: '+91 98301 23456',
  special_requests: 'Quiet room away from elevator, vegetarian meal on long-haul sectors.',
};

/**
 * Generates realistic flight confirmation vouchers
 */
export function generateMockFlightConfirmation(
  tripId: string,
  origin = 'Kolkata (CCU)',
  destination = 'Paris (CDG)'
): FlightConfirmationRecord {
  const refCode = `TT-FL-${tripId.slice(-4).toUpperCase() || 'CONFIRMED'}`;
  const ticketNum = undefined;

  return {
    booking_reference: refCode,
    ticket_number: ticketNum,
    carrier_code: 'AF',
    carrier_name: 'Air France',
    flight_number: 'AF-257',
    origin_code: origin.includes('CCU') ? 'CCU' : 'SFO',
    origin_city: origin.split('(')[0].trim() || 'Kolkata',
    destination_code: destination.includes('CDG') ? 'CDG' : 'KIX',
    destination_city: destination.split('(')[0].trim() || 'Paris',
    departure_time: '2026-10-12T09:45:00',
    arrival_time: '2026-10-12T17:30:00',
    cabin_class: 'Economy Premium',
    travelers_count: 2,
    confirmed_at: new Date().toISOString(),
    status: 'ticketed',
  };
}

/**
 * Generates realistic hotel confirmation vouchers
 */
export function generateMockHotelConfirmation(
  tripId: string,
  hotelName = 'Hotel Lutetia Left Bank',
  destination = 'Paris, France'
): HotelConfirmationRecord {
  const code = `TT-HT-${Math.floor(1000 + Math.random() * 9000)}`;

  return {
    confirmation_code: code,
    property_name: hotelName,
    property_address: '45 Boulevard Raspail, 6th Arrondissement, 75006 Paris',
    room_name: 'Deluxe Heritage King with Courtyard Garden View',
    check_in_date: '2026-10-12',
    check_out_date: '2026-10-18',
    nights_count: 6,
    guests_count: 2,
    cancellation_policy: 'Complimentary cancellation until 48 hours prior to check-in (10 Oct 2026, 14:00 CET)',
    confirmed_at: new Date().toISOString(),
    status: 'voucher_issued',
  };
}

/**
 * Local State Persistence for interactive testing
 */
export function getStoredBookingState(tripId: string): TripBookingStateStore {
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${tripId}`);
    if (raw) return JSON.parse(raw);
  } catch {}
  return {
    isApproved: false,
    flightState: 'idle',
    hotelState: 'idle',
    hasPriceChanged: false,
    isFlightUnavailable: false,
  };
}

export function saveStoredBookingState(tripId: string, state: TripBookingStateStore): void {
  try {
    localStorage.setItem(`${STORAGE_PREFIX}${tripId}`, JSON.stringify(state));
  } catch {}
}

export function clearStoredBookingState(tripId: string): void {
  try {
    localStorage.removeItem(`${STORAGE_PREFIX}${tripId}`);
  } catch {}
}

/**
 * Centralized Mock Booking Service Adapter.
 * Implements IBookingService with realistic async simulated delays.
 */
class MockBookingService implements IBookingService {
  private delay(ms = 800): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async approvePlan(
    tripId: string,
    expectedRevision: number,
    revisionId: string
  ): Promise<{ approved: boolean; approved_at: string; active_revision_number?: number }> {
    // Same local pre-flight validation as production service — no fallback allowed
    if (!revisionId || typeof revisionId !== 'string' || revisionId.trim() === '') {
      throw Object.assign(
        new Error('approvePlan requires an exact revisionId.'),
        { code: 'MISSING_REVISION_ID', statusCode: 400 }
      );
    }
    if (typeof expectedRevision !== 'number' || !Number.isInteger(expectedRevision) || expectedRevision < 1) {
      throw Object.assign(
        new Error('approvePlan requires a valid positive expectedRevision number.'),
        { code: 'MISSING_EXPECTED_REVISION', statusCode: 400 }
      );
    }
    await this.delay(650);
    const state = getStoredBookingState(tripId);
    const approvedAt = new Date().toISOString();
    state.isApproved = true;
    state.approvedAt = approvedAt;
    saveStoredBookingState(tripId, state);
    return { approved: true, approved_at: approvedAt, active_revision_number: expectedRevision + 1 };
  }

  async authorizeBooking(
    tripId: string,
    expectedRevision: number,
    revisionId: string,
    _notes?: string
  ): Promise<{ authorized: boolean; authorized_at: string; authorization_id: string; revision_number: number }> {
    if (!revisionId || typeof revisionId !== 'string' || revisionId.trim() === '') {
      throw Object.assign(
        new Error('authorizeBooking requires an exact revisionId.'),
        { code: 'MISSING_REVISION_ID', statusCode: 400 }
      );
    }
    if (typeof expectedRevision !== 'number' || !Number.isInteger(expectedRevision) || expectedRevision < 1) {
      throw Object.assign(
        new Error('authorizeBooking requires a valid positive expectedRevision number.'),
        { code: 'MISSING_EXPECTED_REVISION', statusCode: 400 }
      );
    }
    await this.delay(500);
    const authId = `mock_auth_${Date.now().toString(36)}`;
    const authorizedAt = new Date().toISOString();
    return {
      authorized: true,
      authorized_at: authorizedAt,
      authorization_id: authId,
      revision_number: expectedRevision,
    };
  }

  async verifyFlight(tripId: string, offerId: string): Promise<{
    state: FlightVerificationState;
    current_amount: number;
    currency: string;
    price_change?: FlightPriceChangeNotice;
    handoff?: ExternalCheckoutHandoff;
  }> {
    await this.delay(1100);
    const state = getStoredBookingState(tripId);

    // If simulating flight unavailable
    if (state.isFlightUnavailable) {
      state.flightState = 'unavailable';
      saveStoredBookingState(tripId, state);
      return {
        state: 'unavailable',
        current_amount: 860,
        currency: 'USD',
      };
    }

    // If simulating price change
    if (state.hasPriceChanged) {
      state.flightState = 'price_changed';
      saveStoredBookingState(tripId, state);
      return {
        state: 'price_changed',
        current_amount: 915,
        currency: 'USD',
        price_change: {
          previous_amount: 860,
          current_amount: 915,
          currency: 'USD',
          reason: 'The fare class booked previously is no longer available; modern inventory revalidated with carrier.',
          change_timestamp: new Date().toISOString(),
        },
      };
    }

    // Default: ready for secure checkout
    state.flightState = 'ready';
    saveStoredBookingState(tripId, state);

    return {
      state: 'ready',
      current_amount: 860,
      currency: 'USD',
      handoff: {
        provider_name: 'Duffel Secure Partner Handoff',
        handoff_token: `duffel_link_${Date.now()}_${tripId.slice(0, 8)}`,
        expires_at: new Date(Date.now() + 20 * 60 * 1000).toISOString(),
        is_simulated: true,
      },
    };
  }

  async acceptPriceChange(tripId: string, offerId: string): Promise<boolean> {
    await this.delay(500);
    const state = getStoredBookingState(tripId);
    state.hasPriceChanged = false;
    state.flightState = 'ready';
    saveStoredBookingState(tripId, state);
    return true;
  }

  async initiateFlightHandoff(tripId: string, offerId: string): Promise<ExternalCheckoutHandoff> {
    await this.delay(600);
    const state = getStoredBookingState(tripId);
    state.flightState = 'handed_off';
    saveStoredBookingState(tripId, state);

    return {
      provider_name: 'Duffel Verified Carrier Checkout',
      handoff_token: `tk_${Date.now().toString(36)}`,
      checkout_url: 'https://checkout.duffel.com/simulated-handoff-session',
      expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
      is_simulated: true,
    };
  }

  async confirmFlightHandoffReturn(tripId: string, handoffToken: string): Promise<FlightConfirmationRecord> {
    await this.delay(1200);
    const state = getStoredBookingState(tripId);
    const confirmation = generateMockFlightConfirmation(tripId);
    state.flightState = 'confirmed';
    state.flightConfirmation = confirmation;
    saveStoredBookingState(tripId, state);
    return confirmation;
  }

  async prepareHotelBooking(tripId: string, hotelId: string): Promise<{ required_amount: number; currency: string }> {
    await this.delay(600);
    const state = getStoredBookingState(tripId);
    state.hotelState = 'payment_required';
    saveStoredBookingState(tripId, state);
    return { required_amount: 720, currency: 'USD' };
  }

  async processHotelPayment(
    tripId: string,
    paymentMethod: 'card' | 'upi' | 'netbanking' | 'pay_at_property',
    contact: TravelerContactInfo
  ): Promise<HotelConfirmationRecord> {
    await this.delay(1500);
    const state = getStoredBookingState(tripId);
    const confirmation = generateMockHotelConfirmation(tripId);
    state.hotelState = 'confirmed';
    state.hotelConfirmation = confirmation;
    saveStoredBookingState(tripId, state);
    return confirmation;
  }

  async requestCancellation(tripId: string, request: CancellationRequest): Promise<{ ticket_id: string; message: string }> {
    await this.delay(700);
    const ticketId = `CN-${Math.floor(100000 + Math.random() * 900000)}`;
    return {
      ticket_id: ticketId,
      message: `Your cancellation request for ${request.booking_type} has been submitted. Our concierge team will review partner terms and contact ${request.contact_email} within 4 hours.`,
    };
  }

  async downloadTravelPlanPdf(tripId: string): Promise<void> {
    await this.delay(600);
    // Frontend callback boundary: later Copilot connects to POST /api/travel/pdf
    // In mock mode, triggers an informational browser notification / print dialog trigger
    return;
  }

  // Section 5B / 5C Mocks
  private mockActiveBookingIds: Map<string, string> = new Map();

  async selectFlightOffer(tripId: string, offerId: string, _passengers: any[]): Promise<any> {
    await this.delay(300);
    const bId = `bkg_${tripId}_${Date.now().toString(36)}`;
    this.mockActiveBookingIds.set(tripId, bId);
    return {
      success: true,
      booking_id: bId,
      trip_id: tripId,
      offer_id: offerId,
      status: 'SELECTED',
      price: 860,
      currency: 'USD',
      human_approval_required: true,
      approved: false,
    };
  }

  async revalidateFlightContract(bookingId: string, acceptedPrice?: number): Promise<any> {
    await this.delay(400);
    return {
      success: true,
      booking_id: bookingId,
      status: 'READY_FOR_BOOKING',
      is_valid: true,
      price_changed: false,
      original_price: acceptedPrice || 860,
      current_price: acceptedPrice || 860,
      currency: 'USD',
      human_approval_required: true,
      approved: false,
    };
  }

  async approveFlightBooking(bookingId: string, _notes?: string): Promise<any> {
    await this.delay(300);
    return {
      success: true,
      booking_id: bookingId,
      status: 'READY_FOR_BOOKING',
      approved: true,
      approved_at: new Date().toISOString(),
      message: 'Human approval recorded.',
    };
  }

  async confirmFlightBooking(bookingId: string): Promise<any> {
    await this.delay(600);
    return {
      success: true,
      booked: true,
      booking_id: bookingId,
      status: 'COMPLETED',
      booking_reference: `TT-FL-${bookingId.slice(-4).toUpperCase() || 'MOCK'}`,
      pnr: undefined,
      e_ticket: undefined,
      price: 860,
      currency: 'USD',
    };
  }

  async getFlightBooking(bookingId: string): Promise<any> {
    await this.delay(200);
    return {
      booking_id: bookingId,
      trip_id: 'trip_1',
      offer_id: 'offer_1',
      status: 'COMPLETED',
      expected_amount: 860,
      currency: 'USD',
      approved: true,
      passenger_count: 1,
      booking_reference: 'TT-FL-9988',
      created_at: new Date().toISOString(),
    };
  }

  getActiveBookingId(tripId: string): string | undefined {
    return this.mockActiveBookingIds.get(tripId);
  }

  setActiveBookingId(tripId: string, bookingId: string): void {
    this.mockActiveBookingIds.set(tripId, bookingId);
  }
}

export const bookingService: IBookingService = new MockBookingService();
