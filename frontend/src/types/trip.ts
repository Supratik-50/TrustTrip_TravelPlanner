import { FlightOffer } from './flight';
import { HotelOffer } from './hotel';

export interface TripLocation {
  city: string;
  country?: string;
  airport_code?: string;
}

export interface TripDates {
  start_date: string;
  end_date: string;
}

export interface TripConstraints {
  budget?: number;
  currency?: string;
  travelers_count?: number;
  flight_class?: 'economy' | 'premium_economy' | 'business' | 'first';
  hotel_stars?: number;
  preferences?: string[];
  dietary_restrictions?: string[];
}

export interface CreateTripRequest {
  origin: string;
  destination: string;
  start_date: string;
  end_date: string;
  travelers_count?: number;
  budget?: number;
  currency?: string;
  preferences?: string[];
  flight_class?: 'economy' | 'premium_economy' | 'business' | 'first';
  hotel_stars?: number;
  notes?: string;
}

export interface TripSummary {
  id?: string;
  trip_id: string;
  title: string;
  origin: string;
  destination: string;
  start_date: string;
  end_date: string;
  status: 'draft' | 'planning' | 'awaiting_approval' | 'approved' | 'booked' | 'cancelled';
  active_revision_number?: number;
  active_revision_id?: string;
  current_revision_number?: number;
  current_revision_id?: string;
  is_booking_authorized?: boolean;
  booking_authorization?: BookingAuthorizationInfo | null;
  thread_id?: string;
  travelers_count?: number;
  budget?: number;
  currency?: string;
  preferences?: string[];
  metadata?: Record<string, any>;
  flight_count?: number;
  hotel_count?: number;
  flights?: any[];
  hotels?: any[];
  created_at: string;
  updated_at: string;
}

export interface BookingAuthorizationInfo {
  authorization_id: string;
  trip_id: string;
  revision_id: string;
  revision_number: number;
  authorized_at: string;
  metadata?: Record<string, any>;
}

export interface BookingEligibilityInfo {
  is_eligible: boolean;
  trip_id: string;
  revision_id?: string;
  revision_number?: number;
  is_approved: boolean;
  is_rejected: boolean;
  is_authorized: boolean;
  authorized_at?: string;
  reasons: string[];
}

export interface DiffItem {
  field: string;
  label?: string;
  old_value?: any;
  new_value?: any;
  value?: any;
  description?: string;
}

export interface StructuredDiff {
  added: DiffItem[];
  removed: DiffItem[];
  changed: DiffItem[];
}

export interface TripRevision {
  revision_id: string;
  revision_number: number;
  trip_id: string;
  parent_revision_id?: string | null;
  parent_id?: string | null;
  status?: 'PROPOSED' | 'APPROVED' | 'REJECTED';
  created_at: string;
  change_summary: string;
  is_approved: boolean;
  is_rejected?: boolean;
  is_booking_authorized?: boolean;
  booking_authorization?: BookingAuthorizationInfo | null;
  approved_at?: string;
  rejected_at?: string;
  rejection_reason?: string;
  decision_metadata?: Record<string, any>;
  origin: string;
  destination: string;
  start_date: string;
  end_date: string;
  travelers_count?: number;
  budget?: number;
  currency?: string;
  preferences?: string[];
  itinerary?: Record<string, unknown>;
  flight_snapshot_id?: string;
  hotel_snapshot_id?: string;
  changes?: TripChangeSet;
  structured_diff?: StructuredDiff;
  previous_revision_id?: string;
  stale_warnings?: string[];
}

export interface TripChangeSet {
  origin?: string;
  destination?: string;
  start_date?: string;
  end_date?: string;
  passengers?: number;
  travelers_count?: number;
  budget?: number;
  currency?: string;
  preferences?: string[];
  flight_constraints?: Record<string, unknown>;
  hotel_constraints?: Record<string, unknown>;
  feedback?: string;
  expected_base_revision?: number;
  expected_base_revision_id?: string;
}

