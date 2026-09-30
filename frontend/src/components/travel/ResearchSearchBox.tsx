import React, { useState } from 'react';
import { Search, Compass, Sparkles, Globe, FileText, Layers } from 'lucide-react';
import { Button } from '../common/Button';

interface ResearchSearchBoxProps {
  onSearch: (question: string, mode: 'auto' | 'web' | 'rag' | 'hybrid') => void;
  isLoading?: boolean;
  destination?: string;
}

export const ResearchSearchBox: React.FC<ResearchSearchBoxProps> = ({
  onSearch,
  isLoading = false,
  destination = 'Kyoto',
}) => {
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState<'auto' | 'web' | 'rag' | 'hybrid'>('hybrid');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    onSearch(question.trim(), mode);
  };

  const sampleQuestions = [
    `What should I know before visiting ${destination}?`,
    `Find traditional neighborhoods and local food experiences in ${destination}.`,
    `Is this neighborhood good for staying with luggage?`,
    `Find cultural experiences and quiet gardens near central ${destination}.`,
    `Public transit passes and rail rules for travelers.`,
  ];

  return (
    <div className="bg-[var(--surface)] rounded-3xl border border-[var(--border)] p-6 sm:p-8 shadow-xs space-y-6">
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Research Modes Selector */}
        <div className="flex flex-wrap items-center justify-between gap-3 pb-2 border-b border-[var(--border)]/70">
          <div className="flex items-center gap-1 bg-[var(--surface-muted)] p-1 rounded-xl border border-[var(--border)] text-xs">
            <button
              type="button"
              onClick={() => setMode('web')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all ${
                mode === 'web'
                  ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <Globe className="w-3.5 h-3.5 text-[var(--terracotta)]" />
              Web Research
            </button>
            <button
              type="button"
              onClick={() => setMode('rag')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all ${
                mode === 'rag'
                  ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <FileText className="w-3.5 h-3.5 text-[var(--primary)]" />
              Private Sources
            </button>
            <button
              type="button"
              onClick={() => setMode('hybrid')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-all ${
                mode === 'hybrid'
                  ? 'bg-[var(--surface)] text-[var(--text-primary)] font-semibold shadow-xs'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <Layers className="w-3.5 h-3.5 text-[var(--primary)]" />
              Combined Research
            </button>
          </div>

          <span className="text-xs text-[var(--text-muted)] font-mono">
            {mode === 'hybrid'
              ? 'Cross-referencing web authorities & your notes'
              : mode === 'rag'
              ? 'Searching uploaded documents only'
              : 'Searching verified web travel authorities'}
          </span>
        </div>

        {/* Input Bar */}
        <div className="relative flex flex-col sm:flex-row items-center gap-3">
          <div className="relative w-full">
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={`What would you like to discover about ${destination}?`}
              className="w-full pl-4 pr-10 py-3.5 rounded-2xl border border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)] text-sm sm:text-base focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] outline-hidden transition-all placeholder:text-[var(--text-muted)] shadow-2xs"
            />
            <Compass className="w-5 h-5 text-[var(--text-muted)] absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          </div>

          <Button
            type="submit"
            variant="primary"
            size="lg"
            isLoading={isLoading}
            disabled={!question.trim()}
            className="w-full sm:w-auto shrink-0 h-[48px] px-6 text-sm"
          >
            Research
          </Button>
        </div>

        {/* Quick Discovery Question Presets */}
        <div className="flex flex-wrap items-center gap-2 pt-2 text-xs">
          <span className="text-[var(--text-muted)] font-mono flex items-center gap-1 text-[11px]">
            <Sparkles className="w-3 h-3 text-[var(--terracotta)]" />
            Try discovering:
          </span>
          {sampleQuestions.map((q, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => {
                setQuestion(q);
                onSearch(q, mode);
              }}
              className="px-2.5 py-1 rounded-lg bg-[var(--surface-muted)] hover:bg-[var(--primary-soft)] hover:text-[var(--primary)] text-[11px] font-medium text-[var(--text-secondary)] transition-colors text-left"
            >
              {q}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
};
