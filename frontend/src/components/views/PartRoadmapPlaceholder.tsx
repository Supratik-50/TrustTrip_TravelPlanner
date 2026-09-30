import React from 'react';
import { Plane, Building2, SearchCheck, ArrowRight, Lock, CheckCircle2 } from 'lucide-react';
import { Button } from '../common/Button';
import { useAuth } from '../../context/AuthContext';
import { NavTab } from '../common/Header';

interface PartRoadmapPlaceholderProps {
  tab: 'flights' | 'hotels' | 'research';
  onOpenAuth: (mode?: 'login' | 'signup') => void;
  onSwitchTab: (tab: NavTab) => void;
}

export const PartRoadmapPlaceholder: React.FC<PartRoadmapPlaceholderProps> = ({
  tab,
  onOpenAuth,
  onSwitchTab,
}) => {
  const { isAuthenticated } = useAuth();

  const configs: Record<
    'flights' | 'hotels' | 'research',
    {
      part: string;
      title: string;
      subtitle: string;
      icon: React.ReactNode;
      endpoints: string[];
      keyCapabilities: string[];
    }
  > = {
    flights: {
      part: 'PART 3',
      title: 'Flexible Flight Search & Duffel Handoff',
      subtitle: 'Live comparison, snapshot selection, and direct provider handoff without card collection.',
      icon: <Plane className="w-6 h-6 text-[var(--primary)]" />,
      endpoints: [
        'POST /api/flights/search-flexible',
        'POST /api/bookings/flight/prepare',
        'POST /api/bookings/flight/{intent_id}/revalidate',
        'POST /api/bookings/flight/{intent_id}/handoff',
        'GET  /api/bookings/{intent_id}',
      ],
      keyCapabilities: [
        'Multi-city and flexible date flight search matrix',
        'Offer snapshot selection tied to trip revision',
        'Fresh price revalidation and change acknowledgment',
        'External checkout handoff (Duffel Link) with zero card data stored',
      ],
    },
    hotels: {
      part: 'PART 3',
      title: 'Curated Hotel Discovery & Prebooking',
      subtitle: 'Image-focused property inspection, transparent room tiers, and Razorpay checkout.',
      icon: <Building2 className="w-6 h-6 text-[var(--primary)]" />,
      endpoints: [
        'POST /api/hotels/search',
        'POST /api/hotels/prebook',
        'GET  /api/health',
      ],
      keyCapabilities: [
        'Editorial room cards with amenity verification',
        'Real-time price calculations and cancellation policies',
        'Secure pre-booking reservation lock',
        'Separate hotel payment verification lifecycle',
      ],
    },
    research: {
      part: 'PART 4',
      title: 'Travel Research & Evidence Workspace',
      subtitle: 'Grounded intelligence with citation cards, freshness indicators, and conflict resolution.',
      icon: <SearchCheck className="w-6 h-6 text-[var(--primary)]" />,
      endpoints: [
        'POST   /api/research/query',
        'POST   /api/research/documents/upload',
        'GET    /api/research/documents',
        'DELETE /api/research/documents/{doc_id}',
        'GET    /api/research/sessions/{session_id}',
      ],
      keyCapabilities: [
        'Natural question answering with source citations',
        'Document repository for personal travel guides / PDF policies',
        'Conflict resolution when guidebooks disagree on facts',
        'Zero vector or embedding jargon exposed in UI',
      ],
    },
  };

  const current = configs[tab];

  return (
    <div className="max-w-4xl mx-auto py-12 px-4 sm:px-6">
      <div className="bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-xs p-8 sm:p-10 space-y-8">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-[var(--border-subtle)]">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-[var(--primary-soft)] flex items-center justify-center shrink-0">
              {current.icon}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-[var(--primary)] uppercase tracking-wider font-mono">
                  {current.part} Scheduled
                </span>
                <span aria-hidden="true" className="opacity-40">·</span>
                <span className="text-[11px] text-[var(--status-success)] font-medium flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" />
                  Canonical Types Ready
                </span>
              </div>
              <h1 className="text-2xl font-bold text-[var(--text-primary)] font-editorial mt-0.5">
                {current.title}
              </h1>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {!isAuthenticated && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenAuth('login')}
                leftIcon={<Lock className="w-3.5 h-3.5" />}
              >
                Sign In First
              </Button>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onSwitchTab('trips')}
            >
              Back to Trips
            </Button>
          </div>
        </div>

        <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
          {current.subtitle}
        </p>

        {/* Key capabilities list */}
        <div className="space-y-3">
          <div className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider">
            Planned Specifications for this Module:
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {current.keyCapabilities.map((cap, i) => (
              <div
                key={i}
                className="p-3 bg-[var(--background)] rounded-lg border border-[var(--border)] flex items-start gap-2 text-xs text-[var(--text-primary)]"
              >
                <CheckCircle2 className="w-3.5 h-3.5 text-[var(--primary)] shrink-0 mt-0.5" />
                <span>{cap}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Canonical API Endpoints */}
        <div className="p-4 bg-[var(--background)] rounded-xl border border-[var(--border)] space-y-2">
          <div className="text-xs font-semibold text-[var(--text-primary)]">
            Source-of-Truth Backend Endpoints:
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 font-mono text-[11px] text-[var(--text-secondary)]">
            {current.endpoints.map((ep, idx) => (
              <div key={idx} className="p-1.5 bg-[var(--surface)] rounded border border-[var(--border-subtle)] text-[var(--text-primary)]">
                {ep}
              </div>
            ))}
          </div>
        </div>

        <div className="pt-2 flex items-center justify-between text-xs text-[var(--text-secondary)]">
          <span>
            Part 2 (Dashboard, Trips & Planning Workspace) is currently active.
          </span>
          <button
            type="button"
            onClick={() => onSwitchTab('trips')}
            className="text-[var(--primary)] font-medium hover:underline inline-flex items-center gap-1 cursor-pointer"
          >
            Explore Trips
            <ArrowRight className="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>
  );
};
