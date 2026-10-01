import React, { useEffect } from 'react';
import { X, ExternalLink, ShieldCheck, Calendar, MapPin, BookOpen, Sparkles, FileText, Globe } from 'lucide-react';
import { ResearchCitation } from '../../types/research';

interface EvidenceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  citation: ResearchCitation | null;
  questionContext?: string;
}

export const EvidenceDrawer: React.FC<EvidenceDrawerProps> = ({
  isOpen,
  onClose,
  citation,
  questionContext,
}) => {
  // Lock body scroll when open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen || !citation) return null;

  const getSourceTypeBadge = (type: string) => {
    switch (type) {
      case 'document':
      case 'rag':
        return {
          label: 'Private Research Document',
          icon: <FileText className="w-3.5 h-3.5 text-[var(--primary)]" />,
          bg: 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)]/20',
        };
      case 'web':
        return {
          label: 'Official Web Source',
          icon: <Globe className="w-3.5 h-3.5 text-[var(--terracotta)]" />,
          bg: 'bg-[var(--terracotta-soft)] text-[var(--terracotta)] border-[var(--terracotta)]/20',
        };
      default:
        return {
          label: 'Verified Knowledge Source',
          icon: <BookOpen className="w-3.5 h-3.5 text-[var(--primary)]" />,
          bg: 'bg-[var(--surface-muted)] text-[var(--text-secondary)] border-[var(--border)]',
        };
    }
  };

  const badge = getSourceTypeBadge(citation.source_type || 'web');

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-xs transition-opacity duration-300"
        onClick={onClose}
      />

      {/* Drawer Container: bottom sheet on mobile, slide-over drawer on desktop */}
      <div className="fixed inset-y-0 right-0 max-w-full flex pl-10 sm:pl-16">
        <div className="w-screen max-w-md bg-[var(--surface)] border-l border-[var(--border)] shadow-2xl flex flex-col justify-between overflow-y-auto">
          {/* Header */}
          <div className="p-6 border-b border-[var(--border)] flex items-start justify-between gap-4 sticky top-0 bg-[var(--surface)]/95 backdrop-blur-md z-10">
            <div className="space-y-1">
              <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold font-mono border ${badge.bg}`}>
                {badge.icon}
                {badge.label}
              </span>
              <h3 className="text-xl font-bold font-editorial text-[var(--text-primary)]">
                Evidence & Source Details
              </h3>
            </div>

            <button
              type="button"
              onClick={onClose}
              className="w-8 h-8 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-muted)] flex items-center justify-center transition-colors cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Body Content */}
          <div className="p-6 space-y-6 flex-1">
            {/* Research Claim */}
            {(citation.claim || (citation.cited_claims && citation.cited_claims.length > 0)) && (
              <div className="space-y-1.5">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--primary)] font-mono">
                  Research Claim
                </span>
                <p className="text-sm font-medium text-[var(--text-primary)] leading-relaxed bg-[var(--surface-muted)]/50 p-3.5 rounded-2xl border border-[var(--border)]">
                  "{citation.claim || citation.cited_claims?.join('. ') || ''}"
                </p>
              </div>
            )}

            {/* Evidence Quote / Excerpt with Paper Notebook Styling */}
            {(citation.snippet || citation.locator) && (
              <div className="space-y-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-[var(--terracotta)]" />
                  Original Evidence Excerpt
                </span>
                <div className="relative p-5 rounded-2xl bg-[var(--surface-muted)] border-l-4 border-[var(--primary)] border-t border-r border-b border-[var(--border)] shadow-xs">
                  <blockquote className="text-sm italic font-serif text-[var(--text-primary)] leading-relaxed">
                    "{citation.snippet || citation.locator}"
                  </blockquote>
                  {(citation.publisher || citation.domain) && (
                    <div className="mt-3 text-right text-xs font-mono font-medium text-[var(--text-secondary)]">
                      — {citation.publisher || citation.domain}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Source Provenance Metadata */}
            <div className="space-y-3 pt-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-secondary)] font-mono">
                Source Provenance
              </span>

              <div className="space-y-2.5 p-4 rounded-2xl bg-[var(--surface)] border border-[var(--border)] text-xs">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-[var(--text-muted)] font-medium">Source:</span>
                  <span className="text-[var(--text-primary)] font-semibold text-right font-editorial text-sm">
                    {citation.title || citation.domain || 'Verified Knowledge Reference'}
                  </span>
                </div>

                {citation.domain && (
                  <div className="flex items-center justify-between gap-3 pt-2 border-t border-[var(--border)]/70">
                    <span className="text-[var(--text-muted)]">Domain:</span>
                    <span className="text-[var(--text-primary)] font-mono text-[11px]">
                      {citation.domain}
                    </span>
                  </div>
                )}

                {citation.publisher && (
                  <div className="flex items-center justify-between gap-3 pt-2 border-t border-[var(--border)]/70">
                    <span className="text-[var(--text-muted)]">Publisher / Authority:</span>
                    <span className="text-[var(--text-primary)] font-medium">
                      {citation.publisher}
                    </span>
                  </div>
                )}

                {citation.location && (
                  <div className="flex items-center justify-between gap-3 pt-2 border-t border-[var(--border)]/70">
                    <span className="text-[var(--text-muted)] flex items-center gap-1">
                      <MapPin className="w-3 h-3 text-[var(--terracotta)]" />
                      Location:
                    </span>
                    <span className="text-[var(--text-primary)] font-medium">
                      {citation.location}
                    </span>
                  </div>
                )}

                {citation.freshness && (
                  <div className="flex items-center justify-between gap-3 pt-2 border-t border-[var(--border)]/70">
                    <span className="text-[var(--text-muted)] flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      Freshness:
                    </span>
                    <span className="text-[var(--text-secondary)] font-mono text-[11px]">
                      {citation.freshness}
                    </span>
                  </div>
                )}

                {citation.confidence !== undefined && (
                  <div className="flex items-center justify-between gap-3 pt-2 border-t border-[var(--border)]/70">
                    <span className="text-[var(--text-muted)] flex items-center gap-1">
                      <ShieldCheck className="w-3 h-3 text-[var(--status-success)]" />
                      Match Confidence:
                    </span>
                    <span className="text-[var(--status-success)] font-semibold font-mono">
                      {Math.round(citation.confidence * 100)}%
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* Travel Inquiry Context */}
            {questionContext && (
              <div className="text-xs text-[var(--text-muted)] pt-2">
                <span className="font-medium text-[var(--text-secondary)]">In response to:</span> "{questionContext}"
              </div>
            )}
          </div>

          {/* Footer with Safe External Link */}
          <div className="p-6 border-t border-[var(--border)] bg-[var(--surface-muted)]/40 flex items-center justify-between gap-3">
            {(citation.canonical_url || citation.url) ? (
              <a
                href={citation.canonical_url || citation.url}
                target="_blank"
                rel="noopener noreferrer"
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-[var(--primary)] hover:bg-[var(--primary-hover)] text-white text-xs font-semibold shadow-xs transition-colors"
              >
                <span>Open Authoritative Source</span>
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            ) : (
              <div className="text-xs text-[var(--text-muted)] font-mono flex items-center gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-[var(--primary)]" />
                Verified internal reference
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
