import { FlightOffer, FlightSegment, BookingIntent } from '../types/flight';
import { HotelOffer, HotelRoom } from '../types/hotel';

// Clean curated image references for destinations and hotels
const DESTINATION_HOTEL_IMAGES: Record<string, string[]> = {
  kyoto: [
    'https://images.unsplash.com/photo-1493976040374-85c8e12f0c0e?q=80&w=1200&auto=format&fit=crop', // Traditional Kyoto garden ryokan
    'https://images.unsplash.com/photo-1503899036084-c55cdd92da26?q=80&w=1200&auto=format&fit=crop', // Pagoda view boutique
    'https://images.unsplash.com/photo-1545569341-9eb8b30979d9?q=80&w=1200&auto=format&fit=crop', // Serene bamboo garden stay
    'https://images.unsplash.com/photo-1528360983277-13d401cdc186?q=80&w=1200&auto=format&fit=crop', // Historic Machiya townhouse
  ],
  amalfi: [
    'https://images.unsplash.com/photo-1533105079780-92b9be482077?q=80&w=1200&auto=format&fit=crop', // Cliffside Amalfi villa
    'https://images.unsplash.com/photo-1516483638261-f4dbaf036963?q=80&w=1200&auto=format&fit=crop', // Mediterranean balcony
    'https://images.unsplash.com/photo-1533900298318-6b8da08a523e?q=80&w=1200&auto=format&fit=crop', // Ravello terrace
  ],
  tokyo: [
    'https://images.unsplash.com/photo-1540959733332-eab4deabeeaf?q=80&w=1200&auto=format&fit=crop', // Tokyo high-rise skyline
    'https://images.unsplash.com/photo-1503899036084-c55cdd92da26?q=80&w=1200&auto=format&fit=crop', // Urban boutique
  ],
  paris: [
    'https://images.unsplash.com/photo-1502602898657-3e91760cbb34?q=80&w=1200&auto=format&fit=crop', // Paris Haussmann balcony
    'https://images.unsplash.com/photo-1549144511-f099e773c147?q=80&w=1200&auto=format&fit=crop', // Latin Quarter boutique
  ],
  goa: [
    'https://images.unsplash.com/photo-1512343879784-a960bf40e7f2?q=80&w=1200&auto=format&fit=crop', // Beach villa resort
    'https://images.unsplash.com/photo-1587974928442-77dc3e0dba72?q=80&w=1200&auto=format&fit=crop', // Heritage Portuguese estate
  ],
  kerala: [
    'https://images.unsplash.com/photo-1602216056096-3b40cc0c9944?q=80&w=1200&auto=format&fit=crop', // Backwaters lakeside retreat
    'https://images.unsplash.com/photo-1593693397690-362cb9666fc2?q=80&w=1200&auto=format&fit=crop', // Munnar plantation bungalow
  ],
  kochi: [
    'https://images.unsplash.com/photo-1602216056096-3b40cc0c9944?q=80&w=1200&auto=format&fit=crop', // Fort Kochi heritage boutique
  ],
  darjeeling: [
    'https://images.unsplash.com/photo-1544735716-392fe2489ffa?q=80&w=1200&auto=format&fit=crop', // Himalayan colonial mountain lodge
    'https://images.unsplash.com/photo-1626621341517-bbf3d9990a23?q=80&w=1200&auto=format&fit=crop', // Tea estate sanctuary
  ],
  sikkim: [
    'https://images.unsplash.com/photo-1544735716-392fe2489ffa?q=80&w=1200&auto=format&fit=crop', // Gangtok valley view hotel
  ],
  kashmir: [
    'https://images.unsplash.com/photo-1566837945700-30057527ade0?q=80&w=1200&auto=format&fit=crop', // Luxury Dal Lake cedar houseboat
  ],
  srinagar: [
    'https://images.unsplash.com/photo-1566837945700-30057527ade0?q=80&w=1200&auto=format&fit=crop', // Dal Lake floating heritage suite
  ],
  jaipur: [
    'https://images.unsplash.com/photo-1599661046289-e31897846e41?q=80&w=1200&auto=format&fit=crop', // Rajput royal haveli courtyard
    'https://images.unsplash.com/photo-1580136579312-94651dfd596d?q=80&w=1200&auto=format&fit=crop', // Palace heritage pool
  ],
  rajasthan: [
    'https://images.unsplash.com/photo-1599661046289-e31897846e41?q=80&w=1200&auto=format&fit=crop', // Heritage palace hotel
  ],
  udaipur: [
    'https://images.unsplash.com/photo-1580136579312-94651dfd596d?q=80&w=1200&auto=format&fit=crop', // Lake Pichola luxury heritage
  ],
  kolkata: [
    'https://images.unsplash.com/photo-1558431382-27e303142255?q=80&w=1200&auto=format&fit=crop', // Grand colonial heritage hotel
  ],
  mumbai: [
    'https://images.unsplash.com/photo-1570168007204-dfb528c6958f?q=80&w=1200&auto=format&fit=crop', // Marine Drive luxury hotel
  ],
  delhi: [
    'https://images.unsplash.com/photo-1587474260584-136574528ed5?q=80&w=1200&auto=format&fit=crop', // Lutyens Delhi boutique
  ],
  default: [
    'https://images.unsplash.com/photo-1566073771259-6a8506099945?q=80&w=1200&auto=format&fit=crop', // Resort lounge
    'https://images.unsplash.com/photo-1582719508461-905c673771fd?q=80&w=1200&auto=format&fit=crop', // Boutique bedroom
    'https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?q=80&w=1200&auto=format&fit=crop', // Ocean view balcony
  ],
};

