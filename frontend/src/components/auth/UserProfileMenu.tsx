import React, { useState, useRef, useEffect } from 'react';
import { User, LogOut, ChevronDown, ShieldCheck, MapPin } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

export const UserProfileMenu: React.FC = () => {
  const { user, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!user) return null;

  const displayName = user.email;
  const initial = displayName.charAt(0).toUpperCase();

  return (
    <div ref={menuRef} className="relative inline-block text-left">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 text-[var(--text-primary)] text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
        aria-expanded={isOpen}
        aria-haspopup="true"
      >
        <span className="w-6 h-6 rounded-full bg-[var(--primary-soft)] text-[var(--primary)] font-semibold text-xs flex items-center justify-center border border-[var(--primary)]/20">
          {initial}
        </span>
        <span className="hidden sm:inline font-medium max-w-[120px] truncate">
          {displayName}
        </span>
        <ChevronDown className={`w-3.5 h-3.5 text-[var(--text-secondary)] transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1.5 w-64 origin-top-right rounded-xl bg-[var(--surface)] shadow-lg border border-[var(--border)] py-2 z-50 text-xs text-[var(--text-primary)] animate-in fade-in zoom-in-95 duration-100">
          <div className="px-3.5 py-2 border-b border-[var(--border-subtle)]">
            <div className="font-semibold text-sm text-[var(--text-primary)] truncate">{displayName}</div>
            <div className="text-[11px] text-[var(--text-secondary)] truncate font-mono">{user.email}</div>
            <div className="flex items-center gap-2 mt-1.5 text-[10px] text-[var(--text-secondary)]">
              <span className="truncate max-w-[130px]" title={user.id}>ID: {user.id}</span>
              <span aria-hidden="true">·</span>
              <span className="text-[var(--status-success)] font-medium flex items-center gap-1 shrink-0">
                <ShieldCheck className="w-3 h-3" />
                Verified
              </span>
            </div>
          </div>

          <div className="py-1">
            <div className="px-3.5 py-1.5 text-[11px] text-[var(--text-secondary)] flex items-center gap-2">
              <MapPin className="w-3.5 h-3.5 text-[var(--primary)]" />
              <span>TrustTrip Active Planner Session</span>
            </div>
          </div>

          <div className="border-t border-[var(--border-subtle)] pt-1 mt-1">
            <button
              type="button"
              onClick={() => {
                setIsOpen(false);
                logout();
              }}
              className="w-full flex items-center gap-2 px-3.5 py-2 text-xs text-[var(--status-danger)] hover:bg-[var(--status-danger)]/10 transition-colors cursor-pointer text-left"
            >
              <LogOut className="w-3.5 h-3.5" />
              Sign Out
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
