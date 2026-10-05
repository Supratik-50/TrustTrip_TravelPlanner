import { ApiError } from '../types/api';
import { AuthResponse, LoginCredentials, SignupCredentials, User } from '../types/auth';
import { BackendHealth } from '../types/api';
import {
  TripSummary,
  TripDetail,
  CreateTripRequest,
  TravelPlanRequest,
  TravelPlanResponse,
  ItineraryDayData,
  TripOfferSelection,
  TripRevision,
  CreateRevisionRequest,
  BookingAuthorizationInfo,
  BookingEligibilityInfo,
} from '../types/trip';
import {
  FlightOffer,
  FlightSearchRequest,
  BookingIntent,
  NormalizedFlightOffer,
  FlightSearchResponse,
  FlightBookingSelectionRequest,
  FlightBookingSelectionResponse,
  FlightBookingRevalidationResponse,
  FlightBookingApprovalResponse,
  FlightBookingConfirmationResponse,
  FlightBookingDetails,
  toFlightOffer,
} from '../types/flight';
import {
  HotelOffer,
  HotelSearchRequest,
  HotelPrebookRequest,
  HotelPrebookResponse,
} from '../types/hotel';
import {
  ResearchDocument,
  ResearchResponse,
  ResearchQueryRequest,
  ResearchSession,
} from '../types/research';
import {
  getMockSelections,
  saveMockFlightSelection,
  saveMockHotelSelection,
  generateMockFlights,
  generateMockHotels,
  createMockBookingIntent,
  revalidateMockBookingIntent,
  getMockResearchDocuments,
  saveMockResearchDocument,
  deleteMockResearchDocument,
  getMockResearchSession,
  executeMockResearchQuery,
  getMockTripRevisions,
  createMockTripRevision,
} from './mockTravelData';
import { storage } from './storage';

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

export const setUnauthorizedHandler = (handler: UnauthorizedHandler | null) => {
  unauthorizedHandler = handler;
};

// Demo mock state for offline preview fallback (trips and booking intents)
const MOCK_STORAGE_TRIPS_KEY = 'trusttrip_demo_mock_trips';
const MOCK_STORAGE_PLANS_KEY = 'trusttrip_demo_mock_plans';
const MOCK_STORAGE_SELECTIONS_KEY = 'trusttrip_demo_mock_selections';
const MOCK_STORAGE_BOOKING_INTENTS_KEY = 'trusttrip_demo_mock_intents';

const getInitialMockTrips = (): TripSummary[] => [
  {
    trip_id: 'trp_kyoto_2026',
    title: 'Kyoto Cultural & Tea Journey',
    origin: 'San Francisco (SFO)',
    destination: 'Kyoto, Japan',
    start_date: '2026-10-14',
    end_date: '2026-10-24',
    status: 'planning',
    active_revision_number: 1,
    active_revision_id: 'rev_kyoto_01',
    thread_id: 'th_kyoto_882',
    travelers_count: 2,
    budget: 4200,
    currency: 'USD',
    preferences: ['Boutique Ryokan', 'Traditional Kaiseki', 'Historic Gardens', 'Rail Transit'],
    created_at: '2026-09-20T10:00:00Z',
    updated_at: '2026-09-22T14:30:00Z',
  },
  {
    trip_id: 'trp_amalfi_2026',
    title: 'Amalfi Coast & Ravello Coastal Trail',
    origin: 'New York (JFK)',
    destination: 'Amalfi Coast, Italy',
    start_date: '2026-11-04',
    end_date: '2026-11-12',
    status: 'draft',
    active_revision_number: 1,
    active_revision_id: 'rev_amalfi_01',
    thread_id: 'th_amalfi_910',
    travelers_count: 2,
    budget: 3800,
    currency: 'EUR',
    preferences: ['Scenic Coastal Ferries', 'Cliffside Dining', 'Villa Gardens'],
    created_at: '2026-09-21T08:15:00Z',
    updated_at: '2026-09-23T11:20:00Z',
  },
];

const getMockTrips = (): TripSummary[] => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_TRIPS_KEY);
    if (raw) return JSON.parse(raw);
    const initial = getInitialMockTrips();
    localStorage.setItem(MOCK_STORAGE_TRIPS_KEY, JSON.stringify(initial));
    return initial;
  } catch {
    return getInitialMockTrips();
  }
};

const saveMockTrips = (trips: TripSummary[]) => {
  try {
    localStorage.setItem(MOCK_STORAGE_TRIPS_KEY, JSON.stringify(trips));
  } catch {}
};

const getMockPlan = (tripId: string): TravelPlanResponse | null => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_PLANS_KEY);
    if (!raw) return null;
    const plans = JSON.parse(raw);
    return plans[tripId] || null;
  } catch {
    return null;
  }
};

const saveMockPlan = (tripId: string, plan: TravelPlanResponse) => {
  try {
    const raw = localStorage.getItem(MOCK_STORAGE_PLANS_KEY);
    const plans = raw ? JSON.parse(raw) : {};
    plans[tripId] = plan;
    localStorage.setItem(MOCK_STORAGE_PLANS_KEY, JSON.stringify(plans));
  } catch {}
};

export function normalizeBackendTrip(raw: any): TripSummary {
  if (!raw) return {} as TripSummary;
  let meta = raw.metadata || {};
  if (typeof meta === 'string') {
    try {
      meta = JSON.parse(meta);
    } catch {
      meta = {};
    }
  }
  const constraints = meta.constraints || {};
  const tripId = raw.id || raw.trip_id;
  return {
    id: raw.id || tripId,
    trip_id: tripId,
    title: raw.title || (raw.destination ? `${raw.destination} Journey` : (meta.destination ? `${meta.destination} Journey` : 'Trip Journey')),
    origin: raw.origin || meta.origin || constraints.origin || '',
    destination: raw.destination || meta.destination || constraints.destination || '',
    start_date: raw.start_date || meta.start_date || constraints.start_date || constraints.departure_date || '',
    end_date: raw.end_date || meta.end_date || constraints.end_date || constraints.return_date || '',
    status: raw.status || 'draft',
    active_revision_number: raw.current_revision_number ?? raw.active_revision_number ?? meta.revision ?? meta.trip_revision ?? 1,
    // FIX 3: Never fabricate revision IDs. If backend doesn't supply one, leave undefined.
    active_revision_id: raw.active_revision_id || raw.current_revision_id || meta.active_revision_id || undefined,
    thread_id: raw.thread_id || meta.thread_id,
    travelers_count: raw.travelers_count ?? meta.travelers_count ?? constraints.travelers_count ?? constraints.adults ?? 1,
    budget: raw.budget ?? meta.budget ?? constraints.budget ?? constraints.budget_amount,
    currency: raw.currency || meta.currency || constraints.currency || 'INR',
    preferences: raw.preferences || meta.preferences || [],
    metadata: meta,
    flight_count: raw.flight_count ?? (Array.isArray(raw.flights) ? raw.flights.length : 0),
    hotel_count: raw.hotel_count ?? (Array.isArray(raw.hotels) ? raw.hotels.length : 0),
    flights: Array.isArray(raw.flights) ? raw.flights : [],
    hotels: Array.isArray(raw.hotels) ? raw.hotels : [],
    is_booking_authorized: Boolean(raw.is_booking_authorized ?? meta.is_booking_authorized),
    booking_authorization: raw.booking_authorization ?? meta.booking_authorization ?? null,
    created_at: raw.created_at || new Date().toISOString(),
    updated_at: raw.updated_at || new Date().toISOString(),
  };
}

