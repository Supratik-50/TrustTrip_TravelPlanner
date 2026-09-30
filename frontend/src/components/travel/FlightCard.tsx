import React, { useState } from 'react';
import {
  Plane,
  Clock,
  ArrowRight,
  ShieldCheck,
  Luggage,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Sparkles,
  Calendar,
} from 'lucide-react';
import { FlightOffer } from '../../types/flight';
import { Button } from '../common/Button';
import { formatCurrency } from '../../lib/currency';

interface FlightCardProps {
  offer: FlightOffer;
  isSelected?: boolean;
  onSelect?: (offer: FlightOffer) => void;
  isSelecting?: boolean;
  tripContext?: {
    tripId: string;
    destinationName: string;
  };
  onPlanTripWithFlight?: (offer: FlightOffer) => void;
}

export const FlightCard: React.FC<FlightCardProps> = ({
  offer,
  isSelected = false,
  onSelect,
  isSelecting = false,
  tripContext,
  onPlanTripWithFlight,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);

  const outboundSlice = offer.slices[0];
  const returnSlice = offer.slices[1];

  const formatTime = (isoString?: string) => {
    if (!isoString) return '--:--';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch {
      return isoString.split('T')[1]?.slice(0, 5) || '--:--';
    }
  };

  const formatDate = (isoString?: string) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch {
      return '';
    }
  };

  const formatDuration = (mins: number) => {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return `${h}h ${m}m`;
  };

  const getAirlineMonogram = (name: string, code: string) => {
    if (code) return code.slice(0, 2).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  };

  return (
    <div
      className={`rounded-3xl border transition-all duration-300 overflow-hidden ${
        isSelected
          ? 'bg-[var(--surface)] border-[var(--primary)] shadow-md ring-2 ring-[var(--primary)]/20'
          : 'bg-[var(--surface)] border-[var(--border)] hover:border-[var(--border-strong)] shadow-xs hover:shadow-md'
      }`}
    >
      {/* Header Badge Row */}
      <div className="px-5 py-3 border-b border-[var(--border)]/60 bg-[var(--surface-muted)]/40 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          {/* Airline Badge */}
          <div className="w-6 h-6 rounded-lg bg-[var(--primary)]/10 text-[var(--primary)] font-mono font-bold text-[10px] flex items-center justify-center">
            {getAirlineMonogram(offer.carrier_name, offer.carrier_code)}
          </div>
          <span className="font-semibold text-[var(--text-primary)]">{offer.carrier_name}</span>
          <span className="text-[var(--text-muted)] font-mono text-[11px]">
            {outboundSlice?.segments[0]?.flight_number || offer.carrier_code}
          </span>
        </div>

        <div className="flex items-center gap-2.5">
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--surface)] border border-[var(--border)] text-[var(--text-secondary)]">
            <Luggage className="w-3 h-3 text-[var(--primary)]" />
            2 Checked Bags Included
          </span>

          {offer.refundable ? (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--status-success)]/10 text-[var(--status-success)] border border-[var(--status-success)]/20">
              <ShieldCheck className="w-3 h-3" />
              Refundable
            </span>
          ) : (
            <span className="px-2.5 py-0.5 rounded-full text-[11px] text-[var(--text-muted)] bg-[var(--surface)] border border-[var(--border)]">
              Standard Fare
            </span>
          )}

          {isSelected && (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[var(--primary)] text-white shadow-xs">
              <CheckCircle2 className="w-3 h-3" />
              Selected for Trip
            </span>
          )}
        </div>
      </div>

      {/* Main Body: Flights Itinerary Slices */}
      <div className="p-5 sm:p-6 space-y-5">
        {/* Outbound Slice */}
        {outboundSlice && (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] flex items-center justify-center shrink-0">
                <Plane className="w-4 h-4 rotate-45" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-[var(--primary)] font-mono">
                    Outbound
                  </span>
                  <span className="text-xs text-[var(--text-muted)] flex items-center gap-1">
                    <Calendar className="w-3 h-3" />
                    {formatDate(outboundSlice.departure_time)}
                  </span>
                </div>
                <div className="text-sm font-semibold text-[var(--text-primary)]">
                  {outboundSlice.origin} ➔ {outboundSlice.destination}
                </div>
              </div>
            </div>

            {/* Flight Times & Duration Bar */}
            <div className="flex-1 max-w-sm flex items-center justify-between gap-3 text-center px-2">
              <div className="text-left">
                <div className="text-lg sm:text-xl font-bold font-editorial text-[var(--text-primary)]">
                  {formatTime(outboundSlice.departure_time)}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-mono font-medium">
                  {outboundSlice.origin}
                </div>
              </div>

              <div className="flex-1 flex flex-col items-center px-2">
                <span className="text-[11px] text-[var(--text-muted)] font-mono mb-1 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatDuration(outboundSlice.total_duration_minutes)}
                </span>
                <div className="relative w-full flex items-center">
                  <div className="h-[2px] w-full bg-[var(--border-strong)]" />
                  <div
                    className={`w-2 h-2 rounded-full absolute left-1/2 -translate-x-1/2 ${
                      outboundSlice.stops === 0 ? 'bg-[var(--status-success)]' : 'bg-[var(--status-warning)]'
                    }`}
                  />
                </div>
                <span
                  className={`text-[10px] mt-1 font-medium ${
                    outboundSlice.stops === 0 ? 'text-[var(--status-success)]' : 'text-[var(--status-warning)]'
                  }`}
                >
                  {outboundSlice.stops === 0 ? 'Non-stop Direct' : `${outboundSlice.stops} stop layover`}
                </span>
              </div>

              <div className="text-right">
                <div className="text-lg sm:text-xl font-bold font-editorial text-[var(--text-primary)]">
                  {formatTime(outboundSlice.arrival_time)}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-mono font-medium">
                  {outboundSlice.destination}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Return Slice (if round trip) */}
        {returnSlice && (
          <div className="pt-4 border-t border-[var(--border)]/70 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-[var(--surface-muted)] text-[var(--text-secondary)] flex items-center justify-center shrink-0">
                <Plane className="w-4 h-4 -rotate-135" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono">
                    Return
                  </span>
                  <span className="text-xs text-[var(--text-muted)] flex items-center gap-1">
                    <Calendar className="w-3 h-3" />
                    {formatDate(returnSlice.departure_time)}
                  </span>
                </div>
                <div className="text-sm font-semibold text-[var(--text-primary)]">
                  {returnSlice.origin} ➔ {returnSlice.destination}
                </div>
              </div>
            </div>

            {/* Flight Times & Duration Bar */}
            <div className="flex-1 max-w-sm flex items-center justify-between gap-3 text-center px-2">
              <div className="text-left">
                <div className="text-lg sm:text-xl font-bold font-editorial text-[var(--text-primary)]">
                  {formatTime(returnSlice.departure_time)}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-mono font-medium">
                  {returnSlice.origin}
                </div>
              </div>

              <div className="flex-1 flex flex-col items-center px-2">
                <span className="text-[11px] text-[var(--text-muted)] font-mono mb-1 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatDuration(returnSlice.total_duration_minutes)}
                </span>
                <div className="relative w-full flex items-center">
                  <div className="h-[2px] w-full bg-[var(--border-strong)]" />
                  <div
                    className={`w-2 h-2 rounded-full absolute left-1/2 -translate-x-1/2 ${
                      returnSlice.stops === 0 ? 'bg-[var(--status-success)]' : 'bg-[var(--status-warning)]'
                    }`}
                  />
                </div>
                <span
                  className={`text-[10px] mt-1 font-medium ${
                    returnSlice.stops === 0 ? 'text-[var(--status-success)]' : 'text-[var(--status-warning)]'
                  }`}
                >
                  {returnSlice.stops === 0 ? 'Non-stop Direct' : `${returnSlice.stops} stop layover`}
                </span>
              </div>

              <div className="text-right">
                <div className="text-lg sm:text-xl font-bold font-editorial text-[var(--text-primary)]">
                  {formatTime(returnSlice.arrival_time)}
                </div>
                <div className="text-xs text-[var(--text-secondary)] font-mono font-medium">
                  {returnSlice.destination}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Pricing & Selection Footer */}
        <div className="pt-4 border-t border-[var(--border)] flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-baseline gap-1.5">
              <span className="text-2xl sm:text-3xl font-bold font-editorial text-[var(--text-primary)]">
                {formatCurrency(offer.total_amount, offer.currency)}
              </span>
              <span className="text-xs text-[var(--text-secondary)] font-mono">
                total
              </span>
            </div>
            <p className="text-[11px] text-[var(--text-muted)]">
              All taxes, passenger fees & carry-on included
            </p>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => setIsExpanded(!isExpanded)}
              className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-medium flex items-center gap-1 px-2.5 py-1.5 rounded-lg hover:bg-[var(--surface-muted)] transition-colors"
            >
              {isExpanded ? 'Hide Details' : 'Flight Details'}
              {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>

            {onSelect && (
              <Button
                variant={isSelected ? 'outline' : 'primary'}
                size="md"
                onClick={() => onSelect(offer)}
                isLoading={isSelecting}
                disabled={isSelected}
                leftIcon={isSelected ? <CheckCircle2 className="w-4 h-4 text-[var(--primary)]" /> : undefined}
                className={isSelected ? 'border-[var(--primary)] text-[var(--primary)]' : ''}
              >
                {isSelected ? 'Snapshot Active' : 'Select for Trip'}
              </Button>
            )}

            {onPlanTripWithFlight && !onSelect && (
              <Button
                variant="accent"
                size="md"
                onClick={() => onPlanTripWithFlight(offer)}
                rightIcon={<ArrowRight className="w-4 h-4" />}
              >
                Plan Trip with Flight
              </Button>
            )}
          </div>
        </div>

        {/* Expandable Flight Segments & Aircraft Info */}
        {isExpanded && (
          <div className="mt-4 pt-4 border-t border-[var(--border)] bg-[var(--surface-muted)]/30 -mx-5 -mb-5 sm:-mx-6 sm:-mb-6 p-5 sm:p-6 space-y-4">
            <h5 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-[var(--terracotta)]" />
              Segment & Aircraft Breakdown
            </h5>

            {offer.slices.map((slice, sIdx) => (
              <div key={sIdx} className="space-y-3">
                <div className="text-xs font-semibold text-[var(--primary)]">
                  {sIdx === 0 ? 'Outbound Journey' : 'Return Journey'} ({slice.origin} ➔ {slice.destination})
                </div>

                {slice.segments.map((seg, segIdx) => (
                  <div
                    key={seg.id || segIdx}
                    className="p-3 rounded-2xl bg-[var(--surface)] border border-[var(--border)] text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">
                        {seg.carrier_name} • Flight {seg.flight_number}
                      </div>
                      <div className="text-[11px] text-[var(--text-secondary)]">
                        Cabin: {seg.cabin_class.toUpperCase()} • Duration: {formatDuration(seg.duration_minutes)}
                      </div>
                    </div>
                    <div className="text-right font-mono text-[11px] text-[var(--text-primary)]">
                      {seg.origin} ({formatTime(seg.departure_time)}) ➔ {seg.destination} ({formatTime(seg.arrival_time)})
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