export interface CreateRevisionRequest {
  expected_base_revision?: number;
  expected_base_revision_id?: string;
  changes?: TripChangeSet;
  change_summary?: string;
  updated_constraints?: Record<string, any>;
  feedback?: string;
}

export interface TripOfferSelection {
  offer_type: 'flight' | 'hotel';
  offer_id: string;
  snapshot_id?: string;
  room_id?: string;
  metadata?: any;
}

export interface ItineraryActivity {
  time?: string;
  title: string;
  description?: string;
  location?: string;
  type?: 'flight' | 'hotel' | 'meal' | 'sightseeing' | 'transit' | 'leisure';
  estimated_cost?: string;
}

export interface ItineraryDayData {
  day_number: number;
  date?: string;
  title: string;
  theme?: string;
  activities: ItineraryActivity[];
}

export interface BackendDailyPlanDay {
  day: number;
  title: string;
  morning?: string;
  afternoon?: string;
  evening?: string;
  estimated_daily_cost_inr?: number;
  notes?: string[];
}

export interface BackendPlanBudget {
  currency?: string;
  flight_estimate_inr?: number;
  hotel_estimate_inr?: number;
  local_transport_estimate_inr?: number;
  activities_estimate_inr?: number;
  contingency_inr?: number;
  estimated_total_inr?: number;
  target_budget_inr?: number;
  within_budget?: boolean;
  pricing_note?: string;
}

export interface BackendPlanJson {
  trip_summary?: string;
  route?: string;
  duration_days?: number;
  travel_dates?: string;
  travel_style?: string;
  daily_plan?: BackendDailyPlanDay[];
  budget?: BackendPlanBudget;
  flight_suggestion?: string;
  accommodation_plan?: string;
  weather_guidance?: string;
  assumptions?: string[];
  warnings?: string[];
  changes_from_feedback?: string[];
  destination?: string;
  origin?: string;
  destination_airport?: string;
  origin_airport?: string;
  departure_date?: string;
  return_date?: string;
  travelers?: number;
  status?: string;
  planning_unavailable?: boolean;
}

export interface BackendTravelResponse {
  success: boolean;
  thread_id: string;
  answer?: string;
  itinerary?: string;
  requires_approval?: boolean;
  approval_request?: string;
  flight_results?: string;
  hotel_results?: string;
  weather_results?: string;
  budget_results?: string;
  plan_json?: BackendPlanJson;
  revision_count?: number;
  guardrail_allowed?: boolean;
  planning_blocked?: boolean;
  planning_feedback?: string[];
}

export interface TravelPlanRequest {
  trip_id: string;
  thread_id?: string;
  message?: string;
  query?: string;
  constraints?: Record<string, unknown>;
}

export interface TravelPlanResponse {
  trip_id: string;
  thread_id: string;
  revision_id?: string;
  revision_number?: number;
  overview?: string;
  itinerary?: ItineraryDayData[];
  accommodation?: {
    name?: string;
    description?: string;
    area?: string;
    est_nightly_rate?: string;
  };
  flights?: {
    route?: string;
    suggested_carrier?: string;
    approx_price?: string;
  };
  estimated_budget?: {
    total?: number;
    target?: number;
    remaining?: number;
    currency?: string;
    breakdown?: Record<string, number | string>;
  };
  notes?: string[];
  raw_plan?: string;
  created_at?: string;
  plan_json?: BackendPlanJson;
}

export interface TripDetail extends TripSummary {
  revisions: TripRevision[];
  current_revision?: TripRevision;
  is_approved: boolean;
  is_booking_authorized?: boolean;
  booking_authorization?: BookingAuthorizationInfo | null;
  selected_flight_offer?: FlightOffer | Record<string, any>;
  selected_hotel_offer?: HotelOffer | Record<string, any>;
  plan?: TravelPlanResponse;
}