export function normalizeBackendPlanResponse(
  raw: any,
  tripId: string,
  threadId: string
): TravelPlanResponse {
  if (!raw) {
    return {
      trip_id: tripId,
      thread_id: threadId,
    };
  }

  // If already normalized TravelPlanResponse
  if (raw.trip_id && Array.isArray(raw.itinerary) && !raw.plan_json) {
    return raw as TravelPlanResponse;
  }

  const planJson = raw.plan_json || (raw.metadata?.plan ? raw.metadata.plan : (raw.trip_summary || raw.daily_plan ? raw : null));
  const rawMarkdown = raw.itinerary || raw.answer || raw.raw_plan || '';

  // 1. Build Day-by-Day Itinerary
  let itineraryDays: ItineraryDayData[] = [];

  if (planJson && Array.isArray(planJson.daily_plan) && planJson.daily_plan.length > 0) {
    itineraryDays = planJson.daily_plan.map((day: any) => {
      const activities = [];

      if (day.morning) {
        const isTransit = /fly|flight|train|rail|airport|station|transfer|arrive/i.test(day.morning);
        activities.push({
          time: 'Morning',
          title: isTransit ? 'Arrival & Regional Transit' : 'Morning Discovery',
          description: day.morning,
          type: isTransit ? ('transit' as const) : ('sightseeing' as const),
        });
      }

      if (day.afternoon) {
        const isMeal = /lunch|café|cafe|restaurant|bistro|dining/i.test(day.afternoon);
        activities.push({
          time: 'Afternoon',
          title: isMeal ? 'Local Culinary & Afternoon Exploration' : 'Afternoon Discovery',
          description: day.afternoon,
          type: isMeal ? ('meal' as const) : ('sightseeing' as const),
        });
      }

      if (day.evening) {
        activities.push({
          time: 'Evening',
          title: 'Evening Leisure & Dining',
          description: day.evening,
          type: 'meal' as const,
        });
      }

      // Add extra notes as sub-activities if provided
      if (Array.isArray(day.notes)) {
        day.notes.forEach((note: string) => {
          if (note && !note.toLowerCase().includes('cost:')) {
            activities.push({
              title: 'Curated Recommendation',
              description: note,
              type: 'leisure' as const,
            });
          }
        });
      }

      return {
        day_number: day.day || 1,
        title: day.title || `Day ${day.day || 1}`,
        theme: day.notes?.[0] || undefined,
        activities,
      };
    });
  } else if (rawMarkdown) {
    // Markdown parser fallback for standard backend day sections: ### Day X: Title
    const dayMatches = rawMarkdown.split(/### Day (\d+):/i);
    if (dayMatches.length > 1) {
      for (let i = 1; i < dayMatches.length; i += 2) {
        const dayNum = parseInt(dayMatches[i], 10);
        const dayBody = dayMatches[i + 1] || '';
        const titleLine = dayBody.split('\n')[0]?.trim() || `Day ${dayNum}`;
        const activities = [];

        const morningMatch = dayBody.match(/- \*\*Morning:\*\*([^\n]+)/i);
        if (morningMatch) {
          activities.push({
            time: 'Morning',
            title: 'Morning Exploration',
            description: morningMatch[1].trim(),
            type: 'sightseeing' as const,
          });
        }

        const afternoonMatch = dayBody.match(/- \*\*Afternoon:\*\*([^\n]+)/i);
        if (afternoonMatch) {
          activities.push({
            time: 'Afternoon',
            title: 'Afternoon Discovery',
            description: afternoonMatch[1].trim(),
            type: 'sightseeing' as const,
          });
        }

        const eveningMatch = dayBody.match(/- \*\*Evening:\*\*([^\n]+)/i);
        if (eveningMatch) {
          activities.push({
            time: 'Evening',
            title: 'Evening Leisure',
            description: eveningMatch[1].trim(),
            type: 'meal' as const,
          });
        }

        itineraryDays.push({
          day_number: dayNum,
          title: titleLine,
          activities,
        });
      }
    }
  }

  // 2. Budget Information
  let budgetObj = undefined;
  const rawBudget = planJson?.budget || raw.budget_results || raw.budget;
  let b = rawBudget;
  if (typeof b === 'string') {
    try {
      b = JSON.parse(b);
    } catch {
      b = null;
    }
  }

  if (b && typeof b === 'object') {
    const rawBreakdown = b.breakdown || {};
    const breakdown: Record<string, number> = {
      'Flights': Math.round(Number(rawBreakdown['Flights'] ?? b.flight_estimate_inr ?? b.flight_estimate ?? 0)),
      'Accommodation': Math.round(Number(rawBreakdown['Accommodation'] ?? b.hotel_estimate_inr ?? b.hotel_estimate ?? 0)),
      'Local Transit': Math.round(Number(rawBreakdown['Local Transit'] ?? b.local_transport_estimate_inr ?? b.local_transport_estimate ?? 0)),
      'Activities': Math.round(Number(rawBreakdown['Activities'] ?? b.activities_estimate_inr ?? b.activities_estimate ?? 0)),
      'Contingency': Math.round(Number(rawBreakdown['Contingency'] ?? b.contingency_inr ?? b.contingency ?? 0)),
    };

    const total = Math.round(Number(b.estimated_total_inr ?? b.total ?? b.estimated_total ?? (
      breakdown['Flights'] + breakdown['Accommodation'] + breakdown['Local Transit'] + breakdown['Activities'] + breakdown['Contingency']
    )));

    const target = Math.round(Number(b.target_budget_inr ?? b.target_budget ?? b.target ?? total));
    const remaining = Math.round(Number(b.remaining_inr ?? (target - total)));

    budgetObj = {
      total,
      target,
      remaining,
      currency: b.currency || 'INR',
      breakdown,
    };
  }

  // 3. Accommodation Recommendation
  let accommodationObj = undefined;
  if (planJson?.accommodation_plan) {
    const rawAcc = planJson.accommodation_plan.replace(/\*\*/g, '').trim();
    const parts = rawAcc.split('–');
    const name = parts[0]?.trim() || 'Selected Hotel Concept';
    const desc = parts.slice(1).join('–').trim() || rawAcc;
    accommodationObj = {
      name,
      description: desc,
      area: planJson.destination || undefined,
    };
  }

  // 4. Flights Recommendation
  let flightsObj = undefined;
  if (planJson?.flight_suggestion || planJson?.route) {
    flightsObj = {
      route: planJson.route || undefined,
      suggested_carrier: planJson.flight_suggestion ? planJson.flight_suggestion.replace(/\*\*/g, '').trim() : undefined,
      approx_price: planJson.budget?.flight_estimate_inr
        ? `₹${Math.round(planJson.budget.flight_estimate_inr).toLocaleString('en-IN')}`
        : undefined,
    };
  }

  // 5. Notes & Advisory
  const notes: string[] = [];
  if (Array.isArray(planJson?.warnings)) {
    notes.push(...planJson.warnings);
  }
  if (planJson?.weather_guidance) {
    notes.push(planJson.weather_guidance);
  }
  if (planJson?.budget?.pricing_note) {
    notes.push(planJson.budget.pricing_note);
  }
  if (Array.isArray(planJson?.assumptions)) {
    notes.push(...planJson.assumptions);
  }

  return {
    trip_id: tripId,
    thread_id: raw.thread_id || threadId,
    revision_number: raw.revision_count ?? 1,
    overview: planJson?.trip_summary || (rawMarkdown ? rawMarkdown.split('\n\n')[0]?.replace(/^#+[^\n]+\n+/, '') : undefined),
    itinerary: itineraryDays,
    accommodation: accommodationObj,
    flights: flightsObj,
    estimated_budget: budgetObj,
    notes: notes.filter(Boolean),
    raw_plan: rawMarkdown,
    plan_json: planJson,
    created_at: new Date().toISOString(),
  };
}

export function normalizeBackendTripDetail(raw: any): TripDetail {
  if (!raw) return {} as TripDetail;
  let meta = raw.metadata || {};
  if (typeof meta === 'string') {
    try {
      meta = JSON.parse(meta);
    } catch {
      meta = {};
    }
  }
  const summary = normalizeBackendTrip(raw);
  // FIX 3: Never synthesize revision identity. Revisions must come exclusively
  // from the backend. Fabricating revision_id values (e.g. rev_<tripId>_01)
  // creates a P0 identity-integrity risk: approval/rejection/authorization
  // endpoints require real UUID revision IDs and will reject or mis-route
  // requests that carry a frontend-invented ID.
  const revisions: TripRevision[] = Array.isArray(raw.revisions) && raw.revisions.length > 0
    ? raw.revisions
    : [];

  const plan = raw.plan
    ? normalizeBackendPlanResponse(raw.plan, summary.trip_id, summary.thread_id || raw.thread_id || `th_${summary.trip_id}`)
    : meta?.plan
    ? normalizeBackendPlanResponse(meta.plan, summary.trip_id, summary.thread_id || meta?.thread_id || `th_${summary.trip_id}`)
    : undefined;

  const selectedFlightOffer = raw.selected_flight_offer || meta.selected_flight || (meta.selected_flight_snapshot_id ? { id: meta.selected_flight_snapshot_id, snapshot_id: meta.selected_flight_snapshot_id, offer_id: meta.selected_flight_snapshot_id } : undefined);
  const selectedHotelOffer = raw.selected_hotel_offer || meta.selected_hotel || (meta.selected_hotel_snapshot_id ? { id: meta.selected_hotel_snapshot_id, snapshot_id: meta.selected_hotel_snapshot_id } : undefined);

  return {
    ...summary,
    revisions,
    current_revision: raw.current_revision || revisions[revisions.length - 1],
    is_approved: summary.status === 'approved',
    selected_flight_offer: selectedFlightOffer,
    selected_hotel_offer: selectedHotelOffer,
    plan,
  };
}

export class ApiClient {
  private getBaseUrl(): string {
    const custom = storage.getCustomApiUrl();
    if (custom) return custom.replace(/\/$/, '');
    const envUrl = (typeof import.meta !== 'undefined' && (import.meta as any)?.env)
      ? ((import.meta as any).env.VITE_API_BASE_URL || (import.meta as any).env.VITE_API_URL)
      : undefined;
    if (envUrl) return envUrl.replace(/\/$/, '');
    return '';
  }

  private isDemoMode(): boolean {
    if (typeof localStorage === 'undefined') return false;
    return localStorage.getItem('trusttrip_force_demo_mode') === 'true';
  }

  public setDemoMode(enabled: boolean) {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem('trusttrip_force_demo_mode', enabled ? 'true' : 'false');
  }

  public getDemoModeState(): boolean {
    return this.isDemoMode();
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const token = storage.getToken();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(options.headers as Record<string, string> || {}),
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const baseUrl = this.getBaseUrl();
    const url = `${baseUrl}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;

    try {
      const response = await fetch(url, {
        ...options,
        headers,
      });

      if (response.status === 401) {
        if (endpoint !== '/api/auth/login' && endpoint !== '/api/auth/signup') {
          if (unauthorizedHandler) {
            unauthorizedHandler();
          }
          throw {
            statusCode: 401,
            message: 'Your session has expired. Please sign in again.',
            code: 'UNAUTHORIZED',
          } as ApiError;
        }
      }

      if (!response.ok) {
        let errorData: any;
        try {
          errorData = await response.json();
        } catch {
          errorData = { detail: response.statusText };
        }

        let message = 'An unexpected error occurred';
        if (typeof errorData?.detail === 'string') {
          message = errorData.detail;
        } else if (typeof errorData?.error === 'string') {
          message = errorData.error;
        } else if (Array.isArray(errorData?.detail)) {
          message = errorData.detail
            .map((d: any) => d.msg || `${d.loc?.join('.')}: invalid`)
            .join('; ');
        } else if (errorData?.message) {
          message = errorData.message;
        }

        const apiError: ApiError = {
          statusCode: response.status,
          message,
          detail: errorData?.detail,
          code: errorData?.code || `HTTP_${response.status}`,
        };
        throw apiError;
      }

      if (response.status === 204) {
        return {} as T;
      }

      return await response.json();
    } catch (err: any) {
      if (err.statusCode) {
        throw err;
      }

      if (
        (this.isDemoMode() && !endpoint.startsWith('/api/auth/')) ||
        endpoint === '/health' ||
        endpoint === '/ready' ||
        endpoint.startsWith('/api/health')
      ) {
        const mockRes = this.handleMockFallback<T>(endpoint, options);
        if (mockRes !== null) {
          return mockRes;
        }
      }

      throw {
        statusCode: 503,
        message: 'Unable to reach the TrustTrip backend service. Please check your connection or switch to Demo Mode.',
        code: 'NETWORK_ERROR',
        detail: err.message,
      } as ApiError;
    }
  }

  private handleMockFallback<T>(endpoint: string, options: RequestInit): T | null {
    const method = (options.method || 'GET').toUpperCase();

    if (
      endpoint === '/health' ||
      endpoint === '/ready' ||
      endpoint === '/api/health' ||
      endpoint === '/api/ready'
    ) {
      return {
        status: 'healthy',
        service: 'TrustTrip FastAPI Backend (Simulated / Local Mode)',
        version: '1.2.0',
        timestamp: new Date().toISOString(),
      } as unknown as T;
    }



    if (endpoint === '/api/trips' && method === 'GET') {
      return getMockTrips() as unknown as T;
    }

    if (endpoint === '/api/trips' && method === 'POST') {
      const body: CreateTripRequest = options.body ? JSON.parse(options.body as string) : {};
      const newTripId = `trp_${Date.now().toString(36)}`;
      const newThreadId = `th_${Date.now().toString(36)}`;
      const newRevisionId = `rev_${Date.now().toString(36)}_01`;

      const newTrip: TripSummary = {
        trip_id: newTripId,
        title: `${body.destination} Exploration`,
        origin: body.origin,
        destination: body.destination,
        start_date: body.start_date,
        end_date: body.end_date,
        status: 'planning',
        active_revision_number: 1,
        active_revision_id: newRevisionId,
        thread_id: newThreadId,
        travelers_count: body.travelers_count || 1,
        budget: body.budget,
        currency: body.currency || 'USD',
        preferences: body.preferences || [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      const trips = getMockTrips();
      saveMockTrips([newTrip, ...trips]);
      return newTrip as unknown as T;
    }

    if (endpoint.startsWith('/api/trips/') && !endpoint.endsWith('/revisions') && method === 'GET') {
      const tripId = endpoint.split('/')[3];
      const trips = getMockTrips();
      const summary = trips.find((t) => t.trip_id === tripId) || {
        trip_id: tripId,
        title: 'Trip Workspace',
        origin: 'San Francisco (SFO)',
        destination: 'Destination',
        start_date: '2026-10-14',
        end_date: '2026-10-24',
        status: 'planning',
        active_revision_number: 1,
        thread_id: `th_${tripId}`,
        travelers_count: 2,
        budget: 3500,
        currency: 'USD',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      const existingPlan = getMockPlan(tripId);
      const selections = getMockSelections(tripId);
      const revisions = getMockTripRevisions(tripId);
      const currentRevision = revisions[revisions.length - 1];

      const detail: TripDetail = {
        ...summary,
        origin: currentRevision?.origin || summary.origin,
        destination: currentRevision?.destination || summary.destination,
        start_date: currentRevision?.start_date || summary.start_date,
        end_date: currentRevision?.end_date || summary.end_date,
        travelers_count: currentRevision?.travelers_count ?? summary.travelers_count,
        budget: currentRevision?.budget ?? summary.budget,
        active_revision_number: currentRevision?.revision_number || summary.active_revision_number || 1,
        active_revision_id: currentRevision?.revision_id || summary.active_revision_id,
        revisions: revisions,
        current_revision: currentRevision,
        is_approved: summary.status === 'approved',
        plan: existingPlan || undefined,
        selected_flight_offer: selections.flight,
        selected_hotel_offer: selections.hotel,
      };

      return detail as unknown as T;
    }

    if (endpoint.startsWith('/api/trips/') && endpoint.endsWith('/select-offer') && method === 'POST') {
      const parts = endpoint.split('/');
      const tripId = parts[3];
      const body: TripOfferSelection = options.body ? JSON.parse(options.body as string) : {};

      if (body.offer_type === 'flight' && body.metadata) {
        saveMockFlightSelection(tripId, body.metadata as unknown as FlightOffer);
      } else if (body.offer_type === 'hotel' && body.metadata) {
        saveMockHotelSelection(tripId, body.metadata as unknown as HotelOffer);
      }

      const trips = getMockTrips();
      const updatedTrips = trips.map((t) => {
        if (t.trip_id === tripId) {
          return {
            ...t,
            active_revision_number: (t.active_revision_number || 1) + 1,
            updated_at: new Date().toISOString(),
          };
        }
        return t;
      });
      saveMockTrips(updatedTrips);

      return this.handleMockFallback<T>(`/api/trips/${tripId}`, { method: 'GET' });
    }

    if (endpoint === '/api/flights/search-flexible' && method === 'POST') {
      const body: FlightSearchRequest = options.body ? JSON.parse(options.body as string) : {};
      const results = generateMockFlights(
        body.origin || 'SFO',
        body.destination || 'KIX',
        body.departure_date || '2026-10-14',
        body.return_date,
        body.cabin_class as any,
        body.passengers || 1
      );
      return {
        search_id: `fl_srch_${Date.now().toString(36)}`,
        offers: results,
      } as unknown as T;
    }

    if (endpoint === '/api/hotels/search' && method === 'POST') {
      const body: HotelSearchRequest = options.body ? JSON.parse(options.body as string) : {};
      const results = generateMockHotels(
        body.city || 'Kyoto',
        body.check_in_date || '2026-10-14',
        body.check_out_date || '2026-10-20',
        body.guests || 2
      );
      return {
        hotels: results,
        offers: results,
      } as unknown as T;
    }

    if (endpoint === '/api/bookings/flight/prepare' && method === 'POST') {
      const body = options.body ? JSON.parse(options.body as string) : {};
      const intent = createMockBookingIntent(
        body.trip_id || 'trip_demo',
        body.offer_id || 'fl_off_demo',
        body.snapshot_id || 'fl_snap_demo',
        body.amount || '860',
        body.currency || 'USD'
      );
      return intent as unknown as T;
    }

    if (endpoint === '/api/bookings/flight/revalidate' && method === 'POST') {
      const body = options.body ? JSON.parse(options.body as string) : {};
      const intent = revalidateMockBookingIntent(body.intent_id || 'bkg_int_demo');
      return intent as unknown as T;
    }

    // --- Part 4: Research Query Fallback ---
    if (endpoint === '/api/research/query' && method === 'POST') {
      const body: ResearchQueryRequest = options.body ? JSON.parse(options.body as string) : { query: '' };
      const response = executeMockResearchQuery(body);
      return response as unknown as T;
    }

    // --- Part 4: Research Documents Fallback ---
    if (endpoint === '/api/research/documents' && (method === 'GET' || !method)) {
      const docs = getMockResearchDocuments();
      return docs as unknown as T;
    }

    if (
      (endpoint === '/api/research/documents' || endpoint === '/api/research/documents/upload') &&
      method === 'POST'
    ) {
      let docName = 'Travel_Research_Notes.pdf';
      let docTitle = 'Travel Research Notes';
      let docSize = 1800000;
      if (typeof options.body === 'string') {
        try {
          const parsed = JSON.parse(options.body);
          if (parsed.title) docTitle = parsed.title;
          if (parsed.filename) docName = parsed.filename;
        } catch {}
      }
      const newDoc = saveMockResearchDocument({
        name: docName,
        title: docTitle,
        size: docSize,
        type: 'pdf',
      });
      return newDoc as unknown as T;
    }

    if (endpoint.startsWith('/api/research/documents/') && method === 'DELETE') {
      const docId = endpoint.split('/')[4];
      deleteMockResearchDocument(docId);
      return { success: true } as unknown as T;
    }

    // --- Part 4: Research Session Fallback ---
    if (endpoint.startsWith('/api/research/sessions/') && (method === 'GET' || !method)) {
      const sessionId = endpoint.split('/')[4];
      const session = getMockResearchSession(sessionId);
      return session as unknown as T;
    }

    // --- Part 4: Trip Revisions Fallback ---
    if (endpoint.startsWith('/api/trips/') && endpoint.endsWith('/revisions') && (method === 'GET' || !method)) {
      const tripId = endpoint.split('/')[3];
      const revs = getMockTripRevisions(tripId);
      return revs as unknown as T;
    }

    if (endpoint.startsWith('/api/trips/') && endpoint.endsWith('/revisions') && method === 'POST') {
      const tripId = endpoint.split('/')[3];
      const body: CreateRevisionRequest = options.body ? JSON.parse(options.body as string) : { expected_base_revision: 1, changes: {} };
      const created = createMockTripRevision(tripId, body);

      // Also update the active revision in mock trips
      const trips = getMockTrips();
      const updated = trips.map((t) => {
        if (t.trip_id === tripId) {
          return {
            ...t,
            origin: created.origin,
            destination: created.destination,
            start_date: created.start_date,
            end_date: created.end_date,
            travelers_count: created.travelers_count,
            budget: created.budget,
            active_revision_number: created.revision_number,
            active_revision_id: created.revision_id,
            updated_at: new Date().toISOString(),
          };
        }
        return t;
      });
      saveMockTrips(updated);

      return created as unknown as T;
    }

    // --- Part 5B: Trip Approval ---
    if (endpoint.startsWith('/api/trips/') && endpoint.endsWith('/approve') && method === 'POST') {
      const tripId = endpoint.split('/')[3];
      const body = options.body ? JSON.parse(options.body as string) : {};
      const trips = getMockTrips();
      const trip = trips.find((t) => t.trip_id === tripId);

      if (trip && body.expected_revision && trip.active_revision_number !== body.expected_revision) {
        throw {
          statusCode: 409,
          message: 'Revision conflict. Your baseline plan does not match the active server revision.',
          code: 'REVISION_CONFLICT',
          detail: {
            server_revision: trip.active_revision_number,
            requested_revision: body.expected_revision,
          },
        } as ApiError;
      }

      const approvedAt = new Date().toISOString();
      const updatedTrips = trips.map((t) => {
        if (t.trip_id === tripId) {
          return {
            ...t,
            status: 'approved' as const,
            active_revision_number: (t.active_revision_number || 1) + 1,
            updated_at: approvedAt,
          };
        }
        return t;
      });
      saveMockTrips(updatedTrips);

      return {
        trip_id: tripId,
        status: 'approved',
        approved_at: approvedAt,
        active_revision_number: ((trip?.active_revision_number || 1) + 1),
        is_approved: true,
      } as unknown as T;
    }

    // Section 9: Booking authorization is a persisted security gate; mock fallback is forbidden for /booking-approval.

    if (endpoint.startsWith('/api/trips/') && endpoint.includes('/booking-eligibility') && method === 'GET') {
      const tripId = endpoint.split('/')[3].split('?')[0];
      const trips = getMockTrips();
      const trip = trips.find((t) => t.trip_id === tripId);
      const isApproved = trip?.status === 'approved';
      const isAuth = !!trip?.is_booking_authorized;

      return {
        success: true,
        eligibility: {
          is_eligible: isApproved && isAuth,
          trip_id: tripId,
          revision_id: trip?.active_revision_id,
          revision_number: trip?.active_revision_number,
          is_approved: isApproved,
          is_rejected: false,
          is_authorized: isAuth,
          authorized_at: trip?.booking_authorization?.authorized_at,
          reasons: isApproved && isAuth ? [] : ['Plan must be approved and authorized for booking'],
        },
      } as unknown as T;
    }

    // --- Part 5B: Flight Price Change Acknowledgment ---
    if (endpoint.includes('/bookings/flight/') && endpoint.endsWith('/acknowledge-price') && method === 'POST') {
      const parts = endpoint.split('/');
      const intentId = parts.length > 4 ? parts[4] : (options.body ? JSON.parse(options.body as string).intent_id : 'bkg_int_demo');
      const intent = revalidateMockBookingIntent(intentId);
      intent.price_change_acknowledged = true;
      intent.status = 'revalidated';
      return intent as unknown as T;
    }

    return null;
  }

  // --- Auth Canonical Endpoints ---
  async login(credentials: LoginCredentials): Promise<AuthResponse> {
    return await this.request<AuthResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(credentials),
    });
  }

  async signup(credentials: SignupCredentials): Promise<AuthResponse> {
    return await this.request<AuthResponse>('/api/auth/signup', {
      method: 'POST',
      body: JSON.stringify(credentials),
    });
  }

  async getMe(): Promise<User> {
    const response = await this.request<{ success: boolean; user: User }>('/api/auth/me', {
      method: 'GET',
    });
    return response.user;
  }

  // --- Health Canonical Endpoints ---
  async checkHealth(): Promise<BackendHealth> {
    try {
      return await this.request<BackendHealth>('/health', { method: 'GET' });
    } catch {
      return {
        status: 'offline',
        service: 'FastAPI Backend Unavailable',
      };
    }
  }

  async checkReady(): Promise<BackendHealth> {
    try {
      return await this.request<BackendHealth>('/ready', { method: 'GET' });
    } catch {
      return {
        status: 'offline',
        service: 'FastAPI Backend Not Ready',
      };
    }
  }

  // --- Trips Canonical Endpoints (Section 3 Integration) ---
  async getTrips(): Promise<TripSummary[]> {
    try {
      const response = await this.request<any>('/api/trips', {
        method: 'GET',
      });
      if (response && Array.isArray(response.trips)) {
        return response.trips.map(normalizeBackendTrip);
      }
      if (Array.isArray(response)) {
        return response.map(normalizeBackendTrip);
      }
      return [];
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR' && this.isDemoMode()) {
        const mock = this.handleMockFallback<TripSummary[]>('/api/trips', { method: 'GET' });
        if (mock) return mock.map(normalizeBackendTrip);
      }
      throw err;
    }
  }

  async createTrip(data: CreateTripRequest): Promise<TripSummary> {
    const payload = {
      title: data.notes?.trim() ? `${data.destination} - ${data.notes.slice(0, 30)}` : `${data.destination} Exploration`,
      destination: data.destination,
      origin: data.origin,
      status: 'draft',
      metadata: {
        start_date: data.start_date,
        end_date: data.end_date,
        travelers_count: data.travelers_count || 1,
        budget: data.budget,
        currency: data.currency || 'INR',
        preferences: data.preferences || [],
        notes: data.notes,
        flight_class: data.flight_class,
        hotel_stars: data.hotel_stars,
      },
    };

    try {
      const response = await this.request<any>('/api/trips', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const rawTrip = response.trip || response;
      return normalizeBackendTrip(rawTrip);
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR' && this.isDemoMode()) {
        const mock = this.handleMockFallback<TripSummary>('/api/trips', {
          method: 'POST',
          body: JSON.stringify(data),
        });
        if (mock) return normalizeBackendTrip(mock);
      }
      throw err;
    }
  }

  async getTrip(tripId: string): Promise<TripDetail> {
    try {
      const response = await this.request<any>(`/api/trips/${tripId}`, {
        method: 'GET',
      });
      const rawTrip = response.trip || response;
      return normalizeBackendTripDetail(rawTrip);
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR' && this.isDemoMode()) {
        const mock = this.handleMockFallback<TripDetail>(`/api/trips/${tripId}`, {
          method: 'GET',
        });
        if (mock) return normalizeBackendTripDetail(mock);
      }
      throw err;
    }
  }

  async getTripBookings(tripId: string): Promise<any> {
    try {
      const response = await this.request<any>(`/api/trips/${encodeURIComponent(tripId)}/bookings`, {
        method: 'GET',
      });
      return response;
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR' && this.isDemoMode()) {
        return { success: true, trip_id: tripId, flight: null, hotel: null };
      }
      throw err;
    }
  }

  async createThread(tripId: string): Promise<{ thread_id: string; trip_id: string }> {
    try {
      const response = await this.request<any>(
        `/api/trips/${tripId}/threads`,
        {
          method: 'POST',
          body: JSON.stringify({ metadata: {} }),
        }
      );
      const thread = response.thread || response;
      return {
        thread_id: thread?.id || thread?.thread_id || `th_${Date.now().toString(36)}`,
        trip_id: thread?.trip_id || tripId,
      };
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR' && this.isDemoMode()) {
        return {
          thread_id: `th_${Date.now().toString(36)}`,
          trip_id: tripId,
        };
      }
      throw err;
    }
  }

  // --- Travel Planning Canonical Endpoint (Section 4 Integration) ---
  async planTravel(request: TravelPlanRequest): Promise<TravelPlanResponse> {
    const userMessage = (request.message || request.query || '').trim();
    if (!userMessage) {
      throw {
        statusCode: 400,
        message: 'Please share your desired trip style, pace, or destinations to shape the itinerary.',
        code: 'VALIDATION_ERROR',
      } as ApiError;
    }

    // Ensure thread foundation exists for this trip
    let threadId = request.thread_id;
    if (!threadId && request.trip_id) {
      try {
        const threadResult = await this.createThread(request.trip_id);
        threadId = threadResult.thread_id;
      } catch (threadErr: any) {
        if (threadErr.statusCode === 401) throw threadErr;
        // If thread creation had an issue but not 401, proceed; backend planner auto-creates thread if omitted
      }
    }

    const payload = {
      message: userMessage,
      thread_id: threadId || undefined,
      trip_id: request.trip_id || undefined,
    };

    try {
      const response = await this.request<any>('/api/travel', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (response && response.success === false) {
        throw {
          statusCode: 400,
          message: response.error || response.detail || 'Travel planner was unable to generate an itinerary for these requirements.',
          code: 'PLANNING_FAILED',
        } as ApiError;
      }

      return normalizeBackendPlanResponse(
        response,
        request.trip_id,
        response.thread_id || threadId || `user_${request.trip_id}`
      );
    } catch (err: any) {
      if (err.statusCode === 401) {
        throw err;
      }
      if (err.statusCode === 409) {
        throw {
          statusCode: 409,
          message: 'Your trip has changed. Refresh the latest plan and try again.',
          code: 'REVISION_CONFLICT',
        } as ApiError;
      }
      if (err.statusCode === 403) {
        throw {
          statusCode: 403,
          message: 'You do not have permission to plan for this trip.',
          code: 'FORBIDDEN',
        } as ApiError;
      }
      if (err.statusCode === 422) {
        throw {
          statusCode: 422,
          message: 'The travel planning request contained invalid fields. Please review your input and try again.',
          code: 'VALIDATION_ERROR',
        } as ApiError;
      }
      if (err.statusCode === 503) {
        throw {
          statusCode: 503,
          message: err.message || err.detail || 'Upstream AI travel planning service is temporarily overloaded. Please try again shortly.',
          code: 'SERVICE_UNAVAILABLE',
          retryable: true,
        } as ApiError;
      }
      // Never silently fallback to fabricated itineraries on travel planning failure
      throw err;
    }
  }

  // --- Flight Canonical Endpoints (Part 3) ---
  // --- Flight Canonical Endpoints (Section 5A / Section 5B / 5C) ---
  async searchFlights(request: FlightSearchRequest): Promise<FlightOffer[]> {
    try {
      // 1. If trip_id is provided, use the authoritative Section 5A contract
      if (request.trip_id) {
        const response = await this.request<FlightSearchResponse>('/api/flights/search', {
          method: 'POST',
          body: JSON.stringify({
            trip_id: request.trip_id,
            cabin_class: request.cabin_class || 'economy',
            max_connections: request.max_connections ?? 2,
          }),
        });

        if (response?.offers && Array.isArray(response.offers)) {
          return response.offers.map((off) => toFlightOffer(off));
        }
        return [];
      }

      // 2. Fallback to flexible search for standalone search queries without a trip_id
      const response = await this.request<{ offers?: (FlightOffer | NormalizedFlightOffer)[] } | FlightOffer[]>(
        '/api/flights/search-flexible',
        {
          method: 'POST',
          body: JSON.stringify(request),
        }
      );
      if (Array.isArray(response)) {
        return response;
      }
      const rawOffers = response?.offers || [];
      return rawOffers.map((o: any) => (o.offer_id ? toFlightOffer(o) : o));
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR' || err.status === 400 || err.status === 404 || err.status === 422 || err.status === 500) {
        const mock = this.handleMockFallback<{ offers: FlightOffer[] }>(
          '/api/flights/search-flexible',
          {
            method: 'POST',
            body: JSON.stringify(request),
          }
        );
        if (mock?.offers) return mock.offers;
      }
      throw err;
    }
  }

  /**
   * Section 5A Canonical Flight Search for a specific Trip.
   * Returns authoritative FlightSearchResponse including availability_state ('available' | 'empty' | 'unavailable' | 'error').
   */
  async searchFlightsForTrip(
    tripId: string,
    options?: { cabin_class?: string; max_connections?: number }
  ): Promise<FlightSearchResponse> {
    const payload = {
      trip_id: tripId,
      cabin_class: options?.cabin_class || 'economy',
      max_connections: options?.max_connections ?? 2,
    };
    return await this.request<FlightSearchResponse>('/api/flights/search', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  // --- Canonical Offer Selection (Part 3) ---
  async selectTripOffer(tripId: string, selection: TripOfferSelection): Promise<TripDetail> {
    try {
      const response = await this.request<any>(`/api/trips/${tripId}/select-offer`, {
        method: 'POST',
        body: JSON.stringify(selection),
      });
      const rawTrip = response?.trip || response;
      return normalizeBackendTripDetail(rawTrip);
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<TripDetail>(`/api/trips/${tripId}/select-offer`, {
          method: 'POST',
          body: JSON.stringify(selection),
        });
        if (mock) return normalizeBackendTripDetail(mock);
      }
      throw err;
    }
  }

  // --- Hotel Canonical Endpoints (Part 3) ---
  async searchHotels(request: HotelSearchRequest): Promise<HotelOffer[]> {
    try {
      const response = await this.request<{ hotels?: HotelOffer[]; offers?: HotelOffer[] } | HotelOffer[]>(
        '/api/hotels/search',
        {
          method: 'POST',
          body: JSON.stringify(request),
        }
      );
      if (Array.isArray(response)) {
        return response;
      }
      return response?.hotels || response?.offers || [];
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<{ hotels: HotelOffer[] }>(
          '/api/hotels/search',
          {
            method: 'POST',
            body: JSON.stringify(request),
          }
        );
        if (mock?.hotels) return mock.hotels;
      }
      throw err;
    }
  }

  // --- Flight Booking & Handoff Foundation (Part 3 / Phase 9) ---
  async prepareFlightBooking(request: {
    trip_id?: string;
    offer_id: string;
    snapshot_id: string;
    amount?: string;
    currency?: string;
  }): Promise<BookingIntent> {
    try {
      return await this.request<BookingIntent>('/api/bookings/flight/prepare', {
        method: 'POST',
        body: JSON.stringify(request),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<BookingIntent>('/api/bookings/flight/prepare', {
          method: 'POST',
          body: JSON.stringify(request),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  async revalidateFlightBooking(
    intentId: string,
    offerId: string,
    snapshotId: string
  ): Promise<BookingIntent> {
    try {
      return await this.request<BookingIntent>('/api/bookings/flight/revalidate', {
        method: 'POST',
        body: JSON.stringify({ intent_id: intentId, offer_id: offerId, snapshot_id: snapshotId }),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<BookingIntent>('/api/bookings/flight/revalidate', {
          method: 'POST',
          body: JSON.stringify({ intent_id: intentId, offer_id: offerId, snapshot_id: snapshotId }),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Section 5B & 5C Authoritative Flight Booking Endpoints ---

  /**
   * Select a persisted flight offer snapshot and initialize a booking intent.
   * POST /api/flights/booking/select
   */
  async selectFlightBooking(
    request: FlightBookingSelectionRequest
  ): Promise<FlightBookingSelectionResponse> {
    return await this.request<FlightBookingSelectionResponse>('/api/flights/booking/select', {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  /**
   * Revalidate live provider price and availability.
   * POST /api/flights/booking/{booking_id}/revalidate
   */
  async revalidateFlight(
    bookingId: string,
    acceptedPrice?: number
  ): Promise<FlightBookingRevalidationResponse> {
    return await this.request<FlightBookingRevalidationResponse>(
      `/api/flights/booking/${bookingId}/revalidate`,
      {
        method: 'POST',
        body: JSON.stringify({
          booking_id: bookingId,
          ...(acceptedPrice !== undefined ? { accepted_price: acceptedPrice } : {}),
        }),
      }
    );
  }

  /**
   * Record explicit Human-In-The-Loop (HITL) approval for a revalidated booking.
   * POST /api/flights/booking/{booking_id}/approve
   */
  async approveFlight(
    bookingId: string,
    options?: { approved?: boolean; notes?: string }
  ): Promise<FlightBookingApprovalResponse> {
    return await this.request<FlightBookingApprovalResponse>(
      `/api/flights/booking/${bookingId}/approve`,
      {
        method: 'POST',
        body: JSON.stringify({
          booking_id: bookingId,
          approved: options?.approved ?? true,
          ...(options?.notes ? { notes: options.notes } : {}),
        }),
      }
    );
  }

  /**
   * Confirm booking with provider after revalidation and human approval.
   * POST /api/flights/booking/{booking_id}/confirm
   */
  async confirmFlightBooking(
    bookingId: string,
    options?: {
      final_confirmation?: boolean;
      payment?: Record<string, any>;
      payment_mode?: string;
    }
  ): Promise<FlightBookingConfirmationResponse> {
    return await this.request<FlightBookingConfirmationResponse>(
      `/api/flights/booking/${bookingId}/confirm`,
      {
        method: 'POST',
        body: JSON.stringify({
          booking_id: bookingId,
          final_confirmation: options?.final_confirmation ?? true,
          ...(options?.payment ? { payment: options.payment } : {}),
          payment_mode: options?.payment_mode || 'auto',
        }),
      }
    );
  }

  /**
   * Query authoritative flight booking status.
   * GET /api/flights/booking/{booking_id}
   */
  async getFlightBooking(bookingId: string): Promise<FlightBookingDetails> {
    return await this.request<FlightBookingDetails>(`/api/flights/booking/${bookingId}`, {
      method: 'GET',
    });
  }

  // --- Part 4: Research Canonical Endpoints ---
  async researchQuery(request: ResearchQueryRequest): Promise<ResearchResponse> {
    try {
      return await this.request<ResearchResponse>('/api/research/query', {
        method: 'POST',
        body: JSON.stringify(request),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<ResearchResponse>('/api/research/query', {
          method: 'POST',
          body: JSON.stringify(request),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  async listResearchDocuments(): Promise<ResearchDocument[]> {
    try {
      const resp = await this.request<ResearchDocument[] | { documents: ResearchDocument[] }>(
        '/api/research/documents',
        { method: 'GET' }
      );
      if (Array.isArray(resp)) return resp;
      return (resp as any)?.documents || [];
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<ResearchDocument[]>('/api/research/documents', {
          method: 'GET',
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  async uploadResearchDocument(file: File, title?: string): Promise<ResearchDocument> {
    // 10MB limit check as per backend spec
    const MAX_SIZE = 10 * 1024 * 1024;
    if (file.size > MAX_SIZE) {
      throw new Error('Document exceeds the 10 MB maximum upload limit.');
    }

    try {
      const formData = new FormData();
      formData.append('file', file);
      if (title) formData.append('title', title);

      // In real backend, upload might take FormData
      return await this.request<ResearchDocument>('/api/research/documents/upload', {
        method: 'POST',
        body: formData as any,
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<ResearchDocument>('/api/research/documents/upload', {
          method: 'POST',
          body: JSON.stringify({ filename: file.name, title: title || file.name }),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  async deleteResearchDocument(documentId: string): Promise<{ success: boolean }> {
    try {
      return await this.request<{ success: boolean }>(`/api/research/documents/${documentId}`, {
        method: 'DELETE',
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<{ success: boolean }>(
          `/api/research/documents/${documentId}`,
          { method: 'DELETE' }
        );
        if (mock) return mock;
      }
      throw err;
    }
  }

  async getResearchSession(sessionId: string): Promise<ResearchSession> {
    try {
      return await this.request<ResearchSession>(`/api/research/sessions/${sessionId}`, {
        method: 'GET',
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<ResearchSession>(
          `/api/research/sessions/${sessionId}`,
          { method: 'GET' }
        );
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Part 4: Trip Revision Canonical Endpoints ---
  async getTripRevisions(tripId: string): Promise<TripRevision[]> {
    try {
      const res = await this.request<{ success?: boolean; trip_id?: string; revisions?: TripRevision[] } | TripRevision[]>(
        `/api/trips/${tripId}/revisions`,
        { method: 'GET' }
      );
      if (Array.isArray(res)) {
        return res;
      }
      return Array.isArray(res?.revisions) ? res.revisions : [];
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<TripRevision[]>(`/api/trips/${tripId}/revisions`, {
          method: 'GET',
        });
        if (mock) {
          return Array.isArray(mock) ? mock : (mock as any)?.revisions ?? [];
        }
      }
      throw err;
    }
  }

  async createTripRevision(
    tripId: string,
    request: CreateRevisionRequest
  ): Promise<TripRevision> {
    try {
      return await this.request<TripRevision>(`/api/trips/${tripId}/revisions`, {
        method: 'POST',
        body: JSON.stringify(request),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<TripRevision>(`/api/trips/${tripId}/revisions`, {
          method: 'POST',
          body: JSON.stringify(request),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Part 5B & Section 8: Trip Approval & Rejection Canonical Endpoints ---
  async approveTrip(
    tripId: string,
    options: { revision_id: string; expected_revision_number: number; notes?: string }
  ): Promise<{
    trip_id: string;
    status: 'approved';
    review_status?: string;
    revision_id?: string;
    approved_at: string;
    active_revision_number: number;
    is_approved: boolean;
  }> {
    try {
      return await this.request<{
        trip_id: string;
        status: 'approved';
        review_status?: string;
        revision_id?: string;
        approved_at: string;
        active_revision_number: number;
        is_approved: boolean;
      }>(`/api/trips/${tripId}/approve`, {
        method: 'POST',
        body: JSON.stringify(options),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<{
          trip_id: string;
          status: 'approved';
          review_status?: string;
          revision_id?: string;
          approved_at: string;
          active_revision_number: number;
          is_approved: boolean;
        }>(`/api/trips/${tripId}/approve`, {
          method: 'POST',
          body: JSON.stringify(options),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  async rejectTripRevision(
    tripId: string,
    revisionId: string,
    options: { expected_revision_number: number; reason?: string }
  ): Promise<{
    trip_id: string;
    revision_id: string;
    status: 'REJECTED';
    trip_status: 'rejected';
    rejection_id: string;
    reason?: string;
  }> {
    try {
      return await this.request<{
        trip_id: string;
        revision_id: string;
        status: 'REJECTED';
        trip_status: 'rejected';
        rejection_id: string;
        reason?: string;
      }>(`/api/trips/${tripId}/revisions/${revisionId}/reject`, {
        method: 'POST',
        body: JSON.stringify(options),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<{
          trip_id: string;
          revision_id: string;
          status: 'REJECTED';
          trip_status: 'rejected';
          rejection_id: string;
          reason?: string;
        }>(`/api/trips/${tripId}/revisions/${revisionId}/reject`, {
          method: 'POST',
          body: JSON.stringify(options),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Section 9: Booking Approval & Eligibility Canonical Endpoints ---
  async authorizeBooking(
    tripId: string,
    options: { revision_id: string; expected_revision_number: number; notes?: string }
  ): Promise<{
    success: boolean;
    trip_id: string;
    revision_id: string;
    revision_number: number;
    authorization_id: string;
    authorized_at: string;
    is_authorized: boolean;
    trip?: TripSummary | TripDetail;
  }> {
    // Section 9: Authorization is a persisted security gate.
    // Real API/network failures MUST NOT fall back to a mock that fabricates authorization success.
    return await this.request<{
      success: boolean;
      trip_id: string;
      revision_id: string;
      revision_number: number;
      authorization_id: string;
      authorized_at: string;
      is_authorized: boolean;
      trip?: TripSummary | TripDetail;
    }>(`/api/trips/${tripId}/booking-approval`, {
      method: 'POST',
      body: JSON.stringify(options),
    });
  }

  async getBookingEligibility(
    tripId: string,
    revisionId?: string,
    expectedRevisionNumber?: number
  ): Promise<BookingEligibilityInfo> {
    try {
      const q = new URLSearchParams();
      if (revisionId) q.set('revision_id', revisionId);
      if (expectedRevisionNumber !== undefined) q.set('expected_revision_number', String(expectedRevisionNumber));
      const url = `/api/trips/${tripId}/booking-eligibility${q.toString() ? `?${q.toString()}` : ''}`;
      const res = await this.request<{ success: boolean; eligibility: BookingEligibilityInfo }>(url, {
        method: 'GET',
      });
      return res.eligibility;
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<BookingEligibilityInfo>(`/api/trips/${tripId}/booking-eligibility`, {
          method: 'GET',
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Part 5B: Flight Booking Intent Price Acknowledgment ---
  async acknowledgePriceChange(
    intentId: string,
    acknowledgedAmount: string
  ): Promise<BookingIntent> {
    try {
      return await this.request<BookingIntent>(`/api/bookings/flight/${intentId}/acknowledge-price`, {
        method: 'POST',
        body: JSON.stringify({ intent_id: intentId, acknowledged_amount: acknowledgedAmount }),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<BookingIntent>(
          `/api/bookings/flight/${intentId}/acknowledge-price`,
          {
            method: 'POST',
            body: JSON.stringify({ intent_id: intentId, acknowledged_amount: acknowledgedAmount }),
          }
        );
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Part 5B: Flight Checkout Handoff ---
  async handoffFlightBooking(
    intentId: string,
    returnUrl?: string
  ): Promise<{
    intent_id: string;
    handoff_url: string;
    provider: string;
    expires_at: string;
    status: string;
  }> {
    try {
      return await this.request<{
        intent_id: string;
        handoff_url: string;
        provider: string;
        expires_at: string;
        status: string;
      }>(`/api/bookings/flight/${intentId}/handoff`, {
        method: 'POST',
        body: JSON.stringify({ intent_id: intentId, return_url: returnUrl }),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<{
          intent_id: string;
          handoff_url: string;
          provider: string;
          expires_at: string;
          status: string;
        }>(`/api/bookings/flight/${intentId}/handoff`, {
          method: 'POST',
          body: JSON.stringify({ intent_id: intentId, return_url: returnUrl }),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Part 5B: Booking Status Retrieval ---
  async getBooking(bookingId: string): Promise<{
    booking_id: string;
    trip_id: string;
    type: 'flight' | 'hotel' | 'bundle';
    status: 'confirmed' | 'pending' | 'failed' | 'cancelled';
    confirmation_reference: string;
    ticket_number?: string;
    confirmed_at: string;
    total_amount: string;
    currency: string;
    carrier_name?: string;
    flight_number?: string;
    origin?: string;
    destination?: string;
    cabin_class?: string;
    property_name?: string;
  }> {
    return await this.request<any>(`/api/bookings/${bookingId}`, {
      method: 'GET',
    });
  }

  // --- Part 5B: Hotel Prebooking ---
  async prebookHotel(request: HotelPrebookRequest): Promise<HotelPrebookResponse> {
    try {
      return await this.request<HotelPrebookResponse>('/api/hotels/prebook', {
        method: 'POST',
        body: JSON.stringify(request),
      });
    } catch (err: any) {
      if (err.code === 'NETWORK_ERROR') {
        const mock = this.handleMockFallback<HotelPrebookResponse>('/api/hotels/prebook', {
          method: 'POST',
          body: JSON.stringify(request),
        });
        if (mock) return mock;
      }
      throw err;
    }
  }

  // --- Section 10: Authoritative Hotel Payment Endpoints ---
  async createHotelPaymentOrder(request: {
    offer?: any;
    prebook?: any;
    holder: any;
    guests: any[];
    trip_id: string;
    revision_id: string;
    expected_revision_number: number;
    payment_idempotency_key: string;
    customer_ref?: string;
    final_confirmation: boolean;
    snapshot_id?: string;
  }): Promise<{
    success: boolean;
    transaction_id: string;
    razorpay_order_id?: string;
    amount_inr: number;
    currency: string;
    key_id?: string;
    provider?: string;
    hotel_name?: string;
    idempotent_replay?: boolean;
    snapshot_id?: string;
  }> {
    if (request.snapshot_id) {
      return await this.request<any>('/api/hotel-payments/create-order-from-snapshot', {
        method: 'POST',
        body: JSON.stringify({
          snapshot_id: request.snapshot_id,
          trip_id: request.trip_id,
          revision_id: request.revision_id,
          expected_revision_number: request.expected_revision_number,
          payment_idempotency_key: request.payment_idempotency_key,
          holder: request.holder,
          guests: request.guests,
          customer_ref: request.customer_ref,
          final_confirmation: request.final_confirmation,
        }),
      });
    }
    return await this.request<any>('/api/hotel-payments/create-order', {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  async verifyAndBookHotel(request: {
    transaction_id: string;
    razorpay_order_id: string;
    razorpay_payment_id: string;
    razorpay_signature: string;
    offer: any;
    prebook: any;
    holder: any;
    guests: any[];
    trip_id: string;
    revision_id: string;
    expected_revision_number: number;
    customer_ref?: string;
    final_confirmation: boolean;
  }): Promise<{
    success: boolean;
    transaction_id: string;
    payment_captured: boolean;
    booking: any;
    amount_inr: number;
    already_booked?: boolean;
    pdf_available?: boolean;
    requires_refund?: boolean;
    error?: string;
  }> {
    return await this.request<any>('/api/hotel-payments/verify-and-book', {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  // --- Part 5B: Booking Cancellation Intent (Uncompleted drafts) ---
  async cancelBookingIntent(
    bookingId: string,
    request: { reason: string; contact_email: string; cancel_scope?: 'all' | 'flight' | 'hotel' }
  ): Promise<{
    ticket_id?: string;
    booking_id: string;
    status: string;
    message: string;
    submitted_at: string;
  }> {
    return await this.request<any>(`/api/bookings/${bookingId}/cancel-intent`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  // --- Wave 3C: Canonical Trip & Booking Cancellation ---
  async cancelTripBookings(
    tripId: string,
    request: { booking_type: 'all' | 'flight' | 'hotel'; reason: string; contact_email?: string }
  ): Promise<{
    success: boolean;
    trip_id: string;
    scope: string;
    status: string;
    message: string;
    components?: Record<string, any>;
  }> {
    return await this.request<any>(`/api/trips/${tripId}/cancel`, {
      method: 'POST',
      body: JSON.stringify(request),
    });
  }

  async cancellationQuote(transactionId: string): Promise<any> {
    return await this.request<any>('/api/trips/cancel/quote', {
      method: 'POST',
      body: JSON.stringify({ transaction_id: transactionId, final_confirmation: true }),
    });
  }

  async cancellationConfirm(transactionId: string, cancellationId: string): Promise<any> {
    return await this.request<any>('/api/trips/cancel/confirm', {
      method: 'POST',
      body: JSON.stringify({ transaction_id: transactionId, cancellation_id: cancellationId, final_confirmation: true }),
    });
  }

  async cancelHotelBooking(
    transactionId: string,
    tripId?: string,
    reason?: string,
    contactEmail?: string
  ): Promise<any> {
    return await this.request<any>('/api/trips/cancel/hotel', {
      method: 'POST',
      body: JSON.stringify({ transaction_id: transactionId, trip_id: tripId, reason, contact_email: contactEmail }),
    });
  }

  // --- Wave 3C: Authoritative Binary Travel PDF Stream ---
  async downloadTravelPlanPdfBlob(tripId: string): Promise<Blob> {
    const token = storage.getToken();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/pdf, application/json',
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const baseUrl = this.getBaseUrl();
    const url = `${baseUrl}/api/travel/pdf`;
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({ trip_id: tripId }),
    });
    if (!response.ok) {
      let message = 'Unable to generate travel PDF.';
      try {
        const errorData = await response.json();
        message = errorData.detail || errorData.error || message;
      } catch {
        // ignore
      }
      throw new Error(message);
    }
    return await response.blob();
  }

  async generateTravelPdf(tripId: string): Promise<Blob> {
    return await this.downloadTravelPlanPdfBlob(tripId);
  }

  // --- Wave 3D-A: Authenticated Binary Flight Ticket PDF Stream ---
  async downloadTicketPdfBlob(transactionId: string): Promise<Blob> {
    const token = storage.getToken();
    const headers: Record<string, string> = {
      Accept: 'application/pdf, application/json',
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const baseUrl = this.getBaseUrl();
    const url = `${baseUrl}/api/trips/${encodeURIComponent(transactionId)}/ticket.pdf`;
    const response = await fetch(url, {
      method: 'GET',
      headers,
    });
    if (!response.ok) {
      let message = 'Unable to download flight ticket PDF.';
      try {
        const errorData = await response.json();
        message = errorData.detail || errorData.error || message;
      } catch {
        // ignore
      }
      throw new Error(message);
    }
    return await response.blob();
  }

  // --- Wave 3D-A: Authenticated Binary Hotel Voucher PDF Stream ---
  async downloadHotelVoucherPdfBlob(transactionId: string): Promise<Blob> {
    const token = storage.getToken();
    const headers: Record<string, string> = {
      Accept: 'application/pdf, application/json',
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const baseUrl = this.getBaseUrl();
    const url = `${baseUrl}/api/trips/${encodeURIComponent(transactionId)}/hotel.pdf`;
    const response = await fetch(url, {
      method: 'GET',
      headers,
    });
    if (!response.ok) {
      let message = 'Unable to download hotel voucher PDF.';
      try {
        const errorData = await response.json();
        message = errorData.detail || errorData.error || message;
      } catch {
        // ignore
      }
      throw new Error(message);
    }
    return await response.blob();
  }
}

export const api = new ApiClient();
export const apiClient = api;
