import React, { useState } from 'react';
import { Compass, Sparkles, RefreshCw, AlertCircle, ArrowRight } from 'lucide-react';
import { TripDetail } from '../../types/trip';
import { api } from '../../lib/apiClient';
import { useNotification } from '../../context/NotificationContext';
import { PlannerInput } from './PlannerInput';
import { ItineraryTimeline } from '../travel/ItineraryTimeline';
import { Button } from '../common/Button';
import { getDestinationImage } from '../../lib/destinationImages';

interface PlanningWorkspaceProps {
  trip: TripDetail;
  onUpdateTrip: (updated: TripDetail) => void;
}

/**
 * Validate destination consistency between generated plan and canonical trip.
 *
 * Hierarchy of validation:
 * 1. Direct lexical matching: planDest contains keywords from canonicalDest or vice-versa.
 * 2. Authoritative backend canonical resolution matching:
 *    When the backend resolves a region/state to a canonical gateway city/airport
 *    (e.g., "Kerala, India" -> destination_city: "Kochi", destination_airport: "COK"),
 *    the generated plan destination matching the backend-resolved city/airport is accepted.
 * 3. Geographic catalog hierarchy matching (secondary support):
 *    Using DESTINATION_CATALOG metadata (e.g., Kochi, Munnar, Alleppey -> region: Kerala, country: India).
 *
 * Preserves strict anti-hallucination bounds:
 * Cross-border or cross-region mismatches (e.g. Kerala -> Paris, Kerala -> Tokyo, Paris -> London)
 * are always rejected.
 */
export function validateDestinationConsistency(
  planDestRaw: string,
  canonicalDestRaw: string,
  response?: any
): boolean {
  const planDest = (planDestRaw || '').toLowerCase().trim();
  const canonicalDest = (canonicalDestRaw || '').toLowerCase().trim();
  if (!planDest || !canonicalDest) return true;

  // 1. Direct lexical matching (existing check)
  const destWords = canonicalDest.replace(/[,\-.]/g, ' ').split(/\s+/).filter(w => w.length >= 3);
  const directMatch = destWords.some(w => planDest.includes(w)) || planDest.includes(canonicalDest) || canonicalDest.includes(planDest);
  if (directMatch) return true;

  // 2. Authoritative backend canonical resolution matching
  if (response?.trip_constraints) {
    const backendQuery = (response.trip_constraints.destination_query || '').toLowerCase().trim();
    const backendCity = (response.trip_constraints.destination_city || '').toLowerCase().trim();
    const backendAirport = (response.trip_constraints.destination_airport || response.destination_airport || '').toLowerCase().trim();
    const planAirport = (response.plan_json?.destination_airport || '').toLowerCase().trim();

    if (backendQuery && (backendQuery === canonicalDest || canonicalDest.includes(backendQuery) || backendQuery.includes(canonicalDest))) {
      if (
        (backendCity && (planDest === backendCity || planDest.includes(backendCity) || backendCity.includes(planDest))) ||
        (backendAirport && (planDest === backendAirport || planAirport === backendAirport))
      ) {
        return true;
      }
    }
  }

  // 3. Geographic catalog hierarchy matching (Secondary support)
  const planMeta = getDestinationImage(planDest);
  const canonMeta = getDestinationImage(canonicalDest);
  const isGeneric = (m: any) => !m || m.country === 'Journey Discovery' || m.region === 'World';

  if (!isGeneric(planMeta) && !isGeneric(canonMeta)) {
    const sameCountry = planMeta.country && canonMeta.country && planMeta.country.toLowerCase() === canonMeta.country.toLowerCase();
    if (sameCountry) {
      const sameRegion = Boolean(planMeta.region && canonMeta.region && planMeta.region.toLowerCase() === canonMeta.region.toLowerCase());
      const regionInCanon = Boolean(planMeta.region && canonicalDest.includes(planMeta.region.toLowerCase()));
      const canonInRegion = Boolean(canonMeta.region && planDest.includes(canonMeta.region.toLowerCase()));
      if (sameRegion || regionInCanon || canonInRegion) {
        return true;
      }
    }
  }

  return false;
}