const getImagesForCity = (city: string): string[] => {
  const normalized = city.toLowerCase();
  for (const [key, urls] of Object.entries(DESTINATION_HOTEL_IMAGES)) {
    if (normalized.includes(key)) return urls;
  }
  return DESTINATION_HOTEL_IMAGES.default;
};

// Storage keys for mock persistence
const MOCK_STORAGE_SELECTIONS_KEY = 'trusttrip_mock_trip_selections';
const MOCK_STORAGE_INTENTS_KEY = 'trusttrip_mock_booking_intents';

interface TripSelections {
  flight?: FlightOffer;
  hotel?: HotelOffer;
  hotelRoom?: HotelRoom;
}

export const getMockSelections = (tripId: string): TripSelections => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_SELECTIONS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed[tripId] || {};
  } catch {
    return {};
  }
};

export const saveMockFlightSelection = (tripId: string, offer: FlightOffer): void => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_SELECTIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    parsed[tripId] = {
      ...(parsed[tripId] || {}),
      flight: offer,
    };
    localStorage.setItem(MOCK_STORAGE_SELECTIONS_KEY, JSON.stringify(parsed));
  } catch {}
};

export const saveMockHotelSelection = (
  tripId: string,
  offer: HotelOffer,
  room?: HotelRoom
): void => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_SELECTIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    parsed[tripId] = {
      ...(parsed[tripId] || {}),
      hotel: offer,
      hotelRoom: room || offer.rooms?.[0],
    };
    localStorage.setItem(MOCK_STORAGE_SELECTIONS_KEY, JSON.stringify(parsed));
  } catch {}
};

export const clearMockTripSelection = (tripId: string, type: 'flight' | 'hotel'): void => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_SELECTIONS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed[tripId]) {
      delete parsed[tripId][type];
      if (type === 'hotel') delete parsed[tripId].hotelRoom;
    }
    localStorage.setItem(MOCK_STORAGE_SELECTIONS_KEY, JSON.stringify(parsed));
  } catch {}
};

// Helper to extract IATA code or plausible code
const cleanAirportCode = (loc: string, fallback: string): string => {
  const match = loc.match(/\(([A-Z]{3})\)/);
  if (match) return match[1];
  const cleaned = loc.trim().toUpperCase().replace(/[^A-Z]/g, '');
  if (cleaned.length >= 3) return cleaned.slice(0, 3);
  return fallback;
};

