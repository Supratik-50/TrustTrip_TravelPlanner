/**
 * TrustTrip - Part 5A Frontend Booking Types & Service Contracts
 * 
 * Clean frontend-only domain models and integration boundaries for:
 * - Journey Review & Approval
 * - Flight Verification & External Checkout Handoff
 * - Hotel Prebooking & Payment Simulation
 * - Partial & Complete Booking States
 * - Final Journey Confirmation & Travel Documents
 */

import { FlightPassenger } from './flight';
import { TripDetail } from './trip';

export type JourneyApprovalStatus = 'pending_review' | 'approved' | 'rejected';

export type FlightVerificationState =
  | 'idle'
  | 'available'
  | 'empty'
  | 'verifying'
  | 'ready'
  | 'price_changed'
  | 'stale'
  | 'unavailable'
  | 'expired'
  | 'awaiting_approval'
  | 'booking_in_progress'
  | 'handed_off'
  | 'confirmed'
  | 'completed'
  | 'failed';

export type HotelBookingState =
  | 'idle'
  | 'reviewing'
  | 'preparing'
  | 'payment_required'
  | 'payment_processing'
  | 'payment_success'
  | 'payment_failed'
  | 'booking_in_progress'
  | 'confirmed';

export type OverallBookingStatus =
  | 'planning'
  | 'plan_approved'
  | 'flight_in_progress'
  | 'flight_confirmed_hotel_pending'
  | 'hotel_confirmed_flight_pending'
  | 'both_confirmed'
  | 'cancelled';

export interface TravelerContactInfo {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  special_requests?: string;
}

export interface BookingPriceSummary {
  currency: string;
  flight_base: number;
  hotel_base: number;
  taxes_and_fees: number;
  tourism_cess?: number;
  total_amount: number;
  savings_amount?: number;
}

export interface FlightPriceChangeNotice {
  previous_amount: number;
  current_amount: number;
  currency: string;
  reason: string;
  change_timestamp: string;
}

export interface ExternalCheckoutHandoff {
  provider_name: string;
  handoff_token: string;
  checkout_url?: string;
  expires_at: string;
  is_simulated: boolean;
}

export interface FlightConfirmationRecord {
  booking_id?: string;
  booking_reference: string;
  ticket_number?: string;
  carrier_code: string;
  carrier_name: string;
  flight_number: string;
  origin_code: string;
  origin_city: string;
  destination_code: string;
  destination_city: string;
  departure_time: string;
  arrival_time: string;
  cabin_class: string;
  travelers_count: number;
  confirmed_at: string;
  status: 'confirmed' | 'ticketed';
}

export interface HotelConfirmationRecord {
  transaction_id?: string;
  confirmation_code: string;
  property_name?: string;
  property_address?: string;
  hotel_name?: string;
  room_name?: string;
  check_in_date?: string;
  check_out_date?: string;
  check_in?: string;
  check_out?: string;
  total_price?: number;
  currency?: string;
  nights_count?: number;
  guests_count?: number;
  cancellation_policy?: string;
  confirmed_at: string;
  status: 'confirmed' | 'voucher_issued';
}

export interface TripConfirmationRecord {
  trip_id: string;
  confirmation_id: string;
  destination: string;
  start_date: string;
  end_date: string;
  travelers_count: number;
  created_at: string;
  flight?: FlightConfirmationRecord;
  hotel?: HotelConfirmationRecord;
  total_paid: number;
  currency: string;
  notes?: string;
}

export interface CancellationRequest {
  booking_type: 'all' | 'flight' | 'hotel';
  reason: string;
  contact_email: string;
  acknowledged_terms: boolean;
}

/**
 * Clean Frontend Service Adapter Boundary.
 * In production, GitHub Copilot will replace the Mock implementation
 * of these methods with live backend API adapters without touching the UI.
 */
export interface IBookingService {
  approvePlan(tripId: string, expectedRevision: number, revisionId: string): Promise<{ approved: boolean; approved_at: string; active_revision_number?: number }>;
  authorizeBooking?(tripId: string, expectedRevision: number, revisionId: string, notes?: string): Promise<{ authorized: boolean; authorized_at: string; authorization_id: string; revision_number: number }>;
  verifyFlight(tripId: string, offerId: string, passengers?: FlightPassenger[]): Promise<{
    state: FlightVerificationState;
    current_amount: number;
    currency: string;
    price_change?: FlightPriceChangeNotice;
    handoff?: ExternalCheckoutHandoff;
  }>;
  acceptPriceChange(tripId: string, offerId: string): Promise<boolean>;
  initiateFlightHandoff(tripId: string, offerId: string): Promise<ExternalCheckoutHandoff>;
  confirmFlightHandoffReturn(tripId: string, handoffToken: string): Promise<FlightConfirmationRecord>;
  prepareHotelBooking(tripId: string, hotelId: string, offerData?: any, guestInfo?: { name?: string; email?: string; firstName?: string; lastName?: string; phone?: string }): Promise<{ required_amount: number; currency: string; prebook_id?: string }>;
  initiateHotelPaymentOrder?(
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
  }>;
  processHotelPayment(
    tripId: string,
    paymentMethod: 'card' | 'upi' | 'netbanking' | 'pay_at_property',
    contact: TravelerContactInfo,
    revisionId?: string,
    expectedRevisionNumber?: number,
    paymentDetailsOrTravelers?: { razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string } | FlightPassenger[],
    allTravelers?: FlightPassenger[]
  ): Promise<HotelConfirmationRecord>;
  requestCancellation(tripId: string, request: CancellationRequest): Promise<{ ticket_id?: string; status?: string; message: string }>;
  downloadTravelPlanPdf(tripId: string): Promise<void>;
  downloadTicketPdf(transactionId: string, bookingRef?: string): Promise<void>;
  downloadHotelVoucherPdf(transactionId: string, bookingRef?: string): Promise<void>;

  // Section 5B / 5C Authoritative Contracts
  selectFlightOffer(tripId: string, offerId: string, passengers: FlightPassenger[]): Promise<any>;
  revalidateFlightContract(bookingId: string, acceptedPrice?: number): Promise<any>;
  approveFlightBooking(bookingId: string, notes?: string): Promise<any>;
  confirmFlightBooking(bookingId: string): Promise<any>;
  getFlightBooking(bookingId: string): Promise<any>;
  getActiveBookingId(tripId: string): string | undefined;
  setActiveBookingId(tripId: string, bookingId: string): void;
  getTripBookings(tripId: string): Promise<any>;
  getCanonicalTrip?(tripId: string): Promise<TripDetail>;
}
