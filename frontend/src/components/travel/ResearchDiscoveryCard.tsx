import React, { useState } from 'react';
import {
  Compass,
  FileText,
  Globe,
  AlertTriangle,
  ArrowRight,
  ShieldCheck,
  Calendar,
  Sparkles,
  BookmarkPlus,
  Check,
} from 'lucide-react';
import { ResearchResponse, ResearchCitation } from '../../types/research';
import { Button } from '../common/Button';

interface ResearchDiscoveryCardProps {
  result: ResearchResponse;
  onSelectCitation: (citation: ResearchCitation, question: string) => void;
  onCopyInsight?: (insightText: string) => void;
  onApplyToTripNotes?: (insightText: string) => void;
  isApplying?: boolean;
}

export const ResearchDiscoveryCard: React.FC<ResearchDiscoveryCardProps> = ({
  result,
  onSelectCitation,
  onCopyInsight,
  onApplyToTripNotes,
  isApplying = false,
}) => {
  const handleCopyAction = onCopyInsight || onApplyToTripNotes;
  const [copied, setCopied] = useState(false);

  const displayQuery = result.query_text || result.question || 'Travel Inquiry';

  // Canonical citation parser: splits string ONLY on canonical [cite:<full UUID>] tokens
  const renderFormattedAnswer = (text: string) => {
    const parts = text.split(/(\[cite:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\])/g);
    return parts.map((part, idx) => {
      const matchCite = part.match(/^\[cite:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]$/);
      if (matchCite) {
        const citeUuid = matchCite[1].toLowerCase();
        // Resolve strictly by exact canonical token or evidence_id, NEVER by position
        const targetCitation = result.citations.find(
          (c) =>
            (c.citation_id && c.citation_id.toLowerCase() === `[cite:${citeUuid}]`) ||
            (c.evidence_id && c.evidence_id.toLowerCase() === citeUuid)
        );

        return (
          <button
            key={idx}
            type="button"
            onClick={() => {
              if (targetCitation) {
                onSelectCitation(targetCitation, displayQuery);
              }
            }}
            title={targetCitation ? `View source: ${targetCitation.title || targetCitation.domain || 'Verified Evidence'}` : 'Source citation'}
            className="inline-flex items-center justify-center px-1.5 py-0.2 mx-0.5 rounded text-[11px] font-mono font-bold text-[var(--primary)] bg-[var(--primary-soft)] hover:bg-[var(--primary)] hover:text-white transition-colors cursor-pointer align-baseline"
          >
            {part}
          </button>
        );
      }
      return <span key={idx}>{part}</span>;
    });
  };

  const handleCopy = () => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(result.answer);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'complete':
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--status-success)]/10 text-[var(--status-success)] border border-[var(--status-success)]/20 font-mono">
            Verified Evidence
          </span>
        );
      case 'partial':
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--status-warning)]/10 text-[var(--status-warning)] border border-[var(--status-warning)]/20 font-mono">
            Partial Sources
          </span>
        );
      case 'conflicted':
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--status-warning)]/10 text-[var(--status-warning)] border border-[var(--status-warning)]/20 font-mono">
            Conflicted Evidence
          </span>
        );
      case 'not_found':
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-[var(--surface-muted)] text-[var(--text-muted)] border border-[var(--border)] font-mono">
            No Verified Evidence
          </span>
        );
      default:
        return null;
    }
  };

  return (
    <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs space-y-6">
      {/* Header with Inquiry, Status, and Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-[var(--border)]">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--primary-soft)] text-[var(--primary)] font-mono">
              Travel Inquiry
            </span>
            {getStatusBadge(result.evidence_status || 'complete')}
            <span className="text-xs text-[var(--text-muted)] font-mono">
              {new Date(result.created_at).toLocaleDateString([], {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
          </div>
          <h3 className="text-xl sm:text-2xl font-bold font-editorial text-[var(--text-primary)]">
            "{displayQuery}"
          </h3>
        </div>

        <div className="flex items-center gap-2">
          {handleCopyAction && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => handleCopyAction(result.answer)}
              isLoading={isApplying}
              leftIcon={<BookmarkPlus className="w-3.5 h-3.5 text-[var(--primary)]" />}
            >
              Copy Insight
            </Button>
          )}

          <button
            type="button"
            onClick={handleCopy}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] px-2.5 py-1.5 rounded-lg border border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-muted)] transition-colors flex items-center gap-1 cursor-pointer"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-[var(--status-success)]" /> : null}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
      </div>

      {/* Answer narrative labeled as Synthesized Answer */}
      <div className="space-y-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)] font-mono">
          Research Summary
        </div>
        <div className="text-sm sm:text-base text-[var(--text-primary)] font-serif leading-relaxed sm:leading-8">
          {renderFormattedAnswer(result.answer)}
        </div>
      </div>

      {/* Source-Backed Facts (Claim-to-Citation Provenance) */}
      {result.facts && result.facts.length > 0 && (
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono">
            <ShieldCheck className="w-3.5 h-3.5 text-[var(--status-success)]" />
            <span>Source-Backed Facts ({result.facts.length})</span>
          </div>
          <div className="space-y-2">
            {result.facts.map((fact) => {
              const cit = result.citations.find(
                (c) => c.citation_id === fact.citation_id || c.evidence_id === fact.evidence_id
              );
              return (
                <div
                  key={fact.fact_id}
                  className="p-3 rounded-2xl bg-[var(--surface-muted)]/50 border border-[var(--border)] text-xs flex items-start justify-between gap-3"
                >
                  <div className="space-y-1">
                    <p className="font-medium text-[var(--text-primary)]">
                      "{fact.claim}"
                    </p>
                    {cit && (
                      <div className="text-[11px] text-[var(--text-muted)] font-mono">
                        Evidence Source: {cit.title || cit.domain || 'Verified Provider Evidence'}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      if (cit) {
                        onSelectCitation(cit, displayQuery);
                      }
                    }}
                    className="px-2 py-0.5 rounded text-[10px] font-mono font-bold text-[var(--primary)] bg-[var(--primary-soft)] hover:bg-[var(--primary)] hover:text-white transition-colors cursor-pointer shrink-0"
                  >
                    {fact.citation_id}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Source Disagreement / Conflict Warning Banner (if present) */}
      {result.conflicts && result.conflicts.length > 0 && (
        <div className="p-4 rounded-2xl bg-[var(--status-warning)]/10 border border-[var(--status-warning)]/30 space-y-2 text-xs">
          <div className="flex items-center gap-2 font-bold text-[var(--status-warning)] font-mono uppercase tracking-wider">
            <AlertTriangle className="w-4 h-4" />
            Source Difference Detected
          </div>
          {result.conflicts.map((conf, idx) => (
            <div key={conf.conflict_id || idx} className="space-y-1 text-[var(--text-primary)]">
              <div className="font-semibold">{conf.topic || conf.field || 'Conflicting Information'}</div>
              <p className="text-[var(--text-secondary)] leading-normal">
                {conf.resolution_summary || conf.description || `${conf.claim_a} vs ${conf.claim_b}`}
              </p>
              {conf.resolution_suggestion && (
                <div className="text-[11px] text-[var(--primary)] font-medium pt-0.5">
                  Recommendation: {conf.resolution_suggestion}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Sources Strip: Citations Grid */}
      <div className="pt-2 border-t border-[var(--border)] space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono flex items-center gap-1.5">
            <Compass className="w-3.5 h-3.5 text-[var(--primary)]" />
            Verified Travel Sources ({result.citations.length})
          </h4>
          {result.freshness_note && (
            <span className="text-[11px] text-[var(--text-muted)] font-mono hidden sm:inline">
              {result.freshness_note}
            </span>
          )}
        </div>

        {/* Citations Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {result.citations.map((cit, idx) => {
            const isDoc = cit.source_type === 'document' || cit.source_type === 'rag';
            const displayTitle = cit.title || cit.domain || 'Verified Knowledge Source';
            return (
              <div
                key={cit.citation_id || cit.id || idx}
                onClick={() => onSelectCitation(cit, displayQuery)}
                className="p-3.5 rounded-2xl border border-[var(--border)] hover:border-[var(--primary)] bg-[var(--surface-muted)]/40 hover:bg-[var(--surface-muted)] cursor-pointer transition-all flex flex-col justify-between space-y-2 group shadow-2xs"
              >
                <div>
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="inline-flex items-center gap-1 text-[10px] font-bold font-mono px-2 py-0.5 rounded bg-[var(--surface)] text-[var(--primary)] border border-[var(--border)]">
                      {cit.citation_id || 'Source'} {isDoc ? 'Private Document' : 'Web Source'}
                    </span>
                    {cit.freshness && (
                      <span className="text-[10px] text-[var(--text-muted)] font-mono">
                        {cit.freshness}
                      </span>
                    )}
                  </div>

                  <div className="font-editorial text-sm font-bold text-[var(--text-primary)] group-hover:text-[var(--primary)] transition-colors line-clamp-1">
                    {displayTitle}
                  </div>

                  {cit.domain && (
                    <div className="text-[11px] text-[var(--text-secondary)] line-clamp-1 mt-0.5 font-mono">
                      {cit.domain}
                    </div>
                  )}
                </div>

                <div className="pt-2 border-t border-[var(--border)]/60 flex items-center justify-between text-xs text-[var(--primary)] font-medium">
                  <span className="text-[11px] text-[var(--text-muted)] group-hover:text-[var(--primary)] transition-colors">
                    Read evidence
                  </span>
                  <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
