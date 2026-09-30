import React from 'react';
import { Building2, Plane, Wallet, CheckCircle2, Compass, BookOpen } from 'lucide-react';
import { TravelPlanResponse } from '../../types/trip';
import { ItineraryDay } from './ItineraryDay';
import { getDestinationImage } from '../../lib/destinationImages';
import { formatCurrency } from '../../lib/currency';

interface ItineraryTimelineProps {
  plan: TravelPlanResponse;
  destinationName?: string;
}

export const ItineraryTimeline: React.FC<ItineraryTimelineProps> = ({ plan, destinationName }) => {
  const destMeta = getDestinationImage(destinationName);

  return (
    <div className="space-y-10">
      {/* Editorial Journey Narrative */}
      {plan.overview && (
        <div className="p-6 sm:p-7 rounded-3xl bg-[var(--surface-elevated)] border border-[var(--border)] space-y-2.5 shadow-2xs">
          <div className="flex items-center gap-2 text-xs font-mono font-semibold text-[var(--primary)] uppercase tracking-wider">
            <BookOpen className="w-3.5 h-3.5" />
            <span>Itinerary Storyline</span>
          </div>
          <p className="text-base sm:text-lg text-[var(--text-primary)] font-editorial italic leading-relaxed text-balance">
            &ldquo;{plan.overview}&rdquo;
          </p>
        </div>
      )}

      {/* Accommodation & Flight Concept Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        {plan.accommodation && (
          <div className="p-5 rounded-2xl bg-[var(--surface)] border border-[var(--border)] space-y-2.5 shadow-xs">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-[var(--terracotta)] flex items-center gap-1.5 font-mono uppercase tracking-wider text-[11px]">
                <Building2 className="w-4 h-4" />
                Selected Stay Concept
              </span>
              {plan.accommodation.est_nightly_rate && (
                <span className="font-mono text-xs text-[var(--text-secondary)]">
                  {plan.accommodation.est_nightly_rate}
                </span>
              )}
            </div>
            <div className="text-base font-bold text-[var(--text-primary)] font-editorial">
              {plan.accommodation.name}
            </div>
            {plan.accommodation.area && (
              <div className="text-xs text-[var(--text-secondary)]">
                Neighborhood: {plan.accommodation.area}
              </div>
            )}
            {plan.accommodation.description && (
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed font-light">
                {plan.accommodation.description}
              </p>
            )}
          </div>
        )}

        {plan.flights && (
          <div className="p-5 rounded-2xl bg-[var(--surface)] border border-[var(--border)] space-y-2.5 shadow-xs">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-[var(--primary)] flex items-center gap-1.5 font-mono uppercase tracking-wider text-[11px]">
                <Plane className="w-4 h-4" />
                Transit Recommendation
              </span>
              {plan.flights.approx_price && (
                <span className="font-mono text-xs text-[var(--text-secondary)]">
                  {plan.flights.approx_price}
                </span>
              )}
            </div>
            <div className="text-base font-bold text-[var(--text-primary)] font-editorial">
              {plan.flights.route || 'Recommended Route'}
            </div>
            {plan.flights.suggested_carrier && (
              <div className="text-xs text-[var(--text-secondary)]">
                Carriers: {plan.flights.suggested_carrier}
              </div>
            )}
            <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed font-light">
              Part 3 connects live Duffel Link checkout and snapshot verification.
            </p>
          </div>
        )}
      </div>

      {/* Day-by-Day Timeline */}
      <div className="space-y-8">
        <div className="flex items-baseline justify-between border-b border-[var(--border)] pb-3">
          <div>
            <div className="text-xs font-mono font-semibold uppercase tracking-wider text-[var(--primary)]">
              Daily Chapter Timeline
            </div>
            <h3 className="text-2xl font-bold font-editorial text-[var(--text-primary)] mt-0.5">
              Your Developing Journey
            </h3>
          </div>
          <span className="text-xs font-mono text-[var(--text-secondary)]">
            {plan.itinerary?.length || 0} Days Curated
          </span>
        </div>

        {plan.itinerary && plan.itinerary.length > 0 ? (
          <div className="space-y-8">
            {plan.itinerary.map((day, idx) => (
              <ItineraryDay
                key={day.day_number}
                day={day}
                dayHeroImage={idx === 0 ? destMeta.imageUrl : undefined}
              />
            ))}
          </div>
        ) : (
          <div className="text-xs text-[var(--text-secondary)] py-4">
            No day-by-day activities generated yet.
          </div>
        )}
      </div>

      {/* Estimated Budget Breakdown */}
      {plan.estimated_budget && (
        <div className="p-6 rounded-3xl bg-[var(--surface)] border border-[var(--border)] space-y-4 shadow-xs">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs font-semibold text-[var(--text-primary)]">
              <Wallet className="w-4 h-4 text-[var(--primary)]" />
              <span className="text-sm font-bold font-editorial">Estimated Investment Overview</span>
            </div>
            <div className="text-base font-bold font-mono text-[var(--text-primary)] tabular-nums">
              Total: {formatCurrency(plan.estimated_budget.total || 0, plan.estimated_budget.currency)}
            </div>
          </div>

          {plan.estimated_budget.breakdown && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 border-t border-[var(--border-subtle)]">
              {Object.entries(plan.estimated_budget.breakdown).map(([category, amount]) => (
                <div key={category} className="p-3 bg-[var(--background)] rounded-xl text-xs space-y-0.5">
                  <span className="text-[11px] text-[var(--text-secondary)] block font-medium">{category}</span>
                  <span className="font-semibold text-[var(--text-primary)] font-mono tabular-nums text-sm">
                    {typeof amount === 'number'
                      ? formatCurrency(amount, plan.estimated_budget?.currency)
                      : amount}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Travel Notes */}
      {plan.notes && plan.notes.length > 0 && (
        <div className="p-5 rounded-2xl bg-[var(--sand-light)]/60 border border-[var(--border)] space-y-2">
          <div className="text-xs font-semibold text-[var(--primary)] flex items-center gap-1.5 font-mono uppercase tracking-wider">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>Essential Planning Notes</span>
          </div>
          <ul className="space-y-1.5 pl-4 list-disc text-xs text-[var(--text-secondary)] font-light">
            {plan.notes.map((note, idx) => (
              <li key={idx} className="leading-relaxed">
                {note}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
