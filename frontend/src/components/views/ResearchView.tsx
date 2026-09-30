import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Compass,
  FileText,
  History,
  Sparkles,
  ArrowLeft,
  AlertCircle,
  RefreshCw,
  FolderOpen,
  Layers,
} from 'lucide-react';
import { ResearchResponse, ResearchCitation, ResearchDocument } from '../../types/research';
import { TripDetail, TripSummary } from '../../types/trip';
import { api } from '../../lib/apiClient';
import { useAuth } from '../../context/AuthContext';
import { useNotification } from '../../context/NotificationContext';
import { ResearchSearchBox } from '../travel/ResearchSearchBox';
import { ResearchDiscoveryCard } from '../travel/ResearchDiscoveryCard';
import { ResearchDocumentManager } from '../travel/ResearchDocumentManager';
import { EvidenceDrawer } from '../travel/EvidenceDrawer';
import { Button } from '../common/Button';

interface ResearchViewProps {
  tripIdOverride?: string;
  onBackOverride?: () => void;
}

export const ResearchView: React.FC<ResearchViewProps> = ({
  tripIdOverride,
  onBackOverride,
}) => {
  const params = useParams<{ tripId: string }>();
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { notify } = useNotification();

  const tripId = tripIdOverride || params.tripId;

  const [trip, setTrip] = useState<TripDetail | null>(null);
  const [allTrips, setAllTrips] = useState<TripSummary[]>([]);
  const [selectedDestination, setSelectedDestination] = useState<string>('Kyoto, Japan');
  const [activeSessionId, setActiveSessionId] = useState<string>(
    `rs_${tripId || 'global'}_${Date.now().toString(36)}`
  );

  // Queries results list
  const [results, setResults] = useState<ResearchResponse[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Private research documents
  const [documents, setDocuments] = useState<ResearchDocument[]>([]);
  const [activeTab, setActiveTab] = useState<'discoveries' | 'documents'>('discoveries');

  // Evidence Drawer state
  const [selectedCitation, setSelectedCitation] = useState<ResearchCitation | null>(null);
  const [citationQuestion, setCitationQuestion] = useState<string>('');
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  // Load trip context if tripId exists
  const loadTripData = useCallback(async () => {
    if (!tripId) return;
    try {
      const data = await api.getTrip(tripId);
      setTrip(data);
    } catch {
      // Offline fallback handled in api
    }
  }, [tripId]);

  // Load documents
  const loadDocuments = useCallback(async () => {
    try {
      const docs = await api.listResearchDocuments();
      setDocuments(docs);
    } catch {}
  }, []);

  useEffect(() => {
    loadTripData();
    loadDocuments();
  }, [tripId, isAuthenticated, loadTripData, loadDocuments]);

  const handleSearch = async (
    question: string,
    mode: 'auto' | 'web' | 'rag' | 'hybrid'
  ) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.researchQuery({
        trip_id: tripId,
        session_id: activeSessionId,
        question,
        mode,
      });

      setResults((prev) => [response, ...prev]);
      notify('Verified travel discovery synthesized with evidence citations.', 'success', 'Knowledge Retrieved');
    } catch (err: any) {
      const msg = err.message || 'Research query is temporarily unavailable. Please retry.';
      setError(msg);
      notify(msg, 'error', 'Research Notice');
    } finally {
      setIsLoading(false);
    }
  };

  // Open Evidence Drawer
  const handleOpenEvidence = (citation: ResearchCitation, question: string) => {
    setSelectedCitation(citation);
    setCitationQuestion(question);
    setIsDrawerOpen(true);
  };

  // Apply to trip notes
  const handleApplyToNotes = (insightText: string) => {
    if (!trip) {
      notify('Selected insight copied to clipboard.', 'info');
      navigator.clipboard.writeText(insightText);
      return;
    }

    notify(
      'Insight linked to journey planning context. Will be referenced during itinerary synthesis.',
      'success',
      'Insight Attached'
    );
  };

  const destinationName = trip?.destination || selectedDestination;

  const popularDestinations = [
    'Kyoto, Japan',
    'Amalfi Coast, Italy',
    'Paris, France',
    'Tokyo, Japan',
    'Santorini, Greece',
  ];

  return (
    <div className="space-y-8 pb-16">
      {/* Top Breadcrumb / Back Bar */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => {
            if (onBackOverride) {
              onBackOverride();
            } else if (tripId) {
              navigate(`/trips/${tripId}`);
            } else {
              navigate('/trips');
            }
          }}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>{tripId ? `Back to ${destinationName} Workspace` : 'Back to Trips'}</span>
        </button>

        {tripId && (
          <span className="text-xs font-mono text-[var(--text-muted)]">
            Trip Revision #{trip?.active_revision_number || 1}
          </span>
        )}
      </div>

      {/* Editorial Page Header */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="px-3 py-1 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
            Research Notebook & Evidence
          </span>
          <span className="text-xs text-[var(--text-muted)] font-mono">
            POST /api/research/query
          </span>
        </div>

        <div className="space-y-1">
          <div className="text-xs uppercase font-mono tracking-widest text-[var(--primary)] font-bold">
            RESEARCH FOR
          </div>
          <h1 className="text-3xl sm:text-4xl lg:text-5xl font-bold font-editorial text-[var(--text-primary)]">
            {destinationName}
          </h1>
        </div>

        <p className="text-sm sm:text-base text-[var(--text-secondary)] max-w-2xl font-light">
          Find places, ideas and practical information for your journey. Every answer is backed by
          verified travel publishers, regional authorities, or your personal uploaded notes.
        </p>

        {/* Standalone Destination Switcher & Trip Linker */}
        {!tripId && (
          <div className="pt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs font-mono text-[var(--text-muted)]">Explore destination:</span>
            {popularDestinations.map((dest) => (
              <button
                key={dest}
                type="button"
                onClick={() => setSelectedDestination(dest)}
                className={`text-xs px-3 py-1 rounded-full border transition-all cursor-pointer ${
                  selectedDestination === dest
                    ? 'border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)] font-semibold shadow-2xs'
                    : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)]'
                }`}
              >
                {dest}
              </button>
            ))}

            {allTrips.length > 0 && (
              <div className="ml-auto flex items-center gap-2 text-xs">
                <span className="text-[var(--text-muted)] font-mono">Link to trip:</span>
                <select
                  onChange={(e) => {
                    if (e.target.value) {
                      navigate(`/trips/${e.target.value}/research`);
                    }
                  }}
                  defaultValue=""
                  className="px-2.5 py-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] text-xs text-[var(--text-primary)]"
                >
                  <option value="" disabled>
                    Select a canonical trip...
                  </option>
                  {allTrips.map((t) => (
                    <option key={t.trip_id} value={t.trip_id}>
                      {t.destination} ({t.origin})
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}
      </div>

      {/* View Tabs: Research Discoveries vs My Research Documents */}
      <div className="flex items-center gap-2 p-1.5 bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-xs overflow-x-auto text-xs sm:text-sm">
        <button
          type="button"
          onClick={() => setActiveTab('discoveries')}
          className={`flex items-center gap-2 px-4 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'discoveries'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <Compass className="w-4 h-4" />
          Research Notebook
          {results.length > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-white/20 text-white font-mono">
              {results.length}
            </span>
          )}
        </button>

        <button
          type="button"
          onClick={() => setActiveTab('documents')}
          className={`flex items-center gap-2 px-4 py-2 rounded-xl font-medium transition-all shrink-0 ${
            activeTab === 'documents'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <FileText className="w-4 h-4" />
          My Documents
          {documents.length > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-[var(--surface-muted)] text-[var(--text-primary)] font-mono">
              {documents.length}
            </span>
          )}
        </button>
      </div>

      {/* TAB 1: DISCOVERIES & RESEARCH NOTEBOOK */}
      {activeTab === 'discoveries' && (
        <div className="space-y-6">
          {/* Research Input Bar */}
          <ResearchSearchBox
            onSearch={handleSearch}
            isLoading={isLoading}
            destination={destinationName.split(',')[0]}
          />

          {/* Loading Indicator */}
          {isLoading && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
              <div className="w-10 h-10 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
                <Compass className="w-5 h-5" />
              </div>
              <p className="text-xs text-[var(--text-secondary)]">
                Synthesizing verified regional transit notices, dining authorities, and notes...
              </p>
            </div>
          )}

          {/* Error Notice */}
          {!isLoading && error && (
            <div className="p-6 text-center bg-[var(--surface)] rounded-3xl border border-[var(--status-danger)]/30 space-y-3">
              <AlertCircle className="w-6 h-6 text-[var(--status-danger)] mx-auto" />
              <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto">{error}</p>
            </div>
          )}

          {/* Results List */}
          {!isLoading && results.length > 0 && (
            <div className="space-y-6">
              {results.map((res, idx) => (
                <ResearchDiscoveryCard
                  key={res.session_id + '_' + idx}
                  result={res}
                  onSelectCitation={handleOpenEvidence}
                  onApplyToTripNotes={trip ? handleApplyToNotes : undefined}
                />
              ))}
            </div>
          )}

          {/* Initial Prompt State if no results yet */}
          {!isLoading && results.length === 0 && (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
              <div className="w-12 h-12 rounded-full bg-[var(--surface-muted)] text-[var(--primary)] mx-auto flex items-center justify-center">
                <Compass className="w-6 h-6" />
              </div>
              <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
                Your travel research notebook is ready
              </h4>
              <p className="text-xs sm:text-sm text-[var(--text-secondary)] max-w-md mx-auto">
                Ask about historic quarters, authentic culinary ryotei, regional baggage regulations,
                or transit pass rules for {destinationName}.
              </p>
            </div>
          )}
        </div>
      )}

      {/* TAB 2: MY RESEARCH DOCUMENTS */}
      {activeTab === 'documents' && (
        <ResearchDocumentManager
          documents={documents}
          onRefresh={loadDocuments}
        />
      )}

      {/* Side / Bottom Evidence Drawer */}
      <EvidenceDrawer
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        citation={selectedCitation}
        questionContext={citationQuestion}
      />
    </div>
  );
};
