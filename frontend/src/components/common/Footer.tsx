import React from 'react';
import { Compass, ShieldCheck } from 'lucide-react';

export const Footer: React.FC = () => {
  return (
    <footer className="border-t border-[var(--border)] bg-[var(--background)] py-10 mt-auto transition-colors">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-[var(--text-secondary)]">
          <div className="flex items-center gap-2 text-[var(--text-primary)] font-semibold">
            <Compass className="w-4 h-4 text-[var(--primary)]" />
            <span>TrustTrip TravelPlanner</span>
            <span aria-hidden="true" className="opacity-40">·</span>
            <span className="font-normal text-[var(--text-secondary)]">High-Trust Travel Workspace</span>
          </div>

          <div className="flex items-center gap-4 text-[11px]">
            <span className="flex items-center gap-1 text-[var(--status-success)]">
              <ShieldCheck className="w-3.5 h-3.5" />
              Verified Canonical Architecture
            </span>
            <span aria-hidden="true" className="opacity-40">·</span>
            <span>FastAPI & PostgreSQL Backend</span>
            <span aria-hidden="true" className="opacity-40">·</span>
            <span>Single-Developer Academic Build</span>
          </div>
        </div>

        <div className="mt-6 pt-4 border-t border-[var(--border-subtle)] flex flex-col sm:flex-row items-center justify-between gap-2 text-[11px] text-[var(--text-secondary)]">
          <p>© {new Date().getFullYear()} TrustTrip. Travel planning with intelligent AI capabilities.</p>
          <p className="font-mono text-[10px]">Part 2: Dashboard & Canonical Planning Active</p>
        </div>
      </div>
    </footer>
  );
};
