export interface FlightSegment {
  id: string;
  origin: string;
  destination: string;
  departure_time: string;
  arrival_time: string;
  carrier_code: string;
  carrier_name: string;
  flight_number: string;
  duration_minutes: number;
  cabin_class: string;
}

export interface FlightOffer {
  id: string;
  snapshot_id?: string;
  offer_id?: string;
  slices: {
    origin: string;
    destination: string;
    departure_time: string;
    arrival_time: string;
    segments: FlightSegment[];
    total_duration_minutes: number;
    stops: number;
  }[];
  total_amount: string;
  price?: number;
  currency: string;
  price_inr?: number | null;
  exchange_rate?: number | null;
  carrier_name: string;
  carrier_code: string;
  carrier_logo_url?: string;
  cabin_class: string;
  refundable: boolean;
  changeable?: boolean;
  expires_at?: string;
}

export interface FlightSearchRequest {
  trip_id?: string;
  origin?: string;
  destination?: string;
  departure_date?: string;
  return_date?: string;
  passengers?: number;
  adults?: number;
  children?: number;
  infants?: number;
  cabin_class?: 'economy' | 'premium_economy' | 'business' | 'first' | string;
  max_stops?: number;
  max_connections?: number;
}

export interface BookingIntent {
  intent_id: string;
  trip_id?: string;
  offer_id: string;
  snapshot_id: string;
  status: 'prepared' | 'revalidating' | 'revalidated' | 'price_changed' | 'handed_off' | 'cancelled' | 'confirmed' | 'unavailable' | 'expired';
  original_amount: string;
  revalidated_amount?: string;
  currency: string;
  price_change_acknowledged: boolean;
  expires_at: string;
  handoff_url?: string;
  created_at: string;
}

// ============================================================================
// SECTION 5A: NORMALIZED FLIGHT MODELS (BACKEND CONTRACT)
// ============================================================================

export interface NormalizedFlightSegment {
  origin: string;
  origin_name?: string | null;
  destination: string;
  destination_name?: string | null;
  departure_time: string;
  arrival_time: string;
  duration?: string | null;
  carrier?: string | null;
  carrier_code?: string | null;
  flight_number?: string | null;
}

export interface NormalizedFlightOffer {
  offer_id: string;
  provider?: string;
  origin: string;
  destination: string;
  departure_time: string;
  arrival_time: string;
  return_departure_time?: string | null;
  return_arrival_time?: string | null;
  duration?: string | null;
  stops: number;
  carrier?: string | null;
  carrier_code?: string | null;
  cabin_class: string;
  price: number;
  currency: string;
  price_inr?: number | null;
  exchange_rate?: number | null;
  price_per_traveler?: number | null;
  refundable?: boolean | null;
  changeable?: boolean | null;
  expires_at?: string | null;
  segments: NormalizedFlightSegment[];
}

export type FlightAvailabilityState = 'available' | 'empty' | 'unavailable' | 'error';

export interface FlightSearchMetadata {
  trip_id: string;
  origin: string;
  destination: string;
  departure_date: string;
  return_date?: string | null;
  traveler_count: number;
  currency: string;
  cabin_class: string;
  count: number;
}

export interface FlightSearchResponse {
  success: boolean;
  metadata?: FlightSearchMetadata | null;
  offers: NormalizedFlightOffer[];
  provider?: string;
  availability_state: FlightAvailabilityState;
  error?: string | null;
}

// ============================================================================
// SECTION 5B / 5C: FLIGHT BOOKING CONTRACT MODELS
// ============================================================================

export interface FlightPassenger {
  given_name: string;
  family_name: string;
  born_on: string; // YYYY-MM-DD
  title?: string;  // mr, mrs, ms, miss, mx, dr
  gender?: string; // m, f, x
  email?: string | null;
  phone_number?: string | null;
  type?: string;
}

export interface FlightBookingSelectionRequest {
  trip_id: string;
  offer_id: string;
  passengers: FlightPassenger[];
}

export interface FlightBookingSelectionResponse {
  success: boolean;
  booking_id: string;
  trip_id: string;
  offer_id: string;
  status: 'SELECTED' | string;
  price: number;
  currency: string;
  price_inr?: number | null;
  passenger_count: number;
  human_approval_required: boolean;
  approved: boolean;
  message?: string;
}

export interface FlightBookingRevalidationRequest {
  booking_id?: string;
  accepted_price?: number;
}

export interface FlightBookingRevalidationResponse {
  success: boolean;
  booking_id: string;
  status: 'READY_FOR_BOOKING' | 'PRICE_CHANGED' | 'UNAVAILABLE' | string;
  is_valid: boolean;
  price_changed: boolean;
  original_price: number;
  current_price: number;
  currency: string;
  human_approval_required: boolean;
  approved: boolean;
  message?: string | null;
  error?: string | null;
}

export interface FlightBookingApprovalRequest {
  booking_id?: string;
  approved?: boolean;
  notes?: string;
}

export interface FlightBookingApprovalResponse {
  success: boolean;
  booking_id: string;
  status: string;
  approved: boolean;
  approved_at: string;
  message: string;
}

export interface FlightBookingConfirmationRequest {
  booking_id?: string;
  final_confirmation: boolean;
  payment?: Record<string, any> | null;
  payment_mode?: string;
}

