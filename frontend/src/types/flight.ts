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
  currency: string;
  carrier_name: string;
  carrier_code: string;
  carrier_logo_url?: string;
  cabin_class: string;
  refundable: boolean;
  expires_at?: string;
}

export interface FlightSearchRequest {
  origin: string;
  destination: string;
  departure_date: string;
  return_date?: string;
  passengers: number;
  cabin_class?: 'economy' | 'premium_economy' | 'business' | 'first';
  max_stops?: number;
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
