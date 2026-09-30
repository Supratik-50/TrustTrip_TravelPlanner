/**
 * Departure airports and domestic route discovery presets for TrustTrip
 */

export interface AirportOption {
  code: string;
  city: string;
  name: string;
  country: string;
  display: string;
}

export interface RoutePreset {
  label: string;
  origin: string;
  destination: string;
  category: 'domestic' | 'international';
  description?: string;
}

export const INDIAN_DEPARTURE_AIRPORTS: AirportOption[] = [
  { code: 'CCU', city: 'Kolkata', name: 'Netaji Subhash Chandra Bose Int.', country: 'India', display: 'Kolkata (CCU)' },
  { code: 'DEL', city: 'Delhi', name: 'Indira Gandhi International', country: 'India', display: 'Delhi (DEL)' },
  { code: 'BOM', city: 'Mumbai', name: 'Chhatrapati Shivaji Maharaj Int.', country: 'India', display: 'Mumbai (BOM)' },
  { code: 'BLR', city: 'Bengaluru', name: 'Kempegowda International', country: 'India', display: 'Bengaluru (BLR)' },
  { code: 'HYD', city: 'Hyderabad', name: 'Rajiv Gandhi International', country: 'India', display: 'Hyderabad (HYD)' },
  { code: 'MAA', city: 'Chennai', name: 'Chennai International', country: 'India', display: 'Chennai (MAA)' },
  { code: 'PNQ', city: 'Pune', name: 'Pune Airport', country: 'India', display: 'Pune (PNQ)' },
  { code: 'AMD', city: 'Ahmedabad', name: 'Sardar Vallabhbhai Patel Int.', country: 'India', display: 'Ahmedabad (AMD)' },
  { code: 'COK', city: 'Kochi', name: 'Cochin International', country: 'India', display: 'Kochi (COK)' },
  { code: 'BBI', city: 'Bhubaneswar', name: 'Biju Patnaik Airport', country: 'India', display: 'Bhubaneswar (BBI)' },
];

export const INTERNATIONAL_DEPARTURE_AIRPORTS: AirportOption[] = [
  { code: 'LHR', city: 'London', name: 'Heathrow Airport', country: 'United Kingdom', display: 'London (LHR)' },
  { code: 'CDG', city: 'Paris', name: 'Charles de Gaulle Airport', country: 'France', display: 'Paris (CDG)' },
  { code: 'HND', city: 'Tokyo', name: 'Haneda Airport', country: 'Japan', display: 'Tokyo (HND)' },
  { code: 'DXB', city: 'Dubai', name: 'Dubai International', country: 'UAE', display: 'Dubai (DXB)' },
  { code: 'SIN', city: 'Singapore', name: 'Changi Airport', country: 'Singapore', display: 'Singapore (SIN)' },
  { code: 'JFK', city: 'New York', name: 'John F. Kennedy Int.', country: 'United States', display: 'New York (JFK)' },
  { code: 'SFO', city: 'San Francisco', name: 'San Francisco International', country: 'United States', display: 'San Francisco (SFO)' },
];

export const DOMESTIC_INDIA_ROUTES: RoutePreset[] = [
  { label: 'Kolkata ➔ Goa', origin: 'Kolkata (CCU)', destination: 'Goa (GOI)', category: 'domestic', description: 'Beaches & coastal leisure' },
  { label: 'Kolkata ➔ Delhi', origin: 'Kolkata (CCU)', destination: 'Delhi (DEL)', category: 'domestic', description: 'Capital heritage & food' },
  { label: 'Delhi ➔ Srinagar', origin: 'Delhi (DEL)', destination: 'Srinagar (SXR)', category: 'domestic', description: 'Kashmir lakes & valleys' },
  { label: 'Mumbai ➔ Goa', origin: 'Mumbai (BOM)', destination: 'Goa (GOI)', category: 'domestic', description: 'Sunlit Konkan getaway' },
  { label: 'Bengaluru ➔ Kochi', origin: 'Bengaluru (BLR)', destination: 'Kochi (COK)', category: 'domestic', description: 'Kerala backwaters & culture' },
  { label: 'Delhi ➔ Jaipur', origin: 'Delhi (DEL)', destination: 'Jaipur (JAI)', category: 'domestic', description: 'Pink City royal palaces' },
  { label: 'Mumbai ➔ Kochi', origin: 'Mumbai (BOM)', destination: 'Kochi (COK)', category: 'domestic', description: 'Spice coast & heritage stay' },
  { label: 'Kolkata ➔ Bagdogra', origin: 'Kolkata (CCU)', destination: 'Bagdogra (IXB)', category: 'domestic', description: 'Gateway to Darjeeling & Sikkim' },
];

export const INTERNATIONAL_ROUTES: RoutePreset[] = [
  { label: 'Delhi ➔ London', origin: 'Delhi (DEL)', destination: 'London (LHR)', category: 'international', description: 'Direct transatlantic hub' },
  { label: 'Mumbai ➔ Dubai', origin: 'Mumbai (BOM)', destination: 'Dubai (DXB)', category: 'international', description: 'Arabian Gulf stopover' },
  { label: 'Kolkata ➔ Bangkok', origin: 'Kolkata (CCU)', destination: 'Bangkok (BKK)', category: 'international', description: 'Southeast Asian gateway' },
  { label: 'SFO ➔ Kyoto / Osaka', origin: 'San Francisco (SFO)', destination: 'Osaka / Kyoto (KIX)', category: 'international', description: 'Historic Japan discovery' },
  { label: 'JFK ➔ Naples / Amalfi', origin: 'New York (JFK)', destination: 'Naples / Amalfi (NAP)', category: 'international', description: 'Italian coast' },
];
