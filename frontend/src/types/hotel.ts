export interface HotelRoom {
  id: string;
  name: string;
  description?: string | null;
  price_per_night?: number | null;
  total_price?: number | null;
  currency?: string;
  beds?: string | null;
  capacity?: number | null;
  amenities?: string[];
}

export interface HotelOffer {
  id: string;
  snapshot_id?: string;
  hotel_id?: string;
  name: string;
  hotel_name?: string;
  stars?: number | null;
  address: string;
  city: string;
  destination?: string;
  rating?: number | null;
  review_count?: number | null;
  image_url?: string;
  rooms: HotelRoom[];
  total_amount: number;
  price?: number;
  currency: string;
  price_inr?: number;
  exchange_rate?: number;
  nightly_price?: number;
  nights?: number;
  rooms_count?: number;
  adults_count?: number;
  checkin?: string;
  checkout?: string;
  check_in_date?: string;
  check_out_date?: string;
  availability?: string;
  cancellation_policy?: string | null;
  refundable?: boolean | null;
  room_name?: string | null;
  board_name?: string | null;
  amenities?: string[];
  is_recommended?: boolean;
  recommendation_rank?: number;
  provider?: string;
}

export interface HotelSearchRequest {
  trip_id?: string;
  destination?: string;
  city?: string;
  checkin?: string;
  checkout?: string;
  check_in_date?: string;
  check_out_date?: string;
  guests?: number;
  adults?: number;
  children?: number;
  rooms_count?: number;
  rooms?: number;
  min_stars?: number;
  budget_inr?: number;
  cheaper_mode?: boolean;
}

export interface HotelPrebookRequest {
  trip_id: string;
  snapshot_id?: string;
  hotel_id?: string;
  room_id?: string;
  check_in_date?: string;
  check_out_date?: string;
  guests?: number | any[];
  guest_name?: string;
  guest_email?: string;
  offer?: any;
}

export interface HotelPrebookResponse {
  success?: boolean;
  prebook_id: string;
  amount: number;
  currency: string;
  status?: string;
  razorpay_order_id?: string;
  hotel_name: string;
  expires_at?: string;
  prebook?: any;
}