export interface FlightBookingConfirmationResponse {
  success: boolean;
  booked: boolean;
  booking_id: string;
  status: 'COMPLETED' | 'FAILED' | 'PRICE_CHANGED' | 'UNAVAILABLE' | string;
  booking_reference?: string | null;
  order_id?: string | null;
  pnr?: string | null;
  e_ticket?: string | null;
  price?: number | null;
  currency?: string | null;
  message?: string | null;
  error?: string | null;
  duplicate_attempt?: boolean;
  requires_final_confirmation?: boolean;
  approval_required?: boolean;
}

export interface FlightBookingDetails {
  booking_id: string;
  trip_id: string;
  offer_id: string;
  status: string;
  expected_amount: number;
  currency: string;
  approved: boolean;
  approved_at?: string | null;
  passenger_count: number;
  booking_reference?: string | null;
  order_id?: string | null;
  pnr?: string | null;
  e_ticket?: string | null;
  created_at: string;
}

export type Section5FlowState =
  | 'available'
  | 'empty'
  | 'stale'
  | 'price_changed'
  | 'unavailable'
  | 'awaiting_approval'
  | 'booking_in_progress'
  | 'completed'
  | 'failed';

// ============================================================================
// HELPER CONVERTER FUNCTIONS
// ============================================================================

function parseDurationMinutes(duration?: string | null): number {
  if (!duration) return 0;
  let total = 0;
  const hMatch = duration.match(/(\d+)\s*h/i);
  const mMatch = duration.match(/(\d+)\s*m/i);
  if (hMatch) total += parseInt(hMatch[1], 10) * 60;
  if (mMatch) total += parseInt(mMatch[1], 10);
  return total || 0;
}

export function toFlightOffer(normalized: NormalizedFlightOffer): FlightOffer {
  const durMins = parseDurationMinutes(normalized.duration);
  const segments: FlightSegment[] = (normalized.segments && normalized.segments.length > 0)
    ? normalized.segments.map((seg, idx) => ({
        id: `${normalized.offer_id}_seg_${idx}`,
        origin: seg.origin,
        destination: seg.destination,
        departure_time: seg.departure_time,
        arrival_time: seg.arrival_time,
        carrier_code: seg.carrier_code || normalized.carrier_code || '',
        carrier_name: seg.carrier || normalized.carrier || 'Airline Partner',
        flight_number: seg.flight_number || (normalized.carrier_code ? `${normalized.carrier_code}-${100 + idx}` : 'FL-100'),
        duration_minutes: parseDurationMinutes(seg.duration) || durMins || 120,
        cabin_class: normalized.cabin_class,
      }))
    : [
        {
          id: `${normalized.offer_id}_seg_0`,
          origin: normalized.origin,
          destination: normalized.destination,
          departure_time: normalized.departure_time,
          arrival_time: normalized.arrival_time,
          carrier_code: normalized.carrier_code || '',
          carrier_name: normalized.carrier || 'Airline Partner',
          flight_number: normalized.carrier_code ? `${normalized.carrier_code}-200` : 'FL-200',
          duration_minutes: durMins || 120,
          cabin_class: normalized.cabin_class,
        },
      ];

  const slices: FlightOffer['slices'] = [];
  if (normalized.return_departure_time) {
    const outboundSegs: FlightSegment[] = [];
    const returnSegs: FlightSegment[] = [];
    for (const seg of segments) {
      if (
        returnSegs.length > 0 ||
        seg.origin === normalized.destination ||
        (normalized.return_departure_time && seg.departure_time >= normalized.return_departure_time)
      ) {
        returnSegs.push(seg);
      } else {
        outboundSegs.push(seg);
      }
    }
    if (outboundSegs.length > 0 && returnSegs.length > 0) {
      slices.push({
        origin: normalized.origin,
        destination: normalized.destination,
        departure_time: normalized.departure_time,
        arrival_time: outboundSegs[outboundSegs.length - 1]?.arrival_time || normalized.arrival_time,
        segments: outboundSegs,
        total_duration_minutes: durMins || 120,
        stops: Math.max(0, outboundSegs.length - 1),
      });
      slices.push({
        origin: normalized.destination,
        destination: normalized.origin,
        departure_time: normalized.return_departure_time,
        arrival_time: normalized.return_arrival_time || returnSegs[returnSegs.length - 1]?.arrival_time || '',
        segments: returnSegs,
        total_duration_minutes: durMins || 120,
        stops: Math.max(0, returnSegs.length - 1),
      });
    }
  }

  if (slices.length === 0) {
    slices.push({
      origin: normalized.origin,
      destination: normalized.destination,
      departure_time: normalized.departure_time,
      arrival_time: normalized.arrival_time,
      segments,
      total_duration_minutes: durMins || 120,
      stops: normalized.stops,
    });
  }

  return {
    id: normalized.offer_id,
    snapshot_id: normalized.offer_id,
    offer_id: normalized.offer_id,
    slices,
    total_amount: String(normalized.price),
    price: normalized.price,
    currency: normalized.currency,
    price_inr: normalized.price_inr,
    exchange_rate: normalized.exchange_rate,
    carrier_name: normalized.carrier || 'Airline Partner',
    carrier_code: normalized.carrier_code || '',
    cabin_class: normalized.cabin_class,
    refundable: Boolean(normalized.refundable),
    changeable: Boolean(normalized.changeable),
    expires_at: normalized.expires_at || undefined,
  };
}