export const generateMockFlights = (
  origin: string,
  destination: string,
  departureDate: string,
  returnDate?: string,
  cabinClass: 'economy' | 'premium_economy' | 'business' | 'first' = 'economy',
  passengers: number = 1
): FlightOffer[] => {
  const origCode = cleanAirportCode(origin, 'DEL');
  const destCode = cleanAirportCode(destination, 'GOI');
  const isRoundTrip = !!returnDate;

  const returnDay = returnDate || departureDate;

  // Indian airport code set
  const indianAirports = ['CCU', 'DEL', 'BOM', 'BLR', 'HYD', 'MAA', 'PNQ', 'AMD', 'COK', 'BBI', 'GOI', 'SXR', 'JAI', 'IXB', 'ATQ', 'VNS', 'IXC', 'GAU', 'TRV', 'UDR', 'JDH', 'DED'];
  const isOrigIndian = indianAirports.includes(origCode);
  const isDestIndian = indianAirports.includes(destCode);
  const isDomesticIndia = isOrigIndian && isDestIndian;
  const isIndianRoute = isOrigIndian || isDestIndian;

  // Multiplier for cabin class
  const classMultiplier =
    cabinClass === 'first'
      ? 4.5
      : cabinClass === 'business'
      ? 2.8
      : cabinClass === 'premium_economy'
      ? 1.5
      : 1.0;

  const basePricePerPerson = isDomesticIndia
    ? 4800 * classMultiplier
    : isIndianRoute
    ? 24500 * classMultiplier
    : 740 * classMultiplier;

  const currencyCode = isIndianRoute ? 'INR' : 'USD';

  // Generate 4 diverse and authentic flight options
  const carriers = isDomesticIndia
    ? [
        {
          code: '6E',
          name: 'IndiGo Airlines',
          flightNo: '6E 215',
          stops: 0,
          stopsLabel: 'Non-stop Direct',
          durationMinutes: 140,
          departureTime: '09:30',
          arrivalTime: '11:50',
          returnDepTime: '18:15',
          returnArrTime: '20:35',
          priceDelta: 250,
          refundable: true,
          badge: 'Most Popular • 98% On-Time Record',
          baggage: '7kg Cabin + 15kg Checked bag included',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
        {
          code: 'UK',
          name: 'Vistara (Tata SIA)',
          flightNo: 'UK 850',
          stops: 0,
          stopsLabel: 'Non-stop Direct',
          durationMinutes: 135,
          departureTime: '13:15',
          arrivalTime: '15:30',
          returnDepTime: '16:45',
          returnArrTime: '19:00',
          priceDelta: 950,
          refundable: true,
          badge: 'Premium Comfort • Complimentary Hot Meal',
          baggage: '7kg Cabin + 20kg Checked bag included',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
        {
          code: 'AI',
          name: 'Air India',
          flightNo: 'AI 402',
          stops: 0,
          stopsLabel: 'Non-stop Direct',
          durationMinutes: 145,
          departureTime: '11:00',
          arrivalTime: '13:25',
          returnDepTime: '15:00',
          returnArrTime: '17:25',
          priceDelta: 400,
          refundable: true,
          badge: 'Full Service • In-flight Refreshment',
          baggage: '7kg Cabin + 15kg Checked bag included',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
        {
          code: 'SG',
          name: 'SpiceJet',
          flightNo: 'SG 112',
          stops: 0,
          stopsLabel: 'Non-stop Direct',
          durationMinutes: 150,
          departureTime: '06:45',
          arrivalTime: '09:15',
          returnDepTime: '21:30',
          returnArrTime: '23:55',
          priceDelta: -350,
          refundable: false,
          badge: 'Best Value Fare • Early Bird',
          baggage: '7kg Cabin bag included',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
      ]
    : [
        {
          code: 'NH',
          name: 'All Nippon Airways (ANA)',
          flightNo: 'NH 007',
          stops: 0,
          stopsLabel: 'Non-stop',
          durationMinutes: 670,
          departureTime: '11:15',
          arrivalTime: '15:25',
          returnDepTime: '17:00',
          returnArrTime: '10:45',
          priceDelta: 120,
          refundable: true,
          badge: 'Curated Choice — High Comfort',
          baggage: '2 checked bags included (23kg each)',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
        {
          code: 'JL',
          name: 'Japan Airlines (JAL)',
          flightNo: 'JL 058',
          stops: 0,
          stopsLabel: 'Non-stop',
          durationMinutes: 655,
          departureTime: '13:40',
          arrivalTime: '17:15',
          returnDepTime: '18:30',
          returnArrTime: '11:55',
          priceDelta: 160,
          refundable: true,
          badge: 'Fastest Flight Time',
          baggage: '2 checked bags included + seat selection',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
        {
          code: 'SQ',
          name: 'Singapore Airlines',
          flightNo: 'SQ 031',
          stops: 1,
          stopsLabel: '1 stop (SIN • 1h 45m)',
          durationMinutes: 790,
          departureTime: '08:20',
          arrivalTime: '14:30',
          returnDepTime: '15:10',
          returnArrTime: '09:20',
          priceDelta: -40,
          refundable: false,
          badge: 'Best Value Fare',
          baggage: '1 checked bag (23kg) included',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
        {
          code: 'UA',
          name: 'United Airlines',
          flightNo: 'UA 875',
          stops: 0,
          stopsLabel: 'Non-stop',
          durationMinutes: 685,
          departureTime: '10:00',
          arrivalTime: '14:25',
          returnDepTime: '16:15',
          returnArrTime: '10:30',
          priceDelta: 20,
          refundable: true,
          badge: 'Flexible Schedule',
          baggage: 'Carry-on + 1 checked bag',
          cabinDescription: cabinClass.replace('_', ' ').toUpperCase(),
        },
      ];

  return carriers.map((c, index) => {
    const totalAmount = Math.round((basePricePerPerson + c.priceDelta) * passengers);
    const offerId = `fl_off_${c.code.toLowerCase()}_${index + 101}`;
    const snapshotId = `fl_snap_${c.code.toLowerCase()}_${Date.now().toString(36).slice(-5)}_${index}`;

    const outboundSegments: FlightSegment[] = [
      {
        id: `seg_out_${index}_1`,
        origin: origCode,
        destination: c.stops > 0 ? 'ICN' : destCode,
        departure_time: `${departureDate}T${c.departureTime}:00Z`,
        arrival_time: `${departureDate}T${c.arrivalTime}:00Z`,
        carrier_code: c.code,
        carrier_name: c.name,
        flight_number: c.flightNo,
        duration_minutes: c.stops > 0 ? 580 : c.durationMinutes,
        cabin_class: cabinClass,
      },
    ];

    if (c.stops > 0) {
      outboundSegments.push({
        id: `seg_out_${index}_2`,
        origin: 'ICN',
        destination: destCode,
        departure_time: `${departureDate}T16:15:00Z`,
        arrival_time: `${departureDate}T18:05:00Z`,
        carrier_code: c.code,
        carrier_name: c.name,
        flight_number: `${c.code} 704`,
        duration_minutes: 110,
        cabin_class: cabinClass,
      });
    }

    const slices = [
      {
        origin: origCode,
        destination: destCode,
        departure_time: `${departureDate}T${c.departureTime}:00Z`,
        arrival_time: `${departureDate}T${c.arrivalTime}:00Z`,
        segments: outboundSegments,
        total_duration_minutes: c.durationMinutes,
        stops: c.stops,
      },
    ];

    if (isRoundTrip) {
      slices.push({
        origin: destCode,
        destination: origCode,
        departure_time: `${returnDay}T${c.returnDepTime}:00Z`,
        arrival_time: `${returnDay}T${c.returnArrTime}:00Z`,
        segments: [
          {
            id: `seg_ret_${index}_1`,
            origin: destCode,
            destination: origCode,
            departure_time: `${returnDay}T${c.returnDepTime}:00Z`,
            arrival_time: `${returnDay}T${c.returnArrTime}:00Z`,
            carrier_code: c.code,
            carrier_name: c.name,
            flight_number: `${c.code} 008`,
            duration_minutes: c.durationMinutes - 30,
            cabin_class: cabinClass,
          },
        ],
        total_duration_minutes: c.durationMinutes - 30,
        stops: 0,
      });
    }

    const expiresDate = new Date();
    expiresDate.setMinutes(expiresDate.getMinutes() + 25);

    return {
      id: offerId,
      snapshot_id: snapshotId,
      slices,
      total_amount: String(totalAmount),
      currency: currencyCode,
      carrier_name: c.name,
      carrier_code: c.code,
      cabin_class: cabinClass,
      refundable: c.refundable,
      expires_at: expiresDate.toISOString(),
    };
  });
};

export const generateMockHotels = (
  city: string,
  checkIn: string,
  checkOut: string,
  guests: number = 2
): HotelOffer[] => {
  const images = getImagesForCity(city);

  // Compute nights
  const checkInDate = new Date(checkIn || '2026-10-14');
  const checkOutDate = new Date(checkOut || '2026-10-20');
  const diffTime = Math.abs(checkOutDate.getTime() - checkInDate.getTime());
  const nights = Math.max(1, Math.ceil(diffTime / (1000 * 60 * 60 * 24))) || 5;

  const cityName = city.split(',')[0].trim() || 'Kyoto';

  return [
    {
      id: 'htl_kyoto_01',
      hotel_id: 'htl_01',
      name: `Sowaka Historic Residence & Garden (${cityName})`,
      stars: 5,
      address: `480 Kiyomizu St., Higashiyama, ${cityName}`,
      city: cityName,
      rating: 9.6,
      review_count: 248,
      image_url: images[0] || images[0],
      total_amount: 320 * nights,
      currency: 'USD',
      cancellation_policy: 'Free cancellation until 48h prior to check-in',
      rooms: [
        {
          id: 'rm_sowaka_01',
          name: 'Traditional Sukiyazukuri Garden Villa',
          description: 'Spacious tatami sanctuary with private Japanese courtyard garden, cedar hinoki bath, and garden terrace.',
          price_per_night: 320,
          total_price: 320 * nights,
          currency: 'USD',
          beds: '2 Futons or King Platform Bed',
          capacity: Math.max(2, guests),
          amenities: ['Private Hinoki Cedar Bath', 'Garden View', 'Daily Gourmet Kaiseki Breakfast', 'Artisan Green Tea Set', 'High-Speed Wi-Fi'],
        },
        {
          id: 'rm_sowaka_02',
          name: 'Maisonette Suite with Mountain Vista',
          description: 'Two-story retreat with private lounge, handcrafted timber ceilings, and sweeping skyline views.',
          price_per_night: 460,
          total_price: 460 * nights,
          currency: 'USD',
          beds: '1 Super King + 1 Daybed',
          capacity: Math.max(3, guests),
          amenities: ['Panoramic Mountain Terrace', 'Complimentary In-room Bar', 'Butler Service', 'Custom Robes'],
        },
      ],
    },
    {
      id: 'htl_kyoto_02',
      hotel_id: 'htl_02',
      name: `Kura Heritage Boutique Ryokan`,
      stars: 4,
      address: `24 Shinbashi-dori, Gion District, ${cityName}`,
      city: cityName,
      rating: 9.3,
      review_count: 382,
      image_url: images[1] || images[0],
      total_amount: 215 * nights,
      currency: 'USD',
      cancellation_policy: 'Free cancellation up to 3 days before check-in',
      rooms: [
        {
          id: 'rm_kura_01',
          name: 'Deluxe Machiya Veranda Room',
          description: 'Harmonious blend of warm timber, shoji screens, and modern rain shower overlooking bamboo garden.',
          price_per_night: 215,
          total_price: 215 * nights,
          currency: 'USD',
          beds: '1 Queen Bed',
          capacity: 2,
          amenities: ['Courtyard Garden Access', 'Yukata Robes', 'Complimentary Teahouse Tasting', 'Breakfast Available'],
        },
      ],
    },
    {
      id: 'htl_kyoto_03',
      hotel_id: 'htl_03',
      name: `Riverside Pavilion & Modern Suites`,
      stars: 4,
      address: `12 Kamo River Promenade, ${cityName}`,
      city: cityName,
      rating: 9.0,
      review_count: 512,
      image_url: images[2] || images[0],
      total_amount: 175 * nights,
      currency: 'USD',
      cancellation_policy: 'Free cancellation until 24h prior to check-in',
      rooms: [
        {
          id: 'rm_river_01',
          name: 'Superior Riverview King',
          description: 'Contemporary minimalist retreat with floor-to-ceiling panoramic glass facing the tranquil riverfront.',
          price_per_night: 175,
          total_price: 175 * nights,
          currency: 'USD',
          beds: '1 King Bed',
          capacity: 2,
          amenities: ['Direct River View', 'Nespresso Coffee Bar', 'Walk to Subway Station', 'Fitness Studio'],
        },
      ],
    },
    {
      id: 'htl_kyoto_04',
      hotel_id: 'htl_04',
      name: `Aman-Style Sanctuary in Arashiyama`,
      stars: 5,
      address: `8 Bamboo Grove Road, Arashiyama, ${cityName}`,
      city: cityName,
      rating: 9.8,
      review_count: 140,
      image_url: images[3] || images[0],
      total_amount: 540 * nights,
      currency: 'USD',
      cancellation_policy: 'Non-refundable special rate',
      rooms: [
        {
          id: 'rm_aman_01',
          name: 'Forest Pavilion with Natural Onsen Spring',
          description: 'Exclusive pavilion nestled in maple woodland with thermal hot spring pool on a secluded deck.',
          price_per_night: 540,
          total_price: 540 * nights,
          currency: 'USD',
          beds: '1 King Bed',
          capacity: 2,
          amenities: ['Private Natural Thermal Onsen', 'Forest Deck', 'Full Board Tasting Menu', 'Airport Chauffeur Transfer'],
        },
      ],
    },
  ];
};

export const createMockBookingIntent = (
  tripId: string,
  offerId: string,
  snapshotId: string,
  amount: string = '860',
  currency: string = 'USD'
): BookingIntent => {
  const expiresAt = new Date();
  expiresAt.setMinutes(expiresAt.getMinutes() + 20);

  const intent: BookingIntent = {
    intent_id: `bkg_int_${Date.now().toString(36)}`,
    trip_id: tripId,
    offer_id: offerId,
    snapshot_id: snapshotId,
    status: 'prepared',
    original_amount: amount,
    currency,
    price_change_acknowledged: false,
    expires_at: expiresAt.toISOString(),
    created_at: new Date().toISOString(),
    handoff_url: `/trips/${tripId}?booking_intent=active`,
  };

  try {
    const raw = localStorage.getItem(MOCK_STORAGE_INTENTS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    parsed[intent.intent_id] = intent;
    localStorage.setItem(MOCK_STORAGE_INTENTS_KEY, JSON.stringify(parsed));
  } catch {}

  return intent;
};

export const revalidateMockBookingIntent = (intentId: string): BookingIntent => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_INTENTS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const existing = parsed[intentId] || {
      intent_id: intentId,
      offer_id: 'fl_off_nh_101',
      snapshot_id: 'fl_snap_nh_01',
      status: 'prepared',
      original_amount: '860',
      currency: 'USD',
      price_change_acknowledged: false,
      expires_at: new Date(Date.now() + 20 * 60 * 1000).toISOString(),
      created_at: new Date().toISOString(),
    };

    const updated: BookingIntent = {
      ...existing,
      status: 'revalidated',
      revalidated_amount: existing.original_amount,
    };
    parsed[intentId] = updated;
    localStorage.setItem(MOCK_STORAGE_INTENTS_KEY, JSON.stringify(parsed));
    return updated;
  } catch {
    return {
      intent_id: intentId,
      offer_id: 'fl_off_nh_101',
      snapshot_id: 'fl_snap_nh_01',
      status: 'revalidated',
      original_amount: '860',
      currency: 'USD',
      price_change_acknowledged: false,
      expires_at: new Date(Date.now() + 20 * 60 * 1000).toISOString(),
      created_at: new Date().toISOString(),
    };
  }
};

// ==========================================
// PART 4: Research, Documents & Revisions
// ==========================================

import {
  ResearchDocument,
  ResearchResponse,
  ResearchQueryRequest,
  ResearchSession,
} from '../types/research';
import { TripRevision, CreateRevisionRequest, TripChangeSet } from '../types/trip';

const MOCK_STORAGE_DOCUMENTS_KEY = 'trusttrip_mock_research_documents';
const MOCK_STORAGE_SESSIONS_KEY = 'trusttrip_mock_research_sessions';
const MOCK_STORAGE_REVISIONS_KEY = 'trusttrip_mock_trip_revisions';

const INITIAL_RESEARCH_DOCS: ResearchDocument[] = [
  {
    document_id: 'doc_kyoto_machiya_01',
    filename: 'Kyoto_Heritage_Preservation_Guide.pdf',
    title: 'Kyoto Machiya & Historic Preservation Rules',
    file_size_bytes: 2450000,
    uploaded_at: '2026-09-18T14:20:00Z',
    status: 'indexed',
    doc_type: 'pdf',
  },
  {
    document_id: 'doc_japan_rail_rules_02',
    filename: 'JR_Pass_Shinkansen_Luggage_2026.pdf',
    title: 'JR Central & West Shinkansen Oversized Baggage Regulations',
    file_size_bytes: 1820000,
    uploaded_at: '2026-09-21T09:45:00Z',
    status: 'indexed',
    doc_type: 'pdf',
  },
];

export const getMockResearchDocuments = (): ResearchDocument[] => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_DOCUMENTS_KEY);
    if (raw) return JSON.parse(raw);
    localStorage.setItem(MOCK_STORAGE_DOCUMENTS_KEY, JSON.stringify(INITIAL_RESEARCH_DOCS));
    return INITIAL_RESEARCH_DOCS;
  } catch {
    return INITIAL_RESEARCH_DOCS;
  }
};

export const saveMockResearchDocument = (file: {
  name: string;
  size: number;
  type?: string;
  title?: string;
}): ResearchDocument => {
  const docs = getMockResearchDocuments();
  const newDoc: ResearchDocument = {
    document_id: `doc_${Date.now().toString(36)}`,
    filename: file.name,
    title: file.title || file.name.replace(/\.[^/.]+$/, ''),
    file_size_bytes: file.size || 1500000,
    uploaded_at: new Date().toISOString(),
    status: 'indexed',
    doc_type: file.type || 'pdf',
  };

  const updated = [newDoc, ...docs];
  try {
    localStorage.setItem(MOCK_STORAGE_DOCUMENTS_KEY, JSON.stringify(updated));
  } catch {}
  return newDoc;
};

export const deleteMockResearchDocument = (documentId: string): boolean => {
  const docs = getMockResearchDocuments();
  const filtered = docs.filter((d) => d.document_id !== documentId);
  try {
    localStorage.setItem(MOCK_STORAGE_DOCUMENTS_KEY, JSON.stringify(filtered));
  } catch {}
  return true;
};

// Research session storage
export const getMockResearchSession = (sessionId: string, tripId?: string): ResearchSession => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_SESSIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (parsed[sessionId]) return parsed[sessionId];
  } catch {}

  const defaultSession: ResearchSession = {
    session_id: sessionId,
    trip_id: tripId,
    queries: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  return defaultSession;
};

export const saveMockResearchQuery = (
  sessionId: string,
  response: ResearchResponse
): void => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_SESSIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const session: ResearchSession = parsed[sessionId] || {
      session_id: sessionId,
      trip_id: response.trip_id,
      queries: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    session.queries.unshift(response);
    session.updated_at = new Date().toISOString();
    parsed[sessionId] = session;
    localStorage.setItem(MOCK_STORAGE_SESSIONS_KEY, JSON.stringify(parsed));
  } catch {}
};

// Travel Knowledge Discovery Mock Engine
export const executeMockResearchQuery = (
  request: ResearchQueryRequest
): ResearchResponse => {
  const q = request.question.toLowerCase();
  const sessionId = request.session_id || `rs_${Date.now().toString(36)}`;
  const tripId = request.trip_id;
  const mode = request.mode || 'hybrid';

  const isKyoto = q.includes('kyoto') || q.includes('japan') || !q.includes('amalfi');
  const isFood = q.includes('food') || q.includes('restaurant') || q.includes('eat') || q.includes('dining') || q.includes('kaiseki');
  const isStay = q.includes('stay') || q.includes('hotel') || q.includes('neighborhood') || q.includes('area');

  let answer = '';
  let citations: ResearchResponse['citations'] = [];
  let conflicts: ResearchResponse['conflicts'] = [];

  if (isKyoto && isFood) {
    answer =
      "For authentic traditional dining in Kyoto, focus on Higashiyama and the historic Gion district, where family-run kaiseki ryotei and machiya tea houses preserve generational culinary techniques [1]. Nishiki Market remains the epicenter for local street-level tastings such as grilled unagi skewers, artisanal yuba (tofu skin), and tsukemono (pickled Kyoto vegetables) [2]. For tea culture, Uji-sourced matcha ceremonies around Kodai-ji Temple provide intimate, reflective sessions away from tour groups [3].";

    citations = [
      {
        id: 'cit_kyo_01',
        source_type: mode === 'rag' ? 'document' : 'web',
        title: 'Historic Gion Dining & Machiya Preservation',
        publisher: 'Kyoto City Tourism Association',
        url: 'https://kyoto.travel/en/tradition/gion-cuisine.html',
        snippet:
          'Traditional wooden machiya townhouses along Shinbashi-dori and Hanami-koji host historic kaiseki ryotei emphasizing local Kyo-yasai heirloom vegetables and seasonal dashi broth.',
        claim: 'Higashiyama and Gion preserve authentic generational kaiseki ryotei.',
        freshness: 'Verified August 2026',
        confidence: 0.98,
        location: 'Gion / Higashiyama, Kyoto',
      },
      {
        id: 'cit_kyo_02',
        source_type: 'web',
        title: 'Nishiki Market Culinary Almanac',
        publisher: 'Kyoto Prefecture Chamber of Commerce',
        url: 'https://kyoto-nishiki.or.jp/english/guide',
        snippet:
          'Narrow 400-meter street market featuring over 130 vendor stalls specialized in yuba, roasted hojicha tea, savory egg tamagoyaki, and seasonal pickles.',
        claim: 'Nishiki Market serves artisanal yuba and heirloom Kyoto pickles.',
        freshness: 'Updated June 2026',
        confidence: 0.94,
        location: 'Nakagyo Ward, Kyoto',
      },
      {
        id: 'cit_kyo_03',
        source_type: mode === 'web' ? 'web' : 'document',
        title: 'Kyoto Machiya & Heritage Preservation Notes',
        publisher: 'Private Planning Notes (Uploaded PDF)',
        snippet:
          'Private teahouses adjacent to Kodai-ji and Kennin-ji hold reserved matcha tea tasting ceremonies with quiet courtyard gardens.',
        claim: 'Intimate matcha tea ceremonies situated near Kodai-ji gardens.',
        freshness: 'Uploaded Document (Sep 2026)',
        confidence: 0.92,
        location: 'Kodai-ji, Kyoto',
      },
    ];

    conflicts = [
      {
        field: 'Dining Hours & Photography in Gion Private Alleys',
        description:
          'City Council regulations strictly prohibit photography in private Gion residential alleys, whereas some public web blogs mistakenly state photography is unrestricted.',
        resolution_suggestion:
          'Keep photography strictly to public streets and respect posted private residence signage.',
        conflicting_sources: ['Kyoto City Ordinance Notice', 'Third-party Travel Blog'],
      },
    ];
  } else if (isKyoto && isStay) {
    answer =
      "For a serene and traditional stay, Higashiyama and Southern Gion offer walking access to wooden machiya stays and peaceful morning temple walks before crowds arrive [1]. For superior transit convenience and bullet train connections, staying near Kyoto Station or Karasuma-Oike provides rapid subway access across all four quadrants of the city [2]. Keep in mind that city buses enforce strict small-baggage policies during peak commute hours [3].";

    citations = [
      {
        id: 'cit_kyo_04',
        source_type: 'web',
        title: 'Kyoto Neighborhood Residential & Ryokan Guide',
        publisher: 'Japan National Tourism Organization (JNTO)',
        url: 'https://www.japan.travel/en/destinations/kansai/kyoto/',
        snippet:
          'Higashiyama remains the most evocative base for travelers seeking traditional Japanese timber architecture, stone-paved paths, and early morning access to Kiyomizu-dera.',
        claim: 'Higashiyama offers walking access to historic shrines and serene machiya.',
        freshness: 'Updated July 2026',
        confidence: 0.96,
        location: 'Higashiyama, Kyoto',
      },
      {
        id: 'cit_kyo_05',
        source_type: 'web',
        title: 'Kyoto Municipal Transportation Bureau',
        publisher: 'City of Kyoto Transportation Department',
        url: 'https://www.city.kyoto.lg.jp/kotsu/',
        snippet:
          'Karasuma and Tozai subway lines intersect at Karasuma-Oike, offering the fastest transfers between Arashiyama, central markets, and Kyoto Station.',
        claim: 'Karasuma-Oike provides rapid bidirectional subway access.',
        freshness: 'Official Schedule 2026',
        confidence: 0.99,
        location: 'Central Kyoto',
      },
      {
        id: 'cit_kyo_06',
        source_type: 'document',
        title: 'JR Central & West Shinkansen Oversized Baggage Regulations',
        publisher: 'JR Railway Documentation (Uploaded Document)',
        snippet:
          'Luggage exceeding 160 cm total dimensions requires dedicated reserved baggage seat bookings on Tokaido Shinkansen trains.',
        claim: 'Oversized luggage requires advance Shinkansen seat reservation.',
        freshness: 'Uploaded Document',
        confidence: 0.97,
        location: 'JR Shinkansen',
      },
    ];
  } else if (q.includes('amalfi') || q.includes('italy')) {
    answer =
      "When planning travel on the Amalfi Coast, ferry transit between Salerno, Amalfi, and Positano is significantly faster and more scenic than coastal road buses, which often experience heavy cliffside congestion between May and October [1]. For hiking enthusiasts, the Path of the Gods (Sentiero degli Dei) from Bomerano to Nocelle offers unmatched panoramic vistas over the Tyrrhenian Sea [2]. Ravello, perched 365 meters above sea level, provides a cooler, quieter haven known for Villa Cimbrone and Villa Rufolo cliffside gardens [3].";

    citations = [
      {
        id: 'cit_ama_01',
        source_type: 'web',
        title: 'Amalfi Coast Maritime Transit Schedule & Ports',
        publisher: 'Campania Region Port Authority',
        url: 'https://www.travelmar.it/en/',
        snippet:
          'Scheduled high-speed ferries connect Salerno, Maiori, Amalfi, and Positano, avoiding serpentine coastal road delays during daytime hours.',
        claim: 'Ferry transit between Amalfi and Positano bypasses road traffic.',
        freshness: 'Official Ferry Timetable 2026',
        confidence: 0.97,
        location: 'Amalfi Marina',
      },
      {
        id: 'cit_ama_02',
        source_type: 'web',
        title: 'Sentiero degli Dei Trailhead Safety & Route Guide',
        publisher: 'Parco Regionale dei Monti Lattari',
        url: 'https://parcoregionaledeimontilattari.it/',
        snippet:
          '7.8 km trail extending from Bomerano (Agerola) to Nocelle. Elevation varies from 650m to 420m with dramatic limestone crags and sea views.',
        claim: 'Path of the Gods connects Bomerano to Nocelle along limestone cliffs.',
        freshness: 'Updated Spring 2026',
        confidence: 0.95,
        location: 'Agerola / Nocelle',
      },
      {
        id: 'cit_ama_03',
        source_type: 'web',
        title: 'Ravello Heritage & Villa Cimbrone Infinity Terrace',
        publisher: 'Ravello Foundation',
        url: 'https://www.ravellofestival.com/',
        snippet:
          'Villa Cimbrone features the renowned Terrazza dell Infinito, adorned with classical marble busts overlooking the azure Gulf of Salerno.',
        claim: 'Villa Cimbrone terrace overlooks the Gulf of Salerno at 365m altitude.',
        freshness: 'Verified 2026',
        confidence: 0.96,
        location: 'Ravello, Italy',
      },
    ];
  } else {
    // General travel discovery
    answer =
      `Based on verified regional travel guidance, optimal visits require balancing landmark cultural highlights with quieter morning hours [1]. Local public transportation is recommended over congested car travel, and securing restaurant reservations 10 to 14 days in advance ensures access to renowned neighborhood dining [2]. Reviewing seasonal climate trends guarantees proper layering for evening strolls and coastal walkways [3].`;

    citations = [
      {
        id: 'cit_gen_01',
        source_type: 'web',
        title: 'Global Heritage & Cultural Sightseeing Guidelines',
        publisher: 'Official Tourism Board',
        snippet: 'Early morning entry between 07:30 and 09:00 provides uncrowded garden visits and superior natural photography lighting.',
        claim: 'Early morning timing minimizes queue wait times at major landmarks.',
        freshness: 'Current 2026 Season',
        confidence: 0.93,
      },
      {
        id: 'cit_gen_02',
        source_type: 'web',
        title: 'Regional Public Transit & Rail Connections',
        publisher: 'Department of Transportation',
        snippet: 'Integrated contactless transit passes provide seamless transfers across local metro lines and suburban railway networks.',
        claim: 'Contactless regional transit passes streamline multimodal journeys.',
        freshness: 'Updated 2026',
        confidence: 0.95,
      },
    ];
  }

  const response: ResearchResponse = {
    session_id: sessionId,
    trip_id: tripId,
    question: request.question,
    answer,
    source_type: mode === 'rag' ? 'rag' : mode === 'web' ? 'web' : 'hybrid',
    confidence_score: 0.96,
    freshness_note: 'Verified against current 2026 transit timetables and official tourism notices',
    citations,
    conflicts: conflicts.length > 0 ? conflicts : undefined,
    insights_count: {
      places: citations.length + 1,
      itinerary_suggestions: 2,
      verified_sources: citations.length,
    },
    created_at: new Date().toISOString(),
  };

  saveMockResearchQuery(sessionId, response);
  return response;
};

// ==========================================
// Trip Revision Mock Store & Conflict Engine
// ==========================================

export const getMockTripRevisions = (tripId: string): TripRevision[] => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_REVISIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (parsed[tripId] && parsed[tripId].length > 0) {
      return parsed[tripId];
    }
  } catch {}

  // Generate initial baseline revisions
  const initial: TripRevision[] = [
    {
      revision_id: `rev_${tripId}_01`,
      revision_number: 1,
      trip_id: tripId,
      created_at: '2026-09-20T10:00:00Z',
      change_summary: 'Initial travel plan baseline created',
      is_approved: false,
      origin: 'San Francisco (SFO)',
      destination: 'Kyoto, Japan',
      start_date: '2026-10-14',
      end_date: '2026-10-24',
      travelers_count: 2,
      budget: 4200,
      currency: 'USD',
      preferences: ['Boutique Ryokan', 'Kaiseki Dining', 'Historic Gardens'],
    },
  ];

  try {
    const raw = localStorage.getItem(MOCK_STORAGE_REVISIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    parsed[tripId] = initial;
    localStorage.setItem(MOCK_STORAGE_REVISIONS_KEY, JSON.stringify(parsed));
  } catch {}

  return initial;
};

