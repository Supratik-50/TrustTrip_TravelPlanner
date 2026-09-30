export interface HotelRoom {
  id: string;
  name: string;
  description: string;
  price_per_night: number;
  total_price: number;
  currency: string;
  beds: string;
  capacity: number;
  amenities: string[];
}

export interface HotelOffer {
  id: string;
  hotel_id: string;
  name: string;
  stars: number;
  address: string;
  city: string;
  rating: number;
  review_count: number;
  image_url?: string;
  rooms: HotelRoom[];
  total_amount: number;
  currency: string;
  cancellation_policy: string;
}

export interface HotelSearchRequest {
  city: string;
  check_in_date: string;
  check_out_date: string;
  guests: number;
  rooms_count?: number;
  min_stars?: number;
}

export interface HotelPrebookRequest {
  hotel_id: string;
  room_id: string;
  check_in_date: string;
  check_out_date: string;
  guests: number;
  guest_name: string;
  guest_email: string;
}

export interface HotelPrebookResponse {
  prebook_id: string;
  amount: number;
  currency: string;
  razorpay_order_id?: string;
  hotel_name: string;
  expires_at: string;
}
