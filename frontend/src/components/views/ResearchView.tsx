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
  Trash2,
  Clock,
} from 'lucide-react';
import { ResearchResponse, ResearchCitation, ResearchDocument, ResearchHistoryItem } from '../../types/research';
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

export function historyItemToResearchResponse(item: ResearchHistoryItem): ResearchResponse {
  return {
    research_session_id: item.session_id,
    query_id: item.query_id,
    query_text: item.query_text,
    intent: item.intent || 'DESTINATION_RESEARCH',
    mode: (item.mode as any) || 'auto',
    answer: item.answer,
    citations: item.citations || [],
    facts: item.facts || [],
    conflicts: item.conflicts || [],
    evidence_status: (item.evidence_status as any) || 'complete',
    evidence_count: item.evidence_count ?? (item.citations?.length || 0),
    created_at: item.created_at,
    trip_id: item.trip_id || undefined,
  };
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

  // Queries results list
  const [results, setResults] = useState<ResearchResponse[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Private research documents
  const [documents, setDocuments] = useState<ResearchDocument[]>([]);
  const [activeTab, setActiveTab] = useState<'discoveries' | 'documents' | 'history'>('discoveries');

  // Persistent Research History
  const [historyItems, setHistoryItems] = useState<ResearchHistoryItem[]>([]);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

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

  // Load history and populate/restore results upon initial mount or refresh
  const loadHistory = useCallback(async () => {
    setIsHistoryLoading(true);
    setHistoryError(null);
    try {
      const items = await api.listResearchHistory(50, 0, tripId);
      setHistoryItems(items);
      if (items && items.length > 0) {
        setResults((prev) => {
          const existingIds = new Set(prev.map((r) => r.query_id).filter(Boolean));
          const restored = items.map(historyItemToResearchResponse);
          const newRestored = restored.filter((r) => !existingIds.has(r.query_id));
          return [...prev, ...newRestored];
        });
      }
    } catch (err: any) {
      setHistoryError(err.message || 'Unable to load research history.');
      // Distinct error state: a failed history request must not replace existing results with an empty list
    } finally {
      setIsHistoryLoading(false);
    }
  }, [tripId]);

  useEffect(() => {
    loadTripData();
    loadDocuments();
    loadHistory();
  }, [tripId, isAuthenticated, loadTripData, loadDocuments, loadHistory]);

  const handleSearch = async (
    question: string,
    mode: 'auto' | 'web' | 'rag' | 'hybrid'
  ) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.researchQuery({
        query: question,
        mode,
        trip_id: tripId,
      });

      setResults((prev) => [response, ...prev]);
      loadHistory();
      notify('Verified travel discovery synthesized with evidence citations.', 'success', 'Knowledge Retrieved');
    } catch (err: any) {
      const msg = err.message || 'Research query is temporarily unavailable. Please retry.';
      setError(msg);
      notify(msg, 'error', 'Research Notice');
    } finally {
      setIsLoading(false);
    }
  };

  const handleReopenHistory = (item: ResearchHistoryItem) => {
    const reopened = historyItemToResearchResponse(item);
    setResults((prev) => [reopened, ...prev.filter((r) => r.query_id !== item.query_id)]);
    setActiveTab('discoveries');
    notify('Reopened historical research query and original citations.', 'info', 'History Reopened');
  };

  const handleDeleteHistoryEntry = async (e: React.MouseEvent, historyId: string) => {
    e.stopPropagation();
    if (!window.confirm('Delete this research history entry? This will not delete your uploaded documents or other notes.')) {
      return;
    }
    const itemToDelete = historyItems.find((h) => h.id === historyId);
    try {
      await api.deleteResearchHistoryEntry(historyId);
      setHistoryItems((prev) => prev.filter((h) => h.id !== historyId));
      if (itemToDelete) {
        setResults((prev) => prev.filter((r) => r.query_id !== itemToDelete.query_id));
      }
      notify('Research history entry removed.', 'success', 'Entry Removed');
    } catch (err: any) {
      notify(err.message || 'Failed to delete history entry.', 'error', 'Error');
    }
  };

  const handleClearHistory = async () => {
    if (!window.confirm('Are you sure you want to clear your entire research history?')) {
      return;
    }
    try {
      await api.clearResearchHistory();
      setHistoryItems([]);
      setResults([]);
      notify('Research history cleared.', 'success', 'History Cleared');
    } catch (err: any) {
      notify(err.message || 'Failed to clear research history.', 'error', 'Error');
    }
  };

  // Open Evidence Drawer
  const handleOpenEvidence = (citation: ResearchCitation, question: string) => {
    setSelectedCitation(citation);
    setCitationQuestion(question);
    setIsDrawerOpen(true);
  };

  // Copy insight handler
  const handleCopyInsight = (insightText: string) => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(insightText);
    }
    notify('Insight copied to clipboard.', 'success', 'Insight Copied');
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
          className={`flex items-center gap-2 px-4 py-2 rounded-xl font-medium transition-all shrink-0 cursor-pointer ${
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

        <button
          type="button"
          onClick={() => {
            setActiveTab('history');
            loadHistory();
          }}
          className={`flex items-center gap-2 px-4 py-2 rounded-xl font-medium transition-all shrink-0 cursor-pointer ${
            activeTab === 'history'
              ? 'bg-[var(--primary)] text-white shadow-xs font-semibold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <History className="w-4 h-4" />
          Research History
          {historyItems.length > 0 && (
            <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-[var(--surface-muted)] text-[var(--text-primary)] font-mono">
              {historyItems.length}
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
                  key={res.query_id || res.research_session_id || idx}
                  result={res}
                  onSelectCitation={handleOpenEvidence}
                  onCopyInsight={handleCopyInsight}
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

      {/* TAB 3: RESEARCH HISTORY */}
      {activeTab === 'history' && (
        <div className="space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-xs">
            <div>
              <h3 className="text-base font-bold text-[var(--text-primary)] font-editorial">
                Persistent Research History
              </h3>
              <p className="text-xs text-[var(--text-secondary)] font-light mt-0.5">
                View, reopen, or delete previous research queries. Original evidence and citations are fully preserved.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={loadHistory}
                isLoading={isHistoryLoading}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
                className="text-xs"
              >
                Refresh
              </Button>
              {historyItems.length > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleClearHistory}
                  leftIcon={<Trash2 className="w-3.5 h-3.5 text-[var(--status-danger)]" />}
                  className="text-xs text-[var(--status-danger)] hover:bg-[var(--status-danger)]/10"
                >
                  Clear History
                </Button>
              )}
            </div>
          </div>

          {isHistoryLoading ? (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
              <div className="w-8 h-8 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] mx-auto flex items-center justify-center animate-spin">
                <RefreshCw className="w-4 h-4" />
              </div>
              <p className="text-xs text-[var(--text-secondary)]">Retrieving research history...</p>
            </div>
          ) : historyError ? (
            <div className="p-6 text-center bg-[var(--surface)] rounded-3xl border border-[var(--status-danger)]/30 space-y-3">
              <AlertCircle className="w-6 h-6 text-[var(--status-danger)] mx-auto" />
              <p className="text-xs text-[var(--status-danger)] max-w-md mx-auto">{historyError}</p>
              <Button variant="outline" size="sm" onClick={loadHistory} className="text-xs">
                Retry
              </Button>
            </div>
          ) : historyItems.length === 0 ? (
            <div className="py-16 text-center bg-[var(--surface)] rounded-3xl border border-[var(--border)] space-y-3">
              <div className="w-12 h-12 rounded-full bg-[var(--surface-muted)] text-[var(--primary)] mx-auto flex items-center justify-center">
                <History className="w-6 h-6" />
              </div>
              <h4 className="text-lg font-bold font-editorial text-[var(--text-primary)]">
                No Research History Yet
              </h4>
              <p className="text-xs sm:text-sm text-[var(--text-secondary)] max-w-md mx-auto">
                Completed queries in the Research Notebook are automatically saved here so you can review and reopen them anytime.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {historyItems.map((item) => (
                <div
                  key={item.id}
                  className="p-5 bg-[var(--surface)] rounded-2xl border border-[var(--border)] shadow-xs hover:border-[var(--border-strong)] transition-all space-y-3"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-[var(--primary-soft)] text-[var(--primary)] font-semibold">
                        {item.mode}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase font-semibold ${
                        item.evidence_status === 'complete'
                          ? 'bg-[var(--status-success)]/15 text-[var(--status-success)]'
                          : item.evidence_status === 'partial'
                          ? 'bg-[var(--status-warning)]/15 text-[var(--status-warning)]'
                          : item.evidence_status === 'conflicted'
                          ? 'bg-purple-500/15 text-purple-600 dark:text-purple-400'
                          : 'bg-zinc-500/15 text-zinc-500'
                      }`}>
                        {item.evidence_status}
                      </span>
                      <span className="flex items-center gap-1 text-[11px] text-[var(--text-muted)] font-mono">
                        <Clock className="w-3 h-3" />
                        {new Date(item.created_at).toLocaleString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <Button
                        variant="accent"
                        size="sm"
                        onClick={() => handleReopenHistory(item)}
                        leftIcon={<Sparkles className="w-3 h-3" />}
                        className="text-xs font-semibold py-1 px-3 shadow-2xs"
                      >
                        Reopen
                      </Button>
                      <button
                        type="button"
                        onClick={(e) => handleDeleteHistoryEntry(e, item.id)}
                        className="p-1.5 text-[var(--text-muted)] hover:text-[var(--status-danger)] hover:bg-[var(--status-danger)]/10 rounded-lg transition-colors cursor-pointer"
                        title="Delete this history entry"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  <h4 className="text-sm sm:text-base font-semibold text-[var(--text-primary)]">
                    {item.query_text}
                  </h4>

                  <p className="text-xs text-[var(--text-secondary)] line-clamp-2 leading-relaxed">
                    {item.answer}
                  </p>

                  <div className="pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between text-[11px] text-[var(--text-muted)] font-mono">
                    <span>
                      {item.citations?.length || item.evidence_count || 0} verified source{(item.citations?.length || item.evidence_count || 0) === 1 ? '' : 's'}
                    </span>
                    {item.facts && item.facts.length > 0 && (
                      <span>{item.facts.length} linked fact{item.facts.length === 1 ? '' : 's'}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
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