export const createMockTripRevision = (
  tripId: string,
  req: CreateRevisionRequest
): TripRevision => {
  const revisions = getMockTripRevisions(tripId);
  const current = revisions[revisions.length - 1] || {
    revision_id: `rev_${tripId}_01`,
    revision_number: 1,
    origin: 'San Francisco (SFO)',
    destination: 'Kyoto, Japan',
    start_date: '2026-10-14',
    end_date: '2026-10-24',
    travelers_count: 2,
    budget: 4200,
  };

  // Conflict validation: verify base revision matches
  if (req.expected_base_revision !== current.revision_number) {
    const conflictErr = new Error(
      `Revision conflict: Expected base revision ${req.expected_base_revision}, but current revision is ${current.revision_number}. The trip was updated elsewhere.`
    );
    (conflictErr as any).statusCode = 409;
    (conflictErr as any).current_revision = current.revision_number;
    throw conflictErr;
  }

  const nextRevNumber = current.revision_number + 1;
  const changes = req.changes || {};

  // Check for stale travel selections
  const staleWarnings: string[] = [];
  const datesChanged =
    (changes.start_date && changes.start_date !== current.start_date) ||
    (changes.end_date && changes.end_date !== current.end_date);
  const routeChanged =
    (changes.origin && changes.origin !== current.origin) ||
    (changes.destination && changes.destination !== current.destination);
  const travelersChanged =
    changes.travelers_count !== undefined && changes.travelers_count !== current.travelers_count;

  if (datesChanged || routeChanged || travelersChanged) {
    staleWarnings.push(
      'Selected flight options may need revalidation due to updated route or dates.'
    );
    staleWarnings.push(
      'Selected stay dates require updated availability verification.'
    );
  }

  const newRevision: TripRevision = {
    revision_id: `rev_${tripId}_0${nextRevNumber}`,
    revision_number: nextRevNumber,
    trip_id: tripId,
    created_at: new Date().toISOString(),
    change_summary:
      req.change_summary ||
      changes.feedback ||
      `Updated trip preferences (Revision ${nextRevNumber})`,
    is_approved: false,
    origin: changes.origin || current.origin,
    destination: changes.destination || current.destination,
    start_date: changes.start_date || current.start_date,
    end_date: changes.end_date || current.end_date,
    travelers_count: changes.travelers_count ?? current.travelers_count,
    budget: changes.budget ?? current.budget,
    currency: changes.currency ?? current.currency,
    preferences: changes.preferences ?? current.preferences,
    changes: req.changes,
    previous_revision_id: current.revision_id,
    stale_warnings: staleWarnings.length > 0 ? staleWarnings : undefined,
  };

  revisions.push(newRevision);

  try {
    const raw = localStorage.getItem(MOCK_STORAGE_REVISIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    parsed[tripId] = revisions;
    localStorage.setItem(MOCK_STORAGE_REVISIONS_KEY, JSON.stringify(parsed));
  } catch {}

  return newRevision;
};