export const PlanningWorkspace: React.FC<PlanningWorkspaceProps> = ({ trip, onUpdateTrip }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastQuery, setLastQuery] = useState<string>('');
  const { notify } = useNotification();

  const handleGeneratePlan = async (query: string) => {
    setIsLoading(true);
    setError(null);
    setLastQuery(query);

    try {
      const isRevisingProposal = Boolean(
        trip.status === 'awaiting_approval' && trip.active_revision_id
      );

      const response = isRevisingProposal
        ? await api.generateRevisionPlan(trip.trip_id, trip.active_revision_id!, {
            message: query,
            query,
          })
        : await api.planTravel({
            trip_id: trip.trip_id,
            thread_id: trip.thread_id,
            message: query,
            query,
          });

      // Verify response consistency against canonical trip
      const planDest = (response.plan_json?.destination || response.accommodation?.area || '').toLowerCase();
      const canonicalDest = (trip.destination || '').toLowerCase();
      if (planDest && canonicalDest) {
        const matches = validateDestinationConsistency(planDest, canonicalDest, response);
        if (!matches) {
          throw new Error(`Consistency error: Generated plan for "${response.plan_json?.destination || planDest}" does not match your trip destination "${trip.destination}".`);
        }
      }

      const planStart = response.plan_json?.departure_date;
      if (planStart && trip.start_date && planStart.slice(0, 10) !== trip.start_date.slice(0, 10)) {
        throw new Error(`Consistency error: Generated plan departure (${planStart}) does not match your trip start date (${trip.start_date}).`);
      }

      const planEnd = response.plan_json?.return_date;
      if (planEnd && trip.end_date && planEnd.slice(0, 10) !== trip.end_date.slice(0, 10)) {
        throw new Error(`Consistency error: Generated plan return (${planEnd}) does not match your trip end date (${trip.end_date}).`);
      }

      const planTravelers = response.plan_json?.travelers;
      if (planTravelers && trip.travelers_count && Number(planTravelers) !== Number(trip.travelers_count)) {
        throw new Error(`Consistency error: Generated plan traveler count (${planTravelers}) does not match your trip traveler count (${trip.travelers_count}).`);
      }

      const updatedTrip: TripDetail = {
        ...trip,
        status: isRevisingProposal ? 'awaiting_approval' : 'planning',
        thread_id: response.thread_id || trip.thread_id,
        plan: response,
      };

      onUpdateTrip(updatedTrip);
      notify('Your itinerary has been shaped with authentic travel recommendations.', 'success', 'Itinerary Composed');
    } catch (err: any) {
      const msg = err.message || "We couldn't shape the plan right now. Please try again.";
      setError(msg);
      notify(msg, 'error', 'Planning Error');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
      {/* Left Column: Trip Philosophy & Requirements (5 cols on large screens) */}
      <div className="lg:col-span-5 space-y-6">
        <PlannerInput
          trip={trip}
          onSubmitPrompt={handleGeneratePlan}
          isLoading={isLoading}
        />
      </div>

      {/* Right Column: Itinerary Storyline (7 cols on large screens) */}
      <div className="lg:col-span-7">
        {/* Loading State */}
        {isLoading && (
          <div className="p-12 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] shadow-xs space-y-4">
            <div className="w-14 h-14 rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center">
              <Compass className="w-7 h-7 animate-spin" />
            </div>
            <div className="space-y-1.5">
              <h4 className="text-xl font-bold font-editorial text-[var(--text-primary)]">
                Shaping your journey to {trip.destination}...
              </h4>
              <p className="text-xs sm:text-sm text-[var(--text-secondary)] max-w-sm mx-auto leading-relaxed font-light">
                Coordinating boutique stays, walking routes, and dining recommendations matching your pace.
              </p>
            </div>
            <div className="pt-2 text-xs font-medium text-[var(--primary)] flex items-center justify-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 animate-pulse" />
              <span>Curating recommendations and neighborhood flow...</span>
            </div>
          </div>
        )}

        {/* Error State */}
        {!isLoading && error && (
          <div className="p-8 text-center bg-[var(--surface)] rounded-3xl border border-[var(--status-danger)]/30 space-y-4 shadow-xs">
            <div className="w-12 h-12 rounded-full bg-[var(--status-danger)]/10 text-[var(--status-danger)] mx-auto flex items-center justify-center">
              <AlertCircle className="w-6 h-6" />
            </div>
            <div className="space-y-1">
              <h4 className="text-lg font-bold text-[var(--text-primary)] font-editorial">
                We couldn&apos;t complete the itinerary right now.
              </h4>
              <p className="text-xs text-[var(--text-secondary)] max-w-sm mx-auto">
                {error}
              </p>
            </div>
            {lastQuery && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleGeneratePlan(lastQuery)}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Try Again
              </Button>
            )}
          </div>
        )}

        {/* Success State: Active Plan */}
        {!isLoading && !error && trip.plan && (
          <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs">
            <ItineraryTimeline plan={trip.plan} destinationName={trip.destination} />
          </div>
        )}

        {/* Idle State: No plan generated yet */}
        {!isLoading && !error && !trip.plan && (
          <div className="p-12 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] shadow-xs space-y-4">
            <div className="w-14 h-14 rounded-2xl bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center">
              <Sparkles className="w-7 h-7 text-[var(--terracotta)]" />
            </div>
            <div className="space-y-1.5 max-w-md mx-auto">
              <h4 className="text-2xl font-bold font-editorial text-[var(--text-primary)]">
                Ready to explore {trip.destination}
              </h4>
              <p className="text-xs sm:text-sm text-[var(--text-secondary)] leading-relaxed font-light">
                Describe your desired travel style on the left or tap below to synthesize
                a personalized multi-day itinerary.
              </p>
            </div>
            <div className="pt-2">
              <Button
                variant="accent"
                size="lg"
                onClick={() => handleGeneratePlan('Relaxed cultural days, great local food, and unhurried neighborhood exploration.')}
                rightIcon={<ArrowRight className="w-4 h-4" />}
                className="shadow-md"
              >
                Compose Initial Plan
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
